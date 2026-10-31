/**
 * v1.414.0 — Build › Code's right column: Changes, Usage, Files and Session,
 * the same icon tabs Chat › Code has. Driven through the real CodePanes (and
 * once through the real BuildCodeView); the daemon's answers are the shapes
 * `tests/test_build_code_panes_v1414.py` pins on the Python side.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({ gets: {} as Record<string, unknown>, calls: [] as string[] }));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError: FakeApiError,
    get: (path: string) => {
      H.calls.push(path);
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      if (r instanceof Error) return Promise.reject(r);
      return Promise.resolve(r);
    },
    post: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    ijToken: () => "",
    API_BASE: "http://127.0.0.1:8787",
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* () {},
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => null }));

import { CODE_PANES_POLL_MS, CODE_PANE_TABS, CodePanes } from "@/components/buildCode/CodePanes";
import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { decodeCodeSession, decodeCodingTools, type CodeEvent } from "@/lib/buildApp";
import { changesCountWords, decodeCodeChanges, decodeCodeUsage, windowLine } from "@/lib/codePanes";

const RAW_SESSION = {
  id: "app_1",
  harness: "claude-code",
  folder: "C:\\work\\shop",
  title: "Fix the cart",
  account: "",
  model: "claude-sonnet-5",
  effort: "low",
  mode: "ask",
  harness_session_id: "",
  created_at: "2026-10-30T09:00:00Z",
  updated_at: "2026-10-30T09:00:00Z",
  archived_at: null,
  last_error: "",
  capabilities: { files: false, shell: false, browser: false, extensions: false, memory: true },
};
const RAW_TOOLS = {
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      own_sign_in: false,
      modes: [
        { id: "ask", label: "Ask first", dangerous: false, about: "Asks before it edits files or runs commands." },
      ],
      hidden_modes: [],
      models: [{ id: "", label: "Claude Code's default" }],
      efforts: ["low", "medium", "high"],
      accounts: [],
      commands: [],
    },
  ],
};
const SESSION = decodeCodeSession(RAW_SESSION)!;
const TOOLS = decodeCodingTools(RAW_TOOLS);

const CHANGES = {
  source: "git",
  words: "Every file in this folder that differs from where git was when the session started here, committed since or not (new files included). It includes changes you or another program made meanwhile; files git ignores are not listed.",
  since: "2026-10-30T09:00:00Z",
  changes: [
    {
      path: "C:\\work\\shop\\cart.py",
      rel: "cart.py",
      name: "cart.py",
      status: "modified",
      added: 1,
      removed: 1,
      diff: "--- a/cart.py\n+++ b/cart.py\n@@ -1,2 +1,2 @@\n x = 1\n-y = 2\n+y = 3",
      by_tool: true,
    },
    {
      path: "C:\\work\\shop\\notes.md",
      rel: "notes.md",
      name: "notes.md",
      status: "created",
      added: 1,
      removed: 0,
      diff: "--- /dev/null\n+++ b/notes.md\n@@ -0,0 +1,1 @@\n+hello",
      by_tool: false,
    },
  ],
  files: 2,
  added: 2,
  removed: 1,
  truncated_files: false,
  by_tool: 1,
  no_undo: "No Undo here: Claude Code made these changes itself, so use git or Claude Code to take one back.",
};

const soon = new Date(Date.now() + 2 * 3600_000).toISOString();
const USAGE = {
  harness: "claude-code",
  account: null,
  seen: {
    windows: [
      { key: "five_hour", label: "5-hour window", percent: 24, resets_at: soon },
      { key: "seven_day", label: "7-day window", percent: 85, resets_at: soon },
    ],
    at: new Date(Date.now() - 4 * 60_000).toISOString(),
    plan: "",
  },
  totals: { replies: 2, input: 12_340, output: 1_200, cache_read: 0, cost_usd: 0.42, list_price: true, cost_reported_by: "" },
  context: { used: 39_055, window: 1_000_000, percent: 4, at: new Date().toISOString() },
};
const USAGE_NONE = {
  harness: "claude-code",
  account: null,
  seen: null,
  totals: { replies: 0, input: 0, output: 0, cache_read: 0, cost_usd: null, list_price: false, cost_reported_by: "" },
  context: null,
};
const OVERVIEW = {
  folder: "C:\\work\\shop",
  folder_exists: true,
  on_share: false,
  git: { repo: true, branch: "main", detached: false },
  rules: ["CLAUDE.md"],
  context: { used: 39_055, window: 1_000_000, percent: 4, at: new Date().toISOString() },
  tool_running: false,
  programs: [],
};

const EVENTS: CodeEvent[] = [
  { type: "user", at: new Date(Date.now() - 60_000).toISOString(), text: "look at this", files: ["C:\\up\\shot.png"] },
  { type: "user", at: new Date().toISOString(), text: "and now this", files: [] },
];

function setGets(extra: Record<string, unknown> = {}) {
  H.gets = {
    "/build/app/sessions/app_1/changes": CHANGES,
    "/build/app/sessions/app_1/usage": USAGE,
    "/build/app/sessions/app_1/overview": OVERVIEW,
    "/editor/options": { editors: [], preferred: "" },
    ...extra,
  };
}

beforeEach(() => {
  window.localStorage.clear();
  H.calls = [];
  setGets();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function panes(running = false, events: CodeEvent[] = EVENTS) {
  return render(<CodePanes session={SESSION} tools={TOOLS} events={events} running={running} />);
}

describe("the four icon tabs", () => {
  it("are Changes, Usage, Files, Session — icons whose only words are their tips", () => {
    panes();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Changes", "Usage", "Files", "Session"]);
    for (const t of CODE_PANE_TABS) {
      const tab = screen.getByRole("tab", { name: t.label });
      expect(tab.className).toContain("h-8 w-8");
      expect(tab.getAttribute("aria-label")).toBeNull();
      expect(tab.querySelector("svg")).not.toBeNull();
      const tip = screen.getByTestId(`code-pane-tip-${t.value}`);
      // Faded at rest; shown on a resting pointer (after 300 ms) or focus.
      expect(tip.className).toContain("opacity-0");
      expect(tip.className).toContain("group-hover:delay-300");
      expect(tip.className).toContain("group-focus-visible:opacity-100");
    }
  });

  it("move with the arrow keys and remember the choice", async () => {
    panes();
    const changes = screen.getByRole("tab", { name: "Changes" });
    expect(changes.getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(changes, { key: "ArrowLeft" });
    expect(screen.getByRole("tab", { name: "Session" }).getAttribute("aria-selected")).toBe("true");
    expect(window.localStorage.getItem("ij.code.pane")).toBe("session");
    await screen.findByTestId("code-pane-session");
  });
});

describe("Changes", () => {
  it("says its source and limits, lists every diff, and offers no Undo", async () => {
    panes();
    await screen.findByTestId("changes-review");
    expect(screen.getByTestId("code-changes-source").textContent).toContain("you or another program");
    expect(screen.getByTestId("review-path-0").textContent).toContain("cart.py");
    expect(screen.getByTestId("review-path-1").textContent).toContain("notes.md");
    expect(screen.getByTestId("code-changes-by-tool").textContent).toBe("1 of these was edited by Claude Code's own file tools.");
    expect(screen.getByTestId("code-changes-no-undo").textContent).toContain("No Undo here");
    expect(screen.queryByText("Undo")).toBeNull();
    // Nothing says Undo takes one back (the review's hint is the journal's).
    expect(screen.queryByTestId("review-hint")).toBeNull();
    expect(document.querySelector('[data-testid^="review-undo-"]')).toBeNull();
  });

  it("with no folder yet says so and lists nothing", async () => {
    setGets({
      "/build/app/sessions/app_1/changes": {
        source: "none",
        words: "This session has no folder yet: it is made with your first message.",
        changes: [],
        files: 0,
      },
    });
    panes();
    await waitFor(() => expect(screen.getByTestId("code-changes-source").textContent).toContain("no folder yet"));
    expect(screen.queryByTestId("changes-review")).toBeNull();
    expect(screen.queryByTestId("code-changes-none")).toBeNull();
  });

  it("is read again every few seconds only while a reply runs", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const view = panes(false);
    await screen.findByTestId("changes-review");
    const idle = H.calls.filter((p) => p.endsWith("/changes")).length;
    await act(async () => {
      vi.advanceTimersByTime(CODE_PANES_POLL_MS * 3);
    });
    expect(H.calls.filter((p) => p.endsWith("/changes")).length).toBe(idle);
    view.rerender(<CodePanes session={SESSION} tools={TOOLS} events={EVENTS} running />);
    await act(async () => {
      vi.advanceTimersByTime(CODE_PANES_POLL_MS * 2 + 50);
    });
    await waitFor(() => expect(H.calls.filter((p) => p.endsWith("/changes")).length).toBeGreaterThanOrEqual(idle + 2));
    // The reply ends: one more read (its last changes), then no more.
    view.rerender(<CodePanes session={SESSION} tools={TOOLS} events={EVENTS} running={false} />);
    await waitFor(() => expect(H.calls.filter((p) => p.endsWith("/changes")).length).toBeGreaterThanOrEqual(idle + 3));
    const after = H.calls.filter((p) => p.endsWith("/changes")).length;
    await act(async () => {
      vi.advanceTimersByTime(CODE_PANES_POLL_MS * 3);
    });
    expect(H.calls.filter((p) => p.endsWith("/changes")).length).toBe(after);
  });
});

describe("Usage", () => {
  it("shows what is left of each window the tool reported, and when it said it", async () => {
    panes();
    fireEvent.click(screen.getByRole("tab", { name: "Usage" }));
    const seen = await screen.findByTestId("code-usage-seen");
    const rows = within(seen).getAllByTestId("code-usage-window");
    expect(rows[0].textContent).toContain("5-hour window");
    expect(rows[0].textContent).toContain("76% left");
    expect(rows[1].textContent).toContain("15% left");
    expect(screen.getByTestId("code-usage-seen-when").textContent).toBe("As Claude Code reported it, 4 min ago.");
    // A plan's use is a list-price EQUIVALENT, never spend.
    const totals = screen.getByTestId("code-usage-totals").textContent ?? "";
    expect(totals).toContain("2 replies");
    expect(totals).toContain("at list price");
    expect(totals).not.toMatch(/spent/i);
  });

  it("says NOT KNOWN when nobody reported a number — never 0%", async () => {
    setGets({ "/build/app/sessions/app_1/usage": USAGE_NONE });
    panes();
    fireEvent.click(screen.getByRole("tab", { name: "Usage" }));
    const nk = await screen.findByTestId("code-usage-not-known");
    expect(nk.textContent).toContain("Not known yet");
    expect(screen.getByTestId("code-pane-usage").textContent).not.toMatch(/0%/);
  });

  it("names the Iron-Proxy account and its windows from the cached snapshot", async () => {
    setGets({
      "/build/app/sessions/app_1/usage": {
        ...USAGE_NONE,
        account: { id: "p1", name: "Work", plan_label: "Max", status: "usage", windows: [{ label: "5-hour window", percent: 63, resets_at: soon }] },
      },
    });
    panes();
    fireEvent.click(screen.getByRole("tab", { name: "Usage" }));
    const proxy = await screen.findByTestId("code-usage-proxy");
    expect(proxy.textContent).toContain("37% left");
    expect(screen.getByTestId("code-usage-who").textContent).toBe("Claude Code · Work · Max");
  });
});

describe("Session", () => {
  it("says where it works, what answers, how full, the last attachment and the changes", async () => {
    panes();
    fireEvent.click(screen.getByRole("tab", { name: "Session" }));
    await screen.findByTestId("code-pane-session");
    await waitFor(() => expect(screen.getByTestId("code-session-branch").textContent).toBe("On branch main"));
    expect(screen.getByTestId("code-session-folder").textContent).toBe("shop");
    expect(screen.getByTestId("code-session-rules").textContent).toBe("Instruction files here: CLAUDE.md");
    expect(screen.getByTestId("code-session-model").textContent).toBe("Claude Code · claude-sonnet-5 · effort low");
    expect(screen.getByTestId("code-session-mode").textContent).toContain("Ask first — Asks before it edits");
    expect(screen.getByTestId("code-session-jarvis-tools").textContent).toBe("Iron Jarvis tools: Memory");
    expect(screen.getByTestId("code-session-context-percent").textContent).toBe("4% used");
    // The NEWEST message that carried files, not the newest message.
    expect(within(screen.getByTestId("code-session-attachment")).getByAltText("shot.png")).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByTestId("code-session-changes-count").textContent).toBe(
        "2 files changed in this folder · 1 by Claude Code",
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId("code-session-account").textContent).toContain("85% of its 7-day window used"),
    );
    expect(screen.getByTestId("code-session-tool-stopped").textContent).toContain("not running right now");
    fireEvent.click(screen.getByTestId("code-session-open-changes"));
    expect(screen.getByRole("tab", { name: "Changes" }).getAttribute("aria-selected")).toBe("true");
  });

  it("lists what runs under the tool only while it runs, and never claims nothing before it read", async () => {
    setGets({
      "/build/app/sessions/app_1/overview": {
        ...OVERVIEW,
        tool_running: true,
        programs: [{ pid: 12, name: "node.exe", command: "node server.js" }],
      },
    });
    panes(true);
    fireEvent.click(screen.getByRole("tab", { name: "Session" }));
    const list = await screen.findByTestId("code-session-programs");
    expect(list.textContent).toContain("node server.js");
    expect(screen.queryByTestId("code-session-programs-none")).toBeNull();
  });

  it("with no context report says so in words", async () => {
    setGets({ "/build/app/sessions/app_1/overview": { ...OVERVIEW, context: null } });
    panes(false, []);
    fireEvent.click(screen.getByRole("tab", { name: "Session" }));
    await waitFor(() => expect(screen.getByTestId("code-session-context-none").textContent).toBe("Measured after the next reply."));
    expect(screen.getByTestId("code-session-attachment-none")).toBeTruthy();
  });
});

describe("the words", () => {
  it("windowLine says used, left and when it resets; the decoders keep only what was said", () => {
    expect(windowLine({ label: "x", percent: 63, resetsAt: null })).toBe("63% used · 37% left");
    expect(decodeCodeUsage({ seen: { windows: [{ label: "x" }] } })?.seen).toBeNull();
    const c = decodeCodeChanges({ source: "tool", words: "w", changes: [{ path: "a", status: "?", diff: "" }], files: 1 });
    expect(c?.changes[0].status).toBe("modified");
    expect(changesCountWords(c, "Pi")).toBe("1 file Pi edited");
  });
});

describe("in the real Build › Code view", () => {
  it("sits beside the conversation, whose box is still there", async () => {
    setGets({
      "/build/app/harnesses": RAW_TOOLS,
      "/build/app/sessions": { sessions: [RAW_SESSION] },
      "/build/app/sessions/app_1": { session: RAW_SESSION, events: [], running: null, pending: [] },
      "/profile/share": { clis: [], limit: 4000 },
    });
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    render(<BuildCodeView initialSession="app_1" onOpenedTerminal={() => {}} />);
    const row = await screen.findByTestId("code-session-row");
    const aside = within(row).getByTestId("code-panes-aside");
    expect(within(row).getByTestId("code-session")).toBeTruthy();
    expect(within(row).getByTestId("code-box")).toBeTruthy();
    expect(within(aside).getAllByRole("tab").map((t) => t.textContent)).toEqual(["Changes", "Usage", "Files", "Session"]);
    // Side by side from xl, stacked below; the conversation keeps its height.
    expect(row.className).toContain("xl:flex-row");
    expect(aside.className).toContain("xl:w-[320px]");
    expect(within(row).getByTestId("code-session").className).toContain("flex-none");
  });
});
