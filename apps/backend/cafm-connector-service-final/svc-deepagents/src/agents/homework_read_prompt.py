"""Compatibility entry point; reviewed Markdown is the sole instruction source."""
from .prompt_assembly import assemble_request_prompt, validate_prompt_manifest


def validate_homework_prompt_configuration():
    from ..services.homework_reads import CAPABILITIES
    return validate_prompt_manifest(CAPABILITIES)


def assemble_homework_read_prompt(principal):
    from ..services.homework_reads import CAPABILITIES
    original=assemble_request_prompt(principal,CAPABILITIES)
    from .unified_prompt import augment
    from .prompt_assembly import AssembledPrompt
    import json
    text,metadata=augment(original.text,original.metadata(),principal)
    return AssembledPrompt(text,json.dumps(metadata,sort_keys=True))


def build_homework_read_prompt(principal):
    # Preserve the old string interface for callers and existing regression tests.
    return assemble_homework_read_prompt(principal).text
