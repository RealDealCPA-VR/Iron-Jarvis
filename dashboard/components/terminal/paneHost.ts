// Build's terminals OUTLIVE the Build page (v1.243.0).
//
// THE REPORT: "when I'm working in Build and switch module and go back, the
// terminals sometimes disconnect or show a strange string of characters, and
// it takes a while to come back to what I was working on. It looks and works
// great as long as I don't leave."
//
// THE MECHANISM. Every TerminalPane owned its xterm AND its WebSocket inside a
// mount effect, so leaving the page — or flipping Rail ⇄ Canvas, which swaps
// the element the pane is rendered in — DISPOSED both. Coming back rebuilt
// each terminal from nothing: a fresh socket, a full scrollback replay (up to
// 256 KB of raw TUI bytes, re-parsed), and the daemon's repaint wiggle, which
// makes a running Claude/Codex redraw its whole screen. Three defects rode
// that rebuild:
//  1. THE STRANGE STRING. A replay re-parses every QUERY the programs in the
//     pane ever sent (a live pane's saved scrollback held Codex's OSC 10/11
//     colour queries and a DA1). xterm answers each one again, and the answer
//     is typed into whatever is running NOW. The old filter knew only CSI
//     answers ending c/n/R/t, for 800 ms by the clock — an OSC colour reply
//     (`ESC ] 11 ; rgb:0a0a/0c0c/1111 ESC \`), a DECRPM (`ESC [ ? 2026 ; 2 $ y`)
//     or a DCS reply went straight into the shell as typed text.
//  2. THE STALL. A pane with no socket had no reader, and a PTY nobody reads
//     fills its output pipe — so the Claude you left working STOPPED until
//     you came back. (The daemon half of this ship drains it now.)
//  3. THE WAIT. The rebuild itself: import, settle, connect, replay, redraw.
//
// THE FIX IS TO NOT REBUILD. A PaneHost is one pane's terminal and socket,
// held in a module-level registry the page's unmount does not touch. xterm is
// opened ONCE, into a wrapper element; a mounted pane ADOPTS the host (the
// wrapper moves into its holder) and an unmount PARKS it (the wrapper moves to
// an off-screen lot, the socket stays open, output keeps landing in the
// buffer). Coming back is a DOM move: no replay, no wiggle, no redraw, and the
// program never noticed anyone left. (VS Code keeps its terminals alive across
// panel moves the same way.)
//
// A REAL re-attach still happens — a reload, a daemon restart, a dropped link
// — and for that the replay is now DELIMITED: the daemon ends it with an empty
// frame, and every report xterm generates while parsing up to that frame is
// dropped, whatever its shape and however long the parse takes.

import type { Terminal } from "@xterm/xterm";
import type { FitAddon } from "@xterm/addon-fit";
import { wsUrl } from "@/lib/api";
import { terminalReconnectDelayMs } from "@/components/terminal/paneStatusCore";

export type ConnState = "connecting" | "open" | "reconnecting" | "closed";

/** WebSocket.OPEN, spelled out so a test's fake socket needs no real class. */
const WS_OPEN = 1;

/** A daemon too old to send the end-of-replay frame must not keep xterm's
 *  answers muted forever — a LIVE query would go unanswered. */
export const REPLAY_FALLBACK_MS = 5000;

/**
 * Is this onData payload an answer xterm GENERATED for a query in the stream
 * it was parsing, rather than something the user typed?
 *
 * The shapes are read off @xterm/xterm 6's own emitters (the
 * `triggerDataEvent` sites in InputHandler and CoreBrowserTerminal), not
 * guessed:
 *  - CSI reports: DA1 `ESC[?1;2c`, DA2 `ESC[>0;276;0c`, DSR `ESC[0n`, CPR
 *    `ESC[12;40R` and DECXCPR `ESC[?12;40R`, window reports `ESC[8;24;80t`,
 *    DECRPM `ESC[?2026;2$y`, focus `ESC[I` / `ESC[O`;
 *  - OSC colour reports: `ESC]11;rgb:0a0a/0c0c/1111` + ST or BEL;
 *  - DCS replies (DECRQSS): `ESC P … ESC \`.
 * No keystroke has these shapes: arrows and function keys end in A–D, H, F
 * or `~`, or ride SS3 (`ESC O`); bracketed paste ends in `~`; a mouse report
 * starts `ESC[<` or `ESC[M`.
 */
