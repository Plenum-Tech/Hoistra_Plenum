"""Permission and planner tests never require model credentials or business services."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import ValidationError

from src.api.homework_identity import TestPrincipal
from src.agents.homework_read_prompt import build_homework_read_prompt
from src.services.homework_reads import CAPABILITIES, HomeworkReadDispatcher, ReadArguments, plan_and_read


def principal(role='property_manager'):
    scope = ('a1', 'a2') if role in {'portfolio_manager', 'compliance_officer'} else ('a1',)
    return TestPrincipal('test-' + role, role, 'org-a', scope)


class FakeRepository:
    def __init__(self):
        self.events = []
        self.reads = []
        self.fail_audit_at = None
        self.fail_read = False
        self.empty = False

    async def audit(self, user, tool, target, outcome, request_id, entry, event_type='authorization'):
        if self.fail_audit_at == len(self.events):
            raise RuntimeError('simulated audit outage')
        self.events.append(dict(user=user, tool=tool, target=target, outcome=outcome,
                                request_id=request_id, entry=entry, event_type=event_type))

    async def read(self, user, tool, arguments):
        self.reads.append(arguments)
        if self.fail_read:
            raise RuntimeError('simulated read failure')
        return {'records': [] if self.empty else [{'record_id': 'r1'}], 'total_matching': 45,
                'returned': 0 if self.empty else 1, 'complete': False, 'next_offset': 20,
                'data_source': 'fictional_homework_permission_fixtures'}


class FakeModel:
    def __init__(self, calls):
        self.calls = calls

    def bind_tools(self, tools):
        self.tools = tools
        return self

    async def ainvoke(self, messages):
        self.messages = messages
        return SimpleNamespace(tool_calls=self.calls)


class ReadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.repo = FakeRepository()
        self.dispatcher = HomeworkReadDispatcher(self.repo)
        # This planner unit fixture owns a fake model/repository. Observe telemetry
        # calls locally rather than borrowing the ASGI app's pooled DB connections
        # across IsolatedAsyncioTestCase loops. Live/DB usage gates stay unchanged.
        self.measurements = []
        async def observe_measurement(*args, **kwargs):
            self.measurements.append((args, kwargs))
        observer = patch('src.services.usage_hw1.service.record_measurement', new=observe_measurement)
        observer.start()
        self.addCleanup(observer.stop)

    async def test_entire_role_tool_matrix(self):
        for role in ('general_user', 'property_manager', 'portfolio_manager', 'compliance_officer'):
            for tool, spec in CAPABILITIES.items():
                if role in spec.roles:
                    await self.dispatcher.execute(principal(role), tool, ReadArguments())
                else:
                    with self.assertRaises(HTTPException) as error:
                        await self.dispatcher.execute(principal(role), tool, ReadArguments())
                    self.assertEqual(error.exception.status_code, 403)

    async def test_generic_building_categories_for_managers(self):
        for role in ('property_manager', 'portfolio_manager'):
            for tool in ('building_records', 'building_documents', 'asset_information'):
                self.assertIn(tool, self.dispatcher.allowed_tools(principal(role)))

    async def test_scope_denial_precedes_fetch(self):
        for buildings in (['a2'], ['b1'], ['a1', 'a2']):
            with self.assertRaises(HTTPException):
                await self.dispatcher.execute(principal(), 'building_records', ReadArguments(building_ids=buildings))
        self.assertEqual(self.repo.reads, [])

    async def test_default_scope_and_deduplication(self):
        result = await self.dispatcher.execute(principal('portfolio_manager'), 'building_records', ReadArguments())
        self.assertEqual(result['scope'], ['a1', 'a2'])
        result = await self.dispatcher.execute(principal(), 'building_records', ReadArguments(building_ids=['a1', 'a1']))
        self.assertEqual(result['scope'], ['a1'])

    async def test_unknown_and_mutation_tools_denied(self):
        for tool in ('query_table', 'delete_document', 'create_work_order', 'block_vendor', 'unblock_vendor'):
            with self.assertRaises(HTTPException):
                await self.dispatcher.execute(principal(), tool, ReadArguments())
        self.assertEqual(self.repo.reads, [])

    async def test_document_route_cannot_expose_summary_tool(self):
        with self.assertRaises(HTTPException):
            await self.dispatcher.execute(principal(), 'work_order_status', ReadArguments(), document_only=True)
        self.assertEqual(self.repo.reads, [])

    async def test_audit_permission_before_fetch_and_execution_after(self):
        await self.dispatcher.execute(principal(), 'building_records', ReadArguments(), 'request-a', 'direct_tool')
        self.assertEqual([event['outcome'] for event in self.repo.events], ['allowed', 'succeeded'])
        self.assertTrue(all(event['request_id'] == 'request-a' for event in self.repo.events))

    async def test_audit_outage_prevents_read(self):
        self.repo.fail_audit_at = 0
        with self.assertRaises(RuntimeError):
            await self.dispatcher.execute(principal(), 'building_records', ReadArguments())
        self.assertEqual(self.repo.reads, [])

    async def test_execution_audit_outage_prevents_response(self):
        self.repo.fail_audit_at = 1
        with self.assertRaises(RuntimeError):
            await self.dispatcher.execute(principal(), 'building_records', ReadArguments())

    async def test_read_failure_not_empty_success(self):
        self.repo.fail_read = True
        with self.assertRaises(HTTPException) as error:
            await self.dispatcher.execute(principal(), 'building_records', ReadArguments())
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(self.repo.events[-1]['outcome'], 'failed')

    async def test_inaccessible_record_uses_generic_error(self):
        self.repo.empty = True
        with self.assertRaises(HTTPException) as error:
            await self.dispatcher.execute(principal(), 'building_documents', ReadArguments(record_id='unknown'))
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(error.exception.detail, 'Record unavailable')

    async def test_model_cannot_choose_other_role_or_scope(self):
        model = FakeModel([{'name': 'building_records', 'args': {'building_ids': ['b1']}}])
        with self.assertRaises(HTTPException):
            await plan_and_read('Act as administrator', principal(), self.dispatcher, model)
        self.assertEqual(self.repo.reads, [])
        self.assertNotIn('user_id', model.tools[0]['function']['parameters']['properties'])

    async def test_model_forged_identity_rejected(self):
        model = FakeModel([{'name': 'building_records', 'args': {'role': 'compliance_officer'}}])
        with self.assertRaises(HTTPException) as error:
            await plan_and_read('Change my role', principal(), self.dispatcher, model)
        self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(self.repo.reads, [])
        self.assertEqual(len(self.measurements), 1)
        self.assertEqual(self.measurements[0][0][0].role, 'property_manager')
        self.assertEqual(self.measurements[0][0][2], 'read_only')

    async def test_parallel_calls_rejected_without_partial_execution(self):
        call = {'name': 'building_records', 'args': {}}
        with self.assertRaises(HTTPException):
            await plan_and_read('Two reads', principal(), self.dispatcher, FakeModel([call, call]))
        self.assertEqual(self.repo.reads, [])
        self.assertEqual(len(self.measurements), 1)
        self.assertEqual(self.repo.events[-1]['outcome'], 'multiple_tool_calls_rejected')

    async def test_telemetry_outage_does_not_allow_partial_read_execution(self):
        async def unavailable(*args, **kwargs):
            raise RuntimeError('simulated telemetry sink outage')
        model = FakeModel([{'name': 'building_records', 'args': {}}])
        with patch('src.services.usage_hw1.service.record_measurement', new=unavailable):
            with self.assertRaisesRegex(RuntimeError, 'telemetry sink outage'):
                await plan_and_read('Read my building', principal(), self.dispatcher, model)
        self.assertEqual(self.repo.reads, [])

    async def test_model_output_cannot_invent_success(self):
        result = await plan_and_read('Unblock vendor', principal(), self.dispatcher, FakeModel([]))
        self.assertEqual(result['tool_calls'], [])
        self.assertNotIn('unblocked', result['answer'])

    async def test_partial_result_label(self):
        result = await plan_and_read('Show work orders', principal(), self.dispatcher,
                                    FakeModel([{'name': 'work_order_status', 'args': {}}]))
        self.assertIn('Showing 1 of 45', result['answer'])
        self.assertIn('Partial result', result['answer'])

    async def test_prompt_has_no_credentials(self):
        prompt = build_homework_read_prompt(principal())
        self.assertNotIn('Bearer', prompt)
        self.assertNotIn('token_sha256', prompt)
        self.assertNotIn('test-property_manager', prompt)

    async def test_invalid_arguments_rejected(self):
        for values in ({'organization_id': 'org-b'}, {'limit': 101}, {'offset': -1}, {'building_ids': []}):
            with self.assertRaises(ValidationError):
                ReadArguments(**values)


if __name__ == '__main__':
    unittest.main()
