import base64
from contextlib import contextmanager
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from spamhop.review import (
    ReviewService,
    ReviewStore,
    fetch_uid,
    message_fields,
    select_readonly,
)


HEADERS = (
    b"From: Alice <alice@example.com>\r\n"
    b"To: Bob <bob@example.com>\r\n"
    b"Subject: Hello\r\n"
    b"Date: Wed, 19 Aug 2026 13:00:00 -0400\r\n"
    b"Message-ID: <one@example.com>\r\n"
)
MESSAGE = HEADERS + b"\r\nSecret body that must never persist.\r\n"


class FakeIMAP:
    def __init__(self, validity=99):
        self.validity = validity
        self.selected = None

    def select(self, name, readonly=False):
        self.selected = name
        if not readonly:
            raise AssertionError("Review must never change the mailbox")
        return "OK", [b"1"]

    def response(self, name):
        return "OK", [str(self.validity).encode("ascii")]

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b"7"]
        if command == "FETCH":
            fields_only = "HEADER.FIELDS" in args[-1]
            return "OK", [(b"7 FETCH ...", HEADERS if fields_only else MESSAGE), b")"]
        raise AssertionError(command)


@contextmanager
def fake_connection(imap):
    yield imap


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, length):
        return b'{"success":true}'


class ReviewQueueTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.folder.name, "review.sqlite3")
        self.store = ReviewStore(self.db_path)

    def tearDown(self):
        self.folder.cleanup()

    def add_one(self):
        self.store.add("INBOX", 99, 7, message_fields(HEADERS))
        return self.store.list("all")[0]["id"]

    def service(self, on_learn=None):
        return ReviewService(
            store=self.store,
            mailboxes=("INBOX",),
            controller_url="http://rspamd.invalid:11334",
            controller_password="secret",
            poll_seconds=120,
            max_messages=500,
            username="review",
            password="another-secret",
            on_learn=on_learn,
        )

    def test_index_stores_only_metadata(self):
        service = self.service()
        imap = FakeIMAP()
        with patch("spamhop.review.upstream_imap", side_effect=lambda: fake_connection(imap)):
            self.assertEqual(service.sync_once(), 1)
            self.assertEqual(service.sync_once(), 0)
        row = self.store.list("all")[0]
        self.assertEqual(row["sender"], "Alice <alice@example.com>")
        self.assertEqual(row["recipient"], "Bob <bob@example.com>")
        self.assertEqual(row["subject"], "Hello")
        self.assertEqual(row["message_date"], "Wed, 19 Aug 2026 13:00:00 -0400")
        self.assertEqual(row["mailbox"], "INBOX")
        self.assertEqual(row["uid"], 7)
        self.assertEqual(row["uidvalidity"], 99)
        self.assertNotIn(b"Secret body", open(self.db_path, "rb").read())

    def test_train_spam_fetches_original_on_demand(self):
        row_id = self.add_one()
        callback = []
        service = self.service(on_learn=lambda: callback.append("invalidated"))
        imap = FakeIMAP()
        with patch("spamhop.review.upstream_imap", side_effect=lambda: fake_connection(imap)), \
             patch("spamhop.review.urlopen", return_value=FakeResponse()) as urlopen:
            self.assertEqual(service.label(row_id, "spam"), "Learned as spam")
            self.assertEqual(service.label(row_id, "spam"), "Already trained as spam")
        self.assertEqual(urlopen.call_count, 1)
        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/learnspam"))
        self.assertEqual(request.data, MESSAGE)
        self.assertEqual(self.store.get(row_id)["trained_label"], "spam")
        self.assertEqual(callback, ["invalidated"])
        self.assertNotIn(b"Secret body", open(self.db_path, "rb").read())

    def test_invalid_uidvalidity_prevents_training(self):
        row_id = self.add_one()
        service = self.service()
        imap = FakeIMAP(validity=100)
        with patch("spamhop.review.upstream_imap", side_effect=lambda: fake_connection(imap)), \
             patch("spamhop.review.urlopen") as urlopen:
            with self.assertRaises(RuntimeError):
                service.label(row_id, "ham")
            urlopen.assert_not_called()
        row = self.store.get(row_id)
        self.assertEqual(row["label"], "ham")
        self.assertEqual(row["training_status"], "error")
        self.assertIsNone(row["trained_label"])

    def test_message_identity_change_prevents_training(self):
        row_id = self.add_one()
        service = self.service()
        with patch("spamhop.review.upstream_imap", side_effect=lambda: fake_connection(FakeIMAP())), \
             patch("spamhop.review.fetch_uid", return_value=b"From: Eve <eve@example.net>\r\n\r\nwrong"), \
             patch("spamhop.review.urlopen") as urlopen:
            with self.assertRaises(RuntimeError):
                service.label(row_id, "ham")
            urlopen.assert_not_called()

    def test_no_message_body_in_database_schema(self):
        with sqlite3.connect(self.db_path) as db:
            columns = [col[1] for col in db.execute("PRAGMA table_info(messages)")]
        self.assertNotIn("body", columns)
        self.assertNotIn("raw_message", columns)
        self.assertNotIn("headers", columns)

    def test_filter_and_label(self):
        a = self.add_one()
        self.store.add("INBOX", 99, 8, {
            "sender": "x@example.com", "recipient": "y@example.com",
            "subject": "Subject", "message_date": "", "message_id": "",
        })
        self.store.mark(a, "spam")
        self.store.finish(a, "spam")
        self.assertEqual(len(self.store.list("spam")), 1)
        self.assertEqual(len(self.store.list("ham")), 0)
        self.assertEqual(len(self.store.list("unreviewed")), 1)

    def test_fetch_uid_uses_peek_not_mark_seen(self):
        imap = FakeIMAP()
        self.assertEqual(fetch_uid(imap, 7, headers_only=False), MESSAGE)
        self.assertEqual(select_readonly(imap, "INBOX"), 99)


if __name__ == "__main__":
    unittest.main()
