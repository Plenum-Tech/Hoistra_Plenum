"""
Quick ingestion test runner — tests all CSV files against the live API.

Usage:
    python tests/run_ingestion_tests.py
"""
import httpx
import os

BASE = "http://localhost:8001"

DB_DIR  = r"C:\Users\Lenovo\Downloads\files\db_ready"
FAC_DIR = r"C:\Users\Lenovo\Downloads\Facilities-20220108T172755Z-001\Facilities"


def test_csv(path: str, group: str) -> dict:
    fname = os.path.basename(path)
    with open(path, "rb") as f:
        data = f.read()
    try:
        r = httpx.post(
            f"{BASE}/ingest/csv",
            files={"file": (fname, data, "text/csv")},
            data={"dry_run": "true"},
            timeout=90,
        )
        if r.status_code != 200:
            return {"file": fname, "group": group, "error": f"HTTP {r.status_code}: {r.text[:150]}"}
        res = r.json()
        return {
            "file":     fname,
            "group":    group,
            "route":    res.get("route", "?"),
            "score":    res.get("confidence", {}).get("eval_score", 0),
            "entities": res.get("entity_counts", {}),
            "total":    sum(res.get("entity_counts", {}).values()),
            "viols":    res.get("confidence", {}).get("rules_violations", []),
        }
    except Exception as exc:
        return {"file": fname, "group": group, "error": str(exc)[:120]}


def main() -> None:
    all_files = (
        [(os.path.join(DB_DIR, f), "db_ready")
         for f in sorted(os.listdir(DB_DIR)) if f.endswith(".csv")]
        + [(os.path.join(FAC_DIR, f), "facilities")
           for f in sorted(os.listdir(FAC_DIR)) if f.endswith(".csv")]
    )

    results = []
    for path, group in all_files:
        print(f"  testing {os.path.basename(path)} ...", flush=True)
        results.append(test_csv(path, group))

    # ── Summary table ──────────────────────────────────────────────────────
    print()
    print(f"{'FILE':<44} {'GRP':<9} {'ROUTE':<14} {'ENT':>5} {'SCORE':>6}  ISSUES")
    print("-" * 110)

    ok = warn = fail = err = 0
    for r in results:
        if "error" in r:
            err += 1
            print(f"{r['file']:<44} {r['group']:<9} ERROR          {'':>5} {'':>6}  {r['error'][:50]}")
            continue
        route = r["route"]
        icon  = "OK  " if route == "accept" else ("WARN" if route == "review_queue" else "FAIL")
        if route == "accept":        ok   += 1
        elif route == "review_queue": warn += 1
        else:                         fail += 1
        issues = "; ".join(v[:55] for v in r["viols"])
        ent_detail = str({k: v for k, v in r["entities"].items() if v > 0})
        print(f"{r['file']:<44} {r['group']:<9} {icon} {route:<10} {r['total']:>5} {r['score']:>6.2f}  {issues or ent_detail[:50]}")

    print()
    print(f"TOTALS:  OK={ok}  REVIEW={warn}  FAIL={fail}  ERROR={err}  (of {len(results)} files)")


if __name__ == "__main__":
    main()
