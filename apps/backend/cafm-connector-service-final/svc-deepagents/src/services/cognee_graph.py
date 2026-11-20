"""cognee graph memory beside Memorilla — how a company's remembered facts connect (20 Nov 2026).

Memorilla (services/memorilla.py) decides *which* memories and *which past plan* bear on a question.
cognee keeps the same company facts as a knowledge graph and adds *how they connect*: the edges
around the question, so a link similarity misses ("fire door repairs" -> lapsed FRA -> blocked BAFE
vendor) reaches the model.
Design: docs/superpowers/specs/2026-11-20-memorilla-cognee-hybrid-design.md.

* Off unless the company is in COGNEE_MEMORY_COMPANIES and COGNEE_DB_URL names cognee's OWN
  Postgres; a URL naming the main database in any spelling is refused.
* COGNEE_MEMORY_MODE: "shadow" (default) traces the graph recall only; "live" adds it to the recall
  block the model and the sub-agents receive.
* One dataset per company, "org_<org id>", each in its own database (per-dataset handlers).
* Only active company facts and corrections go in; personal preferences and plans never do.
* cognee is imported lazily: with the feature off it need not be installed. Nothing here may break
  or hold up a turn or a memory write.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

import structlog

from ..config import settings

log = structlog.get_logger(__name__)

_configured = False

#: Kinds that are the company's and may enter the graph; preferences are a person's.
GRAPH_KINDS = ("fact", "correction")

#: Background graph writes still running. asyncio keeps only weak references to tasks, so a
#: fire-and-forget write with no other owner can be collected mid-flight; this set owns them.
_pending: set[asyncio.Task] = set()


def _flagged(org_id: str) -> bool:
    """'' none, '*' all, else a comma list of ids."""
    raw = (settings.cognee_memory_companies or "").strip()
    if not raw:
        return False
    if raw == "*":
        return True
    wanted = {part.strip().lower() for part in raw.split(",") if part.strip()}
    return str(org_id).lower() in wanted


_LOCAL = frozenset({"localhost", "127.0.0.1", "::1", ""})


def _same_database(url_a: str, url_b: str) -> bool:
    """True when two Postgres URLs name the same database, however they are spelled: the driver
    suffix (`+asyncpg`), the `postgres`/`postgresql` alias, query params, a default port, the
    login used and localhost/127.0.0.1 are not differences — the same server and database under a
    second login is still production (review, 20 Nov)."""
    from urllib.parse import urlsplit  # noqa: PLC0415

    def norm(url: str) -> tuple:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        return (
            "localhost" if host in _LOCAL else host,
            parts.port or 5432,
            (parts.path or "").strip("/").lower(),
        )

    try:
        return norm(url_a) == norm(url_b)
    except ValueError:  # unparseable: refuse to call them different
        return True


def enabled(org_id: str | None) -> bool:
    """True only when the company is known, flagged AND cognee has its own database."""
    if not org_id:
        return False
    cog_url = (settings.cognee_db_url or "").strip()
    if not cog_url or _same_database(cog_url, settings.db_url or ""):
        return False
    return _flagged(org_id)


def live() -> bool:
    """The graph joins the recall block only in "live" mode; anything else is shadow."""
    return (settings.cognee_memory_mode or "").strip().lower() == "live"


def _dataset(org_id: str) -> str:
    return f"org_{str(org_id).lower()}"


def _cognee_settings() -> dict:
    """cognee 1.6.x config, all three stores on cognee's OWN Postgres (COGNEE_DB_URL).

    Field names and types follow cognee 1.6.10 (20 Nov smoke: it has no connection-string field):
    relational takes str ports, pgvector and the Postgres graph int ports, and all want explicit
    credentials because backend access control (on by default) gives every dataset — every
    company — its own database, the isolation this relies on.
    """
    from urllib.parse import unquote, urlsplit  # noqa: PLC0415

    parts = urlsplit(settings.cognee_db_url.strip())
    host, port = parts.hostname or "", parts.port or 5432
    user, password = unquote(parts.username or ""), unquote(parts.password or "")
    name = (parts.path or "").lstrip("/") or "cognee"
    model = settings.openai_model or "gpt-4o-mini"
    # Graph building uses the orchestrator's own model, wherever OPENAI_API_BASE points — a local
    # OpenAI-compatible server (Ollama, llama.cpp) included; local servers take any key.
    base = (settings.openai_api_base or "").strip()
    llm: dict = {
        "llm_provider": "openai",
        "llm_api_key": settings.openai_api_key or ("local" if base else ""),
        "llm_model": model if "/" in model else f"openai/{model}",
    }
    if base:
        llm["llm_endpoint"] = base
    if (settings.cognee_llm_reasoning_effort or "").strip():
        llm["llm_args"] = {
            "extra_body": {"reasoning_effort": settings.cognee_llm_reasoning_effort.strip()}
        }
    embedding: dict = {
        "embedding_provider": settings.cognee_embedding_provider,
        "embedding_model": settings.cognee_embedding_model,
        "embedding_dimensions": int(settings.cognee_embedding_dimensions),
        "embedding_api_key": settings.openai_api_key,
    }
    if (settings.cognee_embedding_endpoint or "").strip():
        embedding["embedding_endpoint"] = settings.cognee_embedding_endpoint.strip()
    if (settings.cognee_embedding_tokenizer or "").strip():
        embedding["huggingface_tokenizer"] = settings.cognee_embedding_tokenizer.strip()
    return {
        "relational": {
            "db_provider": "postgres",
            "db_host": host,
            "db_port": str(port),
            "db_username": user,
            "db_password": password,
            "db_name": name,
        },
        # Per-dataset handlers (not *_shared): every company's dataset gets its own database.
        "vector": {
            "vector_db_provider": "pgvector",
            "vector_db_host": host,
            "vector_db_port": port,
            "vector_db_username": user,
            "vector_db_password": password,
            "vector_db_name": name,
            "vector_dataset_database_handler": "pgvector",
        },
        "graph": {
            "graph_database_provider": "postgres",
            "graph_database_host": host,
            "graph_database_port": port,
            "graph_database_username": user,
            "graph_database_password": password,
            "graph_database_name": name,
            "graph_dataset_database_handler": "postgres_graph",
        },
        "llm": llm,
        # cognee's default text-embedding-3-large answers 403 model_not_found on this OpenAI
        # project (20 Nov smoke); -3-small is what chat_memories already embeds with.
        "embedding": embedding,
    }


def _configure_cognee() -> None:
    """Point cognee at its own Postgres and the orchestrator's OpenAI setup. Once per process;
    retried if a setter raises."""
    global _configured
    if _configured:
        return
    # cognee's session memory (CACHING, on by default) records every search into one shared
    # 'default_session' — every company runs as cognee's one default user — and adds an LLM call
    # per search. Off unless an operator set CACHING; read lazily by cognee, so set before use.
    os.environ.setdefault("CACHING", "false")
    # Every company's dataset in its own database rests on cognee's backend access control; it is
    # on by default, and is never allowed off here.
    os.environ["ENABLE_BACKEND_ACCESS_CONTROL"] = "true"
    import cognee  # noqa: PLC0415 — lazy: off means cognee need not be installed

    cfg = _cognee_settings()
    cognee.config.set_relational_db_config(cfg["relational"])
    cognee.config.set_vector_db_config(cfg["vector"])
    cognee.config.set_graph_db_config(cfg["graph"])
    cognee.config.set_llm_config(cfg["llm"])
    cognee.config.set_embedding_config(cfg["embedding"])
    _configured = True


async def _configured_off_loop() -> None:
    """Import and configure cognee in a worker thread: the first import takes seconds and would
    otherwise stop the event loop — every turn — where no timeout can interrupt it (review)."""
    if not _configured:
        await asyncio.to_thread(_configure_cognee)


# ── company facts in, forgotten facts out ───────────────────────────────────────────────────────


def fact_text(kind: str, text_: str, subject: str | None = None) -> str:
    """A company memory as the graph keeps it — Memorilla's own wording (memorilla.memory_text), so
    both stores hold the same sentence and a forget finds exactly what was added."""
    from . import memorilla  # noqa: PLC0415

    return memorilla.memory_text(kind, text_, subject)


def _graph_texts(items: list[dict[str, Any]]) -> list[str]:
    """Only active company facts and corrections: never a personal preference (the graph is
    company-wide) and never a pending teaching (recall from the graph cannot check approval)."""
    return [
        fact_text(i["kind"], i["text"], i.get("subject"))
        for i in items
        if i.get("kind") in GRAPH_KINDS
        and not i.get("personal")
        and i.get("active")
        and (i.get("text") or "").strip()
    ]


async def _cognee_add(org_id: str, texts: list[str]) -> None:
    """The one place facts are written to cognee: add, then build the graph. Faked in tests."""
    await _configured_off_loop()
    import cognee  # noqa: PLC0415

    await cognee.add(texts, dataset_name=_dataset(org_id))
    await cognee.cognify(datasets=[_dataset(org_id)])


async def _cognee_forget(org_id: str, texts: list[str]) -> None:
    """Remove facts from the company's graph: cognee keeps a text by its content hash, so each
    text's data row is found by cognee's own hash of it and forgotten by id. Faked in tests."""
    await _configured_off_loop()
    import cognee  # noqa: PLC0415
    from cognee.modules.ingestion.data_types.TextData import TextData  # noqa: PLC0415

    name = _dataset(org_id)
    dataset = next((d for d in await cognee.datasets.list_datasets() if d.name == name), None)
    if dataset is None:
        return
    wanted = {await TextData(t).aget_identifier() for t in texts}
    for data in await cognee.datasets.list_data(dataset.id):
        if getattr(data, "content_hash", None) in wanted:
            await cognee.forget(data_id=data.id, dataset=name)


#: One lock per company: its graph writes run one at a time, in the order scheduled, so a forget
#: never runs before an earlier add of the same fact has finished (review, 20 Nov).
_locks: dict[str, asyncio.Lock] = {}


async def _run(what: str, fn, org_id: str, texts: list[str]) -> None:
    try:
        async with _locks.setdefault(org_id, asyncio.Lock()):
            await fn(org_id, texts)
    except Exception as exc:  # noqa: BLE001 — a graph write never reaches the memory write
        log.warning("cognee_graph.write_failed", what=what, error=str(exc)[:300])


def _soon(what: str, fn, org_id: str | None, items: list[dict[str, Any]]) -> None:
    """Schedule a graph write without waiting: building the graph takes seconds (an LLM reads each
    fact), and store/review/forget answer people. Never raises."""
    try:
        if not org_id or not enabled(org_id):
            return
        texts = _graph_texts(items)
        if not texts:
            return
        task = asyncio.get_running_loop().create_task(_run(what, fn, str(org_id), texts))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except RuntimeError:  # no running loop
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("cognee_graph.schedule_failed", what=what, error=str(exc)[:200])


def add_memories_soon(org_id: str | None, items: list[dict[str, Any]]) -> None:
    """New or newly approved memories: the company's active facts and corrections join its graph.
    `items`: {"kind", "text", "subject", "personal", "active"}."""
    _soon("add", _cognee_add, org_id, items)


def forget_memories_soon(org_id: str | None, items: list[dict[str, Any]]) -> None:
    """Forgotten memories leave the graph, so what people forgot never comes back as a
    connection."""
    _soon("forget", _cognee_forget, org_id, [i | {"active": True} for i in items])


# ── reading the graph ───────────────────────────────────────────────────────────────────────────

#: Cap on the context-only graph read (no LLM call: an embedding and a graph lookup). Slower =
#: no edges this turn; recall then is Memorilla's alone.
GRAPH_TIMEOUT_S = 3.0

#: (source, relation, target, description) — one cognee connection.
Edge = tuple[str, str, str, str]

_EDGE = re.compile(
    r"^(?P<src>.+?) --\[(?P<rel>[^\]]+)\]--> (?P<dst>.+?)(?:\s{2,}\((?P<desc>.*)\))?\s*$"
)


def parse_edges(context: str | None) -> list[Edge]:
    """cognee's context-only text -> its connections. cognee 1.6 lists `Nodes:` (name + content)
    then `Connections:`, one per line: `source --[relation]--> target  (description)`. Only lines
    under `Connections:` count — node content can hold anything."""
    if not context:
        return []
    _, sep, tail = context.partition("Connections:")
    edges: list[Edge] = []
    for line in (tail if sep else "").splitlines():
        # cognee wraps the whole context in backticks: the last line ends with one (live smoke).
        m = _EDGE.match(line.strip().rstrip("`").rstrip())
        if m:
            edges.append(
                (m["src"].strip(), m["rel"].strip(), m["dst"].strip(), (m["desc"] or "").strip())
            )
    return edges


def _drop(task: asyncio.Task) -> None:
    """Done-callback for an abandoned read: forget it and swallow its outcome."""
    _pending.discard(task)
    if not task.cancelled():
        task.exception()


def parse_nodes(context: str | None) -> dict[str, str]:
    """cognee's `Nodes:` section -> {node name: node content}. A stored fact is a node whose name is
    a truncated preview and whose content is the fact's full text."""
    out: dict[str, str] = {}
    name: str | None = None
    buf: list[str] = []
    inside = False
    for line in (context or "").partition("Connections:")[0].splitlines():
        stripped = line.strip()
        if not inside and line.startswith("Node: "):
            name, buf = line[len("Node: ") :].strip(), []
        elif stripped == "__node_content_start__":
            inside = True
        elif stripped == "__node_content_end__":
            inside = False
            if name is not None:
                out[name] = "\n".join(buf).strip()
        elif inside:
            buf.append(line)
    return out


