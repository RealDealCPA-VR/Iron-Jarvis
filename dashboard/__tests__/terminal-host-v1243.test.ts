/**
 * v1.243.0 — Build's terminals OUTLIVE the Build page.
 *
 * The user's report: "when I'm working in Build and switch module and go
 * back, the terminals sometimes disconnect or show a strange string of
 * characters, and it takes a while to come back to what I was working on."
 *
 * Every pane used to own its xterm and its WebSocket inside a mount effect,
 * so leaving the page destroyed both and coming back rebuilt them from a full
 * scrollback replay. That replay re-parsed old QUERIES (a live pane's saved
 * scrollback held Codex's OSC 10/11 colour queries), xterm answered them
 * again, and a filter that knew only four CSI shapes let the colour answers
 * into the shell as typed text.
 *
 * paneHost.ts keeps the terminal + socket in a registry the page's unmount
 * does not touch: an unmount PARKS, a mount ADOPTS. What this file pins:
 *  - a parked host is the SAME terminal on the SAME socket when the pane
 *    comes back — no new socket, no reset, no replay;
 *  - a replay (a real re-attach) is delimited by the daemon's empty frame,
 *    and every answer xterm generates before the parser reaches it is dropped
 *    — whatever its shape — while keystrokes and LIVE answers go through;
 *  - the registry builds one host per pane, ends it only on close (or when
 *    the daemon no longer has the pane), and never leaks a socket for a pane
 *    closed mid-build.
 * jsdom cannot run xterm, so the terminal and the socket are fakes shaped like
 * the few members the host uses; the pane's wiring is source-pinned at the
 * bottom (the house idiom — v1.163.0, v1.190.0).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  REPLAY_FALLBACK_MS,
  acquirePaneHost,
  disposePaneHost,
  isReplayEnd,
  isTerminalReport,
  paneHostIds,
  resetPaneHostsForTests,
  retainPaneHosts,
  type PaneHost,
  type PaneHostView,
} from "@/components/terminal/paneHost";

/* ---- fakes ------------------------------------------------------------------ */

class FakeTerm {
  rows = 24;
  cols = 80;
  writes: Array<string | Uint8Array> = [];
  resets = 0;
  refreshes = 0;
  disposed = false;
  buffer = { active: { viewportY: 0, baseY: 0, type: "normal" } };
  scrolledTo: number | "bottom" | null = null;
  private dataListener: ((d: string) => void) | null = null;
  /** Write callbacks, run by parse() — xterm parses asynchronously. */
  private pending: Array<() => void> = [];

  open(parent: HTMLElement) {
    const el = document.createElement("div");
    el.className = "xterm";
    parent.appendChild(el);
  }
  loadAddon() {}
  write(data: string | Uint8Array, cb?: () => void) {
    this.writes.push(data);
    if (cb) this.pending.push(cb);
  }
  /** The parser catches up with everything written so far. */
  parse() {
    const due = this.pending;
    this.pending = [];
    due.forEach((cb) => cb());
  }
  reset() {
    this.resets += 1;
  }
  refresh() {
    this.refreshes += 1;
  }
  onData(listener: (d: string) => void) {
    this.dataListener = listener;
    return { dispose: () => {} };
  }
  /** What xterm does when it answers a query OR the user types: fire onData. */
  emit(d: string) {
    this.dataListener?.(d);
  }
  attachCustomKeyEventHandler() {}
  scrollToLine(n: number) {
    this.scrolledTo = n;
  }
  scrollToBottom() {
    this.scrolledTo = "bottom";
  }
  focus() {}
  dispose() {
    this.disposed = true;
  }
}

class FakeSocket {
  static all: FakeSocket[] = [];
  readyState = 0;
  binaryType = "";
  sent: string[] = [];
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public id: string) {
    FakeSocket.all.push(this);
  }
  send(d: string) {
    this.sent.push(d);
  }
  close() {
    this.closed = true;
    this.readyState = 3;
  }
  /* drivers */
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  frame(data: unknown) {
    this.onmessage?.({ data });
  }
  drop(code = 1006) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
}

let terms: FakeTerm[] = [];
const deps = {
  createTerminal: async () => {
    const t = new FakeTerm();
    terms.push(t);
    return { term: t as never, fit: { fit: () => {} } as never };
  },
  openSocket: (id: string) => new FakeSocket(id) as never,
};

