/**
 * v1.417.0 (a USER BUG: "Im running a coding project via build and claude and
 * it seems to be getting stuck"): Claude Code ended its reply ("I'll report as
 * teams finish") while its helper agents worked on in the background; when
 * they finished it wrote a report of its own that no message owns — and the
 * Code view only read a session while a reply IT started ran, so nothing on
 * screen moved until the user reopened the session.
 *
 *   - while helpers work: ONE quiet line says so ("2 helpers are still
 *     working…", singular for one), and the view reads the session again on a
 *     modest interval (only while the window is seen — the interval itself is
 *     a stand-in here, fired BY HAND, never by a clock);
 *   - the report the tool writes by itself appears as it is written, with no
 *     action from the user, and the line says the tool is writing;
 *   - once nothing is happening: the line goes, the interval stops, and one
 *     last read puts the end of the report on screen;
 *   - background COMMANDS alone (a dev server) are said as commands, read
 *     again far less often, never promised a report.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  view: null as null | (() => Record<string, unknown>),
  reads: 0,
  /** The newest registration of each interval, by its period. */
  intervals: new Map<number, { fn: () => void; enabled: boolean }>(),
}));

vi.mock("@/lib/useVisibleInterval", async () => {
  const React = await import("react");
  return {
    useVisibleInterval: (fn: () => void, ms: number, enabled = true) => {
      // A call site whose period changed (off = 0) leaves its old period.
      const last = React.useRef<number | null>(null);
      if (last.current !== null && last.current !== ms) H.intervals.delete(last.current);
      last.current = ms;
      H.intervals.set(ms, { fn, enabled: enabled && ms > 0 });
    },
  };
});

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: FakeApiError,
    get: (path: string) => {
      if (path === "/build/app/harnesses") return Promise.resolve(TOOLS);
      if (path === "/build/app/sessions") return Promise.resolve({ sessions: [sess()] });
      if (path === "/editor/options") return Promise.resolve({ editors: [], preferred: "" });
      if (path === "/build/app/sessions/a1" && H.view) {
        H.reads += 1;
        return Promise.resolve(H.view());
      }
      return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
    },
    post: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* () {},
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => null }));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import {
  CODE_BACKGROUND_REREAD_MS,
  CODE_COMMANDS_REREAD_MS,
  backgroundRereadMs,
  backgroundWorkWords,
  reuseItems,
  foldConversation,
  type CodeEvent,
} from "@/lib/buildApp";

const TOOLS = {
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      version: "2.1.316",
      modes: [{ id: "ask", label: "Ask first", dangerous: false }],
      hidden_modes: [],
      models: [{ id: "", label: "Claude Code's default" }],
      efforts: [],
      accounts: [],
      commands: [],
    },
  ],
};

function sess(over: Record<string, unknown> = {}) {
  return {
    id: "a1",
    harness: "claude-code",
    folder: "C:\\work\\shop",
    title: "Teams",
    account: "",
    model: "",
    effort: "",
    mode: "ask",
    harness_session_id: "h1",
    created_at: "2026-10-31T09:00:00Z",
    updated_at: "2026-10-31T09:00:00Z",
    archived_at: null,
    last_error: "",
    running: false,
    process: true,
    waiting: false,
    background: 0,
    background_helpers: 0,
    self_reply: false,
    ...over,
  };
}

/** The reply the user's message owned: it ends while the helpers work on. */
const MINE = [
  { type: "user", text: "build it with teams", turn: "t1" },
  { type: "text_done", item: "m1:0", text: "Two teams are on it. I'll report as teams finish.", turn: "t1" },
  { type: "done", stop: "completed", turn: "t1" },
];
/** Claude Code's report of its own (no turn: no message owns it). */
const REPORT_SO_FAR = [{ type: "text", item: "m2:0", delta: "Both teams finished: the cart" }];
const REPORT_DONE = [
  ...REPORT_SO_FAR,
  { type: "text_done", item: "m2:0", text: "Both teams finished: the cart is fixed and tested." },
  { type: "done", stop: "completed" },
];