const REPORT_RE =
  /^(?:\x1b\[[?>=]?[0-9;]*\$?[cnRtyIO]|\x1b\][0-9]+;[^\x07\x1b]*(?:\x07|\x1b\\)|\x1bP[^\x1b]*\x1b\\)+$/;

export function isTerminalReport(data: string): boolean {
  return REPORT_RE.test(data);
}

/** The daemon's end-of-replay frame: one EMPTY binary frame. PTY output is
 *  never empty, so nothing else has this shape. */
export function isReplayEnd(data: unknown): boolean {
  return (
    typeof ArrayBuffer !== "undefined" && data instanceof ArrayBuffer && data.byteLength === 0
  );
}

/** xterm theme tuned to the arc-reactor cyan / near-black aesthetic. */
const XTERM_THEME = {
  background: "#0a0c11",
  foreground: "#cdd3df",
  cursor: "#22d3ee",
  cursorAccent: "#0a0c11",
  selectionBackground: "rgba(34,211,238,0.28)",
  black: "#0b0d11",
  red: "#fb7185",
  green: "#34d399",
  yellow: "#fbbf24",
  blue: "#38bdf8",
  magenta: "#a78bfa",
  cyan: "#22d3ee",
  white: "#cdd3df",
  brightBlack: "#475569",
  brightRed: "#fda4af",
  brightGreen: "#6ee7b7",
  brightYellow: "#fcd34d",
  brightBlue: "#7dd3fc",
  brightMagenta: "#c4b5fd",
  brightCyan: "#67e8f9",
  brightWhite: "#f4f4f5",
} as const;

export type TerminalFactory = () => Promise<{ term: Terminal; fit: FitAddon }>;
export type SocketFactory = (id: string) => WebSocket;

/** The real terminal. xterm is imported HERE, lazily, so it never runs during
 *  SSR / `next build` — the page imports this module statically. */
const createXterm: TerminalFactory = async () => {
  const [{ Terminal: XTerm }, { FitAddon: XFit }] = await Promise.all([
    import("@xterm/xterm"),
    import("@xterm/addon-fit"),
  ]);
  const term = new XTerm({
    cursorBlink: true,
    cursorStyle: "bar",
    fontSize: 12.5,
    lineHeight: 1.15,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
    theme: { ...XTERM_THEME },
    scrollback: 5000,
    allowProposedApi: true,
  });
  const fit = new XFit();
  term.loadAddon(fit);
  return { term, fit };
};

const openAttachSocket: SocketFactory = (id) => new WebSocket(wsUrl(`/terminals/${id}/ws`));

let lot: HTMLDivElement | null = null;

/**
 * Where a parked terminal waits: IN the document, so it keeps real layout and
 * xterm's glyph measurements stay valid, but OFF-SCREEN. xterm's renderer
 * pauses itself when its element stops intersecting the viewport, so a parked
 * pane keeps PARSING (its buffer stays current) and paints nothing. Never
 * `display:none` — the v1.190.0 rule: a terminal with no box measures zero.
 * `inert` keeps a parked terminal out of the tab order.
 */
function parkingLot(): HTMLDivElement {
  if (lot && lot.isConnected) return lot;
  lot = document.createElement("div");
  lot.setAttribute("aria-hidden", "true");
  lot.setAttribute("inert", "");
  lot.dataset.ijTerminalParking = "true";
  Object.assign(lot.style, {
    position: "fixed",
    left: "-100000px",
    top: "0",
    width: "100vw",
    height: "100vh",
    overflow: "hidden",
    visibility: "hidden",
    pointerEvents: "none",
  });
  document.body.appendChild(lot);
  return lot;
}

/** What the mounted pane listens to. Absent while parked. */
export interface PaneHostView {
  /** The connection changed. `lostLink` = a link that dropped, as opposed to
   *  a shell that exited (only the former earns a Reconnect button). */
  onConn(state: ConnState, lostLink: boolean): void;
  /** A frame of PTY output was written to the terminal. `replaying` = it is
   *  the (re)attach catch-up, not something the program just printed. */
  onOutput(data: unknown, replaying: boolean): void;
  /** The socket just opened. Fit and send the size — the daemon's repaint
   *  wiggle keys off an attach's first resize. */
  onOpen(): void;
}

