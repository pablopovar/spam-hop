import math


def clamp_score(value: float) -> float:
    return max(0.0, min(1.0, value))


def technical_adjustment(
    raw_score: float,
    *,
    scale: float,
    max_adjustment: float,
) -> float:
    """Normalize Rspamd's signed score into a bounded signed adjustment."""
    return max_adjustment * math.tanh(raw_score / scale)


def combine_scores(semantic_score: float, adjustment: float) -> float:
    return clamp_score(semantic_score + adjustment)
