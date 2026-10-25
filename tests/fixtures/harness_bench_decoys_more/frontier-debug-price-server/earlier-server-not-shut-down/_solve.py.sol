"""Review decoy: the right code fix, but an earlier server is never shut down.

The agent reproduces the bug on server A (the original code), fixes pricing.py,
starts server B on the fixed code WITHOUT stopping A, replays clean against B
and stops only B with /shutdown. server.log then reads
``listening <A>``, ``listening <B>``, ``stopped``: the last line is ``stopped``
although A never was. (An agent's run would leave A running; this script ends
A's process tree itself at the very end, WITHOUT /shutdown, so the pin leaves
nothing running. The log is the same either way: A did not end with /shutdown.)
"""
import json
import os
import shutil
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


def start():
    before = len(listening_lines())
    proc = subprocess.Popen([sys.executable, "priceserver.py", "--port", "0"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 30
    while len(listening_lines()) <= before:
        if time.monotonic() > deadline or proc.poll() is not None:
            raise SystemExit("the server did not start")
        time.sleep(0.05)
    return proc, int(listening_lines()[-1])


def end_tree(proc):
    """End the process tree of a server THIS script started (not /shutdown)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        proc.kill()
    proc.wait(timeout=30)


a, _port_a = start()
b = None
try:
    subprocess.run([sys.executable, "replay.py"], capture_output=True, text=True, timeout=120)
    shutil.copyfile("fixed_pricing.txt", "pricing.py")
    os.remove("fixed_pricing.txt")
    b, port_b = start()  # "restart the server on your fixed code" -- but A was never stopped
    replay = subprocess.run([sys.executable, "replay.py"], capture_output=True, text=True, timeout=120)
    if replay.returncode != 0:
        raise SystemExit("the replay still has mismatches")
    with urllib.request.urlopen(f"http://127.0.0.1:{port_b}/shutdown", timeout=10) as resp:
        json.loads(resp.read().decode("utf-8"))
    b.wait(timeout=30)
finally:
    end_tree(a)
    if b is not None:
        end_tree(b)
