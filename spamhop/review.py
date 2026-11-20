"""Metadata-only IMAP review queue and on-demand Rspamd learning.

Mail contents are never written to disk. Only headers, IMAP locators, and
manual training outcomes are retained in SQLite.
"""
import base64
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import imaplib
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen


LOG = logging.getLogger(__name__)
HEADER_FIELDS = "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)])"
FULL_MESSAGE = "(BODY.PEEK[])"


def message_fields(raw: bytes) -> dict[str, str]:
    parsed = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
    return {
        "sender": str(parsed.get("From", "")).strip()[:320],
        "recipient": str(parsed.get("To", "")).strip()[:320],
        "subject": str(parsed.get("Subject", "")).strip()[:500],
        "message_date": str(parsed.get("Date", "")).strip()[:100],
        "message_id": str(parsed.get("Message-ID", "")).strip()[:320],
    }


class ReviewStore:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS messages (
                  id INTEGER PRIMARY KEY,
                  mailbox TEXT NOT NULL,
                  uidvalidity INTEGER NOT NULL,
                  uid INTEGER NOT NULL,
                  sender TEXT NOT NULL,
                  recipient TEXT NOT NULL,
                  subject TEXT NOT NULL,
                  message_date TEXT NOT NULL,
                  message_id TEXT NOT NULL,
                  observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  label TEXT,
                  trained_label TEXT,
                  training_status TEXT NOT NULL DEFAULT 'unreviewed',
                  training_error TEXT,
                  UNIQUE(mailbox, uidvalidity, uid)
                );
                CREATE INDEX IF NOT EXISTS messages_recent ON messages(id DESC);
            """)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def exists(self, mailbox: str, validity: int, uid: int) -> bool:
        with self._connect() as db:
            return db.execute(
                "SELECT 1 FROM messages WHERE mailbox=? AND uidvalidity=? AND uid=?",
                (mailbox, validity, uid),
            ).fetchone() is not None

    def add(self, mailbox: str, validity: int, uid: int, fields: dict):
        with self._connect() as db:
            db.execute("""
                INSERT OR IGNORE INTO messages
                (mailbox, uidvalidity, uid, sender, recipient, subject, message_date, message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (mailbox, validity, uid, fields["sender"], fields["recipient"],
                  fields["subject"], fields["message_date"], fields["message_id"]))

    def get(self, message_id: int):
        with self._connect() as db:
            row = db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
            return dict(row) if row else None

    def list(self, selection: str, limit: int = 100):
        clauses = {
            "all": "1=1",
            "unreviewed": "label IS NULL",
            "spam": "label='spam'",
            "ham": "label='ham'",
            "errors": "training_status='error'",
        }
        with self._connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM messages WHERE " + clauses.get(selection, clauses["all"])
                + " ORDER BY id DESC LIMIT ?",
                (limit,),
            )]

    def mark(self, message_id: int, label: str):
        with self._connect() as db:
            db.execute(
                "UPDATE messages SET label=?, training_status='pending', training_error=NULL "
                "WHERE id=?", (label, message_id)
            )

    def finish(self, message_id: int, label: str):
        with self._connect() as db:
            db.execute(
                "UPDATE messages SET trained_label=?, training_status='learned', "
                "training_error=NULL WHERE id=?", (label, message_id)
            )

    def fail(self, message_id: int, error: str):
        with self._connect() as db:
            db.execute(
                "UPDATE messages SET training_status='error', training_error=? WHERE id=?",
                (error[:250], message_id),
            )


@contextmanager
def upstream_imap():
    endpoint = os.environ["SPAMHOP_UPSTREAM"]
    host, port_text = endpoint.rsplit(":", 1)
    tls = os.environ.get("SPAMHOP_UPSTREAM_TLS", "implicit").lower()
    port = int(port_text)
    if tls == "implicit":
        imap = imaplib.IMAP4_SSL(host, port)
    elif tls == "starttls":
        imap = imaplib.IMAP4(host, port)
        imap.starttls()
    elif tls == "none":
        imap = imaplib.IMAP4(host, port)
    else:
        raise RuntimeError("Unsupported upstream IMAP TLS setting")
    try:
        imap.login(
            os.environ["SPAMHOP_UPSTREAM_USERNAME"],
            os.environ["SPAMHOP_UPSTREAM_PASSWORD"],
        )
        yield imap
    finally:
        try:
            imap.logout()
        except Exception:
            LOG.debug("IMAP logout failed", exc_info=True)


def select_readonly(imap, mailbox: str) -> int:
    status, _ = imap.select(mailbox, readonly=True)
    if status != "OK":
        raise RuntimeError("Could not select IMAP mailbox")
    _, values = imap.response("UIDVALIDITY")
    if not values or not values[0]:
        raise RuntimeError("Upstream IMAP server did not provide UIDVALIDITY")
    return int(values[0])


