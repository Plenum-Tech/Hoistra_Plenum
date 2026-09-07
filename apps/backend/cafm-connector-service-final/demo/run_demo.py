"""
demo/run_demo.py

Sprint 2 Live Demo — Full Platform Pipeline

Demonstrates the complete CAFM AI Platform pipeline:

  1. CSV ingestion      → assets.csv, work_orders.csv, parts.csv, scheduled_pm.csv
  2. DOCX ingestion     → site inspection report (Sections A–G)
  3. PDF ingestion      → vendor invoice / compliance certificate
  4. Query (Tier 1)     → "Which assets have open work orders?"
  5. Query (Tier 2)     → "What did the inspection say about AHU-004?"
  6. Layer 5 agents     → All 5 data agents run their determinism cycle
  7. Layer 6 decision   → CMSDecision produced with confidence score
  8. Document gen       → PM schedule DOCX auto-generated and evaluated

Prerequisites:
  - All 10 services running: docker compose up --build
  - ANTHROPIC_API_KEY set in environment
  - Sample files in demo/sample_files/

Run:
    python demo/run_demo.py

Or with verbose output:
    python demo/run_demo.py --verbose

Or to run only specific stages:
    python demo/run_demo.py --stages 1,2,4,8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

# ── Config ────────────────────────────────────────────────────────────────────

INGESTION_URL = os.environ.get("INGESTION_URL", "http://localhost:8001")
QUERY_URL = os.environ.get("QUERY_URL", "http://localhost:8002")
CONNECTOR_URL = os.environ.get("CONNECTOR_URL", "http://localhost:8000")

DEMO_DIR = Path(__file__).parent
SAMPLE_FILES = DEMO_DIR / "sample_files"

_VERBOSE = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_header(title: str) -> None:
    width = 70
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}")


def _print_step(step: str, detail: str = "") -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    suffix = f"  {detail}" if detail else ""
    print(f"  [{ts}] {step}{suffix}")


def _print_ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _print_warn(msg: str) -> None:
    print(f"  ! {msg}")


def _print_result(label: str, value: Any) -> None:
    if isinstance(value, dict):
        value_str = json.dumps(value, indent=4)
        print(f"  {label}:\n{value_str}")
    else:
        print(f"  {label}: {value}")


async def _wait_for_services(client: httpx.AsyncClient) -> bool:
    """Verify all required services are reachable."""
    _print_header("Pre-flight: Service Health Check")
    all_ok = True
    for name, url in [
        ("cafm-connector-service", CONNECTOR_URL),
        ("svc-ingestion", INGESTION_URL),
        ("svc-query", QUERY_URL),
    ]:
        try:
            resp = await client.get(f"{url}/health", timeout=5.0)
            if resp.status_code == 200 and resp.json().get("status") == "ok":
                _print_ok(f"{name} → {url}/health  (200 OK)")
            else:
                _print_warn(f"{name} → {url}/health  (unexpected: {resp.status_code})")
                all_ok = False
        except httpx.ConnectError:
            _print_warn(f"{name} → {url}/health  (CONNECTION REFUSED — is docker compose up?)")
            all_ok = False
    return all_ok


# ── Stage 1 — CSV Ingestion ───────────────────────────────────────────────────

async def stage_1_csv_ingestion(client: httpx.AsyncClient) -> dict[str, Any]:
    _print_header("Stage 1 — CSV Ingestion (assets, work_orders, parts, scheduled_pm)")

    results = {}
    csv_files = [
        ("assets.csv", "assets"),
        ("work_orders.csv", "work_orders"),
        ("parts.csv", "spare_parts"),
        ("scheduled_pm.csv", "scheduled_pm"),
    ]

    for filename, target_table in csv_files:
        file_path = SAMPLE_FILES / filename
        if not file_path.exists():
            _print_warn(f"{filename} not found in demo/sample_files/ — skipping")
            continue

        _print_step(f"Ingesting {filename}", f"→ {target_table}")
        t0 = time.perf_counter()

        payload = {
            "source_type": "csv",
            "source_filename": filename,
            "encoding": "latin1",
            "target_table": target_table,
        }

        # Upload the file + metadata
        try:
            with open(file_path, "rb") as f:
                resp = await client.post(
                    f"{INGESTION_URL}/ingest/csv",
                    data={"metadata": json.dumps(payload)},
                    files={"file": (filename, f, "text/csv")},
                    timeout=30.0,
                )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            _print_warn(f"  Could not reach svc-ingestion: {exc}")
            results[filename] = {"status": "unreachable"}
            continue

        elapsed = (time.perf_counter() - t0) * 1000

        if resp.status_code in (200, 202):
            data = resp.json()
            job_id = data.get("job_id", "unknown")
            _print_ok(f"{filename}: submitted job_id={job_id}  ({elapsed:.0f}ms)")
            results[filename] = {"status": "submitted", "job_id": job_id, "elapsed_ms": elapsed}
        else:
            _print_warn(f"{filename}: HTTP {resp.status_code}  {resp.text[:200]}")
            results[filename] = {"status": "error", "http_status": resp.status_code}

    return results


# ── Stage 2 — DOCX Ingestion ──────────────────────────────────────────────────

async def stage_2_docx_ingestion(client: httpx.AsyncClient) -> dict[str, Any]:
    _print_header("Stage 2 — DOCX Ingestion (site inspection report, Sections A–G)")

    docx_file = SAMPLE_FILES / "site_inspection_nov_2025.docx"
    if not docx_file.exists():
        _print_warn("site_inspection_nov_2025.docx not found — skipping stage 2")
        return {"status": "skipped"}

    _print_step("Ingesting site_inspection_nov_2025.docx")
    t0 = time.perf_counter()

    payload = {
        "source_type": "word",
        "source_filename": "site_inspection_nov_2025.docx",
        "document_subtype": "inspection_report",
    }

    try:
        with open(docx_file, "rb") as f:
            resp = await client.post(
                f"{INGESTION_URL}/ingest/word",
                data={"metadata": json.dumps(payload)},
                files={"file": ("site_inspection_nov_2025.docx", f,
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                timeout=60.0,
            )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        _print_warn(f"Could not reach svc-ingestion: {exc}")
        return {"status": "unreachable"}

    elapsed = (time.perf_counter() - t0) * 1000

    if resp.status_code in (200, 202):
        data = resp.json()
        eval_score = data.get("eval_score", "pending")
        route = data.get("route", "pending")
        _print_ok(f"DOCX submitted: eval_score={eval_score}  route={route}  ({elapsed:.0f}ms)")
        if data.get("route") == "review_queue":
            _print_warn("  → Routed to review queue (eval_score between 0.60–0.84)")
        return data
    else:
        _print_warn(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return {"status": "error", "http_status": resp.status_code}


# ── Stage 3 — PDF Ingestion ───────────────────────────────────────────────────

async def stage_3_pdf_ingestion(client: httpx.AsyncClient) -> dict[str, Any]:
    _print_header("Stage 3 — PDF Ingestion (vendor invoice)")

    pdf_file = SAMPLE_FILES / "vendor_invoice_sample.pdf"
    if not pdf_file.exists():
        _print_warn("vendor_invoice_sample.pdf not found — skipping stage 3")
        return {"status": "skipped"}

    _print_step("Ingesting vendor_invoice_sample.pdf (Claude Vision + EL-2.x)")
    t0 = time.perf_counter()

    payload = {
        "source_type": "pdf",
        "source_filename": "vendor_invoice_sample.pdf",
        "document_subtype": "vendor_invoice",
    }

    try:
        with open(pdf_file, "rb") as f:
            resp = await client.post(
                f"{INGESTION_URL}/ingest/pdf",
                data={"metadata": json.dumps(payload)},
                files={"file": ("vendor_invoice_sample.pdf", f, "application/pdf")},
                timeout=90.0,
            )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        _print_warn(f"Could not reach svc-ingestion: {exc}")
        return {"status": "unreachable"}

    elapsed = (time.perf_counter() - t0) * 1000

    if resp.status_code in (200, 202):
        data = resp.json()
        _print_ok(f"PDF ingested: eval_score={data.get('eval_score', 'pending')}  ({elapsed:.0f}ms)")
        return data
    else:
        _print_warn(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return {"status": "error"}


# ── Stage 4 — Tier 1 Query ────────────────────────────────────────────────────

async def stage_4_tier1_query(client: httpx.AsyncClient) -> dict[str, Any]:
    _print_header("Stage 4 — Tier 1 Query: 'Which assets have open work orders?'")

    query = "Which assets have open work orders?"
    _print_step(f"Query: {query!r}")

    t0 = time.perf_counter()
    try:
        resp = await client.post(
            f"{QUERY_URL}/query",
            json={"query": query, "output_format": "text"},
            timeout=15.0,
        )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        _print_warn(f"Could not reach svc-query: {exc}")
        return {"status": "unreachable"}

    elapsed = (time.perf_counter() - t0) * 1000

    if resp.status_code == 200:
        data = resp.json()
        _print_ok(f"Answered in {elapsed:.0f}ms")
        _print_result("Intent", data.get("intent_type", "unknown"))
        _print_result("Answer", data.get("answer", data.get("content", "(no answer field)")))
        return data
    else:
        _print_warn(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return {"status": "error"}


# ── Stage 5 — Tier 2 Query ────────────────────────────────────────────────────

async def stage_5_tier2_query(client: httpx.AsyncClient) -> dict[str, Any]:
    _print_header("Stage 5 — Tier 2 Query: 'What did the Nov inspection say about AHU-004?'")

    query = "What did the November inspection say about AHU-004?"
    _print_step(f"Query: {query!r}")

    t0 = time.perf_counter()
    try:
        resp = await client.post(
            f"{QUERY_URL}/query",
            json={"query": query, "output_format": "text"},
            timeout=20.0,
        )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        _print_warn(f"Could not reach svc-query: {exc}")
        return {"status": "unreachable"}

    elapsed = (time.perf_counter() - t0) * 1000

    if resp.status_code == 200:
        data = resp.json()
        _print_ok(f"Answered in {elapsed:.0f}ms")
        _print_result("Intent", data.get("intent_type", "unknown"))
        _print_result("Answer", data.get("answer", data.get("content", "(no answer)")))
        return data
    else:
        _print_warn(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return {"status": "error"}


# ── Stage 6 — Layer 5 + Layer 6 ──────────────────────────────────────────────

async def stage_6_cms_decision(client: httpx.AsyncClient) -> dict[str, Any]:
    _print_header("Stage 6 — Layer 5 Agents + Layer 6 CMSDecision for MOB-AHU-001")

    asset_code = "MOB-AHU-001"
    _print_step(f"Running all 5 data agents for asset {asset_code}")
    _print_step("Each agent: BOUND → AGGREGATE (N=3 Haiku, concurrent) → VOTE → CONSTRAIN")

    t0 = time.perf_counter()
    try:
        resp = await client.post(
            f"{INGESTION_URL}/analyze",
            json={"asset_code": asset_code},
            timeout=60.0,
        )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        _print_warn(f"Could not reach svc-ingestion: {exc}")
        return {"status": "unreachable"}

    elapsed = (time.perf_counter() - t0) * 1000

    if resp.status_code == 200:
        data = resp.json()
        decision = data.get("decision", {})
        _print_ok(f"CMSDecision produced in {elapsed:.0f}ms")
        _print_result("Action", decision.get("action", "unknown"))
        _print_result("Priority", decision.get("priority", "unknown"))
        _print_result("Confidence", decision.get("confidence", 0))
        _print_result("Runs agreed", f"{decision.get('runs_agreed', 0)}/3")
        _print_result("Reasoning", decision.get("reasoning", ""))
        _print_result("Contributing agents", decision.get("contributing_agents", []))

        hard_rules = decision.get("hard_rules_fired", [])
        if hard_rules:
            _print_warn(f"Hard rules fired (overrode AI): {hard_rules}")
        if decision.get("action") in ("alert_critical", "human_review"):
            _print_warn("→ Routed to human review (safety gate active)")

        return data
    else:
        _print_warn(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return {"status": "error"}


# ── Stage 7 — Document Generation ────────────────────────────────────────────

async def stage_7_document_generation(client: httpx.AsyncClient) -> dict[str, Any]:
    _print_header("Stage 7 — Document Generation: PM Schedule for AHU assets (DOCX)")

    query = "Build me a PM schedule for all AHU assets"
    _print_step(f"Query: {query!r}")
    _print_step("Pipeline: classify → plan (N=3 Sonnet) → EL-7.DOC.PLAN → render → EL-7.DOC.EVAL")

    t0 = time.perf_counter()
    try:
        resp = await client.post(
            f"{QUERY_URL}/query",
            json={"query": query, "output_format": "docx"},
            timeout=60.0,
        )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        _print_warn(f"Could not reach svc-query: {exc}")
        return {"status": "unreachable"}

    elapsed = (time.perf_counter() - t0) * 1000

    if resp.status_code == 200:
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            # Held for review or error
            data = resp.json()
            if data.get("status") == "held_for_review":
                _print_warn(
                    f"Document held for review: eval_score={data.get('eval_score', '?')}  "
                    f"({elapsed:.0f}ms)\n  → EL-7.DOC.EVAL: score < 0.85, NOT auto-delivered"
                )
            elif data.get("status") == "needs_clarification":
                _print_warn(f"Classifier asked for clarification: {data.get('question', '')}")
            else:
                _print_result("Response", data)
        else:
            # DOCX file bytes
            file_size = len(resp.content)
            output_path = DEMO_DIR / "output" / "pm_schedule_demo.docx"
            output_path.parent.mkdir(exist_ok=True)
            output_path.write_bytes(resp.content)
            _print_ok(f"PM schedule DOCX generated: {file_size:,} bytes  ({elapsed:.0f}ms)")
            _print_ok(f"Saved to: {output_path}")

            # Display audit headers
            audit_id = resp.headers.get("x-audit-id", "not set")
            eval_score = resp.headers.get("x-eval-score", "not set")
            _print_result("Audit ID", audit_id)
            _print_result("EL-7.DOC.EVAL score", eval_score)
        return {"status": "ok", "elapsed_ms": elapsed}
    else:
        _print_warn(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return {"status": "error"}


# ── Stage 8 — Parts Reorder XLSX ─────────────────────────────────────────────

async def stage_8_parts_reorder(client: httpx.AsyncClient) -> dict[str, Any]:
    _print_header("Stage 8 — Parts Reorder XLSX (19 below minimum, MOTOR-8HP critical)")

    query = "Give me a parts reorder summary"
    _print_step(f"Query: {query!r}")

    t0 = time.perf_counter()
    try:
        resp = await client.post(
            f"{QUERY_URL}/query",
            json={"query": query, "output_format": "xlsx"},
            timeout=30.0,
        )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        _print_warn(f"Could not reach svc-query: {exc}")
        return {"status": "unreachable"}

    elapsed = (time.perf_counter() - t0) * 1000

    if resp.status_code == 200:
        content_type = resp.headers.get("content-type", "")
        if "json" not in content_type:
            output_path = DEMO_DIR / "output" / "parts_reorder_demo.xlsx"
            output_path.parent.mkdir(exist_ok=True)
            output_path.write_bytes(resp.content)
            _print_ok(f"Parts reorder XLSX: {len(resp.content):,} bytes  ({elapsed:.0f}ms)")
            _print_ok(f"Saved to: {output_path}")
            _print_warn("Expected: MOTOR-8HP at stock=0 → ORDER NOW (critical)")
        return {"status": "ok", "elapsed_ms": elapsed}
    else:
        _print_warn(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return {"status": "error"}


# ── Summary ───────────────────────────────────────────────────────────────────

def _print_summary(stage_results: dict[str, Any]) -> None:
    _print_header("Demo Summary")
    for stage, result in stage_results.items():
        status = result.get("status", "unknown") if isinstance(result, dict) else "ok"
        elapsed = result.get("elapsed_ms") if isinstance(result, dict) else None
        elapsed_str = f"  ({elapsed:.0f}ms)" if elapsed else ""
        icon = "✓" if status not in ("error", "unreachable", "skipped") else "✗"
        print(f"  {icon} {stage}: {status}{elapsed_str}")

    print(f"\n  Timestamp: {datetime.now().isoformat()}")
    print(f"  Ingestion: {INGESTION_URL}")
    print(f"  Query:     {QUERY_URL}")


# ── Entry point ───────────────────────────────────────────────────────────────

async def main(stages: list[int] | None = None, verbose: bool = False) -> None:
    global _VERBOSE
    _VERBOSE = verbose

    all_stages = {
        1: stage_1_csv_ingestion,
        2: stage_2_docx_ingestion,
        3: stage_3_pdf_ingestion,
        4: stage_4_tier1_query,
        5: stage_5_tier2_query,
        6: stage_6_cms_decision,
        7: stage_7_document_generation,
        8: stage_8_parts_reorder,
    }

    run_stages = {k: v for k, v in all_stages.items() if stages is None or k in stages}

    async with httpx.AsyncClient() as client:
        if not await _wait_for_services(client):
            print(
                "\n  Some services are not reachable.\n"
                "  Start them with: docker compose up --build\n"
                "  Then re-run: python demo/run_demo.py\n"
            )
            # Continue anyway — unreachable stages will self-report

        stage_results: dict[str, Any] = {}
        for stage_num, stage_fn in run_stages.items():
            try:
                result = await stage_fn(client)
                stage_results[f"Stage {stage_num}"] = result
            except Exception as exc:
                print(f"\n  ERROR in Stage {stage_num}: {exc}")
                stage_results[f"Stage {stage_num}"] = {"status": "exception", "error": str(exc)}

        _print_summary(stage_results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CAFM Platform Sprint 2 Demo")
    parser.add_argument(
        "--stages",
        type=str,
        default=None,
        help="Comma-separated stage numbers to run (e.g. 1,2,4,8). Default: all.",
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    stages = None
    if args.stages:
        try:
            stages = [int(s.strip()) for s in args.stages.split(",")]
        except ValueError:
            print(f"Invalid --stages value: {args.stages}. Use comma-separated integers.")
            sys.exit(1)

    asyncio.run(main(stages=stages, verbose=args.verbose))