export class PaneHost {
  readonly id: string;
  readonly term: Terminal;
  readonly fit: FitAddon;
  /** The element xterm was opened into, ONCE. It moves between a pane's
   *  holder and the parking lot; xterm is never re-opened (a second `open()`
   *  is a no-op in xterm 6, so moving the element is the way to re-home it). */
  readonly wrapper: HTMLDivElement;
  state: ConnState = "connecting";
  lostLink = false;
  /** Latest /health verdict, written by the mounted pane. While parked it
   *  keeps its last value — and a link lost while parked heals on the next
   *  adopt, so a stale "online" costs nothing. */
  daemonOnline = true;
  /** The mounted pane's keyboard handler (clipboard, scrollback keys). Null
   *  while parked: nothing can be typed into a parked terminal. */
  keyHandler: ((e: KeyboardEvent) => boolean) | null = null;
  /** The last size the mounted pane sent the daemon ("COLSxROWS"), so a
   *  ResizeObserver tick that changed nothing sends nothing (v1.245.0). A
   *  fresh attach always sends: the pane forces its open-time resize. */
  sentSize = "";

  private readonly openSocket: SocketFactory;
  private view: PaneHostView | null = null;
  private owner: object | null = null;
  private ws: WebSocket | null = null;
  private attempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private replaying = false;
  private replayGen = 0;
  private replayFallback: ReturnType<typeof setTimeout> | null = null;
  private started = false;
  private exited = false;
  private disposed = false;
  private parkedAt: { viewportY: number; atBottom: boolean } | null = null;

  constructor(
    id: string,
    term: Terminal,
    fit: FitAddon,
    wrapper: HTMLDivElement,
    openSocket: SocketFactory,
  ) {
    this.id = id;
    this.term = term;
    this.fit = fit;
    this.wrapper = wrapper;
    this.openSocket = openSocket;
    // Keystrokes AND xterm's own answers go out over whichever socket is live
    // — parked or not, because a program may query the terminal while nobody
    // is looking and will wait for the answer. While a REPLAY is being
    // parsed, though, the answers are to questions asked in the past: drop
    // those (see isTerminalReport).
    term.onData((d) => {
      if (this.replaying && isTerminalReport(d)) return;
      this.send(d);
    });
    term.attachCustomKeyEventHandler((e) => (this.keyHandler ? this.keyHandler(e) : true));
  }

  /** True while the (re)attach replay is still being parsed. */
  get isReplaying(): boolean {
    return this.replaying;
  }

  /** True when the socket is open and typing would land. */
  get isOpen(): boolean {
    return this.ws?.readyState === WS_OPEN;
  }

  get hasExited(): boolean {
    return this.exited;
  }

  get isDisposed(): boolean {
    return this.disposed;
  }

  /**
   * A pane takes this host: the terminal moves into `holder` and `view` hears
   * about the connection from now on. `owner` is the mount's own token — a
   * later adopt by another mount wins, and the earlier mount's release is then
   * ignored (a Rail ⇄ Canvas flip mounts the new pane before React is done
   * with the old one).
   */
  adopt(holder: HTMLElement, owner: object, view: PaneHostView): void {
    if (this.disposed) return;
    this.owner = owner;
    this.view = view;
    if (this.wrapper.parentElement !== holder) holder.appendChild(this.wrapper);
    view.onConn(this.state, this.lostLink);
    // A link that dropped while nobody was looking (the daemon restarted for
    // an update while you were on another page) heals on return — never a
    // Reconnect button for an outage that is already over.
    if (this.started && this.lostLink && !this.exited) this.reconnectNow();
  }

  /** After the pane has fitted a just-adopted terminal: put the reader back
   *  where they were, and repaint every row once (a pane coming back from
   *  the lot has rows the paused renderer never painted). */
  settle(): void {
    const at = this.parkedAt;
    this.parkedAt = null;
    try {
      if (at) {
        if (at.atBottom) this.term.scrollToBottom();
        else this.term.scrollToLine(at.viewportY);
      }
      this.term.refresh(0, Math.max(0, this.term.rows - 1));
    } catch {
      /* disposed mid-settle — nothing to repaint */
    }
  }

  /** The mount is going away: PARK, never dispose. The shell, its socket and
   *  every line it prints stay alive for the next visit. */
  release(owner: object): void {
    if (this.owner !== owner) return; // a newer mount already holds it
    this.owner = null;
    this.view = null;
    this.keyHandler = null;
    if (this.disposed) return;
    try {
      const b = this.term.buffer.active;
      this.parkedAt = { viewportY: b.viewportY, atBottom: b.viewportY >= b.baseY };
    } catch {
      this.parkedAt = null;
    }
    parkingLot().appendChild(this.wrapper);
  }

