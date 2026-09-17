"""The table catalogue: what each plenum_cafm table is FOR, searchable by meaning.

Until this existed the UDR agent received ``get_schema()`` — 221 tables and 2,560 column names
in 8.6 KB of text — and guessed. Measured 17 Sep 2026: asked "which assets have never been
scored?", it wrote a LEFT JOIN against a table that does not exist. Column names say what a
table holds; nothing said what it is for, how its rows relate to other tables, or what its
values look like.

One row per table, built from the live database:

- **purpose / answers / not_for / grain** — written by a small Claude model from the columns,
  their sample values, the keys, the links and three real rows. Falls back to a heuristic
  sentence when there is no key, so the catalogue is never empty.
- **columns** with type, nullability, key role and up to six sample values each.
- **keys** — primary key, declared foreign keys.
- **links_out / links_in** — declared foreign keys AND the links that exist only by column
  naming (``organization_id`` → organizations), marked apart. 230 of the 384 relationships in
  this schema are the second kind; an agent that only knew the first would miss most joins.
- **sample_rows** — three rows, with secrets redacted and emails masked.
- **embedding** of the semantic text, text-embedding-3-small, the model doc-rag already uses.

Search embeds the question once and ranks tables by cosine distance in pgvector, with an ILIKE
fallback when no embedding key is configured.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..catalog_ddl import CATALOG_TABLE, EMBEDDING_DIM
from ..config import settings
from ..core.logging import get_logger

log = get_logger(__name__)

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")

#: Columns whose VALUES must never leave the database in a sample, whatever the data is.
_SECRET = re.compile(r"(password|passwd|pwd|hash|secret|token|api_key|apikey|otp|salt|private_key|credential|signature)", re.I)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")

#: The domain a table belongs to, by name. A reading aid for the catalogue and the ERD; the
#: database declares none of this.
DOMAINS: list[tuple[str, str, str]] = [
    ("org",        "Organisation, sites & access",     r"^(organizations|users|user_|roles|permissions|role_|auth_|approval_action|buildings|locations|sites|site_|floors|move_|moves|spaces|building_assumed|invitations|portfolios)"),
    ("assets",     "Assets & meters",                  r"^(assets|asset_|bms_|meter|equipment)"),
    ("work",       "Work orders & PPM",                r"^(work_order|work_tasks|wo_|maintenance|scheduled_|schedule_|technician|resource_skill|ppm|inspection|task_|report_card)"),
    ("compliance", "Compliance",                       r"^(compliance|country_certificate|building_country|certificate|regulatory|regulation_packs|approvals?_queue|ops_email|ops_audit)"),
    ("energy",     "Energy",                           r"^(energy|building_energy|building_sections|chiller|tariff|degree_day|eui_|weather_)"),
    ("vendors",    "Vendors & contracts",              r"^(vendors|vendor_|sla_|contract|invoice|cost_variance|score_|billing_terms)"),
    ("supply",     "Purchasing & inventory",           r"^(purchase|receipt|spare_parts|stock|inventory|bom_|misc_cost|parts|rfq|cycle_count)"),
    ("docs",       "Documents, ingestion & migration", r"^(ingestion|document|files|uploaded_files|prompt_|review_queue|corrections|claude_|query_audit|canonical|udr_|migration|schema_|connectors|field_maps|fiix_|import_|mapping_templates)"),
    ("reference",  "Reference data (Fiix)",            r"^(accounts|business_|charge_departments|countries|currencies|priorities|projects|resources|transportation|reasons_asset)"),
    ("platform",   "Platform, reports & audit",        r"^(rca_|notifications|audit_log|activity|agent_|alembic|sessions|usage_|reports|fm_report|saved_spaces|known_issues|orchestration_audit|platform_usage)"),
]

#: Which service's migrations create the table, by name — so an agent knows whose API owns it.
OWNERS: list[tuple[str, str]] = [
    (r"^(compliance|country_|building_country|energy|building_energy|building_sections|chiller|eui_|weather_|vendor_|sla_|contract|invoice|cost_variance|score_|auth_|ops_|report_|approvals_queue|asset_condition|asset_criticality|asset_failure|asset_reading_bands|asset_warranties|bms_)", "svc-operations-intelligence"),
    (r"^(work_order|wo_|maintenance_|scheduled_|technician|resource_skill|ppm|inspection|task_|work_tasks)", "svc-work-order-management"),
    (r"^(udr_|saved_spaces)", "svc-udr"),
    (r"^(ingestion|document|prompt_|review_queue|corrections|claude_|query_audit)", "doc-rag / svc-ingestion"),
    (r"^(migration|schema_|canonical|field_maps|mapping_templates|fiix_|import_|connectors|uploaded_files)", "svc-ai-schema-mapper"),
    (r"^(agent_|activity|orchestration_audit|platform_usage|known_issues|sessions)", "svc-deepagents"),
]


def domain_for(name: str) -> str:
    for key, _label, rx in DOMAINS:
        if re.match(rx, name):
            return key
    return "other"


def domain_label(key: str) -> str:
    return next((l for k, l, _ in DOMAINS if k == key), "Other")


def owner_for(name: str) -> str:
    for rx, owner in OWNERS:
        if re.match(rx, name):
            return owner
    return "cafm-connector-service (core ORM)"


def _jsonable(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<{len(v)} bytes>"
    if isinstance(v, (dict, list)):
        return json.loads(json.dumps(v, default=str))
    return v


def redact_value(column: str, value: Any) -> Any:
    """A sample value fit to store and show. Secrets never; emails masked; the rest as is."""
    if value is None:
        return None
    if _SECRET.search(column):
        return "[redacted]"
    if isinstance(value, str) and _EMAIL.match(value):
        local, _, dom = value.partition("@")
        return (local[:1] + "***@" + dom) if dom else "***"
    if isinstance(value, str) and len(value) > 160:
        return value[:157] + "..."
    return _jsonable(value)


def redact_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: redact_value(k, v) for k, v in row.items()}


# ── links ────────────────────────────────────────────────────────────────────────────────

_SELF_REFS = {"parent", "reports_to", "created_by", "updated_by", "approved_by", "assigned_to", "requested_by"}


def inferred_targets(column: str, table_names: set[str]) -> list[str]:
    """Tables a column named ``<stem>_id`` points at by convention, in preference order."""
    if not column.endswith("_id") or column == "id":
        return []
    stem = column[:-3]
    if stem in _SELF_REFS:
        return []
    cands = [stem + "s", stem, stem + "es", re.sub(r"y$", "ies", stem)]
    seen: list[str] = []
    for c in cands:
        if c in table_names and c not in seen:
            seen.append(c)
    return seen


# ── introspection ────────────────────────────────────────────────────────────────────────

async def _table_names(session: AsyncSession, schema: str) -> list[str]:
    rows = await session.execute(text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = :s AND table_type = 'BASE TABLE' ORDER BY table_name"
    ), {"s": schema})
    return [r[0] for r in rows if _IDENT.match(r[0])]


async def _columns(session: AsyncSession, schema: str, table: str) -> list[dict[str, Any]]:
    rows = await session.execute(text("""
        SELECT c.column_name, c.data_type, c.udt_name, c.is_nullable, c.column_default,
               pgd.description
        FROM information_schema.columns c
        LEFT JOIN pg_catalog.pg_statio_all_tables st ON st.schemaname = c.table_schema AND st.relname = c.table_name
        LEFT JOIN pg_catalog.pg_description pgd ON pgd.objoid = st.relid AND pgd.objsubid = c.ordinal_position
        WHERE c.table_schema = :s AND c.table_name = :t
        ORDER BY c.ordinal_position"""), {"s": schema, "t": table})
    out = []
    for r in rows:
        t = r.data_type
        if t == "USER-DEFINED":
            t = r.udt_name
        t = {"character varying": "varchar", "timestamp with time zone": "timestamptz",
             "timestamp without time zone": "timestamp", "double precision": "float8"}.get(t, t)
        out.append({"name": r.column_name, "type": t, "nullable": r.is_nullable == "YES",
                    "default": (r.column_default or None), "comment": r.description})
    return out


async def _keys(session: AsyncSession, schema: str, table: str) -> dict[str, Any]:
    pk = [r[0] for r in await session.execute(text("""
        SELECT kcu.column_name FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = :s AND tc.table_name = :t
        ORDER BY kcu.ordinal_position"""), {"s": schema, "t": table})]
    fks = [{"column": r[0], "to_table": r[1], "to_column": r[2]} for r in await session.execute(text("""
        SELECT kcu.column_name, ccu.table_name, ccu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = :s AND tc.table_name = :t"""), {"s": schema, "t": table})]
    return {"primary_key": pk, "foreign_keys": fks}


async def _table_meta(session: AsyncSession, schema: str, table: str) -> dict[str, Any]:
    r = (await session.execute(text("""
        SELECT c.reltuples::bigint AS est, obj_description(c.oid) AS comment
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = :s AND c.relname = :t"""), {"s": schema, "t": table})).first()
    est = int(r.est) if r and r.est is not None else None
    if est is not None and est < 0:
        est = None
    if est is None or est < 5000:
        # Cheap to count exactly when small; the estimate is stale after a fresh seed.
        est = int((await session.execute(text(f'SELECT count(*) FROM "{schema}"."{table}"'))).scalar_one())
    return {"row_estimate": est, "comment": (r.comment if r else None)}


async def _samples(session: AsyncSession, schema: str, table: str, columns: list[dict[str, Any]],
                   *, rows: int = 3, per_column: int = 6) -> tuple[list[dict[str, Any]], dict[str, list[Any]]]:
    sample_rows = [redact_row(dict(m)) for m in
                   (await session.execute(text(f'SELECT * FROM "{schema}"."{table}" LIMIT :n'), {"n": rows})).mappings().all()]
    values: dict[str, list[Any]] = {}
    for c in columns[:60]:
        if _SECRET.search(c["name"]) or c["type"] in ("jsonb", "json", "bytea", "text", "vector", "tsvector", "ARRAY"):
            continue
        try:
            vals = [redact_value(c["name"], v) for (v,) in (await session.execute(text(
                f'SELECT DISTINCT "{c["name"]}" FROM "{schema}"."{table}" WHERE "{c["name"]}" IS NOT NULL LIMIT :n'
            ), {"n": per_column})).all()]
            values[c["name"]] = vals
        except Exception as exc:  # noqa: BLE001 — one odd column must not lose the table
            log.debug("catalog.sample_column_failed", table=table, column=c["name"], error=str(exc)[:120])
    return sample_rows, values


# ── the words ────────────────────────────────────────────────────────────────────────────

def heuristic_purpose(table: str, columns: list[dict[str, Any]], keys: dict[str, Any], links_out: list[dict[str, Any]]) -> dict[str, Any]:
    """A purpose written from structure alone — what the catalogue says when no model can."""
    words = table.replace("_", " ")
    refs = sorted({l["to_table"] for l in links_out})
    grain = f"one row per {words.rstrip('s')}" if not table.endswith("_log") else "one row per event"
    purpose = f"Holds {words}."
    if refs:
        purpose += " Each row points at " + ", ".join(refs[:5]) + ("." if len(refs) <= 5 else f" and {len(refs) - 5} more.")
    notable = [c["name"] for c in columns if c["name"] not in ("id", "created_at", "updated_at") and not c["name"].endswith("_id")][:8]
    answers = [f"List {words}", f"How many {words} are there"] + [f"Which {words} have a given {n.replace('_', ' ')}" for n in notable[:3]]
    return {"purpose": purpose, "answers": answers, "not_for": None, "grain": grain}


_PURPOSE_PROMPT = """You are documenting one PostgreSQL table for an agent that must decide, from a user's question, which tables to read.

