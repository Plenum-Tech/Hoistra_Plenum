"""A question asked straight after an upload is about the upload, not the register."""

from src.agents.orchestrator import DeepAgentOrchestrator as _O
from src.agents import session_workspace as sw

SID = "scope-test-session"


def _seed(ids):
    sw.get_session_state(SID).pop("last_ingested_certificate_ids", None)
    sw.record_ingested_certificates(SID, ids)


def test_no_ingestion_means_no_pin():
    sw.get_session_state(SID).pop("last_ingested_certificate_ids", None)
    assert _O._ingestion_scope(SID, "which certificates are lapsed?") == []


def test_follow_up_after_an_upload_is_pinned_to_it():
    _seed(["c1", "c2", "c3"])
    assert _O._ingestion_scope(SID, "is it expired?") == ["c1", "c2", "c3"]


def test_a_question_that_widens_scope_releases_the_pin():
    _seed(["c1", "c2"])
    assert _O._ingestion_scope(SID, "show me all certificates that are lapsed") == []
    # and stays released for the rest of the session
    assert _O._ingestion_scope(SID, "is it expired?") == []


def test_asking_about_the_register_releases_the_pin():
    _seed(["c1"])
    assert _O._ingestion_scope(SID, "what does the register look like overall?") == []


def test_empty_ingestion_is_not_recorded():
    sw.get_session_state(SID).pop("last_ingested_certificate_ids", None)
    sw.record_ingested_certificates(SID, [])
    assert _O._ingestion_scope(SID, "is it expired?") == []