  /** Connect for the first time. A no-op on a host that is already live, so a
   *  returning pane can call it unconditionally. */
  start(): void {
    if (this.started || this.disposed) return;
    this.started = true;
    this.setConn("connecting", false);
    this.connect();
  }

  /** A fresh attempt counter and one more connect on the SAME terminal (the
   *  replay lands on it) — the overlay's Reconnect, and the heal on adopt. */
  reconnectNow(): void {
    if (this.disposed || this.exited) return;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const old = this.ws;
    this.ws = null; // its close is old news — the handlers check identity
    if (old) {
      try {
        old.close();
      } catch {
        /* already closing */
      }
    }
    this.attempts = 0;
    this.started = true;
    this.setConn("reconnecting", false);
    this.connect();
  }

  /** Type into the live shell. HONEST when down: a closed or absent socket
   *  sends nothing and returns false, so a caller can refuse instead of
   *  pretending the text landed. */
  send(text: string): boolean {
    const live = this.ws;
    if (!live || live.readyState !== WS_OPEN) return false;
    live.send(text);
    return true;
  }

  /** The pane was closed (or the daemon no longer has it). The only path
   *  that ends a host. */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.replayFallback) clearTimeout(this.replayFallback);
    this.reconnectTimer = null;
    this.replayFallback = null;
    const ws = this.ws;
    this.ws = null;
    try {
      ws?.close();
    } catch {
      /* noop */
    }
    try {
      this.term.dispose();
    } catch {
      /* noop */
    }
    this.wrapper.remove();
    this.view = null;
    this.owner = null;
    this.keyHandler = null;
    if (hosts.get(this.id) === this) hosts.delete(this.id);
  }

  private setConn(state: ConnState, lostLink: boolean): void {
    this.state = state;
    this.lostLink = lostLink;
    this.view?.onConn(state, lostLink);
  }

  private connect(): void {
    if (this.disposed) return;
    const ws = this.openSocket(this.id);
    this.ws = ws;
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.attempts = 0;
      // Every (re)attach replays the session's scrollback — reset so it lands
      // on a clean screen instead of appending to a buffer that already holds
      // the same history, and so a full-screen app's stale modes don't
      // corrupt the replay.
      this.term.reset();
      this.beginReplay();
      this.setConn("open", false);
      this.view?.onOpen();
    };
    ws.onmessage = (ev: MessageEvent) => {
      if (this.ws !== ws) return;
      const data: unknown = ev.data;
      if (isReplayEnd(data)) {
        this.endReplay();
        return;
      }
      // Server -> client: PTY output as binary (ArrayBuffer); text just in case.
      if (typeof data === "string") this.term.write(data);
      else this.term.write(new Uint8Array(data as ArrayBuffer));
      this.view?.onOutput(data, this.replaying);
    };
    ws.onclose = (ev: CloseEvent) => {
      if (this.ws !== ws) return; // a superseded socket's close is old news
      this.ws = null;
      this.cancelReplay();
      if (this.disposed) return;
      // 4000 = the SHELL ITSELF exited (the daemon's explicit signal). There is
      // nothing to reconnect to — retrying re-attached to a dead PTY in a crash
      // loop that also stole focus every cycle.
      if (ev.code === 4000) {
        this.exited = true;
        this.setConn("closed", false);
        return;
      }
      // v1.226.0: quick retries, then keep going with capped backoff while the
      // daemon is offline (a restart rehydrates this same terminal id); see
      // terminalReconnectDelayMs for the schedule.
      const delay = terminalReconnectDelayMs(this.attempts, this.daemonOnline);
      if (delay !== null) {
        this.attempts += 1;
        this.setConn("reconnecting", false);
        this.reconnectTimer = setTimeout(() => {
          this.reconnectTimer = null;
          this.connect();
        }, delay);
      } else {
        this.setConn("closed", true);
      }
    };
    ws.onerror = () => {
      try {
        ws.close();
      } catch {
        /* noop */
      }
    };
  }

  private beginReplay(): void {
    this.replaying = true;
    const gen = ++this.replayGen;
    if (this.replayFallback) clearTimeout(this.replayFallback);
    this.replayFallback = setTimeout(() => this.finishReplay(gen), REPLAY_FALLBACK_MS);
  }

  private endReplay(): void {
    // The marker arrives on the socket, but the replay bytes before it may
    // still be queued in xterm's parser. `write("", cb)` calls back once
    // EVERYTHING written before it has been parsed — so every answer xterm
    // generated for the replay has been emitted (and dropped) by then.
    const gen = this.replayGen;
    this.term.write("", () => this.finishReplay(gen));
  }

  private finishReplay(gen: number): void {
    if (gen !== this.replayGen || !this.replaying) return; // a newer attach owns it
    this.replaying = false;
    if (this.replayFallback) {
      clearTimeout(this.replayFallback);
      this.replayFallback = null;
    }
    // One full repaint once the replay has landed (v1.190.0): the replay
    // arrives as one burst, and any cell painted from transitional metrics
    // stays stale until something repaints it.
    try {
      this.term.refresh(0, Math.max(0, this.term.rows - 1));
    } catch {
      /* disposed mid-parse — nothing to repaint */
    }
  }

  private cancelReplay(): void {
    this.replayGen += 1;
    this.replaying = false;
    if (this.replayFallback) {
      clearTimeout(this.replayFallback);
      this.replayFallback = null;
    }
  }
}

