import logging
import os
from pathlib import Path
import signal
import subprocess
import threading

from spamhop.processor.loader import load_processor
from spamhop.processor.server import ProcessorHTTPServer
from spamhop.review import ReviewService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

log = logging.getLogger("spamhop")


def parse_address(value: str) -> tuple[str, int]:
    host, port = value.rsplit(":", 1)
    return host, int(port)


def main() -> int:
    processor_spec = os.getenv(
        "SPAMHOP_PROCESSOR",
        "spamhop.processor.passthrough:Processor",
    )

    processor_address = os.getenv(
        "SPAMHOP_PROCESSOR_LISTEN",
        "127.0.0.1:18080",
    )

    transport_binary = os.getenv(
        "SPAMHOP_TRANSPORT_BINARY",
        "/usr/local/bin/spamhop-transport",
    )

    processor = load_processor(processor_spec)

    server = ProcessorHTTPServer(
        parse_address(processor_address),
        processor,
    )

    thread = threading.Thread(
        target=server.serve_forever,
        name="processor",
        daemon=True,
    )

    thread.start()

    review = None
    training_enabled = os.getenv("SPAMHOP_TRAINING_ENABLED", "false").lower() in (
        "1", "true", "yes", "on"
    )
    review_enabled = (
        os.getenv("SPAMHOP_REVIEW_ENABLED", "false").lower()
        in ("1", "true", "yes", "on")
    ) or training_enabled
    stored_review = Path(os.getenv(
        "SPAMHOP_REVIEW_DB", "/data/review.sqlite3"
    )).is_file()
    if review_enabled or stored_review:
        review = ReviewService.from_env(require_credentials=review_enabled)
        if training_enabled:
            processor.recorder = review.store.record
        address = (
            parse_address(os.getenv("SPAMHOP_REVIEW_LISTEN", "0.0.0.0:18081"))
            if review_enabled else None
        )
        review.start(address)
        log.info(
            "review UI=%s; seven-day training capture=%s; cleanup active",
            review_enabled, training_enabled,
        )

    log.info("processor=%s", processor.name)

    transport = subprocess.Popen([transport_binary])

    def shutdown(signum, frame):
        if transport.poll() is None:
            transport.send_signal(signum)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        return transport.wait()
    finally:
        if review is not None:
            review.close()
        server.shutdown()
        server.server_close()
