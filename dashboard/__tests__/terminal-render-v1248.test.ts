/**
 * v1.248.0 — Build terminals draw on the GPU, never drown in a flood, and a
 * rail pane nobody can see stops drawing.
 *
 * Three client-side changes, all in components/terminal/paneHost.ts:
 *  - GPU RENDERING. A terminal ON SCREEN (adopted, unparked) loads xterm's
 *    WebGL renderer; parked or released, it gives the context back (browsers
 *    cap live WebGL contexts per page). A lost context falls back to the DOM
 *    renderer on the same buffer and socket. No WebGL2 (jsdom, a GPU-less VM)
 *    or `ij.build.webgl = "off"` → the DOM renderer, silently.
 *  - FLOW CONTROL, client half. The host counts bytes handed to xterm that it
 *    has not parsed yet and sends `{"type":"flow","paused":true}` above
 *    FLOW_HIGH, `{"type":"flow","paused":false}` below FLOW_LOW — ONLY to a
 *    daemon new enough to understand it: an older one TYPES any non-resize
 *    text frame into the shell.
 *  - PARKED RAIL PANES. A rail pane behind the focused one parks its terminal
 *    in the lot but keeps its view (badges keep working) and its socket (no
 *    reconnect, no replay); focusing it unparks.
 * jsdom cannot run xterm, so the terminal, socket and GPU addon are fakes
 * shaped like the members the host uses; the pane/page wiring is
 * source-pinned at the bottom (the house idiom).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  FLOW_HIGH,
  FLOW_LOW,
  FLOW_MIN_DAEMON,
  FLOW_PREF_KEY,
  GPU_MAX_LOSSES,
  WEBGL_PREF_KEY,
  acquirePaneHost,
  daemonSupportsFlow,
  flowWanted,
  resetPaneHostsForTests,
  webglWanted,
  type GpuAddon,
  type GpuLoader,
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
  loaded: unknown[] = [];
  buffer = { active: { viewportY: 0, baseY: 0, type: "normal" } };
  scrolledTo: number | "bottom" | null = null;
  private dataListener: ((d: string) => void) | null = null;
  private pending: Array<() => void> = [];

  open(parent: HTMLElement) {
    const el = document.createElement("div");
    el.className = "xterm";
    parent.appendChild(el);
  }
  /** Like xterm: loading an addon activates it (and may throw). */
  loadAddon(addon: { activate?: (t: unknown) => void }) {
    addon.activate?.(this);
    this.loaded.push(addon);
  }
  write(data: string | Uint8Array, cb?: () => void) {
    this.writes.push(data);
    if (cb) this.pending.push(cb);
  }
  /** The parser catches up with everything written so far. */
  parse() {
    this.parseSome(this.pending.length);
  }
  /** The parser gets through the next `n` writes. */
  parseSome(n: number) {
    const due = this.pending.splice(0, n);
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
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  frame(data: unknown) {
    this.onmessage?.({ data });
  }
  /** The flow frames this socket carried, decoded. */
  flow(): Array<{ type: string; paused: boolean }> {
    return this.sent
      .filter((s) => s.startsWith("{"))
      .map((s) => JSON.parse(s) as { type: string; paused: boolean })
      .filter((o) => o.type === "flow");
  }
}

class FakeGpu implements GpuAddon {
  static all: FakeGpu[] = [];
  activated = false;
  disposed = false;
  private loss: (() => void) | null = null;
  constructor() {
    FakeGpu.all.push(this);
  }
  activate() {
    this.activated = true;
  }
  dispose() {
    this.disposed = true;
  }
  onContextLoss(listener: () => void) {
    this.loss = listener;
    return { dispose: () => {} };
  }
  /** The browser took the context away (a driver reset, the per-page cap). */
  loseContext() {
    this.loss?.();
  }
}

const liveGpus = () => FakeGpu.all.filter((g) => g.activated && !g.disposed);
const fakeGpuLoader: GpuLoader = async () => () => new FakeGpu();

