from spamhop.processor.base import ScoreResult


class Processor:
    name = "passthrough"

    def score(self, message: bytes) -> ScoreResult:
        return ScoreResult(engine=self.name)