// ---- the registry ---------------------------------------------------------

const hosts = new Map<string, PaneHost>();
const building = new Map<string, Promise<PaneHost>>();
/** Closed while still being built — disposed the moment the build lands. */
const doomed = new Set<string>();

export interface AcquireDeps {
  createTerminal?: TerminalFactory;
  openSocket?: SocketFactory;
}

/** The host for this pane: the live one when it exists, else a new one
 *  (built once, however many mounts ask at the same time). A new host is
 *  NOT connected — the pane calls `start()` once it has a real size, so the
 *  first replay lands at the true width (v1.190.0). */
export function acquirePaneHost(id: string, deps: AcquireDeps = {}): Promise<PaneHost> {
  const live = hosts.get(id);
  if (live) return Promise.resolve(live);
  const inflight = building.get(id);
  if (inflight) return inflight;
  const made = (async () => {
    const { term, fit } = await (deps.createTerminal ?? createXterm)();
    const wrapper = document.createElement("div");
    wrapper.className = "h-full w-full";
    wrapper.dataset.paneHost = id;
    // Opened INSIDE the document (the parking lot), so xterm measures its
    // glyphs against real layout; the pane adopts it a moment later.
    parkingLot().appendChild(wrapper);
    term.open(wrapper);
    return new PaneHost(id, term, fit, wrapper, deps.openSocket ?? openAttachSocket);
  })();
  const tracked = made.then(
    (host) => {
      building.delete(id);
      if (doomed.delete(id)) {
        host.dispose();
        throw new Error(`pane ${id} was closed`);
      }
      hosts.set(id, host);
      return host;
    },
    (err: unknown) => {
      building.delete(id);
      doomed.delete(id);
      throw err;
    },
  );
  // A build nobody is still waiting for (its pane closed) must not surface as
  // an unhandled rejection; awaiting callers still see the error.
  tracked.catch(() => {});
  building.set(id, tracked);
  return tracked;
}

/** The pane was closed: end its host for good (or as soon as it is built). */
export function disposePaneHost(id: string): void {
  hosts.get(id)?.dispose();
  if (building.has(id)) doomed.add(id);
}

/** Keep only the hosts for panes the daemon still has. The page calls this
 *  with its fresh list, so a pane closed from elsewhere — an agent, another
 *  window, an exited shell the list no longer offers — does not keep a socket
 *  open in the background forever. */
export function retainPaneHosts(liveIds: Iterable<string>): void {
  const keep = new Set(liveIds);
  for (const [id, host] of Array.from(hosts)) if (!keep.has(id)) host.dispose();
  for (const id of Array.from(building.keys())) if (!keep.has(id)) doomed.add(id);
}

/** The ids with a live host, for tests and diagnostics. */
export function paneHostIds(): string[] {
  return Array.from(hosts.keys());
}

/** Tests only: forget every host (disposing each). */
export function resetPaneHostsForTests(): void {
  for (const host of Array.from(hosts.values())) host.dispose();
  hosts.clear();
  building.clear();
  doomed.clear();
}
