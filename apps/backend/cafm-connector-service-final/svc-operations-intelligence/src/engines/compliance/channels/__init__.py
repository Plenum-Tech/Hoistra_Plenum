"""Public / partner verification channel clients (CCC §8)."""
from __future__ import annotations

from .public_apis import run_public_api_check
from .register_bot import name_match_confidence, run_live_bot_search, select_auto_verify_hit
from .register_search import (
    deep_link,
    register_for_certificate_code,
    run_register_search,
)

__all__ = [
    "run_public_api_check",
    "run_register_search",
    "run_live_bot_search",
    "select_auto_verify_hit",
    "name_match_confidence",
    "register_for_certificate_code",
    "deep_link",
]
