"""
svc-ingestion/src/prompt_engine/engine.py

Task 2.7 — Prompt Engine.

Loads, caches, and renders Jinja2 prompt templates for all ingestion agents.

Template resolution priority (highest → lowest):
  1. Active A/B test variant (if a running PromptAbTest exists for this agent/doc_type)
  2. Active DB template (prompt_templates table — is_active=True, highest version)
  3. Filesystem .j2 file (always present as fallback)

Redis caching:
  - prompt_tpl:{agent_id}:{doc_type}  → JSON with system_prompt + user_template + version
    TTL: cache_ttl_seconds (default 300s = 5 min) — hot-reload within that window
  - prompt_ab:{agent_id}:{doc_type}   → JSON with A/B test state
    TTL: 60s — changes appear within 1 minute

Hot-reload (filesystem):
  Jinja2 Environment uses auto_reload=True. In development (cache_ttl_seconds=0)
  every render re-reads from the DB.

Template file format (.j2):
  Templates define two Jinja2 blocks:
    {% block system %}  ← system prompt (static role description)
    {% block user %}    ← user message template (Jinja2 with variables)

  If a template file lacks these blocks its full content is used as the user
  message and an empty system prompt is returned.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import jinja2
from opentelemetry import trace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cafm_shared.logging import get_logger

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

# ── Template ID helpers ────────────────────────────────────────────────────────

# Maps template_id (e.g. "pdf/inspection_report") → (agent_id, doc_type)
_TEMPLATE_ID_TO_AGENT: dict[str, tuple[str, str]] = {
    "pdf/inspection_report": ("pdf-agent", "inspection_report"),
    "pdf/vendor_invoice":    ("pdf-agent", "vendor_invoice"),
    "pdf/equipment_manual":  ("pdf-agent", "equipment_manual"),
    "pdf/compliance_cert":   ("pdf-agent", "compliance_cert"),
    "pdf/field_notes":       ("pdf-agent", "field_notes"),
    "excel/generic_excel":   ("excel-agent", "generic_excel"),
    "word/generic_word":     ("word-agent", "generic_word"),
    "csv/schema_mapper":     ("csv-agent", "schema_mapper"),
}


def _parse_template_id(template_id: str) -> tuple[str, str]:
    """Return (agent_id, doc_type) for a given template_id string."""
    if template_id in _TEMPLATE_ID_TO_AGENT:
        return _TEMPLATE_ID_TO_AGENT[template_id]
    # Fallback: derive from path format "<subdir>/<name>"
    parts = template_id.split("/", 1)
    if len(parts) == 2:
        subdir, name = parts
        return (f"{subdir}-agent", name)
    return ("unknown-agent", template_id)


# ── Return type ────────────────────────────────────────────────────────────────


@dataclass
class RenderedPrompt:
    """
    Output of PromptEngine.render().

    Pass system_prompt as the "system" role message and user_message as
    the "user" role message when building Anthropic API messages.
    """

    template_id: str
    version: str                     # "1.0" or "filesystem"
    system_prompt: str
    user_message: str
    variant: str | None = None       # "a" or "b" when an A/B test is active
    ab_test_id: str | None = None    # UUID of active PromptAbTest row
    template_db_id: str | None = None  # UUID of PromptTemplate row used (None = filesystem)
    from_cache: bool = False
    render_ms: int = 0


# ── Internal cache value ───────────────────────────────────────────────────────


@dataclass
class _CachedTemplate:
    system_prompt: str
    user_template: str              # raw Jinja2 template string (not yet rendered)
    version: str
    template_db_id: str | None
    variant: str | None = None
    ab_test_id: str | None = None


# ── Engine ────────────────────────────────────────────────────────────────────


class PromptEngine:
    """
    Loads, caches, and renders Jinja2 prompt templates.

    Instantiate once per service and reuse across requests.
    """

    def __init__(
        self,
        templates_dir: Path,
        redis: Any,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        cache_ttl_seconds: int = 300,
    ) -> None:
        self._templates_dir = templates_dir
        self._redis = redis
        self._session_factory = session_factory
        self._cache_ttl = cache_ttl_seconds

        # Jinja2 environment backed by the filesystem templates directory.
        # auto_reload=True ensures Jinja2 re-reads .j2 files when mtime changes.
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(templates_dir)),
            auto_reload=True,
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=jinja2.Undefined,  # silently ignore missing vars (agents supply what they have)
        )

    # ── Public API ──────────────────────────────────────────────────────────────

    async def render(
        self,
        template_id: str,
        variables: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> RenderedPrompt:
        """
        Render a prompt template with the provided variables.

        Args:
            template_id:  Template path, e.g. "pdf/inspection_report".
            variables:    Dict of Jinja2 template variables.
            request_id:   Optional identifier used for deterministic A/B variant
                          assignment (same request_id always gets same variant).

        Returns:
            RenderedPrompt with system_prompt + user_message ready for Claude.
        """
        t0 = time.monotonic()
        agent_id, doc_type = _parse_template_id(template_id)

        with tracer.start_as_current_span("prompt_engine.render") as span:
            span.set_attribute("cafm.template_id", template_id)
            span.set_attribute("cafm.agent_id", agent_id)
            span.set_attribute("cafm.doc_type", doc_type)

            try:
                cached = await self._resolve_template(agent_id, doc_type, request_id)
            except Exception as exc:
                logger.warning(
                    "prompt_engine.resolve_failed_using_filesystem",
                    template_id=template_id,
                    error=str(exc),
                )
                cached = self._load_from_filesystem(template_id)

            # Render the user_template Jinja2 string with supplied variables
            try:
                jinja_template = self._env.from_string(cached.user_template)
                user_message = jinja_template.render(**variables)
            except jinja2.TemplateError as exc:
                logger.error(
                    "prompt_engine.jinja_render_error",
                    template_id=template_id,
                    error=str(exc),
                )
                # Fall back to raw template (no substitution)
                user_message = cached.user_template

            render_ms = round((time.monotonic() - t0) * 1000)
            span.set_attribute("cafm.template_version", cached.version)
            span.set_attribute("cafm.from_cache", cached.template_db_id is not None)
            span.set_attribute("cafm.ab_variant", cached.variant or "none")
            span.set_attribute("cafm.render_ms", render_ms)

            logger.debug(
                "prompt_engine.rendered",
                template_id=template_id,
                version=cached.version,
                variant=cached.variant,
                render_ms=render_ms,
            )

            return RenderedPrompt(
                template_id=template_id,
                version=cached.version,
                system_prompt=cached.system_prompt,
                user_message=user_message,
                variant=cached.variant,
                ab_test_id=cached.ab_test_id,
                template_db_id=cached.template_db_id,
                from_cache=bool(cached.template_db_id),
                render_ms=render_ms,
            )

    async def invalidate(self, agent_id: str, doc_type: str) -> None:
        """
        Remove the Redis cache entry for an agent/doc_type combination.

        Call this after updating a template in the DB to force the next
        render to re-query immediately (instead of waiting for TTL expiry).
        """
        cache_key = f"prompt_tpl:{agent_id}:{doc_type}"
        ab_key = f"prompt_ab:{agent_id}:{doc_type}"
        try:
            await self._redis.delete(cache_key, ab_key)
            logger.info("prompt_engine.cache_invalidated", agent_id=agent_id, doc_type=doc_type)
        except Exception as exc:
            logger.warning("prompt_engine.cache_invalidate_failed", error=str(exc))

    # ── Template resolution ─────────────────────────────────────────────────────

    async def _resolve_template(
        self,
        agent_id: str,
        doc_type: str,
        request_id: str | None,
    ) -> _CachedTemplate:
        """
        Resolve the best template for (agent_id, doc_type).

        Checks:
          1. Redis cache → fast path
          2. DB A/B test (active) → select variant
          3. DB active template → use directly
          4. Filesystem fallback
        """
        cache_key = f"prompt_tpl:{agent_id}:{doc_type}"
        ab_key = f"prompt_ab:{agent_id}:{doc_type}"

        # ── Redis fast path ───────────────────────────────────────────────
        if self._cache_ttl > 0:
            try:
                cached_raw = await self._redis.get(cache_key)
                if cached_raw:
                    data = json.loads(cached_raw)
                    return _CachedTemplate(**data)
            except Exception as exc:
                logger.debug("prompt_engine.cache_miss", reason=str(exc))

        # ── DB lookup ─────────────────────────────────────────────────────
        if self._session_factory is None:
            return self._load_from_filesystem(f"{agent_id.replace('-agent','')}/{doc_type}")

        # Import here to avoid circular imports at module level
        from models.ingestion import PromptAbTest, PromptTemplate  # noqa: PLC0415

        async with self._session_factory() as session:
            # Check for running A/B test
            ab_result = await session.execute(
                select(PromptAbTest)
                .where(PromptAbTest.status == "running")
                .join(
                    PromptTemplate,
                    PromptAbTest.template_a_id == PromptTemplate.id,
                )
                .where(PromptTemplate.agent_id == agent_id)
                .where(PromptTemplate.doc_type == doc_type)
                .limit(1)
            )
            ab_test = ab_result.scalar_one_or_none()

            if ab_test is not None:
                return await self._resolve_ab_variant(
                    session, ab_test, agent_id, doc_type, request_id, ab_key
                )

            # Active template (no A/B test)
            tpl_result = await session.execute(
                select(PromptTemplate)
                .where(PromptTemplate.agent_id == agent_id)
                .where(PromptTemplate.doc_type == doc_type)
                .where(PromptTemplate.is_active == True)  # noqa: E712
                .order_by(PromptTemplate.created_at.desc())
                .limit(1)
            )
            tpl = tpl_result.scalar_one_or_none()

            if tpl is None:
                # No DB template — use filesystem
                template_id = f"{agent_id.replace('-agent','')}/{doc_type}"
                return self._load_from_filesystem(template_id)

            cached = _CachedTemplate(
                system_prompt=tpl.system_prompt,
                user_template=tpl.user_template,
                version=tpl.version,
                template_db_id=str(tpl.id),
            )

        # Store in Redis
        await self._cache_template(cache_key, cached)
        return cached

    async def _resolve_ab_variant(
        self,
        session: AsyncSession,
        ab_test: Any,
        agent_id: str,
        doc_type: str,
        request_id: str | None,
        ab_key: str,
    ) -> _CachedTemplate:
        """Select template A or B based on deterministic hash of request_id."""
        # 50/50 split by default — deterministic on request_id
        if request_id:
            digest = int(hashlib.md5(request_id.encode(), usedforsecurity=False).hexdigest(), 16)
            use_b = (digest % 100) >= 50
        else:
            import random
            use_b = random.random() >= 0.5

        variant = "b" if use_b else "a"
        tpl_id = ab_test.template_b_id if use_b else ab_test.template_a_id

        from models.ingestion import PromptTemplate  # noqa: PLC0415

        tpl_result = await session.execute(
            select(PromptTemplate).where(PromptTemplate.id == tpl_id)
        )
        tpl = tpl_result.scalar_one_or_none()

        if tpl is None:
            # A/B test references deleted template — fall back to filesystem
            template_id = f"{agent_id.replace('-agent','')}/{doc_type}"
            return self._load_from_filesystem(template_id)

        cached = _CachedTemplate(
            system_prompt=tpl.system_prompt,
            user_template=tpl.user_template,
            version=tpl.version,
            template_db_id=str(tpl.id),
            variant=variant,
            ab_test_id=str(ab_test.id),
        )

        # Cache A/B result briefly (60s) — refresh often to catch test state changes
        try:
            await self._redis.setex(ab_key, 60, json.dumps({
                "system_prompt": cached.system_prompt,
                "user_template": cached.user_template,
                "version": cached.version,
                "template_db_id": cached.template_db_id,
                "variant": cached.variant,
                "ab_test_id": cached.ab_test_id,
            }))
        except Exception:
            pass

        return cached

    # ── Filesystem fallback ────────────────────────────────────────────────────

    def _load_from_filesystem(self, template_id: str) -> _CachedTemplate:
        """
        Load template from .j2 file on disk.

        Expects blocks:
          {% block system %}...{% endblock %}
          {% block user %}...{% endblock %}

        If blocks are absent, the full file content becomes the user_template
        and system_prompt is a default CAFM extraction role description.
        """
        # template_id like "pdf/inspection_report" → "pdf/inspection_report.j2"
        path_str = f"{template_id}.j2"

        try:
            raw = self._env.loader.get_source(self._env, path_str)[0]  # type: ignore[index]
        except jinja2.TemplateNotFound:
            logger.warning("prompt_engine.template_not_found", path=path_str)
            raw = _DEFAULT_USER_TEMPLATE

        system_prompt, user_template = _split_blocks(raw)

        return _CachedTemplate(
            system_prompt=system_prompt,
            user_template=user_template,
            version="filesystem",
            template_db_id=None,
        )

    # ── Redis helpers ──────────────────────────────────────────────────────────

    async def _cache_template(self, key: str, tpl: _CachedTemplate) -> None:
        if self._cache_ttl <= 0:
            return
        try:
            await self._redis.setex(key, self._cache_ttl, json.dumps({
                "system_prompt": tpl.system_prompt,
                "user_template": tpl.user_template,
                "version": tpl.version,
                "template_db_id": tpl.template_db_id,
                "variant": tpl.variant,
                "ab_test_id": tpl.ab_test_id,
            }))
        except Exception as exc:
            logger.debug("prompt_engine.cache_write_failed", error=str(exc))


# ── Block parser ───────────────────────────────────────────────────────────────


def _split_blocks(raw: str) -> tuple[str, str]:
    """
    Extract {% block system %} and {% block user %} from a template string.

    Returns (system_prompt, user_template).
    If blocks are absent, returns ("", raw).
    """
    import re

    system_match = re.search(
        r"\{%-?\s*block\s+system\s*-?%\}(.*?)\{%-?\s*endblock\s*-?%\}",
        raw,
        re.DOTALL,
    )
    user_match = re.search(
        r"\{%-?\s*block\s+user\s*-?%\}(.*?)\{%-?\s*endblock\s*-?%\}",
        raw,
        re.DOTALL,
    )

    if system_match and user_match:
        return system_match.group(1).strip(), user_match.group(1).strip()

    # No blocks — treat entire file as user_template
    return _DEFAULT_SYSTEM_PROMPT, raw.strip()


# ── Defaults ───────────────────────────────────────────────────────────────────

_DEFAULT_SYSTEM_PROMPT = (
    "You are a CAFM (Computer-Aided Facilities Management) data extraction specialist. "
    "Extract structured data accurately from the provided document. "
    "Return only valid JSON as instructed."
)

_DEFAULT_USER_TEMPLATE = (
    "Extract all CAFM-relevant data from: {{ source_filename }}. "
    "Return a JSON object with keys: entities, confidence, audit."
)
