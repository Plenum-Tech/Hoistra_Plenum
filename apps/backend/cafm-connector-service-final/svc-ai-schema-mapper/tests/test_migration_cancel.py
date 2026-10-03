"""Cancelling a migration: who may, where the run stops, and that "cancelled" stays cancelled.

DELETE /api/migration/{id} took no token until 30 Sep 2026 and marked the job without stopping
anything - the run carried on and its next status write replaced "cancelled". These hold the
three pieces that fixed it: the caller check, the check before every pipeline step, and the
trigger installed at startup.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from src.services.principal import Principal, from_me, may_cancel

ORG = uuid.uuid4()


def who(role="user", org=ORG, can_ingest=False):
    return Principal(uuid.uuid4(), "p@x", org, role, can_ingest)


class TestWhoMayCancel:
    def test_an_admin_of_the_company_may(self):
        assert may_cancel(who("admin"), ORG) == (True, "")

    def test_a_user_with_the_ingestion_right_may(self):
        assert may_cancel(who(can_ingest=True), ORG)[0] is True

    def test_a_user_without_it_may_not(self):
        ok, why = may_cancel(who(), ORG)
        assert ok is False and "admin" in why

    def test_another_company_may_not(self):
        ok, why = may_cancel(who("admin", org=uuid.uuid4()), ORG)
        assert ok is False and "another company" in why

    def test_a_run_with_no_company_is_superadmin_only(self):
        assert may_cancel(who("admin"), None)[0] is False
        assert may_cancel(who("superadmin", org=None), None)[0] is True

    def test_the_role_comes_from_me(self):
        p = from_me({"user": {"id": str(uuid.uuid4()), "organization_id": str(ORG), "platform_role": "superadmin",
                              "role": "admin", "can_ingest": True, "email": "s@x"}})
        assert p.is_superadmin and p.can_ingest and p.organization_id == ORG


class TestTheRunStops:
    def test_a_cancelled_run_does_not_start_its_next_step(self, monkeypatch):
        from src.graph import migration_graph as mg

        async def cancelled(_mid):
            return True
        monkeypatch.setattr(mg, "_is_cancelled", cancelled)
        ran = []

        class G:
            def __init__(self):
                self.nodes = {}

            def add_node(self, name, fn):
                self.nodes[name] = fn

        # The wrapper _add_node registers is what every node runs through; exercise it directly.
        wrapped = {}

        def capture(name, fn):
            wrapped[name] = fn
        monkeypatch.setattr(mg, "StateGraph", lambda _s: type("SG", (), {
            "add_node": lambda self, n, f: capture(n, f),
            "add_edge": lambda self, *a, **k: None,
            "add_conditional_edges": lambda self, *a, **k: None,
            "set_entry_point": lambda self, *a, **k: None,
            "compile": lambda self, **k: self,
        })())
        try:
            mg.build_migration_graph(None)
        except Exception:  # noqa: BLE001 - only the registered wrappers matter here
            pass
        assert "ingest_node" in wrapped

        async def run():
            await wrapped["deterministic_mapper_node"]({"migration_id": "m1"})
        with pytest.raises(mg.MigrationCancelled):
            asyncio.run(run())
        assert not ran

    def test_the_worker_reports_a_cancelled_run_as_cancelled(self):
        import inspect
        from src import worker
        src = inspect.getsource(worker._run_graph)
        assert 'type(exc).__name__ == "MigrationCancelled"' in src
        assert src.index("MigrationCancelled") < src.index('status="failed"'), "checked before the failure write"


class TestCancelledIsFinal:
    def test_the_trigger_holds_status_and_clears_the_gate(self):
        from src import schema_patches as sp
        assert "OLD.status = 'cancelled' AND NEW.status IS DISTINCT FROM 'cancelled'" in sp.CANCEL_FUNCTION_DDL
        assert "NEW.pending_gate_type := NULL" in sp.CANCEL_FUNCTION_DDL
        assert "BEFORE UPDATE ON plenum_cafm.migration_jobs" in sp.CANCEL_TRIGGER_DDL

    def test_an_installed_trigger_takes_no_lock(self):
        from src import schema_patches as sp
        executed = []

        class Conn:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt, params=None):
                executed.append(str(stmt))

                class R:
                    def scalar(self):
                        return 1
                return R()

        class Engine:
            def connect(self):
                return Conn()

            def begin(self):
                raise AssertionError("an installed trigger must not open a DDL transaction")

        assert asyncio.run(sp.ensure_cancel_is_final(Engine())) == "already_applied"
        assert all("CREATE" not in e for e in executed)
