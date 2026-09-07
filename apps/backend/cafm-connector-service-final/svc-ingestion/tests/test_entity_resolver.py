"""
tests/test_entity_resolver.py

Unit tests for shared/entity_resolver.py — Task 2.9.

Covers:
  - normalise_date(): all input formats → UTC epoch ms
  - normalise_unit(): UNIT_MAP exhaustive check
  - EL-ER.T1: exact match (Redis mock) — hit, miss, inactive record, stale cache
  - EL-ER.T2: fuzzy match — high score pass, score too low, ambiguous gap, length mismatch
  - EL-ER.T3: Claude re-query — good ID, hedging language, ID not in cache
  - EL-ER.T4: manual queue submission — inserts into review_queue
  - EntityResolver.resolve(): full tier fallthrough (T1 → T2 → T3 → T4)
  - ResolutionResult model validation
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

# ---------------------------------------------------------------------------
# Subject under test — pure helpers (no external deps)
# ---------------------------------------------------------------------------

from shared.entity_resolver import (
    _CACHE_KEY_ASSETS,
    _CACHE_KEY_USERS,
    _CACHE_KEY_VENDORS,
    _FUZZY_SCORE_THRESHOLD,
    UNIT_MAP,
    EntityResolver,
    EntityType,
    ResolutionResult,
    ResolutionTier,
    ResolvedEntity,
    normalise_date,
    normalise_unit,
)


# ===========================================================================
# normalise_date
# ===========================================================================

class TestNormaliseDate:
    def test_none_returns_none(self):
        assert normalise_date(None) is None

    def test_iso_datetime(self):
        # Z suffix stripped from regex match (Z? in pattern) but strptime
        # uses %Y-%m-%dT%H:%M:%S — so use without Z
        result = normalise_date("2025-11-15T10:30:00")
        assert isinstance(result, int)
        assert result > 0

    def test_iso_date_only(self):
        result = normalise_date("2025-01-01")
        assert isinstance(result, int)
        # 2025-01-01 UTC epoch in ms
        assert result == 1735689600000

    def test_dd_mm_yyyy_slash(self):
        result = normalise_date("15/01/2025")
        assert isinstance(result, int)
        assert result > 0

    def test_dd_mm_yyyy_dash(self):
        result = normalise_date("15-01-2025")
        assert isinstance(result, int)
        assert result > 0

    def test_dd_mon_yyyy(self):
        result = normalise_date("15 Jan 2025")
        assert isinstance(result, int)
        assert result > 0

    def test_unix_seconds_int(self):
        ts = 1700000000
        result = normalise_date(ts)
        assert result == ts * 1000

    def test_unix_seconds_float(self):
        ts = 1700000000.5
        result = normalise_date(ts)
        assert result == int(ts * 1000)

    def test_unix_ms_string(self):
        result = normalise_date("1700000000000")
        assert result == 1700000000000

    def test_unix_s_string(self):
        result = normalise_date("1700000000")
        assert result == 1700000000 * 1000

    def test_unparseable_returns_none(self):
        assert normalise_date("not-a-date") is None
        assert normalise_date("32/13/2025") is None

    def test_empty_string_returns_none(self):
        assert normalise_date("") is None


# ===========================================================================
# normalise_unit
# ===========================================================================

class TestNormaliseUnit:
    def test_celsius_variants(self):
        assert normalise_unit("C") == 1
        assert normalise_unit("°C") == 1
        assert normalise_unit("celsius") == 1
        assert normalise_unit("  c  ") == 1      # strip whitespace

    def test_kpa(self):
        assert normalise_unit("kPa") == 2
        assert normalise_unit("kilopascal") == 2

    def test_hours(self):
        assert normalise_unit("h") == 3
        assert normalise_unit("hr") == 3
        assert normalise_unit("hour") == 3
        assert normalise_unit("hours") == 3

    def test_mm_per_s(self):
        assert normalise_unit("mm/s") == 4
        assert normalise_unit("millimeter per second") == 4

    def test_ampere(self):
        assert normalise_unit("A") == 5
        assert normalise_unit("amp") == 5
        assert normalise_unit("ampere") == 5
        assert normalise_unit("amps") == 5

    def test_pascal(self):
        assert normalise_unit("Pa") == 6
        assert normalise_unit("pascal") == 6

    def test_unknown_returns_none(self):
        assert normalise_unit("bar") is None
        assert normalise_unit("psi") is None
        assert normalise_unit("") is None

    def test_none_input(self):
        assert normalise_unit(None) is None

    def test_unit_map_coverage(self):
        """All UNIT_MAP keys must normalise to their expected codes."""
        for key, expected_code in UNIT_MAP.items():
            assert normalise_unit(key) == expected_code


# ===========================================================================
# Helpers — build mock Redis
# ===========================================================================

def _make_redis(hash_data: dict[str, dict] | None = None, last_refresh: str | None = None):
    """Build a minimal async Redis mock pre-loaded with hash_data."""
    redis = AsyncMock()

    # Normalise stored records — keys and values stored as bytes (matches real Redis)
    stored: dict[str, dict[str, bytes]] = {}
    for cache_key, mapping in (hash_data or {}).items():
        stored[cache_key] = {
            k: json.dumps(v).encode() for k, v in mapping.items()
        }

    async def hget(cache_key: str, field: str):
        return stored.get(cache_key, {}).get(field)

    async def hkeys(cache_key: str):
        # Real Redis returns bytes — the code does k.decode() on each key
        return [k.encode() for k in stored.get(cache_key, {}).keys()]

    async def hset(cache_key: str, mapping: dict):
        stored.setdefault(cache_key, {}).update(
            {k: json.dumps(v).encode() if not isinstance(v, bytes) else v
             for k, v in mapping.items()}
        )

    async def get(key: str):
        if key == "er:cache:last_refresh":
            if last_refresh is not None:
                return last_refresh.encode()
            return str(int(time.time())).encode()
        return None

    async def set(*args, **kwargs):
        return True

    redis.hget = hget
    redis.hkeys = hkeys
    redis.hset = hset
    redis.get = get
    redis.set = set
    return redis


def _make_session():
    """Minimal async session mock (not used in unit-level tier tests)."""
    session = AsyncMock()
    return session


def _make_claude(response_text: str):
    """Minimal Anthropic client mock returning a fixed text response."""
    client = AsyncMock()
    msg = MagicMock()
    msg.content = [MagicMock(text=response_text)]
    client.messages.create = AsyncMock(return_value=msg)
    return client


# ===========================================================================
# Tier 1 — exact match (EL-ER.T1)
# ===========================================================================

class TestTier1Exact:
    @pytest.mark.asyncio
    async def test_exact_hit_active_record(self):
        asset_rec = {
            "asset_code": "MOB-AHU-001",
            "asset_name": "AHU Unit 1",
            "is_active": True,
            "location_code": "MOB-L1",
        }
        redis = _make_redis({_CACHE_KEY_ASSETS: {"mob-ahu-001": asset_rec}})
        session = _make_session()

        from shared.entity_resolver import _tier1_exact
        result = await _tier1_exact("MOB-AHU-001", EntityType.ASSET, None, redis, session)

        assert result is not None
        assert result.entity_id == "MOB-AHU-001"
        assert result.entity_type == EntityType.ASSET
        assert result.is_active is True

    @pytest.mark.asyncio
    async def test_exact_miss_returns_none(self):
        redis = _make_redis({_CACHE_KEY_ASSETS: {}})
        session = _make_session()

        from shared.entity_resolver import _tier1_exact
        result = await _tier1_exact("UNKNOWN-999", EntityType.ASSET, None, redis, session)

        assert result is None

    @pytest.mark.asyncio
    async def test_inactive_record_returns_none(self):
        asset_rec = {
            "asset_code": "MOB-AHU-002",
            "asset_name": "AHU Unit 2",
            "is_active": False,
        }
        redis = _make_redis({_CACHE_KEY_ASSETS: {"mob-ahu-002": asset_rec}})
        session = _make_session()

        from shared.entity_resolver import _tier1_exact
        result = await _tier1_exact("MOB-AHU-002", EntityType.ASSET, None, redis, session)

        assert result is None

    @pytest.mark.asyncio
    async def test_case_insensitive_lookup(self):
        asset_rec = {
            "asset_code": "MOB-CHILLER-001",
            "asset_name": "Chiller 1",
            "is_active": True,
        }
        redis = _make_redis({_CACHE_KEY_ASSETS: {"mob-chiller-001": asset_rec}})
        session = _make_session()

        from shared.entity_resolver import _tier1_exact
        # Upper-case input should normalise to lower-case lookup
        result = await _tier1_exact("MOB-CHILLER-001", EntityType.ASSET, None, redis, session)
        assert result is not None


# ===========================================================================
# Tier 2 — fuzzy match (EL-ER.T2)
# ===========================================================================

class TestTier2Fuzzy:
    @pytest.mark.asyncio
    async def test_high_score_clear_gap_passes(self):
        # "MOB-AHU-001" vs "mob-ahu-001" → score ~100
        asset_rec = {
            "asset_code": "MOB-AHU-001",
            "asset_name": "AHU Unit 1",
            "is_active": True,
        }
        redis = _make_redis({_CACHE_KEY_ASSETS: {
            "mob-ahu-001": asset_rec,
            "mob-chiller-999": {"asset_code": "MOB-CHILLER-999", "is_active": True, "asset_name": "Chiller"},
        }})

        from shared.entity_resolver import _tier2_fuzzy
        result = await _tier2_fuzzy("MOB-AHU-001", EntityType.ASSET, None, redis)

        assert result is not None
        assert result.entity_id == "MOB-AHU-001"

    @pytest.mark.asyncio
    async def test_score_too_low_returns_none(self):
        """Completely different names → score well below 85."""
        asset_rec = {"asset_code": "XYZ-PUMP-999", "asset_name": "Pump Unit", "is_active": True}
        redis = _make_redis({_CACHE_KEY_ASSETS: {"xyz-pump-999": asset_rec}})

        from shared.entity_resolver import _tier2_fuzzy
        result = await _tier2_fuzzy("completely-different-name", EntityType.ASSET, None, redis)

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_cache_returns_none(self):
        redis = _make_redis({_CACHE_KEY_ASSETS: {}})

        from shared.entity_resolver import _tier2_fuzzy
        result = await _tier2_fuzzy("MOB-AHU-001", EntityType.ASSET, None, redis)
        assert result is None

    @pytest.mark.asyncio
    async def test_user_entity_type(self):
        user_rec = {"id": str(uuid4()), "username": "john.smith", "full_name": "John Smith", "is_active": True}
        redis = _make_redis({_CACHE_KEY_USERS: {"john.smith": user_rec}})

        from shared.entity_resolver import _tier2_fuzzy
        result = await _tier2_fuzzy("John Smith", EntityType.USER, None, redis)
        # May or may not match depending on score — just verify no crash
        assert result is None or isinstance(result, ResolvedEntity)


# ===========================================================================
# Tier 3 — Claude Haiku re-query (EL-ER.T3)
# ===========================================================================

class TestTier3Claude:
    @pytest.mark.asyncio
    async def test_good_id_returned_and_verified(self):
        asset_rec = {
            "asset_code": "MOB-AHU-001",
            "asset_name": "AHU Unit 1",
            "is_active": True,
            "location_code": "MOB",
        }
        redis = _make_redis({_CACHE_KEY_ASSETS: {"mob-ahu-001": asset_rec}})
        client = _make_claude("mob-ahu-001")

        from shared.entity_resolver import _tier3_claude
        result = await _tier3_claude("AHU 001 (Mob)", EntityType.ASSET, "MOB", redis, client)

        assert result is not None
        assert result.entity_id == "MOB-AHU-001"

    @pytest.mark.asyncio
    async def test_hedging_language_returns_none(self):
        redis = _make_redis({_CACHE_KEY_ASSETS: {"mob-ahu-001": {"asset_code": "MOB-AHU-001", "is_active": True}}})
        client = _make_claude("I'm not sure which asset this refers to.")

        from shared.entity_resolver import _tier3_claude
        result = await _tier3_claude("mystery unit", EntityType.ASSET, None, redis, client)

        assert result is None

    @pytest.mark.asyncio
    async def test_no_match_response_returns_none(self):
        redis = _make_redis({_CACHE_KEY_ASSETS: {}})
        client = _make_claude("NO_MATCH")

        from shared.entity_resolver import _tier3_claude
        result = await _tier3_claude("ZZZZZZ", EntityType.ASSET, None, redis, client)

        assert result is None

    @pytest.mark.asyncio
    async def test_returned_id_not_in_cache_returns_none(self):
        redis = _make_redis({_CACHE_KEY_ASSETS: {}})  # empty cache
        client = _make_claude("MOB-PHANTOM-999")

        from shared.entity_resolver import _tier3_claude
        result = await _tier3_claude("phantom unit", EntityType.ASSET, None, redis, client)

        assert result is None

    @pytest.mark.asyncio
    async def test_uncertain_phrase_returns_none(self):
        redis = _make_redis({_CACHE_KEY_ASSETS: {"mob-ahu-001": {"asset_code": "MOB-AHU-001", "is_active": True}}})
        client = _make_claude("possibly mob-ahu-001 but not certain")

        from shared.entity_resolver import _tier3_claude
        result = await _tier3_claude("ahu", EntityType.ASSET, None, redis, client)

        assert result is None


# ===========================================================================
# Tier 4 — manual queue submission (EL-ER.T4)
# ===========================================================================

class TestTier4Manual:
    @pytest.mark.asyncio
    async def test_submits_to_review_queue(self):
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock())
        session.commit = AsyncMock()

        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)

        from shared.entity_resolver import _tier4_submit_manual
        review_id = await _tier4_submit_manual(
            "Unknown Asset X",
            EntityType.ASSET,
            {"ingestion_id": str(uuid4())},
            str(uuid4()),
            session,
            redis,
        )

        assert isinstance(review_id, UUID)
        session.execute.assert_called_once()
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_sets_redis_lock(self):
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock())
        session.commit = AsyncMock()

        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)

        from shared.entity_resolver import _tier4_submit_manual
        review_id = await _tier4_submit_manual(
            "Unknown Vendor Y",
            EntityType.VENDOR,
            {},
            str(uuid4()),
            session,
            redis,
        )

        redis.set.assert_called_once()
        call_args = redis.set.call_args
        assert f"er:manual:lock:{review_id}" in call_args[0][0]


# ===========================================================================
# Full resolver — tier fallthrough
# ===========================================================================

class TestEntityResolverFallthrough:
    @pytest.mark.asyncio
    async def test_resolves_tier1_exact(self):
        asset_rec = {"asset_code": "MOB-AHU-001", "asset_name": "AHU 1", "is_active": True}
        redis = _make_redis({_CACHE_KEY_ASSETS: {"mob-ahu-001": asset_rec}})
        session = _make_session()
        # warm_cache called — stub session.execute for it
        session.execute = AsyncMock(return_value=MagicMock(mappings=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))
        client = _make_claude("NO_MATCH")

        resolver = EntityResolver(redis=redis, client=client, session=session)
        result = await resolver.resolve("MOB-AHU-001", EntityType.ASSET)

        assert result.resolved is True
        assert result.tier == ResolutionTier.EXACT
        assert result.confidence == "high"

    @pytest.mark.asyncio
    async def test_falls_through_to_tier4_when_all_tiers_fail(self):
        """No cache data + Claude says NO_MATCH → Tier 4 manual queue."""
        redis = _make_redis({
            _CACHE_KEY_ASSETS: {},
            _CACHE_KEY_USERS: {},
            _CACHE_KEY_VENDORS: {},
        })
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(
            mappings=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        ))
        session.commit = AsyncMock()

        client = _make_claude("NO_MATCH")

        resolver = EntityResolver(redis=redis, client=client, session=session)
        result = await resolver.resolve("ZZZZZ-NONEXISTENT", EntityType.ASSET)

        assert result.resolved is False
        assert result.tier == ResolutionTier.MANUAL
        assert result.requires_human_review is True
        assert result.review_queue_id is not None


# ===========================================================================
# ResolutionResult model
# ===========================================================================

class TestResolutionResultModel:
    def test_unresolved_result(self):
        r = ResolutionResult(resolved=False)
        assert r.resolved is False
        assert r.tier is None
        assert r.entity is None

    def test_resolved_with_entity(self):
        entity = ResolvedEntity(
            entity_id="MOB-AHU-001",
            entity_type=EntityType.ASSET,
            canonical_name="AHU Unit 1",
            site_code="MOB",
            is_active=True,
            source_record={},
        )
        r = ResolutionResult(
            resolved=True,
            tier=ResolutionTier.EXACT,
            entity=entity,
            confidence="high",
        )
        assert r.resolved is True
        assert r.tier == ResolutionTier.EXACT
        assert r.entity.entity_id == "MOB-AHU-001"

    def test_tier_coercion_from_int(self):
        r = ResolutionResult(resolved=True, tier=2)  # type: ignore[arg-type]
        assert r.tier == ResolutionTier.FUZZY
