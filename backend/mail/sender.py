"""The email seam. The app only ever talks to `EmailSender`; which implementation sits
behind it is configuration. Tests and CI use a fake or a local SMTP catcher and never
a real mailbox.

Only the local-inbox sender exists so far (Mailpit, or any SMTP catcher on the same
port). Real Gmail sending is a later, separate step: asking for it today fails loudly
instead of silently doing nothing."""

import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from functools import lru_cache
from typing import Protocol

from backend.config import Settings, get_settings

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def clean_header(value: str, limit: int = 200) -> str:
    """One safe line for a header: no CR/LF (header injection), no control characters."""
    return _CONTROL.sub(" ", value).strip()[:limit]


class EmailSendError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str
    from_name: str  # shown as "<name> via TaskPilot"


class EmailSender(Protocol):
    def send(self, message: OutgoingEmail) -> None:
        """Deliver or raise EmailSendError (retryable says whether trying again can help)."""


def build_mime(message: OutgoingEmail, mail_from: str) -> EmailMessage:
    mime = EmailMessage()
    mime["From"] = formataddr((clean_header(f"{message.from_name} via TaskPilot", 100), mail_from))
    mime["To"] = clean_header(message.to, 254)
    mime["Subject"] = clean_header(message.subject, 200)
    mime.set_content(message.text)
    mime.add_alternative(message.html, subtype="html")
    return mime


class MailpitSender:
    """Plain SMTP to a local inbox (Mailpit in docker, or scripts/dev_mailbox.py). This uses
    the standard library's blocking smtplib rather than aiosmtplib: the app sends from
    ordinary threads (request handlers and the scheduler), so async buys nothing here."""

    def __init__(self, host: str, port: int, mail_from: str, timeout: float = 10):
        self.host, self.port, self.mail_from, self.timeout = host, port, mail_from, timeout

    def send(self, message: OutgoingEmail) -> None:
        mime = build_mime(message, self.mail_from)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
                smtp.send_message(mime)
        except smtplib.SMTPRecipientsRefused as exc:
            # Not a response exception: the per-recipient codes live in .recipients.
            codes = [code for code, _ in exc.recipients.values()]
            raise EmailSendError(f"Recipient refused ({', '.join(map(str, codes))})", retryable=all(c < 500 for c in codes)) from exc
        except smtplib.SMTPResponseException as exc:
            # 4xx means "try later"; 5xx means the server refused it for good.
            raise EmailSendError(f"SMTP {exc.smtp_code}: {exc.smtp_error!r}"[:200], retryable=exc.smtp_code < 500) from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailSendError(f"{type(exc).__name__}: {exc}"[:200]) from exc


def make_sender(settings: Settings) -> EmailSender:
    if settings.email_backend == "mailpit":
        return MailpitSender(settings.mailpit_host, settings.mailpit_port, settings.mail_from)
    raise RuntimeError(
        "EMAIL_BACKEND=gmail is not available yet: real Gmail sending has not been built. "
        "Use EMAIL_BACKEND=mailpit."
    )


@lru_cache
def default_sender() -> EmailSender:
    return make_sender(get_settings())
