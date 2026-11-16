"""
A small local mail service for testing: an SMTP server that accepts and keeps every message, plus a web inbox.
Nothing is relayed anywhere. Standard library only.

    python testing/mail_sink.py                              # SMTP on 127.0.0.1:1025, inbox on http://127.0.0.1:8025
    python testing/mail_sink.py --maildir testing/output/mail     # also write each message as a .eml file
    python testing/mail_sink.py --reject 'bounce|@invalid\\.'     # answer 550 for matching recipients (failure paths)

Point the portal (or authsvc/mailsvc) at it; codes and notices then show up in the inbox:

    PORTAL_SMTP_URL=smtp://127.0.0.1:1025 PORTAL_MAIL_FROM=portal@example.local uvicorn utsu_core:create_app --factory

The mail campaign tool can use it too with an `smtp` config of {"host": "127.0.0.1", "port": 1025, "security": "none"}.
It listens on the same ports as Mailhog, so either can be used.

Inbox endpoints:
    GET    /                      HTML inbox (newest first)
    GET    /messages[?to=ADDR]    JSON list of messages, optionally only those sent to ADDR
    GET    /messages/latest[?to=ADDR]
    GET    /messages/<id>         JSON for one message;  /messages/<id>.eml for the raw message
    DELETE /messages              empty the inbox

Tests can run it in-process: `with MailSink(smtp_port=0, http_port=0) as sink: ...` then read `sink.messages`.
"""
import argparse
import asyncio
import base64
import html
import json
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.utils import getaddresses
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

MAX_SIZE = 10 * 1024 * 1024
STREAM_LIMIT = 1024 * 1024  # longest single line accepted


@dataclass
class Message:
    id: int
    mail_from: str
    rcpt_to: list[str]
    raw: bytes
    received: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def summary(self) -> dict:
        msg = message_from_bytes(self.raw, policy=policy.default)
        body = msg.get_body(preferencelist=("plain", "html"))
        try:
            text = body.get_content().replace("\r\n", "\n") if body is not None else ""
        except (LookupError, ValueError):
            text = ""
        return {"id": self.id, "received": self.received, "envelope_from": self.mail_from, "envelope_to": self.rcpt_to,
                "from": str(msg.get("From", "")), "to": str(msg.get("To", "")), "subject": str(msg.get("Subject", "")),
                "text": text}

    def addressed_to(self, addr: str) -> bool:
        addr = addr.strip().lower()
        msg = message_from_bytes(self.raw, policy=policy.compat32)
        headers = [a for _, a in getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []))]
        return any(a.lower() == addr for a in [*self.rcpt_to, *headers])


class Inbox:
    def __init__(self, limit: int = 1000, maildir: Path | None = None):
        self._lock = threading.Lock()
        self._messages: deque[Message] = deque(maxlen=limit)
        self._next = 1
        self.maildir = maildir
        if maildir:
            maildir.mkdir(parents=True, exist_ok=True)

    def add(self, mail_from: str, rcpt_to: list[str], raw: bytes) -> Message:
        with self._lock:
            m = Message(self._next, mail_from, list(rcpt_to), raw)
            self._next += 1
            self._messages.append(m)
        if self.maildir:
            (self.maildir / f"{m.id:06d}.eml").write_bytes(raw)
        return m

    def list(self, to: str | None = None) -> list[Message]:
        with self._lock:
            items = list(self._messages)
        return [m for m in items if not to or m.addressed_to(to)]

    def get(self, mid: int) -> Message | None:
        return next((m for m in self.list() if m.id == mid), None)

    def clear(self):
        with self._lock:
            self._messages.clear()


def _addr(arg: str, prefix: str) -> str | None:
    m = re.match(rf"{prefix}:\s*<([^>]*)>", arg, re.I)
    return m.group(1) if m else None


