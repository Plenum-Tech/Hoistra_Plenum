"""A lookup table is for a vocabulary, not for whatever two columns happen to share.

Normalising a shared attribute means: the values are a closed, repeating set that the schema
does not already model — status, currency, utility, asset category. The automatic pass was
applying it to anything two tables had in common, and on the Vasiliki data that produced:

    phone                people's phone numbers, 20 values over 33 rows
    free_text_attr_G11   people's names, 20 over 33
    install_date         15 equipment commissioning dates

A phone number is not a vocabulary; neither is a name, nor a date. Each one puts a foreign key
on a per-record attribute and a table of trivia into the customer's schema. The boolean guard
added earlier was the first case of this; these are the rest of it.

Four questions decide it, in the order a person would ask them:

  does it repeat        a vocabulary is re-used; values that are nearly one-per-row are an
                        identifier or free text, whatever they look like
  is it a flag          yes/no is not a vocabulary (the existing boolean guard)
  is it a measurement   a date or timestamp is measured, not chosen from a list
  is it contact data    an email, a URL or a phone number belongs to its record

A PERSON may still ask for one. Re-classifying a foreign key to a shared attribute at the gate
is a deliberate instruction, and alarm_id — 769 near-unique codes — is exactly the table that
was asked for on 30 Oct. So these rules bind the automatic pass only. The boolean guard stays
on both paths: a two-row true/false table is never what anybody meant.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.column_intelligence import synthesize_shared_attribute_table  # noqa: E402


def _synth(pairs, canonical="attr", **kw):
    tables = {}
    for (t, c, vals) in pairs:
        tables.setdefault(t, {"rows": [], "columns": []})
        tables[t]["columns"].append(c)
        tables[t]["rows"] = [{c: v} for v in vals]
    return synthesize_shared_attribute_table(
        tables, canonical_name=canonical, group_id="G1",
        member_pairs=[(t, c) for (t, c, _) in pairs], **kw)


class TestWhatIsRefusedAutomatically:
    def test_phone_numbers_are_not_a_vocabulary(self):
        a = [f"029 2018 0{i:03d}" for i in range(20)]
        assert _synth([("contacts", "phone", a),
                       ("vendors", "account_manager_phone", a[:13])], "phone") is None

    def test_peoples_names_are_not_a_vocabulary(self):
        # 20 contacts and 13 vendors, as in the data this came from.
        names = ["Megan Rhys", "Jamal Osei", "Kate Pritchard", "Dewi Harris", "Aled Parry",
                 "Fiona Caldwell", "Ravi Menon", "Rhian Evans", "Owen Hughes", "Priya Patel",
                 "Gareth Morgan", "Elin Williams", "Tom Reid", "Nadia Khan", "Dafydd Jones",
                 "Sophie Clarke", "Marcus Bennett", "Hannah Price", "Idris Thomas", "Ceri Lloyd"]
        assert _synth([("contacts", "name", names),
                       ("vendors", "account_manager", names[:13])], "name") is None

    def test_dates_are_measured_not_chosen(self):
        d = ["2009-11-02", "2011-06-01", "2011-10-01", "2014-03-20", "2016-07-11"]
        assert _synth([("assets", "install_date", d * 8),
                       ("meters", "install_date", d * 4)], "install_date") is None

    def test_emails_belong_to_their_record(self):
        e = [f"person{i}@example.test" for i in range(12)]
        assert _synth([("contacts", "email", e), ("vendors", "am_email", e[:8])],
                      "email") is None

    @pytest.mark.parametrize("vals", [
        ["https://a.example", "https://b.example", "https://c.example"],
        ["+44 7700 900101", "+44 7700 900102", "+44 7700 900103"],
    ])
    def test_urls_and_numbers_too(self, vals):
        assert _synth([("a", "x", vals), ("b", "y", vals)], "x") is None

    def test_a_column_that_barely_repeats_is_an_identifier(self):
        codes = [f"REF-{i:04d}" for i in range(40)]
        assert _synth([("a", "ref", codes), ("b", "ref", codes[:5])], "ref") is None


class TestWhatStillBecomesALookupTable:
    def test_a_status_vocabulary(self):
        v = ["Valid", "Expired", "Expiring soon"]
        e = _synth([("compliance_documents", "status", v * 20),
                    ("vendor_certificates", "status", v * 15)], "status")
        assert e is not None and e["distinct_count"] == 3

    def test_a_currency_vocabulary(self):
        e = _synth([("contracts", "currency", ["GBP"] * 13),
                    ("work_orders", "currency", ["GBP", "AED"] * 40)], "currency")
        assert e is not None

    def test_a_multi_word_vocabulary_is_not_mistaken_for_free_text(self):
        """Document types repeat heavily; spaces in the values are not the test."""
        v = ["Gas Safety Record", "LOLER Thorough Examination", "Fire Risk Assessment"]
        e = _synth([("compliance_documents", "document_type", v * 12),
                    ("ppm_schedules", "task_type", v * 9)], "document_type")
        assert e is not None and e["distinct_count"] == 3

    def test_an_asset_category(self):
        v = ["Chiller", "AHU", "FCU", "Pump", "Lift"]
        e = _synth([("assets", "category", v * 25), ("work_orders", "discipline", v * 30)],
                   "category")
        assert e is not None


class TestAPersonCanStillAskForOne:
    """Re-classifying an FK to a shared attribute at the gate is an instruction, not a guess."""

    def test_near_unique_codes_are_built_when_asked_for(self):
        codes = [f"ALM-{i:05d}" for i in range(769)]
        e = _synth([("bms_alarms", "alarm_id", codes),
                    ("work_orders", "linked_alarm_id", codes[:119])],
                   "alarm_id", vocabulary_only=False)
        assert e is not None and e["table_name"] == "alarm_id"
        assert e["distinct_count"] == 769

    def test_dates_too_when_asked_for(self):
        d = ["2009-11-02", "2011-06-01", "2011-10-01"]
        assert _synth([("assets", "install_date", d * 8), ("meters", "install_date", d * 4)],
                      "install_date", vocabulary_only=False) is not None

    def test_but_a_yes_no_flag_is_refused_on_both_paths(self):
        for kw in ({}, {"vocabulary_only": False}):
            assert _synth([("ppm_schedules", "statutory", ["True", "False"] * 10),
                           ("bms_alarms", "nuisance", ["True", "False"] * 10)], "flag", **kw) is None

    def test_the_gate_override_path_does_not_enforce_the_vocabulary_rules(self):
        """apply_classification_decisions builds on a person's instruction."""
        from udr.column_intelligence import apply_classification_decisions
        codes = [f"ALM-{i:05d}" for i in range(50)]
        tables = {
            "bms_alarms": {"rows": [{"alarm_id": v} for v in codes], "columns": ["alarm_id"]},
            "work_orders": {"rows": [{"linked_alarm_id": v} for v in codes[:9]],
                            "columns": ["linked_alarm_id"]},
        }
        ci = {"classification": [{
            "group_id": "G25", "canonical_name": "alarm_id",
            "members": ["bms_alarms.alarm_id", "work_orders.linked_alarm_id"],
            "has_pk": "bms_alarms.alarm_id", "canonical_has_pk": "bms_alarms.alarm_id",
            "fk_column": None, "ri": 1.0, "verdict": "Foreign Key relationship"}],
            "shared_attribute_tables": [], "fk_candidates": [], "summary": {}}
        out = apply_classification_decisions(ci, tables, overrides={"G25": "shared"})
        assert out["lookup_tables_added"] == ["alarm_id"], (
            "the person asked for this table; the automatic vocabulary rules must not veto it")


