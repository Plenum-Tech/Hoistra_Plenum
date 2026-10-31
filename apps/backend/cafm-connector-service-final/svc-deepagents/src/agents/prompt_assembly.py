"""Fail-closed assembly of reviewed instructions for the existing read-only profile."""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SKILLS_DIR = Path(__file__).resolve().parents[2] / 'skills'
INSTRUCTION_PATHS = {
    'core': ('core', 'orchestrator/SKILL.md'),
    'permissions': ('shared', 'policies/permissions.md'),
    'actions': ('shared', 'policies/actions-and-evidence.md'),
    'responses': ('shared', 'policies/responses-and-audit.md'),
    'query_builder': ('shared', 'query-builder/SKILL.md'),
}
ROLE_NAMES = {'general_user', 'property_manager', 'portfolio_manager', 'compliance_officer'}


class PromptAssemblyError(ValueError):
    """Configuration errors must prevent model execution, never trigger a fallback."""


@dataclass(frozen=True)
class AssembledPrompt:
    text: str
    metadata_json: str

    def metadata(self):
        return json.loads(self.metadata_json)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PromptAssemblyError('Duplicate manifest key')
        result[key] = value
    return result


def _hash(content):
    return hashlib.sha256(content).hexdigest()


def _load_manifest(skills_dir):
    try:
        manifest_path = skills_dir / 'prompt-manifest.json'
        if manifest_path.is_symlink() or not manifest_path.resolve().is_relative_to(skills_dir.resolve()):
            raise PromptAssemblyError('Manifest path must stay within the skills directory')
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw, object_pairs_hook=_unique_object)
    except (OSError, ValueError, TypeError):
        raise PromptAssemblyError('Prompt manifest missing or invalid') from None
    expected = {'schema_version', 'manifest_version', 'profile', 'policy_version',
                'specification_version', 'max_tool_calls', 'instructions', 'role_tools'}
    if not isinstance(manifest, dict) or set(manifest) != expected:
        raise PromptAssemblyError('Unexpected manifest fields')
    # Versions identify reviewed requirements/configuration; the read-only guard stays independent.
    if (type(manifest['schema_version']) is not int or type(manifest['max_tool_calls']) is not int
            or manifest['schema_version'] != 1 or manifest['profile'] != 'hw1-read-only'
            or manifest['policy_version'] != 'hw1-point2-v1'
            or manifest['specification_version'] != '0.37.0'
            or manifest['manifest_version'] != '1.2.0' or manifest['max_tool_calls'] != 1):
        raise PromptAssemblyError('Unsupported manifest or policy version')
    if not isinstance(manifest['instructions'], list):
        raise PromptAssemblyError('Instruction entries must be a list')
    if not isinstance(manifest['role_tools'], dict) or set(manifest['role_tools']) != ROLE_NAMES:
        raise PromptAssemblyError('Manifest must describe exactly the approved roles')
    for tools in manifest['role_tools'].values():
        if (not isinstance(tools, list) or any(not isinstance(t, str) for t in tools)
                or len(tools) != len(set(tools))):
            raise PromptAssemblyError('Invalid or duplicate tool declaration')
    return manifest, _hash(raw)


def validate_prompt_manifest(capabilities, skills_dir=None):
    """Compare declarations to Python permissions; the manifest cannot grant tools."""
    root = Path(skills_dir) if skills_dir is not None else DEFAULT_SKILLS_DIR
    manifest, manifest_hash = _load_manifest(root)
    for role in ROLE_NAMES:
        actual = sorted(name for name, spec in capabilities.items() if role in spec.roles)
        if sorted(manifest['role_tools'][role]) != actual:
            raise PromptAssemblyError('Manifest tool declarations differ from Python permissions')
    entries = manifest['instructions']
    if [entry.get('id') if isinstance(entry, dict) else None for entry in entries] != list(INSTRUCTION_PATHS):
        raise PromptAssemblyError('Missing, duplicate or incorrectly ordered instructions')
    chunks, loaded = [], []
    for entry in entries:
        if set(entry) != {'id', 'kind', 'path', 'sha256'}:
            raise PromptAssemblyError('Invalid instruction declaration')
        kind, relative = INSTRUCTION_PATHS[entry['id']]
        if entry['kind'] != kind or entry['path'] != relative:
            raise PromptAssemblyError('Unexpected instruction kind or path')
        path = root / relative
        if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
            raise PromptAssemblyError('Instruction path must stay within the skills directory')
        try:
            content = path.read_bytes()
            decoded = content.decode('utf-8')
        except (OSError, UnicodeError):
            raise PromptAssemblyError('Required instruction file unreadable') from None
        digest = _hash(content)
        if not decoded.strip() or digest != entry['sha256']:
            raise PromptAssemblyError('Instruction content empty or checksum mismatch')
        # Keep bytes/order intact so extraction does not silently rewrite the read contract.
        chunks.append(decoded)
        loaded.append(dict(entry))
    return manifest, manifest_hash, ''.join(chunks), loaded


def assemble_request_prompt(principal, capabilities, skills_dir=None):
    from ..api.homework_identity import TestPrincipal
    if not isinstance(principal, TestPrincipal) or principal.role not in ROLE_NAMES:
        raise PromptAssemblyError('Authenticated principal required')
    manifest, manifest_hash, instructions, loaded = validate_prompt_manifest(capabilities, skills_dir)
    context = {'role': principal.role, 'organization_id': principal.organization_id,
               'building_ids': list(principal.building_ids)}
    text = instructions + '\nTrusted identity context: ' + json.dumps(context)
    metadata = {'profile': manifest['profile'], 'manifest_version': manifest['manifest_version'],
                'manifest_sha256': manifest_hash, 'specification_version': manifest['specification_version'],
                'policy_version': manifest['policy_version'], 'rendered_prompt_sha256': _hash(text.encode()),
                'loaded_instructions': loaded, 'selected_domain_skills': [],
                'bound_tools': [name for name, spec in capabilities.items() if principal.role in spec.roles]}
    return AssembledPrompt(text, json.dumps(metadata, sort_keys=True))
