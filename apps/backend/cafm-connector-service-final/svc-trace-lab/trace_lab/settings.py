"""Trace Lab's configuration, read from the environment only (G0 addendum 2.2).

Nothing here touches a database, the network or application code. load() is pure: it reads the
mapping it is given (os.environ by default) and raises ValueError naming the variable when a value
is wrong. An unset database URL is not an error here: the service still starts, answers /health,
and reports itself unavailable until the URL is set (the lifespan decides that, not this module).

Never put these values in a .env file baked into an image: compose and the platform pass them.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse

SCHEMA_MODES = ("verify", "apply")
#: Settings.upstreams key -> the variable naming where the proxy forwards allowed reads (Phase 3).
UPSTREAM_VARS: dict[str, str] = {
    "udr": "TRACE_LAB_UPSTREAM_UDR",
    "oi": "TRACE_LAB_UPSTREAM_OI",
    "wom": "TRACE_LAB_UPSTREAM_WOM",
    "docrag": "TRACE_LAB_UPSTREAM_DOCRAG",
}
MAX_GOLDEN_CONCURRENCY = 4
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"", "0", "false", "no", "off"})


def _no_upstreams() -> dict[str, str]:
    return {key: "" for key in UPSTREAM_VARS}


@dataclass(frozen=True)
class Settings:
    main_db_url: str = ""                 # DB_URL (or DATABASE_URL): the application database, read-only
    lab_db_url: str = ""                  # TRACE_LAB_DB_URL: Trace Lab's own database
    schema_mode: str = "verify"           # TRACE_LAB_SCHEMA: "verify" | "apply"
    enabled: bool = False                 # TRACE_LAB_ENABLED: the kill switch
    upstreams: dict[str, str] = field(default_factory=_no_upstreams)
    judge_model: str | None = None        # TRACE_LAB_JUDGE_MODEL
    golden_max_concurrency: int = 1       # TRACE_LAB_GOLDEN_MAX_CONCURRENCY


def _first(env: Mapping[str, str], *names: str) -> tuple[str, str]:
    """The first of names that is set to something other than blanks, and its value."""
    for name in names:
        value = (env.get(name) or "").strip()
        if value:
            return name, value
    return names[0], ""


def _db_url(name: str, value: str) -> str:
    if not value:
        return ""
    if urlparse(value).scheme != "postgresql+asyncpg":
        raise ValueError(f"{name} must be a postgresql+asyncpg:// URL")
    return value


def _flag(name: str, value: str) -> bool:
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ValueError(f"{name} must be 1 or 0, not {value[:20]!r}")


def _upstream(name: str, value: str) -> str:
    v = value.strip().rstrip("/")
    if not v:
        return ""
    u = urlparse(v)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError(f"{name} must be an http:// or https:// URL")
    return v


def load(env: Mapping[str, str] | None = None) -> Settings:
    """The settings from env (os.environ when None). ValueError names the variable that is wrong."""
    e: Mapping[str, str] = os.environ if env is None else env
    main_name, main_value = _first(e, "DB_URL", "DATABASE_URL")
    mode = (e.get("TRACE_LAB_SCHEMA") or "").strip().lower() or "verify"
    if mode not in SCHEMA_MODES:
        raise ValueError(f"TRACE_LAB_SCHEMA must be verify or apply, not {mode[:20]!r}")
    raw_conc = (e.get("TRACE_LAB_GOLDEN_MAX_CONCURRENCY") or "").strip() or "1"
    try:
        concurrency = int(raw_conc)
    except ValueError:
        raise ValueError("TRACE_LAB_GOLDEN_MAX_CONCURRENCY must be a whole number") from None
    if not 1 <= concurrency <= MAX_GOLDEN_CONCURRENCY:
        raise ValueError(f"TRACE_LAB_GOLDEN_MAX_CONCURRENCY must be between 1 and {MAX_GOLDEN_CONCURRENCY}")
    return Settings(
        main_db_url=_db_url(main_name, main_value),
        lab_db_url=_db_url("TRACE_LAB_DB_URL", (e.get("TRACE_LAB_DB_URL") or "").strip()),
        schema_mode=mode,
        enabled=_flag("TRACE_LAB_ENABLED", e.get("TRACE_LAB_ENABLED") or ""),
        upstreams={key: _upstream(var, e.get(var) or "") for key, var in UPSTREAM_VARS.items()},
        judge_model=(e.get("TRACE_LAB_JUDGE_MODEL") or "").strip() or None,
        golden_max_concurrency=concurrency,
    )
