"""Reference solution: start the fixed watcher, feed it, stop it with STOP."""
import os
import shutil
import subprocess
import sys
import time


def wait_for(line, proc, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if os.path.exists("watcher.log"):
            with open("watcher.log", encoding="utf-8") as fh:
                if line in [x.strip() for x in fh]:
                    return
        if proc.poll() is not None and line != "stopped":
            raise SystemExit(f"the watcher exited before {line!r}")
        time.sleep(0.05)
    raise SystemExit(f"timed out waiting for {line!r}")


proc = subprocess.Popen([sys.executable, "watcher.py"])
try:
    deadline = time.monotonic() + 30
    while not os.path.exists("watcher.log"):
        if time.monotonic() > deadline or proc.poll() is not None:
            raise SystemExit("the watcher did not start")
        time.sleep(0.05)
    for name in ("a.csv", "b.csv", "c.csv"):
        shutil.copyfile(os.path.join("incoming", name), os.path.join("inbox", name))
        wait_for(f"processed {name}", proc)
    open(os.path.join("inbox", "STOP"), "w").close()
    proc.wait(timeout=30)
finally:
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)
