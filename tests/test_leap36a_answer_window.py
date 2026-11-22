"""Quick Ask: the answer appears right there (next leaps 36-a, R2.2).

The hotkey box (Ctrl+Shift+Space) used to pull the WHOLE main window forward
onto a new chat (v1.506.0). Now Enter keeps the words in the main process as
before, hides the box and opens ONE small answer window where the box was:
the chat page itself (`/chat?new=1&quick=1&compact=1&popout=1`), drawn
compact, with the MAIN preload and token and a pop-out's navigation guards.
It floats on top and does not hide when you click back into your other
program (D-36.1); Esc or its close button hides it; hidden for the box's idle
span it is destroyed. "Continue in the main window" is the sender-checked
`quick-ask:to-main`: the same chat opens in the main window and the small one
steps aside. A door to another page opens in the main window too.

House idiom (``test_desktop_idle_v1333.py``, ``test_desktop_popout_v1283.py``):
the shipped functions are LIFTED out of ``desktop/main.js`` and run under
node against stubs, never copied, so a change to main.js runs here changed.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_desktop_idle_v1333 import _decl, _lift, _run, _src, requires_node

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


# ---------------------------------------------------------------------------
# The pure pieces: where the window goes, and what may open in the main window
# ---------------------------------------------------------------------------

_PURE = """
const DASHBOARD_URL = "http://localhost:8788";
const DASHBOARD_ORIGINS = new Set(["http://localhost:8788", "http://127.0.0.1:8788"]);
__LIFTED__
const work = { x: 0, y: 0, width: 1920, height: 1040 };
const out = {};
// Where the box was: centred under it, the box's top edge.
out.underBox = quickAnswerBounds({ x: 650, y: 180, width: 620, height: 150 }, work);
// No box (destroyed): the middle of the work area, near the top.
out.noBox = quickAnswerBounds(null, work);
// A box near the right and bottom edges: kept inside the screen.
out.edge = quickAnswerBounds({ x: 1800, y: 900, width: 620, height: 150 }, work);
// A small screen: never bigger than the work area.
out.small = quickAnswerBounds({ x: 0, y: 0, width: 620, height: 150 }, { x: 0, y: 0, width: 400, height: 500 });
// A second display to the left (negative x): stays on that display.
out.left = quickAnswerBounds({ x: -1500, y: 180, width: 620, height: 150 }, { x: -1920, y: 0, width: 1920, height: 1040 });
out.paths = [
  quickAnswerMainPath({ thread: "t1" }),
  quickAnswerMainPath({ thread: "a1b2_C3-d4" }),
  quickAnswerMainPath({ thread: "../x" }),
  quickAnswerMainPath({ thread: "t1&ask=delete everything" }),
  quickAnswerMainPath({ thread: "" }),
  quickAnswerMainPath({ thread: "x".repeat(129) }),
  quickAnswerMainPath({ path: "/settings?section=appearance#theme" }),
  quickAnswerMainPath({ path: "/memory#prefs-suggested" }),
  quickAnswerMainPath({ path: "//evil.example.com/x" }),
  quickAnswerMainPath({ path: "/\\\\evil.example.com" }),
  quickAnswerMainPath({ path: "http://evil.example.com/" }),
  quickAnswerMainPath({ path: "settings" }),
  quickAnswerMainPath({ path: "/a b" }),
  quickAnswerMainPath({ path: "/" + "x".repeat(600) }),
  quickAnswerMainPath({}),
  quickAnswerMainPath(null),
  quickAnswerMainPath("t1"),
];
out.url = quickAnswerUrl(DASHBOARD_URL);
console.log(JSON.stringify(out));
"""


@requires_node
def test_the_window_opens_where_the_box_was_and_only_dashboard_paths_reach_the_main_window(tmp_path):
    lifted = "\n".join(
        [
            _decl("QUICK_ANSWER_WIDTH"),
            _decl("QUICK_ANSWER_HEIGHT"),
            _decl("QUICK_THREAD_RX"),
            _lift("function quickAskUrl("),
            _lift("function quickAnswerUrl("),
            _lift("function quickAnswerBounds("),
            _lift("function quickAnswerMainPath("),
        ]
    )
    out = _run(_PURE.replace("__LIFTED__", lifted), tmp_path, "pure")
    assert out["underBox"] == {"x": 720, "y": 180, "width": 480, "height": 560}
    assert out["noBox"]["width"] == 480 and out["noBox"]["height"] == 560
    assert out["noBox"]["x"] == 720
    assert out["edge"] == {"x": 1440, "y": 480, "width": 480, "height": 560}
    assert out["small"] == {"x": 0, "y": 0, "width": 400, "height": 500}
    assert out["left"]["x"] == -1430 and out["left"]["x"] >= -1920
    assert out["paths"] == [
        "/chat?thread=t1",
        "/chat?thread=a1b2_C3-d4",
        "",  # a traversal is not a thread id
        "",  # nothing rides along with the id
        "",
        "",
        "/settings?section=appearance#theme",
        "/memory#prefs-suggested",
        "",  # another host
        "",  # a backslash a URL parser would turn into another host
        "",  # a whole address
        "",  # not a path
        "",  # whitespace
        "",  # too long
        "",
        "",
        "",
    ]
    # The words never ride the address: only the landing flags.
    assert out["url"] == "http://localhost:8788/chat?new=1&quick=1&compact=1&popout=1"


# ---------------------------------------------------------------------------
# The window itself: built like a pop-out, one at a time, the main window
# untouched
# ---------------------------------------------------------------------------

_WINDOW = """
const events = [];
const built = [];
let mainLoaded = [];
let mainShown = 0;
let mainWin = { isDestroyed() { return false; }, loadURL(u) { mainLoaded.push(u); } };
function showMainWindow() { mainShown += 1; }
const opened = [];
function openDashboardPath(p) { opened.push(p); return true; }
const external = [];
function openExternalIfSafe(u) { external.push(u); return true; }
const DASHBOARD_URL = "http://localhost:8788";
const DASHBOARD_ORIGINS = new Set(["http://localhost:8788", "http://127.0.0.1:8788"]);
function isDashboardUrl(url) { try { return DASHBOARD_ORIGINS.has(new URL(String(url)).origin); } catch { return false; } }
function isTrustedDashboardSender(event) { return !!(event && event.senderFrame && isDashboardUrl(event.senderFrame.url)); }
let authToken = "tok";
let isQuitting = false;
let shuttingDown = false;
const path = { join: (...a) => a.join("/") };
const __dirname = "D";
function windowIcon() { return "icon"; }
function installSpellcheckMenu() {}
const crashReload = [];
function installPopoutCrashReload(win, route) { crashReload.push(route); }
function waitForDashboard() { return Promise.resolve(); }
function desktopLog() {}
const screen = {
  getDisplayMatching() { return { workArea: { x: 0, y: 0, width: 1920, height: 1040 } }; },
  getPrimaryDisplay() { return { workArea: { x: 0, y: 0, width: 1920, height: 1040 } }; },
};
const timers = [];
function setTimeout(fn, ms) { const t = { fn, ms, live: true, unref() {} }; timers.push(t); return t; }
function clearTimeout(t) { if (t) t.live = false; }
class FakeContents {
  constructor() { this.handlers = {}; this.sent = []; this.loaded = []; this.openHandler = null; this.history = { back: 0, can: true }; this.navigationHistory = { canGoBack: () => this.history.can, goBack: () => { this.history.back += 1; } }; }
  on(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
  once(ev, fn) { const w = (...a) => { this.handlers[ev] = this.handlers[ev].filter((f) => f !== w); fn(...a); }; this.on(ev, w); }
  emit(ev, ...a) { for (const fn of [...(this.handlers[ev] || [])]) fn(...a); }
  setWindowOpenHandler(fn) { this.openHandler = fn; }
  send(ch) { this.sent.push(ch); }
  executeJavaScript() { return Promise.resolve("present"); }
  reload() {}
}
class BrowserWindow {
  constructor(opts) { this.opts = opts; this.handlers = {}; this.visible = false; this.destroyed = false; this.webContents = new FakeContents(); this.focused = 0; this.top = null; this.bounds = null; built.push(this); }
  on(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
  once(ev, fn) { this.on(ev, fn); }
  emit(ev, ...a) { for (const fn of [...(this.handlers[ev] || [])]) fn(...a); }
  isDestroyed() { return this.destroyed; }
  isVisible() { return this.visible; }
  show() { this.visible = true; this.emit("show"); }
  hide() { this.visible = false; this.emit("hide"); }
  focus() { this.focused += 1; }
  destroy() { this.destroyed = true; this.visible = false; this.emit("closed"); }
  close() { const e = { prevented: false, preventDefault() { this.prevented = true; } }; this.emit("close", e); if (!e.prevented) this.destroy(); return e.prevented; }
  loadURL(u) { this.webContents.loaded.push(u); }
  setAlwaysOnTop(on, level) { this.top = { on, level }; }
  setBounds(b) { this.bounds = b; }
  getBounds() { return this.bounds || { x: 650, y: 180, width: 620, height: 150 }; }
}
let spotlightWin = new BrowserWindow({ box: true });
spotlightWin.visible = true;
built.length = 0;
function daemonRequest() { return Promise.resolve({ status: "ok" }); }
let NOW = 1000;
const Date = { now: () => NOW };
__LIFTED__
const fire = () => { for (const t of timers.splice(0)) if (t.live) t.fn(); };
(async () => {
  const out = {};
  out.result = await runSpotlightTask("plain English?");
  const win = built[0];
  out.builtCount = built.length;
  out.boxVisible = spotlightWin.visible;
  out.mainLoaded = mainLoaded;
  out.mainShown = mainShown;
  out.opts = {
    width: win.opts.width, height: win.opts.height, x: win.opts.x, y: win.opts.y,
    show: win.opts.show, titleBarStyle: win.opts.titleBarStyle, overlay: !!win.opts.titleBarOverlay,
    preload: win.opts.webPreferences.preload,
    contextIsolation: win.opts.webPreferences.contextIsolation,
    nodeIntegration: win.opts.webPreferences.nodeIntegration,
    args: win.opts.webPreferences.additionalArguments,
    alwaysOnTop: win.opts.alwaysOnTop, skipTaskbar: win.opts.skipTaskbar,
    minimizable: win.opts.minimizable, maximizable: win.opts.maximizable,
  };
  out.top = win.top;
  out.loaded = win.webContents.loaded.slice();
  out.crashReload = crashReload.slice();
  // Not shown before the page has drawn; shown and focused once it has.
  out.visibleBeforeLoad = win.visible;
  win.webContents.emit("did-finish-load");
  out.visibleAfterLoad = win.visible;
  out.focused = win.focused;
  // The words are taken ONCE, by the answer window's page.
  out.first = takeQuickAsk(NOW + 5);
  out.second = takeQuickAsk(NOW + 6);
  // Clicking back into another program does NOT hide it (D-36.1).
  win.emit("blur");
  out.visibleAfterBlur = win.visible;
  // Guards: a link to another site leaves for the system browser; a full
  // navigation to another dashboard page opens in the MAIN window.
  out.openHandler = win.webContents.openHandler({ url: "https://example.com/" });
  const nav = (url) => { const e = { prevented: false, preventDefault() { this.prevented = true; } }; win.webContents.emit("will-navigate", e, url); return e.prevented; };
  out.navExternal = nav("https://evil.example.com/");
  out.navOtherPage = nav("http://localhost:8788/schedules?x=1");
  out.navChat = nav("http://localhost:8788/chat?thread=t9");
  const redir = (url) => { const e = { prevented: false, preventDefault() { this.prevented = true; } }; win.webContents.emit("will-redirect", e, url); return e.prevented; };
  out.redirExternal = redir("https://evil.example.com/");
  // A page change made inside the page (a link Next followed) goes to the
  // main window and the small window steps back to its chat.
  win.webContents.emit("did-navigate-in-page", {}, "http://localhost:8788/settings#appearance", true);
  win.webContents.emit("did-navigate-in-page", {}, "http://localhost:8788/chat?compact=1", true);
  out.back = win.webContents.history.back;
  out.opened = opened.slice();
  out.external = external.slice();
  // The hotkey while it is on screen: the window, not the box.
  spotlightWin.visible = false;
  out.toggledToAnswer = focusQuickAnswer();
  out.focusSent = win.webContents.sent.slice();
  // The close button hides it (the chat stays saved); Quit really closes it.
  out.closePrevented = win.close();
  out.visibleAfterClose = win.visible;
  out.destroyedAfterClose = win.destroyed;
  out.focusWhenHidden = focusQuickAnswer();
  // Hidden for the idle span: destroyed, and the next ask builds a new one.
  out.idleDelay = timers.filter((t) => t.live).map((t) => t.ms);
  fire();
  out.destroyedAfterIdle = win.destroyed;
  out.liveAfterIdle = liveQuickAnswer() === null;
  // ONE answer window at a time: a second ask reuses a hidden window.
  spotlightWin.visible = true;
  NOW = 5000;
  await runSpotlightTask("second question");
  const win2 = built[1];
  win2.webContents.emit("did-finish-load");
  win2.hide();
  spotlightWin.visible = true;
  await runSpotlightTask("third question");
  out.builtAfterThree = built.length;
  out.win2Loads = win2.webContents.loaded.length;
  out.win2Visible = win2.visible;
  isQuitting = true;
  out.quitClosePrevented = win2.close();
  out.quitDestroyed = win2.destroyed;
  out.mainLoadedAtEnd = mainLoaded;
  console.log(JSON.stringify(out));
})();
"""


def _window_lifted() -> str:
    return "\n".join(
        [
            _decl("QUICK_ASK_TTL_MS"),
            _decl("pendingQuickAsk"),
            _decl("SPOTLIGHT_IDLE_DESTROY_MS"),
            _decl("QUICK_ANSWER_WIDTH"),
            _decl("QUICK_ANSWER_HEIGHT"),
            _decl("QUICK_ANSWER_ROUTE"),
            _decl("QUICK_THREAD_RX"),
            _decl("quickAnswerWin"),
            _decl("_quickAnswerDestroyTimer"),
            _lift("function quickAskUrl("),
            _lift("function quickAnswerUrl("),
            _lift("function takeQuickAsk("),
            _lift("function quickAnswerBounds("),
            _lift("function quickAnswerMainPath("),
            _lift("function quickAnswerWorkArea("),
            _lift("function dashboardPathOf("),
            _lift("function isQuickAnswerPage("),
            _lift("function liveQuickAnswer("),
            _lift("function cancelQuickAnswerDestroy("),
            _lift("function scheduleQuickAnswerDestroy("),
            _lift("function showQuickAnswerWhenDrawn("),
            _lift("function createQuickAnswerWindow("),
            _lift("function openQuickAnswer("),
            _lift("function focusQuickAnswer("),
            _lift("function hideQuickAnswer("),
            _lift("async function runSpotlightTask("),
        ]
    )


@requires_node
def test_a_quick_ask_opens_one_small_answer_window_and_leaves_the_main_window_alone(tmp_path):
    out = _run(_WINDOW.replace("__LIFTED__", _window_lifted()), tmp_path, "window")
    assert out["result"] == {"ok": True, "opened": "answer"}
    # The main window is neither loaded nor brought forward.
    assert out["mainLoaded"] == [] and out["mainShown"] == 0
    assert out["mainLoadedAtEnd"] == []
    # The box steps aside; ONE answer window opens where it was.
    assert out["boxVisible"] is False
    assert out["builtCount"] == 1
    o = out["opts"]
    assert (o["width"], o["height"], o["x"], o["y"]) == (480, 560, 720, 180)
    # Built like a pop-out: the MAIN preload (token, the take bridge), the
    # frameless chrome with the native close button, isolated.
    assert o["preload"] == "D/preload.js"
    assert o["contextIsolation"] is True and o["nodeIntegration"] is False
    assert o["titleBarStyle"] == "hidden" and o["overlay"] is True
    assert o["args"] == ["--ij-token=tok", "--ij-popout=/chat", "--ij-quick-answer=1"]
    assert o["show"] is False
    # Floating on top (not the box's screen-saver level), no taskbar button
    # of its own, nothing to minimise it out of reach.
    assert o["alwaysOnTop"] is True and out["top"] == {"on": True, "level": "floating"}
    assert o["skipTaskbar"] is True and o["minimizable"] is False and o["maximizable"] is False
    assert out["crashReload"] == ["/chat"]
    # The chat page, compact, told to take the waiting words; never the words.
    assert out["loaded"] == ["http://localhost:8788/chat?new=1&quick=1&compact=1&popout=1"]
    assert "plain" not in out["loaded"][0]
    assert out["visibleBeforeLoad"] is False and out["visibleAfterLoad"] is True
    assert out["focused"] >= 1
    assert out["first"] == "plain English?" and out["second"] == ""
    # D-36.1: clicking into another program leaves it on screen.
    assert out["visibleAfterBlur"] is True
    # The guards: another site goes to the system browser; another page of the
    # app opens in the main window; the chat itself stays.
    assert out["openHandler"] == {"action": "deny"}
    assert out["navExternal"] is True and out["navOtherPage"] is True and out["navChat"] is False
    assert out["redirExternal"] is True
    assert out["opened"] == ["/schedules?x=1", "/settings#appearance"]
    assert out["back"] == 1
    assert out["external"] == ["https://example.com/", "https://evil.example.com/", "https://evil.example.com/"]
    # The hotkey while the window is open focuses its composer.
    assert out["toggledToAnswer"] is True and out["focusSent"] == ["quick-ask:focus"]
    # The close button hides it; hidden, the hotkey shows the box again.
    assert out["closePrevented"] is True and out["visibleAfterClose"] is False
    assert out["destroyedAfterClose"] is False and out["focusWhenHidden"] is False
    # Light on the PC: hidden for the idle span it is destroyed.
    assert out["idleDelay"] == [10 * 60 * 1000]
    assert out["destroyedAfterIdle"] is True and out["liveAfterIdle"] is True
    # One at a time: a hidden window is reused for the next ask.
    assert out["builtAfterThree"] == 2
    assert out["win2Loads"] == 2 and out["win2Visible"] is False
    # Quit closes it for real.
    assert out["quitClosePrevented"] is False and out["quitDestroyed"] is True


def test_the_box_routes_to_the_open_answer_window_and_never_loads_the_main_window():
    toggle = _lift("function toggleSpotlight(")
    assert "if (focusQuickAnswer()) return;" in toggle
    assert toggle.index("focusQuickAnswer()") < toggle.index("createSpotlightWindow()")
    run = _lift("async function runSpotlightTask(")
    assert "mainWin" not in run and "showMainWindow" not in run
    assert "openQuickAnswer()" in run
    # The answer window has no blur handler: only the box hides on blur.
    body = _lift("function createQuickAnswerWindow(")
    assert '"blur"' not in body
    assert 'win.on("hide", () => scheduleQuickAnswerDestroy(win));' in body
    assert 'win.on("show", () => cancelQuickAnswerDestroy());' in body
    # The box's own idle timer is not shared with the answer window.
    assert "scheduleSpotlightDestroy" not in body


# ---------------------------------------------------------------------------
# The IPC: sender-checked, and only the answer window may move its chat
# ---------------------------------------------------------------------------

_IPC = """
const DASHBOARD_URL = "http://localhost:8788";
const DASHBOARD_ORIGINS = new Set(["http://localhost:8788", "http://127.0.0.1:8788"]);
function isDashboardUrl(url) { try { return DASHBOARD_ORIGINS.has(new URL(String(url)).origin); } catch { return false; } }
__TRUSTED__
const opened = [];
function openDashboardPath(p) { opened.push(p); return true; }
const answerContents = { id: "answer" };
const otherContents = { id: "main" };
let hidden = 0;
let quickAnswerWin = { isDestroyed() { return false; }, isVisible() { return true; }, hide() { hidden += 1; }, webContents: answerContents };
let pendingQuickAsk = null;
const QUICK_ASK_TTL_MS = 120000;
__LIFTED__
const ev = (url, sender) => ({ senderFrame: { url }, sender });
const out = {};
// A frame that is not the dashboard: refused, nothing opens.
out.offOrigin = quickAskToMain(ev("https://evil.example.com/", answerContents), { thread: "t1" });
// The dashboard, but ANOTHER window (the main one): refused.
out.otherWindow = quickAskToMain(ev("http://localhost:8788/chat", otherContents), { thread: "t1" });
out.openedAfterRefusals = opened.slice();
out.hiddenAfterRefusals = hidden;
// The answer window: the chat opens in the main window, the small one hides.
out.cont = quickAskToMain(ev("http://localhost:8788/chat?compact=1", answerContents), { thread: "t1" });
out.hiddenAfterContinue = hidden;
// A door: opens in the main window, the answer stays on screen.
out.door = quickAskToMain(ev("http://localhost:8788/chat", answerContents), { path: "/schedules" });
out.hiddenAfterDoor = hidden;
// A bad target: refused in words, nothing opens.
out.bad = quickAskToMain(ev("http://localhost:8788/chat", answerContents), { path: "//evil.example.com" });
out.opened = opened.slice();
// Esc's hide: only the answer window may ask.
out.hideOther = quickAskHide(ev("http://localhost:8788/chat", otherContents));
out.hideOff = quickAskHide(ev("https://evil.example.com/", answerContents));
out.hideOwn = quickAskHide(ev("http://localhost:8788/chat", answerContents));
out.hiddenAtEnd = hidden;
// The words: only the answer window's page takes them.
pendingQuickAsk = { text: "plain English?", at: Date.now() };
out.takeOther = quickAskTake(ev("http://localhost:8788/chat?quick=1", otherContents));
out.takeOwn = quickAskTake(ev("http://localhost:8788/chat?quick=1", answerContents));
console.log(JSON.stringify(out));
"""


def _ipc_lifted() -> str:
    return "\n".join(
        [
            _decl("QUICK_THREAD_RX"),
            _lift("function takeQuickAsk("),
            _lift("function quickAnswerMainPath("),
            _lift("function liveQuickAnswer("),
            _lift("function isFromQuickAnswer("),
            _lift("function hideQuickAnswer("),
            _lift("function quickAskToMain("),
            _lift("function quickAskHide("),
            _lift("function quickAskTake("),
        ]
    )


@requires_node
def test_continue_in_the_main_window_refuses_any_frame_but_the_answer_windows_own(tmp_path):
    script = _IPC.replace("__TRUSTED__", _lift("function isTrustedDashboardSender(")).replace(
        "__LIFTED__", _ipc_lifted()
    )
    out = _run(script, tmp_path, "ipc")
    assert out["offOrigin"] is None and out["otherWindow"] is None
    assert out["openedAfterRefusals"] == [] and out["hiddenAfterRefusals"] == 0
    assert out["cont"] == {"ok": True} and out["hiddenAfterContinue"] == 1
    assert out["door"] == {"ok": True} and out["hiddenAfterDoor"] == 1
    assert out["bad"] == {"ok": False}
    assert out["opened"] == ["/chat?thread=t1", "/schedules"]
    assert out["hideOther"] is False and out["hideOff"] is False and out["hideOwn"] is True
    assert out["hiddenAtEnd"] == 2
    # The words were typed for the answer window: no other window takes them.
    assert out["takeOther"] is None and out["takeOwn"] == "plain English?"


def test_the_handlers_are_wired_and_the_preload_exposes_them():
    src = _src()
    for chan, fn in (
        ("quick-ask:to-main", "quickAskToMain(event, raw)"),
        ("quick-ask:hide", "quickAskHide(event)"),
        ("quick-ask:take", "quickAskTake(event)"),
    ):
        at = src.index(f'ipcMain.handle("{chan}"')
        assert fn in src[at : at + 200], chan
    pre = _read("desktop/preload.js")
    assert 'const QUICK_ANSWER = readArg("--ij-quick-answer=") === "1";' in pre
    at = pre.index("quickAsk: {")
    block = pre[at : pre.index("\n  },", at)]
    assert 'take: () => ipcRenderer.invoke("quick-ask:take"),' in block
    assert "isAnswerWindow: QUICK_ANSWER," in block
    assert 'ipcRenderer.invoke("quick-ask:to-main", quickTarget(target))' in block
    assert 'hide: () => ipcRenderer.invoke("quick-ask:hide"),' in block
    assert 'ipcRenderer.on("quick-ask:focus", handler)' in block
    # The box's own bridge still holds no clipboard and no dashboard reach.
    box = _read("desktop/spotlight-preload.js")
    assert "clipboard" not in box and "quick-ask" not in box


def test_the_box_says_where_the_answer_opens_and_the_tray_calls_it_quick_ask():
    html = _read("desktop/spotlight.html")
    assert "The answer opens here." in html
    assert "Opens in chat" not in html
    assert "<title>Iron Jarvis — Quick Ask</title>" in html
    menu = _lift("function buildTrayContextMenu(")
    assert "Quick Ask…" in menu and "Quick task" not in menu
    # One name: the vocabulary row, with the old word kept for search.
    vocab = _read("VOCABULARY.md")
    row = next(line for line in vocab.splitlines() if "**Quick Ask**" in line)
    assert "quick task" in row.lower()
    assert re.search(r"Continue in the main window", row)
