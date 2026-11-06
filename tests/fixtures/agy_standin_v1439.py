# v1.439.0: a stand-in Gemini API on 127.0.0.1 (never Google) for the opt-in live
# Antigravity test (tests/test_build_app_antigravity_live_v1439.py): logs each request and
# answers from a script. Each answer is a list of parts, or
# {"status": N, "error": {...}} for an HTTP error, or {"delay": s, "parts": [...]}.
# The answers file is re-read on every request, so a recorder can switch it.
import http.server
import json
import sys
import threading
import time

port, logpath, scriptpath = int(sys.argv[1]), sys.argv[2], sys.argv[3]
state = {"n": 0, "script": None}
lock = threading.Lock()


def answers():
    with open(scriptpath, encoding="utf-8") as fh:
        return json.load(fh)


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _body(self):
        n = int(self.headers.get("content-length") or 0)
        return self.rfile.read(n).decode("utf-8", "replace") if n else ""

    def do_GET(self):
        with open(logpath, "a", encoding="utf-8") as f:
            f.write(json.dumps({"method": "GET", "path": self.path}) + "\n")
        self.send_response(404)
        self.send_header("content-length", "0")
        self.end_headers()

    def do_POST(self):
        body = self._body()
        with open(logpath, "a", encoding="utf-8") as f:
            f.write(json.dumps({"method": "POST", "path": self.path.split("?")[0], "body": body[:200000]}) + "\n")
        if "flash-lite" in self.path:
            ans = [{"text": "Probe title"}]
        else:
            with lock:
                A = answers()
                key = json.dumps(A)
                if key != state["script"]:
                    state["script"], state["n"] = key, 0
                i = min(state["n"], len(A) - 1)
                state["n"] += 1
                ans = A[i]
        if isinstance(ans, dict) and ans.get("delay"):
            time.sleep(float(ans["delay"]))
        if isinstance(ans, dict) and ans.get("status"):
            data = json.dumps({"error": ans.get("error") or {}}).encode()
            self.send_response(int(ans["status"]))
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        parts = ans.get("parts") if isinstance(ans, dict) else ans
        cand = {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP", "index": 0}],
                "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 7, "totalTokenCount": 127},
                "modelVersion": "stand-in"}
        if "stream" in self.path:
            data = ("data: " + json.dumps(cand) + "\r\n\r\n").encode()
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
        else:
            data = json.dumps(cand).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