function serve(over: Record<string, unknown>, events: Record<string, unknown>[]) {
  H.view = () => ({ session: sess(over), events, running: null, turn_id: null, pending: [] });
}

/** Fire the newest registration of the interval with this period, by hand. */
async function tick(ms: number): Promise<boolean> {
  const it = H.intervals.get(ms);
  if (!it || !it.enabled) return false;
  await act(async () => {
    it.fn();
  });
  return true;
}

const enabled = (ms: number) => H.intervals.get(ms)?.enabled === true;

beforeEach(() => {
  H.view = null;
  H.reads = 0;
  H.intervals = new Map();
});

afterEach(() => {
  cleanup();
});

describe("the words", () => {
  it("says helpers, one or many; the tool's own reply; commands; nothing while a reply runs", () => {
    const s = (b: number, h: number, self = false) => ({ background: b, backgroundHelpers: h, selfReply: self });
    expect(backgroundWorkWords(s(1, 1), "Claude Code", false)).toBe(
      "1 helper is still working in the background. Its report will appear here when it finishes.",
    );
    expect(backgroundWorkWords(s(3, 3), "Claude Code", false)).toBe(
      "3 helpers are still working in the background. Their report will appear here when they finish.",
    );
    expect(backgroundWorkWords(s(0, 0, true), "Claude Code", false)).toBe(
      "Claude Code is writing a reply of its own. It appears here as it goes.",
    );
    expect(backgroundWorkWords(s(1, 0), "Claude Code", false)).toBe("1 background command is still running.");
    expect(backgroundWorkWords(s(2, 0), "Claude Code", false)).toBe("2 background commands are still running.");
    expect(backgroundWorkWords(s(2, 2), "Claude Code", true)).toBe("");
    expect(backgroundWorkWords(s(0, 0), "Claude Code", false)).toBe("");
    // How often to read again: helpers or the tool's own reply often,
    // commands alone rarely, nothing at all while a reply of the user's runs
    // (it is followed) or when nothing goes on.
    expect(backgroundRereadMs(s(2, 2), false)).toBe(CODE_BACKGROUND_REREAD_MS);
    expect(backgroundRereadMs(s(0, 0, true), false)).toBe(CODE_BACKGROUND_REREAD_MS);
    expect(backgroundRereadMs(s(1, 0), false)).toBe(CODE_COMMANDS_REREAD_MS);
    expect(backgroundRereadMs(s(2, 2, true), true)).toBe(0);
    expect(backgroundRereadMs(s(0, 0), false)).toBe(0);
    expect(backgroundRereadMs(null, false)).toBe(0);
  });

  it("a read that changes nothing keeps the SAME conversation list (nothing draws again)", () => {
    const first = foldConversation(MINE as CodeEvent[], []).items;
    const again = foldConversation(JSON.parse(JSON.stringify(MINE)) as CodeEvent[], []).items;
    expect(reuseItems(first, again)).toBe(first);
    const more = foldConversation([...MINE, ...REPORT_DONE] as CodeEvent[], []).items;
    expect(reuseItems(first, more)).not.toBe(first);
  });
});