Write JSON only, with these keys:
- "grain": what one row IS, in one short phrase (e.g. "one certificate held by one vendor or building").
- "purpose": 2-3 sentences: what the table records, in the language a facilities manager uses, and how it relates to the tables it links to. Do not restate the column list.
- "answers": 5-7 short user questions this table is the right source for (the kind of thing a property manager asks).
- "not_for": 1-2 sentences naming the near-miss — a question that sounds like this table but is answered elsewhere — with the other table named, if the metadata makes that clear. Otherwise null.

Be specific to THIS table's columns and sample values. If the sample values look seeded or thin, say the table is sparsely populated rather than inventing meaning.

TABLE: {schema}.{table}
DOMAIN: {domain}
OWNING SERVICE: {owner}
ROW ESTIMATE: {rows}
TABLE COMMENT: {comment}
PRIMARY KEY: {pk}
DECLARED FOREIGN KEYS: {fks}
LINKS BY COLUMN NAME (no constraint): {inferred}
REFERENCED BY: {referenced_by}
COLUMNS (name type nullable  sample values):
{columns}
SAMPLE ROWS (redacted):
{samples}
"""


async def write_purpose(meta: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """The model's description, or the heuristic one. Returns (fields, source)."""
    key = (settings.anthropic_api_key or "").strip()
    fallback = heuristic_purpose(meta["table"], meta["columns"], meta["keys"], meta["links_out"])
    if not key:
        return fallback, "heuristic"
    try:
        import anthropic  # local import: the package is a runtime dependency, not a test one

        client = anthropic.AsyncAnthropic(api_key=key)
        cols = "\n".join(
            f"  {c['name']} {c['type']} {'null' if c['nullable'] else 'not null'}"
            + (f"  e.g. {json.dumps(c.get('sample_values')[:4], default=str)}" if c.get("sample_values") else "")
            + (f"  -- {c['comment']}" if c.get("comment") else "")
            for c in meta["columns"][:70]
        )
        prompt = _PURPOSE_PROMPT.format(
            schema=settings.db_schema, table=meta["table"], domain=domain_label(meta["domain"]), owner=meta["owner"],
            rows=meta["row_estimate"], comment=meta.get("comment") or "(none)",
            pk=", ".join(meta["keys"]["primary_key"]) or "(none declared)",
            fks=", ".join(f"{f['column']} -> {f['to_table']}.{f['to_column']}" for f in meta["keys"]["foreign_keys"]) or "(none)",
            inferred=", ".join(f"{l['column']} -> {l['to_table']}" for l in meta["links_out"] if l["kind"] == "inferred") or "(none)",
            referenced_by=", ".join(f"{l['from_table']}.{l['column']}" for l in meta["links_in"][:12]) or "(nothing)",
            columns=cols, samples=json.dumps(meta["sample_rows"], default=str)[:2500],
        )
        resp = await client.messages.create(
            model=settings.catalog_purpose_model, max_tokens=700, temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        txt = "".join(getattr(b, "text", "") for b in resp.content)
        m = re.search(r"\{.*\}", txt, re.S)
        data = json.loads(m.group(0)) if m else {}
        out = {
            "grain": str(data.get("grain") or fallback["grain"])[:300],
            "purpose": str(data.get("purpose") or fallback["purpose"])[:1500],
            "answers": [str(a)[:200] for a in (data.get("answers") or fallback["answers"])][:8],
            "not_for": (str(data["not_for"])[:500] if data.get("not_for") else None),
        }
        return out, "model"
    except Exception as exc:  # noqa: BLE001 — a model refusal must not lose the row
        log.warning("catalog.purpose_failed", table=meta["table"], error=str(exc)[:200])
        return fallback, "heuristic"


def semantic_text(meta: dict[str, Any], words: dict[str, Any]) -> str:
    """What gets embedded: the table's meaning, its questions, and its vocabulary."""
    cols = ", ".join(c["name"] for c in meta["columns"][:80])
    links = ", ".join(sorted({l["to_table"] for l in meta["links_out"]}))
    refs = ", ".join(sorted({l["from_table"] for l in meta["links_in"]})[:15])
    parts = [
        f"Table {meta['table']} ({domain_label(meta['domain'])}). {words['grain']}.",
        words["purpose"],
        "Answers: " + " | ".join(words["answers"]),
        (f"Not for: {words['not_for']}" if words.get("not_for") else ""),
        f"Columns: {cols}.",
        (f"Links to: {links}." if links else ""),
        (f"Referenced by: {refs}." if refs else ""),
    ]
    return "\n".join(p for p in parts if p)


# ── embeddings ───────────────────────────────────────────────────────────────────────────

async def embed(texts: list[str]) -> list[list[float]] | None:
    """text-embedding-3-small through OpenAI's HTTP API, or None when no key is set."""
    key = (settings.openai_api_key or "").strip()
    if not key or not texts:
        return None
    out: list[list[float]] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(0, len(texts), 64):
            batch = [t[:8000] for t in texts[i:i + 64]]
            r = await client.post("https://api.openai.com/v1/embeddings",
                                  headers={"Authorization": f"Bearer {key}"},
                                  json={"model": settings.openai_embedding_model, "input": batch})
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            out.extend(d["embedding"] for d in data)
    return out


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


# ── build ────────────────────────────────────────────────────────────────────────────────

async def build(session: AsyncSession, *, only: list[str] | None = None, with_model: bool = True,
                progress: Any = None) -> dict[str, Any]:
    """(Re)build the catalogue for every table, or for ``only``. Writes rows; returns a summary."""
    schema = settings.db_schema
    names = await _table_names(session, schema)
    name_set = set(names)
    if only:
        bad = [t for t in only if t not in name_set]
        if bad:
            raise ValueError(f"unknown tables: {bad}")
    metas: dict[str, dict[str, Any]] = {}
    # Pass 1 — structure for EVERY table, because links_in needs the whole graph.
    for t in names:
        cols = await _columns(session, schema, t)
        keys = await _keys(session, schema, t)
        declared = {(f["column"], f["to_table"]) for f in keys["foreign_keys"]}
        links_out = [{"column": f["column"], "to_table": f["to_table"], "to_column": f["to_column"], "kind": "declared"}
                     for f in keys["foreign_keys"]]
        for c in cols:
            if any(c["name"] == d[0] for d in declared):
                continue
            for tgt in inferred_targets(c["name"], name_set):
                if tgt != t:
                    links_out.append({"column": c["name"], "to_table": tgt, "to_column": "id", "kind": "inferred"})
                    break
        metas[t] = {"table": t, "schema": schema, "domain": domain_for(t), "owner": owner_for(t),
                    "columns": cols, "keys": keys, "links_out": links_out, "links_in": []}
    for t, m in metas.items():
        for l in m["links_out"]:
            if l["to_table"] in metas:
                metas[l["to_table"]]["links_in"].append({"from_table": t, "column": l["column"], "kind": l["kind"]})
    # Pass 2 — rows, samples, words, embedding, write.
    todo = only or names
    written = 0; model_written = 0; failures: list[str] = []
    for i, t in enumerate(todo):
        m = metas[t]
        try:
            m.update(await _table_meta(session, schema, t))
            sample_rows, values = await _samples(session, schema, t, m["columns"])
            for c in m["columns"]:
                if c["name"] in values:
                    c["sample_values"] = values[c["name"]]
                c["pk"] = c["name"] in m["keys"]["primary_key"]
                c["fk"] = any(l["column"] == c["name"] for l in m["links_out"])
            m["sample_rows"] = sample_rows
            words, source = (await write_purpose(m)) if with_model else (heuristic_purpose(t, m["columns"], m["keys"], m["links_out"]), "heuristic")
            sem = semantic_text(m, words)
            vec = await embed([sem])
            await session.execute(text(f"""
                INSERT INTO "{schema}".{CATALOG_TABLE}
                    (table_name, domain, owning_service, grain, purpose, answers, not_for, row_estimate, columns, keys,
                     links_out, links_in, sample_rows, semantic_text, embedding, embedding_model, purpose_model, purpose_source, built_at)
                VALUES (:t, :d, :o, :g, :p, CAST(:a AS jsonb), :nf, :re, CAST(:c AS jsonb), CAST(:k AS jsonb),
                        CAST(:lo AS jsonb), CAST(:li AS jsonb), CAST(:sr AS jsonb), :st, CAST(:e AS vector), :em, :pm, :ps, now())
                ON CONFLICT (table_name) DO UPDATE SET
                    domain = EXCLUDED.domain, owning_service = EXCLUDED.owning_service, grain = EXCLUDED.grain,
                    purpose = EXCLUDED.purpose, answers = EXCLUDED.answers, not_for = EXCLUDED.not_for,
                    row_estimate = EXCLUDED.row_estimate, columns = EXCLUDED.columns, keys = EXCLUDED.keys,
                    links_out = EXCLUDED.links_out, links_in = EXCLUDED.links_in, sample_rows = EXCLUDED.sample_rows,
                    semantic_text = EXCLUDED.semantic_text, embedding = EXCLUDED.embedding,
                    embedding_model = EXCLUDED.embedding_model, purpose_model = EXCLUDED.purpose_model,
                    purpose_source = EXCLUDED.purpose_source, built_at = now()
            """), {
                "t": t, "d": m["domain"], "o": m["owner"], "g": words["grain"], "p": words["purpose"],
                "a": json.dumps(words["answers"]), "nf": words.get("not_for"), "re": m["row_estimate"],
                "c": json.dumps(m["columns"], default=str), "k": json.dumps(m["keys"]),
                "lo": json.dumps(m["links_out"]), "li": json.dumps(m["links_in"]),
                "sr": json.dumps(m["sample_rows"], default=str), "st": sem,
                "e": _vec(vec[0]) if vec else None,
                "em": settings.openai_embedding_model if vec else None,
                "pm": settings.catalog_purpose_model if source == "model" else None, "ps": source,
            })
            await session.commit()
            written += 1; model_written += (source == "model")
            if progress:
                progress(i + 1, len(todo), t)
        except Exception as exc:  # noqa: BLE001 — one table must not lose the catalogue
            await session.rollback()
            failures.append(f"{t}: {str(exc)[:160]}")
            log.warning("catalog.table_failed", table=t, error=str(exc)[:200])
    return {"tables": len(todo), "written": written, "with_model_purpose": model_written,
            "embedded": bool(settings.openai_api_key), "failures": failures}


# ── read ─────────────────────────────────────────────────────────────────────────────────

_CARD_COLS = ("table_name, domain, owning_service, grain, purpose, answers, not_for, row_estimate, columns, keys, "
              "links_out, links_in, sample_rows, purpose_source, built_at")


def _card(row: Any, *, score: float | None = None, full: bool = True) -> dict[str, Any]:
    m = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    out = {
        "table": m["table_name"], "domain": m["domain"], "domain_label": domain_label(m["domain"]),
        "owning_service": m["owning_service"], "grain": m["grain"], "purpose": m["purpose"],
        "answers": m["answers"], "not_for": m["not_for"], "row_estimate": m["row_estimate"],
        "primary_key": (m["keys"] or {}).get("primary_key", []),
        "links_out": m["links_out"], "links_in": m["links_in"],
        "purpose_source": m["purpose_source"], "built_at": m["built_at"].isoformat() if m.get("built_at") else None,
    }
    if score is not None:
        out["score"] = round(float(score), 4)
    if full:
        out["columns"] = m["columns"]
        out["sample_rows"] = m["sample_rows"]
    else:
        out["columns"] = [c["name"] for c in (m["columns"] or [])]
    return out


async def search(session: AsyncSession, question: str, *, k: int = 8, domain: str | None = None) -> dict[str, Any]:
    """The tables most likely to answer a question, best first."""
    schema = settings.db_schema
    k = max(1, min(int(k), 25))
    where = "WHERE embedding IS NOT NULL" + (" AND domain = :dom" if domain else "")
    params: dict[str, Any] = {"k": k}
    if domain:
        params["dom"] = domain
    vec = await embed([question])
    if vec:
        params["q"] = _vec(vec[0])
        rows = (await session.execute(text(f"""
            SELECT {_CARD_COLS}, 1 - (embedding <=> CAST(:q AS vector)) AS score
            FROM "{schema}".{CATALOG_TABLE} {where}
            ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"""), params)).all()
        return {"ok": True, "method": "embedding", "question": question,
                "tables": [_card(r, score=r.score, full=False) for r in rows]}
    # No embedding key: match words against the semantic text.
    terms = [w for w in re.findall(r"[a-z0-9_]+", question.lower()) if len(w) > 2][:8]
    if not terms:
        return {"ok": True, "method": "text", "question": question, "tables": []}
    conds = " + ".join(f"(CASE WHEN semantic_text ILIKE :w{i} THEN 1 ELSE 0 END)" for i in range(len(terms)))
    for i, w in enumerate(terms):
        params[f"w{i}"] = f"%{w}%"
    rows = (await session.execute(text(f"""
        SELECT {_CARD_COLS}, ({conds}) AS score FROM "{schema}".{CATALOG_TABLE}
        WHERE ({conds}) > 0 {"AND domain = :dom" if domain else ""}
        ORDER BY score DESC, table_name LIMIT :k"""), params)).all()
    return {"ok": True, "method": "text", "question": question, "tables": [_card(r, score=r.score, full=False) for r in rows]}


async def card(session: AsyncSession, table: str) -> dict[str, Any] | None:
    if not _IDENT.match(table or ""):
        return None
    row = (await session.execute(text(
        f'SELECT {_CARD_COLS} FROM "{settings.db_schema}".{CATALOG_TABLE} WHERE table_name = :t'), {"t": table})).first()
    return _card(row) if row else None


async def summary(session: AsyncSession) -> dict[str, Any]:
    schema = settings.db_schema
    rows = (await session.execute(text(f"""
        SELECT domain, count(*) AS n, count(embedding) AS embedded,
               sum(CASE WHEN purpose_source = 'model' THEN 1 ELSE 0 END) AS model_purposes, max(built_at) AS built_at
        FROM "{schema}".{CATALOG_TABLE} GROUP BY domain ORDER BY n DESC"""))).all()
    return {"ok": True, "domains": [{"domain": r.domain, "label": domain_label(r.domain), "tables": r.n,
                                     "embedded": r.embedded, "model_purposes": r.model_purposes,
                                     "built_at": r.built_at.isoformat() if r.built_at else None} for r in rows],
            "total": sum(r.n for r in rows)}
