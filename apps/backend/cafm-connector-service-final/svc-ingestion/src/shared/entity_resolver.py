"""
Entity Resolver — 4-tier resolution with eval layers at every tier.

Tier 1: Exact match (Redis cache)        → EL-ER.T1
Tier 2: RapidFuzz fuzzy match            → EL-ER.T2
Tier 3: Claude Haiku re-query            → EL-ER.T3
Tier 4: Manual review queue              → EL-ER.T4
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

import structlog
from anthropic import AsyncAnthropic
from opentelemetry import trace
from pydantic import BaseModel, field_validator
from rapidfuzz import fuzz, process
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CACHE_KEY_ASSETS = "er:cache:assets"           # hash  field → json
_CACHE_KEY_USERS = "er:cache:users"             # hash  field → json
_CACHE_KEY_VENDORS = "er:cache:vendors"         # hash  field → json
_CACHE_REFRESH_KEY = "er:cache:last_refresh"    # string → epoch seconds
_CACHE_REFRESH_TTL = 3600                        # 1 hour refresh cycle
_CACHE_FRESHNESS_THRESHOLD = 7200               # 2 hours — EL-ER.T1 flag
_MANUAL_LOCK_TTL = 600                          # 10-min reviewer lock
_FUZZY_SCORE_THRESHOLD = 85.0                   # EL-ER.T2 min score (0–100)
_FUZZY_GAP_THRESHOLD = 10.0                     # EL-ER.T2 min score gap
_NAME_LENGTH_TOLERANCE = 0.30                   # EL-ER.T2 30% length diff

_HEDGING_PHRASES = [
    "i'm not sure", "i am not sure", "unclear", "cannot determine",
    "not certain", "possibly", "might be", "could be", "uncertain",
    "hard to tell", "difficult to say", "not enough information",
    "no clear match", "no match found",
]

# Unit mapping (canonical → integer code)
UNIT_MAP: dict[str, int] = {
    "c": 1, "°c": 1, "celsius": 1,
    "kpa": 2, "kilopascal": 2,
    "h": 3, "hr": 3, "hour": 3, "hours": 3,
    "mm/s": 4, "millimeter per second": 4,
    "a": 5, "amp": 5, "ampere": 5, "amps": 5,
    "pa": 6, "pascal": 6,
}

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class EntityType(str, Enum):
    ASSET = "asset"
    USER = "user"
    VENDOR = "vendor"


class ResolutionTier(int, Enum):
    EXACT = 1
    FUZZY = 2
    CLAUDE = 3
    MANUAL = 4


class ResolvedEntity(BaseModel):
    entity_id: str
    entity_type: EntityType
    canonical_name: str
    site_code: str | None
    is_active: bool
    source_record: dict[str, Any]


class ResolutionResult(BaseModel):
    resolved: bool
    tier: ResolutionTier | None = None
    entity: ResolvedEntity | None = None
    manual: bool = False
    confidence: Literal["high", "medium", "low"] | None = None
    requires_human_review: bool = False
    review_queue_id: UUID | None = None
    error: str | None = None

    @field_validator("tier", mode="before")
    @classmethod
    def coerce_tier(cls, v: Any) -> ResolutionTier | None:
        if isinstance(v, int):
            return ResolutionTier(v)
        return v


class ManualResolutionRequest(BaseModel):
    raw_name: str
    entity_type: EntityType
    site_code: str | None
    context_json: dict[str, Any]
    ingestion_id: str


# ---------------------------------------------------------------------------
# Date normaliser
# ---------------------------------------------------------------------------

_DATE_PATTERNS = [
    # ISO variants
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?Z?$"), "%Y-%m-%dT%H:%M:%S"),
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"), "%Y-%m-%d"),
    # DD/MM/YYYY and DD-MM-YYYY
    (re.compile(r"^(\d{2})/(\d{2})/(\d{4})$"), "%d/%m/%Y"),
    (re.compile(r"^(\d{2})-(\d{2})-(\d{4})$"), "%d-%m-%Y"),
    # MM/DD/YYYY
    (re.compile(r"^(\d{2})/(\d{2})/(\d{4})$"), "%m/%d/%Y"),
    # DD MMM YYYY (e.g. 15 Jan 2025)
    (re.compile(r"^(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})$"), "%d %b %Y"),
    # Unix timestamp (numeric string)
    (re.compile(r"^\d{10}$"), "unix"),
    (re.compile(r"^\d{13}$"), "unix_ms"),
]


def normalise_date(raw: str | int | float | None) -> int | None:
    """Return UTC epoch milliseconds or None if unparseable."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        ts = float(raw)
        # Detect seconds vs milliseconds
        if ts < 1e11:
            return int(ts * 1000)
        return int(ts)
    raw_str = str(raw).strip()
    for pattern, fmt in _DATE_PATTERNS:
        if not pattern.match(raw_str):
            continue
        try:
            if fmt == "unix":
                return int(float(raw_str) * 1000)
            if fmt == "unix_ms":
                return int(float(raw_str))
            dt = datetime.strptime(raw_str, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    return None


def normalise_unit(raw: str | None) -> int | None:
    """Map unit string to canonical integer code."""
    if raw is None:
        return None
    return UNIT_MAP.get(raw.strip().lower())


# ---------------------------------------------------------------------------
# Cache warm-up helpers
# ---------------------------------------------------------------------------


async def warm_cache(session: AsyncSession, redis: Redis) -> None:
    """Load assets, users, vendors from PostgreSQL into Redis hashes."""
    with tracer.start_as_current_span("entity_resolver.warm_cache") as span:
        log = logger.bind(action="warm_cache")

        # Assets
        result = await session.execute(
            text(
                "SELECT asset_code, asset_name, category, location_code, "
                "is_active FROM plenum_cafm.assets"
            )
        )
        rows = result.mappings().all()
        asset_map: dict[str, str] = {}
        for row in rows:
            rec = dict(row)
            asset_map[rec["asset_code"].lower()] = json.dumps(rec)
            if rec.get("asset_name"):
                asset_map[rec["asset_name"].lower()] = json.dumps(rec)
        if asset_map:
            await redis.hset(_CACHE_KEY_ASSETS, mapping=asset_map)  # type: ignore[arg-type]

        # Users
        result = await session.execute(
            text(
                "SELECT u.id::text, u.username, u.full_name, u.is_active, "
                "u.organization_id::text "
                "FROM plenum_cafm.users u"
            )
        )
        rows = result.mappings().all()
        user_map: dict[str, str] = {}
        for row in rows:
            rec = dict(row)
            if rec.get("username"):
                user_map[rec["username"].lower()] = json.dumps(rec)
            if rec.get("full_name"):
                user_map[rec["full_name"].lower()] = json.dumps(rec)
        if user_map:
            await redis.hset(_CACHE_KEY_USERS, mapping=user_map)  # type: ignore[arg-type]

        # Vendors
        result = await session.execute(
            text(
                "SELECT id::text, name, is_active FROM plenum_cafm.vendors"
            )
        )
        rows = result.mappings().all()
        vendor_map: dict[str, str] = {}
        for row in rows:
            rec = dict(row)
            if rec.get("name"):
                vendor_map[rec["name"].lower()] = json.dumps(rec)
        if vendor_map:
            await redis.hset(_CACHE_KEY_VENDORS, mapping=vendor_map)  # type: ignore[arg-type]

        now = str(int(time.time()))
        await redis.set(_CACHE_REFRESH_KEY, now)

        span.set_attribute("cafm.assets_cached", len(asset_map))
        span.set_attribute("cafm.users_cached", len(user_map))
        span.set_attribute("cafm.vendors_cached", len(vendor_map))
        log.info("cache_warmed", assets=len(asset_map), users=len(user_map), vendors=len(vendor_map))


async def _ensure_cache_fresh(redis: Redis, session: AsyncSession) -> bool:
    """Return True if cache is fresh; trigger background refresh if stale."""
    last_refresh = await redis.get(_CACHE_REFRESH_KEY)
    if last_refresh is None:
        await warm_cache(session, redis)
        return True
    age = int(time.time()) - int(last_refresh)
    return age < _CACHE_FRESHNESS_THRESHOLD


def _cache_key_for(entity_type: EntityType) -> str:
    return {
        EntityType.ASSET: _CACHE_KEY_ASSETS,
        EntityType.USER: _CACHE_KEY_USERS,
        EntityType.VENDOR: _CACHE_KEY_VENDORS,
    }[entity_type]


def _is_active_field(entity_type: EntityType) -> str:
    return "is_active"


# ---------------------------------------------------------------------------
# EL-ER.T1 — Exact match
# ---------------------------------------------------------------------------


async def _tier1_exact(
    raw_name: str,
    entity_type: EntityType,
    site_code: str | None,
    redis: Redis,
    session: AsyncSession,
) -> ResolvedEntity | None:
    with tracer.start_as_current_span("entity_resolver.tier1_eval") as span:
        cache_key = _cache_key_for(entity_type)
        lookup = raw_name.strip().lower()

        raw = await redis.hget(cache_key, lookup)
        if raw is None:
            span.set_attribute("cafm.match_unique", False)
            span.set_attribute("cafm.record_active", False)
            return None

        rec: dict[str, Any] = json.loads(raw)

        # EL-ER.T1 — active check
        if not rec.get("is_active", True):
            span.set_attribute("cafm.match_unique", True)
            span.set_attribute("cafm.record_active", False)
            logger.warning("tier1_inactive_record", entity_type=entity_type, raw_name=raw_name)
            return None

        # EL-ER.T1 — cache freshness flag (warn, don't fail)
        last_refresh = await redis.get(_CACHE_REFRESH_KEY)
        if last_refresh:
            age = int(time.time()) - int(last_refresh)
            if age > _CACHE_FRESHNESS_THRESHOLD:
                logger.warning(
                    "tier1_cache_stale",
                    age_seconds=age,
                    entity_type=entity_type,
                )

        entity_id = rec.get("asset_code") or rec.get("id") or rec.get("username") or ""
        canonical_name = rec.get("asset_name") or rec.get("full_name") or rec.get("name") or raw_name

        span.set_attribute("cafm.match_unique", True)
        span.set_attribute("cafm.record_active", True)

        return ResolvedEntity(
            entity_id=str(entity_id),
            entity_type=entity_type,
            canonical_name=canonical_name,
            site_code=rec.get("location_code") or rec.get("organization_id"),
            is_active=True,
            source_record=rec,
        )


# ---------------------------------------------------------------------------
# EL-ER.T2 — Fuzzy match (RapidFuzz)
# ---------------------------------------------------------------------------


async def _tier2_fuzzy(
    raw_name: str,
    entity_type: EntityType,
    site_code: str | None,
    redis: Redis,
) -> ResolvedEntity | None:
    with tracer.start_as_current_span("entity_resolver.tier2_eval") as span:
        cache_key = _cache_key_for(entity_type)
        all_keys: list[str] = [k.decode() for k in await redis.hkeys(cache_key)]

        if not all_keys:
            return None

        lookup = raw_name.strip().lower()
        matches = process.extract(lookup, all_keys, scorer=fuzz.WRatio, limit=3)

        if not matches:
            span.set_attribute("cafm.top_score", 0.0)
            return None

        top_match, top_score, _ = matches[0]
        second_score = matches[1][1] if len(matches) > 1 else 0.0

        span.set_attribute("cafm.top_score", top_score)
        span.set_attribute("cafm.score_gap", top_score - second_score)

        # EL-ER.T2: score threshold
        if top_score < _FUZZY_SCORE_THRESHOLD:
            logger.debug("tier2_score_too_low", score=top_score, raw_name=raw_name)
            return None

        # EL-ER.T2: score gap
        if (top_score - second_score) < _FUZZY_GAP_THRESHOLD and len(matches) > 1:
            logger.debug(
                "tier2_ambiguous_match",
                top=top_score,
                second=second_score,
                raw_name=raw_name,
            )
            return None

        # EL-ER.T2: name length within 30%
        len_ratio = abs(len(lookup) - len(top_match)) / max(len(top_match), 1)
        if len_ratio > _NAME_LENGTH_TOLERANCE:
            logger.debug("tier2_length_mismatch", ratio=len_ratio, raw_name=raw_name)
            return None

        raw = await redis.hget(cache_key, top_match)
        if raw is None:
            return None
        rec: dict[str, Any] = json.loads(raw)

        if not rec.get("is_active", True):
            return None

        # EL-ER.T2: site match (warn only — not hard block; some records lack site)
        rec_site = rec.get("location_code") or rec.get("organization_id")
        site_match = (site_code is None) or (rec_site is None) or (site_code == rec_site)
        span.set_attribute("cafm.site_match", site_match)
        if not site_match:
            logger.warning(
                "tier2_site_mismatch",
                expected_site=site_code,
                record_site=rec_site,
                raw_name=raw_name,
            )
            # Not a hard block — different sites can share assets, let through with warning

        entity_id = rec.get("asset_code") or rec.get("id") or rec.get("username") or ""
        canonical_name = rec.get("asset_name") or rec.get("full_name") or rec.get("name") or top_match

        return ResolvedEntity(
            entity_id=str(entity_id),
            entity_type=entity_type,
            canonical_name=canonical_name,
            site_code=rec_site,
            is_active=True,
            source_record=rec,
        )


# ---------------------------------------------------------------------------
# EL-ER.T3 — Claude Haiku re-query
# ---------------------------------------------------------------------------


async def _tier3_claude(
    raw_name: str,
    entity_type: EntityType,
    site_code: str | None,
    redis: Redis,
    client: AsyncAnthropic,
) -> ResolvedEntity | None:
    with tracer.start_as_current_span("entity_resolver.tier3_eval") as span:
        cache_key = _cache_key_for(entity_type)

        # Build candidate list from Redis (up to 50 candidates for Haiku context)
        all_keys: list[str] = [k.decode() for k in await redis.hkeys(cache_key)]
        candidates = all_keys[:50]

        system_prompt = (
            "You are a CAFM entity resolution specialist. "
            "Given an unmatched entity name and a list of known entities, "
            "return ONLY the exact ID string from the list that best matches. "
            "If no good match exists, return exactly: NO_MATCH"
        )
        user_msg = (
            f"Unmatched {entity_type.value}: {raw_name!r}\n"
            f"Site: {site_code or 'unknown'}\n\n"
            f"Known {entity_type.value} IDs/names:\n"
            + "\n".join(f"- {c}" for c in candidates)
            + "\n\nReturn only the matching ID string or NO_MATCH."
        )

        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=64,
            messages=[{"role": "user", "content": user_msg}],
            system=system_prompt,
        )
        answer = response.content[0].text.strip() if response.content else ""

        span.set_attribute("cafm.response_valid", bool(answer))

        # EL-ER.T3: hedging check
        answer_lower = answer.lower()
        for phrase in _HEDGING_PHRASES:
            if phrase in answer_lower:
                span.set_attribute("cafm.id_exists", False)
                logger.debug("tier3_hedging_detected", answer=answer, raw_name=raw_name)
                return None

        if answer == "NO_MATCH" or not answer:
            span.set_attribute("cafm.id_exists", False)
            return None

        # EL-ER.T3: verify returned ID exists in cache
        lookup = answer.lower()
        raw = await redis.hget(cache_key, lookup)
        if raw is None:
            # Try as-is
            raw = await redis.hget(cache_key, answer)
        if raw is None:
            span.set_attribute("cafm.id_exists", False)
            logger.warning("tier3_id_not_in_cache", returned_id=answer, raw_name=raw_name)
            return None

        rec: dict[str, Any] = json.loads(raw)
        if not rec.get("is_active", True):
            span.set_attribute("cafm.id_exists", True)
            return None

        # EL-ER.T3: site match
        rec_site = rec.get("location_code") or rec.get("organization_id")
        if site_code and rec_site and site_code != rec_site:
            logger.warning("tier3_site_mismatch", expected=site_code, got=rec_site)
            return None

        entity_id = rec.get("asset_code") or rec.get("id") or rec.get("username") or answer
        canonical_name = rec.get("asset_name") or rec.get("full_name") or rec.get("name") or answer

        span.set_attribute("cafm.id_exists", True)

        return ResolvedEntity(
            entity_id=str(entity_id),
            entity_type=entity_type,
            canonical_name=canonical_name,
            site_code=rec_site,
            is_active=True,
            source_record=rec,
        )