type RecordingView = PaneHostView & {
  conns: Array<[string, boolean]>;
  outputs: Array<[unknown, boolean]>;
};
function recordingView(): RecordingView {
  const v: RecordingView = {
    conns: [],
    outputs: [],
    onConn: (s, lost) => v.conns.push([s, lost]),
    onOutput: (data, replaying) => v.outputs.push([data, replaying]),
    onOpen: () => {},
  };
  return v;
}

function holder(): HTMLDivElement {
  const el = document.createElement("div");
  document.body.appendChild(el);
  return el;
}

const bytes = (s: string) => new TextEncoder().encode(s).buffer as ArrayBuffer;
const REPLAY_END = () => new ArrayBuffer(0);
const lastSocket = () => FakeSocket.all[FakeSocket.all.length - 1];

/** A host that is adopted, connected, and past its (empty) replay. */
async function liveHost(id = "t1"): Promise<{
  host: PaneHost;
  term: FakeTerm;
  sock: FakeSocket;
  owner: object;
  view: RecordingView;
  where: HTMLDivElement;
}> {
  const host = await acquirePaneHost(id, deps);
  const term = terms[terms.length - 1];
  const where = holder();
  const owner = {};
  const view = recordingView();
  host.adopt(where, owner, view);
  host.start();
  const sock = lastSocket();
  sock.open();
  sock.frame(REPLAY_END());
  term.parse();
  return { host, term, sock, owner, view, where };
}

beforeEach(() => {
  resetPaneHostsForTests();
  FakeSocket.all = [];
  terms = [];
});
afterEach(() => {
  resetPaneHostsForTests();
  vi.useRealTimers();
});

/* ---- the report filter ------------------------------------------------------ */

