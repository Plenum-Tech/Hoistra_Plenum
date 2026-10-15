"""EMAIL_PROVIDER picks the transport; a forced one that is not configured sends nothing."""
import pytest

from src.shared import email_transport as et


@pytest.fixture
def both(monkeypatch):
    s = et.settings
    for k, v in dict(azure_tenant_id="t", azure_client_id="c", azure_client_secret="x", outlook_user_mail="admin@hoistra.ai",
                     smtp_host="smtp.office365.com", smtp_user="admin@hoistra.ai", smtp_password="pw").items():
        monkeypatch.setattr(s, k, v, raising=False)
    return s


@pytest.mark.parametrize("choice,expected", [("auto", "graph"), ("graph", "graph"), ("smtp", "smtp"), ("SMTP ", "smtp")])
def test_the_choice_with_both_configured(both, monkeypatch, choice, expected):
    monkeypatch.setattr(both, "email_provider", choice)
    assert et.email_transport() == expected


def test_a_forced_transport_that_is_not_configured_is_none(both, monkeypatch):
    monkeypatch.setattr(both, "email_provider", "smtp")
    monkeypatch.setattr(both, "smtp_password", "")
    assert et.email_transport() == "none"          # not quietly sent through Graph instead


def test_auto_falls_back_to_smtp_without_graph(both, monkeypatch):
    monkeypatch.setattr(both, "email_provider", "auto")
    monkeypatch.setattr(both, "azure_client_secret", "")
    assert et.email_transport() == "smtp"