# ---------------------------------------------------------------------------
# EL-ER.T4 — Manual review queue submission
# ---------------------------------------------------------------------------


async def _tier4_submit_manual(
    raw_name: str,
    entity_type: EntityType,
    context: dict[str, Any],
    ingestion_id: str,
    session: AsyncSession,
    redis: Redis,
) -> UUID:
    """Insert into review_queue and return the queue item UUID."""
    with tracer.start_as_current_span("entity_resolver.tier4_eval") as span:
        review_id = uuid4()

        await session.execute(
            text(
                """
                INSERT INTO plenum_cafm.review_queue
                  (id, ingestion_id, review_type, status, payload, created_at)
                VALUES
                  (:id, :ingestion_id, 'entity_resolution', 'pending', :payload, now())
                """
            ),
            {
                "id": str(review_id),
                "ingestion_id": ingestion_id,
                "payload": json.dumps(
                    {
                        "raw_name": raw_name,
                        "entity_type": entity_type.value,
                        "context": context,
                    }
                ),
            },
        )
        await session.commit()

        # Redis lock slot (10-min reviewer lock key, no value yet — reviewer claims it)
        lock_key = f"er:manual:lock:{review_id}"
        await redis.set(lock_key, "", ex=_MANUAL_LOCK_TTL)

        span.set_attribute("cafm.reviewer_authorized", True)  # RBAC checked by API layer
        span.set_attribute("cafm.resolved", False)

        logger.info(
            "tier4_manual_queued",
            review_id=str(review_id),
            raw_name=raw_name,
            entity_type=entity_type,
        )
        return review_id


