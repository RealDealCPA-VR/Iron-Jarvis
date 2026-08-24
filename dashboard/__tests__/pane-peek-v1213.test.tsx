/**
 * v1.213.0 — the LIVE PEEK STRIP: the badges (v1.212.0) say THAT something is
 * happening in a pane's hidden view; the strip shows WHAT, as one slim line
 * at the pane's bottom edge, clickable to flip.
 *
 *  - In TERMINAL view the strip shows the hidden CHAT's activity, only while
 *    the chat is busy or approval-waiting: an approval outranks everything
 *    (amber, "Approval needed — click to answer"), else the last still-running
 *    tool, else the streamed text's tail, else "Chat: working…". Click →
 *    chat view.
 *  - In CHAT view the strip shows the hidden TERMINAL's last non-empty output
 *    line (ANSI/OSC-stripped, capped), while output is recent (~15s quiet
 *    window — timestamp state + one timeout from the last frame, no polling)
 *    OR while the unseen-output flag is set. Click → terminal view.
 *
 * HONESTY: the strip renders SERVER truth only — stream text/tools that
 * actually arrived, terminal bytes that actually landed — and renders ONLY
 * when it has something true to say (a stripped line that comes out empty is
 * nothing, never an empty husk). The every-token textTail is PACED in
 * PaneChat (one report per TAIL_REPORT_MS; transitions immediate) so a token
 * burst cannot re-render the whole Build canvas per token — proven here with
 * fake timers. The v1.206.0 flip invariants and the v1.212.0 badge behavior
 * are pinned in their own files and re-run alongside this one.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

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

/* ---- useChatStream: controllable stream state ----------------------------- */

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
      streaming: false,
      text: "",
      tools: [] as Record<string, unknown>[],
      approval: null as Record<string, unknown> | null,
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
// Told apart by contract props as in pane-toggle-v1206/pane-status-v1212:
// PaneChat gets `paneId`, TerminalPane gets `info`. The chat stub's emit
// buttons deliver the FULL v1.213.0 status payloads a real PaneChat reports;
// the terminal stub's output button delivers whatever raw chunk the test
// staged in P.chunk — the page's strip derivation is the thing under test.

const P = vi.hoisted(() => ({
  chunk: "$ echo hi\r\nhi\r\n",
}));

type FullStatus = {
  streaming: boolean;
  approval: boolean;
  tool: string;
  textTail: string;
};

