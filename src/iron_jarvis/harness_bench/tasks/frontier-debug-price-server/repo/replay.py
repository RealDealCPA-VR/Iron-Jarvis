"""Replay the recorded traffic against the RUNNING price server (see README.md).

    python replay.py

It reads the server's port from server.log (the last ``listening <port>``
line), sends every request in traffic.jsonl in order, compares each answer
with the recorded one, and writes replay_report.json. It exits with 1 when any
answer was different.
"""
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TRAFFIC = os.path.join(HERE, "traffic.jsonl")
LOG = os.path.join(HERE, "server.log")
REPORT = os.path.join(HERE, "replay_report.json")
#: How many mismatches the report shows in full.
SHOWN = 5


def server_port():
    try:
        with open(LOG, encoding="utf-8") as fh:
            lines = [line.strip() for line in fh if line.strip()]
    except FileNotFoundError:
        raise SystemExit("server.log not found: start the server first (python priceserver.py --port 0)") from None
    for line in reversed(lines):
        if line == "stopped":
            raise SystemExit("the server's last run has stopped: start it again (python priceserver.py --port 0)")
        if line.startswith("listening "):
            return int(line.split()[1])
    raise SystemExit("server.log has no 'listening' line: start the server first")


def get(port, path):
    """(status, parsed JSON body) of GET ``path`` on the server."""
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body) if body else None


def main():
    port = server_port()
    try:
        _, version = get(port, "/debug/version")
    except OSError as exc:
        raise SystemExit(f"could not reach the server on port {port} ({exc}): is it running?") from None
    requests = 0
    mismatches = []
    with open(TRAFFIC, encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            entry = json.loads(line)
            requests += 1
            status, body = get(port, entry["request"])
            if status != entry["status"] or body != entry["response"]:
                mismatches.append({
                    "line": number,
                    "request": entry["request"],
                    "expected": {"status": entry["status"], "body": entry["response"]},
                    "got": {"status": status, "body": body},
                })
    report = {
        "server_version": version.get("version"),
        "requests": requests,
        "mismatches": len(mismatches),
        "first_mismatches": mismatches[:SHOWN],
    }
    with open(REPORT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, indent=2)
        fh.write("\n")
    print(f"replayed {requests} requests: {len(mismatches)} wrong answers (see replay_report.json)")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
