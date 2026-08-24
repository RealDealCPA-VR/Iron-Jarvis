/**
 * v1.212.0 — the Build pane's view toggle stops being BLIND.
 *
 * Since v1.206.0 each pane flips between a Terminal layer and a Chat layer
 * with visibility only (both stay mounted — the flip invariants live in
 * pane-toggle-v1206.test.tsx and are NOT retested here). But the toggle
 * buttons carried no signal: from terminal view you could not see that the
 * hidden chat was streaming — or, worst case, PAUSED on an ApprovalCard the
 * daemon holds for up to 180s — and from chat view you could not see that
 * the hidden terminal printed new output. This file pins the progress path:
 *
 *  - PaneChat reports {streaming, approval} via the new onStatus prop, on
 *    change only, with streaming = sending || stream.streaming so the
 *    pre-stream POST window counts as working (the composer-spinner rule);
 *  - the page paints badges on the toggle buttons: amber pulse = approval
 *    waiting (outranks streaming), accent pulse = chat working — only while
 *    the chat is HIDDEN; a neutral dot on the terminal button = unseen
 *    output — only while the chat is SHOWING, cleared by flipping back;
 *  - TerminalPane fires onOutput from ws.onmessage, throttled through the
 *    pure paneStatusCore.outputNotifyAt gate (frame classification + the
 *    ~300ms throttle + the replay-window guard), tested unit-level here and
 *    source-pinned into the ws handler (jsdom cannot run xterm — the house
 *    idiom, v1.163.0/v1.190.0).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

/* ---- api (serves BOTH the page's loads and PaneChat's thread machinery) --- */

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

/* ---- useChatStream: controllable status + a GATE holding run() open ------- */

const S = vi.hoisted(() => {
  class FakeStreamError extends Error {
    status = 0;
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    FakeStreamError,
    stream: {
      bodies: [] as Record<string, unknown>[],
      result: { reply: "ok" } as Record<string, unknown>,
      streaming: false,
      text: "",
      tools: [] as Record<string, unknown>[],
      approval: null as Record<string, unknown> | null,
      /** While set, run() stays in flight — the `sending` window under test. */
      gate: null as Promise<void> | null,
    },
  };
});

vi.mock("@/lib/useChatStream", () => ({
  StreamError: S.FakeStreamError,
  useChatStream: () => ({
    streaming: S.stream.streaming,
    text: S.stream.text,
    tools: S.stream.tools,
    approval: S.stream.approval,
    run: async (body: Record<string, unknown>) => {
      S.stream.bodies.push(body);
      if (S.stream.gate) await S.stream.gate;
      return S.stream.result;
    },
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
    health: {
      status: "ok",
      version: "test",
      default_provider: "mock",
      default_model: "m",
      providers: [],
    },
  }),
}));

// The markdown pipeline is not under test here (house idiom).
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

/* ---- next/dynamic stubs for the PAGE tests -------------------------------- */
// Told apart by contract props exactly as in pane-toggle-v1206: PaneChat gets
// `paneId`, TerminalPane gets `info`. The stubs expose BUTTONS that fire the
// new callbacks — the page's badge logic is the thing under test, so the
// children only need to deliver the reports a real child would.

vi.mock("next/dynamic", () => ({
  default: () => {
    function DynamicStub(props: Record<string, unknown>) {
      if (typeof props.paneId === "string") {
        const paneId = props.paneId;
        const onStatus = props.onStatus as
          | ((s: { streaming: boolean; approval: boolean }) => void)
          | undefined;
        return (
          <div data-testid="pane-chat">
            <button
              type="button"
              data-testid={`emit-streaming-${paneId}`}
              onClick={() => onStatus?.({ streaming: true, approval: false })}
            >
              streaming
            </button>
            {/* An approval ALWAYS arrives mid-turn, so it reports both true —
                which is exactly the precedence case the badge must resolve. */}
            <button
              type="button"
              data-testid={`emit-approval-${paneId}`}
              onClick={() => onStatus?.({ streaming: true, approval: true })}
            >
              approval
            </button>
            <button
              type="button"
              data-testid={`emit-idle-${paneId}`}
              onClick={() => onStatus?.({ streaming: false, approval: false })}
            >
              idle
            </button>
          </div>
        );
      }
      const info = props.info as { id: string; shell: string };
      const onOutput = props.onOutput as (() => void) | undefined;
      return (
        <div data-testid={`terminal-pane-${info.id}`}>
          <header className="ij-term-drag">{info.shell}</header>
          <button
            type="button"
            data-testid={`emit-output-${info.id}`}
            onClick={() => onOutput?.()}
          >
            output
          </button>
        </div>
      );
    }
    return DynamicStub;
  },
}));