let terms: FakeTerm[] = [];
const deps = {
  createTerminal: async () => {
    const t = new FakeTerm();
    terms.push(t);
    return { term: t as never, fit: { fit: () => {} } as never };
  },
  openSocket: (id: string) => new FakeSocket(id) as never,
  loadGpu: fakeGpuLoader,
};

function recordingView(): PaneHostView & { outputs: unknown[] } {
  const v = {
    outputs: [] as unknown[],
    onConn: () => {},
    onOutput: (data: unknown) => v.outputs.push(data),
    onOpen: () => {},
  };
  return v;
}

function holder(): HTMLDivElement {
  const el = document.createElement("div");
  document.body.appendChild(el);
  return el;
}

/** Let the (async) GPU loader settle. */
const settleGpu = () => new Promise<void>((r) => setTimeout(r, 0));
const bytes = (n: number) => new Uint8Array(n).buffer as ArrayBuffer;
const REPLAY_END = () => new ArrayBuffer(0);
const lastSocket = () => FakeSocket.all[FakeSocket.all.length - 1];
const inLot = (host: PaneHost) =>
  (host.wrapper.parentElement as HTMLElement | null)?.dataset.ijTerminalParking === "true";

async function liveHost(id = "t1", d: typeof deps | Omit<typeof deps, "loadGpu"> = deps) {
  const host = await acquirePaneHost(id, d);
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
  FakeGpu.all = [];
  terms = [];
});
afterEach(() => {
  resetPaneHostsForTests();
  localStorage.removeItem(FLOW_PREF_KEY);
  localStorage.removeItem(WEBGL_PREF_KEY);
});

/* ---- GPU rendering ---------------------------------------------------------- */

describe("GPU rendering: a context only while the terminal is on screen", () => {
  it("draws on the GPU on screen, gives the context back when released, takes a new one on return", async () => {
    const { host, owner, sock } = await liveHost();
    await settleGpu();
    expect(host.gpuActive).toBe(true);
    expect(liveGpus()).toHaveLength(1);

    host.release(owner); // left Build
    expect(host.gpuActive).toBe(false);
    expect(FakeGpu.all[0].disposed).toBe(true);
    expect(liveGpus()).toHaveLength(0);

    host.adopt(holder(), {}, recordingView()); // came back
    await settleGpu();
    expect(liveGpus()).toHaveLength(1);
    expect(FakeGpu.all).toHaveLength(2); // a NEW context, not a zombie
    expect(FakeSocket.all).toEqual([sock]); // and still the one socket
  });

  it("a pane parked while its renderer is still loading never takes a context", async () => {
    let open: () => void = () => {};
    const gate = new Promise<void>((r) => {
      open = r;
    });
    const slow: GpuLoader = async () => {
      await gate;
      return () => new FakeGpu();
    };
    const { host, owner } = await liveHost("t1", { ...deps, loadGpu: slow });
    host.park(owner);
    open();
    await settleGpu();
    expect(FakeGpu.all).toHaveLength(0);
    expect(host.gpuActive).toBe(false);
  });

  it("a lost context falls back to the DOM renderer on the SAME buffer and socket", async () => {
    const { host, term, sock, owner } = await liveHost();
    await settleGpu();
    const refreshed = term.refreshes;
    FakeGpu.all[0].loseContext();
    expect(host.gpuActive).toBe(false);
    expect(FakeGpu.all[0].disposed).toBe(true);
    expect(term.refreshes).toBe(refreshed + 1); // repainted by the DOM renderer
    expect(sock.closed).toBe(false);
    expect(term.disposed).toBe(false);
    sock.frame(bytes(3));
    expect(term.writes[term.writes.length - 1]).toEqual(new Uint8Array(3));

    // The next time it is on screen it tries again — up to GPU_MAX_LOSSES.
    let o = owner;
    for (let i = 1; i < GPU_MAX_LOSSES; i += 1) {
      host.release(o);
      o = {};
      host.adopt(holder(), o, recordingView());
      await settleGpu();
      expect(host.gpuActive).toBe(true);
      FakeGpu.all[FakeGpu.all.length - 1].loseContext();
    }
    const made = FakeGpu.all.length;
    host.release(o);
    host.adopt(holder(), {}, recordingView());
    await settleGpu();
    expect(FakeGpu.all).toHaveLength(made); // gave up on the GPU
    expect(host.gpuActive).toBe(false);
  });

  it("a machine with no WebGL2 context stays on the DOM renderer and does not keep trying", async () => {
    let calls = 0;
    const broken: GpuLoader = async () => () => {
      calls += 1;
      return {
        activate() {
          throw new Error("could not create a WebGL2 context");
        },
        dispose() {},
      };
    };
    const { host, owner } = await liveHost("t1", { ...deps, loadGpu: broken });
    await settleGpu();
    expect(calls).toBe(1);
    expect(host.gpuActive).toBe(false);
    host.release(owner);
    host.adopt(holder(), {}, recordingView());
    await settleGpu();
    expect(calls).toBe(1);
  });

  it("the default loader never tries without WebGL2 (jsdom, a GPU-less VM)", async () => {
    const { term } = await liveHost("t1", {
      createTerminal: deps.createTerminal,
      openSocket: deps.openSocket,
    });
    await settleGpu();
    expect(term.loaded).toEqual([]);
  });

  it("`ij.build.webgl = off` switches the GPU off", () => {
    expect(webglWanted()).toBe(true);
    localStorage.setItem(WEBGL_PREF_KEY, "off");
    expect(webglWanted()).toBe(false);
  });

  it("closing the pane gives the context back", async () => {
    const { host } = await liveHost();
    await settleGpu();
    host.dispose();
    expect(liveGpus()).toHaveLength(0);
  });
});