def fetch_uid(imap, uid: int, *, headers_only: bool) -> bytes:
    status, result = imap.uid(
        "FETCH",
        str(uid),
        HEADER_FIELDS if headers_only else FULL_MESSAGE,
    )
    if status != "OK":
        raise RuntimeError("IMAP UID FETCH failed")
    for part in result or ():
        if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes):
            return part[1]
    raise RuntimeError("Message is no longer available at this IMAP UID")


class ReviewService:
    def __init__(self, *, store: ReviewStore, mailboxes: tuple[str, ...],
                 controller_url: str, controller_password: str, poll_seconds: int,
                 max_messages: int, username: str, password: str, on_learn=None):
        self.store = store
        self.mailboxes = mailboxes
        self.controller_url = controller_url.rstrip("/")
        self.controller_password = controller_password
        self.poll_seconds = poll_seconds
        self.max_messages = max_messages
        self.username = username
        self.password = password
        self.on_learn = on_learn
        self.csrf = secrets.token_urlsafe(32)
        self.last_sync = "Not synchronized yet"
        self.stop_event = threading.Event()
        self.train_lock = threading.Lock()
        self.http_server = None
        self.scan_thread = None
        self.web_thread = None

    @classmethod
    def from_env(cls, *, on_learn=None):
        username = os.getenv("SPAMHOP_REVIEW_USER", "").strip()
        password = os.getenv("SPAMHOP_REVIEW_PASSWORD", "")
        controller_password = os.getenv("SPAMHOP_REVIEW_RSPAMD_PASSWORD", "")
        if not username or not password or not controller_password:
            raise RuntimeError(
                "SPAMHOP_REVIEW_USER, SPAMHOP_REVIEW_PASSWORD and "
                "SPAMHOP_REVIEW_RSPAMD_PASSWORD are required when review is enabled"
            )
        mailboxes = tuple(
            item.strip() for item in
            os.getenv("SPAMHOP_REVIEW_MAILBOXES", "INBOX").split(",")
            if item.strip()
        )
        if not mailboxes:
            raise RuntimeError("At least one review mailbox is required")
        poll_seconds = int(os.getenv("SPAMHOP_REVIEW_POLL_SECONDS", "120"))
        max_messages = int(os.getenv("SPAMHOP_REVIEW_MAX_MESSAGES", "500"))
        if poll_seconds < 15 or max_messages < 1:
            raise RuntimeError("Invalid review poll interval or max messages")
        return cls(
            store=ReviewStore(os.getenv("SPAMHOP_REVIEW_DB", "/data/review.sqlite3")),
            mailboxes=mailboxes,
            controller_url=os.getenv("SPAMHOP_REVIEW_RSPAMD_URL", "http://rspamd:11334"),
            controller_password=controller_password,
            poll_seconds=poll_seconds,
            max_messages=max_messages,
            username=username,
            password=password,
            on_learn=on_learn,
        )

    def sync_once(self) -> int:
        count = 0
        with upstream_imap() as imap:
            for mailbox in self.mailboxes:
                validity = select_readonly(imap, mailbox)
                status, result = imap.uid("SEARCH", None, "ALL")
                if status != "OK":
                    raise RuntimeError("IMAP UID SEARCH failed")
                uids = [int(n) for n in (result[0] or b"").split()]
                for uid in uids[-self.max_messages:]:
                    if self.stop_event.is_set():
                        break
                    if self.store.exists(mailbox, validity, uid):
                        continue
                    fields = message_fields(fetch_uid(imap, uid, headers_only=True))
                    self.store.add(mailbox, validity, uid, fields)
                    count += 1
        return count

    def scan_loop(self):
        while not self.stop_event.is_set():
            try:
                count = self.sync_once()
                self.last_sync = "Synchronized ({} new messages)".format(count)
            except Exception as exc:
                LOG.warning("Review IMAP synchronization failed: %s", exc)
                self.last_sync = "Synchronization error; check server logs"
            self.stop_event.wait(self.poll_seconds)

    def label(self, message_id: int, label: str) -> str:
        if label not in ("spam", "ham"):
            raise ValueError("Invalid label")
        with self.train_lock:
            row = self.store.get(message_id)
            if row is None:
                raise ValueError("Unknown message")
            if row["trained_label"] == label and row["training_status"] == "learned":
                return "Already trained as " + label
            self.store.mark(message_id, label)
            try:
                with upstream_imap() as imap:
                    validity = select_readonly(imap, row["mailbox"])
                    if validity != row["uidvalidity"]:
                        raise RuntimeError("IMAP UIDVALIDITY changed; message cannot be verified")
                    message = fetch_uid(imap, row["uid"], headers_only=False)
                fields = message_fields(message)
                for field in ("sender", "recipient", "subject", "message_date", "message_id"):
                    if fields[field] != row[field]:
                        raise RuntimeError("Message identity changed; training cancelled")
                request = Request(
                    self.controller_url + ("/learnspam" if label == "spam" else "/learnham"),
                    data=message,
                    headers={
                        "Content-Type": "message/rfc822",
                        "Password": self.controller_password,
                    },
                    method="POST",
                )
                with urlopen(request, timeout=30) as response:
                    payload = response.read(4096)
                if payload:
                    try:
                        parsed = json.loads(payload)
                    except (ValueError, UnicodeDecodeError):
                        parsed = None
                    if isinstance(parsed, dict) and (
                        parsed.get("success") is False or parsed.get("error")
                    ):
                        raise RuntimeError("Rspamd declined learning request")
                self.store.finish(message_id, label)
                if self.on_learn is not None:
                    self.on_learn()
                return "Learned as " + label
            except Exception as exc:
                self.store.fail(message_id, str(exc))
                LOG.warning("Review learning failed for row %s: %s", message_id, exc)
                raise RuntimeError("Learning failed; see server logs") from exc

    def start(self, address: tuple[str, int]):
        self.http_server = ThreadingHTTPServer(address, ReviewHandler)
        self.http_server.service = self
        self.web_thread = threading.Thread(
            target=self.http_server.serve_forever, name="review-web", daemon=True
        )
        self.scan_thread = threading.Thread(
            target=self.scan_loop, name="review-indexer", daemon=True
        )
        self.web_thread.start()
        self.scan_thread.start()

    def close(self):
        self.stop_event.set()
        if self.http_server is not None:
            self.http_server.shutdown()
            self.http_server.server_close()
        if self.web_thread is not None:
            self.web_thread.join(timeout=2)