class SmtpSession:
    """One SMTP conversation (RFC 5321 subset). AUTH is accepted with any credentials; STARTTLS is not offered."""

    def __init__(self, reader, writer, inbox: Inbox, hostname: str, reject: re.Pattern | None):
        self.r, self.w, self.inbox, self.hostname, self.reject = reader, writer, inbox, hostname, reject
        self.reset()

    def reset(self):
        self.mail_from, self.rcpt_to = None, []

    async def reply(self, *lines: str):
        """Each line is 'CODE text'; all but the last get the 'CODE-' continuation marker."""
        for i, line in enumerate(lines):
            num, _, text = line.partition(" ")
            self.w.write(f"{num}{'-' if i < len(lines) - 1 else ' '}{text}\r\n".encode())
        await self.w.drain()

    async def readline(self) -> bytes:
        return await self.r.readline()

    async def run(self):
        await self.reply(f"220 {self.hostname} UTSU test mail sink ESMTP")
        try:
            while line := await self.readline():
                verb, _, arg = line.decode("utf-8", "replace").rstrip("\r\n").partition(" ")
                handler = getattr(self, f"do_{verb.upper()}", None)
                if handler is None:
                    await self.reply("502 Command not implemented")
                elif await handler(arg.strip()) is False:
                    break
        except (ConnectionError, ValueError):  # ValueError: a line longer than the stream limit
            pass
        finally:
            self.w.close()

    async def do_HELO(self, arg):
        self.reset()
        await self.reply(f"250 {self.hostname}")

    async def do_EHLO(self, arg):
        self.reset()
        await self.reply(f"250 {self.hostname}", f"250 SIZE {MAX_SIZE}", "250 8BITMIME", "250 SMTPUTF8",
                         "250 AUTH PLAIN LOGIN")

    async def do_AUTH(self, arg):
        mech, _, initial = arg.partition(" ")
        if mech.upper() == "PLAIN":
            if not initial:
                await self.reply("334 ")
                await self.readline()
        elif mech.upper() == "LOGIN":
            for prompt in ("Username:", "Password:"):
                if prompt == "Username:" and initial:
                    continue
                await self.reply(f"334 {base64.b64encode(prompt.encode()).decode()}")
                await self.readline()
        else:
            return await self.reply("504 Unrecognized authentication type")
        await self.reply("235 Authentication successful")

    async def do_MAIL(self, arg):
        addr = _addr(arg, "FROM")
        if addr is None:
            return await self.reply("501 Syntax: MAIL FROM:<address>")
        self.reset()
        self.mail_from = addr
        await self.reply("250 OK")

    async def do_RCPT(self, arg):
        if self.mail_from is None:
            return await self.reply("503 Need MAIL command first")
        addr = _addr(arg, "TO")
        if not addr:
            return await self.reply("501 Syntax: RCPT TO:<address>")
        if self.reject and self.reject.search(addr):
            return await self.reply(f"550 Mailbox unavailable: {addr}")
        self.rcpt_to.append(addr)
        await self.reply("250 OK")

    async def do_DATA(self, arg):
        if not self.rcpt_to:
            return await self.reply("503 Need RCPT command first")
        await self.reply("354 End data with <CR><LF>.<CR><LF>")
        lines, size, too_big = [], 0, False
        while True:
            line = await self.readline()
            if not line:
                raise ConnectionError("client went away during DATA")
            if line in (b".\r\n", b".\n"):
                break
            if line.startswith(b"."):
                line = line[1:]
            size += len(line)
            too_big = too_big or size > MAX_SIZE
            if not too_big:
                lines.append(line)
        if too_big:
            self.reset()
            return await self.reply("552 Message exceeds fixed maximum message size")
        m = self.inbox.add(self.mail_from, self.rcpt_to, b"".join(lines))
        self.reset()
        await self.reply(f"250 OK: queued as {m.id}")

    async def do_RSET(self, arg):
        self.reset()
        await self.reply("250 OK")

    async def do_NOOP(self, arg):
        await self.reply("250 OK")

    async def do_VRFY(self, arg):
        await self.reply("252 Cannot VRFY user")

    async def do_QUIT(self, arg):
        await self.reply("221 Bye")
        return False


def _page(messages: list[Message]) -> str:
    rows = []
    for m in reversed(messages):
        s = m.summary()
        rows.append(
            f"<details><summary><b>{html.escape(s['subject'] or '(no subject)')}</b> &rarr; {html.escape(s['to'])}"
            f" <small>#{m.id} {html.escape(s['received'])} &middot; <a href='/messages/{m.id}.eml'>raw</a></small>"
            f"</summary><pre>{html.escape(s['text'])}</pre></details>")
    body = "".join(rows) or "<p>No messages yet.</p>"
    return ("<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=5><title>Test inbox</title>"
            "<style>body{font:15px system-ui;margin:2rem auto;max-width:60rem;padding:0 1rem}"
            "details{border-bottom:1px solid #ddd;padding:.6rem 0}pre{white-space:pre-wrap;background:#f6f6f6;padding:.8rem}"
            "small{color:#666}</style>"
            f"<h1>Test inbox <small>({len(messages)} message{'s' * (len(messages) != 1)}, refreshes every 5s)</small></h1>"
            f"{body}")


