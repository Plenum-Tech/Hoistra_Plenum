"""A record is found however its name is typed (29 Oct 2026, "fix this permanently, no edge cases").

"HOW MANY FLOORS IN JASMEET BUILDING" — the planner passed match "JASMEET BUILDING"; the building is
named "JASMEET". Exact and search both compared the whole phrase, in order, so a kind word the user
added ("building", "site", "tower block"), a different word order, a possessive, a stray comma or a
code typed without its hyphen made a record that is on the register unfindable.

These are the pure pieces of the resolution ladder (agents/name_match.py). Against a real Postgres,
the ladder itself: tests/test_the_resolution_ladder_on_postgres.py.
"""
import pytest

from src.agents import name_match as nm

KIND = {"building", "buildings", "site", "sites"}


@pytest.mark.parametrize("raw,clean", [
    ("JASMEET BUILDING", "jasmeet building"),
    ("  “Bishopsgate Tower”  ", "bishopsgate tower"),
    ("Jasmeet's", "jasmeet"),
    ("Jasmeet’s floors", "jasmeet floors"),
    ("the buildings' floors", "the buildings floors"),
    ("Zürich Haus", "zurich haus"),
    ("RT5,  Crossrail", "rt5, crossrail"),
    ("Boiler 2 — central plant", "boiler 2 - central plant"),
])
def test_text_is_cleaned_the_same_way_every_time(raw, clean):
    assert nm.clean(raw) == clean


@pytest.mark.parametrize("raw,compact", [
    ("B-090765", "b090765"), ("b 090765", "b090765"), ("B090765", "b090765"),
    ("RT5, Crossrail", "rt5crossrail"), ("PT-B-301-A-BOILER-01", "ptb301aboiler01"),
])
def test_a_code_compares_without_its_separators(raw, compact):
    assert nm.compact(raw) == compact


@pytest.mark.parametrize("raw,kept", [
    ("JASMEET BUILDING", ["jasmeet"]),
    ("the Jasmeet building", ["jasmeet"]),
    ("building JASMEET", ["jasmeet"]),
    ("Bishopsgate Tower site", ["bishopsgate", "tower"]),
    ("details for Jasmeet", ["jasmeet"]),
    ("the building", []),                       # nothing but kind words: names nothing
    ("Building 7", ["7"]),
    ("Tower A", ["tower", "a"]),                # a letter after a name is the name, not an article
    ("Block A building", ["block", "a"]),
    ("show me a building called Jasmeet", ["jasmeet"]),
    ("a building called Jasmeet", ["jasmeet"]),
])
def test_kind_words_and_fillers_are_not_part_of_a_name(raw, kept):
    assert nm.meaningful(raw, KIND) == kept


def test_the_ladder_tries_the_text_as_typed_before_anything_looser():
    variants = nm.phrase_variants("JASMEET BUILDING", KIND)
    assert variants == ["JASMEET BUILDING", "jasmeet"]
    assert nm.phrase_variants("Jasmeet", KIND) == ["Jasmeet"]
    assert nm.phrase_variants("the building", KIND) == ["the building"]   # never an empty search


@pytest.mark.parametrize("query,stored,ok", [
    ("Jasmet", "JASMEET", True),                 # one letter missing
    ("Hussian Tower", "hussain tower", True),    # transposition
    ("Bishopgate Tower", "Bishopsgate Tower", True),
    ("Manchster Town Hall", "Manchester Town Hall", True),
    ("Jasmeet", "Vasiliki House", False),
    ("B-301", "B-302", False),                   # a code is never "close enough"
    ("B-301", "B-3010", False),
    ("RT5 Crosrail", "RT5, Crossrail", True),    # the code part exact, the word part close
    ("RT6 Crossrail", "RT5, Crossrail", False),
])
def test_close_spelling_is_accepted_only_for_words_never_for_codes(query, stored, ok):
    assert (nm.similarity(query, stored) >= nm.FUZZY_MIN) is ok


