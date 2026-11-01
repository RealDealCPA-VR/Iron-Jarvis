/**
 * v1.422.0 (audit gap B4, fix plan C4.7): Build › Code's idle stop no longer
 * kills a dev server the coding tool started, and the session in front gets 60
 * minutes. What the Code view does about it:
 *
 *   - while the session is on screen (the window seen), it tells Iron Jarvis
 *     so once a minute (`POST …/seen`) — never on mount (the first read
 *     already says it);
 *   - a session kept open for a program it runs says so in ONE quiet line
 *     (the daemon's words, naming the program and its port), with a two-press
 *     "Close Claude Code" that stops the tool and the program;
 *   - such a session is read again like one with background commands (its
 *     line stays true, and each read is a look);
 *   - the session list row shows a small icon while it is kept open;
 *   - what the last idle stop ENDED is said until the next message.
 *
 * The intervals are stand-ins fired BY HAND (never by a clock).
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  view: null as null | (() => Record<string, unknown>),
  list: [] as Record<string, unknown>[],
  posts: [] as string[],
  intervals: new Map<number, { fn: () => void; enabled: boolean }>(),
}));

vi.mock("@/lib/useVisibleInterval", async () => {
  const React = await import("react");
  return {
    useVisibleInterval: (fn: () => void, ms: number, enabled = true) => {
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
      if (path === "/build/app/sessions") return Promise.resolve({ sessions: H.list });
      if (path === "/editor/options") return Promise.resolve({ editors: [], preferred: "" });
      if (path === "/build/app/sessions/a1" && H.view) return Promise.resolve(H.view());
      return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
    },
    post: (path: string) => {
      H.posts.push(path);
      return Promise.resolve({ ok: true });
    },
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
  CODE_COMMANDS_REREAD_MS,
  CODE_SEEN_BEAT_MS,
  backgroundRereadMs,
  closeToolWords,
  decodeCodeSession,
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

/** The daemon's own words (hosted/programs.keep_words). */
const KEEP =
  "Keeping Claude Code open: it runs a program on port 3000 (node vite.js --port 3000). It closes by itself once " +
  "that program stops, or after 8 hours with nothing new. To close it now, ask Claude Code to stop it, or press " +
  "Close Claude Code (the program stops with it).";
const STOPPED =
  "Iron Jarvis closed Claude Code after 15 minutes with nothing to do. The program it ran stopped with it:\n" +
  "- node vite.js --port 3000 (port 3000)";

function sess(over: Record<string, unknown> = {}) {
  return {
    id: "a1",
    harness: "claude-code",
    folder: "C:\\work\\shop",
    title: "Shop",
    account: "",
    model: "",
    effort: "",
    mode: "ask",
    harness_session_id: "h1",
    created_at: "2026-11-01T09:00:00Z",
    updated_at: "2026-11-01T09:00:00Z",
    archived_at: null,
    last_error: "",
    running: false,
    process: true,
    waiting: false,
    background: 0,
    background_helpers: 0,
    self_reply: false,
    programs: [],
    keep_note: "",
    stopped_note: "",
    ...over,
  };
}

const EVENTS = [
  { type: "user", text: "start the dev server", turn: "t1" },
  { type: "text_done", item: "m1:0", text: "It runs on port 3000.", turn: "t1" },
  { type: "done", stop: "completed", turn: "t1" },
];

function serve(over: Record<string, unknown>) {
  H.view = () => ({ session: sess(over), events: EVENTS, running: null, turn_id: null, pending: [] });
  H.list = [sess(over)];
}

async function tick(ms: number): Promise<boolean> {
  const it = H.intervals.get(ms);
  if (!it || !it.enabled) return false;
  await act(async () => {
    it.fn();
  });
  return true;
}

const kept = { programs: [{ command: "node vite.js --port 3000", ports: [3000] }], keep_note: KEEP };

beforeEach(() => {
  H.view = null;
  H.list = [];
  H.posts = [];
  H.intervals = new Map();
});

afterEach(cleanup);

describe("what the daemon sends", () => {
  it("decodes the programs, the line and the stop's words; a bad port or no command is dropped", () => {
    const s = decodeCodeSession(
      sess({
        programs: [
          { command: "node vite.js", ports: [3000, 0, 70000, "x", 5173] },
          { command: "", ports: [80] },
          "nope",
        ],
        keep_note: KEEP,
        stopped_note: STOPPED,
      }),
    )!;
    expect(s.programs).toEqual([{ command: "node vite.js", ports: [3000, 5173] }]);
    expect(s.keepNote).toBe(KEEP);
    expect(s.stoppedNote).toBe(STOPPED);
    const old = decodeCodeSession({ id: "x", harness: "codex" })!;
    expect([old.programs, old.keepNote, old.stoppedNote]).toEqual([[], "", ""]);
  });

  it("a session kept for its programs is read again like one with background commands", () => {
    const base = { background: 0, backgroundHelpers: 0, selfReply: false };
    expect(backgroundRereadMs({ ...base, programs: [{ command: "node", ports: [3000] }] }, false)).toBe(
      CODE_COMMANDS_REREAD_MS,
    );
    expect(backgroundRereadMs({ ...base, programs: [] }, false)).toBe(0);
    expect(backgroundRereadMs({ ...base, programs: [{ command: "node", ports: [3000] }] }, true)).toBe(0);
  });
});

describe("the session on screen", () => {
  it("beats once a minute while seen, never on mount", async () => {
    serve({});
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-conversation");
    expect(H.posts.filter((p) => p.endsWith("/seen"))).toEqual([]);
    expect(CODE_SEEN_BEAT_MS).toBe(60_000);
    expect(await tick(CODE_SEEN_BEAT_MS)).toBe(true);
    expect(H.posts).toContain("/build/app/sessions/a1/seen");
  });

  it("kept open for a program: one quiet line, the row's icon, and a two-press Close", async () => {
    serve(kept);
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const line = await screen.findByTestId("code-keeping");
    expect(line.textContent).toContain(KEEP);
    expect(screen.getByTestId("code-row-keeping-a1").getAttribute("title")).toBe(KEEP);
    // Read again (the program may end) — like background commands.
    expect(H.intervals.get(CODE_COMMANDS_REREAD_MS)?.enabled).toBe(true);
    const press = () => screen.getByTestId("code-close-tool").querySelector("button")!;
    expect(press().textContent).toBe(closeToolWords("Claude Code").idle);
    fireEvent.click(press());
    expect(H.posts.filter((p) => p.endsWith("/close"))).toEqual([]);
    expect(press().textContent).toBe(closeToolWords("Claude Code").armed);
    serve({ process: false, stopped_note: "" });
    await act(async () => {
      fireEvent.click(press());
    });
    await waitFor(() => expect(H.posts).toContain("/build/app/sessions/a1/close"));
    await waitFor(() => expect(screen.queryByTestId("code-keeping")).toBeNull());
  });

  it("what the last stop ended is said until the next message", async () => {
    serve({ process: false, stopped_note: STOPPED });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const note = await screen.findByTestId("code-stopped-programs");
    expect(note.textContent).toBe(STOPPED);
    expect(screen.queryByTestId("code-keeping")).toBeNull();
  });

  it("nothing kept: no line, no icon, no extra reads", async () => {
    serve({});
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-conversation");
    expect(screen.queryByTestId("code-keeping")).toBeNull();
    expect(screen.queryByTestId("code-stopped-programs")).toBeNull();
    expect(screen.queryByTestId("code-row-keeping-a1")).toBeNull();
    expect(H.intervals.get(CODE_COMMANDS_REREAD_MS)?.enabled ?? false).toBe(false);
  });
});
