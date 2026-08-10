import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from spamhop.processor.base import ScoreResult


class ProcessorHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, processor):
        self.processor = processor
        super().__init__(address, ProcessorHandler)


class ProcessorHandler(BaseHTTPRequestHandler):
    server: ProcessorHTTPServer

    def do_POST(self):
        if self.path != "/score":
            self.send_error(404)
            return

        length_header = self.headers.get("Content-Length")
        if length_header is None:
            self.send_error(411)
            return

        try:
            length = int(length_header)
        except ValueError:
            self.send_error(400)
            return

        if length < 0:
            self.send_error(400)
            return

        message = self.rfile.read(length)
        if len(message) != length:
            self.send_error(400)
            return

        try:
            result = self.server.processor.score(message)
            if not isinstance(result, ScoreResult):
                raise TypeError("processor must return ScoreResult")
        except Exception as exc:
            self.send_error(500, str(exc))
            return

        payload = json.dumps(
            asdict(result),
            separators=(",", ":"),
        ).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return
