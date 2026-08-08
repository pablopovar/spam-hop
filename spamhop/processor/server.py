from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ProcessorHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, processor):
        self.processor = processor
        super().__init__(address, ProcessorHandler)


class ProcessorHandler(BaseHTTPRequestHandler):
    server: ProcessorHTTPServer

    def do_POST(self):
        if self.path != "/process":
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

        message = self.rfile.read(length)

        try:
            result = self.server.processor.process(message)
        except Exception as exc:
            self.send_error(500, str(exc))
            return

        if result is None:
            self.send_response(204)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "message/rfc822")
        self.send_header("Content-Length", str(len(result)))
        self.end_headers()
        self.wfile.write(result)

    def log_message(self, format, *args):
        return
