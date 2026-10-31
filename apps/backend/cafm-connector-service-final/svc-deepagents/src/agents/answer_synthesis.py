"""One tool-free answer stage over unchanged authorized execution results."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from langchain_core.messages import HumanMessage, SystemMessage

ROOT = Path(__file__).resolve().parents[2] / 'skills'
MAX_INPUT_BYTES = 150_000
MAX_ANSWER_CHARS = 14_000
FAILURE_ANSWER = ('The tool results were retained, but I could not generate a verified explanation. '
                  'No additional operation was performed. Please review the recorded results.')


def build_answer_input(question, authorized_context, execution):
    """Wrap raw authorized calls; do not rename fields or infer missing evidence."""
    value = {
        'question': question,
        'conversation_context': deepcopy(authorized_context),
        'outcome': execution.get('outcome'),
        'tool_calls': deepcopy(execution.get('tool_calls', [])),
        'source_context': deepcopy(execution.get('source_context', {})),
        'execution_observation': {
            'provider': deepcopy(execution.get('provider_execution', {})),
            'actual_tools': deepcopy(execution.get('actual_tools', [])),
        },
        'environment': {'deployment': 'isolated_homework', 'fixtures_are_fictional': True,
                        'external_delivery_enabled': False},
    }
    if 'limitations' in execution:
        value['limitations'] = deepcopy(execution['limitations'])
    # Refuse oversized evidence rather than silently dropping a page or request part.
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    if len(serialized.encode()) > MAX_INPUT_BYTES:
        raise ValueError('Answer evidence exceeds the bounded input limit')
    return value


def answer_prompt():
    """Validate this stage independently; it never loads domain prompt prose."""
    from .unified_prompt import read, sha, unique
    manifest = json.loads(read('unified-manifest.json', ROOT), object_pairs_hook=unique)
    entry = manifest['answer_instruction']
    raw = read('orchestrator/answer-synthesis.md', ROOT)
    if entry != {'path': 'orchestrator/answer-synthesis.md', 'sha256': sha(raw)}:
        raise ValueError('Answer instruction checksum mismatch')
    return raw.decode(), sha(raw)


async def generate_answer(model, answer_input):
    """No tool binding, retries, backend decisions, or unbounded response generation."""
    prompt, prompt_hash = answer_prompt()
    serialized = json.dumps(answer_input, ensure_ascii=False, default=str)
    if len(serialized.encode()) > MAX_INPUT_BYTES:
        raise ValueError('Answer evidence exceeds the bounded input limit')
    metadata = {'status': 'started', 'prompt_sha256': prompt_hash, 'tools_bound': [],
                'evidence_sha256': hashlib.sha256(serialized.encode()).hexdigest(),
                'successful_responses': 0, 'token_usage': None}
    try:
        response = await asyncio.wait_for(model.ainvoke(
            [SystemMessage(content=prompt), HumanMessage(content=serialized)], max_tokens=2500), timeout=60)
        metadata['successful_responses'] = 1
        metadata['token_usage'] = getattr(response, 'usage_metadata', None)
        text = response.content
        # A response with a proposed call is invalid even if the provider ignores tools=[] semantics.
        if getattr(response, 'tool_calls', None) or not isinstance(text, str) or not text.strip() or len(text) > MAX_ANSWER_CHARS:
            raise ValueError('Invalid final answer response')
        metadata['status'] = 'generated'
        return text.strip(), metadata
    except Exception as error:
        # Retrying a final answer must never replay an earlier business operation.
        metadata.update(status='answer_generation_failed', reason=type(error).__name__)
        return FAILURE_ANSWER, metadata


def create_answer_model():
    """Create a fresh unbound model; keep runtime configuration out of pure unit tests."""
    from ..llm_factory import create_chat_model
    return create_chat_model()