vi.mock("next/dynamic", () => ({
  default: () => {
    function DynamicStub(props: Record<string, unknown>) {
      if (typeof props.paneId === "string") {
        const paneId = props.paneId;
        const onStatus = props.onStatus as ((s: FullStatus) => void) | undefined;
        const emit = (id: string, s: FullStatus) => (
          <button type="button" data-testid={`${id}-${paneId}`} onClick={() => onStatus?.(s)}>
            {id}
          </button>
        );
        return (
          <div data-testid="pane-chat">
            {/* An approval always arrives mid-turn — tool and text ride along,
                which is exactly the precedence the strip must resolve. */}
            {emit("emit-approval", {
              streaming: true,
              approval: true,
              tool: "shell",
              textTail: "running npm test",
            })}
            {emit("emit-tool", {
              streaming: true,
              approval: false,
              tool: "write_file",
              textTail: "writing the report",
            })}
            {emit("emit-text", {
              streaming: true,
              approval: false,
              tool: "",
              textTail: "…and the final answer is 42",
            })}
            {emit("emit-working", {
              streaming: true,
              approval: false,
              tool: "",
              textTail: "",
            })}
            {emit("emit-idle", {
              streaming: false,
              approval: false,
              tool: "",
              textTail: "",
            })}
          </div>
        );
      }
      const info = props.info as { id: string; shell: string };
      const onOutput = props.onOutput as ((chunk: string) => void) | undefined;
      const onClose = props.onClose as (() => void) | undefined;
      return (
        <div data-testid={`terminal-pane-${info.id}`}>
          <header className="ij-term-drag">{info.shell}</header>
          <button
            type="button"
            data-testid={`emit-output-${info.id}`}
            onClick={() => onOutput?.(P.chunk)}
          >
            output
          </button>
          {/* The real pane's X — routes to the page's setPendingClose. */}
          <button
            type="button"
            data-testid={`term-close-${info.id}`}
            onClick={() => onClose?.()}
          >
            close
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
  CHAT_TAIL_CHARS,
  TERM_LINE_CHARS,
  TERM_PEEK_QUIET_MS,
  lastLine,
  stripAnsi,
  textTail,
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

const chatBtn = (id: string) =>
  screen.getByRole("button", { name: new RegExp(`^Chat view for pane ${id}`) });
const termBtn = (id: string) =>
  screen.getByRole("button", { name: new RegExp(`^Terminal view for pane ${id}`) });

async function renderPage(firstId = "t1") {
  render(<TerminalsPage />);
  await screen.findByTestId(`terminal-pane-${firstId}`);
}

beforeEach(() => {
  localStorage.clear();
  seedApi([term("t1", "C:\\proj\\alpha"), term("t2", "C:\\proj\\beta")]);
  S.stream.streaming = false;
  S.stream.text = "";
  S.stream.tools = [];
  S.stream.approval = null;
  P.chunk = "$ echo hi\r\nhi\r\n";
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

/* ---- (a) the pure text helpers ---------------------------------------------- */

describe("stripAnsi (pure)", () => {
  it("removes CSI sequences (colors, cursor moves) and keeps the text", () => {
    expect(stripAnsi("\x1b[32mPASS\x1b[0m tests \x1b[1;31mFAIL\x1b[m")).toBe(
      "PASS tests FAIL",
    );
  });

  it("removes OSC sequences with BOTH terminators (BEL and ESC-backslash)", () => {
    expect(stripAnsi("\x1b]0;window title\x07hello")).toBe("hello");
    expect(stripAnsi("\x1b]633;A\x1b\\next")).toBe("next");
  });

  it("BOUNDS an unterminated OSC — it can never swallow a whole chunk's real output", () => {
    // Terminator still in the next frame: the payload match stops at 256
    // chars, so output past the bound SURVIVES. (A short unterminated OSC is
    // consumed to end-of-chunk — a title fragment is not output.)
    const long = `\x1b]0;${"x".repeat(300)}REAL OUTPUT`;
    expect(stripAnsi(long).endsWith("REAL OUTPUT")).toBe(true);
    expect(stripAnsi("\x1b]0;title-fragment")).toBe("");
  });

  it("turns \\r into line breaks so a progress bar's LAST redraw wins", () => {
    // Deleting \r outright would weld the redraws into "10%50%90%".
    expect(lastLine("10%\r50%\r90%")).toBe("90%");
  });

  it("removes control chars and decode-replacement chars, keeps \\n", () => {
    expect(stripAnsi("a\x07b\x08c\td")).toBe("abcd");
    expect(stripAnsi("one\ntwo")).toBe("one\ntwo");
    expect(stripAnsi("he\ufffdllo")).toBe("hello");
    expect(stripAnsi("\x1b(Bhello")).toBe("hello"); // charset designation
  });
});

describe("lastLine (pure)", () => {
  it("returns the last NON-empty line, trimmed of prompt whitespace", () => {
    expect(lastLine("one\r\ntwo  \r\n\r\n   ")).toBe("two");
  });

  it("caps to the NEWEST chars with an ellipsis owning the cut", () => {
    const line = "a".repeat(50) + "TAIL-END";
    const out = lastLine(`x\n${line}`, 20);
    expect(out.length).toBe(20);
    expect(out.startsWith("…")).toBe(true);
    expect(out.endsWith("TAIL-END")).toBe(true);
    // Default cap is the shared constant.
    expect(lastLine("z".repeat(300)).length).toBe(TERM_LINE_CHARS);
  });

  it("a chunk that strips to nothing is NOTHING — empty string, no husk", () => {
    expect(lastLine("\x1b[2J\x1b[H")).toBe("");
    expect(lastLine("\r\n  \r\n")).toBe("");
    expect(lastLine("")).toBe("");
  });
});

describe("textTail (pure)", () => {
  it("collapses ALL whitespace to single spaces and trims", () => {
    expect(textTail("a\nb\n\n  c   d ")).toBe("a b c d");
    expect(textTail("")).toBe("");
  });

  it("keeps the TAIL, capped, with an ellipsis owning the cut", () => {
    const out = textTail("start " + "mid ".repeat(40) + "the end", 24);
    expect(out.length).toBe(24);
    expect(out.startsWith("…")).toBe(true);
    expect(out.endsWith("the end")).toBe(true);
    expect(textTail("x".repeat(300)).length).toBe(CHAT_TAIL_CHARS);
  });
});

/* ---- (b) PaneChat: paced textTail reports, immediate transitions ------------- */

describe("PaneChat peek reporting (real component, fake timers)", () => {
  it("a token burst produces BOUNDED reports; the trailing report carries the final tail", () => {
    vi.useFakeTimers();
    const onStatus = vi.fn();
    // No stored thread id → mount is fully synchronous (no waitFor needed
    // under fake timers).
    const view = render(<PaneChat paneId="pb" cwd={CWD} onStatus={onStatus} />);
    expect(onStatus).toHaveBeenCalledTimes(1);
    expect(onStatus.mock.calls[0][0]).toEqual({
      streaming: false,
      approval: false,
      tool: "",
      textTail: "",
    });
    onStatus.mockClear();

    // Stream starts: the busy TRANSITION reports immediately.
    S.stream.streaming = true;
    S.stream.text = "tk0";
    view.rerender(<PaneChat paneId="pb" cwd={CWD} onStatus={onStatus} />);
    expect(onStatus).toHaveBeenCalledTimes(1);
    expect(onStatus.mock.calls[0][0]).toMatchObject({
      streaming: true,
      textTail: "tk0",
    });

    // THE BURST: 20 more token updates, 50ms apart (1s of streaming). Every
    // one changes textTail; a naive report-on-change would fire 20 times and
    // re-render the whole Build canvas per token.
    for (let i = 1; i <= 20; i += 1) {
      act(() => {
        vi.advanceTimersByTime(50);
      });
      S.stream.text += ` tk${i}`;
      view.rerender(<PaneChat paneId="pb" cwd={CWD} onStatus={onStatus} />);
    }
    // Flush the one trailing report the pacer keeps scheduled.
    act(() => {
      vi.advanceTimersByTime(400);
    });
    // Bounded: ~1 transition + one per TAIL_REPORT_MS window (400ms) over
    // 1s + the trailing flush — far fewer than one per token.
    expect(onStatus.mock.calls.length).toBeLessThanOrEqual(6);
    expect(onStatus.mock.calls.length).toBeGreaterThanOrEqual(2);
    // The LAST report carries the burst's final tokens (latestStatusRef is
    // read at fire time — the tail is never stale).
    const last = onStatus.mock.calls[onStatus.mock.calls.length - 1][0];
    expect(last.streaming).toBe(true);
    expect(last.textTail.endsWith("tk20")).toBe(true);

    // A TOOL starting mid-stream bypasses the pacer — immediate, no timer
    // advance, and it names the last still-RUNNING card.
    onStatus.mockClear();
    S.stream.tools = [
      { id: "a", name: "read_file", status: "done", ok: true },
      { id: "b", name: "shell", status: "running" },
    ];
    view.rerender(<PaneChat paneId="pb" cwd={CWD} onStatus={onStatus} />);
    expect(onStatus).toHaveBeenCalledTimes(1);
    expect(onStatus.mock.calls[0][0]).toMatchObject({ tool: "shell" });

    // Idle: everything clears in ONE immediate report — a finished turn's
    // stale card list must never read as current activity.
    onStatus.mockClear();
    S.stream.streaming = false;
    S.stream.text = "";
    S.stream.tools = [{ id: "b", name: "shell", status: "running" }];
    view.rerender(<PaneChat paneId="pb" cwd={CWD} onStatus={onStatus} />);
    expect(onStatus).toHaveBeenCalledTimes(1);
    expect(onStatus.mock.calls[0][0]).toEqual({
      streaming: false,
      approval: false,
      tool: "",
      textTail: "",
    });
  });
});

/* ---- (c) the chat-activity strip (terminal view) ----------------------------- */

describe("chat-activity peek strip (page, terminal view)", () => {
  it("approval outranks tool and text; then tool beats text; then text; then working…; idle removes it", async () => {
    await renderPage();
    fireEvent.click(chatBtn("t1")); // mount the chat

    // In CHAT view the chat is on screen — no chat strip, ever.
    fireEvent.click(screen.getByTestId("emit-approval-t1"));
    expect(screen.queryByTestId("pane-peek-t1")).toBeNull();

    // Hidden chat + approval → the amber strip, outranking the simultaneous
    // tool and text the same report carried.
    fireEvent.click(termBtn("t1"));
    const strip = screen.getByTestId("pane-peek-t1");
    expect(strip).toHaveTextContent("Approval needed — click to answer");
    expect(strip.className).toContain("amber");
    expect(strip.tagName).toBe("BUTTON"); // react-rnd cancel="button…" drag-exempt

    // Approval resolved, tool still running → "Chat: <tool>…".
    fireEvent.click(screen.getByTestId("emit-tool-t1"));
    expect(screen.getByTestId("pane-peek-t1")).toHaveTextContent("Chat: write_file…");
    expect(screen.getByTestId("pane-peek-t1").className).not.toContain("amber");

    // Tools done, text streaming → the tail verbatim.
    fireEvent.click(screen.getByTestId("emit-text-t1"));
    expect(screen.getByTestId("pane-peek-t1")).toHaveTextContent(
      "Chat: …and the final answer is 42",
    );

    // Sending, nothing streamed yet → the honest minimum.
    fireEvent.click(screen.getByTestId("emit-working-t1"));
    expect(screen.getByTestId("pane-peek-t1")).toHaveTextContent("Chat: working…");

    // Turn over → the strip has nothing true to say → gone (same lifecycle
    // as the badge: the moment the chat reports idle).
    fireEvent.click(screen.getByTestId("emit-idle-t1"));
    expect(screen.queryByTestId("pane-peek-t1")).toBeNull();
    // The untouched pane never grew a strip.
    expect(screen.queryByTestId("pane-peek-t2")).toBeNull();
  });

  it("clicking the strip flips to the chat", async () => {
    await renderPage();
    fireEvent.click(chatBtn("t1"));
    fireEvent.click(screen.getByTestId("emit-approval-t1"));
    fireEvent.click(termBtn("t1"));
    expect(screen.getByTestId("chat-layer-t1")).toHaveStyle({ visibility: "hidden" });

    fireEvent.click(screen.getByTestId("pane-peek-t1"));
    // The flip is real: the chat layer is the visible one again…
    expect(screen.getByTestId("chat-layer-t1")).toHaveStyle({ visibility: "visible" });
    expect(screen.getByTestId("term-layer-t1")).toHaveStyle({ visibility: "hidden" });
    // …and the chat strip is gone (the user is looking at the chat now).
    expect(screen.queryByTestId("pane-peek-t1")).toBeNull();
  });

  it("never renders over the pendingClose confirm", async () => {
    await renderPage();
    fireEvent.click(chatBtn("t1"));
    fireEvent.click(screen.getByTestId("emit-approval-t1"));
    fireEvent.click(termBtn("t1"));
    expect(screen.getByTestId("pane-peek-t1")).toBeInTheDocument();

    // Open the close confirm from THIS view (the pane's X → setPendingClose)
    // while the strip has every reason to show: the confirm wins. Belt: the
    // strip is not rendered at all; suspenders: its z-20 sits under the
    // confirm's z-30 anyway.
    fireEvent.click(screen.getByTestId("term-close-t1"));
    expect(screen.getByText("Close this terminal?")).toBeInTheDocument();
    expect(screen.queryByTestId("pane-peek-t1")).toBeNull();

    // Cancel the close: the still-true status comes right back.
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByTestId("pane-peek-t1")).toBeInTheDocument();
  });
});

/* ---- (d) the terminal-line strip (chat view) --------------------------------- */

describe("terminal-line peek strip (page, chat view)", () => {
  it("shows the chunk's last line ANSI-stripped, and clicking flips to the terminal", async () => {
    await renderPage();
    fireEvent.click(chatBtn("t1"));

    P.chunk = "\x1b]0;title\x07\x1b[32m$ pnpm test\x1b[0m\r\nAll 43 passed  \r\n";
    fireEvent.click(screen.getByTestId("emit-output-t1"));
    const strip = screen.getByTestId("pane-peek-t1");
    expect(strip).toHaveTextContent("All 43 passed");
    expect(strip.textContent).not.toContain("\x1b");
    expect(strip.tagName).toBe("BUTTON");

    // Output while WATCHING the terminal grows no strip (t2 stays in
    // terminal view).
    fireEvent.click(screen.getByTestId("emit-output-t2"));
    expect(screen.queryByTestId("pane-peek-t2")).toBeNull();

    // Click → terminal view; the strip and the unseen badge clear together.
    fireEvent.click(strip);
    expect(screen.getByTestId("term-layer-t1")).toHaveStyle({ visibility: "visible" });
    expect(screen.queryByTestId("pane-peek-t1")).toBeNull();
    expect(screen.queryByTestId("pane-term-badge-t1")).toBeNull();
  });

  it("a chunk that strips to NOTHING renders no strip — the unseen badge may fire, the husk may not", async () => {
    await renderPage();
    fireEvent.click(chatBtn("t1"));
    P.chunk = "\x1b[2J\x1b[H\r\n   \r\n"; // real frame, pure escape/whitespace
    fireEvent.click(screen.getByTestId("emit-output-t1"));
    // The frame was REAL output — the v1.212.0 badge honestly fires…
    expect(screen.getByTestId("pane-term-badge-t1")).toBeInTheDocument();
    // …but a peek line with nothing in it is nothing.
    expect(screen.queryByTestId("pane-peek-t1")).toBeNull();
  });

  it("stays while unseen, survives a flip round-trip via recency, hides after the quiet window", async () => {
    await renderPage();
    fireEvent.click(chatBtn("t1"));
    // Fake timers AFTER the async page load; every timeout scheduled from
    // here on (the quiet window) lives on the fake clock.
    vi.useFakeTimers();

    P.chunk = "building 10%\r\n";
    fireEvent.click(screen.getByTestId("emit-output-t1"));
    expect(screen.getByTestId("pane-peek-t1")).toHaveTextContent("building 10%");

    // UNSEEN holds the strip past the quiet window: the timeout fires, the
    // recency check fails, and the strip is still there because the output
    // is still unseen (the spec's OR).
    act(() => {
      vi.advanceTimersByTime(TERM_PEEK_QUIET_MS + 1_000);
    });
    expect(screen.getByTestId("pane-peek-t1")).toHaveTextContent("building 10%");

    // A fresh frame refreshes the line and the clock.
    P.chunk = "building 90%\r\n";
    fireEvent.click(screen.getByTestId("emit-output-t1"));
    expect(screen.getByTestId("pane-peek-t1")).toHaveTextContent("building 90%");

    // Flip to the terminal (output seen — unseen clears), then back: the
    // strip is still up on RECENCY alone…
    fireEvent.click(termBtn("t1"));
    fireEvent.click(chatBtn("t1"));
    expect(screen.getByTestId("pane-peek-t1")).toHaveTextContent("building 90%");
    expect(screen.queryByTestId("pane-term-badge-t1")).toBeNull(); // unseen is clear

    // …and after ~15s of QUIET the scheduled timeout re-evaluates and the
    // strip hides — no polling loop anywhere, one timeout from the last
    // frame. act() flushes the timeout's setState synchronously, so this
    // asserts the REAL thing (the strip's absence) directly.
    act(() => {
      vi.advanceTimersByTime(TERM_PEEK_QUIET_MS);
    });
    expect(screen.queryByTestId("pane-peek-t1")).toBeNull();
  });
});
