"""The migration steps the engine does, as the graph's nodes run them.

``go_write_node`` is write_node for a Go-engine run: the engine plans the write before the gate
(read-only — what will merge, what is already on file, which values will not fit) and, once the
person confirms, applies it in one transaction. Everything around the write — the gate, the field
mappings, the job row, the closing event — is write_node's own code.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode

from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt

from . import store
from .client import EngineError, run_engine
from .progress import EngineProgress
from .rules_spec import write_rules

logger = logging.getLogger(__name__)

SCHEMA = "plenum_cafm"
#: The write's own deadline: under the 3600 s ARQ job timeout, with room for the steps after it.
APPLY_TIMEOUT_S = 3300.0
PLAN_TIMEOUT_S = 900.0
OUTPUTS_TIMEOUT_S = 1800.0
PARSE_TIMEOUT_S = 1800.0


ZIP_MAGIC = b"PK\x03\x04"
OLE2_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")  # an old binary workbook (.xls)


def not_for_the_engine(state: dict, content: bytes) -> bool:
    """An upload chosen for the engine by its name that the engine does not read: an old binary
    workbook under any name, a workbook name over bytes that are not a zip (a zip with bytes
    before it, a renamed .xls), or a zip with no xl/workbook.xml (an .ods or .xlsb renamed).
    Python's parse reads them (CSV, then calamine's content sniffing)."""
    if content.startswith(ZIP_MAGIC):
        import io
        import zipfile

        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                names = {n.lower() for n in z.namelist()}
        except zipfile.BadZipFile:
            return False  # refused by the engine in Python's words ("Cannot detect file format")
        return "xl/workbook.xml" not in names
    if content.startswith(OLE2_MAGIC):
        return True
    name = str(state.get("source_blob_path") or state.get("source_filename") or "").strip().lower()
    return name.endswith((".xlsx", ".xlsm"))


def parse_source(content: bytes, dest: Path) -> tuple[Path, str]:
    """Write the upload where `hoist-engine parse` reads it, and the encoding to report.

    The engine reads UTF-8 text and workbooks. Any other text is decoded here exactly as ingest
    decodes it today (chardet's guess, else UTF-8 with replacement), so every codec reads as
    Python reads it, and the engine gets the text as UTF-8. The encoding is "" when nothing was
    decoded.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if content.startswith(ZIP_MAGIC):
        dest.write_bytes(content)
        return dest, ""
    try:
        content.decode("utf-8")
        dest.write_bytes(content)
        return dest, ""
    except UnicodeDecodeError:
        pass
    import chardet

    encoding = chardet.detect(content).get("encoding") or "utf-8"
    try:
        text = content.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        text, encoding = content.decode("utf-8", errors="replace"), "utf-8"
    dest.write_bytes(text.encode("utf-8"))
    return dest, encoding


@dataclass(frozen=True)
class EngineFile:
    """A file the engine wrote, uploaded as it is (text artefacts are gzip-encoded already)."""

    path: str
    raw_len: int


def engine_db_dsn() -> str:
    """The database the writer writes to, as a libpq URL for the engine.

    The same database and the same TLS decision as the Python writer's asyncpg engine
    (db._async_engine_connect_args): when that engine uses TLS it verifies the certificate
    (ssl.create_default_context), so the engine does too (sslmode=verify-full).
    """
    from sqlalchemy.engine import make_url

    from ..config import get_settings

    # Parsed as the writer's own engine parses it (SQLAlchemy), which takes a password with '#',
    # '?' or '/' as written; urllib would cut it there. Every part is percent-encoded for pgx.
    url = make_url(get_settings().db_url)
    query = url.query or {}
    raw_mode = query.get("sslmode") or ""
    sslmode = str(raw_mode[0] if isinstance(raw_mode, tuple) else raw_mode).lower()
    host = url.host or ""
    needs_ssl = sslmode in ("require", "verify-ca", "verify-full") or (
        not sslmode and ("postgres.database.azure.com" in host or ".azure." in host)
    )
    auth = quote(url.username or "", safe="")
    if url.password is not None:
        auth += ":" + quote(str(url.password), safe="")
    netloc = (f"[{host}]" if ":" in host else host) + (f":{url.port}" if url.port else "")
    if auth:
        netloc = f"{auth}@{netloc}"
    params = urlencode({"sslmode": "verify-full" if needs_ssl else "disable", "connect_timeout": "15"})
    return f"postgresql://{netloc}/{quote(url.database or '', safe='')}?{params}"


def engine_db_env() -> dict[str, str]:
    """The engine reads its DSN from the environment, never from argv or the job file."""
    return {"HOIST_ENGINE_DSN": engine_db_dsn()}


def _hierarchies(confirmed: list | None) -> list[dict]:
    out = []
    for h in confirmed or []:
        hd = h if isinstance(h, dict) else (getattr(h, "__dict__", {}) or {})
        out.append({"source_table": str(hd.get("source_table") or ""),
                    "target_table": str(hd.get("target_table") or "")})
    return out


def write_job(*, mode: str, organization_id: str, default_building_id: str | None, cleaned_dir: str,
              out_dir: str, table_routing: dict, approved_new_columns: dict, confirmed_hierarchies: list | None,
              ddl_statements: list[dict], column_renames: list[dict], deterministic_ids: bool = False) -> dict:
    """The `hoist-engine write` job (engine/internal/write/job.go)."""
    job = {
        "mode": mode,
        "schema": SCHEMA,
        "organization_id": str(organization_id or ""),
        "default_building_id": str(default_building_id or ""),
        "cleaned_dir": str(cleaned_dir),
        "out_dir": str(out_dir),
        "table_routing": {str(k): str(v) for k, v in (table_routing or {}).items()},
        "approved_new_columns": {str(t): sorted(str(c) for c in cols)
                                 for t, cols in sorted((approved_new_columns or {}).items())},
        "confirmed_hierarchies": _hierarchies(confirmed_hierarchies),
        "ddl_statements": [{"sql": str(d["sql"]), "description": str(d.get("description") or "")}
                           for d in ddl_statements or []],
        "column_renames": [{"table": str(r["table"]), "from": str(r["from"]), "to": str(r["to"])}
                           for r in column_renames or []],
        "rules": write_rules(),
    }
    if deterministic_ids:
        job["deterministic_ids"] = True
    return job


def plan_key(job: dict, cleaned_version: str) -> str:
    """What a cached plan was made from: the job (whatever its mode) and the cleaned tables' version."""
    body = {k: v for k, v in job.items() if k != "mode"}
    raw = json.dumps({"job": body, "cleaned": cleaned_version}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _engine_dir(state: dict, kind: str, scratch: str) -> "tuple[Path | None, str]":
    """The run's `kind` tables ("full" / "cleaned") in the engine's format, and their version: the
    run's bridged copy, or — for a run whose rows reached this step without one — the rows on the
    state, written under the step's own `scratch` dir now. (None, "") when the run has none."""
    mid = str(state.get("migration_id") or "")
    ref = (state.get("engine_refs") or {}).get(kind)
    if ref:
        return await store.ensure_local(mid, ref), str(ref.get("version") or "")
    from ..graph.bulk_tables import hydrate

    channel = {"full": "full_tables", "cleaned": "cleaned_tables"}[kind]
    await hydrate(state, [channel])
    tables = state.get(channel) or {}
    if not tables:
        return None, ""
    d = store.kind_dir(mid, scratch) / f"_{kind}"
    store.write_tables(d, tables)
    state[channel] = {}
    return d, hashlib.sha256((d / "manifest.json").read_bytes()).hexdigest()[:16]


async def _cleaned_dir(state: dict) -> tuple[Path, str]:
    d, version = await _engine_dir(state, "cleaned", "write")
    if d is None:
        d = store.kind_dir(str(state.get("migration_id") or ""), "write") / "_cleaned"
        store.write_tables(d, {})
    return d, version


class _Progress:
    """Relays the engine's progress to the run card (ProgressBeat 90 → 99) and its log lines to
    the worker log. Each table resolves and then inserts its rows: two halves of the work."""

    def __init__(self, migration_id: str | None, total_rows: int):
        from ..graph.progress_beat import ProgressBeat

        self.beat = ProgressBeat(migration_id, 90.0, 99.0, every=5.0)
        self.total = max(int(total_rows), 1) * 2
        self.seen: dict[tuple[str, str], int] = {}

    async def __call__(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "log":
            level = {"warning": logging.WARNING, "error": logging.ERROR}.get(str(ev.get("level")), logging.INFO)
            logger.log(level, "%s", ev.get("message"))
            return
        if kind != "progress" or ev.get("stage") not in ("resolve", "insert"):
            return
        key = (str(ev.get("stage")), str(ev.get("table")))
        self.seen[key] = max(self.seen.get(key, 0), int(ev.get("done") or 0))
        await self.beat.tick(min(sum(self.seen.values()), self.total), self.total)


async def go_write_node(state: dict) -> dict:
    """write_node for a Go-engine run (see the module docstring)."""
    from ..graph.nodes import write_node as wn
    from ..graph.nodes.db_writer import clear_gate_payload, write_error, write_gate_payload

    migration_id = state.get("migration_id")
    try:
        cleaned_dir, cleaned_version = await _cleaned_dir(state)
        ddl_statements, column_renames = await wn._ddl_statements_for(state, rename_rows=False)
        out_dir = store.kind_dir(str(migration_id), "write")
        out_dir.mkdir(parents=True, exist_ok=True)
        base = dict(
            organization_id=state.get("organization_id") or "",
            default_building_id=state.get("building_id") or "",
            cleaned_dir=str(cleaned_dir), out_dir=str(out_dir),
            table_routing=state.get("table_routing") or {},
            approved_new_columns=wn._collect_approved_new_columns(state),
            confirmed_hierarchies=state.get("confirmed_hierarchies") or [],
            ddl_statements=ddl_statements, column_renames=column_renames,
        )
        db_env = engine_db_env()

        # ── The plan, once per job: a resume re-runs this node and must not plan again ──
        plan_job = write_job(mode="plan", **base)
        key = plan_key(plan_job, cleaned_version)
        plan_path, key_path = out_dir / "plan.json", out_dir / "plan.key"
        if plan_path.exists() and key_path.exists() and key_path.read_text().strip() == key:
            plan = json.loads(plan_path.read_text())
        else:
            result = await run_engine("write", plan_job, workdir=out_dir, env=db_env, timeout_s=PLAN_TIMEOUT_S,
                                      on_event=EngineProgress(migration_id, "write_plan").on_event)
            plan = result.get("plan") or json.loads(plan_path.read_text())
            key_path.write_text(key)

        # ── The write gate: today's summary, and what the write will do ──
        payload = wn._gate3_payload(state, (state.get("engine_reports") or {}).get("entity_counts"))
        payload["plan"] = plan
        state["write_review_payload"] = payload
        if migration_id:
            await write_gate_payload(migration_id, "write", payload)
        decision = interrupt(payload)
        if migration_id:
            await clear_gate_payload(migration_id)
        if wn._gate3_action(decision) != "confirm":
            return wn._gate3_rejected(state)
        logger.info("[Node 9] GATE 3 CONFIRMED - the engine writes the run")

        # ── Confirmed: record the mappings once, then write ──
        await wn._persist_field_mappings(state)
        progress = _Progress(migration_id, sum(int(t.get("rows") or 0) for t in plan.get("tables") or []))
        try:
            result = await run_engine("write", write_job(mode="apply", **base), workdir=out_dir, env=db_env,
                                      on_event=EngineProgress(migration_id, "write", relay=progress).on_event,
                                      timeout_s=APPLY_TIMEOUT_S)
        except EngineError as exc:
            return await _write_failed(state, exc, write_error)
        state["handoff_status"] = "applied_sql_aligned"
        state["svc_ingestion_response"] = {"status": "applied_sql_aligned", **result}
        logger.info(
            "[Node 9] Engine write applied: %s row(s) across %s table(s), %s skipped, %s merged, "
            "%s building link(s), %s reading(s) placed on a meter",
            result.get("rows_inserted", 0), result.get("tables_written", 0), result.get("rows_skipped", 0),
            result.get("rows_merged", 0), result.get("buildings_linked", 0), result.get("meters_linked", 0))
        return await wn._finish_successful_write(state)
    except GraphInterrupt:
        raise
    except EngineError as exc:  # the plan failed: nothing was written
        logger.error("[Node 9] engine plan failed: %s", exc)
        state["error_message"] = f"Schema-aligned write failed: {exc.message[:300]}"
        state["error_node"] = 9
        state["el_m9_passed"] = False
        return state
    except Exception as exc:  # noqa: BLE001 — the node reports, it does not crash the worker
        logger.exception("[Node 9] Unhandled exception: %s", exc)
        state["error_message"] = str(exc)
        state["error_node"] = 9
        state["status"] = "failed"
        state["el_m9_passed"] = False
        return state


async def _write_failed(state: dict, exc: EngineError, write_error) -> dict:
    from ..graph.nodes import write_node as wn

    migration_id = state.get("migration_id")
    state["error_node"] = 9
    state["el_m9_passed"] = False
    if exc.code == "connection_lost":
        logger.error("[Node 9] The database connection closed mid-write: %s", exc.message)
        state["error_message"] = wn._connection_lost_message(exc.message)
    elif exc.code == "ddl_failed":
        logger.error("[Node 9] DDL ROLLBACK: %s", exc.message)
        state["status"] = "ddl_failed"
        state["error_message"] = exc.message
        if migration_id:
            try:
                await write_error(migration_id, exc.message, error_node=9, status="ddl_failed")
            except Exception:  # noqa: BLE001 — as write_node: recording the failure is best-effort
                pass
    else:
        logger.error("[Node 9] Schema-aligned write failed: %s", exc)
        state["error_message"] = f"Schema-aligned write failed: {exc.message[:300]}"
    return state


# ── The output step ────────────────────────────────────────────────────────────────────────

class _Rows:
    """Stands in for a table's records where only their number and first row's keys are read."""

    def __init__(self, rows: int, columns: list):
        self.rows, self.columns = int(rows), list(columns or [])

    def __len__(self) -> int:
        return self.rows

    def __getitem__(self, i):
        if i != 0 or not self.rows:
            raise IndexError(i)
        return dict.fromkeys(self.columns)


def _outputs_hierarchies(confirmed) -> list[dict]:
    out = []
    for h in confirmed if isinstance(confirmed, list) else []:
        hd = h if isinstance(h, dict) else (getattr(h, "__dict__", {}) or {})
        out.append({k: (None if hd.get(k) is None else str(hd.get(k)))
                    for k in ("source_table", "target_table", "relationship_type", "source_column", "target_column")})
    return out


def _artefact_size(content) -> int:
    if isinstance(content, EngineFile):
        return int(content.raw_len)
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    return len(content)


async def go_outputs(state: dict, log, execution_logs: list, started_at: datetime) -> dict:
    """output_generator_node for a Go-engine run: the engine writes output.json, output.sql, the
    CSVs and the workbook from the run's tables; the node builds the report, structure.md and the
    IntermediateSchema metadata, and uploads everything as the Python path does."""
    from ..export import build_intermediate_schema, generate_pdf_report
    from ..export.intermediate_schema_builder import entity_type_for
    from ..graph.nodes import output_generator_node as og
    from ..graph.progress_beat import ProgressBeat

    migration_id = state.get("migration_id")
    cmms_name = state.get("cmms_name", "Unknown")
    routing = state.get("table_routing") or {}
    confirmed = state.get("confirmed_hierarchies", [])
    tier1 = state.get("tier1_mappings", [])
    tier2_auto = state.get("tier2_auto_accepted", [])
    tier2_human = state.get("human_approved_mappings", [])
    log(f"Starting output generation for migration {migration_id} (engine)")

    def failed(msg: str) -> dict:
        log(f"❌ {msg}")
        state["error_message"] = msg
        state["error_node"] = 8
        state["status"] = "failed"
        state["execution_logs"] = execution_logs
        return state

    try:
        full_dir, _ = await _engine_dir(state, "full", "outputs")
        cleaned_dir, _ = await _engine_dir(state, "cleaned", "outputs")
        out_dir = store.kind_dir(str(migration_id), "outputs") / "files"
        if out_dir.exists():
            shutil.rmtree(out_dir)  # a re-run of the step never uploads the last run's files
        job = {
            "full_dir": str(full_dir or ""), "cleaned_dir": str(cleaned_dir or ""), "out_dir": str(out_dir),
            "schema": SCHEMA, "table_routing": {str(k): str(v) for k, v in routing.items()},
            "confirmed_hierarchies": _outputs_hierarchies(confirmed),
            "lookup_ddl_blocks": og.lookup_ddl_blocks(state),
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }
        beat = ProgressBeat(migration_id, 80.0, 84.0, every=5.0)

        async def on_event(ev: dict) -> None:
            if ev.get("type") == "log":
                log(str(ev.get("message")))
            elif ev.get("type") == "progress":
                await beat.tick(int(ev.get("done") or 0), int(ev.get("total") or 0))

        result = await run_engine("outputs", job, workdir=out_dir.parent,
                                  on_event=EngineProgress(migration_id, "outputs", relay=on_event).on_event,
                                  timeout_s=OUTPUTS_TIMEOUT_S)
    except EngineError as exc:
        return failed(f"Output generation failed: {exc.message[:300]}")

    records = result.get("records_tables") or []
    files = {f["name"]: f for f in result.get("files") or []}
    log(f"Engine wrote {len(files)} file(s) from {len(records)} table(s): "
        + ", ".join(f"{n} ({int(f.get('raw_bytes') or 0):,} bytes)" for n, f in files.items()))
    if result.get("xlsx_skipped_reason"):
        log(f"❌ Excel workbook generation failed: {result['xlsx_skipped_reason']}")

    try:
        pdf_bytes = generate_pdf_report(
            migration_id=str(migration_id), cmms_name=cmms_name,
            tier1_count=len(tier1), tier2_auto_count=len(tier2_auto), tier2_human_count=len(tier2_human),
            tier2_unmappable=state.get("tier2_unmappable", []),
            overall_confidence=state.get("overall_confidence", 0.0),
            data_quality_warnings=state.get("data_quality_warnings", []),
            tier1_mappings=tier1, tier2_auto_mappings=tier2_auto, tier2_human_decisions=tier2_human,
            confirmed_hierarchies=confirmed, hierarchy_cycles=state.get("hierarchy_cycles", []),
        )
        log(f"✅ PDF report generated: {len(pdf_bytes):,} bytes")
    except Exception as e:  # noqa: BLE001 — the report is not critical, as in the Python path
        log(f"❌ PDF report generation failed: {e}")
        pdf_bytes = None
        log("⚠️  Continuing without PDF report...")

    # The IntermediateSchema's metadata; its rows are the engine's files now, and the write
    # gate reads the counts from engine_reports["entity_counts"].
    try:
        schema_dict = build_intermediate_schema(
            migration_id=str(migration_id), cmms_name=cmms_name,
            source_filename=state.get("source_filename", "unknown"), source_blob_url=state.get("source_blob_url"),
            cleaned_tables={}, tier1_mappings=tier1, tier2_auto_mappings=tier2_auto,
            tier2_human_decisions=tier2_human, overall_confidence=state.get("overall_confidence", 0.0),
            confirmed_hierarchies=confirmed, table_routing=routing, new_tables=state.get("new_tables"),
            detected_file_format=state.get("detected_file_format"),
        ).dict()
        og.check_el_m8(schema_dict)
        log("✅ EL-M.8 PASSED: IntermediateSchema validates")
    except Exception as e:  # noqa: BLE001
        state["el_m8_passed"] = False
        return failed(f"IntermediateSchema validation failed: {e}")

    try:
        structure_md = og.build_structure_markdown(
            cmms_name=cmms_name, records_tables={t["name"]: _Rows(t["rows"], t["columns"]) for t in records},
            confirmed_hierarchies=confirmed, containment_hierarchy=state.get("containment_hierarchy", {}),
        )
    except Exception as e:  # noqa: BLE001
        log(f"⚠️  Structure markdown generation failed: {e}")
        structure_md = None

    def engine_file(name: str) -> EngineFile:
        return EngineFile(files[name]["path"], int(files[name].get("raw_bytes") or 0))

    artefacts: dict = {"output.json": engine_file("output.json"), "output.sql": engine_file("output.sql")}
    if structure_md:
        artefacts["structure.md"] = structure_md
    if pdf_bytes:
        artefacts["migration_report.pdf"] = pdf_bytes
    if "output.xlsx" in files:
        artefacts["output.xlsx"] = engine_file("output.xlsx")
    for name in files:
        if name.startswith("table_") and name.endswith(".csv"):
            artefacts[name] = engine_file(name)
    csv_count = sum(1 for n in artefacts if n.startswith("table_") and n.endswith(".csv"))

    log("Uploading artefacts to Azure Blob...")
    urls, uploaded = await og.upload_all(migration_id, artefacts, log, lo=84.0)
    log(f"✅ Uploaded {uploaded}/{len(artefacts)} artefacts to Blob")

    counts: dict[str, int] = {}
    for t in records:
        if int(t.get("rows") or 0) > 0:
            e = entity_type_for(t["name"], routing)
            counts[e] = counts.get(e, 0) + int(t["rows"])
    state["intermediate_schema"] = schema_dict
    state["el_m8_passed"] = True
    state["output_json_url"] = urls.get("output.json", "")
    state["output_csv_url"] = urls.get("output.xlsx", "")
    state["output_sql_url"] = urls.get("output.sql", "")
    state["migration_report_url"] = urls.get("migration_report.pdf", "")
    state["output_structure_md_url"] = urls.get("structure.md", "")
    state["exported_artefacts"] = {
        "total_count": uploaded,
        "by_type": {"json": 1, "csv": csv_count, "sql": 1, "pdf": 1 if pdf_bytes else 0},
        "total_size_bytes": sum(_artefact_size(c) for c in artefacts.values()),
        "urls": urls,
    }
    state["engine_reports"] = {**(state.get("engine_reports") or {}), "outputs": result, "entity_counts": counts}
    log(f"✅ Output generation complete: 1 JSON + {csv_count} CSV + 1 SQL + 1 PDF + IntermediateSchema")
    state["current_step"] = 8
    state["execution_logs"] = execution_logs
    if isinstance(state.get("event_log"), list):
        state["event_log"].append({
            "timestamp": datetime.utcnow().isoformat(), "event": "node_complete", "node": 8,
            "detail": f"All outputs generated and uploaded ({uploaded} artefacts)",
        })
    if migration_id:
        from ..graph.nodes.db_writer import update_node_progress
        from ..graph.nodes.schema_db_writer import migration_append_node_log_auto

        await update_node_progress(
            migration_id, "8_output_generation",
            output_json_url=state.get("output_json_url"), output_csv_url=state.get("output_csv_url"),
            output_sql_url=state.get("output_sql_url"), migration_report_url=state.get("migration_report_url"),
            output_structure_md_url=state.get("output_structure_md_url"),
        )
        # No step pause: a Go run moves on from this step (engine/graph_proxy.py).
        await migration_append_node_log_auto(
            migration_id, 9, "Output Generation", started_at, datetime.utcnow(),
            output={"table_count": len(records), "artifacts_uploaded": uploaded,
                    "formats": ["json", "csv", "sql", "pdf"], "json_url": state.get("output_json_url"),
                    "csv_url": state.get("output_csv_url"), "sql_url": state.get("output_sql_url"),
                    "report_url": state.get("migration_report_url")},
            logs=[f"Generated outputs for {len(records)} tables", "Formats: JSON, CSV, SQL, PDF",
                  f"{uploaded} artifacts uploaded to Azure Blob",
                  f"EL-M.8: {'PASSED' if state.get('el_m8_passed') else 'FAILED'}"],
        )
    return state


# ── parse (ingest_node steps 1–4, the null scan and the duplicate merge) ───────────────────────


async def go_parse(state: dict, content: bytes):
    """Parse the upload on the engine. Returns ingest's ParsedSource with no rows on it (they are
    the run's "full" data set, on state["engine_refs"]) and the engine's null scan and merge
    report; raises SourceParseError for a file the engine cannot read, as Python's parse does.
    None when the engine is missing or crashed: the run is then a Python run from here on."""
    from ..graph.nodes.column_merge import KNOWN_DESTINATION_COLUMNS
    from ..graph.nodes.ingest_node import POST_WRITE_SHEETS, ParsedSource, SourceParseError
    from .selection import ENGINE_PYTHON

    mid = str(state.get("migration_id") or "")
    if not_for_the_engine(state, content):
        logger.info("[Node 1] the upload is not a zip workbook the engine reads; parsing in Python, and this "
                    "run continues on the Python engine")
        state["engine"] = ENGINE_PYTHON
        return None
    scratch = store.kind_dir(mid, "parse")
    out = store.new_data_dir(mid, "full")
    source, encoding = await asyncio.to_thread(parse_source, content, scratch / "source")
    job = {"source": str(source), "out_dir": str(out), "encoding": encoding,
           "known_destination_columns": sorted(KNOWN_DESTINATION_COLUMNS),
           "post_write_sheets": sorted(POST_WRITE_SHEETS), "preview_rows": 10, "nan_sample_rows": 20}
    try:
        res = await run_engine("parse", job, workdir=scratch, timeout_s=PARSE_TIMEOUT_S,
                               on_event=EngineProgress(mid, "parse").on_event)
    except EngineError as exc:
        if exc.code in ("parse_error", "data_error"):
            raise SourceParseError(exc.message) from exc
        logger.warning("[Node 1] hoist-engine parse failed (%s: %s); parsing in Python, and this run "
                       "continues on the Python engine", exc.code, exc.message)
        state["engine"] = ENGINE_PYTHON
        return None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    ref = await store.publish(mid, "full", out)
    state["engine_refs"] = {**(state.get("engine_refs") or {}), "full": ref}
    tables = res.get("tables") or []
    state["engine_reports"] = {**(state.get("engine_reports") or {}),
                               "parse": {"tables": [{k: t.get(k) for k in ("name", "rows", "columns", "kept_columns")}
                                                    for t in tables]}}
    return ParsedSource(
        detected_file_format=res["detected_file_format"], source_encoding=res["source_encoding"],
        source_delimiter=res["source_delimiter"],
        parsed_tables={t["name"]: t["preview"] for t in tables}, full_tables=None,
        set_aside_sheets=list(res.get("set_aside_sheets") or []),
        row_counts={t["name"]: int(t["rows"]) for t in tables},
        nan_report=res["nan_report"], duplicate_column_report=res["duplicate_column_report"])


# ── preprocess (preprocess_node's cleaning, steps 1–8) ─────────────────────────────────────────

PREPROCESS_TIMEOUT_S = 1800.0


def preprocess_job(full_dir, cleaned_dir, renamed_full_dir, scratch_dir, mapping_dict_by_table: dict,
                   skip_fields_by_table: dict) -> dict:
    """The `hoist-engine preprocess` job (engine/internal/preprocess/clean.go): the parse's full
    data set in, the cleaned and the renamed full ones out, and — under scratch_dir — the values
    of any date column it leaves to Python."""
    return {
        "full_dir": str(full_dir), "cleaned_dir": str(cleaned_dir), "renamed_full_dir": str(renamed_full_dir),
        "scratch_dir": str(scratch_dir),
        "rename_by_table": {str(t): {str(k): str(v) for k, v in (m or {}).items()}
                            for t, m in (mapping_dict_by_table or {}).items()},
        "skip_by_table": {str(t): sorted(str(f) for f in (fs or ())) for t, fs in (skip_fields_by_table or {}).items()},
    }


def _finish_python_dates(cleaned_dir: Path, scratch_dir: Path, pending: list[dict]) -> dict[str, int]:
    """Run today's _coerce_dates on the columns the engine left to Python (a first value no
    format family it knows reads, a time zone, "now"…): their values (after the null fill) are in
    the scratch "deferred" data set; a coerced column that reaches the cleaned table is rewritten
    there. Returns how many columns of each table were coerced (for the warnings)."""
    import pandas as pd
    import pyarrow as pa

    from ..graph.nodes.ingest_node import _sanitize_records
    from ..graph.nodes.preprocess_node import _coerce_dates

    coerced: dict[str, int] = {}
    if not pending:
        return coerced
    deferred = store.read_tables(scratch_dir / "deferred")
    manifest = json.loads((cleaned_dir / "manifest.json").read_text(encoding="utf-8"))
    files = {t["name"]: t.get("file") for t in manifest.get("tables") or []}
    rewrite: dict[str, dict[str, list]] = {}
    for p in pending:
        table, column, to = str(p["table"]), str(p["column"]), str(p.get("renamed_to") or "")
        values = [r.get(column) for r in deferred.get(table) or []]
        df = pd.DataFrame({column: pd.Series(values, dtype=object)})
        if not _coerce_dates(df, column):
            continue
        coerced[table] = coerced.get(table, 0) + 1
        if to:
            # As the next node reads them on a Python run (the offload's json default=str): a value
            # pandas leaves as a datetime (dateutil per value, zones on some) is its str().
            rewrite.setdefault(table, {})[to] = [
                v if v is None or isinstance(v, str) else str(v)
                for v in (r[column] for r in _sanitize_records(df.to_dict(orient="records")))]
    for table, cols in rewrite.items():
        path = cleaned_dir / files[table]
        with pa.OSFile(str(path), "rb") as src:
            t = pa.ipc.open_file(src).read_all()
        for name, iso in cols.items():
            i = t.schema.get_field_index(name)
            t = t.set_column(i, pa.field(name, pa.string(), nullable=True), pa.array(iso, type=pa.string()))
        tmp = path.with_suffix(".tmp")
        with pa.OSFile(str(tmp), "wb") as sink:
            with pa.ipc.new_file(sink, t.schema, options=pa.ipc.IpcWriteOptions(compression="zstd")) as w:
                w.write_table(t)
        tmp.replace(path)
    return coerced


def preprocess_warnings(tables: list[dict], python_coerced: dict[str, int]) -> list[str]:
    """preprocess_tables' warnings, from the engine's per-table report, word for word."""
    warnings: list[str] = []
    for t in tables:
        name = t["name"]
        if t["dedup_dropped"] > 0:
            warnings.append(f"{name}: Dropped {t['dedup_dropped']} duplicate rows")
        nulls = list(t.get("null_columns_dropped") or [])
        if nulls:
            warnings.append(f"{name}: Dropped {len(nulls)} fully-null column(s): {nulls}")
        dates = len(t.get("date_columns") or []) + python_coerced.get(name, 0)
        if dates:
            warnings.append(f"{name}: Coerced {dates} date columns to ISO 8601")
        ratio = t["rows_out"] / t["rows_in"] if t["rows_in"] > 0 else 1.0
        if ratio < 0.80:
            warnings.append(f"{name}: High duplication ({100 * (1 - ratio):.1f}% dropped)")
    return warnings


@dataclass
class EnginePreprocess:
    """What the engine's preprocess reports, in preprocess_tables' terms (the rows stay in the
    cleaned and renamed full data sets)."""

    row_count_post_dedup_by_table: dict
    dedup_drop_count_by_table: dict
    warnings: list
    total_original_rows: int
    total_cleaned_rows: int
    report: dict


async def preprocess_on_engine(full_dir: Path, cleaned_dir: Path, renamed_full_dir: Path, mapping_dict_by_table: dict,
                               skip_fields_by_table: dict, *, workdir: Path, on_event=None, env: "dict | None" = None,
                               extra: "dict | None" = None) -> EnginePreprocess:
    scratch = Path(workdir) / "preprocess-scratch"
    job = {**preprocess_job(full_dir, cleaned_dir, renamed_full_dir, scratch, mapping_dict_by_table, skip_fields_by_table),
           **(extra or {})}
    try:
        res = await run_engine("preprocess", job, workdir=workdir, env=env, on_event=on_event,
                               timeout_s=PREPROCESS_TIMEOUT_S)
        coerced = await asyncio.to_thread(_finish_python_dates, Path(cleaned_dir), scratch,
                                          list(res.get("python_date_columns") or []))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    tables = list(res.get("tables") or [])
    return EnginePreprocess(
        row_count_post_dedup_by_table={t["name"]: int(t["rows_out"]) for t in tables},
        dedup_drop_count_by_table={t["name"]: int(t["dedup_dropped"]) for t in tables},
        warnings=preprocess_warnings(tables, coerced),
        total_original_rows=sum(int(t["rows_in"]) for t in tables),
        total_cleaned_rows=sum(int(t["rows_out"]) for t in tables),
        report=res,
    )


async def full_heads(state: dict, n: int = 300) -> dict:
    """The first n rows of each full table — what preprocess's type guard samples — from the
    engine's data set, without loading the rest."""
    d, _version = await _engine_dir(state, "full", "preprocess")
    if d is None:
        return {}
    return await asyncio.to_thread(store.read_heads, d, n)


def _previews(cleaned_dir: Path, n: int = 3) -> dict:
    """The step's table previews (columns + the first n rows as str(), total rows), as the Python
    branch's step pause built them."""
    manifest = json.loads((Path(cleaned_dir) / "manifest.json").read_text(encoding="utf-8"))
    heads = store.read_heads(Path(cleaned_dir), n)
    out = {}
    for entry in manifest.get("tables") or []:
        records = heads.get(entry["name"]) or []
        if not records:
            out[entry["name"]] = {"columns": [], "rows": [], "total_rows": 0}
            continue
        columns = list(records[0].keys())
        out[entry["name"]] = {"columns": columns,
                              "rows": [[str(r.get(c, "")) for c in columns] for r in records],
                              "total_rows": int(entry.get("rows") or 0)}
    return out


async def go_preprocess(state: dict, mapping_dict_by_table: dict, skip_fields_by_table: dict):
    """preprocess_node's cleaning on the engine: the parse's full data set in; the cleaned and the
    renamed full data sets published (engine_refs["cleaned"], engine_refs["full"]); EL-4.0 and the
    link statistics on engine_reports["preprocess"]. The renamed full set has its own directory, so
    re-running this step from an earlier checkpoint still reads the parse's tables. Returns the
    EnginePreprocess and the table previews."""
    from .rules_spec import write_rules

    mid = str(state.get("migration_id") or "")
    full_dir, _version = await _engine_dir(state, "full", "preprocess")
    if full_dir is None:
        full_dir = store.kind_dir(mid, "preprocess") / "_full"
        store.write_tables(full_dir, {})
    cleaned, renamed = store.new_data_dir(mid, "cleaned"), store.new_data_dir(mid, "renamed_full")
    keys = {}
    for t, cols in (state.get("pk_confirmed_by_table") or {}).items():
        m = mapping_dict_by_table.get(t) or {}
        keys[str(t)] = [str(m.get(c, c)) for c in cols or []]
    extra = {"schema": SCHEMA, "table_routing": {str(k): str(v) for k, v in (state.get("table_routing") or {}).items()},
             "system_supplied_columns": list(write_rules().get("system_supplied_columns") or []),
             "keys_by_table": keys, "max_link_pairs": 200}
    try:
        env = engine_db_env()  # EL-4.0 reads the destination schema (never writes)
    except Exception as exc:  # noqa: BLE001 — no database configured: the check is skipped
        logger.info("[Node 5] EL-4.0 pre-write check has no database: %s", exc)
        env = {}
    res = await preprocess_on_engine(full_dir, cleaned, renamed, mapping_dict_by_table, skip_fields_by_table,
                                     workdir=store.kind_dir(mid, "preprocess"), env=env, extra=extra,
                                     on_event=EngineProgress(mid, "preprocess").on_event)
    refs = {"cleaned": await store.publish(mid, "cleaned", cleaned),
            "full": await store.publish(mid, "renamed_full", renamed)}
    state["engine_refs"] = {**(state.get("engine_refs") or {}), **refs}
    report = res.report
    state["engine_reports"] = {**(state.get("engine_reports") or {}),
                               "preprocess": {"tables": report.get("tables") or [],
                                              "python_date_columns": report.get("python_date_columns") or [],
                                              "prewrite": report.get("prewrite") or [],
                                              "links": report.get("links") or []}}
    previews = await asyncio.to_thread(_previews, cleaned)
    return res, previews