/* ---- page chrome ----------------------------------------------------------- */

vi.mock("react-rnd", () => ({
  Rnd: ({
    children,
    onMouseDown,
    cancel,
  }: {
    children?: React.ReactNode;
    onMouseDown?: () => void;
    cancel?: string;
  }) => (
    <div data-testid="rnd" data-cancel={cancel} onMouseDown={onMouseDown}>
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
import { PaneChat } from "@/components/terminal/PaneChat";
import {
  OUTPUT_NOTIFY_MS,
  isOutputFrame,
  outputNotifyAt,
} from "@/components/terminal/paneStatusCore";

/* ---- fixtures -------------------------------------------------------------- */

const CWD = "C:\\work\\demo";

const term = (id: string, cwd: string) => ({
  id,
  cwd,
  shell: "pwsh",
  argv: [],
  cols: 120,
  rows: 30,
  alive: true,
  exit_code: null,
  created_at: "2026-08-23T00:00:00Z",
});

function seedApi(terminals: unknown[]) {
  api.responses = {
    "/terminals": { terminals },
    "/terminals/shells": { shells: [] },
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
  };
}

// The accessible names GROW a status suffix when a badge shows, so the base
// lookups match by prefix (the plain name is asserted exactly where the
// absence of a badge is the point).
const chatBtn = (id: string) =>
  screen.getByRole("button", { name: new RegExp(`^Chat view for pane ${id}`) });
const termBtn = (id: string) =>
  screen.getByRole("button", { name: new RegExp(`^Terminal view for pane ${id}`) });

async function renderPage(firstId = "t1") {
  render(<TerminalsPage />);
  await screen.findByTestId(`terminal-pane-${firstId}`);
}

async function typeAndSend(text: string) {
  const box = screen.getByLabelText("Message");
  await waitFor(() => expect(box).toBeEnabled());
  fireEvent.change(box, { target: { value: text } });
  fireEvent.click(screen.getByLabelText("Send"));
}

beforeEach(() => {
  localStorage.clear();
  seedApi([term("t1", "C:\\proj\\alpha"), term("t2", "C:\\proj\\beta")]);
  S.stream.bodies = [];
  S.stream.result = { reply: "ok" };
  S.stream.streaming = false;
  S.stream.text = "";
  S.stream.tools = [];
  S.stream.approval = null;
  S.stream.gate = null;
});

afterEach(() => {
  cleanup();
});

/* ---- (a) PaneChat reports onStatus ----------------------------------------- */

describe("PaneChat status reporting (rendered against the real component)", () => {
  it("reports idle on mount, WORKING through the whole send window, idle after", async () => {
    const onStatus = vi.fn();
    let release!: () => void;
    S.stream.gate = new Promise<void>((r) => {
      release = r;
    });
    render(<PaneChat paneId="p1" cwd={CWD} onStatus={onStatus} />);
    await waitFor(() =>
      expect(onStatus).toHaveBeenCalledWith({ streaming: false, approval: false }),
    );
    onStatus.mockClear();

    await typeAndSend("go");
    // THE NUANCE UNDER TEST: the POST is still in flight (`sending`), the
    // hook's own streaming flag is FALSE — and the report already says
    // working, because streaming = sending || stream.streaming (the same
    // derivation as the composer's spinner). A report keyed on the hook flag
    // alone would leave the badge dark for the whole pre-stream window.
    await waitFor(() =>
      expect(onStatus).toHaveBeenCalledWith({ streaming: true, approval: false }),
    );
    expect(onStatus).not.toHaveBeenCalledWith(
      expect.objectContaining({ approval: true }),
    );

    onStatus.mockClear();
    await act(async () => {
      release();
      await S.stream.gate;
    });
    await waitFor(() =>
      expect(onStatus).toHaveBeenCalledWith({ streaming: false, approval: false }),
    );
  });

  it("a held approval reports approval:true; the resolved frame reports it back down", async () => {
    S.stream.approval = {
      id: "apr_1",
      callId: "c1",
      tool: "shell",
      args: { command: "npm test" },
    };
    const onStatus = vi.fn();
    const view = render(<PaneChat paneId="p2" cwd={CWD} onStatus={onStatus} />);
    await waitFor(() =>
      expect(onStatus).toHaveBeenCalledWith({ streaming: false, approval: true }),
    );
    // The card itself renders too — the report and the card are one fact.
    expect(screen.getByTestId("chat-approval-card")).toBeInTheDocument();

    // The approval_resolved frame clears the hook's approval (real-hook
    // behaviour) — the next report must drop it.
    S.stream.approval = null;
    onStatus.mockClear();
    view.rerender(<PaneChat paneId="p2" cwd={CWD} onStatus={onStatus} />);
    await waitFor(() =>
      expect(onStatus).toHaveBeenCalledWith({ streaming: false, approval: false }),
    );
  });

  it("unmount hygiene: the LAST report is idle — no stale badge outlives the chat", async () => {
    S.stream.streaming = true;
    const onStatus = vi.fn();
    const { unmount } = render(<PaneChat paneId="p3" cwd={CWD} onStatus={onStatus} />);
    await waitFor(() =>
      expect(onStatus).toHaveBeenCalledWith({ streaming: true, approval: false }),
    );
    unmount();
    expect(onStatus.mock.calls[onStatus.mock.calls.length - 1][0]).toEqual({
      streaming: false,
      approval: false,
    });
  });
});

/* ---- (b)(c) chat-side badges on the page ------------------------------------ */

describe("chat badge on the toggle (page)", () => {
  it("an approval waiting in the HIDDEN chat shows the amber pulse — outranking streaming — and the button says so", async () => {
    await renderPage();
    fireEvent.click(chatBtn("t1")); // mount the chat, pane now in chat view
    fireEvent.click(screen.getByTestId("emit-approval-t1")); // streaming AND approval

    // Hide the chat: the pause must surface on the button that reveals it.
    fireEvent.click(termBtn("t1"));
    const badge = screen.getByTestId("pane-chat-badge-t1");
    // Amber + pulse = approval; precedence over the simultaneous streaming.
    expect(badge.className).toContain("bg-amber-400");
    expect(badge.className).toContain("animate-pulse");
    expect(badge.className).not.toContain("bg-accent");
    // Title AND accessible name carry the status.
    const btn = screen.getByRole("button", {
      name: "Chat view for pane t1 — Approval waiting in chat",
    });
    expect(btn).toHaveAttribute("title", "Chat view — Approval waiting in chat");

    // Approval answered, turn still streaming → the accent working pulse.
    fireEvent.click(screen.getByTestId("emit-streaming-t1"));
    const working = screen.getByTestId("pane-chat-badge-t1");
    expect(working.className).toContain("bg-accent");
    expect(working.className).not.toContain("bg-amber-400");
    expect(
      screen.getByRole("button", { name: "Chat view for pane t1 — Chat is working" }),
    ).toHaveAttribute("title", "Chat view — Chat is working");

    // Turn done → no badge, and the PLAIN name is back (exact match).
    fireEvent.click(screen.getByTestId("emit-idle-t1"));
    expect(screen.queryByTestId("pane-chat-badge-t1")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Chat view for pane t1" }),
    ).toBeInTheDocument();
  });

  it("no chat badge while the pane IS in chat view — the user is already watching", async () => {
    await renderPage();
    fireEvent.click(chatBtn("t1"));
    fireEvent.click(screen.getByTestId("emit-streaming-t1"));
    // In chat view the stream is on screen; a badge would be noise.
    expect(screen.queryByTestId("pane-chat-badge-t1")).toBeNull();
    // The SAME held status surfaces the moment the chat hides.
    fireEvent.click(termBtn("t1"));
    expect(screen.getByTestId("pane-chat-badge-t1")).toBeInTheDocument();
    // The untouched pane never shows anything.
    expect(screen.queryByTestId("pane-chat-badge-t2")).toBeNull();
  });
});

/* ---- (d) terminal-output badge ---------------------------------------------- */

describe("terminal-output badge on the toggle (page)", () => {
  it("appears only for output that lands while the pane shows CHAT, and flipping back clears it", async () => {
    await renderPage();

    // Terminal view: the output is being watched — no badge, ever.
    fireEvent.click(screen.getByTestId("emit-output-t1"));
    expect(screen.queryByTestId("pane-term-badge-t1")).toBeNull();

    // Chat view: BEFORE any new output, the badge must be absent — the
    // terminal-view emission above must not have set the unseen flag (the
    // viewsRef gate in noteTermOutput). Without this assertion, deleting
    // that gate survives every test: the render gate hides the badge while
    // in terminal view, and this flip is the first moment a stale flag
    // would become visible (reviewer finding 1, mutation survivor).
    fireEvent.click(chatBtn("t1"));
    expect(screen.queryByTestId("pane-term-badge-t1")).toBeNull();
    fireEvent.click(screen.getByTestId("emit-output-t1"));
    expect(screen.getByTestId("pane-term-badge-t1")).toBeInTheDocument();
    const btn = screen.getByRole("button", {
      name: "Terminal view for pane t1 — New terminal output",
    });
    expect(btn).toHaveAttribute("title", "Terminal view — New terminal output");

    // A pane sitting in terminal view is untouched by its own output.
    fireEvent.click(screen.getByTestId("emit-output-t2"));
    expect(screen.queryByTestId("pane-term-badge-t2")).toBeNull();

    // Flip back: seen — badge gone, plain name restored (exact match).
    fireEvent.click(btn);
    expect(screen.queryByTestId("pane-term-badge-t1")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Terminal view for pane t1" }),
    ).toBeInTheDocument();

    // And the gate keeps reading the LIVE view across repeated flips.
    fireEvent.click(chatBtn("t1"));
    fireEvent.click(screen.getByTestId("emit-output-t1"));
    expect(screen.getByTestId("pane-term-badge-t1")).toBeInTheDocument();
  });
});

/* ---- (e) the pure gate: frame classification + throttle --------------------- */

describe("outputNotifyAt (pure — the throttle TerminalPane runs in ws.onmessage)", () => {
  it("two bursts inside the window produce ONE notification", () => {
    const frame = new ArrayBuffer(8);
    const first = outputNotifyAt(frame, 1_000, 0, 0);
    expect(first).toBe(1_000);
    // Second burst 100ms later: suppressed — the page already knows.
    expect(outputNotifyAt(frame, 1_100, first as number, 0)).toBeNull();
    expect(outputNotifyAt(frame, 1_000 + OUTPUT_NOTIFY_MS - 1, first as number, 0)).toBeNull();
    // The window elapses: the next frame notifies again.
    expect(outputNotifyAt(frame, 1_000 + OUTPUT_NOTIFY_MS, first as number, 0)).toBe(
      1_000 + OUTPUT_NOTIFY_MS,
    );
  });

  it("stays quiet through the (re)connect replay window — scrollback catch-up is old news", () => {
    // The replay is NOT distinguishable by frame shape (the daemon replays it
    // as ordinary send_bytes), so the gate reuses the pane's replayGuardUntil
    // heuristic — quiet strictly BEFORE the deadline, live from it on.
    expect(outputNotifyAt("replayed history", 500, 0, 800)).toBeNull();
    expect(outputNotifyAt("live output", 800, 0, 800)).toBe(800);
  });

  it("only frames CARRYING output count — empty and unknown shapes never notify", () => {
    expect(isOutputFrame("hi")).toBe(true);
    expect(isOutputFrame(new ArrayBuffer(2))).toBe(true);
    expect(isOutputFrame("")).toBe(false);
    expect(isOutputFrame(new ArrayBuffer(0))).toBe(false);
    expect(isOutputFrame(null)).toBe(false);
    expect(isOutputFrame(undefined)).toBe(false);
    expect(isOutputFrame({ type: "resize" })).toBe(false);
    expect(outputNotifyAt("", 1_000, 0, 0)).toBeNull();
  });
});

/* ---- source pins (jsdom cannot run xterm — the house idiom) ------------------ */

describe("TerminalPane wiring (source-pinned)", () => {
  const pane = readFileSync(
    join(process.cwd(), "components", "terminal", "TerminalPane.tsx"),
    "utf8",
  );

  it("notifies from ws.onmessage through the pure gate, guarded and throttled", () => {
    // Anchor on the ASSIGNMENT ("ws.onmessage = "), not the bare phrase — a
    // comment 500 lines earlier also says "ws.onmessage", and a window opened
    // there spans the whole onopen handler, letting the notify block drift
    // out of the real message handler unnoticed (reviewer finding 2).
    const om = pane.slice(pane.indexOf("ws.onmessage = "), pane.indexOf("ws.onclose"));
    // The decision is the tested pure helper, fed the frame, the clock, the
    // throttle memory, and the SAME replay window the answerback suppression
    // trusts — not a re-implementation.
    expect(om).toContain("outputNotifyAt(");
    expect(om).toContain("replayGuardUntil");
    expect(om).toContain("lastOutputNotifyRef.current = at;");
    expect(om).toContain("onOutputRef.current?.()");
    // The honest limitation is documented where the code lives: the replay is
    // not distinguishable by frame shape (the phrase wraps in the source, so
    // the pin holds the line-stable half).
    expect(om).toContain("NOT distinguishable by frame");
    expect(om).toContain("no marker");
  });

  it("reads the callback through a ref — the once-per-session socket effect must never pin a stale page closure", () => {
    expect(pane).toContain("const onOutputRef = useRef(onOutput);");
    expect(pane).toContain("onOutputRef.current = onOutput;");
  });
});
