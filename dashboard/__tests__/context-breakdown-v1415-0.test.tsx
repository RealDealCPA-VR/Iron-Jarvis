/**
 * v1.415.0 — the Session tab's CONTEXT drop-down, in both coding-session
 * views (components/harness/ContextBreakdown, lib/contextBreakdown). The
 * user, beside Claude Code's own panel: "72% used … Messages 337.7k …
 * Memory files … Free space … 240k until auto-compact … Compact session".
 *
 * The daemon's answers are the REAL ones (fixtures/context-breakdown-v1415-0
 * .json, written by tests/test_context_breakdown_v14150.py with
 * IJ_REGEN_CONTEXT_BREAKDOWN=1): a Chat › Code reply line's `context`, and
 * Build › Code's GET …/context for Claude Code.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import fixture from "./fixtures/context-breakdown-v1415-0.json";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown>,
    puts: [] as [string, unknown][],
    posts: [] as [string, unknown][],
  };
});
vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) => {
    if (!(p in api.gets)) return Promise.reject(new api.ApiError(`unmocked ${p}`, 404));
    return Promise.resolve(api.gets[p]);
  },
  post: (p: string, b?: unknown) => {
    api.posts.push([p, b]);
    if (p === "/chat/changes") return Promise.resolve({ changes: [] });
    return Promise.resolve({});
  },
  put: (p: string, b?: unknown) => {
    api.puts.push([p, b]);
    return Promise.resolve({});
  },
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => null }));

import { HarnessPanes, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { CodePanes } from "@/components/buildCode/CodePanes";
import { decodeSessionView, type BuildSessionView } from "@/lib/buildSessions";
import { decodeCodeSession, decodeCodingTools } from "@/lib/buildApp";
import {
  autoCompactChoice,
  decodeBreakdown,
  decodeCodeContext,
  partPercent,
  settingFor,
  untilSummaryWords,
} from "@/lib/contextBreakdown";

const LINE_CTX = fixture.harness_line_context as Record<string, unknown>;
const CLAUDE_CTX = fixture.claude_context as Record<string, unknown>;

function view(over: Record<string, unknown> = {}): BuildSessionView {
  const v = decodeSessionView({
    id: "s1",
    title: "Cart",
    folder: "C:\\work\\shop",
    folder_name: "shop",
    session_id: "build:s1",
    messages: [
      { role: "user", content: "Add a cart", at: "2026-10-31T10:00:00" },
      { role: "assistant", content: "Done.", at: "2026-10-31T10:01:00", context: LINE_CTX },
    ],
    ...over,
  });
  if (!v) throw new Error("bad fixture");
  return v;
}

const terminal: SessionTerminalApi = { find: () => null, open: async () => null, render: () => null, close: () => {} };

function renderRail(v: BuildSessionView, extra: Partial<React.ComponentProps<typeof HarnessPanes>> = {}) {
  const r = render(
    <HarnessPanes view={v} livePlan={null} terminal={terminal} refreshKey={0} undoFor={() => null}
      onUndo={async () => {}} modelWords="Opus 5.5" {...extra} />,
  );
  fireEvent.click(screen.getByTestId("harness-pane-tab-session"));
  return r;
}

beforeEach(() => {
  api.gets = { "/settings/values": { values: { context_compaction: { suggest_at: 0.7 } } } };
  api.puts = [];
  api.posts = [];
  window.sessionStorage.clear();
  window.localStorage.clear();
});
afterEach(() => cleanup());

describe("the words and numbers (pure)", () => {
  it("decodes the daemon's own breakdown and its parts add up", () => {
    const b = decodeBreakdown(LINE_CTX.breakdown)!;
    expect(b.used).toBe(LINE_CTX.tokens);
    expect(b.parts.filter((p) => p.kind === "used").reduce((n, p) => n + p.tokens, 0)).toBe(b.used);
    expect(b.parts.reduce((n, p) => n + p.tokens, 0)).toBe(b.window);
    expect(b.parts.map((p) => p.label)).toContain("Instruction files");
    expect(b.extra.map((e) => e.label)).toEqual(["Built-in tools"]);
  });

  it("formats a part's share like Claude Code (one decimal, a sliver as <0.1%)", () => {
    expect(partPercent(337_700, 1_000_000)).toBe("33.8%");
    expect(partPercent(703, 1_000_000)).toBe("0.1%");
    expect(partPercent(6, 1_000_000)).toBe("<0.1%");
  });

  it("names how far the automatic summary is", () => {
    const b = decodeBreakdown({ used: 722_500, window: 1_000_000, parts: [], auto_compact: { enabled: true, at: 962_500, left: 240_000 } })!;
    expect(untilSummaryWords(b)).toBe("240k until earlier messages are summarized");
    expect(untilSummaryWords({ ...b, autoCompact: { enabled: false, at: null, left: null, percent: null } }))
      .toBe("The automatic summary is turned off.");
  });

  it("the choice keeps the rest of the setting", () => {
    expect(autoCompactChoice({ suggest_at: 0.7 })).toBe("auto");
    expect(autoCompactChoice({ auto_at: 0.8 })).toBe("80");
    expect(settingFor({ suggest_at: 0.6, enabled: true }, "75")).toEqual({ suggest_at: 0.6, enabled: true, auto_at: 0.75 });
    expect(settingFor({ suggest_at: 0.6, auto_at: 0.8 }, "auto")).toEqual({ suggest_at: 0.6 });
  });
});

describe("Chat › Code: the Session tab's Context drop-down", () => {
  it("shows every part with its swatch, tokens and percent, and a stacked bar with the same words", () => {
    renderRail(view());
    const b = decodeBreakdown(LINE_CTX.breakdown)!;
    const rows = screen.getByTestId("session-rail-context-parts");
    for (const p of b.parts) {
      const row = rows.querySelector(`[data-part="${p.key}"]`) as HTMLElement;
      expect(row.textContent).toContain(p.label);
      expect(row.textContent).toContain(partPercent(p.tokens, b.window));
    }
    const stack = screen.getByTestId("session-rail-context-stack");
    // The free space is the bar's own track, not a segment; every used part
    // is one, and its label rides the bar's accessible name (not colour alone).
    expect(stack.querySelectorAll("[data-part]").length).toBe(b.parts.filter((p) => p.kind !== "free").length);
    expect(stack.getAttribute("aria-label")).toContain("Instruction files");
    expect(screen.getByTestId("session-rail-context-window").textContent).toContain(" / ");
    // Tool descriptions are named as NOT in the percent (never folded in).
    expect(screen.getByTestId("session-rail-context-extra").textContent).toContain("Built-in tools");
    expect(screen.getByTestId("session-rail-context-extra").textContent).toContain("not counted above");
    expect(screen.getByTestId("session-rail-context-until").textContent).toMatch(/until earlier messages are summarized$/);
  });

  it("a list folds open on a press", () => {
    renderRail(view());
    const list = screen.getByTestId("session-rail-context-list-rules");
    const press = within(list).getByRole("button");
    expect(press.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(press);
    expect(press.getAttribute("aria-expanded")).toBe("true");
    expect(list.textContent).toContain("AGENTS.md");
  });

  it("is a disclosure whose closed state this window remembers", () => {
    renderRail(view());
    const toggle = screen.getByTestId("session-rail-context-toggle");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(window.sessionStorage.getItem("ij.session.context.open")).toBe("0");
    expect(toggle.textContent).toContain("% used");
    cleanup();
    renderRail(view());
    expect(screen.getByTestId("session-rail-context-toggle").getAttribute("aria-expanded")).toBe("false");
  });

  it("Summarize runs the session's own Compact, and waits while a reply runs", () => {
    const onCompact = vi.fn();
    renderRail(view(), { onCompact });
    fireEvent.click(screen.getByTestId("session-rail-context-compact"));
    expect(onCompact).toHaveBeenCalledTimes(1);
    cleanup();
    renderRail(view(), { onCompact, busy: true });
    expect((screen.getByTestId("session-rail-context-compact") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId("session-rail-context-compact-why").textContent).toBe(
      // v1.415.0 review (moved on purpose): Chat › Code says the app's word.
      "Wait for the reply to finish, then summarize the earlier turns.",
    );
  });

  it("the automatic summary's mark is the real setting, saved with the rest kept", async () => {
    renderRail(view());
    const select = (await screen.findByTestId("session-rail-context-auto")) as HTMLSelectElement;
    expect(select.value).toBe("auto");
    fireEvent.change(select, { target: { value: "80" } });
    await waitFor(() => expect(api.puts).toHaveLength(1));
    expect(api.puts[0]).toEqual(["/settings/values", { values: { context_compaction: { suggest_at: 0.7, auto_at: 0.8 } } }]);
  });

  it("a reply with no breakdown keeps its plain reading", () => {
    renderRail(view({
      messages: [{ role: "assistant", content: "b", context: { window: 1_000_000, used: 300_000, percent: 38, level: "ok" } }],
    }));
    expect(screen.getByTestId("session-rail-context-window").textContent).toBe("of 1M tokens");
    expect(screen.queryByTestId("session-rail-context-parts")).toBeNull();
    expect(screen.queryByTestId("session-rail-context-auto")).toBeNull();
  });
});

const RAW_SESSION = {
  id: "app_1",
  harness: "claude-code",
  folder: "C:\\work\\shop",
  title: "Fix the cart",
  account: "",
  model: "claude-sonnet-5",
  effort: "",
  mode: "ask",
  harness_session_id: "",
  created_at: "2026-10-30T09:00:00Z",
  updated_at: "2026-10-30T09:00:00Z",
  archived_at: null,
  last_error: "",
  capabilities: { files: false, shell: false, browser: false, extensions: false, memory: true },
};
const TOOLS = decodeCodingTools({
  harnesses: [{ id: "claude-code", label: "Claude Code", installed: true, signed_in: true, own_sign_in: false,
    modes: [{ id: "ask", label: "Ask first", dangerous: false, about: "" }], hidden_modes: [], models: [] }],
});

function renderCode(running = false, onSend = vi.fn()) {
  const session = decodeCodeSession(RAW_SESSION)!;
  render(<CodePanes session={session} tools={TOOLS} events={[]} running={running} onSend={onSend} />);
  fireEvent.click(screen.getByTestId("code-pane-tab-session"));
  return onSend;
}

describe("Build › Code: Claude Code's own /context", () => {
  it("decodes the real route's answer", () => {
    const c = decodeCodeContext(CLAUDE_CTX)!;
    expect(c.reading?.source).toBe("tool");
    expect(c.compactHow).toBe("message");
    expect(c.compactText).toBe("/compact");
  });

  it("draws Claude Code's parts in plain words and sends its own /compact", async () => {
    api.gets["/build/app/sessions/app_1/context"] = CLAUDE_CTX;
    const onSend = renderCode();
    const rows = await screen.findByTestId("code-session-context-parts");
    expect(rows.textContent).toContain("System prompt");
    expect(rows.textContent).toContain("Instruction files");
    expect(rows.textContent).toContain("Kept free for the automatic summary");
    expect(screen.getByTestId("code-session-context-extra").textContent).toContain("Built-in tools (loaded when needed)");
    fireEvent.click(screen.getByTestId("code-session-context-compact"));
    expect(onSend).toHaveBeenCalledWith("/compact");
  });

  it("never sends /compact while a reply runs", async () => {
    api.gets["/build/app/sessions/app_1/context"] = CLAUDE_CTX;
    const onSend = renderCode(true);
    const press = (await screen.findByTestId("code-session-context-compact")) as HTMLButtonElement;
    expect(press.disabled).toBe(true);
    fireEvent.click(press);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("a tool that says only a total says so, and why there is no Compact", async () => {
    api.gets["/build/app/sessions/app_1/context"] = {
      harness: "pi",
      reading: { source: "tool", used: 60_000, window: 200_000, percent: 30, parts: [] },
      waiting: false,
      note: "Pi says only how full the conversation is, not what fills it.",
      compact: { how: "", why: "Pi compacts by itself when the conversation is full." },
    };
    renderCode();
    expect((await screen.findByTestId("code-session-context-note")).textContent).toBe(
      "Pi says only how full the conversation is, not what fills it.",
    );
    expect(screen.getByTestId("code-session-context-percent").textContent).toBe("30% used");
    expect(screen.queryByTestId("code-session-context-parts")).toBeNull();
    expect(screen.queryByTestId("code-session-context-compact")).toBeNull();
    expect(screen.getByTestId("code-session-context-compact-absent").textContent).toBe(
      "Pi compacts by itself when the conversation is full.",
    );
  });
});
