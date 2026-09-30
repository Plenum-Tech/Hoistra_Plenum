"""EMAIL_PROVIDER=smtp sends the connector's mail through the SMTP mailbox, never Graph."""
import asyncio

from src.integrations import smtp_sender
from src.integrations.outlook_connector import OutlookConnector


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None, **kw):
        self.host, self.port, self.calls = host, port, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, pw):
        self.calls.append(("login", user))

    def send_message(self, msg, to_addrs=None):
        FakeSMTP.sent.append((self.host, self.port, self.calls, msg["From"], to_addrs, msg["Subject"], msg.get_content_type()))


def test_smtp_provider_sends_without_touching_graph(monkeypatch):
    s = smtp_sender.settings
    for k, v in dict(email_provider="smtp", smtp_host="smtp.office365.com", smtp_port=587, smtp_user="admin@hoistra.ai",
                     smtp_password="pw", smtp_from="", smtp_use_tls=True, smtp_use_ssl=False).items():
        monkeypatch.setattr(s, k, v)
    monkeypatch.setattr(smtp_sender.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.sent = []
    conn = OutlookConnector("tenant", "client", "secret", "admin@hoistra.ai")

    async def no_graph():
        raise AssertionError("Graph must not be called when EMAIL_PROVIDER=smtp")
    monkeypatch.setattr(conn, "_headers", no_graph)

    asyncio.run(conn.send_email("a@x.com, b@y.com", "WO-123 approved", "<p>ok</p>", reply_to_id="AAMk", is_html=True))
    host, port, calls, sender, rcpt, subject, ctype = FakeSMTP.sent[0]
    assert (host, port) == ("smtp.office365.com", 587)
    assert calls[:2] == ["ehlo", "starttls"] and ("login", "admin@hoistra.ai") in calls
    assert sender == "admin@hoistra.ai" and rcpt == ["a@x.com", "b@y.com"] and subject == "WO-123 approved"
    assert ctype == "multipart/alternative"


def test_auto_leaves_the_graph_path(monkeypatch):
    monkeypatch.setattr(smtp_sender.settings, "email_provider", "auto")
    assert smtp_sender.smtp_selected() is False