describe("isTerminalReport — every answer xterm 6 generates, and no keystroke", () => {
  // Read off @xterm/xterm 6's triggerDataEvent sites (InputHandler.ts,
  // CoreBrowserTerminal.ts), not invented.
  const REPORTS = [
    "\x1b[?1;2c", // DA1
    "\x1b[?6c", // DA1 (VT102)
    "\x1b[>0;276;0c", // DA2
    "\x1b[0n", // DSR ok
    "\x1b[12;40R", // CPR
    "\x1b[?12;40R", // DECXCPR
    "\x1b[8;24;80t", // text-area size
    "\x1b[4;480;640t", // pixel size
    "\x1b[?2026;2$y", // DECRPM (private) — the synchronized-output probe TUIs send
    "\x1b[4;2$y", // DECRPM (ANSI)
    "\x1b[I", // focus in
    "\x1b[O", // focus out
    "\x1b]11;rgb:0a0a/0c0c/1111\x1b\\", // OSC 11 background, ST
    "\x1b]10;rgb:cdcd/d3d3/dfdf\x07", // OSC 10 foreground, BEL
    "\x1b]4;1;rgb:fbfb/7171/8585\x1b\\", // OSC 4 palette entry
    "\x1bP1$r0m\x1b\\", // DECRQSS reply
    "\x1b]10;rgb:cdcd/d3d3/dfdf\x1b\\\x1b]11;rgb:0a0a/0c0c/1111\x1b\\", // two at once
  ];
  const KEYSTROKES = [
    "a",
    "ls -la\r",
    "\x1b", // Escape
    "\x1b[A", // up
    "\x1b[1;5C", // ctrl+right
    "\x1bOA", // up, application mode
    "\x1bOP", // F1
    "\x1b[15~", // F5
    "\x1b[200~pasted\x1b[201~", // bracketed paste
    "\x1b[<0;10;5M", // SGR mouse press
    "\x1b[M !!", // X10 mouse
    "\x03", // ctrl+c
    "\x7f", // backspace
  ];

  it.each(REPORTS)("drops-able report %j", (r) => {
    expect(isTerminalReport(r)).toBe(true);
  });

  it.each(KEYSTROKES)("never a keystroke %j", (k) => {
    expect(isTerminalReport(k)).toBe(false);
  });

  it("covers the answers the old filter let straight into the shell", () => {
    // The v1.190-era filter, verbatim. These are the shapes a live pane's
    // replay produced (Codex queries OSC 10/11) and it matched none of them.
    const OLD = /^\x1b\[[?>=0-9;]*[cnRt]/;
    for (const leaked of [
      "\x1b]11;rgb:0a0a/0c0c/1111\x1b\\",
      "\x1b]10;rgb:cdcd/d3d3/dfdf\x07",
      "\x1b[?2026;2$y",
      "\x1bP1$r0m\x1b\\",
    ]) {
      expect(OLD.test(leaked)).toBe(false);
      expect(isTerminalReport(leaked)).toBe(true);
    }
  });
});

describe("isReplayEnd — the daemon's empty frame and nothing else", () => {
  it("is exactly an empty binary frame", () => {
    expect(isReplayEnd(new ArrayBuffer(0))).toBe(true);
    expect(isReplayEnd(bytes("x"))).toBe(false);
    expect(isReplayEnd("")).toBe(false);
    expect(isReplayEnd(null)).toBe(false);
  });
});

/* ---- a host outlives its pane ----------------------------------------------- */

describe("a host outlives its pane", () => {
  it("parks on release and comes back on adopt: same terminal, same socket, no replay", async () => {
    const { host, term, sock, owner, where } = await liveHost();
    expect(term.resets).toBe(1); // the one attach
    expect(where.contains(host.wrapper)).toBe(true);

    host.release(owner); // the user switched to another module
    expect(where.contains(host.wrapper)).toBe(false);
    expect(host.wrapper.isConnected).toBe(true); // parked in the document, not destroyed
    expect(sock.closed).toBe(false);
    expect(term.disposed).toBe(false);

    sock.frame(bytes("printed while away")); // the program keeps working
    expect(term.writes[term.writes.length - 1]).toEqual(
      new Uint8Array(bytes("printed while away")),
    );

    const again = await acquirePaneHost("t1", deps); // the user came back
    expect(again).toBe(host);
    const back = holder();
    host.adopt(back, {}, recordingView());
    host.start(); // a returning pane calls it unconditionally
    expect(back.contains(host.wrapper)).toBe(true);
    expect(FakeSocket.all).toHaveLength(1); // no second socket…
    expect(terms).toHaveLength(1); // …no second terminal…
    expect(term.resets).toBe(1); // …and no wipe-and-replay
  });

  it("parks off-screen and inert — never display:none (the v1.190.0 rule)", async () => {
    const { host, owner } = await liveHost();
    host.release(owner);
    const lot = host.wrapper.parentElement as HTMLElement;
    expect(lot.getAttribute("inert")).toBe("");
    expect(lot.getAttribute("aria-hidden")).toBe("true");
    expect(lot.style.left).toBe("-100000px");
    expect(lot.style.display).not.toBe("none");
  });

  it("a stale mount's release cannot park a pane a newer mount holds", async () => {
    // A Rail ⇄ Canvas flip mounts the new pane before React is done with the
    // old one — the old mount's cleanup must not yank the terminal away.
    const { host, owner } = await liveHost();
    const newer = holder();
    host.adopt(newer, {}, recordingView());
    host.release(owner);
    expect(newer.contains(host.wrapper)).toBe(true);
  });

  it("puts the reader back where they were", async () => {
    const { host, term, owner } = await liveHost();
    term.buffer.active = { viewportY: 10, baseY: 50, type: "normal" }; // scrolled up
    host.release(owner);
    host.adopt(holder(), {}, recordingView());
    host.settle();
    expect(term.scrolledTo).toBe(10);

    const owner2 = {};
    host.adopt(holder(), owner2, recordingView());
    term.buffer.active = { viewportY: 50, baseY: 50, type: "normal" }; // following output
    host.release(owner2);
    host.adopt(holder(), {}, recordingView());
    host.settle();
    expect(term.scrolledTo).toBe("bottom");
  });

  it("output keeps flowing to the terminal with nobody mounted", async () => {
    const { host, term, sock, owner, view } = await liveHost();
    host.release(owner);
    const seen = view.outputs.length;
    sock.frame(bytes("a"));
    sock.frame(bytes("b"));
    expect(view.outputs.length).toBe(seen); // the old view hears nothing…
    expect(term.writes.slice(-2)).toEqual([new Uint8Array(bytes("a")), new Uint8Array(bytes("b"))]); // …the terminal gets all of it
  });
});

/* ---- the delimited replay --------------------------------------------------- */

describe("a real re-attach: the replay is delimited, its answers dropped", () => {
  it("drops every answer to a REPLAYED query, passes keystrokes, then answers live queries", async () => {
    const host = await acquirePaneHost("t1", deps);
    const term = terms[0];
    host.adopt(holder(), {}, recordingView());
    host.start();
    const sock = lastSocket();
    sock.open();
    expect(host.isReplaying).toBe(true);

    sock.frame(bytes("\x1b]11;?\x07\x1b]10;?\x07\x1b[c")); // Codex's queries, replayed
    term.emit("\x1b]11;rgb:0a0a/0c0c/1111\x1b\\"); // xterm answers while parsing
    term.emit("\x1b]10;rgb:cdcd/d3d3/dfdf\x1b\\");
    term.emit("\x1b[?1;2c");
    term.emit("typed"); // the user can still type during a replay
    sock.frame(REPLAY_END());
    term.emit("\x1b[?2026;2$y"); // still parsing replay bytes — still stale
    term.parse(); // the parser reaches the end-of-replay write
    expect(host.isReplaying).toBe(false);
    term.emit("\x1b[?1;2c"); // a LIVE query's answer must reach the program

    expect(sock.sent).toEqual(["typed", "\x1b[?1;2c"]);
  });

  it("tells the page which frames are catch-up, so it badges only new output", async () => {
    const host = await acquirePaneHost("t1", deps);
    const view = recordingView();
    host.adopt(holder(), {}, view);
    host.start();
    const sock = lastSocket();
    sock.open();
    sock.frame(bytes("history"));
    sock.frame(REPLAY_END());
    terms[0].parse();
    sock.frame(bytes("new"));
    expect(view.outputs.map(([, replaying]) => replaying)).toEqual([true, false]);
  });

  it("repaints once when the replay lands", async () => {
    const host = await acquirePaneHost("t1", deps);
    host.adopt(holder(), {}, recordingView());
    host.start();
    lastSocket().open();
    const before = terms[0].refreshes;
    lastSocket().frame(REPLAY_END());
    terms[0].parse();
    expect(terms[0].refreshes).toBe(before + 1);
  });

  it("a daemon that never sends the marker unmutes after the fallback", async () => {
    vi.useFakeTimers();
    const host = await acquirePaneHost("t1", deps);
    host.adopt(holder(), {}, recordingView());
    host.start();
    const sock = lastSocket();
    sock.open();
    vi.advanceTimersByTime(REPLAY_FALLBACK_MS);
    expect(host.isReplaying).toBe(false);
    terms[0].emit("\x1b[?1;2c");
    expect(sock.sent).toEqual(["\x1b[?1;2c"]);
  });

  it("a superseded attach's late parse cannot end the NEW attach's replay", async () => {
    const host = await acquirePaneHost("t1", deps);
    host.adopt(holder(), {}, recordingView());
    host.start();
    const first = lastSocket();
    first.open();
    first.frame(REPLAY_END()); // its callback is queued, not yet run
    host.reconnectNow();
    lastSocket().open(); // the new attach begins its own replay
    terms[0].parse(); // the OLD callback fires now
    expect(host.isReplaying).toBe(true);
  });
});

/* ---- links ------------------------------------------------------------------- */

describe("links", () => {
  it("a link lost while parked heals the moment the pane comes back", async () => {
    vi.useFakeTimers();
    const { host, owner } = await liveHost();
    host.release(owner);
    // The daemon restarts for an update while the user is elsewhere; the
    // quick retries run out with nobody watching.
    for (let i = 0; i < 5; i += 1) {
      lastSocket().drop();
      vi.advanceTimersByTime(2500);
    }
    expect(host.state).toBe("closed");
    expect(host.lostLink).toBe(true);
    const before = FakeSocket.all.length;
    const view = recordingView();
    host.adopt(holder(), {}, view);
    expect(FakeSocket.all.length).toBe(before + 1); // reconnected on its own
    expect(view.conns[view.conns.length - 1]).toEqual(["reconnecting", false]);
  });

  it("an exited shell (4000) is final: no retry, and no heal on return", async () => {
    vi.useFakeTimers();
    const { host, owner, sock } = await liveHost();
    sock.drop(4000);
    vi.advanceTimersByTime(30_000);
    expect(host.state).toBe("closed");
    expect(host.lostLink).toBe(false);
    host.release(owner);
    host.adopt(holder(), {}, recordingView());
    expect(FakeSocket.all).toHaveLength(1);
  });

  it("send is honest: false when the socket is not open", async () => {
    const host = await acquirePaneHost("t1", deps);
    expect(host.send("ls\r")).toBe(false); // never started
    host.adopt(holder(), {}, recordingView());
    host.start();
    expect(host.send("ls\r")).toBe(false); // still connecting
    lastSocket().open();
    expect(host.send("ls\r")).toBe(true);
    expect(lastSocket().sent).toEqual(["ls\r"]);
  });
});

/* ---- the registry ------------------------------------------------------------ */

describe("the registry", () => {
  it("builds one host however many mounts ask at once", async () => {
    const [a, b] = await Promise.all([acquirePaneHost("t1", deps), acquirePaneHost("t1", deps)]);
    expect(a).toBe(b);
    expect(terms).toHaveLength(1);
  });

  it("a new host is not connected until a pane starts it (the replay needs the true width)", async () => {
    await acquirePaneHost("t1", deps);
    expect(FakeSocket.all).toHaveLength(0);
  });

  it("disposePaneHost ends the terminal and the socket for good", async () => {
    const { host, term, sock } = await liveHost();
    disposePaneHost("t1");
    expect(host.isDisposed).toBe(true);
    expect(term.disposed).toBe(true);
    expect(sock.closed).toBe(true);
    expect(host.wrapper.isConnected).toBe(false);
    expect(paneHostIds()).toEqual([]);
    const fresh = await acquirePaneHost("t1", deps);
    expect(fresh).not.toBe(host);
  });

  it("retainPaneHosts keeps only the panes the daemon still has", async () => {
    await acquirePaneHost("t1", deps);
    await acquirePaneHost("t2", deps);
    retainPaneHosts(["t2"]);
    expect(paneHostIds()).toEqual(["t2"]);
    expect(terms[0].disposed).toBe(true);
    expect(terms[1].disposed).toBe(false);
  });

  it("a pane closed while its host is still being built never gets one", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    const slow = {
      ...deps,
      createTerminal: async () => {
        await gate;
        return deps.createTerminal();
      },
    };
    const pending = acquirePaneHost("t9", slow);
    disposePaneHost("t9");
    release();
    await expect(pending).rejects.toThrow(/closed/);
    expect(paneHostIds()).not.toContain("t9");
    expect(terms[0].disposed).toBe(true);
  });
});

