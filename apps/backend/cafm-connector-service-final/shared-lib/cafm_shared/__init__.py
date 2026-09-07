"""cafm_shared — Shared telemetry, logging, exceptions and model re-exports."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cafm-shared")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["__version__"]
