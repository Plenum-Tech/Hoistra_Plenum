"""Smoke-test the imported Fiix portfolio through the public Homework API.

Session values are read from the root-only runtime file and are never printed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE_URL = "http://127.0.0.1:18008"
SITE_IDS = tuple(f"S{number:03d}" for number in range(1, 41))


def building_id(site_id: str) -> str:
    return "hw1-building-fiix-" + site_id.lower()


def call(token: str, method: str, path: str, payload=None, expected: int = 200):
    body = None if payload is None else json.dumps(payload).encode()
    request = Request(
        BASE_URL + path,
        data=body,
        method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=60) as response:
            status = response.status
            content = json.loads(response.read())
    except HTTPError as error:
        status = error.code
        content = json.loads(error.read())
    if status != expected:
        raise AssertionError(f"{method} {path}: expected {expected}, received {status}: {content}")
    return content


def identity(token: str):
    return call(token, "GET", "/api/homework/identity")


def execute(token: str, tool: str, site_id: str, *, expected: int = 200):
    return call(
        token,
        "POST",
        "/api/homework/tools/execute",
        {
            "tool": tool,
            "arguments": {"building_ids": [building_id(site_id)], "limit": 1},
        },
        expected,
    )


def main(token_file: Path) -> int:
    if not token_file.is_file() or token_file.stat().st_mode & 0o077:
        raise ValueError("Token file must exist and be owner-only")
    tokens = json.loads(token_file.read_text())
    required = {
        "hw1-portfolio",
        "hw1-compliance",
        "hw1-property-S001",
        "hw1-user-S001",
    }
    if not required.issubset(tokens):
        raise ValueError("Required verification identities are unavailable")

    property_identity = identity(tokens["hw1-property-S001"])
    assert property_identity["role"] == "property_manager"
    assert property_identity["building_ids"] == [building_id("S001")]

    general_identity = identity(tokens["hw1-user-S001"])
    assert general_identity["role"] == "general_user"
    assert general_identity["building_ids"] == [building_id("S001")]

    portfolio_identity = identity(tokens["hw1-portfolio"])
    assert all(building_id(site) in portfolio_identity["building_ids"] for site in SITE_IDS)

    compliance_identity = identity(tokens["hw1-compliance"])
    assert all(building_id(site) in compliance_identity["building_ids"] for site in SITE_IDS)

    property_work_orders = execute(tokens["hw1-property-S001"], "work_order_status", "S001")
    assert property_work_orders["total_matching"] > 0
    assert property_work_orders["records"][0]["building_id"] == building_id("S001")

    general_work_orders = execute(tokens["hw1-user-S001"], "work_order_status", "S001")
    assert general_work_orders["total_matching"] > 0
    execute(tokens["hw1-user-S001"], "asset_information", "S001", expected=403)
    execute(tokens["hw1-property-S001"], "work_order_status", "S002", expected=403)

    compliance = execute(tokens["hw1-compliance"], "compliance_evidence", "S001")
    assert compliance["total_matching"] > 0
    vendor = execute(tokens["hw1-portfolio"], "vendor_performance", "S001")
    assert vendor["total_matching"] > 0
    energy = execute(tokens["hw1-portfolio"], "energy_findings", "S001")
    assert energy["total_matching"] > 0

    print(
        json.dumps(
            {
                "status": "verified",
                "buildings_visible_to_portfolio": sum(
                    building_id(site) in portfolio_identity["building_ids"] for site in SITE_IDS
                ),
                "buildings_visible_to_compliance": sum(
                    building_id(site) in compliance_identity["building_ids"] for site in SITE_IDS
                ),
                "property_manager_scope": property_identity["building_ids"],
                "general_user_scope": general_identity["building_ids"],
                "property_work_order_count_s001": property_work_orders["total_matching"],
                "general_work_order_count_s001": general_work_orders["total_matching"],
                "compliance_evidence_count_s001": compliance["total_matching"],
                "vendor_performance_count_s001": vendor["total_matching"],
                "energy_finding_count_s001": energy["total_matching"],
                "general_user_asset_information": "denied",
                "property_manager_cross_building": "denied",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: verify_fiix_portfolio.py /path/to/test-session-tokens.json")
    raise SystemExit(main(Path(sys.argv[1])))
