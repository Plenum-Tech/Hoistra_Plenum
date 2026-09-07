"""Reclassify mis-routed BPCA PestGuard draft from EICR Building → BPCA Vendor."""
from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8009"
CERT_ID = "e91c38e5-70bc-4b98-b4b7-55cf2d8b70a6"


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
        if "EICR" in summary and ("BPCA" in summary or "29596" in summary):
            _req(
                "POST",
                f"/api/approvals/{item['id']}/decide",
                {"decision": "dismiss", "pm_notes": "reclassified as BPCA Vendor"},
            )
            print("dismissed", item["id"][:8], summary[:70])

    up = _req(
        "POST",
        "/api/compliance/certificates",
        {
            "id": CERT_ID,
            "cert_scope": "Vendor",
            "certificate_type_code": "BPCA",
            "certificate_number": "BPCA-29596",
            "issue_date": "2025-07-31",
            "expiry_date": "2026-07-31",
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "country_code": "UK",
            "confirmed_by_pm": False,
            "raw_metadata": {
                "single_door": True,
                "repaired": True,
                "source_filename": (
                    "3153810b-1ac0-4f00-8fe1-052063b06ad1_53_BPCA_PestGuard.docx"
                ),
            },
        },
    )
    cert = up.get("certificate") or {}
    print(
        "upsert ok=",
        up.get("ok"),
        "type=",
        cert.get("certificate_type_code"),
        "scope=",
        cert.get("cert_scope"),
        "err=",
        up.get("error"),
        "queue=",
        up.get("queue_items"),
    )


if __name__ == "__main__":
    main()
