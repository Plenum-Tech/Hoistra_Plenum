"""One document, every domain — and a domain that was not found says why.

The ingest pipeline classified a document from its FILENAME and the user's chat message,
picked one winner, and ran one extractor. An FM contract names the supplier, the plant it
covers, the PPM frequency it commits to and the certificates the contractor must hold. Four
domains in one file, three of them never read — and nothing said so, because the file was
indexed, the page said "ingested", and the facts were simply absent.
"""
from src.engines.ingestion import document_facts as df

FM_CONTRACT = """
    MASTER SERVICES AGREEMENT
    Apex Mechanical Services Ltd · Company registration 08841122 · VAT registration GB1845521
    Contract period 01 April 2026 to 31 March 2029. Contract value GBP 412,000.
    Service level agreement: 4 hour response, service credits apply.
    Planned preventative maintenance: quarterly service visits to all chiller plant.
    Equipment covered: CHILLER-101, model 30XA, serial number 3311-A9.
    The contractor shall hold a valid Gas Safe certificate; EICR at five yearly intervals.
"""

ENERGY_BILL = """
    ELECTRICITY BILL · MPAN 12 3456 7890 123 · half hourly
    Billing period 01 August 2026 to 31 August 2026
    Consumption 184,220 kWh · day rate 26.10 p/kWh · standing charge GBP 412.00
"""

LEASE = """
    This lease is made between the landlord and the tenant for the demised premises on the
    third floor, for a term of ten years, subject to the rent review provisions.
"""


class TestOneDocumentReachesEveryDomain:

    def test_an_fm_contract_is_four_domains_not_one(self):
        plan = df.extraction_plan(FM_CONTRACT)
        assert set(plan["domains_present"]) == {"compliance", "vendors", "assets", "maintenance"}

    def test_the_domains_it_does_not_mention_are_still_reported(self):
        """Absent is a finding. Omitting it makes "not checked" look like "checked, clean"."""
        plan = df.extraction_plan(FM_CONTRACT)
        assert "energy" in plan["domains_absent"]
        assert plan["detection"]["energy"]["present"] is False
        assert plan["detection"]["energy"]["why"]

    def test_every_present_domain_brings_its_own_field_list(self):
        plan = df.extraction_plan(FM_CONTRACT)
        assert plan["fields_total"] > 40
        assert "vendor_name" in plan["plan"]["vendors"]["fields"]
        assert "serial_number" in plan["plan"]["assets"]["fields"]
        assert "frequency" in plan["plan"]["maintenance"]["fields"]
        assert "expiry_date" in plan["plan"]["compliance"]["fields"]


class TestDetectionDiscriminates:

    def test_a_bill_is_energy_and_nothing_else(self):
        plan = df.extraction_plan(ENERGY_BILL)
        assert plan["domains_present"] == ["energy"]

    def test_a_document_of_no_interest_claims_nothing(self):
        """A lease is a real document about none of these five. Claiming a domain here would
        send a person to look for facts that were never in the file."""
        assert df.extraction_plan(LEASE)["domains_present"] == []

    def test_a_single_weak_word_is_not_evidence(self):
        det = df.detect("The engineer visited.")
        assert all(not v["present"] for v in det.values())

    def test_one_strong_signal_is_enough_and_says_which(self):
        det = df.detect("MPAN 12 3456 7890 123")
        assert det["energy"]["present"] is True
        assert "mpan" in det["energy"]["matched_strong"]

    def test_signals_match_on_word_boundaries(self):
        """'ppm' must not fire inside 'ppmv', which is a concentration, not maintenance."""
        det = df.detect("Refrigerant leak detection at 5 ppmv across the plant room.")
        assert "ppm" not in det["maintenance"]["matched_strong"]

    def test_detection_survives_an_empty_or_missing_document(self):
        for doc in (None, "", "   "):
            det = df.detect(doc)
            assert all(not v["present"] for v in det.values())
            assert all(v["why"] for v in det.values())


class TestTheCatalogueIsUsableAsData:

    def test_all_five_domains_are_declared(self):
        keys = {d["domain"] for d in df.domains()["domains"]}
        assert keys == {"compliance", "vendors", "energy", "assets", "maintenance"}

    def test_a_domain_says_whether_anything_can_write_it_yet(self):
        """Three domains have no extractor behind them. The catalogue states that rather
        than implying a fact will be stored when it will only be shown."""
        by = {d["domain"]: d for d in df.domains()["domains"]}
        assert by["compliance"]["writes"] is True
        assert by["vendors"]["writes"] is True
        for d in ("energy", "assets", "maintenance"):
            assert by[d]["writes"] is False, d
            assert by[d]["handler_note"]

    def test_every_field_carries_the_question_that_fetches_it(self):
        for d in df.domains()["domains"]:
            for f in d["fields"]:
                assert f["question"].strip(), f"{d['domain']}.{f['name']}"
                assert f["type"] in {"text", "date", "number", "money", "boolean"}


class TestNothingIsWrittenOnThinEvidence:

    def test_facts_split_by_what_may_be_written(self):
        out = df.grade({
            "vendor_name": {"value": "Apex Mechanical", "confidence": 0.95},
            "contract_value": {"value": 412000, "confidence": 0.7},
            "visits_per_year": {"value": 4, "confidence": 0.2},
        })
        assert list(out["accept"]) == ["vendor_name"]
        assert list(out["review"]) == ["contract_value"]
        assert list(out["discard"]) == ["visits_per_year"]

    def test_a_fact_with_no_confidence_goes_to_review_not_to_accept(self):
        """Unknown is not high. Treating it as high is how an unmeasured figure becomes a row."""
        out = df.grade({"expiry_date": {"value": "2027-03-01", "confidence": None}})
        assert list(out["review"]) == ["expiry_date"]
        assert out["accept"] == {}

    def test_empty_values_are_not_facts_at_any_confidence(self):
        out = df.grade({"issuer": {"value": "", "confidence": 0.99},
                        "result": {"value": None, "confidence": 0.99}})
        assert out["counts"] == {"accept": 0, "review": 0, "discard": 0}

    def test_a_bare_value_is_accepted_as_a_fact_of_unknown_confidence(self):
        out = df.grade({"issuer": "NICEIC"})
        assert list(out["review"]) == ["issuer"]
