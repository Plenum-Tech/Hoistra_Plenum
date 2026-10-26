"""Expected values: the grammar of the Expected values column (this half), and the check that finds each
value in an answer (grading.check_values, appended by Task 19). 15 Oct 2026. Spec: Golden set §1.3, §4.2."""
from __future__ import annotations

import pytest

from trace_lab import golden


def _one(cell):
    items = golden.parse_expected_values(cell)
    assert len(items) == 1, items
    return items[0]


def test_a_bare_integer_is_an_exact_number():
    it = _one("759")
    assert (it["kind"], it["value"], it["tol_abs"], it["tol_pct"], it["negate"]) == ("number", 759, 0, None, False)


def test_a_label_is_kept_for_messages_only():
    it = _one("floors: 22")
    assert (it["label"], it["kind"], it["value"], it["raw"]) == ("floors", "number", 22, "floors: 22")


def test_a_percent_takes_half_its_last_decimal_place():
    it = _one("79.4%")
    assert (it["kind"], it["value"], it["tol_abs"]) == ("percent", 79.4, 0.05)


def test_money_with_an_explicit_percent_tolerance_and_multipliers():
    it = _one("£463,000 ±2%")
    assert (it["kind"], it["value"], it["currency"], it["tol_abs"], it["tol_pct"]) == ("money", 463000, "GBP", 0, 2)
    assert _one("$1.2m")["value"] == 1200000
    assert _one("£463k")["value"] == 463000
    plus_minus = _one("AED 5,000 +/- 100")
    assert (plus_minus["currency"], plus_minus["value"], plus_minus["tol_abs"]) == ("AED", 5000, 100)


def test_a_unit_after_a_number_is_kept_but_not_required():
    it = _one("717,338 kWh")
    assert (it["kind"], it["value"], it["unit"]) == ("number", 717338, "kWh")
    assert _one("49,400 m²")["unit"] == "m²"          # m is a unit here, not millions: no currency


@pytest.mark.parametrize("cell", ["18 October 2026", "18 Oct 2026", "October 18, 2026", "18/10/2026",
                                  "2026-10-18", "18th October 2026"])
def test_dates_in_every_accepted_form(cell):
    it = _one(cell)
    assert (it["kind"], it["value"]) == ("date", "2026-10-18")


def test_quoted_text_is_a_name_and_code_is_forced():
    it = _one('"Ostley Power Services"')
    assert (it["kind"], it["value"]) == ("name", "Ostley Power Services")
    it = _one("code:FRA-B-301-2025")
    assert (it["kind"], it["value"]) == ("code", "FRA-B-301-2025")


def test_a_negated_name_must_not_appear():
    it = _one("!Pennard Fire Services")
    assert (it["kind"], it["value"], it["negate"]) == ("name", "Pennard Fire Services", True)


def test_yes_and_no():
    assert (_one("yes")["kind"], _one("yes")["value"]) == ("yesno", "yes")
    assert _one("No")["value"] == "no"


def test_approximately_means_two_percent():
    it = _one("~120")
    assert (it["kind"], it["value"], it["tol_abs"], it["tol_pct"]) == ("number", 120, 0, 2)


def test_items_split_on_semicolons_and_lines_but_not_inside_quotes():
    items = golden.parse_expected_values('27; 11\n"Apex; Lifts"')
    assert [i["value"] for i in items] == [27, 11, "Apex; Lifts"]


@pytest.mark.parametrize("cell", ["number:abc", '"Ostley" ±2%', "date:31/02/2026", "~Ostley", "!"])
def test_an_unreadable_item_names_itself(cell):
    with pytest.raises(ValueError) as err:
        golden.parse_expected_values(cell)
    assert cell.strip() in str(err.value)


def test_at_most_forty_items():
    with pytest.raises(ValueError):
        golden.parse_expected_values("; ".join(str(i) for i in range(41)))


def test_parse_date_text_needs_the_whole_cell_to_be_a_date():
    assert golden.parse_date_text("13/10/2026").isoformat() == "2026-10-13"
    assert golden.parse_date_text("2026-10-13T09:30:00Z").isoformat() == "2026-10-13"
    assert golden.parse_date_text("on 13/10/2026") is None
    assert golden.parse_date_text("31/02/2026") is None


# ── the check half (Task 19): finding each value in the answer and its cards (spec §4.2) ──

from trace_lab import grading  # noqa: E402


def _check(cell, answer, cards=None):
    return grading.check_values(golden.parse_expected_values(cell), answer, cards or [])


@pytest.mark.parametrize("cell,answer,ok", [
    ("759", "There were 759 work orders.", True),
    ("759", "There were 758 work orders.", False),
    ("759", "There were 7,590 work orders.", False),
    ("floors: 22", "Bishopsgate Tower has twenty-two floors.", True),
    ("79.4%", "Compliance is 79.4 per cent.", True),
    ("79.4%", "Compliance is 79.4.", False),
    ("£463,000 ±2%", "The contracts total £463k.", True),
    ("£463,000 ±2%", "The contracts total £460,000.", True),
    ("£463,000 ±2%", "The contracts total £400,000.", False),
    ("717,338 kWh", "Consumption was 717338 last year.", True),
    ("18 October 2026", "Due on 18/10/2026.", True),
    ("18 October 2026", "Due on 2026-10-18.", True),
    ("18 October 2026", "Due on 18 October.", False),
    ('"Ostley Power Services"', "It is maintained by OSTLEY power-services.", True),
    ("code:FRA-B-301-2025", "The last FRA (FRA-B-301-2025, by ProudCastle Fire) found breaches.", True),
    ("code:FRA-B-301-2025", "The last FRA was FRA-B-301-2024.", False),
    ("!Pennard Fire Services", "Blocked: SafeLift and ProudCastle Fire.", True),
    ("!Pennard Fire Services", "Blocked: Pennard Fire Services.", False),
    ("yes", "Yes. CP17-B-301-2026 passed.", True),
    ("yes", "No, it lapsed.", False),
    ('"Nine Elms Plant"', "Maintained by Nine Elms Plant.", True),
    ("code:TEN-01", "Ref TEN-01 open.", True),
])
def test_the_value_table(cell, answer, ok):
    det = _check(cell, answer)
    assert det["items"][0]["ok"] is ok, det


def test_cards_count_and_ids_never_match():
    cards = [{"id": "759", "building_id": "22", "kpis": [{"label": "Work orders", "value": 759}], "url": "/x/22"}]
    assert _check("759", "See the cards.", cards)["status"] == "all_found"
    assert _check("22", "See the cards.", [{"id": "22", "row_id": 22, "href": "22"}])["status"] == "mismatch"


def test_flatten_cards_skips_ids_icons_actions_and_links():
    flat = grading.flatten_cards([{"id": "a", "x_id": "b", "icon": "c", "action": "d", "url": "e", "href": "f",
                                   "title": "Boilers", "n": 3, "ok": True, "nested": [{"label": "fine"}]}])
    assert flat.split("\n") == ["Boilers", "3", "fine"]


def test_statuses_evidence_and_nearest():
    det = _check("759; 22", "759 work orders across 22 floors")
    assert det["status"] == "all_found" and "759 work orders" in det["items"][0]["evidence"]
    det = _check("759", "758 work orders")
    assert det["status"] == "mismatch" and det["items"][0]["nearest"] == "758"
    assert _check("yes", "It depends on the building.")["status"] == "unclear"
    assert grading.check_values([], "anything", [])["status"] == "none"


def test_number_words_become_digits():
    assert grading.words_to_digits("twenty-two floors and seven lifts, ninety nine") == "22 floors and 7 lifts, 99"
