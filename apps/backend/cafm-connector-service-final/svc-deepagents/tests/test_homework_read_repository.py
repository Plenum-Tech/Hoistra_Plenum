"""Real PostgreSQL checks against the isolated fictional permission fixtures."""
import json
import unittest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.config import settings
from src.api.homework_identity import TestPrincipal
from src.services.homework_reads import PostgresReadRepository, ReadArguments, runtime_dispatcher


class RepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # A fresh engine per event loop prevents asyncpg pooled connections crossing loops.
        import os
        self.engine = create_async_engine(os.environ['HW1_READ_DB_URL'])
        self.factory = async_sessionmaker(self.engine)
        self.repo = PostgresReadRepository(self.factory)
        self.user = TestPrincipal('hw1-property', 'property_manager', 'hw1-org-a', ('hw1-building-a1',))

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_projection_and_pagination(self):
        result = await self.repo.read(self.user, 'work_order_status',
                                     ReadArguments(building_ids=['hw1-building-a1'], limit=10))
        self.assertEqual(result['total_matching'], 25)
        self.assertEqual(result['returned'], 10)
        self.assertFalse(result['complete'])
        self.assertEqual(result['next_offset'], 10)
        self.assertNotIn('PROJECTION_CANARY', json.dumps(result))

    async def test_unknown_sensitive_and_mixed_content_withheld(self):
        for record in ('hw1-unknown-scope', 'hw1-sensitive-document', 'hw1-mixed-document'):
            result = await self.repo.read(self.user, 'building_documents',
                ReadArguments(building_ids=['hw1-building-a1'], record_id=record))
            self.assertEqual(result['records'], [])

    async def test_database_role_is_read_only_and_audit_append_only(self):
        async with self.factory() as session:
            for privilege, expected in [('SELECT', True), ('INSERT', False), ('UPDATE', False), ('DELETE', False)]:
                value = (await session.execute(text(
                    "SELECT has_table_privilege(current_user, 'hw1_access.read_records', :permission)"),
                    {'permission': privilege})).scalar_one()
                self.assertEqual(value, expected)
            for privilege, expected in [('INSERT', True), ('SELECT', False), ('UPDATE', False), ('DELETE', False)]:
                value = (await session.execute(text(
                    "SELECT has_table_privilege(current_user, 'hw1_access.authorization_audit', :permission)"),
                    {'permission': privilege})).scalar_one()
                self.assertEqual(value, expected)

    async def test_actual_count_and_page_share_snapshot_during_concurrent_insert(self):
        admin = create_async_engine(settings.db_url)
        base_factory = self.factory
        marker = 'hw1-concurrency-test-only'

        class ConcurrentSession:
            async def __aenter__(self):
                self.session = base_factory()
                await self.session.__aenter__()
                self.inserted = False
                return self

            async def __aexit__(self, *args):
                return await self.session.__aexit__(*args)

            async def execute(self, statement, parameters=None):
                result = await self.session.execute(statement, parameters or {})
                if str(statement).startswith('SELECT count(*)') and not self.inserted:
                    self.inserted = True
                    async with admin.begin() as connection:
                        await connection.execute(text("""
                            INSERT INTO hw1_access.read_records
                            (record_id, organization_id, building_id, content_building_ids,
                             scope_verified, contains_sensitive_data, kind, payload)
                            VALUES (:marker, 'hw1-org-a', 'hw1-building-a1',
                                    ARRAY['hw1-building-a1'], true, false, 'building_records', '{}')
                        """), {'marker': marker})
                return result

        try:
            result = await PostgresReadRepository(ConcurrentSession).read(self.user, 'building_records',
                ReadArguments(building_ids=['hw1-building-a1'], limit=100))
            self.assertEqual(result['returned'], result['total_matching'])
            self.assertNotIn(marker, json.dumps(result))
            async with admin.connect() as connection:
                count = (await connection.execute(text(
                    'SELECT count(*) FROM hw1_access.read_records WHERE record_id = :marker'),
                    {'marker': marker})).scalar_one()
                self.assertEqual(count, 1)
        finally:
            async with admin.begin() as connection:
                await connection.execute(text('DELETE FROM hw1_access.read_records WHERE record_id = :marker'),
                                         {'marker': marker})
            await admin.dispose()


if __name__ == '__main__':
    unittest.main()
