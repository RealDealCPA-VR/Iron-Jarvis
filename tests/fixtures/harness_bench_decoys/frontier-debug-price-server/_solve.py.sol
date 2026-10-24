"""Reference run: start the fixed server on port 0, replay the traffic until it is
clean, stop the server with GET /shutdown and wait for it to exit."""
import json
import os
import subprocess
import sys
import time
import urllib.request

LOG = "server.log"


def listening_lines():
    if not os.path.exists(LOG):
        return []
    with open(LOG, encoding="utf-8") as fh:
        return [line.split()[1] for line in fh if line.startswith("listening ")]


before = len(listening_lines())
proc = subprocess.Popen([sys.executable, "priceserver.py", "--port", "0"], stdout=subprocess.DEVNULL)
try:
    deadline = time.monotonic() + 30
    while len(listening_lines()) <= before:
        if time.monotonic() > deadline or proc.poll() is not None:
            raise SystemExit("the server did not start")
        time.sleep(0.05)
    port = int(listening_lines()[-1])
    replay = subprocess.run([sys.executable, "replay.py"], capture_output=True, text=True, timeout=120)
    print(replay.stdout, replay.stderr)
    if replay.returncode != 0:
        raise SystemExit("the replay still has mismatches")
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/shutdown", timeout=10) as resp:
        json.loads(resp.read().decode("utf-8"))
    proc.wait(timeout=30)
finally:
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)
with open(LOG, encoding="utf-8") as fh:
    assert fh.read().splitlines()[-1] == "stopped"
