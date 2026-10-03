"""UDR's pairwise tests read each column's statistics once — and get exactly the same report."""
import dataclasses
import random
import time

import pytest

from src.udr import memo
from src.udr.pipeline import run_udr_pipeline

VOLATILE = {"stage_times", "stage_durations", "sla_report"}
DEST = {"sites": "sites", "assets": "assets", "vendors": "vendors", "work_orders": "work_orders"}


def _workbook(n_assets=400, n_wos=4000, seed=7):
    rnd = random.Random(seed)
    sites = [{"site_id": f"S-{i:02d}", "site_name": f"Site {i}", "city": rnd.choice(["Leeds", "York"])} for i in range(12)]
    assets = [{"asset_id": f"A-{i:04d}", "site_id": rnd.choice(sites)["site_id"],
               "asset_type": rnd.choice(["AHU", "FCU", "Chiller", "Pump"]), "serial": f"SN{rnd.randrange(10**8)}",
               "install_date": f"20{rnd.randrange(10, 25)}-0{rnd.randrange(1, 9)}-1{rnd.randrange(0, 9)}",
               "criticality": rnd.choice(["Low", "Medium", "High", None])} for i in range(n_assets)]
    vendors = [{"vendor_code": f"V{i:03d}", "vendor_name": f"Vendor {i} Ltd", "trade": rnd.choice(["Lift", "Fire", "HVAC"])}
               for i in range(30)]
    wos = [{"wo_code": f"W-{i:06d}", "asset_id": rnd.choice(assets)["asset_id"], "vendor": rnd.choice(vendors)["vendor_code"],
            "cost": str(rnd.randrange(50, 5000)), "status": rnd.choice(["open", "closed"]),
            "raised": f"2026-0{rnd.randrange(1, 9)}-0{rnd.randrange(1, 9)}"} for i in range(n_wos)]
    return {"sites": sites, "assets": assets, "vendors": vendors, "work_orders": wos}


def _run(tables):
    t0 = time.perf_counter()
    res = run_udr_pipeline({k: [dict(r) for r in v] for k, v in tables.items()}, run_id="t",
                           dest_table_by_source=DEST)
    return res, time.perf_counter() - t0


def _comparable(res):
    d = dataclasses.asdict(res)
    for k in VOLATILE:
        d.pop(k, None)
    return d


def test_the_report_is_identical_with_and_without_the_memo(monkeypatch):
    tables = _workbook()
    with_memo, _ = _run(tables)
    monkeypatch.setattr(memo, "enabled", lambda: False)
    without, _ = _run(tables)
    assert _comparable(with_memo) == _comparable(without)


def test_the_memo_makes_the_pairwise_stages_much_faster(monkeypatch):
    tables = _workbook(n_assets=1500, n_wos=20000)
    _, fast = _run(tables)
    monkeypatch.setattr(memo, "enabled", lambda: False)
    _, slow = _run(tables)
    assert slow / fast >= 5, f"memo {fast:.2f}s vs none {slow:.2f}s"


def test_outside_a_scope_nothing_is_cached():
    rows = [{"a": "1"}]
    calls = []
    assert memo.memoized("k", rows, "a", lambda: calls.append(1) or 1) == 1
    assert memo.memoized("k", rows, "a", lambda: calls.append(1) or 1) == 1
    assert len(calls) == 2


def test_inside_a_scope_a_column_is_computed_once_and_nested_scopes_share_it():
    rows = [{"a": "1"}]
    calls = []
    with memo.memo_scope():
        memo.memoized("k", rows, "a", lambda: calls.append(1) or 1)
        with memo.memo_scope():
            memo.memoized("k", rows, "a", lambda: calls.append(1) or 1)
    assert len(calls) == 1
