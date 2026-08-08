class Processor:
    name = "passthrough"

    def process(self, message: bytes) -> bytes | None:
        return None
