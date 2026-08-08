import logging
import os
import signal
import subprocess
import threading

from spamhop.processor.loader import load_processor
from spamhop.processor.server import ProcessorHTTPServer


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
        server.shutdown()
        server.server_close()