/* ---- the wiring (source-pinned: jsdom cannot render xterm) ------------------ */

describe("the pane and page wiring (source-pinned)", () => {
  const read = (...p: string[]) =>
    readFileSync(join(process.cwd(), ...p), "utf8").replace(/\r\n/g, "\n");
  const pane = read("components", "terminal", "TerminalPane.tsx");
  const page = read("app", "terminals", "page.tsx");

  it("the pane PARKS its host on unmount and never disposes the terminal", () => {
    const cleanup = pane.slice(pane.indexOf("return () => {\n      disposed = true;"));
    expect(cleanup).toContain("host?.release(owner);");
    expect(pane).not.toMatch(/term\??\.dispose\(\)/);
    expect(pane).not.toContain("new WebSocket(");
  });

  it("the pane adopts, settles at the true size, THEN starts", () => {
    const adopt = pane.indexOf("h.adopt(holder, owner,");
    const wait = pane.indexOf("await waitForStableSize(holder)", adopt);
    const start = pane.indexOf("h.start();", wait);
    expect(adopt).toBeGreaterThan(-1);
    expect(wait).toBeGreaterThan(adopt);
    expect(start).toBeGreaterThan(wait);
    const between = pane.slice(wait, start);
    expect(between).toContain("if (disposed) return;");
    expect(between).toContain("doFit();");
    expect(between).toContain("h.settle();");
  });

  it("the page ends a host only on close, and drops hosts for panes the daemon lost", () => {
    const close = page.slice(page.indexOf("const closeTerminal = useCallback"));
    expect(close.slice(0, 600)).toContain("disposePaneHost(id);");
    expect(page).toContain("retainPaneHosts(alive.map((t) => t.id));");
  });
});
