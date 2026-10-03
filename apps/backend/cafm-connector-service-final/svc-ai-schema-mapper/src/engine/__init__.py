"""hoist-engine integration: the Go binary that does a migration's row-heavy steps.

See docs/superpowers/specs/2026-10-01-go-migration-engine-design.md.
"""
from .client import EngineError, engine_available, engine_binary, run_engine

__all__ = ["EngineError", "engine_available", "engine_binary", "run_engine"]