async def accept_manual_resolution(
    review_id: UUID,
    resolved_entity_id: str,
    reviewer_user_id: str,
    entity_type: EntityType,
    session: AsyncSession,
    redis: Redis,
) -> ResolutionResult:
    """
    Called by the review API when a human resolver confirms an entity.
    EL-ER.T4: verifies ID is active, no race condition, writes to cache.
    """
    with tracer.start_as_current_span("entity_resolver.tier4_accept") as span:
        # Race condition guard: try to claim the lock
        lock_key = f"er:manual:lock:{review_id}"
        claimed = await redis.set(lock_key, reviewer_user_id, ex=_MANUAL_LOCK_TTL, nx=True)
        if not claimed:
            existing = await redis.get(lock_key)
            if existing and existing.decode() not in ("", reviewer_user_id):
                span.set_attribute("cafm.resolved", False)
                return ResolutionResult(
                    resolved=False,
                    error=f"Review item {review_id} is already locked by another reviewer.",
                )

        # Verify resolved entity exists and is active in cache
        cache_key = _cache_key_for(entity_type)
        raw = await redis.hget(cache_key, resolved_entity_id.lower())
        if raw is None:
            raw = await redis.hget(cache_key, resolved_entity_id)

        if raw is None:
            span.set_attribute("cafm.resolved", False)
            return ResolutionResult(resolved=False, error=f"Entity ID {resolved_entity_id!r} not found in cache.")

        rec: dict[str, Any] = json.loads(raw)
        if not rec.get("is_active", True):
            return ResolutionResult(resolved=False, error=f"Entity {resolved_entity_id!r} is not active.")

        # Update review_queue status
        await session.execute(
            text(
                """
                UPDATE plenum_cafm.review_queue
                SET status = 'resolved',
                    resolved_value = :resolved_value,
                    resolved_by = :reviewer,
                    resolved_at = now()
                WHERE id = :id
                """
            ),
            {
                "id": str(review_id),
                "resolved_value": resolved_entity_id,
                "reviewer": reviewer_user_id,
            },
        )

        # Log to corrections_log
        await session.execute(
            text(
                """
                INSERT INTO plenum_cafm.corrections_log
                  (id, review_queue_id, corrected_value, corrected_by, created_at)
                VALUES
                  (:id, :review_id, :corrected_value, :corrected_by, now())
                """
            ),
            {
                "id": str(uuid4()),
                "review_id": str(review_id),
                "corrected_value": resolved_entity_id,
                "corrected_by": reviewer_user_id,
            },
        )
        await session.commit()

        # Write to entity_resolution_cache so future Tier 1 hits work
        payload_raw = await redis.hget(
            "er:review_queue_payloads", str(review_id)
        )
        if payload_raw:
            payload = json.loads(payload_raw)
            raw_name_key = payload.get("raw_name", "").lower()
            if raw_name_key:
                await redis.hset(cache_key, raw_name_key, json.dumps(rec))

        entity_id = rec.get("asset_code") or rec.get("id") or rec.get("username") or resolved_entity_id
        canonical_name = rec.get("asset_name") or rec.get("full_name") or rec.get("name") or resolved_entity_id

        span.set_attribute("cafm.resolved", True)
        span.set_attribute("cafm.reviewer_authorized", True)

        return ResolutionResult(
            resolved=True,
            tier=ResolutionTier.MANUAL,
            manual=True,
            confidence="high",
            entity=ResolvedEntity(
                entity_id=str(entity_id),
                entity_type=entity_type,
                canonical_name=canonical_name,
                site_code=rec.get("location_code") or rec.get("organization_id"),
                is_active=True,
                source_record=rec,
            ),
        )


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------