async def _cognee_context(org_id: str, question: str) -> str:
    """The one place the graph is read: GRAPH_COMPLETION with only_context — the retrieved nodes and
    connections, no answer written (no LLM call). Faked in tests."""
    await _configured_off_loop()
    import cognee  # noqa: PLC0415
    from cognee import SearchType  # noqa: PLC0415

    found = await cognee.search(
        query_text=question,
        query_type=SearchType.GRAPH_COMPLETION,
        datasets=[_dataset(org_id)],
        only_context=True,
    )
    parts: list[str] = []
    for item in found or []:  # 1.6 wraps hits as {"dataset_name", ..., "search_result": ...}
        inner = item.get("search_result") if isinstance(item, dict) else item
        parts.extend(str(x) for x in (inner if isinstance(inner, list) else [inner]))
    return "\n".join(parts)


async def graph_context(question: str, org_id: str | None) -> dict[str, Any] | None:
    """The company's graph around a question: {"edges", "nodes", "latency_ms", "error"}, or None
    when the graph is off for this company. Never raises; capped at GRAPH_TIMEOUT_S."""
    if not enabled(org_id):
        return None
    t0 = time.monotonic()
    edges: list[Edge] = []
    nodes: dict[str, str] = {}
    error: str | None = None
    try:
        task = asyncio.ensure_future(_cognee_context(str(org_id), question))
        done, _ = await asyncio.wait({task}, timeout=GRAPH_TIMEOUT_S)
        if not done:
            # Stop waiting now; a read that is slow to cancel finishes on its own (review).
            task.cancel()
            _pending.add(task)
            task.add_done_callback(_drop)
            raise TimeoutError
        context = task.result()
        edges, nodes = parse_edges(context), parse_nodes(context)
    except TimeoutError:
        error = "timeout"
    except Exception as exc:  # noqa: BLE001 — a graph read never reaches the turn
        # A company with nothing in its graph yet (or everything forgotten): cognee raises
        # NoDataError. That is "no connections", not a failed read (live smoke, 20 Nov).
        if type(exc).__name__ != "NoDataError":
            error = str(exc)[:300]
    latency_ms = int((time.monotonic() - t0) * 1000)
    if error:
        log.warning("cognee_graph.read_failed", error=error, latency_ms=latency_ms)
    return {"edges": edges, "nodes": nodes, "latency_ms": latency_ms, "error": error}


