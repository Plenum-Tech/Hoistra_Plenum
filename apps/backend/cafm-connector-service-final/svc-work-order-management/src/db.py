from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from .config import settings
from .models.base import Base
from . import models as _models  # noqa: F401 — registers all ORM models with Base.metadata
from .core.logging import get_logger

log = get_logger(__name__)

engine = create_async_engine(
    settings.db_url,
    echo=False,
    pool_size=5,
    max_overflow=10,
    # Health-check pooled connections + retire after 30 min so an idle-closed cloud-DB
    # connection is transparently reconnected instead of failing with "connection is closed".
    pool_pre_ping=True,
    pool_recycle=1800,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    log.info("db.init", status="starting")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("db.init", status="complete")


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
