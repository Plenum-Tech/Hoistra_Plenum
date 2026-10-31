"""Storing a register link on a certificate — and the two things it must never do."""
from __future__ import annotations

import asyncio
from typing import Any

from src.engines.compliance import verification as V


class _Cert:
    def __init__(self, meta: dict[str, Any] | None = None):
        self.raw_metadata = meta or {}
        self.updated_at = None


class _Session:
    def __init__(self, cert):
        self._cert = cert
        self.committed = False

    async def get(self, model, pk):
        return self._cert

    async def commit(self):
        self.committed = True

    async def rollback(self):
        pass


LINK = {
    "register_url": "https://www.sia.homeoffice.gov.uk/Pages/acs-rosta.aspx",
    "verification_url": "https://www.sia.homeoffice.gov.uk/Pages/acs-rosta.aspx?q=12345",
    "issuing_body": "Security Industry Authority (SIA)",
    "prefills_number": True,
}


def _persist(cert, link=None):
    s = _Session(cert)
    ok = asyncio.run(V.persist_register_link(s, "any-id", link or LINK))
    return ok, s


def test_the_link_lands_on_the_certificate():
    cert = _Cert()
    ok, s = _persist(cert)
    v = cert.raw_metadata["verification"]
    assert ok is True and s.committed is True
    assert v["register_url"] == LINK["register_url"]
    assert v["verification_url"] == LINK["verification_url"]
    assert v["issuing_body"] == "Security Industry Authority (SIA)"
    assert v["link_built_at"], "when the link was built travels with it"


def test_it_commits_rather_than_only_flushing():
    """verify-now is otherwise a read-only endpoint whose session nobody commits. A flush
    alone reported the link stored and wrote nothing — it came back marked stored and was
    gone on the next request."""
    _, s = _persist(_Cert())
    assert s.committed is True


# ── a link is not a verification ─────────────────────────────────────────────


def test_storing_a_link_never_marks_a_certificate_verified():
    """A link to a register says where to look. It does not say anyone looked, and a
    certificate that has only ever had a link built must not read as accredited."""
    cert = _Cert({"verification": {"verified": False, "status": "dump_miss"}})
    _persist(cert)
    v = cert.raw_metadata["verification"]
    assert v["verified"] is False, "untouched"
    assert v["status"] == "dump_miss", "untouched"
    assert v["verification_url"], "the link is there alongside"


def test_a_certificate_with_no_verification_at_all_does_not_gain_one():
    cert = _Cert()
    _persist(cert)
    v = cert.raw_metadata["verification"]
    assert "verified" not in v and "status" not in v


# ── merging, both ways ───────────────────────────────────────────────────────


def test_the_link_is_merged_onto_an_existing_verification_not_over_it():
    """Replacing the block would erase the evidence that the vendor was verified, while the
    status line kept rendering — a loss nobody would see."""
    cert = _Cert({"verification": {
        "verified": True, "status": "verified", "channel": "register_bot",
        "checked_at": "2026-09-08T12:00:00+00:00",
        "evidence": {"match_confidence": 1.0},
    }})
    _persist(cert)
    v = cert.raw_metadata["verification"]
    assert v["verified"] is True
    assert v["channel"] == "register_bot"
    assert v["checked_at"] == "2026-09-08T12:00:00+00:00"
    assert v["evidence"] == {"match_confidence": 1.0}
    assert v["register_url"] == LINK["register_url"]


def test_other_metadata_on_the_certificate_is_left_alone():
    cert = _Cert({"forensics": {"verdict": "pass"}, "answered_fields": {"x": 1}})
    _persist(cert)
    assert cert.raw_metadata["forensics"] == {"verdict": "pass"}
    assert cert.raw_metadata["answered_fields"] == {"x": 1}


def test_a_missing_certificate_is_reported_not_raised():
    class _Empty(_Session):
        async def get(self, model, pk):
            return None

    assert asyncio.run(V.persist_register_link(_Empty(None), "gone", LINK)) is False


def test_a_write_that_fails_says_so_rather_than_claiming_success():
    class _Broken(_Session):
        async def commit(self):
            raise RuntimeError("connection lost")

    assert asyncio.run(V.persist_register_link(_Broken(_Cert()), "id", LINK)) is False