class EntityResolver:
    """
    4-tier entity resolver.  Call resolve() for each entity name encountered
    during ingestion.  Callers must provide an AsyncAnthropic client and an
    active Redis connection.
    """

    def __init__(
        self,
        redis: Redis,
        client: AsyncAnthropic,
        session: AsyncSession,
    ) -> None:
        self._redis = redis
        self._client = client
        self._session = session

    async def resolve(
        self,
        raw_name: str,
        entity_type: EntityType,
        *,
        site_code: str | None = None,
        ingestion_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> ResolutionResult:
        """
        Attempt to resolve raw_name to a canonical entity, trying each tier
        in order.  Each tier's output is evaluated before acceptance.
        """
        log = logger.bind(
            raw_name=raw_name,
            entity_type=entity_type.value,
            ingestion_id=ingestion_id,
        )

        with tracer.start_as_current_span("entity_resolver.resolve") as span:
            span.set_attribute("cafm.entity_type", entity_type.value)
            span.set_attribute("cafm.raw_name", raw_name)

            # Ensure cache exists / is fresh
            await _ensure_cache_fresh(self._redis, self._session)

            # ── Tier 1: Exact match ───────────────────────────────────────
            entity = await _tier1_exact(
                raw_name, entity_type, site_code, self._redis, self._session
            )
            if entity:
                log.info("resolved_tier1", entity_id=entity.entity_id)
                span.set_attribute("cafm.resolution_tier", 1)
                return ResolutionResult(
                    resolved=True,
                    tier=ResolutionTier.EXACT,
                    entity=entity,
                    confidence="high",
                )

            # ── Tier 2: Fuzzy match ───────────────────────────────────────
            entity = await _tier2_fuzzy(raw_name, entity_type, site_code, self._redis)
            if entity:
                log.info("resolved_tier2", entity_id=entity.entity_id)
                span.set_attribute("cafm.resolution_tier", 2)
                return ResolutionResult(
                    resolved=True,
                    tier=ResolutionTier.FUZZY,
                    entity=entity,
                    confidence="medium",
                )

            # ── Tier 3: Claude Haiku re-query ─────────────────────────────
            entity = await _tier3_claude(
                raw_name, entity_type, site_code, self._redis, self._client
            )
            if entity:
                log.info("resolved_tier3", entity_id=entity.entity_id)
                span.set_attribute("cafm.resolution_tier", 3)
                return ResolutionResult(
                    resolved=True,
                    tier=ResolutionTier.CLAUDE,
                    entity=entity,
                    confidence="medium",
                )

            # ── Tier 4: Manual review queue ───────────────────────────────
            review_id = await _tier4_submit_manual(
                raw_name,
                entity_type,
                context or {},
                ingestion_id,
                self._session,
                self._redis,
            )
            log.info("unresolved_manual_queued", review_id=str(review_id))
            span.set_attribute("cafm.resolution_tier", 4)
            return ResolutionResult(
                resolved=False,
                tier=ResolutionTier.MANUAL,
                requires_human_review=True,
                review_queue_id=review_id,
            )


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


async def resolve_entity(
    raw_name: str,
    entity_type: EntityType,
    *,
    site_code: str | None = None,
    ingestion_id: str = "",
    context: dict[str, Any] | None = None,
    redis: Redis,
    client: AsyncAnthropic,
    session: AsyncSession,
) -> ResolutionResult:
    """Thin wrapper so callers don't need to instantiate EntityResolver."""
    resolver = EntityResolver(redis=redis, client=client, session=session)
    return await resolver.resolve(
        raw_name,
        entity_type,
        site_code=site_code,
        ingestion_id=ingestion_id,
        context=context,
    )
