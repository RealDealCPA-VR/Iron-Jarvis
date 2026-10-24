# IJ-BENCH-HIDDEN-CHECK frontier-debug-price-server
"""Hidden check: the final replay ran clean against the final code and the
server was stopped with /shutdown; the fixed server answers OTHER traffic
(currencies, quantities, spellings) right, computes each distinct price exactly
once, also under simultaneous requests; the recorded files are unchanged."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CRLF = bytes([13, 10])
LF = bytes([10])
SOURCE_FILES = ("priceserver.py", "pricing.py", "catalog.py")
VISIBLE_REQUESTS = 41
UNCHANGED = {
    "catalog.py": "21f9e7d69c548924e165a5e9bbb9e83a506e439d91c1ad5feb46173b66905118",
    "replay.py": "0608b5daca312dac906614f1743aece96d155bce5e98af33cc9ab5572c1de750",
    "traffic.jsonl": "e56f97708cf30701545c2343204829fa295fbb80c467ae849b94beea3bab5891"
}

#: [request path, expected answer] — sent in this order to a fresh server.
HIDDEN_TRAFFIC = [
    ["/price?sku=A100&tier=gold&currency=USD&qty=1", {"currency": "USD", "qty": 1, "sku": "A100", "tier": "gold", "total": "21.91", "unit": "21.91"}],
    ["/price?sku=A100&tier=gold&currency=EUR&qty=1", {"currency": "EUR", "qty": 1, "sku": "A100", "tier": "gold", "total": "20.19", "unit": "20.19"}],
    ["/price?sku=A100&tier=gold&currency=EUR&qty=12", {"currency": "EUR", "qty": 12, "sku": "A100", "tier": "gold", "total": "234.96", "unit": "19.58"}],
    ["/price?sku=A100&tier=gold&currency=JPY&qty=1", {"currency": "JPY", "qty": 1, "sku": "A100", "tier": "gold", "total": "3317", "unit": "3317"}],
    ["/price?sku=A100&tier=gold&currency=GBP&qty=50", {"currency": "GBP", "qty": 50, "sku": "A100", "tier": "gold", "total": "802.00", "unit": "16.04"}],
    ["/price?sku=A100&tier=gold&currency=USD&qty=200", {"currency": "USD", "qty": 200, "sku": "A100", "tier": "gold", "total": "3944.00", "unit": "19.72"}],
    ["/price?sku=a100&tier=Gold&currency=eur&qty=012", {"currency": "EUR", "qty": 12, "sku": "A100", "tier": "gold", "total": "234.96", "unit": "19.58"}],
    ["/price?sku=A100&tier=gold&currency=EUR&qty=12", {"currency": "EUR", "qty": 12, "sku": "A100", "tier": "gold", "total": "234.96", "unit": "19.58"}],
    ["/price?sku=e512&tier=REGULAR&currency=JPY", {"currency": "JPY", "qty": 1, "sku": "E512", "tier": "regular", "total": "8212", "unit": "8212"}],
    ["/price?sku=e512&tier=gold&currency=EUR&qty=120", {"currency": "EUR", "qty": 120, "sku": "E512", "tier": "gold", "total": "4908.00", "unit": "40.90"}],
    ["/price?sku=D405&tier=+gold+&currency=+GBP+", {"currency": "GBP", "qty": 1, "sku": "D405", "tier": "gold", "total": "6.89", "unit": "6.89"}],
    ["/price?sku=a100&tier=gold&currency=eur&qty=3", {"currency": "EUR", "qty": 3, "sku": "A100", "tier": "gold", "total": "60.57", "unit": "20.19"}],
    ["/price?sku=e512&tier=gold&currency=chf&qty=01", {"currency": "CHF", "qty": 1, "sku": "E512", "tier": "gold", "total": "42.23", "unit": "42.23"}],
    ["/price?sku=D405&currency=CHF&qty=49", {"currency": "CHF", "qty": 49, "sku": "D405", "tier": "regular", "total": "418.46", "unit": "8.54"}],
    ["/price?sku=B220&currency=chf&qty=200", {"currency": "CHF", "qty": 200, "sku": "B220", "tier": "regular", "total": "30094.00", "unit": "150.47"}],
    ["/price?sku=A100&tier=silver&currency=CHF&qty=10", {"currency": "CHF", "qty": 10, "sku": "A100", "tier": "silver", "total": "203.00", "unit": "20.30"}],
    ["/price?sku=B220&currency=EUR&qty=120", {"currency": "EUR", "qty": 120, "sku": "B220", "tier": "regular", "total": "19432.80", "unit": "161.94"}],
    ["/price?sku=D405&tier=+gold+&currency=EUR&qty=9", {"currency": "EUR", "qty": 9, "sku": "D405", "tier": "gold", "total": "72.63", "unit": "8.07"}],
    ["/price?sku=E512&tier=platinum&currency=EUR&qty=10", {"currency": "EUR", "qty": 10, "sku": "E512", "tier": "platinum", "total": "397.50", "unit": "39.75"}],
    ["/price?sku=E512&tier=platinum&currency=+GBP+&qty=50", {"currency": "GBP", "qty": 50, "sku": "E512", "tier": "platinum", "total": "1628.00", "unit": "32.56"}],
    ["/price?sku=C310&tier=GOLD&currency=EUR&qty=200", {"currency": "EUR", "qty": 200, "sku": "C310", "tier": "gold", "total": "10580.00", "unit": "52.90"}],
    ["/price?sku=A100&tier=regular&qty=200", {"currency": "USD", "qty": 200, "sku": "A100", "tier": "regular", "total": "4482.00", "unit": "22.41"}],
    ["/price?sku=B220&tier=gold&currency=+USD+&qty=3", {"currency": "USD", "qty": 3, "sku": "B220", "tier": "gold", "total": "498.96", "unit": "166.32"}],
    ["/price?sku=d405&currency=USD&qty=120", {"currency": "USD", "qty": 120, "sku": "D405", "tier": "regular", "total": "1110.00", "unit": "9.25"}],
    ["/price?sku=E512&tier=Platinum&currency=gbp&qty=49", {"currency": "GBP", "qty": 49, "sku": "E512", "tier": "platinum", "total": "1664.04", "unit": "33.96"}],
    ["/price?sku=D405&tier=+silver+&qty=250", {"currency": "USD", "qty": 250, "sku": "D405", "tier": "silver", "total": "2127.50", "unit": "8.51"}],
    ["/price?sku=B220&tier=Platinum&currency=USD&qty=3", {"currency": "USD", "qty": 3, "sku": "B220", "tier": "platinum", "total": "464.94", "unit": "154.98"}],
    ["/price?sku=C310&tier=silver&currency=EUR&qty=049", {"currency": "EUR", "qty": 49, "sku": "C310", "tier": "silver", "total": "3015.95", "unit": "61.55"}],
    ["/price?sku=E512&tier=gold&currency=GBP", {"currency": "GBP", "qty": 1, "sku": "E512", "tier": "gold", "total": "37.58", "unit": "37.58"}],
    ["/price?sku=C310&currency=jpy&qty=0120", {"currency": "JPY", "qty": 120, "sku": "C310", "tier": "regular", "total": "1224720", "unit": "10206"}],
    ["/price?sku=A100&tier=SILVER&currency=jpy&qty=03", {"currency": "JPY", "qty": 3, "sku": "A100", "tier": "silver", "total": "10743", "unit": "3581"}],
    ["/price?sku=c310&tier=silver&currency=JPY&qty=250", {"currency": "JPY", "qty": 250, "sku": "C310", "tier": "silver", "total": "2345750", "unit": "9383"}],
    ["/price?sku=c310&tier=regular&currency=JPY&qty=9", {"currency": "JPY", "qty": 9, "sku": "C310", "tier": "regular", "total": "98766", "unit": "10974"}],
    ["/price?sku=B220&tier=platinum&qty=250", {"currency": "USD", "qty": 250, "sku": "B220", "tier": "platinum", "total": "34870.00", "unit": "139.48"}],
    ["/price?sku=A100&tier=silver&currency=JPY&qty=12", {"currency": "JPY", "qty": 12, "sku": "A100", "tier": "silver", "total": "41676", "unit": "3473"}],
    ["/price?sku=C310&tier=Silver&currency=EUR&qty=200", {"currency": "EUR", "qty": 200, "sku": "C310", "tier": "silver", "total": "11422.00", "unit": "57.11"}],
    ["/price?sku=E512&currency=usd&qty=12", {"currency": "USD", "qty": 12, "sku": "E512", "tier": "regular", "total": "631.44", "unit": "52.62"}],
    ["/price?sku=+E512+&tier=silver&currency=EUR&qty=12", {"currency": "EUR", "qty": 12, "sku": "E512", "tier": "silver", "total": "552.72", "unit": "46.06"}],
    ["/price?sku=+E512+&tier=+gold+&currency=GBP&qty=120", {"currency": "GBP", "qty": 120, "sku": "E512", "tier": "gold", "total": "4194.00", "unit": "34.95"}],
    ["/price?sku=A100&tier=gold&currency=JPY&qty=10", {"currency": "JPY", "qty": 10, "sku": "A100", "tier": "gold", "total": "32170", "unit": "3217"}],
    ["/price?sku=B220&tier=platinum&currency=CHF&qty=50", {"currency": "CHF", "qty": 50, "sku": "B220", "tier": "platinum", "total": "6375.00", "unit": "127.50"}],
    ["/price?sku=B220&tier=platinum&currency=+EUR+&qty=200", {"currency": "EUR", "qty": 200, "sku": "B220", "tier": "platinum", "total": "25700.00", "unit": "128.50"}],
    ["/price?sku=b220&tier=GOLD&qty=200", {"currency": "USD", "qty": 200, "sku": "B220", "tier": "gold", "total": "29938.00", "unit": "149.69"}],
    ["/price?sku=C310&tier=platinum&currency=USD&qty=9", {"currency": "USD", "qty": 9, "sku": "C310", "tier": "platinum", "total": "535.05", "unit": "59.45"}],
    ["/price?sku=B220&tier=gold&currency=EUR&qty=9", {"currency": "EUR", "qty": 9, "sku": "B220", "tier": "gold", "total": "1379.07", "unit": "153.23"}],
    ["/price?sku=+E512+&tier=platinum&currency=eur&qty=120", {"currency": "EUR", "qty": 120, "sku": "E512", "tier": "platinum", "total": "4574.40", "unit": "38.12"}],
    ["/price?sku=E512&tier=Platinum&currency=jpy&qty=49", {"currency": "JPY", "qty": 49, "sku": "E512", "tier": "platinum", "total": "320068", "unit": "6532"}],
    ["/price?sku=e512&tier=silver&currency=EUR&qty=50", {"currency": "EUR", "qty": 50, "sku": "E512", "tier": "silver", "total": "2208.00", "unit": "44.16"}],
    ["/price?sku=C310&tier=platinum&currency=GBP&qty=10", {"currency": "GBP", "qty": 10, "sku": "C310", "tier": "platinum", "total": "453.90", "unit": "45.39"}],
    ["/price?sku=B220&tier=silver&currency=USD&qty=10", {"currency": "USD", "qty": 10, "sku": "B220", "tier": "silver", "total": "1741.60", "unit": "174.16"}],
    ["/price?sku=A100&tier=GOLD&currency=jpy&qty=49", {"currency": "JPY", "qty": 49, "sku": "A100", "tier": "gold", "total": "157633", "unit": "3217"}],
    ["/price?sku=C310&currency=usd&qty=200", {"currency": "USD", "qty": 200, "sku": "C310", "tier": "regular", "total": "13050.00", "unit": "65.25"}],
    ["/price?sku=a100&tier=silver&currency=chf&qty=12", {"currency": "CHF", "qty": 12, "sku": "A100", "tier": "silver", "total": "243.60", "unit": "20.30"}],
    ["/price?sku=E512&tier=Gold&currency=GBP&qty=049", {"currency": "GBP", "qty": 49, "sku": "E512", "tier": "gold", "total": "1786.05", "unit": "36.45"}],
    ["/price?sku=B220&tier=platinum&currency=+JPY+&qty=200", {"currency": "JPY", "qty": 200, "sku": "B220", "tier": "platinum", "total": "4222600", "unit": "21113"}],
    ["/price?sku=D405&tier=SILVER&currency=GBP&qty=12", {"currency": "GBP", "qty": 12, "sku": "D405", "tier": "silver", "total": "86.64", "unit": "7.22"}],
    ["/price?sku=D405&tier=silver&currency=CHF&qty=12", {"currency": "CHF", "qty": 12, "sku": "D405", "tier": "silver", "total": "97.32", "unit": "8.11"}],
    ["/price?sku=e512&tier=silver&currency=chf&qty=49", {"currency": "CHF", "qty": 49, "sku": "E512", "tier": "silver", "total": "2166.78", "unit": "44.22"}],
    ["/price?sku=c310&tier=silver&currency=+CHF+&qty=9", {"currency": "CHF", "qty": 9, "sku": "C310", "tier": "silver", "total": "548.37", "unit": "60.93"}],
    ["/price?sku=C310&tier=platinum&currency=GBP&qty=50", {"currency": "GBP", "qty": 50, "sku": "C310", "tier": "platinum", "total": "2176.00", "unit": "43.52"}],
    ["/price?sku=D405&tier=Gold&currency=jpy&qty=50", {"currency": "JPY", "qty": 50, "sku": "D405", "tier": "gold", "total": "61650", "unit": "1233"}],
    ["/price?sku=e512&tier=PLATINUM&currency=gbp&qty=1", {"currency": "GBP", "qty": 1, "sku": "E512", "tier": "platinum", "total": "35.01", "unit": "35.01"}],
    ["/price?sku=B220&tier=gold&currency=+EUR+&qty=3", {"currency": "EUR", "qty": 3, "sku": "B220", "tier": "gold", "total": "459.69", "unit": "153.23"}],
    ["/price?sku=d405&tier=platinum&currency=EUR&qty=12", {"currency": "EUR", "qty": 12, "sku": "D405", "tier": "platinum", "total": "87.48", "unit": "7.29"}],
    ["/price?sku=C310&currency=EUR&qty=9", {"currency": "EUR", "qty": 9, "sku": "C310", "tier": "regular", "total": "601.11", "unit": "66.79"}],
    ["/price?sku=A100&tier=silver&currency=EUR&qty=250", {"currency": "EUR", "qty": 250, "sku": "A100", "tier": "silver", "total": "4902.50", "unit": "19.61"}],
    ["/price?sku=e512&tier=gold&currency=GBP&qty=3", {"currency": "GBP", "qty": 3, "sku": "E512", "tier": "gold", "total": "112.74", "unit": "37.58"}],
    ["/price?sku=E512&currency=eur&qty=9", {"currency": "EUR", "qty": 9, "sku": "E512", "tier": "regular", "total": "449.82", "unit": "49.98"}],
    ["/price?sku=C310&tier=gold&currency=CHF&qty=200", {"currency": "CHF", "qty": 200, "sku": "C310", "tier": "gold", "total": "10158.00", "unit": "50.79"}],
    ["/price?sku=e512&currency=GBP&qty=120", {"currency": "GBP", "qty": 120, "sku": "E512", "tier": "regular", "total": "4765.20", "unit": "39.71"}],
    ["/price?sku=B220&tier=gold&currency=CHF&qty=49", {"currency": "CHF", "qty": 49, "sku": "B220", "tier": "gold", "total": "6992.79", "unit": "142.71"}],
    ["/price?sku=C310&tier=platinum&currency=GBP&qty=3", {"currency": "GBP", "qty": 3, "sku": "C310", "tier": "platinum", "total": "140.37", "unit": "46.79"}],
    ["/price?sku=A100&tier=gold&currency=JPY&qty=1", {"currency": "JPY", "qty": 1, "sku": "A100", "tier": "gold", "total": "3317", "unit": "3317"}],
    ["/price?sku=e512&tier=gold&currency=EUR&qty=120", {"currency": "EUR", "qty": 120, "sku": "E512", "tier": "gold", "total": "4908.00", "unit": "40.90"}],
    ["/price?sku=C310&tier=GOLD&currency=EUR&qty=200", {"currency": "EUR", "qty": 200, "sku": "C310", "tier": "gold", "total": "10580.00", "unit": "52.90"}],
    ["/price?sku=B220&tier=platinum&qty=250", {"currency": "USD", "qty": 250, "sku": "B220", "tier": "platinum", "total": "34870.00", "unit": "139.48"}],
    ["/price?sku=B220&tier=platinum&currency=+EUR+&qty=200", {"currency": "EUR", "qty": 200, "sku": "B220", "tier": "platinum", "total": "25700.00", "unit": "128.50"}],
    ["/price?sku=A100&tier=GOLD&currency=jpy&qty=49", {"currency": "JPY", "qty": 49, "sku": "A100", "tier": "gold", "total": "157633", "unit": "3217"}],
    ["/price?sku=d405&tier=GOLD&currency=gbp&qty=01", {"currency": "GBP", "qty": 1, "sku": "D405", "tier": "gold", "total": "6.89", "unit": "6.89"}],
    ["/price?sku=d405&tier=SILVER&currency=usd&qty=0250", {"currency": "USD", "qty": 250, "sku": "D405", "tier": "silver", "total": "2127.50", "unit": "8.51"}],
    ["/price?sku=e512&tier=GOLD&currency=gbp&qty=0120", {"currency": "GBP", "qty": 120, "sku": "E512", "tier": "gold", "total": "4194.00", "unit": "34.95"}],
    ["/price?sku=e512&tier=SILVER&currency=eur&qty=050", {"currency": "EUR", "qty": 50, "sku": "E512", "tier": "silver", "total": "2208.00", "unit": "44.16"}],
    ["/price?sku=d405&tier=GOLD&currency=jpy&qty=050", {"currency": "JPY", "qty": 50, "sku": "D405", "tier": "gold", "total": "61650", "unit": "1233"}],
]
#: How many distinct (normalized) prices HIDDEN_TRAFFIC asks for.
HIDDEN_DISTINCT = 70
#: Sent all at the same moment: eight requests for ONE price.
BURST_ONE = [
    ["/price?sku=C310&tier=silver&currency=CHF&qty=40", {"currency": "CHF", "qty": 40, "sku": "C310", "tier": "silver", "total": "2364.00", "unit": "59.10"}],
    ["/price?sku=C310&tier=silver&currency=CHF&qty=40", {"currency": "CHF", "qty": 40, "sku": "C310", "tier": "silver", "total": "2364.00", "unit": "59.10"}],
    ["/price?sku=C310&tier=silver&currency=CHF&qty=40", {"currency": "CHF", "qty": 40, "sku": "C310", "tier": "silver", "total": "2364.00", "unit": "59.10"}],
    ["/price?sku=C310&tier=silver&currency=CHF&qty=40", {"currency": "CHF", "qty": 40, "sku": "C310", "tier": "silver", "total": "2364.00", "unit": "59.10"}],
    ["/price?sku=C310&tier=silver&currency=CHF&qty=40", {"currency": "CHF", "qty": 40, "sku": "C310", "tier": "silver", "total": "2364.00", "unit": "59.10"}],
    ["/price?sku=C310&tier=silver&currency=CHF&qty=40", {"currency": "CHF", "qty": 40, "sku": "C310", "tier": "silver", "total": "2364.00", "unit": "59.10"}],
    ["/price?sku=C310&tier=silver&currency=CHF&qty=40", {"currency": "CHF", "qty": 40, "sku": "C310", "tier": "silver", "total": "2364.00", "unit": "59.10"}],
    ["/price?sku=C310&tier=silver&currency=CHF&qty=40", {"currency": "CHF", "qty": 40, "sku": "C310", "tier": "silver", "total": "2364.00", "unit": "59.10"}],
]
#: Then, at the same moment: two requests each for a few prices.
BURST_TWO = [
    ["/price?sku=E512&tier=platinum&currency=GBP&qty=7", {"currency": "GBP", "qty": 7, "sku": "E512", "tier": "platinum", "total": "245.07", "unit": "35.01"}],
    ["/price?sku=E512&tier=platinum&currency=GBP&qty=7", {"currency": "GBP", "qty": 7, "sku": "E512", "tier": "platinum", "total": "245.07", "unit": "35.01"}],
    ["/price?sku=e512&tier=Platinum&currency=gbp&qty=07", {"currency": "GBP", "qty": 7, "sku": "E512", "tier": "platinum", "total": "245.07", "unit": "35.01"}],
    ["/price?sku=e512&tier=Platinum&currency=gbp&qty=07", {"currency": "GBP", "qty": 7, "sku": "E512", "tier": "platinum", "total": "245.07", "unit": "35.01"}],
    ["/price?sku=B220&tier=gold&currency=JPY&qty=60", {"currency": "JPY", "qty": 60, "sku": "B220", "tier": "gold", "total": "1404840", "unit": "23414"}],
    ["/price?sku=B220&tier=gold&currency=JPY&qty=60", {"currency": "JPY", "qty": 60, "sku": "B220", "tier": "gold", "total": "1404840", "unit": "23414"}],
    ["/price?sku=D405&tier=regular&currency=EUR&qty=300", {"currency": "EUR", "qty": 300, "sku": "D405", "tier": "regular", "total": "2475.00", "unit": "8.25"}],
    ["/price?sku=D405&tier=regular&currency=EUR&qty=300", {"currency": "EUR", "qty": 300, "sku": "D405", "tier": "regular", "total": "2475.00", "unit": "8.25"}],
]
BURST_TWO_DISTINCT = 3


def _sha_of(*names, folder=HERE):
    digest = hashlib.sha256()
    for name in names:
        with open(os.path.join(folder, name), "rb") as fh:
            digest.update(fh.read().replace(CRLF, LF))
    return digest.hexdigest()


def _refused(exc):
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, ConnectionRefusedError)


def _get(port, path, timeout=60):
    """(status, body) of one GET. A connection the server REFUSED (its listen
    backlog was full under a burst) never reached it, so it is tried again."""
    url = f"http://127.0.0.1:{port}{path}"
    for attempt in range(100):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                return exc.code, json.loads(body)
            except ValueError:
                return exc.code, body
        except (urllib.error.URLError, ConnectionRefusedError) as exc:
            if not _refused(exc) or attempt == 99:
                raise
            time.sleep(0.05)
    raise AssertionError("unreachable")


class _Server:
    """The agent's server, copied to a temporary folder and started there on
    port 0 (optionally with PRICING_SLOW_MS); stopped with /shutdown."""

    def __init__(self, slow_ms=None):
        self.slow_ms = slow_ms
        self.proc = None
        self.port = None

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="ij-price-check-")
        self.folder = os.path.join(self.tmp, "server")
        shutil.copytree(
            HERE, self.folder,
            ignore=shutil.ignore_patterns("__pycache__", "test_*", "server.log", "replay_report.json"),
        )
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
        env.pop("PRICING_SLOW_MS", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        if self.slow_ms:
            env["PRICING_SLOW_MS"] = str(self.slow_ms)
        self.err = open(os.path.join(self.tmp, "stderr.txt"), "wb")
        self.proc = subprocess.Popen(
            [sys.executable, "-s", "priceserver.py", "--port", "0"],
            cwd=self.folder, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=self.err,
        )
        log = os.path.join(self.folder, "server.log")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if os.path.exists(log):
                with open(log, encoding="utf-8") as fh:
                    found = [line.split()[1] for line in fh if line.startswith("listening ")]
                if found:
                    self.port = int(found[-1])
                    return self
            if self.proc.poll() is not None:
                break
            time.sleep(0.05)
        self._stop()
        with open(os.path.join(self.tmp, "stderr.txt"), "rb") as fh:
            said = fh.read().decode("utf-8", errors="replace")
        shutil.rmtree(self.tmp, ignore_errors=True)
        raise AssertionError("the server did not start (no 'listening' line in server.log):\n" + said[-2000:])

    def _stop(self):
        if self.proc is not None and self.proc.poll() is None:
            if self.port is not None:
                try:
                    _get(self.port, "/shutdown", timeout=10)
                except OSError:
                    pass
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()  # only the server this check started
                self.proc.wait(timeout=30)
        self.err.close()

    def __exit__(self, *exc):
        self._stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False

    def compute_calls(self):
        status, body = _get(self.port, "/debug/cache")
        if status != 200 or not isinstance(body, dict) or "compute_calls" not in body:
            raise AssertionError(f"/debug/cache must answer compute_calls (README.md); it answered {status} {body!r}")
        return body["compute_calls"]


def _burst(port, rows):
    results = [None] * len(rows)
    barrier = threading.Barrier(len(rows))

    def one(i):
        try:
            barrier.wait(timeout=30)
        except threading.BrokenBarrierError:
            pass
        try:
            results[i] = _get(port, rows[i][0])
        except OSError as exc:
            results[i] = (None, repr(exc))

    threads = [threading.Thread(target=one, args=(i,), daemon=True) for i in range(len(rows))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    return results


class HiddenPriceServer(unittest.TestCase):
    def test_a_the_final_replay_ran_clean_on_the_final_code(self):
        path = os.path.join(HERE, "replay_report.json")
        self.assertTrue(os.path.isfile(path), "replay_report.json is missing: replay.py was never run")
        with open(path, encoding="utf-8") as fh:
            report = json.load(fh)
        self.assertEqual(report.get("requests"), VISIBLE_REQUESTS, "the report is not from a full replay of traffic.jsonl")
        self.assertEqual(report.get("mismatches"), 0, "the last replay still had wrong answers")
        self.assertEqual(report.get("first_mismatches"), [])
        self.assertEqual(
            report.get("server_version"), _sha_of(*SOURCE_FILES),
            "the last replay ran against a server that was NOT running the code as it is now "
            "(the code changed after that server started)",
        )

    def test_b_the_last_server_run_was_stopped_with_shutdown(self):
        path = os.path.join(HERE, "server.log")
        self.assertTrue(os.path.isfile(path), "server.log is missing: the server never ran here")
        with open(path, encoding="utf-8") as fh:
            lines = [line.strip() for line in fh if line.strip()]
        starts = [i for i, line in enumerate(lines) if line.startswith("listening ")]
        self.assertTrue(starts, "server.log has no 'listening' line")
        self.assertEqual(lines[-1], "stopped", "the last server run did not end with /shutdown (server.log)")

    def test_c_other_traffic_gets_the_right_answers(self):
        wrong = []
        with _Server() as server:
            for path, expected in HIDDEN_TRAFFIC:
                status, body = _get(server.port, path)
                if status != 200 or body != expected:
                    wrong.append(f"{path}\n    expected {expected}\n    got      {status} {body}")
        if wrong:
            self.fail(
                f"{len(wrong)} of {len(HIDDEN_TRAFFIC)} answers were wrong; the first ones:\n" + "\n".join(wrong[:5])
            )

    def test_d_each_distinct_price_is_computed_exactly_once(self):
        with _Server() as server:
            for path, _expected in HIDDEN_TRAFFIC:
                _get(server.port, path)
            calls = server.compute_calls()
        self.assertEqual(
            calls, HIDDEN_DISTINCT,
            f"{len(HIDDEN_TRAFFIC)} requests asked for {HIDDEN_DISTINCT} distinct prices (PERFORMANCE.md), "
            f"but _compute_price ran {calls} times",
        )

    def test_e_simultaneous_requests_for_one_price_cost_one_computation(self):
        with _Server(slow_ms=400) as server:
            got = _burst(server.port, BURST_ONE)
            for (path, expected), (status, body) in zip(BURST_ONE, got):
                self.assertEqual((status, body), (200, expected), path)
            first = server.compute_calls()
            got = _burst(server.port, BURST_TWO)
            for (path, expected), (status, body) in zip(BURST_TWO, got):
                self.assertEqual((status, body), (200, expected), path)
            second = server.compute_calls()
        self.assertEqual(first, 1, f"{len(BURST_ONE)} simultaneous requests for one price computed it {first} times")
        self.assertEqual(
            second, 1 + BURST_TWO_DISTINCT,
            f"simultaneous requests for {BURST_TWO_DISTINCT} prices computed {second - first} prices",
        )

    def test_f_the_recorded_files_are_unchanged(self):
        for name, digest in UNCHANGED.items():
            with self.subTest(name=name):
                self.assertEqual(_sha_of(name), digest, f"{name} must not be changed")


if __name__ == "__main__":
    unittest.main()