describe("helpers at work after the reply ended", () => {
  it("says so, reads again while they work, shows the report as it is written, and stops when nothing happens", async () => {
    serve({ background: 2, background_helpers: 2 }, MINE);
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const line = await screen.findByTestId("code-background");
    expect(line.textContent).toBe(
      "2 helpers are still working in the background. Their report will appear here when they finish.",
    );
    expect(line.getAttribute("role")).toBe("status");
    expect(enabled(CODE_BACKGROUND_REREAD_MS)).toBe(true);

    // One helper ends.
    serve({ background: 1, background_helpers: 1 }, MINE);
    expect(await tick(CODE_BACKGROUND_REREAD_MS)).toBe(true);
    await waitFor(() =>
      expect(screen.getByTestId("code-background").textContent).toBe(
        "1 helper is still working in the background. Its report will appear here when it finishes.",
      ),
    );

    // The last helper ends; Claude Code starts its report by itself.
    serve({ background: 0, background_helpers: 0, self_reply: true }, [...MINE, ...REPORT_SO_FAR]);
    expect(await tick(CODE_BACKGROUND_REREAD_MS)).toBe(true);
    await waitFor(() =>
      expect(screen.getByTestId("code-background").textContent).toBe(
        "Claude Code is writing a reply of its own. It appears here as it goes.",
      ),
    );
    await waitFor(() => expect(screen.getByTestId("code-conversation").textContent).toContain("Both teams finished: the cart"));
    expect(enabled(CODE_BACKGROUND_REREAD_MS)).toBe(true);

    // The report ended. The read that says so still held the report half
    // written; the view reads once more and the end is on screen — by itself.
    let answered = 0;
    H.view = () => {
      answered += 1;
      return {
        session: sess(),
        events: answered === 1 ? [...MINE, ...REPORT_SO_FAR] : [...MINE, ...REPORT_DONE],
        running: null,
        turn_id: null,
        pending: [],
      };
    };
    expect(await tick(CODE_BACKGROUND_REREAD_MS)).toBe(true);
    await waitFor(() =>
      expect(screen.getByTestId("code-conversation").textContent).toContain(
        "Both teams finished: the cart is fixed and tested.",
      ),
    );
    expect(answered).toBe(2);
    expect(screen.queryByTestId("code-background")).toBeNull();
    // Nothing is happening: no more reads.
    expect(enabled(CODE_BACKGROUND_REREAD_MS)).toBe(false);
    expect(enabled(CODE_COMMANDS_REREAD_MS)).toBe(false);
    const before = H.reads;
    expect(await tick(CODE_BACKGROUND_REREAD_MS)).toBe(false);
    expect(H.reads).toBe(before);
  });

  it("a session with nothing in the background shows no line and reads nothing again", async () => {
    serve({}, MINE);
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-conversation");
    expect(screen.queryByTestId("code-background")).toBeNull();
    expect(enabled(CODE_BACKGROUND_REREAD_MS)).toBe(false);
    expect(enabled(CODE_COMMANDS_REREAD_MS)).toBe(false);
  });

  it("background commands alone are said as commands and read again far less often", async () => {
    serve({ background: 1, background_helpers: 0 }, MINE);
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const line = await screen.findByTestId("code-background");
    expect(line.textContent).toBe("1 background command is still running.");
    expect(enabled(CODE_BACKGROUND_REREAD_MS)).toBe(false);
    expect(enabled(CODE_COMMANDS_REREAD_MS)).toBe(true);
    expect(CODE_COMMANDS_REREAD_MS).toBeGreaterThan(CODE_BACKGROUND_REREAD_MS);
  });
});

describe("Running now", () => {
  it("lists a session whose helpers work on as 'helpers working', and its own report as being written", async () => {
    const { buildAppReplyItems } = await import("@/lib/runningNow");
    const [helpers] = buildAppReplyItems([
      { turn_id: "bg:a1", thread_id: "a1", surface: "build_app", harness: "claude-code", started_at: "", waiting: false, title: "Teams", background: true, helpers: 2 },
    ]);
    // v1.420.0 (C5.1, moved on purpose): the tool in brackets.
    expect(helpers.line).toBe("Coding session (Claude Code) · helpers working");
    expect(helpers.detail).toBe("Teams");
    const [report] = buildAppReplyItems([
      { turn_id: "bg:a1", thread_id: "a1", surface: "build_app", harness: "claude-code", started_at: "", waiting: false, title: "Teams", background: true, helpers: 0 },
    ]);
    expect(report.line).toBe("Coding session (Claude Code) · replying on its own");
  });
});
