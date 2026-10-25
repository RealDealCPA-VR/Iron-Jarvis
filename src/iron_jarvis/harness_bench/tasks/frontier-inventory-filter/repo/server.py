"""The inventory page: a small web server (Python standard library only).

    python server.py [--port N]     serve on http://127.0.0.1:N/ (N = 0: any free port)

It prints ``listening http://127.0.0.1:<port>/`` once it is ready. It serves
the files in ``static/`` and one API, ``GET /api/items``: the items in
``data/items.json``. Stop it with Ctrl+C (or by stopping the process you
started).
"""

import argparse
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
DATA = os.path.join(HERE, "data", "items.json")


def load_items():
    with open(DATA, encoding="utf-8") as fh:
        return json.load(fh)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC, **kwargs)

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/api/items":
            body = json.dumps(load_items()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):  # quiet
        pass


def create_server(host="127.0.0.1", port=0):
    """A server bound to ``host:port`` (not started: call serve_forever)."""
    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the inventory page.")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    server = create_server(port=args.port)
    print(f"listening http://127.0.0.1:{server.server_address[1]}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
