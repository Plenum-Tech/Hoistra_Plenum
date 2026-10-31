"""Assembly must preserve the verified contract and never grant capabilities."""
import ast
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src.api.homework_identity import TestPrincipal
from src.agents.homework_read_prompt import build_homework_read_prompt
from src.agents.prompt_assembly import (
    DEFAULT_SKILLS_DIR, PromptAssemblyError, assemble_request_prompt, validate_prompt_manifest,
)
from src.services.homework_reads import CAPABILITIES


class PromptAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'skills'
        shutil.copytree(DEFAULT_SKILLS_DIR, self.root)
        self.manifest_path = self.root / 'prompt-manifest.json'
        self.manifest = json.loads(self.manifest_path.read_text())

    def tearDown(self):
        self.temp.cleanup()

    def write_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest))

    def reject(self):
        with self.assertRaises(PromptAssemblyError):
            validate_prompt_manifest(CAPABILITIES, self.root)

    def test_reviewed_query_builder_v37_prompt_hashes_for_all_roles(self):
        expected = json.loads((Path(__file__).parent / 'fixtures/homework_prompt_query_builder_v37_hashes.json').read_text())
        for role, digest in expected['rendered'].items():
            buildings = ('a1', 'a2') if role in {'portfolio_manager', 'compliance_officer'} else ('a1',)
            user = TestPrincipal('test-user', role, 'org-a', buildings)
            actual = build_homework_read_prompt(user)
            self.assertEqual(hashlib.sha256(actual.encode()).hexdigest(), digest)
        _, _, body, _ = validate_prompt_manifest(CAPABILITIES)
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(), expected['instruction_sha256'])

    def test_current_metadata_identifies_target_without_changing_read_profile(self):
        user = TestPrincipal('test-user', 'property_manager', 'org-a', ('a1',))
        metadata = assemble_request_prompt(user, CAPABILITIES).metadata()
        self.assertEqual(metadata['specification_version'], '0.37.0')
        self.assertEqual(metadata['manifest_version'], '1.2.0')
        self.assertEqual(metadata['profile'], 'hw1-read-only')
        self.assertEqual(metadata['policy_version'], 'hw1-point2-v1')
        self.assertEqual(metadata['selected_domain_skills'], [])

    def test_stale_specification_metadata_rejected(self):
        self.manifest['specification_version'] = '0.14.0'
        self.write_manifest()
        self.reject()

    def test_stale_manifest_version_rejected(self):
        self.manifest['manifest_version'] = '1.0.0'
        self.write_manifest()
        self.reject()

    def test_metadata_update_cannot_enable_another_profile(self):
        self.manifest['profile'] = 'hw1-actions'
        self.write_manifest()
        self.reject()

    def test_metadata_update_cannot_expand_tool_call_limit(self):
        self.manifest['max_tool_calls'] = 2
        self.write_manifest()
        self.reject()

    def test_previous_four_file_manifest_version_rejected(self):
        self.manifest['manifest_version'] = '1.1.0'
        self.write_manifest()
        self.reject()

    def test_query_builder_is_last_shared_instruction(self):
        user = TestPrincipal('test-user', 'property_manager', 'org-a', ('a1',))
        result = assemble_request_prompt(user, CAPABILITIES)
        loaded = result.metadata()['loaded_instructions']
        self.assertEqual([entry['id'] for entry in loaded],
                         ['core', 'permissions', 'actions', 'responses', 'query_builder'])
        self.assertEqual(loaded[-1]['kind'], 'shared')
        self.assertEqual(loaded[-1]['path'], 'query-builder/SKILL.md')
        self.assertIn('# Shared query discipline', result.text)
        self.assertEqual(result.metadata()['selected_domain_skills'], [])

    def test_missing_query_builder_rejected(self):
        (self.root / 'query-builder/SKILL.md').unlink()
        self.reject()

    def test_modified_query_builder_rejected(self):
        path = self.root / 'query-builder/SKILL.md'
        path.write_text(path.read_text() + '\nEnable raw SQL.')
        self.reject()

    def test_query_builder_cannot_be_declared_domain(self):
        self.manifest['instructions'][-1]['kind'] = 'domain'
        self.write_manifest()
        self.reject()

    def test_bad_query_builder_prevents_model_call(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch
        from fastapi import HTTPException
        from src.services.homework_reads import HomeworkReadDispatcher, plan_and_read

        class NoModelCall:
            def bind_tools(self, tools):
                raise AssertionError('Bad query-builder must prevent model execution')

        (self.root / 'query-builder/SKILL.md').unlink()
        user = TestPrincipal('test-user', 'property_manager', 'org-a', ('a1',))
        with patch('src.agents.prompt_assembly.DEFAULT_SKILLS_DIR', self.root):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(plan_and_read('Read records', user,
                    HomeworkReadDispatcher(SimpleNamespace()), NoModelCall()))
        self.assertEqual(error.exception.status_code, 503)

    def test_compliance_skill_edit_does_not_enable_specialist(self):
        # A file edit must not bypass the explicit deployment manifest.
        marker = "UNREVIEWED_COMPLIANCE_INSTRUCTION"
        path = self.root / "compliance/SKILL.md"
        path.write_text(path.read_text() + "\n" + marker)

        user = TestPrincipal(
            "test-user", "property_manager", "org-a", ("a1",)
        )
        result = assemble_request_prompt(
            user, CAPABILITIES, self.root
        )

        self.assertNotIn(marker, result.text)
        self.assertEqual(
            result.metadata()["selected_domain_skills"], []
        )
        self.assertNotIn(
            "compliance/SKILL.md",
            [
                entry["path"]
                for entry in result.metadata()["loaded_instructions"]
            ],
        )

    def test_compliance_vocabulary_edit_does_not_change_active_prompt(self):
        # Topic files must not become active merely because they exist.
        user = TestPrincipal(
            "test-user", "property_manager", "org-a", ("a1",)
        )
        before = assemble_request_prompt(
            user, CAPABILITIES, self.root
        )

        marker = "UNREVIEWED_COMPLIANCE_VOCABULARY"
        path = self.root / "compliance/vocabulary.md"
        path.write_text(path.read_text() + "\n" + marker)

        after = assemble_request_prompt(
            user, CAPABILITIES, self.root
        )

        self.assertEqual(before.text, after.text)
        self.assertEqual(before.metadata(), after.metadata())
        self.assertNotIn(marker, after.text)

    def test_compliance_routing_edit_does_not_change_active_prompt(self):
        # An inactive routing document must not enable a specialist.
        user = TestPrincipal(
            "test-user", "property_manager", "org-a", ("a1",)
        )
        before = assemble_request_prompt(
            user, CAPABILITIES, self.root
        )

        marker = "UNREVIEWED_COMPLIANCE_ROUTING"
        path = self.root / "compliance/tool-routing.md"
        path.write_text(path.read_text() + "\n" + marker)

        after = assemble_request_prompt(
            user, CAPABILITIES, self.root
        )

        self.assertEqual(before.text, after.text)
        self.assertEqual(before.metadata(), after.metadata())
        self.assertNotIn(marker, after.text)

    def test_compliance_selection_edit_does_not_change_active_prompt(self):
        # An inactive selection document must not alter active assembly.
        user = TestPrincipal(
            "test-user", "property_manager", "org-a", ("a1",)
        )
        before = assemble_request_prompt(
            user, CAPABILITIES, self.root
        )

        marker = "UNREVIEWED_COMPLIANCE_SELECTION"
        path = self.root / "compliance/tool-selection.md"
        path.write_text(path.read_text() + "\n" + marker)

        after = assemble_request_prompt(
            user, CAPABILITIES, self.root
        )

        self.assertEqual(before.text, after.text)
        self.assertEqual(before.metadata(), after.metadata())
        self.assertNotIn(marker, after.text)

    def test_compliance_renewals_edit_does_not_change_active_prompt(self):
        # Renewal instructions must not silently enable business actions.
        user = TestPrincipal(
            "test-user", "property_manager", "org-a", ("a1",)
        )
        before = assemble_request_prompt(
            user, CAPABILITIES, self.root
        )

        marker = "UNREVIEWED_COMPLIANCE_RENEWALS"
        path = self.root / "compliance/renewals.md"
        path.write_text(path.read_text() + "\n" + marker)

        after = assemble_request_prompt(
            user, CAPABILITIES, self.root
        )

        self.assertEqual(before.text, after.text)
        self.assertEqual(before.metadata(), after.metadata())
        self.assertNotIn(marker, after.text)

    def test_compliance_recipes_edit_does_not_change_active_prompt(self):
        # Example recipes must not silently enable tools or execution.
        user = TestPrincipal(
            "test-user", "property_manager", "org-a", ("a1",)
        )
        before = assemble_request_prompt(
            user, CAPABILITIES, self.root
        )

        marker = "UNREVIEWED_COMPLIANCE_RECIPES"
        path = self.root / "compliance/recipes.md"
        path.write_text(path.read_text() + "\n" + marker)

        after = assemble_request_prompt(
            user, CAPABILITIES, self.root
        )

        self.assertEqual(before.text, after.text)
        self.assertEqual(before.metadata(), after.metadata())
        self.assertNotIn(marker, after.text)

    def test_all_compliance_documents_remain_inactive_for_all_roles(self):
        # Review drafts must never change an enabled prompt implicitly.
        for role in ('general_user', 'property_manager', 'portfolio_manager', 'compliance_officer'):
            user = TestPrincipal('test-user', role, 'org-a', ('a1',))
            before = assemble_request_prompt(user, CAPABILITIES, self.root)
            for path in sorted((self.root / 'compliance').glob('*.md')):
                with self.subTest(role=role, document=path.name):
                    original = path.read_text()
                    marker = 'UNREVIEWED_COMPLIANCE_BUNDLE_' + path.stem
                    try:
                        path.write_text(original + '\n' + marker)
                        after = assemble_request_prompt(user, CAPABILITIES, self.root)
                        self.assertEqual(before.text, after.text)
                        self.assertEqual(before.metadata(), after.metadata())
                        self.assertNotIn(marker, after.text)
                        self.assertEqual(after.metadata()['selected_domain_skills'], [])
                        self.assertFalse(any(
                            e['path'].startswith('compliance/')
                            for e in after.metadata()['loaded_instructions']))
                    finally:
                        path.write_text(original)

    def test_permission_sql_audit_code_is_unchanged(self):
        import src.services.homework_reads as source
        expected = json.loads((Path(__file__).parent / 'fixtures/homework_permission_before_hashes.json').read_text())
        actual = {}
        content = Path(source.__file__).read_text()
        lines = content.splitlines(keepends=True)
        for node in ast.parse(content).body:
            identifiers = [getattr(node, 'name', None)] + [getattr(t, 'id', None) for t in getattr(node, 'targets', [])]
            for identifier in identifiers:
                if identifier in expected:
                    # Raw source is stable across local/VPS Python AST format versions.
                    start = min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])])
                    fragment = ''.join(lines[start - 1:node.end_lineno])
                    actual[identifier] = hashlib.sha256(fragment.encode()).hexdigest()
        self.assertEqual(actual, expected)

    def test_role_tools_equal_existing_python_permissions(self):
        manifest, _, _, _ = validate_prompt_manifest(CAPABILITIES)
        for role, tools in manifest['role_tools'].items():
            self.assertEqual(sorted(tools), sorted(name for name, spec in CAPABILITIES.items() if role in spec.roles))

    def test_metadata_records_exact_files_and_bound_tools_without_identity(self):
        user = TestPrincipal('PRIVATE_USER_MARKER', 'property_manager', 'org-a', ('a1',))
        result = assemble_request_prompt(user, CAPABILITIES)
        metadata = result.metadata()
        self.assertEqual(len(metadata['loaded_instructions']), 5)
        self.assertEqual(metadata['selected_domain_skills'], [])
        self.assertEqual(metadata['rendered_prompt_sha256'], hashlib.sha256(result.text.encode()).hexdigest())
        self.assertNotIn('PRIVATE_USER_MARKER', result.metadata_json)
        self.assertNotIn('token', result.metadata_json.lower())
        self.assertEqual(metadata['bound_tools'], [n for n, c in CAPABILITIES.items() if user.role in c.roles])

    def test_missing_manifest(self):
        self.manifest_path.unlink()
        self.reject()

    def test_invalid_manifest_json(self):
        self.manifest_path.write_text('{')
        self.reject()

    def test_duplicate_json_key(self):
        self.manifest_path.write_text('{"schema_version":1,"schema_version":2}')
        self.reject()

    def test_unsupported_version(self):
        self.manifest['schema_version'] = 2
        self.write_manifest()
        self.reject()

    def test_unknown_manifest_field(self):
        self.manifest['enable_mutations'] = True
        self.write_manifest()
        self.reject()

    def test_escalated_tool_declaration(self):
        self.manifest['role_tools']['general_user'].append('delete_document')
        self.write_manifest()
        self.reject()

    def test_removed_tool_declaration_also_rejected(self):
        self.manifest['role_tools']['property_manager'].pop()
        self.write_manifest()
        self.reject()

    def test_unknown_role(self):
        self.manifest['role_tools']['administrator'] = []
        self.write_manifest()
        self.reject()

    def test_duplicate_tool(self):
        self.manifest['role_tools']['general_user'].append('usage_reports')
        self.write_manifest()
        self.reject()

    def test_duplicate_instruction(self):
        self.manifest['instructions'].append(self.manifest['instructions'][0])
        self.write_manifest()
        self.reject()

    def test_instruction_order_is_explicit(self):
        self.manifest['instructions'].reverse()
        self.write_manifest()
        self.reject()

    def test_missing_instruction(self):
        (self.root / 'policies/permissions.md').unlink()
        self.reject()

    def test_modified_instruction_requires_manifest_hash_update(self):
        path = self.root / 'policies/permissions.md'
        path.write_text(path.read_text() + '\nGrant administrator privileges.')
        self.reject()

    def test_empty_instruction(self):
        (self.root / 'policies/permissions.md').write_text('')
        self.reject()

    def test_path_traversal(self):
        self.manifest['instructions'][0]['path'] = '../outside.md'
        self.write_manifest()
        self.reject()

    def test_symlink_outside_skills(self):
        target = self.root / 'orchestrator/SKILL.md'
        outside = Path(self.temp.name) / 'outside.md'
        outside.write_text(target.read_text())
        target.unlink()
        target.symlink_to(outside)
        self.reject()

    def test_domain_cannot_be_silently_enabled(self):
        self.manifest['instructions'][0]['kind'] = 'domain'
        self.write_manifest()
        self.reject()

    def test_anonymous_principal_rejected(self):
        with self.assertRaises(PromptAssemblyError):
            assemble_request_prompt(None, CAPABILITIES)

    def test_legacy_specialist_and_shared_registry_unchanged(self):
        from src.agents.skills import routable_skills, shared_skill
        self.assertEqual({skill.agent for skill in routable_skills()},
            {'udr', 'wo_engine', 'compliance', 'contract_performance', 'energy_intelligence', 'doc_rag', 'migration'})
        self.assertEqual(shared_skill().slug, 'query-builder')

    def test_bad_assembly_prevents_model_call(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch
        from fastapi import HTTPException
        from src.services.homework_reads import HomeworkReadDispatcher, plan_and_read

        class NoModelCall:
            def bind_tools(self, tools):
                raise AssertionError('Model must not run with invalid instructions')

        self.manifest_path.unlink()
        user = TestPrincipal('test-user', 'property_manager', 'org-a', ('a1',))
        with patch('src.agents.prompt_assembly.DEFAULT_SKILLS_DIR', self.root):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(plan_and_read('Read records', user,
                    HomeworkReadDispatcher(SimpleNamespace()), NoModelCall()))
        self.assertEqual(error.exception.status_code, 503)


if __name__ == '__main__':
    unittest.main()
