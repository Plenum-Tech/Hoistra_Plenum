"""Repair FIRE-DOOR-20482: set Fail/C1 remedial Critical alert + email draft."""
from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8009"
CERT_ID = "c566642f-197e-4ed1-b350-ab7d90aaf772"


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
    up = _req(
        "POST",
        "/api/compliance/certificates",
        {
            "id": CERT_ID,
            "cert_scope": "Building",
            "certificate_type_code": "FIRE_DOOR",
            "certificate_number": "FIRE-DOOR-20482",
            "issue_date": "2025-11-30",
            "expiry_date": "2026-11-30",
            "inspector_name": "Michael O'Brien",
            "result": "C1 danger present - immediate action",
            "defects_found": "C1 danger present - immediate action",
            "remedial_actions": "Immediate remedial action required",
            "organization_id": "00000000-0000-0000-0000-000000000001",
            "country_code": "UK",
            "confirmed_by_pm": False,
            "raw_metadata": {
                "single_door": True,
                "repaired": True,
                "source_filename": "05_FIRE_DOOR_Kingsgate.docx",
                "building": "Kingsgate Tower, 45 Marsh Wall, Canary Wharf, London E14 9GE",
            },
        },
    )
    cert = up.get("certificate") or {}
    print(
        "upsert",
        "ok=",
        up.get("ok"),
        "result=",
        cert.get("result"),
        "remedial=",
        cert.get("remedial_status"),
        "queue=",
        up.get("queue_items"),
        "err=",
        up.get("error"),
    )
    after = _req("GET", "/api/approvals?status=pending&source_feature=A")
    for item in after.get("items") or []:
        if "FIRE" in (item.get("summary") or "") or "Fire Door" in (item.get("summary") or ""):
            draft = item.get("email_draft") or {}
            print(
                "-",
                item.get("item_type"),
                item.get("severity"),
                "draft=",
                bool(draft.get("subject")),
                "|",
                (item.get("summary") or "")[:90],
            )


if __name__ == "__main__":
    main()
