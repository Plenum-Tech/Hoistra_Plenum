"""
tests/perf/locustfile.py

HTTP-level load test for all CAFM platform services using Locust.

Installation: pip install locust
Run (headless):
    locust -f tests/perf/locustfile.py --headless -u 20 -r 5 --run-time 60s \
           --host http://localhost:8001

Or with web UI:
    locust -f tests/perf/locustfile.py

P95 Latency Targets:
    GET  /health                     < 50ms
    GET  /metrics                    < 100ms
    POST /ingest  (CSV job submit)   < 500ms   (async — returns job_id)
    POST /query   (Tier 1 SQL)       < 3000ms  (SQL grounded answer)
    POST /query   (document_generate)< 15000ms (plan + render + eval)
"""

from __future__ import annotations

import random
import uuid

from locust import HttpUser, TaskSet, between, task


# ── Ingestion service (port 8001) ─────────────────────────────────────────────

class IngestionTasks(TaskSet):
    """Tasks for svc-ingestion (http://localhost:8001)."""

    @task(10)
    def health_check(self):
        with self.client.get("/health", catch_response=True) as resp:
            if resp.status_code == 200 and resp.json().get("status") == "ok":
                resp.success()
            else:
                resp.failure(f"Unexpected response: {resp.status_code} {resp.text[:100]}")

    @task(2)
    def metrics_endpoint(self):
        with self.client.get("/metrics", catch_response=True) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"/metrics returned {resp.status_code}")

    @task(3)
    def submit_csv_ingest_job(self):
        """Submit a CSV ingestion job — should return 202 with job_id immediately."""
        payload = {
            "source_type": "csv",
            "blob_url": "https://plenumstorage.blob.core.windows.net/csv-raw/assets.csv",
            "encoding": "latin1",
        }
        with self.client.post(
            "/ingest",
            json=payload,
            catch_response=True,
            name="/ingest [CSV]",
        ) as resp:
            if resp.status_code in (200, 202):
                resp.success()
            elif resp.status_code == 422:
                resp.failure(f"Validation error: {resp.text[:200]}")
            else:
                resp.failure(f"Unexpected: {resp.status_code}")

    @task(1)
    def check_job_status(self):
        """Poll a (fake) job_id — should return 404 gracefully."""
        job_id = str(uuid.uuid4())
        with self.client.get(
            f"/ingest/status/{job_id}",
            catch_response=True,
            name="/ingest/status/{job_id}",
        ) as resp:
            if resp.status_code in (200, 202, 404):
                resp.success()
            else:
                resp.failure(f"Unexpected: {resp.status_code}")


class IngestionUser(HttpUser):
    host = "http://localhost:8001"
    tasks = [IngestionTasks]
    wait_time = between(0.5, 2.0)


# ── Query service (port 8002) ─────────────────────────────────────────────────

_TIER1_QUERIES = [
    "Which assets have open work orders?",
    "How many parts are below minimum stock?",
    "Show me all Highest priority work orders",
    "List assets in Building 1",
    "What is the status of AHU-001?",
]

_TIER2_QUERIES = [
    "What did the November inspection report say about AHU-004?",
    "Show me the findings from the last site inspection",
    "What were the erosion control issues found in Section B?",
]

_DOC_GEN_QUERIES = [
    "Build me a PM schedule for all AHU assets",
    "Create a weekly work order report",
    "Generate a parts reorder summary",
    "Give me an asset health summary for Building 1",
]

_TEMPLATE_QUERIES = [
    "Fill in the inspection template for AHU-001 with this week's data",
    "Populate the WO package template for WO-2024-001",
]


class QueryTasks(TaskSet):
    """Tasks for svc-query (http://localhost:8002)."""

    @task(10)
    def health_check(self):
        with self.client.get("/health", catch_response=True) as resp:
            if resp.status_code == 200 and resp.json().get("status") == "ok":
                resp.success()
            else:
                resp.failure(f"Unexpected: {resp.status_code}")

    @task(2)
    def metrics_endpoint(self):
        with self.client.get("/metrics", catch_response=True) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"/metrics returned {resp.status_code}")

    @task(6)
    def tier1_query(self):
        """Tier 1 structured SQL query — P95 target < 3000ms."""
        query_text = random.choice(_TIER1_QUERIES)
        payload = {"query": query_text, "output_format": "text"}
        with self.client.post(
            "/query",
            json=payload,
            catch_response=True,
            name="/query [tier1]",
            timeout=10,
        ) as resp:
            if resp.status_code in (200, 202):
                resp.success()
            elif resp.status_code == 422:
                resp.failure(f"Validation: {resp.text[:200]}")
            else:
                resp.failure(f"Unexpected: {resp.status_code}")

    @task(2)
    def tier2_query(self):
        """Tier 2 fetch-then-read query."""
        query_text = random.choice(_TIER2_QUERIES)
        payload = {"query": query_text, "output_format": "text"}
        with self.client.post(
            "/query",
            json=payload,
            catch_response=True,
            name="/query [tier2]",
            timeout=15,
        ) as resp:
            if resp.status_code in (200, 202):
                resp.success()
            else:
                resp.failure(f"Unexpected: {resp.status_code}")

    @task(1)
    def document_generate(self):
        """Document generation — P95 target < 15000ms."""
        query_text = random.choice(_DOC_GEN_QUERIES)
        payload = {"query": query_text, "output_format": "docx"}
        with self.client.post(
            "/query",
            json=payload,
            catch_response=True,
            name="/query [doc_gen]",
            timeout=30,
        ) as resp:
            if resp.status_code in (200, 202):
                resp.success()
            else:
                resp.failure(f"Unexpected: {resp.status_code}")


class QueryUser(HttpUser):
    host = "http://localhost:8002"
    tasks = [QueryTasks]
    wait_time = between(0.5, 3.0)
