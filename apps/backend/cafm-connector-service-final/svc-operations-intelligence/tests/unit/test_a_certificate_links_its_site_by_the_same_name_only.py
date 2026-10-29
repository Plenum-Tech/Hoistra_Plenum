"""A certificate links the site it names in any exact form — and never a site it does not (29 Oct 2026).

resolve_site_link files a certificate against a site at ingest; backfill_site_links does it for the
register. Both LINK, so neither may match by part of a name or a close spelling. But the same name
written another way is the same site: "JASMEET BUILDING" is the site "JASMEET", "B090765" is the site
coded "B-090765". Before, ingest read only sites whose name contained the certificate's phrase in
order (ILIKE '%JASMEET BUILDING%') or equalled it, so it never even saw "JASMEET" or "RT5, Crossrail";
and the containment tier both use filed a certificate for "Building 7" under "Building 70".
"""
from __future__ import annotations

import re

import pytest

from src.engines.compliance import site_links
from src.engines.compliance.site_links import (
    as_uuid,
    choose_site_match,
    normalize_name,
    normalize_ref,
)


def _site(key, *names, codes=()):
    u = as_uuid(key)
    return {
        "key": str(u) if u else key, "site_uuid": u, "site_ref": None if u else key,
        "label": names[0] if names else key,
        "name_keys": {normalize_name(n) for n in names if normalize_name(n)},
        "code_keys": {normalize_ref(c) for c in (*codes, key) if normalize_ref(c)},
    }


JASMEET = _site("S-JAS", "JASMEET", codes=("B-090765",))
HEIGHTS = _site("S-HTS", "Jasmeet Heights")
HUSSAIN = _site("S-HUS", "Hussain Tower")
B70 = _site("S-B70", "Building 70")
MILL = _site("S-MILL", "Mill")
SITES = [JASMEET, HEIGHTS, HUSSAIN, B70, MILL]


def test_a_code_written_as_the_building_name_links_its_site():
    match, reason = choose_site_match(SITES, name="B090765")
    assert match is not None and match["key"] == "S-JAS", reason


def test_the_kind_word_beside_the_name_still_links_the_exact_site():
    """Jasmeet Heights also starts with "jasmeet"; the exact core decides, not a guess."""
    match, reason = choose_site_match([JASMEET, HEIGHTS, _site("S-JB", "Jasmeet Heights East")],
                                      name="Jasmeet Heights Building")
    assert match is not None and match["key"] == "S-HTS", reason


def test_building_7_is_not_filed_under_building_70():
    match, reason = choose_site_match(SITES, name="Building 7")
    assert match is None and reason == "no_match"


def test_no_part_name_no_word_order_no_close_spelling_on_a_link():
    for typed in ("tower hussain", "Jasmet", "Tower Hussain Building"):
        match, reason = choose_site_match(SITES, name=typed)
        assert match is None, (typed, reason)


def test_a_short_core_is_not_enough_to_link():
    """"Mill Building" is not proved to be the site "Mill": a core under the containment length
    links nothing, exactly as containment never did."""
    match, reason = choose_site_match(SITES, name="Mill Building")
    assert match is None and reason == "no_match"


def test_the_kind_of_record_alone_links_nothing():
    for typed in ("the building", "Building", "the site"):
        match, reason = choose_site_match(SITES, name=typed)
        assert match is None, (typed, reason)


def test_two_sites_with_the_same_exact_form_are_ambiguous():
    rows = [_site("S-1", "RT5 Crossrail"), _site("S-2", "RT5, Crossrail")]
    match, reason = choose_site_match(rows, name="RT5  Crossrail")
    assert match is None and reason.startswith("ambiguous")


# ── resolve_site_link: the candidates it reads must include the exact forms ──────────────────

STORED = [("S-JAS", "JASMEET", "B-090765"), ("S-RT5", "RT5, Crossrail", None), ("S-B70", "Building 70", None)]


def _compact(v):
    return re.sub(r"[^a-z0-9]+", "", str(v or "").lower())


class _Sites:
    """Evaluates the WHERE resolve_site_link sends, by its parameters, over STORED."""

    def __init__(self):
        self.sql = []

    def begin_nested(self):
        class _Ctx:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *exc):
                return False

        return _Ctx()

    async def execute(self, stmt, params=None):
        p = params or {}
        self.sql.append(str(stmt))
        rows = []
        for key, name, code in STORED:
            low = name.lower()
            hit = (("like" in p and p["like"].strip("%").lower() in low)
                   or ("exact" in p and p["exact"].strip().lower() == low)
                   or (low in (p.get("texts") or []))
                   or (_compact(name) in (p.get("compacts") or []))
                   or (code and _compact(code) in (p.get("compacts") or []))
                   or (code and "ref" in p and p["ref"].strip().upper() == code.upper()))
            if hit:
                rows.append({"key_site_id": key, "name_site_name": name, "code_site_code": code})

        class _R:
            def mappings(self_inner):
                return self_inner

            def all(self_inner):
                return rows

        return _R()


@pytest.fixture
def shaped(monkeypatch):
    async def _shape(session, refresh=False):
        return {"columns": {"site_id": "character varying", "site_name": "text", "site_code": "text"},
                "key_columns": ["site_id"], "name_columns": ["site_name"], "code_columns": ["site_code", "site_id"],
                "usable": True}

    monkeypatch.setattr(site_links, "sites_shape", _shape)


@pytest.mark.asyncio
@pytest.mark.parametrize("typed, expected", [
    ("JASMEET BUILDING", "S-JAS"),
    ("RT5 Crossrail", "S-RT5"),
    ("B090765", "S-JAS"),
])
async def test_ingest_reads_and_links_the_site_in_any_exact_form(shaped, typed, expected):
    link = await site_links.resolve_site_link(_Sites(), name=typed)
    assert link is not None and link.site_ref == expected, typed


@pytest.mark.asyncio
@pytest.mark.parametrize("typed", ["Building 7", "Jasmet", "the building"])
async def test_ingest_links_nothing_it_is_not_sure_of(shaped, typed):
    assert await site_links.resolve_site_link(_Sites(), name=typed) is None
