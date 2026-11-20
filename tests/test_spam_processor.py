import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from spamhop.processor.spam import Processor
from spamhop.provider.base import ProviderResult
from spamhop.technical.rspamd import RspamdResult


class FakeProvider:
    engine = "fake:model"

    def __init__(self, score: float = 0.60, fail: bool = False):
        self.value = score
        self.fail = fail
        self.calls = 0

    def score(self, system_prompt: str, message: str) -> ProviderResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        return ProviderResult(score=self.value)


class FakeRspamd:
    def __init__(self, score: float = 0.0, fail: bool = False):
        self.value = score
        self.fail = fail
        self.calls = 0

    def score(self, message: bytes) -> RspamdResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("rspamd down")
        return RspamdResult(
            score=self.value,
            required_score=15.0,
            action="no action",
            symbols=("TEST",),
        )


class SpamProcessorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.prompt_file = Path(self.temp_dir.name) / "prompt.txt"
        self.prompt_file.write_text("Score spam.", encoding="utf-8")
        self.env = patch.dict(
            os.environ,
            {
                "SPAMHOP_LLM_PROVIDER": "fake",
                "SPAMHOP_LLM_BASE_URL": "http://example.invalid",
                "SPAMHOP_LLM_MODEL": "fake-model",
                "SPAMHOP_LLM_TIMEOUT_SECONDS": "1",
                "SPAMHOP_LLM_MAX_BYTES": "65536",
                "SPAMHOP_PROMPT_FILE": str(self.prompt_file),
                "SPAMHOP_SPAM_THRESHOLD": "0.70",
                "SPAMHOP_RSPAMD_ENABLED": "true",
                "SPAMHOP_RSPAMD_URL": "http://rspamd.invalid:11333",
                "SPAMHOP_RSPAMD_TIMEOUT_SECONDS": "1",
                "SPAMHOP_RSPAMD_SCORE_SCALE": "6.0",
                "SPAMHOP_RSPAMD_MAX_ADJUSTMENT": "0.30",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp_dir.cleanup()

    def build_processor(self, provider: FakeProvider, rspamd: FakeRspamd) -> Processor:
        with patch("spamhop.processor.spam.load_provider", return_value=provider), patch(
            "spamhop.processor.spam.RspamdClient", return_value=rspamd
        ):
            return Processor()

    def test_positive_test_bypasses_both_scorers(self):
        provider = FakeProvider()
        rspamd = FakeRspamd()
        processor = self.build_processor(provider, rspamd)

        result = processor.score(
            b"Subject: SpamHop Positive Test\r\nFrom: test@example.com\r\n\r\nhello"
        )

        self.assertEqual(result.score, 1.0)
        self.assertEqual(result.verdict, "spam")
        self.assertEqual(result.engine, "positive-test")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(rspamd.calls, 0)

    def test_positive_rspamd_score_can_push_message_over_threshold(self):
        provider = FakeProvider(score=0.60)
        rspamd = FakeRspamd(score=6.0)
        processor = self.build_processor(provider, rspamd)

        result = processor.score(b"Subject: offer\r\n\r\nhello")

        self.assertIsNotNone(result.score)
        self.assertGreater(result.score, 0.70)
        self.assertEqual(result.verdict, "spam")
        self.assertEqual(result.engine, "fake:model+rspamd")

    def test_negative_rspamd_score_reduces_semantic_score(self):
        provider = FakeProvider(score=0.80)
        rspamd = FakeRspamd(score=-6.0)
        processor = self.build_processor(provider, rspamd)

        result = processor.score(b"Subject: conversation\r\n\r\nhello")

        self.assertIsNotNone(result.score)
        self.assertLess(result.score, 0.70)
        self.assertEqual(result.verdict, "ham")

    def test_rspamd_failure_leaves_llm_score_in_control(self):
        provider = FakeProvider(score=0.80)
        rspamd = FakeRspamd(fail=True)
        processor = self.build_processor(provider, rspamd)

        result = processor.score(b"Subject: test\r\n\r\nhello")

        self.assertEqual(result.score, 0.80)
        self.assertEqual(result.verdict, "spam")
        self.assertEqual(result.engine, "fake:model")

    def test_llm_failure_uses_neutral_baseline_plus_rspamd(self):
        provider = FakeProvider(fail=True)
        rspamd = FakeRspamd(score=12.0)
        processor = self.build_processor(provider, rspamd)

        result = processor.score(b"Subject: test\r\n\r\nhello")

        self.assertIsNotNone(result.score)
        self.assertGreater(result.score, 0.70)
        self.assertEqual(result.verdict, "spam")
        self.assertEqual(result.engine, "rspamd")

    def test_both_scorers_unavailable_returns_unknown(self):
        provider = FakeProvider(fail=True)
        rspamd = FakeRspamd(fail=True)
        processor = self.build_processor(provider, rspamd)

        result = processor.score(b"Subject: test\r\n\r\nhello")

        self.assertIsNone(result.score)
        self.assertEqual(result.verdict, "unknown")
        self.assertEqual(result.engine, "unavailable")


    def test_training_recorder_keeps_original_score_without_rescoring(self):
        provider = FakeProvider(score=0.74)
        rspamd = FakeRspamd(score=0.0)
        processor = self.build_processor(provider, rspamd)
        observed = []
        processor.recorder = lambda message, result: observed.append((message, result))
        message = b"From: alice@example.net\r\nSubject: test\r\n\r\nhello"
        first = processor.score(message)
        second = processor.score(message)
        self.assertEqual(first, second)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(rspamd.calls, 1)
        self.assertEqual(len(observed), 2)
        self.assertEqual(observed[0][0], message)
        self.assertEqual(observed[0][1].score, 0.74)

    def test_training_capture_failure_never_blocks_scoring(self):
        processor = self.build_processor(FakeProvider(score=0.20), FakeRspamd())
        processor.recorder = lambda *_: (_ for _ in ()).throw(OSError("disk full"))
        result = processor.score(b"Subject: normal\r\n\r\nhello")
        self.assertEqual(result.verdict, "ham")

    def test_result_is_cached_by_message(self):
        provider = FakeProvider(score=0.75)
        rspamd = FakeRspamd(score=0.0)
        processor = self.build_processor(provider, rspamd)
        message = b"Subject: ordinary message\r\n\r\nhello"

        first = processor.score(message)
        second = processor.score(message)

        self.assertEqual(first, second)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(rspamd.calls, 1)


if __name__ == "__main__":
    unittest.main()
