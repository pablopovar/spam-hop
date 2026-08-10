from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ScoreResult:
    score: float | None = None
    verdict: str | None = None
    engine: str | None = None


class Processor(Protocol):
    name: str

    def score(self, message: bytes) -> ScoreResult:
        """Score one complete RFC message representation."""
        ...
