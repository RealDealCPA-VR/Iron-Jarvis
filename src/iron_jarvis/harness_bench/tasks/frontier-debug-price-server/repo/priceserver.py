"""The price server (README.md says how to run and use it).

    python priceserver.py --port 0

It listens on 127.0.0.1 (port 0 = any free port), appends ``listening <port>``
to server.log and prints the same line, and runs until it gets GET /shutdown.
"""
import argparse
import hashlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pricing

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "server.log")
SOURCE_FILES = ("priceserver.py", "pricing.py", "catalog.py")


def source_version(folder=HERE):
    """sha256 of the server's source files: their contents one after another,
    in SOURCE_FILES order, with CRLF line endings read as LF."""
    digest = hashlib.sha256()
    for name in SOURCE_FILES:
        with open(os.path.join(folder, name), "rb") as fh:
            digest.update(fh.read().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


#: The version of the code this process started with (read once, at start).
VERSION = source_version()


def log(line):
    with open(LOG, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")


class Handler(BaseHTTPRequestHandler):
    server_version = "priceserver/1.0"

    def log_message(self, format, *args):  # noqa: A002  (keep the console quiet)
        pass

    def _send(self, status, body):
        data = json.dumps(body, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        url = urlsplit(self.path)
        query = {key: values[-1] for key, values in parse_qs(url.query, keep_blank_values=True).items()}
        if url.path == "/price":
            if "sku" not in query:
                return self._send(400, {"error": "sku is required"})
            try:
                answer = pricing.price(
                    query["sku"],
                    query.get("tier", "regular"),
                    query.get("currency", "USD"),
                    query.get("qty", "1"),
                )
            except pricing.PriceError as exc:
                return self._send(400, {"error": str(exc)})
            return self._send(200, answer)
        if url.path == "/debug/cache":
            return self._send(200, pricing.cache_info())
        if url.path == "/debug/version":
            return self._send(200, {"version": VERSION, "files": list(SOURCE_FILES)})
        if url.path == "/shutdown":
            self._send(200, {"stopping": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return None
        return self._send(404, {"error": f"no such path: {url.path}"})


def main(argv=None):
    parser = argparse.ArgumentParser(description="The price server.")
    parser.add_argument("--port", type=int, default=0, help="the port to listen on (0 = any free port)")
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    port = server.server_address[1]
    log(f"listening {port}")
    print(f"listening {port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
    log("stopped")
    print("stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
