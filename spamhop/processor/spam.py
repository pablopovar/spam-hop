from email import policy
from email.parser import BytesParser
import hashlib
import logging
import threading

from spamhop.config import load_llm_config, load_scoring_config
from spamhop.processor.base import ScoreResult
from spamhop.provider.loader import load_provider
from spamhop.scoring import combine_scores, technical_adjustment
from spamhop.technical.rspamd import RspamdClient


POSITIVE_TEST_SUBJECT = "SpamHop Positive Test"
LOG = logging.getLogger(__name__)


class Processor:
    name = "spam"

    def __init__(self):
        llm_config = load_llm_config()
        scoring_config = load_scoring_config()

        self.prompt = llm_config.prompt_file.read_text(encoding="utf-8").strip()
        if not self.prompt:
            raise RuntimeError("SpamHop prompt file is empty")

        self.max_bytes = llm_config.max_bytes
        self.threshold = scoring_config.spam_threshold
        self.rspamd_score_scale = scoring_config.rspamd_score_scale
        self.rspamd_max_adjustment = scoring_config.rspamd_max_adjustment
        self.provider = load_provider(
            llm_config.provider,
            base_url=llm_config.base_url,
            api_key=llm_config.api_key,
            model=llm_config.model,
            timeout_seconds=llm_config.timeout_seconds,
        )
        self.rspamd = (
            RspamdClient(
                base_url=scoring_config.rspamd_url,
                timeout_seconds=scoring_config.rspamd_timeout_seconds,
            )
            if scoring_config.rspamd_enabled
            else None
        )
        self._cache: dict[str, ScoreResult] = {}
        self._cache_lock = threading.Lock()
        self.recorder = None

    def clear_cache(self):
        """Invalidate previous scores when the local statistical model changes."""
        with self._cache_lock:
            self._cache.clear()

    def score(self, message: bytes) -> ScoreResult:
        """Compute a verdict, then capture it without changing classification."""
        result = self._score(message)
        if self.recorder is not None:
            try:
                self.recorder(message, result)
            except Exception:
                # Training storage must never block ordinary IMAP retrieval.
                LOG.exception("Training capture failed")
        return result

    def _score(self, message: bytes) -> ScoreResult:
        parsed = BytesParser(policy=policy.default).parsebytes(message, headersonly=True)
        subject = str(parsed.get("Subject", "")).strip()

        if subject == POSITIVE_TEST_SUBJECT:
            return ScoreResult(
                score=1.0,
                verdict="spam",
                engine="positive-test",
            )

        digest = hashlib.sha256(message).hexdigest()
        with self._cache_lock:
            cached = self._cache.get(digest)
        if cached is not None:
            return cached

        llm_score: float | None = None
        llm_engine: str | None = None
        try:
            message_text = message[: self.max_bytes].decode("utf-8", "replace")
            provider_result = self.provider.score(self.prompt, message_text)
            llm_score = provider_result.score
            llm_engine = self.provider.engine
        except Exception as exc:
            LOG.warning("semantic scorer unavailable: %s", exc)

        rspamd_score: float | None = None
        adjustment = 0.0
        if self.rspamd is not None:
            try:
                technical = self.rspamd.score(message)
                rspamd_score = technical.score
                adjustment = technical_adjustment(
                    technical.score,
                    scale=self.rspamd_score_scale,
                    max_adjustment=self.rspamd_max_adjustment,
                )
                LOG.info(
                    "rspamd score=%.3f adjustment=%+.3f action=%s symbols=%s",
                    technical.score,
                    adjustment,
                    technical.action,
                    ",".join(technical.symbols[:12]),
                )
            except Exception as exc:
                LOG.warning("technical scorer unavailable: %s", exc)

        if llm_score is None and rspamd_score is None:
            result = ScoreResult(
                score=None,
                verdict="unknown",
                engine="unavailable",
            )
        else:
            semantic_baseline = 0.5 if llm_score is None else llm_score
            final_score = combine_scores(semantic_baseline, adjustment)
            verdict = "spam" if final_score >= self.threshold else "ham"

            engines: list[str] = []
            if llm_engine is not None:
                engines.append(llm_engine)
            if rspamd_score is not None:
                engines.append("rspamd")
            result = ScoreResult(
                score=final_score,
                verdict=verdict,
                engine="+".join(engines) or "spamhop",
            )

        with self._cache_lock:
            self._cache[digest] = result

        return result
