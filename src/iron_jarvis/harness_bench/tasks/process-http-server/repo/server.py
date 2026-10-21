"""A tiny local status server.

Run it from this folder:  python server.py

It listens on 127.0.0.1 on a free port, writes that port number to
`server.port`, and writes a line to `server.log` for every request.

    GET /status    -> a JSON summary of inventory.csv
    GET /shutdown  -> stops the server
"""
import csv
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "server.log"
STARTED = time.strftime("%Y%m%dT%H%M%S") + f"-{time.perf_counter_ns() % 1000000:06d}"


def log(line):
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def status():
    raw = (HERE / "inventory.csv").read_bytes()
    rows = list(csv.DictReader(raw.decode("utf-8").splitlines()))
    return {
        "ok": True,
        "items": len(rows),
        "units": sum(int(row["units"]) for row in rows),
        "checksum": hashlib.sha256(raw).hexdigest()[:16],
        "started": STARTED,
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        log(f"GET {self.path}")
        if self.path == "/status":
            self._send(200, json.dumps(status()).encode("utf-8"), "application/json")
        elif self.path == "/shutdown":
            self._send(200, b"stopping\n", "text/plain")
            threading.Thread(target=self.server.shutdown).start()
        else:
            self._send(404, b"not found\n", "text/plain")

    def _send(self, code, body, kind):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    log(f"started {STARTED}")
    (HERE / "server.port").write_text(str(server.server_address[1]), encoding="utf-8")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        log("stopped")


if __name__ == "__main__":
    main()
