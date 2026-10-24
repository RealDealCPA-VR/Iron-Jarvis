# IJ-BENCH-HIDDEN-CHECK frontier-inventory-filter
"""Hidden check: serve the page and use it in a real (headless) browser.

Run as a script: exit 0 passed, 1 failed, 3 when no browser can start
(Playwright missing, no Microsoft Edge, or it would not launch) — a check
that cannot run is never a pass or a fail.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
UNSCORED = 3
DATA_SHA = "3cbde076c5fa65d8fc78f54af57c73d2c9606e73933a7e121b3cdbf3dace8dbd"
EDGE_PLACES = (
    os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 "Microsoft", "Edge", "Application", "msedge.exe"),
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                 "Microsoft", "Edge", "Application", "msedge.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
    "/usr/bin/microsoft-edge", "/opt/microsoft/msedge/msedge",
)
STATE_JS = """() => {
  const rows = Array.from(document.querySelectorAll('#items tbody tr'));
  const shown = rows.filter(tr => tr.checkVisibility()).map(tr => tr.cells[0] ? tr.cells[0].textContent.trim() : '');
  const count = document.getElementById('count');
  const empty = document.getElementById('empty');
  return {
    rows: shown,
    count: count ? count.textContent.replace(/\\s+/g, ' ').trim() : null,
    count_shown: count ? count.checkVisibility() : false,
    empty_shown: empty ? empty.checkVisibility() : false,
    empty_text: empty ? empty.textContent.replace(/\\s+/g, ' ').trim() : null,
  };
}"""


def find_edge():
    found = shutil.which("msedge") or ""
    for path in (found, *EDGE_PLACES):
        if path and os.path.isfile(path):
            return path
    return ""


def data_items():
    with open(os.path.join(HERE, "data", "items.json"), "rb") as fh:
        raw = fh.read().replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest(), json.loads(raw.decode("utf-8"))


def expected(items, text, low):
    needle = text.strip().lower()
    return [
        it["sku"] for it in items
        if (not needle or needle in it["sku"].lower() or needle in it["name"].lower())
        and (not low or it["qty"] <= it["reorder_at"])
    ]


def run_page_checks(edge, items):
    """Drive the page; return a list of what was wrong ("" items = none)."""
    import server
    from playwright.sync_api import sync_playwright

    problems = []
    srv = server.create_server(port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(
                    executable_path=edge, headless=True,
                    args=["--no-first-run", "--no-default-browser-check", "--disable-extensions"],
                )
            except Exception as exc:  # noqa: BLE001
                raise BrowserMissing(f"the browser would not start ({exc})") from exc
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                page.goto(url, wait_until="load", timeout=30000)
                total = len(items)

                def settle(step, want_rows):
                    want = {
                        "rows": want_rows,
                        "count": f"Showing {len(want_rows)} of {total} items",
                        "count_shown": True,
                        "empty_shown": not want_rows,
                    }
                    state = None
                    deadline = time.monotonic() + 6
                    while time.monotonic() < deadline:
                        state = page.evaluate(STATE_JS)
                        if all(state.get(k) == v for k, v in want.items()) and (
                            want_rows or state.get("empty_text") == "No items match"
                        ):
                            return
                        page.wait_for_timeout(50)
                    problems.append(f"{step}: wanted {want} (and #empty saying 'No items match' when no row "
                                    f"shows), the page shows {state}")

                page.wait_for_function("document.querySelectorAll('#items tbody tr').length > 0", timeout=20000)
                settle("after loading", expected(items, "", False))
                search = page.locator("#search")
                if search.count() != 1:
                    problems.append("there is no #search box")
                    return problems
                search.click()
                search.press_sequentially("bolt", delay=25)
                settle("typing 'bolt'", expected(items, "bolt", False))
                page.click("#low-stock")
                settle("'bolt' + ticking Low stock only", expected(items, "bolt", True))
                search.fill("  WASHER  ")
                settle("'  WASHER  ' + Low stock only", expected(items, "washer", True))
                search.fill("zzz")
                settle("a search that matches nothing", [])
                search.fill("")
                settle("clearing the search (Low stock only still ticked)", expected(items, "", True))
                page.click("#low-stock")
                settle("unticking Low stock only", expected(items, "", False))
                search.fill("m8")
                settle("'m8'", expected(items, "m8", False))
                page.click("#low-stock")
                settle("'m8' + Low stock only", expected(items, "m8", True))
                if errors:
                    problems.append("the page raised errors: " + "; ".join(errors[:3]))
            finally:
                browser.close()
    finally:
        srv.shutdown()
        srv.server_close()
    return problems


class BrowserMissing(RuntimeError):
    pass


class TheServerStillWorks(unittest.TestCase):
    def test_the_data_is_unchanged(self):
        self.assertEqual(data_items()[0], DATA_SHA, "data/items.json was changed")

    def test_the_servers_own_tests_pass(self):
        suite = unittest.defaultTestLoader.loadTestsFromName("test_server")
        with open(os.devnull, "w") as sink:
            result = unittest.TextTestRunner(stream=sink, verbosity=0).run(suite)
        self.assertTrue(result.wasSuccessful() and result.testsRun >= 2, "test_server.py does not pass")


def main():
    edge = find_edge()
    if not edge:
        print("no Microsoft Edge was found, so the page cannot be checked in a browser")
        return UNSCORED
    try:
        import playwright.sync_api  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"Playwright is not available ({exc})")
        return UNSCORED
    scratch = tempfile.mkdtemp(prefix="ij-page-check-")
    os.environ["TEMP"] = os.environ["TMP"] = os.environ["TMPDIR"] = scratch
    try:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(TheServerStillWorks)
        basics = unittest.TextTestRunner(verbosity=2).run(suite)
        try:
            problems = run_page_checks(edge, data_items()[1])
        except BrowserMissing as exc:
            print(str(exc))
            return UNSCORED
        except Exception as exc:  # noqa: BLE001 — the agent's page or server broke
            problems = [f"the page could not be used: {type(exc).__name__}: {exc}"]
        for problem in problems:
            print("FAIL:", problem)
        if not problems and basics.wasSuccessful():
            print("the page behaves as UI.md says")
            return 0
        return 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