class ReviewHandler(BaseHTTPRequestHandler):
    def authenticated(self):
        expected = "{}:{}".format(self.server.service.username, self.server.service.password)
        supplied = self.headers.get("Authorization", "")
        try:
            scheme, value = supplied.split(" ", 1)
            decoded = base64.b64decode(value, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            decoded = ""
            scheme = ""
        if scheme.lower() != "basic" or not hmac.compare_digest(decoded, expected):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="SpamHop Review"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return False
        return True

    def respond(self, code: int, body: str):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.authenticated():
            return
        url = urlsplit(self.path)
        if url.path != "/":
            self.send_error(404)
            return
        service = self.server.service
        selection = parse_qs(url.query).get("filter", ["all"])[0]
        if selection not in ("all", "unreviewed", "spam", "ham", "errors"):
            selection = "all"
        links = " ".join(
            '<a href="/?filter={}">{}</a>'.format(item, item.title())
            for item in ("all", "unreviewed", "spam", "ham", "errors")
        )
        rows = []
        for msg in service.store.list(selection):
            status = msg["training_status"]
            if msg["label"]:
                status = "{} / {}".format(msg["label"], status)
            buttons = "".join(
                '<form method="post" action="/label">'
                '<input type="hidden" name="csrf" value="{}">'
                '<input type="hidden" name="id" value="{}">'
                '<button name="label" value="{}" type="submit">{}</button></form>'.format(
                    escape(service.csrf, quote=True), msg["id"], tag, caption
                )
                for tag, caption in (("spam", "Spam"), ("ham", "Ham"))
            )
            rows.append(
                "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
                "<td>{}</td><td>{}</td><td class='actions'>{}</td></tr>".format(
                    escape(msg["message_date"]), escape(msg["sender"]),
                    escape(msg["recipient"]), escape(msg["subject"]),
                    escape(msg["mailbox"]), escape(status), buttons,
                )
            )
        document = """<!doctype html><html lang="en"><meta charset="utf-8">
<title>SpamHop Review</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font:14px system-ui,sans-serif;margin:2rem auto;max-width:1450px;padding:0 1rem;color:#24292f}
h1{font-size:1.5rem}nav{display:flex;gap:1rem;margin:1rem 0}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:.65rem;text-align:left;
border-bottom:1px solid #ddd;vertical-align:top;overflow-wrap:anywhere}
th{background:#f6f7f8}form{display:inline-block;margin:0 3px 3px 0}
button{cursor:pointer;padding:.3rem .55rem} .scroll{overflow-x:auto}
p{color:#555} .actions{white-space:nowrap} @media(max-width:650px){body{margin:1rem auto}}
</style><h1>SpamHop Review</h1><p>{}</p><nav>{}</nav>
<div class="scroll"><table><thead><tr><th>Date</th><th>Sender</th><th>Recipient</th>
<th>Subject</th><th>Folder</th><th>Review</th><th>Mark</th></tr></thead>
<tbody>{}</tbody></table></div><p>Showing up to 100 newest matching messages.
Only headers and review status are stored. Original messages stay upstream.</p>
</html>""".format(escape(service.last_sync), links, "".join(rows))
        self.respond(200, document)

    def do_POST(self):
        if not self.authenticated():
            return
        if urlsplit(self.path).path != "/label":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                raise ValueError("Invalid body length")
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            if not hmac.compare_digest(
                data.get("csrf", [""])[0], self.server.service.csrf
            ):
                self.send_error(403)
                return
            message_id = int(data["id"][0])
            label = data["label"][0]
            if message_id < 1 or label not in ("spam", "ham"):
                raise ValueError("Invalid feedback")
            self.server.service.label(message_id, label)
        except (ValueError, KeyError):
            self.send_error(400)
            return
        except RuntimeError:
            # Error status persists in the row so it can be retried.
            pass
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, fmt, *args):
        return