class TestAnAlternateKeyIsNotAVocabulary:
    """vendor_certificates.vendor_name beside vendors.trading_name, 13 values over 78 rows.

    It repeats, so the ratio test keeps it — but inside `vendors` every value appears exactly
    once. A column that identifies the rows of its own table is that entity's alternate key, and
    a column elsewhere holding the same values is a reference to the entity, not a vocabulary
    shared with it. The certificates already carry vendor_id; the name is a denormalised copy,
    and normalising it builds a second table of vendor names beside `vendors`.
    """

    def _tables(self, parent_rows, child_rows):
        return {
            "vendors": {"rows": [{"trading_name": v} for v in parent_rows],
                        "columns": ["trading_name"]},
            "vendor_certificates": {"rows": [{"vendor_name": v} for v in child_rows],
                                    "columns": ["vendor_name"]},
        }

    def _synth(self, parent_rows, child_rows, **kw):
        return synthesize_shared_attribute_table(
            self._tables(parent_rows, child_rows), canonical_name="vendor_name", group_id="G16",
            member_pairs=[("vendors", "trading_name"),
                          ("vendor_certificates", "vendor_name")], **kw)

    def test_a_name_unique_within_its_own_table_is_refused(self):
        names = ["Mitie", "Otis", "Daikin Applied UK", "Hamworthy Heating",
                 "Chubb Fire & Security", "Trend Control Systems", "Cummins", "Eaton",
                 "Schneider Electric", "Grundfos", "Veolia Water Technologies", "EMCOR UK",
                 "Securitas Technology"]
        assert self._synth(names, names * 5) is None

    def test_the_same_values_repeating_in_the_parent_are_a_vocabulary(self):
        """If the parent repeats them too, nothing there identifies a row — it is a real list."""
        v = ["Mechanical", "Electrical", "Fabric"]
        e = self._synth(v * 9, v * 20)
        assert e is not None and e["distinct_count"] == 3

    def test_a_person_can_still_ask_for_it(self):
        names = [f"Vendor {i}" for i in range(13)]
        assert self._synth(names, names * 5, vocabulary_only=False) is not None

    def test_a_tiny_table_is_not_judged_an_alternate_key(self):
        """Two or three rows are unique by accident, not by design."""
        e = self._synth(["GBP", "AED"], ["GBP"] * 40)
        assert e is not None
