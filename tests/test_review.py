import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from spamhop.processor.base import ScoreResult
from spamhop.review import (
    LearningRejected, LearningUncertain, ReviewService,
    ReviewStore, rspamd_learning_request,
)


MESSAGE = (
    b"From: Alice <alice@example.com>\r\n"
    b"To: Bob <bob@example.com>\r\n"
    b"Subject: Hello\r\n"
    b"Date: Wed, 19 Aug 2026 13:00:00 -0400\r\n"
    b"Message-ID: <one@example.com>\r\n"
    b"\r\nSecret body that must never persist after expiration.\r\n"
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, length):
        return self.payload


class ReviewQueueTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.now = [1000000]
        base = Path(self.folder.name)
        self.db_path = base / "review.sqlite3"
        self.samples = base / "samples"
        self.store = ReviewStore(
            str(self.db_path), str(self.samples),
            retention_seconds=7 * 86400,
            clock=lambda: self.now[0],
        )

    def tearDown(self):
        self.folder.cleanup()

    def capture(self, message=MESSAGE, score=0.22):
        self.store.record(message, ScoreResult(
            score=score, verdict="ham", engine="ollama:test+rspamd",
        ))
        return self.store.list()[0]["id"]

    def service(self):
        return ReviewService(
            store=self.store,
            controller_url="http://rspamd.invalid:11334",
            controller_password="controller-secret",
            username="review",
            password="review-secret",
        )

    def test_scoring_snapshot_has_requested_fields(self):
        item_id = self.capture()
        row = self.store.get(item_id)
        self.assertEqual(row["sender"], "Alice <alice@example.com>")
        self.assertEqual(row["recipient"], "Bob <bob@example.com>")
        self.assertEqual(row["subject"], "Hello")
        self.assertEqual(row["message_date"], "Wed, 19 Aug 2026 13:00:00 -0400")
        self.assertEqual(row["original_score"], 0.22)
        self.assertEqual(row["original_verdict"], "ham")
        self.assertEqual(row["original_engine"], "ollama:test+rspamd")
        self.assertIsNone(row["label"])

    def test_capture_does_not_extend_retention(self):
        item_id = self.capture()
        first_expiry = self.store.get(item_id)["expires_at"]
        self.now[0] += 86400
        self.capture(score=0.99)
        self.assertEqual(self.store.get(item_id)["expires_at"], first_expiry)
        self.assertEqual(self.store.get(item_id)["original_score"], 0.22)
        self.assertEqual(len(self.store.list()), 1)

    def test_seven_day_expiration_removes_raw_only(self):
        item_id = self.capture()
        self.assertEqual(len(list(self.samples.glob("*.eml"))), 1)
        self.now[0] += 7 * 86400
        self.assertEqual(self.store.prune(), 1)
        self.assertEqual(len(list(self.samples.glob("*.eml"))), 0)
        self.assertEqual(self.store.get(item_id)["payload_state"], "expired")
        self.assertEqual(self.store.get(item_id)["original_score"], 0.22)
        self.assertNotIn(b"Secret body", self.db_path.read_bytes())

    def test_prune_when_restart(self):
        self.capture()
        self.now[0] += 7 * 86400 + 1
        fresh = ReviewStore(
            str(self.db_path), str(self.samples),
            clock=lambda: self.now[0],
        )
        self.assertEqual(fresh.list()[0]["payload_state"], "expired")
        self.assertEqual(len(list(self.samples.glob("*.eml"))), 0)

    def test_max_bytes_does_not_persist_large_email(self):
        base = Path(self.folder.name)
        limited = ReviewStore(
            str(base / "small.sqlite3"), str(base / "other"),
            max_message_bytes=10, clock=lambda: self.now[0],
        )
        self.assertTrue(limited.record(MESSAGE, ScoreResult(
            score=0.9, verdict="spam", engine="test",
        )))
        self.assertEqual(limited.list()[0]["payload_state"], "too-large")
        self.assertEqual(list((base / "other").glob("*.eml")), [])

    def test_learning_is_confirmed_only_on_exact_success(self):
        item_id = self.capture()
        service = self.service()
        with patch("spamhop.review.urlopen", return_value=FakeResponse(
            b'{"success": true}'
        )) as call:
            self.assertEqual(service.label(item_id, "spam"),
                             "Confirmed learned as spam")
        request = call.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/learnspam"))
        self.assertEqual(request.data, MESSAGE)
        row = self.store.get(item_id)
        self.assertEqual(row["trained_label"], "spam")
        self.assertEqual(row["label"], "spam")
        self.assertEqual(row["training_status"], "learned")
        self.assertEqual(row["original_score"], 0.22)
        with patch("spamhop.review.urlopen") as repeat:
            self.assertEqual(service.label(item_id, "spam"),
                             "Already learned as spam")
            repeat.assert_not_called()

    def test_ham_training_on_same_original_score(self):
        item_id = self.capture(score=0.99)
        with patch("spamhop.review.urlopen", return_value=FakeResponse(
            b'{"success": true}'
        )) as call:
            self.service().label(item_id, "ham")
        self.assertEqual(call.call_args.args[0].data, MESSAGE)
        self.assertTrue(call.call_args.args[0].full_url.endswith("/learnham"))
        self.assertEqual(self.store.get(item_id)["original_score"], 0.99)
        self.assertEqual(self.store.get(item_id)["trained_label"], "ham")

    def test_empty_response_is_not_recorded_as_success(self):
        item_id = self.capture()
        with patch("spamhop.review.urlopen", return_value=FakeResponse(b"")):
            self.assertIn("uncertain", self.service().label(item_id, "spam"))
        row = self.store.get(item_id)
        self.assertIsNone(row["trained_label"])
        self.assertEqual(row["training_status"], "uncertain")

    def test_malformed_json_is_uncertain(self):
        item_id = self.capture()
        with patch("spamhop.review.urlopen", return_value=FakeResponse(b"OK")):
            self.service().label(item_id, "ham")
        self.assertEqual(self.store.get(item_id)["training_status"], "uncertain")

    def test_success_must_be_exact_true_not_truthy(self):
        for payload in [b'{}', b'{"success":1}', b'{"success":"true"}']:
            with self.subTest(payload=payload):
                with patch("spamhop.review.urlopen", return_value=FakeResponse(payload)):
                    with self.assertRaises(LearningUncertain):
                        rspamd_learning_request("http://invalid/learnspam", "pw", MESSAGE)

    def test_explicit_rejection_is_error(self):
        item_id = self.capture()
        with patch("spamhop.review.urlopen", return_value=FakeResponse(
            b'{"success": false, "error": "reject"}'
        )):
            self.assertIn("rejected", self.service().label(item_id, "spam"))
        row = self.store.get(item_id)
        self.assertEqual(row["training_status"], "error")
        self.assertIsNone(row["trained_label"])

    def test_connection_drop_is_uncertain(self):
        item_id = self.capture()
        with patch("spamhop.review.urlopen", side_effect=URLError("offline")):
            self.assertIn("uncertain", self.service().label(item_id, "spam"))
        self.assertEqual(self.store.get(item_id)["training_status"], "uncertain")

    def test_expired_sample_saves_feedback_but_does_not_train(self):
        item_id = self.capture()
        self.now[0] += 7 * 86400 + 1
        self.store.prune()
        with patch("spamhop.review.urlopen") as call:
            self.assertIn("unavailable", self.service().label(item_id, "spam"))
            call.assert_not_called()
        self.assertEqual(self.store.get(item_id)["label"], "spam")
        self.assertEqual(self.store.get(item_id)["training_status"], "expired")

    def test_retained_sample_is_integrity_checked(self):
        item_id = self.capture()
        sample = list(self.samples.glob("*.eml"))[0]
        sample.write_bytes(b"altered")
        with patch("spamhop.review.urlopen") as call:
            self.service().label(item_id, "ham")
            call.assert_not_called()
        self.assertEqual(self.store.get(item_id)["training_status"], "error")

    def test_database_never_stores_body(self):
        self.capture()
        columns = [
            row[1] for row in sqlite3.connect(self.db_path).execute(
                "PRAGMA table_info(messages)"
            )
        ]
        self.assertNotIn("body", columns)
        self.assertNotIn("raw_message", columns)
        self.assertNotIn(b"Secret body", self.db_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