# ── the graph picks which remembered facts connect ──────────────────────────────────────────────
#
# Offline comparison 20 Nov (9 questions x 3 runs, Plenum's export, the orchestrator's model):
# entity-to-entity edges added little — 1-5 per question, mostly "x located_in building". The
# graph's value is WHICH stored facts it links to the question's entities: AHU-3 -> "the vendor for
# AHU-3 is Apex Mechanical" -> "Apex Mechanical must not be instructed ... CHAS". Recall coverage:
# Memorilla alone 8/9 questions, Memorilla + graph-linked facts 9/9.

#: Most facts the graph adds to Memorilla's recall.
LINKED_CAP = 4
#: Relations cognee draws from a stored text to what it mentions.
CHUNK_RELATIONS = frozenset({"contains", "is_part_of", "made_from", "mentions"})
_STOP = frozenset(
    """about after again also anything before being could does doing done each
first from have here into just know last like many more most much next only other over should
since some such than that their them then there these they this those through today under until
very want week what when where which while will with would year your""".split()
)
_CODE = re.compile(r"\b[A-Z]{1,5}-?\d[\w-]*\b")  # B-101, SP203-1, AHU-3
_PROPER = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")  # Bishopsgate Tower, Apex Mechanical
_WORD = re.compile(r"[a-z][a-z-]{3,}")