/* ---- parked rail panes ------------------------------------------------------ */

describe("a rail pane behind the focused one parks — and keeps listening", () => {
  it("parks off-screen without a context, keeps its view and socket; unparks with no replay", async () => {
    const { host, term, sock, owner, view, where } = await liveHost();
    await settleGpu();

    host.park(owner);
    expect(host.isParked).toBe(true);
    expect(inLot(host)).toBe(true);
    expect(host.gpuActive).toBe(false);
    sock.frame(bytes(5)); // the program keeps printing
    expect(view.outputs).toHaveLength(1); // …and the rail still hears it (badges)

    host.unpark(where, owner);
    expect(host.isParked).toBe(false);
    expect(where.contains(host.wrapper)).toBe(true);
    await settleGpu();
    expect(host.gpuActive).toBe(true);
    expect(FakeSocket.all).toEqual([sock]); // no reconnect
    expect(term.resets).toBe(1); // no wipe-and-replay
  });

  it("only the mount that holds the host may park or unpark it", async () => {
    const { host, owner, where } = await liveHost();
    host.park({});
    expect(host.isParked).toBe(false);
    host.park(owner);
    host.unpark(where, {});
    expect(host.isParked).toBe(true);
  });

  it("keeps the reader's place while parked and restores it on return", async () => {
    const { host, term, owner, where } = await liveHost();
    term.buffer.active = { viewportY: 10, baseY: 50, type: "normal" }; // scrolled up
    host.park(owner);
    host.settle(); // a stray settle while parked must not spend the place
    expect(term.scrolledTo).toBe(null);
    host.unpark(where, owner);
    host.settle();
    expect(term.scrolledTo).toBe(10);
  });

  it("a release of a parked pane keeps the place it recorded when it parked", async () => {
    const { host, term, owner } = await liveHost();
    term.buffer.active = { viewportY: 7, baseY: 50, type: "normal" };
    host.park(owner);
    term.buffer.active = { viewportY: 50, baseY: 50, type: "normal" }; // output moved on
    host.release(owner);
    host.adopt(holder(), {}, recordingView());
    host.settle();
    expect(term.scrolledTo).toBe(7);
  });
});