def _http_handler(inbox: Inbox):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status: int, body: bytes | str, ctype: str):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def json(self, obj, status=200):
            self.send(status, json.dumps(obj, indent=2), "application/json")

        def do_GET(self):
            url = urlsplit(self.path)
            to = parse_qs(url.query).get("to", [None])[0]
            path = url.path.rstrip("/")
            if path == "":
                return self.send(200, _page(inbox.list()), "text/html; charset=utf-8")
            if path == "/messages":
                return self.json([m.summary() for m in inbox.list(to)])
            if path == "/messages/latest":
                found = inbox.list(to)
                return self.json(found[-1].summary()) if found else self.json({"error": "no messages"}, 404)
            m = re.fullmatch(r"/messages/(\d+)(\.eml)?", path)
            msg = inbox.get(int(m.group(1))) if m else None
            if msg is None:
                return self.json({"error": "not found"}, 404)
            if m.group(2):
                return self.send(200, msg.raw, "message/rfc822")
            return self.json(msg.summary())

        def do_DELETE(self):
            if urlsplit(self.path).path.rstrip("/") != "/messages":
                return self.json({"error": "not found"}, 404)
            inbox.clear()
            self.send(204, b"", "text/plain")

    return Handler


class MailSink:
    """Runs the SMTP server and web inbox on a background thread. Port 0 picks a free port."""

    def __init__(self, host="127.0.0.1", smtp_port=1025, http_port: int | None = 8025, maildir: Path | None = None,
                 reject: str | None = None, limit=1000):
        self.host, self.smtp_port, self.http_port = host, smtp_port, http_port
        self.inbox = Inbox(limit, maildir)
        self.reject = re.compile(reject, re.I) if reject else None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    @property
    def messages(self) -> list[Message]:
        return self.inbox.list()

    def start(self) -> "MailSink":
        if self.http_port is not None:
            self._http = ThreadingHTTPServer((self.host, self.http_port), _http_handler(self.inbox))
            self.http_port = self._http.server_address[1]
            threading.Thread(target=self._http.serve_forever, daemon=True).start()
        self._thread = threading.Thread(target=self._run_smtp, daemon=True)
        self._thread.start()
        self._ready.wait(10)
        if self._error:
            raise self._error
        return self

    def _run_smtp(self):
        self._loop = asyncio.new_event_loop()

        async def main():
            async def handle(reader, writer):
                await SmtpSession(reader, writer, self.inbox, "mail-sink.local", self.reject).run()

            server = await asyncio.start_server(handle, self.host, self.smtp_port, limit=STREAM_LIMIT)
            self.smtp_port = server.sockets[0].getsockname()[1]
            self._stopped = asyncio.Event()
            self._ready.set()
            async with server:
                await self._stopped.wait()

        try:
            self._loop.run_until_complete(main())
        except BaseException as e:
            self._error = e
            self._ready.set()
        finally:
            self._loop.close()

    def stop(self):
        if getattr(self, "_http", None):
            self._http.shutdown()
            self._http.server_close()
        if getattr(self, "_stopped", None):
            self._loop.call_soon_threadsafe(self._stopped.set)
            self._thread.join(5)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--host", default="127.0.0.1", help="interface to listen on (default 127.0.0.1)")
    p.add_argument("--smtp-port", type=int, default=1025)
    p.add_argument("--http-port", type=int, default=8025)
    p.add_argument("--maildir", type=Path, help="also write every message to this folder as NNNNNN.eml")
    p.add_argument("--reject", metavar="REGEX", help="answer 550 for recipients matching this (case-insensitive)")
    p.add_argument("--limit", type=int, default=1000, help="messages kept in memory (oldest dropped first)")
    a = p.parse_args(argv)
    sink = MailSink(a.host, a.smtp_port, a.http_port, a.maildir, a.reject, a.limit).start()
    print(f"SMTP on {a.host}:{sink.smtp_port}   inbox http://{a.host}:{sink.http_port}/   (Ctrl+C to stop)", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        sink.stop()


if __name__ == "__main__":
    main()