def _anchors(question: str, hits: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    """(strong, weak) anchors, lowercased. Strong: the recalled memories' subjects, proper-name
    phrases and codes in the question and the memories. Weak: the question's own content words."""
    strong: set[str] = set()
    for t in [question] + [h.get("text") or "" for h in hits]:
        strong |= {m.lower() for m in _CODE.findall(t)}
        strong |= {m.lower() for m in _PROPER.findall(t)}
    strong |= {(h.get("subject") or "").lower() for h in hits if h.get("subject")}
    weak = {w for w in _WORD.findall(question.lower()) if w not in _STOP} - strong
    return strong, weak


def _is_chunk(edge: Edge) -> bool:
    """An edge from a stored text to something it mentions (not a link between two things)."""
    return edge[1].lower() in CHUNK_RELATIONS or edge[0].startswith(("Question:", "["))


def linked_texts(
    question: str, hits: list[dict[str, Any]], graph: dict[str, Any] | None
) -> list[str]:
    """The stored texts the graph links to this question: fact nodes whose "contains" edge reaches
    an entity named by the question or by a recalled memory (so a recalled fact's vendor leads on to
    that vendor's other facts — the second hop). In cognee's order, without repeats."""
    if not graph:
        return []
    strong, weak = _anchors(question, hits)
    anchors = strong | weak
    nodes = graph.get("nodes") or {}
    out: list[str] = []
    for edge in graph.get("edges") or []:
        content = nodes.get(edge[0]) or ""
        if (
            content
            and content not in out
            and _is_chunk(edge)
            and any(a and a in edge[2].lower() for a in anchors)
        ):
            out.append(content)
    return out


# ── the recall hook (orchestrator._build_stateful_input) ────────────────────────────────────────


def _caller_org() -> str | None:
    from ..http_client import caller_organization_id  # noqa: PLC0415
    from .principal import caller_principal  # noqa: PLC0415

    p = caller_principal.get()
    return caller_organization_id.get() or (
        str(p.organization_id) if p and p.organization_id else None
    )


async def graph_for(question: str) -> dict[str, Any] | None:
    """The acting company's graph read for this question — run beside Memorilla's recall. None
    when the graph is off for the company. Never raises."""
    try:
        return await graph_context(question, _caller_org())
    except Exception as exc:  # noqa: BLE001
        log.warning("cognee_graph.graph_for_failed", error=str(exc)[:200])
        return None


def shadow_soon(question: str, hits: list[dict[str, Any]]) -> None:
    """Shadow mode: read the graph and trace what it would have linked AFTER the turn has moved on —
    the turn never waits for a read whose result it does not use (review). Nothing is scheduled
    when the graph is off for the acting company. Never raises."""
    try:
        org = _caller_org()
        if live() or not enabled(org):
            return

        async def run() -> None:
            await linked_memories(question, hits, await graph_context(question, org))

        task = asyncio.get_running_loop().create_task(run())
        _pending.add(task)
        task.add_done_callback(_drop)
    except RuntimeError:  # no running loop
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("cognee_graph.shadow_failed", error=str(exc)[:200])


async def linked_memories(
    question: str,
    hits: list[dict[str, Any]],
    graph: dict[str, Any] | None,
    *,
    cap: int = LINKED_CAP,
) -> list[dict[str, Any]]:
    """The memories the graph links to this question that Memorilla did not already recall, as
    chat-memory rows. Only memories the caller may see (chat_memories.visible): a fact forgotten,
    pending or another person's never comes back from a stale graph. Traced as one "graph recall"
    span; returned only in live mode (shadow: [] — traced only). Never raises."""
    if graph is None:
        return []
    try:
        from . import chat_memories  # noqa: PLC0415

        texts = linked_texts(question, hits, graph)
        have = {h.get("id") for h in hits}
        linked: list[dict[str, Any]] = []
        if texts:
            # Only the caller's visible company facts and corrections, matched by the whole text
            # the graph keeps for them (fact_text) — never a person's preference that merely shares
            # a sentence with a fact (review).
            wording: dict[str, str] = {}
            for m in await chat_memories.visible():
                if (
                    m.get("kind") in GRAPH_KINDS
                    and m.get("user_id") is None
                    and m.get("text")
                    and m.get("id") not in have
                ):
                    ft = fact_text(m["kind"], m["text"], m.get("subject"))
                    if any(ft in c for c in texts):
                        wording[m["id"]] = ft
                        linked.append(m)
            order = {c: n for n, c in enumerate(texts)}
            linked.sort(key=lambda m: min(order[c] for c in texts if wording[m["id"]] in c))
            linked = linked[:cap]
        mode = "live" if live() else "shadow"
        try:
            from ..agents import trace  # noqa: PLC0415

            trace.on_memory(
                "graph recall",
                {"question": question, "mode": mode, "recalled_memories": len(hits)},
                {
                    "linked": [
                        {"id": m["id"], "text": (m.get("text") or "")[:200]} for m in linked
                    ],
                    "linked_texts": len(texts),
                    "edges": len(graph.get("edges") or []),
                },
                ok=graph.get("error") is None,
                error=graph.get("error"),
                latency_ms=graph.get("latency_ms"),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("cognee_graph.trace_failed", error=str(exc)[:200])
        return linked if mode == "live" else []
    except Exception as exc:  # noqa: BLE001
        log.warning("cognee_graph.linked_memories_failed", error=str(exc)[:200])
        return []