/* ---- flow control ------------------------------------------------------------ */

describe("which daemon may be sent a flow frame", () => {
  it("is the first version that understands one, or newer", () => {
    expect(FLOW_MIN_DAEMON).toBe("1.248.0");
    for (const v of ["1.248.0", "1.248.3", "1.249.0", "2.0.0", "v1.248.0"]) {
      expect(daemonSupportsFlow(v)).toBe(true);
    }
    for (const v of ["1.247.9", "1.246.1", "0.999.999", "", "dev", null, undefined]) {
      expect(daemonSupportsFlow(v)).toBe(false);
    }
  });

  it("follows the version unless the override says otherwise", () => {
    expect(flowWanted("1.248.0")).toBe(true);
    expect(flowWanted("1.246.1")).toBe(false);
    localStorage.setItem(FLOW_PREF_KEY, "off");
    expect(flowWanted("9.9.9")).toBe(false);
    localStorage.setItem(FLOW_PREF_KEY, "on");
    expect(flowWanted(undefined)).toBe(true);
  });
});

describe("flow control: hold the stream while xterm is behind", () => {
  it("a flood holds the stream ONCE and resumes it ONCE the parser is below the low mark", async () => {
    const { host, term, sock } = await liveHost();
    host.flowEnabled = true;
    const chunk = 200 * 1024;
    for (let i = 0; i < 3; i += 1) sock.frame(bytes(chunk)); // 600 KiB queued
    expect(host.flowPendingBytes).toBe(3 * chunk);
    expect(sock.flow()).toEqual([{ type: "flow", paused: true }]);
    sock.frame(bytes(chunk)); // still above — no second hold
    expect(sock.flow()).toHaveLength(1);

    term.parseSome(2); // 400 KiB left — below HIGH but above LOW: still held
    expect(host.flowPendingBytes).toBe(2 * chunk);
    expect(sock.flow()).toHaveLength(1);
    term.parseSome(1); // 200 KiB — still above LOW (128 KiB)
    expect(sock.flow()).toHaveLength(1);
    term.parseSome(1); // drained
    expect(host.flowPendingBytes).toBe(0);
    expect(sock.flow()).toEqual([
      { type: "flow", paused: true },
      { type: "flow", paused: false },
    ]);
    expect(host.isFlowPaused).toBe(false);
  });

  it("the thresholds are the agreed contract", () => {
    expect(FLOW_HIGH).toBe(512 * 1024);
    expect(FLOW_LOW).toBe(128 * 1024);
  });

  it("an OLDER daemon is never sent one — it would type it into the shell", async () => {
    const { host, sock } = await liveHost(); // flowEnabled defaults to false
    for (let i = 0; i < 5; i += 1) sock.frame(bytes(FLOW_HIGH));
    expect(sock.sent).toEqual([]);
    expect(host.isFlowPaused).toBe(false);
  });

  it("the replay counts: a huge catch-up holds the stream too", async () => {
    const host = await acquirePaneHost("t1", deps);
    host.flowEnabled = true;
    host.adopt(holder(), {}, recordingView());
    host.start();
    const sock = lastSocket();
    sock.open();
    expect(host.isReplaying).toBe(true);
    sock.frame(bytes(FLOW_HIGH + 1));
    expect(sock.flow()).toEqual([{ type: "flow", paused: true }]);
  });

  it("never sends on a socket that is not open", async () => {
    const { host, sock } = await liveHost();
    host.flowEnabled = true;
    sock.readyState = 2; // closing
    sock.frame(bytes(FLOW_HIGH + 1));
    expect(sock.sent).toEqual([]);
    expect(host.isFlowPaused).toBe(false);
    sock.readyState = 1;
    sock.frame(bytes(1));
    expect(sock.flow()).toEqual([{ type: "flow", paused: true }]);
  });

  it("a reconnect starts unpaused and forgets the old socket's bytes", async () => {
    const { host, term, sock } = await liveHost();
    host.flowEnabled = true;
    sock.frame(bytes(FLOW_HIGH + 1));
    expect(host.isFlowPaused).toBe(true);
    host.reconnectNow();
    const fresh = lastSocket();
    expect(fresh).not.toBe(sock);
    expect(host.isFlowPaused).toBe(false);
    expect(host.flowPendingBytes).toBe(0);
    fresh.open();
    term.parse(); // the OLD socket's writes finish parsing now
    expect(host.flowPendingBytes).toBe(0); // not negative
    expect(fresh.flow()).toEqual([]); // no stray resume on the new stream
  });

  it("switching flow off while the stream is held lifts the hold first", async () => {
    const { host, sock } = await liveHost();
    host.flowEnabled = true;
    sock.frame(bytes(FLOW_HIGH + 1));
    host.flowEnabled = false;
    expect(sock.flow()).toEqual([
      { type: "flow", paused: true },
      { type: "flow", paused: false },
    ]);
    expect(host.isFlowPaused).toBe(false);
  });

  it("a parked host keeps counting — xterm parses off-screen", async () => {
    const { host, term, sock, owner } = await liveHost();
    host.flowEnabled = true;
    host.park(owner);
    sock.frame(bytes(FLOW_HIGH + 1));
    expect(sock.flow()).toHaveLength(1);
    term.parse();
    expect(sock.flow()).toHaveLength(2);
  });
});

