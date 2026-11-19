from dataclasses import dataclass
from pathlib import Path
import os


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


@dataclass(frozen=True, slots=True)
class LLMConfig:
    provider: str
    base_url: str
    api_key: str
    model: str
    prompt_file: Path
    timeout_seconds: float
    max_bytes: int


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    spam_threshold: float
    rspamd_enabled: bool
    rspamd_url: str
    rspamd_timeout_seconds: float
    rspamd_score_scale: float
    rspamd_max_adjustment: float


def load_llm_config() -> LLMConfig:
    timeout_seconds = float(required_env("SPAMHOP_LLM_TIMEOUT_SECONDS"))
    max_bytes = int(required_env("SPAMHOP_LLM_MAX_BYTES"))

    if timeout_seconds <= 0:
        raise RuntimeError("SPAMHOP_LLM_TIMEOUT_SECONDS must be greater than zero")
    if max_bytes <= 0:
        raise RuntimeError("SPAMHOP_LLM_MAX_BYTES must be greater than zero")

    prompt_file = Path(required_env("SPAMHOP_PROMPT_FILE"))
    if not prompt_file.is_file():
        raise RuntimeError(f"prompt file not found: {prompt_file}")

    return LLMConfig(
        provider=required_env("SPAMHOP_LLM_PROVIDER"),
        base_url=required_env("SPAMHOP_LLM_BASE_URL"),
        api_key=os.getenv("SPAMHOP_LLM_API_KEY", "").strip(),
        model=required_env("SPAMHOP_LLM_MODEL"),
        prompt_file=prompt_file,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
    )


def load_scoring_config() -> ScoringConfig:
    spam_threshold = float(os.getenv("SPAMHOP_SPAM_THRESHOLD", "0.70"))
    rspamd_enabled = env_bool("SPAMHOP_RSPAMD_ENABLED", True)
    rspamd_url = os.getenv("SPAMHOP_RSPAMD_URL", "http://rspamd:11333").strip()
    rspamd_timeout_seconds = float(
        os.getenv("SPAMHOP_RSPAMD_TIMEOUT_SECONDS", "15")
    )
    rspamd_score_scale = float(os.getenv("SPAMHOP_RSPAMD_SCORE_SCALE", "6.0"))
    rspamd_max_adjustment = float(
        os.getenv("SPAMHOP_RSPAMD_MAX_ADJUSTMENT", "0.30")
    )

    if not 0 < spam_threshold < 1:
        raise RuntimeError("SPAMHOP_SPAM_THRESHOLD must be between zero and one")
    if rspamd_enabled and not rspamd_url:
        raise RuntimeError("SPAMHOP_RSPAMD_URL is required when Rspamd is enabled")
    if rspamd_timeout_seconds <= 0:
        raise RuntimeError("SPAMHOP_RSPAMD_TIMEOUT_SECONDS must be greater than zero")
    if rspamd_score_scale <= 0:
        raise RuntimeError("SPAMHOP_RSPAMD_SCORE_SCALE must be greater than zero")
    if not 0 <= rspamd_max_adjustment <= 0.5:
        raise RuntimeError(
            "SPAMHOP_RSPAMD_MAX_ADJUSTMENT must be between zero and 0.5"
        )

    return ScoringConfig(
        spam_threshold=spam_threshold,
        rspamd_enabled=rspamd_enabled,
        rspamd_url=rspamd_url.rstrip("/"),
        rspamd_timeout_seconds=rspamd_timeout_seconds,
        rspamd_score_scale=rspamd_score_scale,
        rspamd_max_adjustment=rspamd_max_adjustment,
    )
