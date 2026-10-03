"""Does every page get the data a company has ingested? Run it after an ingest and it says.

On 28 Sep 2026 three things hid ingested data and each looked like "there is none":
  - svc-udr answered 503 to every signed-in call (its compose block never said where
    ops-intelligence was), so the Buildings graph and saved spaces read nothing for days;
  - a query change made every approvals read 500 on hoistra_test (vendors.id is a uuid there
    and varchar on production), and the Vendors Invoices tab read "No invoice line is held"
    over 21 held lines;
  - a Rebuild on an instance running older scoring code scored every work order a second time.
None of them was found by looking at the database, and none by looking at a page: the
database was right and each page's empty state was worded like a real answer. This compares
the two, for one company, per kind of data:

  the database   read-only counts (Postgres enforces it: default_transaction_read_only=on)
  the reads      the same routes the pages call, replayed inside the running containers
                 through _replay_page_reads.py, also read-only, and with no sign-in
  the stack      whether svc-udr can reach its identity check, and every 5xx the gateway
                 logged for a GET in the window

Nothing here writes: not to the database, not through an API. Each line reads PASS, WARN
(the page gets less than the database holds, by a stated rule) or FAIL. Exits 1 on any FAIL.

    python db/tools/check_data_visibility.py <organization_id> [--project hoistra_plenum]
        [--home-org <id> --role superadmin] [--since 2h]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

import asyncpg

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
REPLAY = HERE / "_replay_page_reads.py"


def dsn() -> str:
    """HOISTRA_TEST_DSN, else PLENUM_DB_DSN, from the environment or the repo .env."""
    vals = {}
    try:
        for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                vals[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    raw = (os.environ.get("HOISTRA_TEST_DSN") or vals.get("HOISTRA_TEST_DSN")
           or os.environ.get("PLENUM_DB_DSN") or vals.get("PLENUM_DB_DSN"))
    if not raw:
        raise SystemExit("Neither HOISTRA_TEST_DSN nor PLENUM_DB_DSN is set, in the environment or the repo .env.")
    return re.sub(r"^postgresql\+[a-z]+:", "postgresql:", raw).split("?")[0]


# ── what the database holds ───────────────────────────────────────────────────────────────
#: key: SQL returning one integer for :org ($1). Only tables every deployment has.
DB = {
    "buildings": "SELECT count(*) FROM plenum_cafm.buildings WHERE organization_id = $1",
    "assets": "SELECT count(*) FROM plenum_cafm.assets WHERE organization_id = $1",
    "work_orders": "SELECT count(*) FROM plenum_cafm.work_orders WHERE organization_id = $1",
    "sections": "SELECT count(*) FROM plenum_cafm.building_sections WHERE organization_id = $1",
    "vendors": "SELECT count(*) FROM plenum_cafm.vendors WHERE organization_id = $1",
    "contracts": "SELECT count(*) FROM plenum_cafm.contract_sla_parameters WHERE organization_id = $1",
    "scorecards": "SELECT count(*) FROM plenum_cafm.vendor_monthly_scorecards WHERE organization_id = $1",
    "wo_scores": "SELECT count(*) FROM plenum_cafm.vendor_wo_scores WHERE organization_id = $1",
    "wo_scores_distinct": ("SELECT count(*) FROM (SELECT DISTINCT vendor_id, score_month, wo_code "
                           "FROM plenum_cafm.vendor_wo_scores WHERE organization_id = $1) d"),
    "invoices": "SELECT count(*) FROM plenum_cafm.invoice_verifications WHERE organization_id = $1",
    "held_invoice_lines": ("SELECT count(*) FROM plenum_cafm.approvals_queue_items WHERE organization_id = $1 "
                           "AND status = 'pending' AND item_type LIKE 'invoice_flag%'"),
    "pending_approvals": ("SELECT count(*) FROM plenum_cafm.approvals_queue_items "
                          "WHERE organization_id = $1 AND status = 'pending'"),
    # The register hides the same three flags list_certificates() does.
    "certificates": ("SELECT count(*) FROM plenum_cafm.compliance_certificates WHERE organization_id = $1 "
                     "AND COALESCE(raw_metadata->>'archived','') IN ('','false','0') "
                     "AND COALESCE(raw_metadata->>'superseded_duplicate','') IN ('','false','0') "
                     "AND COALESCE(raw_metadata->>'a1_test_fixture','') IN ('','false','0')"),
    "meters": "SELECT count(*) FROM plenum_cafm.energy_meters WHERE organization_id = $1",
    "open_anomalies": "SELECT count(*) FROM plenum_cafm.energy_anomalies WHERE organization_id = $1 AND status = 'open'",
    "inspections": "SELECT count(*) FROM plenum_cafm.inspections WHERE organization_id = $1",
    "vendors_listed": ("SELECT count(*) FROM plenum_cafm.vendors v WHERE v.organization_id = $1 AND ("
                       "EXISTS (SELECT 1 FROM plenum_cafm.vendor_monthly_scorecards c WHERE c.vendor_id::text = v.id::text) OR "
                       "EXISTS (SELECT 1 FROM plenum_cafm.contract_sla_parameters p WHERE p.vendor_id::text = v.id::text))"),
}


async def db_counts(org: str) -> dict[str, int | str]:
    conn = await asyncpg.connect(dsn(), ssl="require",
                                 server_settings={"default_transaction_read_only": "on",
                                                  "application_name": "hoistra-visibility-check"})
    try:
        if await conn.fetchval("SELECT current_setting('default_transaction_read_only')") != "on":
            raise SystemExit("the read-only guard is not active; refusing to run")
        out: dict[str, int | str] = {}
        for key, sql in DB.items():
            try:
                out[key] = int(await conn.fetchval(sql, org))
            except Exception as exc:  # noqa: BLE001 — a table this deployment lacks
                out[key] = f"error: {type(exc).__name__}"
        return out
    finally:
        await conn.close()


# ── what the reads return ─────────────────────────────────────────────────────────────────
OPS = [
    "/api/energy/buildings?organization_id=ORG&limit=5000",
    "/api/energy/sections?organization_id=ORG",
    "/api/energy/condition/assets?organization_id=ORG&limit=2000",
    "/api/energy/meters?organization_id=ORG&limit=500",
    "/api/energy/anomalies?organization_id=ORG&status=open&limit=500",
    "/api/compliance/certificates?organization_id=ORG&limit=1000",
    "/api/contract-performance/contracts?organization_id=ORG&limit=500",
    "/api/contract-performance/scorecards?organization_id=ORG&limit=200",
    "/api/contract-performance/saved-space/summary?organization_id=ORG",
    "/api/contract-performance/approvals?organization_id=ORG",
    "/api/contract-performance/invoices?organization_id=ORG&limit=500",
    "/api/approvals?organization_id=ORG&status=pending&limit=500",
]
WO = [
    "/api/assets?organization_id=ORG&limit=200",
    "/api/work-orders/?limit=200&organization_id=ORG",
    "/api/maintenance/inspections?organization_id=ORG&limit=500",
]
#: The Assets page reads the work-order list page by page to the end (assetsLive.readAllPages),
#: so the check reads it the same way. Page 1 is WO[1]; pages 2.. are appended per run.
WO_PAGE = "/api/work-orders/?limit=200&page={p}&organization_id=ORG"


def replay(container: str, paths: list[str], org: str, home_org: str, role: str) -> dict[str, dict]:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("\n".join(paths))
        plist = f.name
    try:
        for src, dst in ((str(REPLAY), "/tmp/_replay_page_reads.py"), (plist, "/tmp/_replay_paths.txt")):
            subprocess.run(["docker", "cp", src, f"{container}:{dst}"], check=True, capture_output=True)
        run = subprocess.run(
            ["docker", "exec", "-w", "/app", container, "python", "/tmp/_replay_page_reads.py",
             org, "/tmp/_replay_paths.txt", home_org, role],
            capture_output=True, text=True, timeout=600)
    finally:
        os.unlink(plist)
    out: dict[str, dict] = {}
    guard = None
    for line in run.stdout.splitlines():
        if line.startswith('{"guard"'):
            guard = json.loads(line)["guard"]
        elif line.startswith('{"path"'):
            rec = json.loads(line)
            out[rec["path"]] = rec
    if guard != "on":
        raise SystemExit(f"{container}: the replay did not confirm its read-only guard.\n{run.stderr[-600:]}")
    return out


def udr_identity(project: str) -> str:
    code = ("import httpx\nfrom src.services import principal as p\n"
            "try:\n r = httpx.get(p.OPS_BASE_URL + '/api/auth/me', timeout=5); print(r.status_code)\n"
            "except Exception as e: print('unreachable ' + p.OPS_BASE_URL)\n")
    r = subprocess.run(["docker", "exec", f"{project}-svc-udr-1", "python", "-c", code],
                       capture_output=True, text=True, timeout=60)
    return (r.stdout.strip() or r.stderr.strip()[-200:])


def gateway_5xx(project: str, since: str) -> dict[str, int]:
    r = subprocess.run(["docker", "logs", "--since", since, f"{project}-gateway-app-1"],
                       capture_output=True, text=True, timeout=60)
    counts: dict[str, int] = {}
    for m in re.finditer(r'"GET (/backend/[^ ?"]+)[^"]*" (5\d\d) ', r.stdout + r.stderr):
        key = re.sub(r"/[0-9a-f]{8}-[0-9a-f-]{27,}", "/{id}", m.group(1)) + " " + m.group(2)
        counts[key] = counts.get(key, 0) + 1
    return counts


# ── the comparison ────────────────────────────────────────────────────────────────────────
def n(rec: dict | None, *keys: str):
    if not rec or rec.get("status") != 200:
        return None
    s = rec["shape"]
    for k in keys:
        if isinstance(s.get(k), int) and not isinstance(s.get(k), bool):
            return s[k]
    return None


def status_of(rec: dict | None) -> str:
    if rec is None:
        return "not run"
    return str(rec.get("status")) + (" " + rec.get("detail", "")[:160] if rec.get("status") != 200 else "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("organization_id")
    ap.add_argument("--project", default="hoistra_plenum", help="docker compose project name")
    ap.add_argument("--home-org", help="the reading user's own company (default: the one checked)")
    ap.add_argument("--role", default="admin", help="the reading user's role, e.g. superadmin")
    ap.add_argument("--since", default="2h", help="gateway log window for 5xx, e.g. 30m, 2h")
    a = ap.parse_args()
    org, home = a.organization_id, a.home_org or a.organization_id

    db = asyncio.run(db_counts(org))
    ops = replay(f"{a.project}-svc-operations-intelligence-1", OPS, org, home, a.role)
    wo_total = db.get("work_orders") if isinstance(db.get("work_orders"), int) else 0
    wo_pages = [WO_PAGE.format(p=p) for p in range(2, min(50, wo_total // 200 + 3))]
    wo = replay(f"{a.project}-backend-app-1", WO + wo_pages, org, home, a.role)
    R = lambda paths, i: paths.get(OPS[i] if paths is ops else WO[i])  # noqa: E731

    rows: list[tuple[str, str, str, str, str]] = []  # (verdict, what, db, reads, note)

    def compare(what, db_key, rec, got, cap=None, note_ok="", equal_is="PASS"):
        have = db.get(db_key)
        if got is None:
            rows.append(("FAIL", what, str(have), "—", "the read did not answer: " + status_of(rec)))
        elif isinstance(have, str):
            rows.append(("WARN", what, have, str(got), "database count failed"))
        elif got == have:
            rows.append((equal_is, what, str(have), str(got), note_ok))
        elif cap is not None and got >= cap and have > got:
            rows.append(("WARN", what, str(have), str(got), f"the read is capped at {cap}; {have - got} are never sent"))
        else:
            rows.append(("FAIL", what, str(have), str(got), "the read returns a different number than the database holds"))

    compare("Buildings", "buildings", R(ops, 0), n(R(ops, 0), "count", "buildings"))
    compare("Building sections", "sections", R(ops, 1), n(R(ops, 1), "count", "sections"))
    compare("Assets (condition)", "assets", R(ops, 2), n(R(ops, 2), "total", "assets"))
    compare("Assets (asset list)", "assets", R(wo, 0), n(R(wo, 0), "<list>"), cap=200)
    first = R(wo, 1)
    pages = [first] + [wo.get(p) for p in wo_pages]
    got_wo = None if any(p is None or p.get("status") != 200 for p in pages) else sum(n(p, "<list>") or 0 for p in pages)
    # The list route ignores organization_id, so a superadmin viewing a company also gets other
    # companies' work orders: more than the company holds is that, not a fault in the reads.
    if isinstance(got_wo, int) and isinstance(db.get("work_orders"), int) and got_wo > db["work_orders"]:
        rows.append(("WARN", "Work orders (Assets page, every page)", str(db["work_orders"]), str(got_wo),
                     f"{got_wo - db['work_orders']} belong to other companies — GET /api/work-orders/ ignores organization_id"))
    else:
        compare("Work orders (Assets page, every page)", "work_orders", first, got_wo)
    compare("Meters", "meters", R(ops, 3), n(R(ops, 3), "count", "meters"), cap=500)
    compare("Open energy anomalies", "open_anomalies", R(ops, 4), n(R(ops, 4), "count", "anomalies"), cap=500)
    compare("Compliance certificates", "certificates", R(ops, 5), n(R(ops, 5), "count", "certificates"), cap=1000)
    compare("Contract terms", "contracts", R(ops, 6), n(R(ops, 6), "count", "parameters"), cap=500)
    compare("Vendor scorecards", "scorecards", R(ops, 7), n(R(ops, 7), "count", "scorecards"), cap=200)
    summ = R(ops, 8)
    rows.append(("PASS" if summ and summ.get("status") == 200 else "FAIL", "Vendors page summary", "—",
                 str(n(summ, "scorecards")), "" if summ and summ.get("status") == 200 else status_of(summ)))
    cp = R(ops, 9)
    held = None
    if cp and cp.get("status") == 200:
        held = sum(v for k, v in (cp["shape"].get("items.item_type") or {}).items() if k.startswith("invoice_flag"))
    compare("Held invoice lines (Invoices tab)", "held_invoice_lines", cp, held)
    compare("Verified invoices", "invoices", R(ops, 10), n(R(ops, 10), "count", "invoices"),
            note_ok="the Vendors page lists only held lines, not these")
    ap_rec = R(ops, 11)
    got = n(ap_rec, "count", "items")
    have = db.get("pending_approvals")
    if got is None:
        rows.append(("FAIL", "Pending decisions (queue)", str(have), "—", "the read did not answer: " + status_of(ap_rec)))
    elif isinstance(have, int) and got < have:
        rows.append(("WARN", "Pending decisions (queue)", str(have), str(got),
                     f"{have - got} hidden — items whose certificate/vendor/anomaly is gone, or past the 500 cap"))
    else:
        rows.append(("PASS", "Pending decisions (queue)", str(have), str(got), "includes items the queue merges in"))
    compare("Inspections", "inspections", R(wo, 2), n(R(wo, 2), "count", "inspections"), cap=500)

    ws, wd = db.get("wo_scores"), db.get("wo_scores_distinct")
    if isinstance(ws, int) and isinstance(wd, int):
        rows.append(("PASS" if ws == wd else "FAIL", "Work-order scores, one per job per month", str(wd), str(ws),
                     "" if ws == wd else f"{ws - wd} duplicate score rows — each monthly card counts those jobs twice"))
    vl, vt = db.get("vendors_listed"), db.get("vendors")
    if isinstance(vl, int) and isinstance(vt, int):
        rows.append(("PASS" if vl == vt else "WARN", "Vendors on the Vendors page", str(vt), str(vl),
                     "" if vl == vt else f"{vt - vl} vendor(s) have no contract and no scorecard, so the directory never lists them"))

    ident = udr_identity(a.project)
    rows.append(("PASS" if ident == "401" else "FAIL", "svc-udr identity check (Buildings graph, spaces)", "—", ident,
                 "" if ident == "401" else "svc-udr cannot reach ops-intelligence; every signed-in call answers 503"))
    errs = gateway_5xx(a.project, a.since)
    rows.append(("PASS" if not errs else "FAIL", f"GETs that 5xx'd in the last {a.since}", "0", str(sum(errs.values())),
                 "; ".join(f"{k} ×{v}" for k, v in sorted(errs.items(), key=lambda kv: -kv[1])[:6])))

    width = max(len(r[1]) for r in rows)
    print(f"Data visibility — organization {org} (read-only; reads replayed as {a.role})\n")
    print(f"{'':5} {'what':<{width}}  {'in DB':>8}  {'reads':>8}  note")
    for verdict, what, have, got, note in rows:
        print(f"{verdict:5} {what:<{width}}  {have:>8}  {got:>8}  {note}")
    fails = sum(r[0] == "FAIL" for r in rows)
    warns = sum(r[0] == "WARN" for r in rows)
    print(f"\n{fails} FAIL, {warns} WARN, {len(rows) - fails - warns} PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