def test_the_closest_spelling_wins_only_when_it_is_clearly_closest():
    best = nm.pick_closest("Jasmet", [("b1", "JASMEET"), ("b2", "Vasiliki House")])
    assert [k for k, _ in best] == ["b1"]
    tie = nm.pick_closest("Tower A", [("t1", "Tower B"), ("t2", "Tower C")])
    assert len(tie) != 1                          # two equally close: never silently one of them
    assert nm.pick_closest("zzzz", [("b1", "JASMEET")]) == []


def test_fragments_for_the_close_spelling_sweep_cover_both_ends_of_each_word():
    assert set(nm.fragments("Jasmet")) >= {"jas", "met"}
    assert nm.fragments("7") == []


# ── the same ladder for names compared in Python (tool filters over rows a service returned) ────

ROWS = [("b1", "JASMEET"), ("b2", "Jasmeet Heights"), ("b3", "hussain tower"), ("b4", "Bishopsgate Tower"),
        ("b5", "RT5, Crossrail"), ("b6", "Building 7"), ("b7", "Building 70"), ("b8", "Manchester Town Hall"),
        ("b9", "sams tower")]


@pytest.mark.parametrize("typed,keys", [
    ("JASMEET BUILDING", ["b1"]), ("jasmeet", ["b1"]), ("Jasmeet Heights", ["b2"]),
    ("tower hussain", ["b3"]), ("hussian tower", ["b3"]), ("Bishopsgate", ["b4"]),
    ("RT5 Crossrail", ["b5"]), ("Building 7", ["b6"]), ("town hall", ["b8"]),
    ("tower", ["b3", "b4", "b9"]), ("the building", []), ("Kensington Palace", []), ("B-1", []),
])
def test_rows_are_ranked_by_the_same_ladder(typed, keys):
    assert sorted(k for k, _ in nm.best_matches(typed, ROWS, KIND)) == sorted(keys)


@pytest.mark.parametrize("typed,stored,ok", [
    ("Jasmeet Building", "JASMEET", True), ("tower hussain", "hussain tower", True),
    ("Jasmet", "JASMEET", True), ("the building", "JASMEET", False), ("Tower A", "Tower B", False),
    ("Acme", "Acme Facilities Ltd", True), ("B-301", "B-3010", False),
])
def test_one_name_against_one_stored_name(typed, stored, ok):
    assert nm.names_match(typed, stored, KIND) is ok


# ── found by the groups that moved every site onto the ladder (29 Oct 2026) ─────────────────────

def test_a_phrase_of_kind_words_only_matches_only_a_record_named_exactly_that():
    assert nm.rung("the building", "The Building Centre", KIND) is None
    assert nm.rung("the building", "The Building", KIND) == 0
    assert nm.best_matches("the building", [("x", "The Building Centre")], KIND) == []


def test_dotted_initials_read_as_one_word():
    assert nm.clean("R.D.M. Electrical") == "rdm electrical"
    assert nm.compact("R.D.M.") == "rdm"


@pytest.mark.parametrize("query,stored,ok", [
    ("Acme Facilites", "Acme Facilities Ltd", True),        # the typed words against the same words
    ("Kelley", "Gough and Kelly Ltd", True),
    ("R.D.M. Electrcal", "RDM Electrical Services Ltd", True),
    ("Halt", "Manchester Town Hall", False),
    ("Jasmeet", "Vasiliki House", False),
])
def test_close_spelling_compares_the_typed_words_with_the_same_words_of_the_name(query, stored, ok):
    assert (nm.similarity(query, stored) >= nm.FUZZY_MIN) is ok


def test_a_whole_name_close_spelling_still_beats_part_of_a_longer_name():
    assert [k for k, _ in nm.pick_closest("Jasmet", [("b1", "JASMEET"), ("b2", "Jasmeet Heights")])] == ["b1"]
