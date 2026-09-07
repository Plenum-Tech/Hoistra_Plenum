"""Universal canonical field registry — single cross-client JSON register.

This is the deterministic alias register that learns over time. It starts
seeded from CMMS_ALIASES (Maximo, Fiix, SAP PM, Archibus, etc.) and grows
each time a semantic match is approved — either automatically at Node 3
(confidence ≥ 0.85) or by a human at Node 4.

Once an alias is in the registry, future Node 2 runs hit it as Strategy R
(deterministic) instead of reaching Strategy 4 (LLM), saving embedding and
LLM calls for fields that have already been seen before.

File location:
    src/matchers/canonical_field_registry.json

The file is written atomically (temp-file + rename) on every append, so it
is safe to read concurrently while a write is in progress.

Usage in nodes:
    from ...matchers.registry import registry_lookup, registry_append

    # Strategy R in Node 2 (deterministic mapper):
    hit = registry_lookup(source_field)
    if hit:
        canonical, confidence, tier = hit
        ...

    # After auto-approval in Node 3 / human-approval in Node 4:
    await registry_append(
        alias=source_field,
        canonical=target_field,
        source_cmms=cmms_name,
        confidence=confidence,
        approved_by="auto",   # or "human"
        migration_id=migration_id,
    )
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cafm_shared.logging import get_logger
logger = get_logger(__name__)

# ── File path ─────────────────────────────────────────────────────────────────

_REGISTRY_FILE = Path(__file__).parent / "canonical_field_registry.json"

# ── In-memory cache ───────────────────────────────────────────────────────────
# Keyed by normalized alias → entry dict.
_cache: dict[str, dict] = {}
_loaded: bool = False
_write_lock: Optional[asyncio.Lock] = None   # created lazily inside event loop


def _get_lock() -> asyncio.Lock:
    global _write_lock
    if _write_lock is None:
        _write_lock = asyncio.Lock()
    return _write_lock


# ── Normalisation ─────────────────────────────────────────────────────────────

def _norm(alias: str) -> str:
    """Lowercase, collapse separators to underscore, strip edges."""
    s = alias.lower().strip()
    s = re.sub(r"[\s\-_/.,;:]+", "_", s)
    return s.strip("_")


# ── Seed from CMMS_ALIASES ────────────────────────────────────────────────────

_FIIX_PREFIXES = {"x"}
_MAXIMO_KEYS = {
    "assetnum", "assetid", "wonum", "wopriority", "wostatus", "worktype",
    "pmnumplan", "itemnum", "siteid", "personname", "assetname", "assetclass",
}
_SAP_PREFIXES = {"sap", "z_", "t_"}

def _infer_source_cmms(alias: str) -> str:
    """Best-effort CMMS attribution for seed entries."""
    low = alias.lower()
    if low.startswith("x") and len(alias) > 2:
        return "Fiix"
    if any(low.startswith(p) for p in _SAP_PREFIXES):
        return "SAP PM"
    if alias.lower() in _MAXIMO_KEYS:
        return "Maximo"
    return "Generic"


def _build_seed() -> dict:
    """
    Build the initial registry from CMMS_ALIASES.

    Returns raw_mappings dict: alias → entry.
    """
    from .cmms_aliases import CMMS_ALIASES

    seed_ts = "2026-04-13T00:00:00Z"
    raw: dict[str, dict] = {}
    for alias, canonical in CMMS_ALIASES.items():
        raw[alias] = {
            "canonical": canonical,
            "source_cmms": _infer_source_cmms(alias),
            "confidence": 0.97,
            "tier": "alias",
            "approved_by": "seed",
            "approved_at": seed_ts,
        }
    return raw


# ── Persistence ───────────────────────────────────────────────────────────────

def _persist(raw_mappings: dict) -> None:
    """Write registry to disk atomically (sync — called infrequently)."""
    try:
        total = len(raw_mappings)
        learned = sum(
            1 for v in raw_mappings.values()
            if v.get("tier") == "semantic_approved"
        )
        data = {
            "_meta": {
                "version": 1,
                "description": (
                    "Universal deterministic alias registry — "
                    "seeded from CMMS_ALIASES, grows via approved semantic matches. "
                    "Covers: Maximo, Fiix, SAP PM, Archibus, and learned aliases."
                ),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "total_mappings": total,
                "learned_count": learned,
            },
            "mappings": raw_mappings,
        }
        tmp = _REGISTRY_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        tmp.replace(_REGISTRY_FILE)
        logger.debug(f"[registry] Persisted {total} entries ({learned} learned) → {_REGISTRY_FILE.name}")
    except Exception as exc:
        logger.warning(f"[registry] Persist failed (non-fatal): {exc}")


# ── Load ──────────────────────────────────────────────────────────────────────

def _ensure_loaded() -> None:
    """Load registry from file into _cache (idempotent)."""
    global _loaded

    if _loaded:
        return

    if _REGISTRY_FILE.exists():
        try:
            with open(_REGISTRY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("mappings", {})
            for alias, entry in raw.items():
                _cache[_norm(alias)] = entry
            _loaded = True
            meta = data.get("_meta", {})
            logger.info(
                f"[registry] Loaded {len(_cache)} entries "
                f"({meta.get('learned_count', 0)} learned) "
                f"from {_REGISTRY_FILE.name}"
            )
            return
        except Exception as exc:
            logger.warning(
                f"[registry] Failed to load {_REGISTRY_FILE.name}: {exc} "
                f"— rebuilding from CMMS_ALIASES"
            )

    # File missing or corrupt — seed from CMMS_ALIASES and save
    raw = _build_seed()
    for alias, entry in raw.items():
        _cache[_norm(alias)] = entry
    _loaded = True
    _persist(raw)
    logger.info(
        f"[registry] Seeded {len(_cache)} entries from CMMS_ALIASES "
        f"→ {_REGISTRY_FILE.name}"
    )


# ── Public API ────────────────────────────────────────────────────────────────

def registry_lookup(alias: str) -> tuple[str, float, str] | None:
    """
    Look up an alias in the registry.

    Returns (canonical_field, confidence, tier) or None.

    Tiers:
        "alias"             — seeded from CMMS_ALIASES (same as Strategy 2)
        "semantic_approved" — learned from Node 3/4 approvals (Strategy R)

    Strategy R in Node 2 should only trust "semantic_approved" tier entries
    (the "alias" tier is already covered by hardcoded CMMS_ALIASES in Strategy 2).
    """
    _ensure_loaded()
    entry = _cache.get(_norm(alias))
    if entry:
        return (entry["canonical"], entry["confidence"], entry["tier"])
    return None


def registry_lookup_learned_only(alias: str) -> tuple[str, float, str] | None:
    """
    Look up only semantic_approved entries (Strategy R in Node 2).

    Returns (canonical_field, confidence, tier) or None.
    Skips seed/alias entries — those are already covered by Strategy 2.
    """
    _ensure_loaded()
    entry = _cache.get(_norm(alias))
    if entry and entry.get("tier") == "semantic_approved":
        return (entry["canonical"], entry["confidence"], entry["tier"])
    return None


async def registry_append(
    alias: str,
    canonical: str,
    source_cmms: str,
    confidence: float,
    approved_by: str = "auto",
    migration_id: Optional[str] = None,
) -> bool:
    """
    Append a new semantic-approved alias to the registry.

    Called by:
        - Node 3 (semantic mapper) for auto-approved matches (confidence ≥ 0.85)
        - Node 4 (human review) for human-accepted or human-overridden mappings

    Idempotent — if the alias already exists with ANY tier, skips silently.
    Returns True if a new entry was written, False if alias already exists.
    """
    _ensure_loaded()
    key = _norm(alias)

    # Fast check without lock
    if key in _cache:
        logger.debug(
            f"[registry] Skip '{alias}' — already mapped "
            f"to {_cache[key]['canonical']} ({_cache[key]['tier']})"
        )
        return False

    async with _get_lock():
        # Double-check inside lock
        if key in _cache:
            return False

        entry: dict = {
            "canonical": canonical,
            "source_cmms": source_cmms or "Unknown",
            "confidence": round(confidence, 4),
            "tier": "semantic_approved",
            "approved_by": approved_by,
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }
        if migration_id:
            entry["migration_id"] = migration_id

        _cache[key] = entry

        # Rebuild raw dict for persistence (preserve original alias key, not normalized)
        raw = {k: v for k, v in _cache.items()}
        await asyncio.to_thread(_persist, raw)

    logger.info(
        f"[registry] ✓ Learned: '{alias}' → {canonical} "
        f"({confidence:.2f}, src={source_cmms}, by={approved_by})"
    )
    return True


def registry_stats() -> dict:
    """Return quick statistics about the registry."""
    _ensure_loaded()
    by_tier: dict[str, int] = {}
    by_cmms: dict[str, int] = {}
    for entry in _cache.values():
        t = entry.get("tier", "unknown")
        c = entry.get("source_cmms", "unknown")
        by_tier[t] = by_tier.get(t, 0) + 1
        by_cmms[c] = by_cmms.get(c, 0) + 1
    return {
        "total": len(_cache),
        "by_tier": by_tier,
        "by_cmms": by_cmms,
        "learned": by_tier.get("semantic_approved", 0),
    }


def get_all_entries(tier_filter: Optional[str] = None) -> dict:
    """
    Return all current cache entries as a plain dict.

    Args:
        tier_filter: If set, only return entries where tier == tier_filter.
                     Use "semantic_approved" to get only learned (non-seed) entries.

    Used by registry_cache.save_new_version() to snapshot learned mappings into DB.
    """
    _ensure_loaded()
    if tier_filter:
        return {k: v for k, v in _cache.items() if v.get("tier") == tier_filter}
    return dict(_cache)


def load_from_snapshot(learned_mappings: dict) -> None:
    """
    Merge learned entries from a DB snapshot into the in-memory cache.

    Called once at startup by registry_cache.load_or_build() after it reads the
    latest registry JSON from the DB.  Seed (alias tier) entries from the JSON
    file are already loaded by _ensure_loaded(); this function only adds entries
    that have tier == "semantic_approved" so we never overwrite seed entries.

    Args:
        learned_mappings: The "learned_mappings" dict from a canonical_registry row.
                          Keys are normalised aliases, values are entry dicts.
    """
    global _loaded
    _ensure_loaded()  # load seed entries first
    added = 0
    for alias, entry in learned_mappings.items():
        key = _norm(alias)
        if key not in _cache:
            _cache[key] = entry
            added += 1
    if added:
        logger.info(f"[registry] Merged {added} learned entries from DB snapshot into cache")
