"""The table catalogue: what each table is for, searchable by meaning.

The agent used to receive 221 table names and 2,560 column names and guess. Measured 17 Sep
2026: "which assets have never been scored?" became a LEFT JOIN against a table that does not
exist. These pin the parts of the catalogue that can be wrong without a database: which
domain a table lands in, which links are inferred from a column name, what a sample may
contain, and what the embedded text says.
"""
from src.services import catalog as C


class TestDomains:

    def test_every_known_prefix_lands_somewhere_named(self):
        for name, dom in (("compliance_certificates", "compliance"), ("energy_meters", "energy"),
                          ("work_orders", "work"), ("vendor_monthly_scorecards", "vendors"),
                          ("purchase_orders", "supply"), ("ingestion_documents", "docs"),
                          ("organizations", "org"), ("assets", "assets"), ("rca_problems", "platform"),
                          ("currencies", "reference")):
            assert C.domain_for(name) == dom, name

    def test_an_unknown_table_is_other_not_an_error(self):
        assert C.domain_for("zzz_mystery") == "other"
        assert C.domain_label("other") == "Other"

    def test_the_owner_is_the_service_whose_migrations_create_it(self):
        assert C.owner_for("compliance_certificates") == "svc-operations-intelligence"
        assert C.owner_for("work_orders") == "svc-work-order-management"
        assert C.owner_for("udr_run_versions") == "svc-udr"
        assert "core" in C.owner_for("assets")


class TestInferredLinks:
    """230 of this schema's 384 links exist only by column naming. The rule that finds them
    must find the real ones and not invent self-references or people."""

    TABLES = {"organizations", "buildings", "assets", "work_orders", "users", "categories", "companies"}

    def test_a_plural_table_is_found_from_a_singular_column(self):
        assert C.inferred_targets("organization_id", self.TABLES) == ["organizations"]
        assert C.inferred_targets("building_id", self.TABLES) == ["buildings"]
        assert C.inferred_targets("work_order_id", self.TABLES) == ["work_orders"]

    def test_ies_plurals_are_handled(self):
        assert C.inferred_targets("category_id", self.TABLES) == ["categories"]
        assert C.inferred_targets("company_id", self.TABLES) == ["companies"]

    def test_the_primary_key_itself_is_not_a_link(self):
        assert C.inferred_targets("id", self.TABLES) == []

    def test_people_columns_are_not_guessed_at(self):
        """created_by / approved_by are users by convention too, but a wrong guess here puts a
        join in the answer that was never there; those stay explicit."""
        for col in ("created_by_id", "approved_by_id", "parent_id"):
            assert C.inferred_targets(col, self.TABLES) == []

    def test_a_column_with_no_matching_table_links_nowhere(self):
        assert C.inferred_targets("tenant_id", self.TABLES) == []


class TestSamplesAreSafe:

    def test_secrets_never_leave_as_values(self):
        for col in ("password_hash", "api_key", "refresh_token", "otp_code", "client_secret", "salt"):
            assert C.redact_value(col, "hunter2") == "[redacted]", col

    def test_emails_are_masked_not_dropped(self):
        assert C.redact_value("email", "bala.r@plenum-tech.com") == "b***@plenum-tech.com"

    def test_ordinary_values_pass_through(self):
        assert C.redact_value("status", "Completed") == "Completed"
        assert C.redact_value("cost", 12.5) == 12.5

    def test_long_text_is_cut_so_a_sample_stays_a_sample(self):
        assert len(C.redact_value("notes", "x" * 500)) <= 160

    def test_a_whole_row_is_redacted_column_by_column(self):
        row = C.redact_row({"id": 1, "email": "a@b.co", "password_hash": "abc", "name": "Site A"})
        assert row == {"id": 1, "email": "a***@b.co", "password_hash": "[redacted]", "name": "Site A"}


class TestTheWords:

    META = {
        "table": "compliance_certificates", "domain": "compliance",
        "columns": [{"name": "id", "type": "uuid", "nullable": False}, {"name": "certificate_type_code", "type": "varchar", "nullable": True},
                    {"name": "expiry_date", "type": "date", "nullable": True}, {"name": "vendor_id", "type": "uuid", "nullable": True}],
        "keys": {"primary_key": ["id"], "foreign_keys": []},
        "links_out": [{"column": "vendor_id", "to_table": "vendors", "to_column": "id", "kind": "inferred"}],
        "links_in": [{"from_table": "compliance_scan_runs", "column": "certificate_id", "kind": "inferred"}],
    }

    def test_the_heuristic_purpose_names_what_it_links_to(self):
        w = C.heuristic_purpose("compliance_certificates", self.META["columns"], self.META["keys"], self.META["links_out"])
        assert "vendors" in w["purpose"]
        assert w["grain"].startswith("one row per")
        assert len(w["answers"]) >= 3

    def test_the_embedded_text_carries_meaning_vocabulary_and_joins(self):
        words = {"grain": "one certificate", "purpose": "Statutory certificates held by vendors and buildings.",
                 "answers": ["which certificates expire soon"], "not_for": "Work orders raised from a lapse live in work_orders."}
        t = C.semantic_text(self.META, words)
        for needle in ("compliance_certificates", "Compliance", "Statutory certificates", "which certificates expire soon",
                       "Not for:", "certificate_type_code", "Links to: vendors", "Referenced by: compliance_scan_runs"):
            assert needle in t, needle

    def test_vectors_are_serialised_the_way_pgvector_reads_them(self):
        assert C._vec([0.5, -1.0, 0.1234567891]) == "[0.5000000,-1.0000000,0.1234568]"
