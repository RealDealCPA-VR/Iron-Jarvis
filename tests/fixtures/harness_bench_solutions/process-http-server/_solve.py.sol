"""Reference solution: start the server, ask it for /status, stop it."""
import os
import subprocess
import sys
import time
import urllib.request

proc = subprocess.Popen([sys.executable, "server.py"])
try:
    deadline = time.monotonic() + 30
    while not os.path.exists("server.port"):
        if time.monotonic() > deadline or proc.poll() is not None:
            raise SystemExit("the server did not start")
        time.sleep(0.05)
    port = open("server.port", encoding="utf-8").read().strip()
    base = f"http://127.0.0.1:{port}"
    with urllib.request.urlopen(base + "/status", timeout=10) as resp:
        body = resp.read()
    with open("status.json", "wb") as fh:
        fh.write(body)
    with urllib.request.urlopen(base + "/shutdown", timeout=10) as resp:
        resp.read()
    proc.wait(timeout=30)
finally:
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)
