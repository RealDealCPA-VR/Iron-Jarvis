"""The hotkey quick window goes through CHAT (north-star fix plan 6.5, D11).

The quick window (Ctrl+Shift+Space, ``desktop/spotlight.html``) says "Tell
Iron Jarvis what to do…", but ``runSpotlightTask`` posted every request to
``POST /sessions`` with ``agent_type: "builder"``: a coding agent in a
workspace that holds none of chat's settings tools, record tools, lookup,
doors or "not offered" lines. "Switch to dark mode" did nothing and the toast
said "Done: switch to dark mode".

Now the words go to chat: the desktop checks Iron Jarvis is answering, keeps
the words in the MAIN process (one-shot, for two minutes), and opens the main
window on ``/chat?new=1&quick=1``; the chat page takes them once over the
sender-checked ``quick-ask:take`` bridge and sends them as the first message
of a fresh, saved chat (``dashboard/__tests__/north-star-quick-window-v1506``
drives that half on the real page). Chat hands off to an agent run only when
chat does. No run is watched and no "Done" toast is shown: the quick window
says it opened in chat, and the answer is on screen.

House idiom (``test_desktop_idle_v1333.py``): the shipped functions are LIFTED
out of ``desktop/main.js`` and run under node against stubs.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_desktop_idle_v1333 import _decl, _lift, _run, _src, requires_node

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


_HARNESS = """
const calls = [];
const loaded = [];
let shown = 0;
let mainWin = { isDestroyed() { return false; }, loadURL(u) { loaded.push(u); } };
function showMainWindow() { shown += 1; }
const DASHBOARD_URL = "http://localhost:8788";
let FAIL = process.argv[2] || "";
function daemonRequest(method, p, body) {
  calls.push({ method, path: p, body: body || null });
  if (FAIL) return Promise.reject(new Error(FAIL));
  return Promise.resolve({ status: "ok" });
}
let NOW = 1000;
const Date = { now: () => NOW };
__LIFTED__
(async () => {
  const out = { calls, loaded };
  try {
    out.result = await runSpotlightTask("switch to dark mode");
  } catch (e) {
    out.error = String(e && e.message);
  }
  out.shown = shown;
  out.first = takeQuickAsk(NOW + 5);
  out.second = takeQuickAsk(NOW + 6);
  // Words left waiting longer than the hold are never sent.
  pendingQuickAsk = { text: "old words", at: NOW };
  out.stale = takeQuickAsk(NOW + QUICK_ASK_TTL_MS + 1);
  out.staleAfter = pendingQuickAsk;
  pendingQuickAsk = { text: "fresh words", at: NOW };
  out.fresh = takeQuickAsk(NOW + QUICK_ASK_TTL_MS);
  console.log(JSON.stringify(out));
})();
"""


def _lifted() -> str:
    return "\n".join(
        [
            _decl("QUICK_ASK_TTL_MS"),
            _decl("pendingQuickAsk"),
            _lift("function quickAskUrl("),
            _lift("function takeQuickAsk("),
            _lift("async function runSpotlightTask("),
        ]
    )


@requires_node
def test_the_quick_window_opens_chat_with_the_words_and_starts_no_agent_run(tmp_path):
    out = _run(_HARNESS.replace("__LIFTED__", _lifted()), tmp_path, "quick")
    # No agent run: the only daemon read is "is Iron Jarvis answering?".
    assert [c["path"] for c in out["calls"]] == ["/health"]
    assert all(c["method"] == "GET" for c in out["calls"])
    # The main window opens on a FRESH chat, the words not in the URL.
    assert out["shown"] == 1
    assert out["loaded"] == ["http://localhost:8788/chat?new=1&quick=1"]
    assert "dark" not in out["loaded"][0]
    assert out["result"] == {"ok": True, "opened": "chat"}
    # The page takes the words ONCE.
    assert out["first"] == "switch to dark mode"
    assert out["second"] == ""
    assert out["stale"] == "" and out["staleAfter"] is None
    assert out["fresh"] == "fresh words"


@requires_node
def test_when_iron_jarvis_is_not_answering_nothing_opens_and_nothing_waits(tmp_path):
    script = _HARNESS.replace("__LIFTED__", _lifted())
    f = tmp_path / "down.js"
    f.write_text(script, encoding="utf-8")
    import json
    import subprocess

    proc = subprocess.run(
        ["node", str(f), "connect ECONNREFUSED 127.0.0.1:8787"],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    # The error reaches the IPC handler, which words it (spotlightErrorWords:
    # "Iron Jarvis isn't running yet. Open the main window.").
    assert "ECONNREFUSED" in out["error"]
    assert out["loaded"] == [] and out["shown"] == 0
    assert out["first"] == ""


def test_the_old_agent_route_and_its_done_toast_are_gone():
    src = _src()
    body = _lift("async function runSpotlightTask(")
    assert '"/sessions"' not in body and "builder" not in body
    # Nothing watches a run the quick window no longer starts.
    for gone in ("function watchSpotlightTask(", "SPOTLIGHT_OUTCOME_WORDS", "function spotlightOutcome("):
        assert gone not in src, gone


def test_the_words_are_handed_over_only_to_the_dashboard():
    src = _src()
    i = src.index('ipcMain.handle("quick-ask:take"')
    block = src[i : src.index("});", i)]
    assert "isTrustedDashboardSender(event)" in block
    assert "takeQuickAsk(Date.now())" in block
    preload = _read("desktop/preload.js")
    assert re.search(r'quickAsk:\s*\{\s*take:\s*\(\)\s*=>\s*ipcRenderer\.invoke\("quick-ask:take"\)', preload)


def test_the_quick_window_says_where_the_words_go():
    html = _read("desktop/spotlight.html")
    assert "notification when it's done" not in html
    assert "Opens in chat" in html
    assert "Opening in chat" in html
