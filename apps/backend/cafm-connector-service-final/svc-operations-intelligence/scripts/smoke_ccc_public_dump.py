#!/usr/bin/env python3
"""Smoke CCC public_api + data_dump verify paths against a running ops-intel.

Usage:
  python scripts/smoke_ccc_public_dump.py
  python scripts/smoke_ccc_public_dump.py --base-url http://localhost:8009

Checks (no secrets required):
  1) Ingest sample ASBESTOS_LICENCE dump → lookup hit status
  2) Verify type with unknown key → dump_miss
  3) EPC without keys → needs_config (or verified if keys set)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "testdata" / "verification_dumps" / "ASBESTOS_LICENCE.csv"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8009")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")
    failed = 0

    with httpx.Client(timeout=30.0) as client:
        content = SAMPLE.read_text(encoding="utf-8")
        r = client.post(
            f"{base}/api/compliance/verification-dumps/ingest",
            json={
                "certificate_type_code": "ASBESTOS_LICENCE",
                "content": content,
                "source_file": SAMPLE.name,
                "format": "csv",
            },
        )
        print("ingest", r.status_code, r.text[:300])
        if r.status_code >= 400:
            failed += 1
        else:
            body = r.json()
            if not body.get("ok") or int(body.get("total") or body.get("inserted") or 0) < 1:
                print("FAIL: ingest expected rows")
                failed += 1

        r = client.post(
            f"{base}/api/compliance/verify",
            json={
                "certificate_type_code": "ASBESTOS_LICENCE",
                "certificate_number": "HSE-LIC-10001",
                "persist": False,
            },
        )
        print("dump_hit", r.status_code, r.text[:400])
        body = r.json() if r.content else {}
        if body.get("status") != "verified" or body.get("channel") != "data_dump":
            print("FAIL: expected dump verified hit")
            failed += 1

        r = client.post(
            f"{base}/api/compliance/verify",
            json={
                "certificate_type_code": "ASBESTOS_LICENCE",
                "certificate_number": "HSE-LIC-NOPE",
                "persist": False,
            },
        )
        print("dump_miss", r.status_code, r.text[:400])
        body = r.json() if r.content else {}
        if body.get("status") != "dump_miss":
            print("FAIL: expected dump_miss")
            failed += 1

        r = client.post(
            f"{base}/api/compliance/verify",
            json={
                "certificate_type_code": "EPC",
                "certificate_number": "0000-0000-0000-0000-0000",
                "persist": False,
            },
        )
        print("epc", r.status_code, r.text[:500])
        body = r.json() if r.content else {}
        # Without keys: needs_config; with keys: verified/failed/inconclusive
        if body.get("status") not in (
            "needs_config",
            "verified",
            "failed",
            "inconclusive",
            "website_only",
        ):
            print("FAIL: unexpected EPC status", body.get("status"))
            failed += 1
        else:
            print("epc_ok", json.dumps({"status": body.get("status"), "evidence": body.get("evidence")}, indent=2)[:500])

        r = client.post(f"{base}/api/compliance/verification-dumps/run-cron", json={})
        print("run_cron", r.status_code, r.text[:300])

    print("PASS" if failed == 0 else f"FAILED ({failed})")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
