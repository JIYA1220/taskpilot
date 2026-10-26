"""A minimal stand-in for Mailpit, for machines without Docker.

    python -m scripts.dev_mailbox

Listens for SMTP on localhost:1025 and serves an inbox page on http://localhost:8025,
the same ports Mailpit uses, so no configuration changes are needed. Nothing is ever
delivered anywhere: messages are kept in memory. Use real Mailpit (docker compose up)
when you can; this exists so the email flow can be tried and demoed without it."""

import html
import sys
import threading
import time
from email import message_from_bytes, policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from aiosmtpd.controller import Controller

SMTP_PORT, HTTP_PORT = 1025, 8025
MESSAGES: list[dict] = []
LOCK = threading.Lock()


class Collector:
    async def handle_DATA(self, server, session, envelope):
        parsed = message_from_bytes(envelope.content, policy=policy.default)
        text = parsed.get_body(("plain",))
        page = parsed.get_body(("html",))
        with LOCK:
            MESSAGES.append(
                {
                    "at": time.strftime("%H:%M:%S"),
                    "from": str(parsed["From"]),
                    "to": ", ".join(envelope.rcpt_tos),
                    "subject": str(parsed["Subject"]),
                    "text": text.get_content() if text else "",
                    "html": page.get_content() if page else "",
                }
            )
        print(f"[{MESSAGES[-1]['at']}] to {MESSAGES[-1]['to']}: {MESSAGES[-1]['subject']}", flush=True)
        return "250 Message accepted"


PAGE = (
    "<!doctype html><meta charset=utf-8><meta name=color-scheme content=light><title>Dev mailbox</title>"
    "<style>html{{color-scheme:light}}body{{font:14px Arial;margin:24px;color:#1e293b;background:#fff}}td,th{{padding:6px 12px;text-align:left;"
    "border-bottom:1px solid #e2e8f0}}a{{color:#4338ca}}pre{{background:#f8fafc;padding:12px;white-space:pre-wrap}}"
    "iframe{{width:100%;height:320px;border:1px solid #e2e8f0;background:#fff;color-scheme:light}}</style>{body}"
)


class Inbox(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the console for received mail
        pass

    def reply(self, body: str, status: int = 200):
        data = PAGE.format(body=body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        with LOCK:
            messages = list(MESSAGES)
        if self.path.startswith("/m/"):
            try:
                m = messages[int(self.path[3:])]
            except (ValueError, IndexError):
                return self.reply("<p>No such message. <a href=/>Back</a></p>", 404)
            self.reply(
                f"<p><a href=/>&larr; Inbox</a></p><h2>{html.escape(m['subject'])}</h2>"
                f"<p><b>From:</b> {html.escape(m['from'])}<br><b>To:</b> {html.escape(m['to'])}<br><b>At:</b> {m['at']}</p>"
                f"<h3>Plain text</h3><pre>{html.escape(m['text'])}</pre>"
                f"<h3>HTML version</h3><iframe sandbox srcdoc=\"{html.escape(m['html'], quote=True)}\"></iframe>"
            )
        else:
            rows = "".join(
                f"<tr><td>{m['at']}</td><td>{html.escape(m['to'])}</td><td><a href=/m/{i}>{html.escape(m['subject'])}</a></td></tr>"
                for i, m in reversed(list(enumerate(messages)))
            )
            self.reply(
                f"<h1>Dev mailbox ({len(messages)})</h1><p>Stand-in for Mailpit. Nothing is delivered anywhere.</p>"
                f"<table><tr><th>Time</th><th>To</th><th>Subject</th></tr>{rows}</table>"
            )


def main() -> int:
    smtp = Controller(Collector(), hostname="127.0.0.1", port=SMTP_PORT)
    smtp.start()
    http = ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), Inbox)
    print(f"SMTP on localhost:{SMTP_PORT}, inbox at http://localhost:{HTTP_PORT}  (Ctrl+C to stop)", flush=True)
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        smtp.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
