"""Centralized structured logger.

Every module should import `logger` from here:
    from app.core.logger import logger
    logger.info("message", extra_field="value")

Logs go to both stderr and a rotating file in ./logs/.
"""
import sys
from pathlib import Path

from loguru import logger as _logger

from app.core.config import settings

# Remove the default loguru handler so we can install our own formatters.
_logger.remove()

# ---- Console handler ----
_logger.add(
    sys.stderr,
    level=settings.log_level,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    ),
    colorize=True,
    backtrace=True,
    diagnose=settings.app_env != "production",
)

# ---- File handler (rotating) ----
log_dir = Path("./logs")
log_dir.mkdir(parents=True, exist_ok=True)
_logger.add(
    log_dir / "rag_platform.log",
    level=settings.log_level,
    rotation="20 MB",
    retention="10 days",
    compression="zip",
    format=(
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
        "{name}:{function}:{line} | {message}"
    ),
    enqueue=True,  # async-safe
    backtrace=True,
    diagnose=False,
)

# Bind application name so every line carries it.
logger = _logger.bind(app=settings.app_name)

logger.info(
    "Logger initialized | env={} | level={} | mock_mode={} | sqlite_dev={}",
    settings.app_env,
    settings.log_level,
    settings.is_mock_mode,
    settings.use_sqlite_dev,
)
