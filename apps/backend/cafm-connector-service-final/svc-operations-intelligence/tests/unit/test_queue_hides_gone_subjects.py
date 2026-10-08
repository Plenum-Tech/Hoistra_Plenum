"""A pending decision about something that is no longer there is not offered to anyone.

An approvals item names its subject by (related_entity_type, related_entity_id): a type name
and a loose uuid, not a foreign key. Nothing that removes a subject closes the items about
it - deleting a document takes its certificates, a data reset empties a company's Compliance
and Contracts tables, archiving hides a certificate from the register - so each left its lapse
alert or vendor block pending. On 28 Sep 2026 the top bar read "7 Pending", every one of them
Compliance, over a register that held no certificates at all.

These pin the rule list_queue applies, in SQL, before its LIMIT: a pending item is listed only
while its subject is still on record, in the state the register would show it. Decided items
are history and are never hidden. No database.
"""
from __future__ import annotations

from sqlalchemy.dialects import postgresql

from src.shared import approvals


class _Session:
    def __init__(self) -> None:
        self.statements: list = []

    async def execute(self, stmt, *a, **k):
        self.statements.append(stmt)

        class _R:
            def scalars(self):
                return self

            def all(self):
                return []

        return _R()


async def _sql(**kw) -> str:
    s = _Session()
    await approvals.list_queue(s, **kw)
    return " ".join(str(s.statements[0].compile(dialect=postgresql.dialect())).split())


async def test_a_certificate_item_needs_its_certificate_on_the_register():
    sql = await _sql()
    assert "FROM plenum_cafm.compliance_certificates" in sql
    assert "compliance_certificates.id = plenum_cafm.approvals_queue_items.related_entity_id" in sql


async def test_a_certificate_the_register_hides_hides_its_items_too():
    # The same three flags list_certificates() drops a row for.
    sql = await _sql()
    assert "compliance_certificates.raw_metadata ->> " in sql, "flags must be read as text"
    s = _Session()
    await approvals.list_queue(s)
    params = s.statements[0].compile(dialect=postgresql.dialect()).params
    names = {v for v in params.values() if isinstance(v, str)}
    assert {"archived", "superseded_duplicate", "a1_test_fixture"} <= names


async def test_a_vendor_item_needs_its_vendor():
    # vendors.id is varchar on production (about half the ids are legacy non-uuid) and uuid on
    # hoistra_test. Comparing only the item's side as text raised "operator does not exist:
    # uuid = character varying" on hoistra_test (28 Sep 2026) and every approvals read 500'd,
    # emptying the Decision queue and the Vendors Invoices tab. Both sides as text work on both.
    sql = await _sql()
    assert "FROM plenum_cafm.vendors" in sql
    assert ("CAST(plenum_cafm.vendors.id AS VARCHAR) = "
            "CAST(plenum_cafm.approvals_queue_items.related_entity_id AS VARCHAR)") in sql


async def test_anomaly_and_contract_items_need_their_row():
    sql = await _sql()
    assert "FROM plenum_cafm.energy_anomalies" in sql
    assert "FROM plenum_cafm.contract_sla_parameters" in sql


async def test_decided_items_are_history_and_are_never_hidden():
    sql = await _sql(status=None)
    assert "approvals_queue_items.status != %(status_1)s" in sql
    s = _Session()
    await approvals.list_queue(s, status=None)
    params = s.statements[0].compile(dialect=postgresql.dialect()).params
    assert "pending" in [v for v in params.values() if isinstance(v, str)]


async def test_an_item_that_names_nothing_checkable_is_left_alone():
    # A site, a document or no subject at all cannot be proved gone, so it stays listed.
    sql = await _sql()
    assert "related_entity_id IS NULL" in sql
    assert "related_entity_type IS NULL" in sql
    assert "related_entity_type NOT IN" in sql


async def test_the_rule_runs_before_the_limit():
    # Filtering after LIMIT would let a page of orphans crowd every live item out.
    sql = await _sql(limit=5)
    assert sql.index("EXISTS") < sql.index("LIMIT")
