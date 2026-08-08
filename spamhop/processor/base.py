from typing import Protocol


class Processor(Protocol):
    name: str

    def process(self, message: bytes) -> bytes | None:
        """
        Process one complete RFC message representation.

        Return:
            None:
                Representation is unchanged.

            bytes:
                Replacement representation to expose downstream.
        """
        ...
