"""Review log with seven-day training samples and explicit learning results.

Raw RFC822 messages are retained ONLY when training capture is enabled.
A human label trains future Rspamd classification; the original score
remains immutable. No upstream IMAP mutation or retroactive rescoring.
"""
import base64
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
import hashlib
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen


LOG = logging.getLogger(__name__)
WEEK_SECONDS = 7 * 24 * 60 * 60
DEFAULT_MAX_MESSAGE_BYTES = 25 * 1024 * 1024


def message_fields(raw: bytes) -> dict[str, str]:
    parsed = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
    return {
        "sender": str(parsed.get("From", "")).strip()[:320],
        "recipient": str(parsed.get("To", "")).strip()[:320],
        "subject": str(parsed.get("Subject", "")).strip()[:500],
        "message_date": str(parsed.get("Date", "")).strip()[:100],
    }


@contextmanager
def _database(path: str):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class ReviewStore:
    def __init__(self, db_path: str, samples_path: str, *,
                 retention_seconds: int = WEEK_SECONDS,
                 max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
                 clock=None):
        if retention_seconds <= 0 or max_message_bytes <= 0:
            raise ValueError("Invalid retention or message size setting")
        self.db_path = db_path
        self.samples_path = Path(samples_path)
        self.retention_seconds = retention_seconds
        self.max_message_bytes = max_message_bytes
        self.clock = clock or time.time
        self.lock = threading.RLock()
        Path(db_path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.samples_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.samples_path, 0o700)
        with _database(self.db_path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    digest TEXT UNIQUE NOT NULL,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    message_date TEXT NOT NULL,
                    original_score REAL,
                    original_verdict TEXT,
                    original_engine TEXT,
                    observed_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    payload_state TEXT NOT NULL,
                    label TEXT,
                    trained_label TEXT,
                    training_status TEXT NOT NULL DEFAULT 'unreviewed',
                    training_error TEXT,
                    trained_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS recent_review
                  ON messages(observed_at DESC, id DESC);
            """)
        os.chmod(self.db_path, 0o600)
        self.prune()

    def _sample_path(self, digest: str) -> Path:
        return self.samples_path / (digest + ".eml")

    def record(self, message: bytes, result) -> bool:
        """Capture original score + message once, without extending its TTL."""
        digest = hashlib.sha256(message).hexdigest()
        now = int(self.clock())
        fields = message_fields(message)
        with self.lock:
            with _database(self.db_path) as db:
                if db.execute(
                    "SELECT 1 FROM messages WHERE digest=?", (digest,)
                ).fetchone():
                    return False

                state = "too-large" if len(message) > self.max_message_bytes else "retained"
                if state == "retained":
                    fd, temporary = tempfile.mkstemp(
                        prefix=".tmp-", dir=str(self.samples_path)
                    )
                    try:
                        with os.fdopen(fd, "wb") as destination:
                            os.fchmod(destination.fileno(), 0o600)
                            destination.write(message)
                            destination.flush()
                            os.fsync(destination.fileno())
                        os.replace(temporary, self._sample_path(digest))
                    finally:
                        if os.path.exists(temporary):
                            os.unlink(temporary)
                try:
                    db.execute("""
                        INSERT INTO messages
                        (digest,sender,recipient,subject,message_date,
                         original_score,original_verdict,original_engine,
                         observed_at,expires_at,payload_state)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        digest, fields["sender"], fields["recipient"],
                        fields["subject"], fields["message_date"],
                        result.score, result.verdict, result.engine, now,
                        now + self.retention_seconds, state,
                    ))
                except Exception:
                    if state == "retained":
                        self._sample_path(digest).unlink(missing_ok=True)
                    raise
        return True

    def get(self, item_id: int):
        with _database(self.db_path) as db:
            row = db.execute("SELECT * FROM messages WHERE id=?", (item_id,)).fetchone()
            return dict(row) if row else None

    def list(self, selection="all", limit=100, offset=0):
        clauses = {
            "all": "1=1",
            "unreviewed": "label IS NULL",
            "spam": "label='spam'",
            "ham": "label='ham'",
            "errors": "training_status IN ('error','uncertain')",
        }
        clause = clauses.get(selection, "1=1")
        with _database(self.db_path) as db:
            rows = db.execute(
                "SELECT * FROM messages WHERE " + clause
                + " ORDER BY observed_at DESC,id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            return [dict(row) for row in rows]

    def mark(self, item_id: int, label: str) -> None:
        if label not in ("spam", "ham"):
            raise ValueError("Invalid review label")
        with _database(self.db_path) as db:
            db.execute(
                "UPDATE messages SET label=?,training_status='pending',"
                "training_error=NULL WHERE id=?", (label, item_id)
            )

    def finish(self, item_id: int, label: str):
        with _database(self.db_path) as db:
            db.execute(
                "UPDATE messages SET trained_label=?,training_status='learned',"
                "training_error=NULL,trained_at=? WHERE id=?",
                (label, int(self.clock()), item_id),
            )

    def fail(self, item_id: int, status: str, detail: str):
        if status not in ("error", "uncertain", "expired"):
            raise ValueError("Invalid failure state")
        with _database(self.db_path) as db:
            db.execute(
                "UPDATE messages SET training_status=?,training_error=? WHERE id=?",
                (status, detail[:180], item_id),
            )

    def read_sample(self, row: dict) -> bytes:
        if row["payload_state"] != "retained" or int(self.clock()) >= row["expires_at"]:
            raise FileNotFoundError("The seven-day training window has expired")
        try:
            data = self._sample_path(row["digest"]).read_bytes()
        except FileNotFoundError:
            raise FileNotFoundError("The training sample is unavailable") from None
        if hashlib.sha256(data).hexdigest() != row["digest"]:
            raise RuntimeError("Training sample integrity check failed")
        return data

    def prune(self) -> int:
        """Delete expired raw samples; preserve metadata and feedback indefinitely."""
        now = int(self.clock())
        removed = 0
        with self.lock:
            with _database(self.db_path) as db:
                rows = db.execute(
                    "SELECT id,digest FROM messages WHERE "
                    "payload_state='retained' AND expires_at<=?", (now,)
                ).fetchall()
                for row in rows:
                    self._sample_path(row["digest"]).unlink(missing_ok=True)
                    db.execute(
                        "UPDATE messages SET payload_state='expired' WHERE id=?",
                        (row["id"],),
                    )
                    removed += 1
                active = {
                    row["digest"] for row in db.execute(
                        "SELECT digest FROM messages WHERE payload_state='retained'"
                    )
                }
            # Clean up files left behind by a crash between the atomic
            # sample write and the SQLite insert.
            for path in self.samples_path.glob("*.eml"):
                if path.stem not in active:
                    path.unlink(missing_ok=True)
            for path in self.samples_path.glob(".tmp-*"):
                if path.stat().st_mtime < time.time() - 3600:
                    path.unlink(missing_ok=True)
        return removed


class LearningRejected(RuntimeError):
    pass


class LearningUncertain(RuntimeError):
    pass


def rspamd_learning_request(url: str, password: str, message: bytes):
    """Only explicit JSON {"success": true} confirms a training operation."""
    request = Request(
        url,
        data=message,
        headers={"Content-Type": "message/rfc822", "Password": password},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read(4097)
    except HTTPError as exc:
        if exc.code >= 500:
            raise LearningUncertain(
                "Rspamd returned HTTP {}; training may have occurred".format(exc.code)
            ) from exc
        raise LearningRejected("Rspamd returned HTTP {}".format(exc.code)) from exc
    except (URLError, TimeoutError, OSError) as exc:
        # A timeout/disconnect can occur AFTER the server learned the message.
        raise LearningUncertain("Rspamd connection failed; training may have occurred") from exc
    if len(body) > 4096:
        raise LearningUncertain("Rspamd response exceeded the supported size")
    try:
        result = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise LearningUncertain("Rspamd returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise LearningUncertain("Rspamd returned an unexpected response")
    if result.get("success") is True:
        return
    if result.get("success") is False or result.get("error"):
        raise LearningRejected("Rspamd did not accept the training request")
    raise LearningUncertain("Rspamd did not explicitly confirm success")


class ReviewService:
    def __init__(self, store: ReviewStore, *,
                 controller_url: str, controller_password: str,
                 username: str, password: str):
        self.store = store
        self.controller_url = controller_url.rstrip("/")
        self.controller_password = controller_password
        self.username = username
        self.password = password
        self.csrf = secrets.token_urlsafe(32)
        self.train_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.http_server = None
        self.web_thread = None
        self.expiry_thread = None

    @classmethod
    def from_env(cls, *, require_credentials=True):
        username = os.getenv("SPAMHOP_REVIEW_USER", "").strip()
        password = os.getenv("SPAMHOP_REVIEW_PASSWORD", "")
        controller_password = os.getenv("SPAMHOP_REVIEW_RSPAMD_PASSWORD", "")
        if require_credentials and (not username or not password or not controller_password):
            raise RuntimeError("Review username, password and Rspamd learning password are required")
        return cls(
            store=ReviewStore(
                os.getenv("SPAMHOP_REVIEW_DB", "/data/review.sqlite3"),
                os.getenv("SPAMHOP_TRAINING_SAMPLES_DIR", "/data/training"),
                max_message_bytes=int(os.getenv(
                    "SPAMHOP_TRAINING_MAX_MESSAGE_BYTES", str(DEFAULT_MAX_MESSAGE_BYTES)
                )),
            ),
            controller_url=os.getenv("SPAMHOP_REVIEW_RSPAMD_URL", "http://rspamd:11334"),
            controller_password=controller_password,
            username=username,
            password=password,
        )

    def label(self, item_id: int, label: str):
        if label not in ("spam", "ham"):
            raise ValueError("Invalid label")
        with self.train_lock:
            row = self.store.get(item_id)
            if row is None:
                raise ValueError("Unknown email")
            if row["trained_label"] == label and row["training_status"] == "learned":
                return "Already learned as " + label
            self.store.mark(item_id, label)
            try:
                message = self.store.read_sample(row)
            except FileNotFoundError as exc:
                self.store.fail(item_id, "expired", str(exc))
                return "Feedback saved; sample unavailable"
            except Exception as exc:
                self.store.fail(item_id, "error", str(exc))
                return "Feedback saved; sample could not be verified"

            try:
                rspamd_learning_request(
                    self.controller_url
                    + ("/learnspam" if label == "spam" else "/learnham"),
                    self.controller_password,
                    message,
                )
            except LearningRejected as exc:
                self.store.fail(item_id, "error", str(exc))
                return "Feedback saved; Rspamd rejected training"
            except LearningUncertain as exc:
                self.store.fail(item_id, "uncertain", str(exc))
                return "Feedback saved; training outcome uncertain"
            except Exception:
                self.store.fail(item_id, "uncertain", "Unrecognized training failure")
                LOG.exception("Unexpected Rspamd training failure for row %s", item_id)
                return "Feedback saved; training outcome uncertain"
            self.store.finish(item_id, label)
            return "Confirmed learned as " + label

    def expiration_loop(self):
        while not self.stop_event.wait(600):
            try:
                self.store.prune()
            except Exception:
                LOG.exception("Review sample expiration failed")

    def start(self, address: tuple[str, int] | None):
        if address is not None:
            self.http_server = ThreadingHTTPServer(address, ReviewHandler)
            self.http_server.service = self
            self.web_thread = threading.Thread(
                target=self.http_server.serve_forever,
                name="review-web", daemon=True
            )
            self.web_thread.start()
        self.expiry_thread = threading.Thread(
            target=self.expiration_loop, name="review-expiry", daemon=True
        )
        self.expiry_thread.start()

    def close(self):
        self.stop_event.set()
        if self.http_server is not None:
            self.http_server.shutdown()
            self.http_server.server_close()
        if self.web_thread is not None:
            self.web_thread.join(timeout=2)
        if self.expiry_thread is not None:
            self.expiry_thread.join(timeout=2)


class ReviewHandler(BaseHTTPRequestHandler):
    def authenticated(self):
        expected = "{}:{}".format(self.server.service.username, self.server.service.password)
        supplied = self.headers.get("Authorization", "")
        try:
            scheme, value = supplied.split(" ", 1)
            decoded = base64.b64decode(value, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            decoded, scheme = "", ""
        if scheme.lower() != "basic" or not hmac.compare_digest(decoded, expected):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="SpamHop Review"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return False
        return True

    def respond(self, code, content):
        data = content.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; "
            "form-action 'self'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.authenticated():
            return
        if urlsplit(self.path).path != "/":
            self.send_error(404)
            return
        service = self.server.service
        parameters = parse_qs(urlsplit(self.path).query)
        selection = parameters.get("filter", ["all"])[0]
        if selection not in ("all", "unreviewed", "spam", "ham", "errors"):
            selection = "all"
        try:
            page = max(1, min(10000, int(parameters.get("page", ["1"])[0])))
        except ValueError:
            page = 1
        filters = " ".join(
            '<a href="/?filter={}">{}</a>'.format(item, item.title())
            for item in ("all", "unreviewed", "spam", "ham", "errors")
        )
        rows = []
        for msg in service.store.list(selection, limit=100, offset=(page - 1) * 100):
            score = "Unknown" if msg["original_score"] is None else "{:.3f}".format(
                msg["original_score"]
            )
            status = msg["training_status"]
            label = msg["label"] or "—"
            if msg["payload_state"] != "retained" and not msg["label"]:
                status = msg["payload_state"]
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
                "<td>{}</td><td>{}</td><td>{}</td><td class='actions'>{}</td></tr>".format(
                    escape(msg["message_date"]), escape(msg["sender"]),
                    escape(msg["subject"]), escape(msg["recipient"]),
                    escape(score + " (" + (msg["original_verdict"] or "unknown") + ")"),
                    escape(label + " / " + status), buttons,
                )
            )
        nav = '<nav>{} <a href="/?filter={}&page={}">Next page</a></nav>'.format(
            filters, selection, page + 1
        )
        document = """<!doctype html><html lang="en"><meta charset="utf-8">
<title>SpamHop Review</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font:14px system-ui,sans-serif;margin:2rem auto;max-width:1450px;padding:0 1rem;color:#24292f}
h1{font-size:1.5rem}nav{display:flex;gap:1rem;margin:1rem 0;flex-wrap:wrap}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:.65rem;text-align:left;
border-bottom:1px solid #ddd;vertical-align:top;overflow-wrap:anywhere}
th{background:#f6f7f8}form{display:inline-block;margin:0 3px 3px 0}
button{cursor:pointer;padding:.3rem .55rem}.scroll{overflow-x:auto}
p{color:#555}.actions{white-space:nowrap}
</style><h1>SpamHop — Training Review</h1>
<p>Original scores are preserved. Human feedback trains future mail; it does not
retroactively alter the displayed score or message.</p>{}<div class="scroll">
<table><thead><tr><th>Date</th><th>Sender</th><th>Subject</th>
<th>Recipient</th><th>Original score</th><th>Re-classified to</th>
<th>Feedback</th></tr></thead><tbody>{}</tbody></table></div>
<p>Only scored messages encountered while training mode is enabled are captured.
Full samples are automatically deleted after seven days; review metadata remains.</p>
</html>""".format(nav, "".join(rows))
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
            item_id = int(data["id"][0])
            label = data["label"][0]
            if item_id < 1 or label not in ("spam", "ham"):
                raise ValueError("Invalid review")
            self.server.service.label(item_id, label)
        except (ValueError, KeyError):
            self.send_error(400)
            return
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, fmt, *args):
        return
