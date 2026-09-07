"""One-shot script to create all tables.

Usage:
    python -m scripts.init_db
"""
from app.core.logger import logger
from app.db.session import init_db


def main() -> None:
    logger.info("Running init_db...")
    init_db()
    logger.info("Done.")


if __name__ == "__main__":
    main()
