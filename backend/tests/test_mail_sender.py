import socket
from email import message_from_bytes, policy

import pytest
from aiosmtpd.controller import Controller

from backend.config import Settings
from backend.mail.sender import (
    EmailSendError,
    MailpitSender,
    OutgoingEmail,
    build_mime,
    clean_header,
    make_sender,
)

FROM = "taskpilot@acme.example.com"
MESSAGE = OutgoingEmail("ravi.kumar@acme.example.com", "Weekly report", "plain body", "<p>html body</p>", "Jiya Sharma")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Inbox:
    """A tiny SMTP server that records what it receives and can refuse recipients."""

    def __init__(self, refuse: str | None = None):
        self.received: list = []
        self.refuse = refuse

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        if self.refuse:
            return self.refuse
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        self.received.append(envelope)
        return "250 Message accepted"


@pytest.fixture
def smtp():
    def start(refuse=None):
        inbox = Inbox(refuse)
        controller = Controller(inbox, hostname="127.0.0.1", port=free_port())
        controller.start()
        started.append(controller)
        return inbox, controller.port

    started = []
    yield start
    for controller in started:
        controller.stop()


def test_mime_has_both_parts_and_the_sender_name():
    mime = build_mime(MESSAGE, FROM)
    assert mime["To"] == "ravi.kumar@acme.example.com" and mime["Subject"] == "Weekly report"
    assert mime["From"] == "Jiya Sharma via TaskPilot <taskpilot@acme.example.com>"
    assert mime.get_body(("plain",)).get_content().strip() == "plain body"
    assert "html body" in mime.get_body(("html",)).get_content()


def test_header_injection_is_neutralised():
    evil = OutgoingEmail("a@acme.example.com", "Hi\r\nBcc: evil@x.com", "b", "b", "Name\nX-Injected: 1")
    mime = build_mime(evil, FROM)
    raw = mime.as_string()
    assert "\nBcc:" not in raw and "X-Injected:" not in raw.split("\n\n")[0].replace("Name X-Injected: 1", "")
    assert mime["Bcc"] is None
    assert "\n" not in mime["Subject"] and "\r" not in mime["Subject"]


def test_clean_header_flattens_control_characters_and_limits_length():
    assert clean_header("a\r\nb\tc\x00d") == "a b c d"
    assert len(clean_header("x" * 500, 50)) == 50


def test_mailpit_sender_delivers_over_real_smtp(smtp):
    inbox, port = smtp()
    MailpitSender("127.0.0.1", port, FROM).send(MESSAGE)
    [envelope] = inbox.received
    assert envelope.rcpt_tos == ["ravi.kumar@acme.example.com"] and envelope.mail_from == FROM
    received = message_from_bytes(envelope.content, policy=policy.default)
    assert received["Subject"] == "Weekly report"
    assert received.get_body(("plain",)).get_content().strip() == "plain body"


def test_unreachable_server_is_a_retryable_error():
    with pytest.raises(EmailSendError) as info:
        MailpitSender("127.0.0.1", free_port(), FROM, timeout=2).send(MESSAGE)
    assert info.value.retryable is True


def test_permanent_refusal_is_not_retryable(smtp):
    _, port = smtp(refuse="550 5.1.1 no such user")
    with pytest.raises(EmailSendError) as info:
        MailpitSender("127.0.0.1", port, FROM).send(MESSAGE)
    assert info.value.retryable is False and "550" in str(info.value)


def test_temporary_refusal_is_retryable(smtp):
    _, port = smtp(refuse="451 4.3.0 try again later")
    with pytest.raises(EmailSendError) as info:
        MailpitSender("127.0.0.1", port, FROM).send(MESSAGE)
    assert info.value.retryable is True


def test_the_mailpit_backend_is_the_default():
    sender = make_sender(Settings(_env_file=None))
    assert isinstance(sender, MailpitSender) and (sender.host, sender.port) == ("localhost", 1025)


def test_asking_for_gmail_fails_loudly_because_it_is_not_built_yet():
    with pytest.raises(RuntimeError, match="not available yet"):
        make_sender(Settings(_env_file=None, email_backend="gmail"))