/* ---- the wiring (source-pinned: jsdom cannot render xterm) ------------------- */

describe("the pane and page wiring (source-pinned)", () => {
  const read = (...p: string[]) =>
    readFileSync(join(process.cwd(), ...p), "utf8").replace(/\r\n/g, "\n");
  const pane = read("components", "terminal", "TerminalPane.tsx");
  const page = read("app", "terminals", "page.tsx");
  const block = (start: string, end = "\n    };\n") => {
    const at = pane.indexOf(start);
    expect(at).toBeGreaterThan(-1);
    return pane.slice(at, pane.indexOf(end, at) + end.length);
  };

  it("the page parks every RAIL pane but the focused one, and no canvas pane", () => {
    expect(page).toContain('parked={shape === "rail" && activeId !== t.id}');
  });

  it("a pane parks only AFTER it has fitted and connected at its true size", () => {
    const wait = pane.indexOf("await waitForStableSize(holder);");
    const start = pane.indexOf("h.start();", wait);
    const park = pane.indexOf("if (parkedRef.current) h.park(owner);", start);
    expect(wait).toBeGreaterThan(-1);
    expect(start).toBeGreaterThan(wait);
    expect(park).toBeGreaterThan(start);
  });

  it("a parked terminal is never fitted to the lot and never resizes the PTY", () => {
    expect(block("    const doFit = () => {")).toContain("if (host?.isParked) return;");
    expect(block("    const sendResize = (force = false) => {")).toContain(
      "if (host?.isParked) return;",
    );
  });

  it("focusing a parked pane unparks, waits for a stable size, claims it, then settles", () => {
    const eff = pane.slice(pane.indexOf("h.unpark(holder, owner);"));
    const wait = eff.indexOf("await waitForStableSize(holder);");
    const claim = eff.indexOf("claimSizeRef.current?.();");
    const settle = eff.indexOf("h.settle();");
    expect(wait).toBeGreaterThan(-1);
    expect(claim).toBeGreaterThan(wait);
    expect(settle).toBeGreaterThan(claim);
    expect(pane).toContain("}, [parked]);");
  });

  it("flow frames are enabled from the daemon's version, never unconditionally", () => {
    expect(pane).toContain("const flowOn = flowWanted(health?.version);");
    expect(pane).toContain("h.flowEnabled = flowOnRef.current;");
    expect(pane).not.toMatch(/flowEnabled\s*=\s*true/);
  });
});
