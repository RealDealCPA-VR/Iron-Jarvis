/**
 * v1.333.0 (Lane B) — the Build page stays light while nobody is looking.
 *
 *  - B4, HIDDEN-WINDOW RECONNECT CHURN. A minimized window hides the page; xterm
 *    parses on throttled timers there, so a busy pane paused its stream, the
 *    daemon queued 8 MB for it and cut the link (1013), and the pane
 *    reconnected at once to a 256 KB replay into a terminal that fell behind
 *    again — all night. A 1013 while hidden now waits for the window to be
 *    SEEN (one visibilitychange listener, no timer), and the output the pane
 *    missed counts as unseen. A 1013 while visible reconnects as before.
 *  - B6, A PEEK THAT CHANGES NOTHING RENDERS NOTHING. A TUI repainting its
 *    status line sent the page a frame ~3 times a second, and every frame
 *    built a new peek object, re-rendering the whole Build page per pane.
 *    The newest frame's time lives in a ref; the state changes only when what
 *    is shown changes.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import {
  ATTACH_OVERFLOW,
  acquirePaneHost,
  resetPaneHostsForTests,
  type PaneHostView,
} from "@/components/terminal/paneHost";

/* ---- B4: the host -------------------------------------------------------- */

class FakeTerm {
  rows = 24;
  cols = 80;
  resets = 0;
  buffer = { active: { viewportY: 0, baseY: 0, type: "normal" } };
  private pending: Array<() => void> = [];
  open(parent: HTMLElement) {
    parent.appendChild(document.createElement("div"));
  }
  loadAddon() {}
  write(_d: unknown, cb?: () => void) {
    if (cb) this.pending.push(cb);
  }
  parse() {
    const due = this.pending;
    this.pending = [];
    due.forEach((cb) => cb());
  }
  reset() {
    this.resets += 1;
  }
  refresh() {}
  onData() {
    return { dispose: () => {} };
  }
  attachCustomKeyEventHandler() {}
  scrollToLine() {}
  scrollToBottom() {}
  focus() {}
  dispose() {}
}

class FakeSocket {
  static all: FakeSocket[] = [];
  readyState = 0;
  binaryType = "";
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public id: string) {
    FakeSocket.all.push(this);
  }
  send() {}
  close() {
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  frame(data: unknown) {
    this.onmessage?.({ data });
  }
  drop(code: number) {
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
  loadGpu: null,
};

let hidden = false;
function setHidden(value: boolean) {
  hidden = value;
  document.dispatchEvent(new Event("visibilitychange"));
}

async function liveHost() {
  const host = await acquirePaneHost("t1", deps);
  const outputs: Array<[unknown, boolean]> = [];
  const view: PaneHostView = {
    onConn: () => {},
    onOutput: (data, replaying) => outputs.push([data, replaying]),
    onOpen: () => {},
  };
  const where = document.createElement("div");
  document.body.appendChild(where);
  host.adopt(where, {}, view);
  host.start();
  const sock = FakeSocket.all[FakeSocket.all.length - 1];
  sock.open();
  sock.frame(new ArrayBuffer(0)); // end of the (empty) replay
  terms[terms.length - 1].parse();
  return { host, sock, outputs };
}

describe("a 1013 while the window is hidden waits for the window (B4)", () => {
  beforeEach(() => {
    resetPaneHostsForTests();
    FakeSocket.all = [];
    terms = [];
    hidden = false;
    Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  });
  afterEach(() => {
    resetPaneHostsForTests();
    vi.useRealTimers();
  });

  it("opens no socket while hidden, however long, and one when the window is seen", async () => {
    const { host, sock, outputs } = await liveHost();
    vi.useFakeTimers();
    hidden = true;
    sock.drop(ATTACH_OVERFLOW);
    vi.advanceTimersByTime(8 * 60 * 60 * 1000); // a night minimized
    expect(FakeSocket.all).toHaveLength(1);
    expect(host.isReconnectDeferred).toBe(true);
    // The output it missed is UNSEEN: the view heard one live (non-replay) frame.
    expect(outputs.filter(([, replaying]) => !replaying)).toHaveLength(1);
    setHidden(true); // still hidden: nothing
    expect(FakeSocket.all).toHaveLength(1);
    setHidden(false);
    expect(FakeSocket.all).toHaveLength(2);
    expect(host.isReconnectDeferred).toBe(false);
    setHidden(true);
    setHidden(false); // the listener is gone: no second reconnect
    expect(FakeSocket.all).toHaveLength(2);
  });

  it("a 1013 while visible still reconnects on its own schedule", async () => {
    const { host, sock } = await liveHost();
    vi.useFakeTimers();
    sock.drop(ATTACH_OVERFLOW);
    expect(host.isReconnectDeferred).toBe(false);
    vi.advanceTimersByTime(5_000);
    expect(FakeSocket.all.length).toBeGreaterThan(1);
  });

  it("closing the pane while a reconnect waits removes the listener", async () => {
    const { host, sock } = await liveHost();
    hidden = true;
    sock.drop(ATTACH_OVERFLOW);
    expect(host.isReconnectDeferred).toBe(true);
    host.dispose();
    expect(host.isReconnectDeferred).toBe(false); // the listener went with it
    setHidden(false);
    expect(FakeSocket.all).toHaveLength(1);
  });
});

/* ---- B6: the page's peek ------------------------------------------------- */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return { responses: {} as Record<string, unknown>, FakeApiError };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: (path: string) => {
    if (path === "/projects") return Promise.resolve({ projects: [] });
    if (path === "/undo?session_id=chat") return Promise.resolve({ actions: [] });
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    }
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({ ok: true }),
  put: () => Promise.resolve({ id: "th_1", title: "t" }),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useChatStream", () => ({
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  StreamError: class extends Error {},
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async () => ({ reply: "ok" }),
    abort: () => {},
  }),
}));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    refresh: () => {},
    health: { status: "ok", version: "test", default_provider: "mock", default_model: "m", providers: [] },
  }),
}));

vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

const P = vi.hoisted(() => ({ chunk: "", renders: 0 }));

vi.mock("next/dynamic", () => ({
  default: () => {
    function DynamicStub(props: Record<string, unknown>) {
      if (typeof props.paneId === "string") return <div data-testid="pane-chat" />;
      const info = props.info as { id: string; shell: string };
      const onOutput = props.onOutput as ((chunk: string) => void) | undefined;
      if (info.id === "t1") P.renders += 1;
      return (
        <div data-testid={`terminal-pane-${info.id}`}>
          <header className="ij-term-drag">{info.shell}</header>
          <button type="button" data-testid={`emit-output-${info.id}`} onClick={() => onOutput?.(P.chunk)}>
            output
          </button>
        </div>
      );
    }
    return DynamicStub;
  },
}));

vi.mock("react-rnd", () => ({
  Rnd: ({ children, onMouseDown }: { children?: React.ReactNode; onMouseDown?: () => void }) => (
    <div data-testid="rnd" onMouseDown={onMouseDown}>
      {children}
    </div>
  ),
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {actions}
    </div>
  ),
}));
vi.mock("@/components/ui", () => ({
  Card: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  OfflineHint: () => <div />,
  ErrorNote: ({ children }: { children?: React.ReactNode }) => <div role="alert">{children}</div>,
  Spinner: ({ label }: { label?: string }) => <div>{label}</div>,
  ConfirmButton: ({ label }: { label: string }) => <button type="button">{label}</button>,
  LoaderInline: () => <span />,
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <div data-testid="directory-tree" />,
}));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: () => <div data-testid="files-panel" />,
}));

import TerminalsPage from "@/app/terminals/page";

const term = (id: string, cwd: string) => ({
  id,
  cwd,
  shell: "pwsh",
  argv: [],
  cols: 120,
  rows: 30,
  alive: true,
  exit_code: null,
  created_at: "2026-10-11T00:00:00Z",
});

describe("a terminal frame that changes nothing on screen renders nothing (B6)", () => {
  beforeEach(() => {
    localStorage.clear();
    api.responses = {
      "/terminals": { terminals: [term("t1", "C:\\proj\\alpha")] },
      "/terminals/shells": { shells: [] },
      "/models": { models: [] },
      "/terminals/ai-clis": { clis: [] },
      "/skills": { skills: [] },
    };
    P.chunk = "";
    P.renders = 0;
  });
  afterEach(() => cleanup());

  it("ten repaints of the same status line cost one render; a new line still shows", async () => {
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    fireEvent.click(screen.getByRole("button", { name: /^Chat view for pane t1/ }));
    P.chunk = "\x1b[2K\r✻ Thinking… (esc to interrupt)\r\n";
    fireEvent.click(screen.getByTestId("emit-output-t1"));
    expect(screen.getByTestId("pane-peek-t1")).toHaveTextContent("Thinking");
    const before = P.renders;
    for (let i = 0; i < 10; i += 1) fireEvent.click(screen.getByTestId("emit-output-t1"));
    expect(P.renders - before).toBe(0);
    P.chunk = "Done. 12 files changed\r\n";
    fireEvent.click(screen.getByTestId("emit-output-t1"));
    expect(screen.getByTestId("pane-peek-t1")).toHaveTextContent("Done. 12 files changed");
  });
});
