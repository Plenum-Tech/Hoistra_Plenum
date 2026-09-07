"""
Test configuration and fixtures for Phase 8 integration tests.

This conftest provides:
1. In-memory SQLite database for testing
2. Test data fixtures (migrations, mappings, CSV data)
3. FastAPI client with dependency overrides (when app is available)

Note: Some tests may not require full app imports and will work
with minimal dependencies.
"""
import os
from pathlib import Path

import pytest
import pytest_asyncio

# Set test env variables early
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-anthropic")
os.environ.setdefault("OPENAI_API_KEY", "test-key-openai")
os.environ.setdefault("LANGSMITH_API_KEY", "test-key-langsmith")
os.environ.setdefault("LANGSMITH_PROJECT", "test-project")
os.environ.setdefault("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")


# ============================================================================
# Fixtures for E2E Tests (no app dependency)
# ============================================================================

@pytest.fixture
def sample_csv_path():
    """Return path to sample CSV file."""
    return Path(__file__).parent.parent / "fixtures" / "assets_sample.csv"


@pytest.fixture
def sample_csv_content():
    """Return sample CSV content as bytes."""
    csv_path = Path(__file__).parent.parent / "fixtures" / "assets_sample.csv"
    with open(csv_path, "rb") as f:
        return f.read()


@pytest.fixture
def sample_mapping_doc():
    """Return sample mapping document."""
    return {
        "asset_id": "asset_id",
        "asset_code": "asset_code",
        "asset_name": "asset_name",
        "asset_type": "asset_type",
        "location": "location_description",
        "department": "department_name",
        "serial_number": "serial_number",
        "manufacturer": "manufacturer_name",
        "model": "model_number",
        "acquisition_date": "acquisition_date",
        "condition_status": "condition_status",
        "last_maintenance_date": "last_maintenance_date",
    }


# ============================================================================
# Fixtures for API Tests (requires app)
# ============================================================================

# These fixtures will only be loaded if the test imports from conftest
# They're defined but may not work without full environment setup

try:
    import uuid
    from typing import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import declarative_base

    from src.models.migration import MigrationJob, MigrationFieldMapping, MigrationHierarchy

    Base = declarative_base()

    @pytest_asyncio.fixture
    async def test_db_engine():
        """Create test database engine with in-memory SQLite."""
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        # Create all tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        yield engine

        # Cleanup
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    @pytest_asyncio.fixture
    async def test_session_factory(test_db_engine):
        """Create session factory for tests."""
        from sqlalchemy.ext.asyncio import async_sessionmaker

        async_session = async_sessionmaker(
            test_db_engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
        return async_session

    @pytest_asyncio.fixture
    async def test_session(test_session_factory) -> AsyncGenerator[AsyncSession, None]:
        """Provide a test session."""
        async with test_session_factory() as session:
            yield session

    @pytest_asyncio.fixture
    async def sample_migration_job(test_session):
        """Create a sample migration job in database."""
        job = MigrationJob(
            id=uuid.uuid4(),
            source_blob_url="https://example.blob.core.windows.net/uploads/assets.csv",
            source_filename="assets.csv",
            source_system="Maximo",
            encoding="utf-8",
            delimiter=",",
            row_count=60,
            column_count=12,
            status="pending",
            created_at=None,
            updated_at=None,
            completed_at=None,
        )
        test_session.add(job)
        await test_session.commit()
        await test_session.refresh(job)
        return job

    @pytest_asyncio.fixture
    async def sample_migration_job_with_mappings(test_session, sample_migration_job):
        """Create a migration job with field mappings."""
        mappings = [
            MigrationFieldMapping(
                id=uuid.uuid4(),
                migration_id=sample_migration_job.id,
                source_field="asset_id",
                target_field="asset_id",
                confidence=0.99,
                tier=1,
                rationale="Exact match",
            ),
            MigrationFieldMapping(
                id=uuid.uuid4(),
                migration_id=sample_migration_job.id,
                source_field="asset_code",
                target_field="asset_code",
                confidence=0.99,
                tier=1,
                rationale="Exact match",
            ),
            MigrationFieldMapping(
                id=uuid.uuid4(),
                migration_id=sample_migration_job.id,
                source_field="location",
                target_field="location_description",
                confidence=0.92,
                tier=2,
                rationale="Semantic similarity",
            ),
        ]
        test_session.add_all(mappings)
        await test_session.commit()
        return sample_migration_job

except ImportError as e:
    # If imports fail, create dummy fixtures
    @pytest.fixture
    def test_session():
        """Dummy fixture when DB dependencies unavailable."""
        return None

    @pytest.fixture
    def sample_migration_job():
        """Dummy fixture when DB dependencies unavailable."""
        return None

    @pytest.fixture
    def sample_migration_job_with_mappings():
        """Dummy fixture when DB dependencies unavailable."""
        return None
