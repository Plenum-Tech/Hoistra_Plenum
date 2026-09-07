"""One-shot repair: reclassify EMERGENCY-LIGHTING-29404 as Building Critical.

Run inside ops container or against local :8009:

  python scripts/repair_emergency_lighting_29404.py
"""
from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8009"
CERT_ID = "3e2f0632-3f77-41e3-ab7c-57d2e05a4edc"


def _req(method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urllib.request.urlopen(r, timeout=40) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    pending = _req("GET", "/api/approvals?status=pending&source_feature=A")
    for item in pending.get("items") or []:
        summary = item.get("summary") or ""
        if "NICEIC" in summary and "EMERGENCY" in summary:
            _req(
                "POST",
                f"/api/approvals/{item['id']}/decide",
                {
                    "decision": "dismiss",
                    "pm_notes": "reclassified as EMERGENCY_LIGHTING Building",
                },
            )
            print("dismissed", item["id"][:8], summary[:60])

    up = _req(
        "POST",
        "/api/compliance/certificates",
        {
            "id": CERT_ID,
            "cert_scope": "Building",
            "certificate_type_code": "EMERGENCY_LIGHTING",
            "certificate_number": "EMERGENCY-LIGHTING-29404",
            "issue_date": "2025-07-31",
            "expiry_date": "2026-07-31",
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "country_code": "UK",
            "document_id": "fa65da14-1583-4b94-bb8d-e87eb119ae41",
            "confirmed_by_pm": False,
            "raw_metadata": {
                "single_door": True,
                "repaired": True,
                "source_filename": (
                    "3790bf9b-8bb5-452e-8197-075ef70634f3_"
                    "03_EMERGENCY_LIGHTING_Kingsgate.docx"
                ),
            },
        },
    )
    cert = up.get("certificate") or {}
    print(
        "upsert",
        "ok=",
        up.get("ok"),
        "type=",
        cert.get("certificate_type_code"),
        "scope=",
        cert.get("cert_scope"),
        "status=",
        cert.get("status"),
        "days=",
        cert.get("days_to_expiry"),
        "queue=",
        up.get("queue_items"),
        "err=",
        up.get("error"),
    )
    after = _req("GET", "/api/approvals?status=pending&source_feature=A")
    for item in after.get("items") or []:
        draft = item.get("email_draft") or {}
        print(
            "-",
            item.get("item_type"),
            item.get("severity"),
            "draft=",
            bool(draft.get("subject") or draft.get("body")),
            "|",
            (item.get("summary") or "")[:80],
        )
        if draft.get("subject"):
            print("  subject=", draft["subject"])


if __name__ == "__main__":
    main()
