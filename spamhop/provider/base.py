from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ProviderResult:
    score: float


class Provider(Protocol):
    engine: str

    def score(self, system_prompt: str, message: str) -> ProviderResult:
        ...
