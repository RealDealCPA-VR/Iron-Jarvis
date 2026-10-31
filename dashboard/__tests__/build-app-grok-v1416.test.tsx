/**
 * v1.416.0 — Grok in Build's Code view: the user's REAL Grok (`grok agent
 * --no-leader stdio`, the Agent Client Protocol), its own tools and config on;
 * Iron Jarvis turns every permission question Grok asks into a card. Driven
 * through the real view and lib/buildApp; only the network (lib/api), the
 * stream reader and the folder tree are stand-ins.
 *
 * The events are THE CONTRACT's, written by the REAL daemon parser from
 * recorded grok 1.0.70 output (tests/test_build_app_grok_v1416.py,
 * IJ_REGEN_BUILD_APP_GROK=1): no new event kind. Grok's own words: three
 * modes (Auto edits and Plan said with their reasons), a command card that is
 * Allow once only (Grok SAVES its own "always"), and Grok listed only when it
 * is installed.
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
  postReply: {} as Record<string, unknown>,
  streams: [] as { path: string; body: unknown }[],
  scripts: [] as { event: string; data: Record<string, unknown> }[][],
  hold: null as Promise<void> | null,
  release: null as (() => void) | null,
}));

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
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(typeof r === "function" ? (r as () => unknown)() : r);
    },
    post: (path: string, body?: unknown) => {
      H.posts.push({ path, body });
      return Promise.resolve(H.postReply[path] ?? {});
    },
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* (
    path: string,
    body: unknown,
    signal?: AbortSignal,
    watch?: { onFrame?: (event: string, data: Record<string, unknown>) => void },
  ) {
    H.streams.push({ path, body });
    const frames = H.scripts.shift() ?? [];
    for (const f of frames) {
      if (signal?.aborted) return;
      watch?.onFrame?.(f.event, f.data);
      await new Promise((r) => setTimeout(r, 5));
    }
    if (H.hold) await H.hold;
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: ({ onSelect }: { onSelect: (p: string) => void }) => (
    <button type="button" data-testid="stub-pick-folder" onClick={() => onSelect("C:\\work\\shop")}>
      pick
    </button>
  ),
}));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import {
  decodeCodeEvent,
  decodeCodingTools,
  foldConversation,
  keepsLastTitle,
  toolChoiceWords,
  toolLabel,
  toolVendor,
  usageWords,
  type CodeEvent,
} from "@/lib/buildApp";
import { codeViewOfferFor } from "@/lib/buildCodeOffer";
import { NO_WINDOWS_TOOLS } from "@/lib/codePanes";

const REAL = join(process.cwd(), "__tests__", "fixtures", "build-app-events-v1416.json");
const SC = (JSON.parse(readFileSync(REAL, "utf8")) as { scenarios: Record<string, Record<string, unknown>[]> }).scenarios;
const decodeAll = (evs: Record<string, unknown>[]) => evs.map((e) => decodeCodeEvent(e)).filter(Boolean) as CodeEvent[];

const GROK_TOOL = {
  id: "grok",
  label: "Grok",
  installed: true,
  signed_in: true,
  version: null,
  own_sign_in: false,
  only_when_installed: true,
  modes: [
    { id: "ask", label: "Ask first", dangerous: false, about: "Asks before it changes a file or runs a command." },
    { id: "auto", label: "Auto", dangerous: false, about: "Grok's own Auto." },
    { id: "full", label: "Full access", dangerous: true, about: "Runs everything without asking." },
  ],
  hidden_modes: [
    { id: "edits", label: "Auto edits", reason: "Grok has no setting over its agent protocol that lets file edits through without asking." },
    { id: "plan", label: "Plan", reason: "Grok's plan mode cannot be started from here (its agent mode ignores it) — choose Ask first." },
  ],
  models: [
    { id: "", label: "Grok's default", efforts: ["low", "medium", "high", "xhigh"] },
    { id: "grok-4.5", label: "Grok 4.5", efforts: ["low", "medium", "high"] },
  ],
  efforts: ["low", "medium", "high", "xhigh"],
  accounts: [],
  commands: [{ name: "compact", description: "Compress conversation history" }],
};
const CLAUDE_TOOL = {
  ...GROK_TOOL,
  id: "claude-code",
  label: "Claude Code",
  only_when_installed: false,
  models: [{ id: "", label: "Claude Code's default" }],
  commands: [],
};

const sess = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  harness: "grok",
  folder: "C:\\work\\shop",
  title: `Session ${id}`,
  account: "",
  model: "grok-4.5",
  effort: "",
  mode: "ask",
  harness_session_id: "01a19366-8cea-7361-97d2-84add5db6831",
  created_at: "2026-10-31T09:00:00Z",
  updated_at: "2026-10-31T09:00:00Z",
  archived_at: null,
  last_error: "",
  ...over,
});

function seed(sessions: Record<string, unknown>[] = [], tools: unknown[] = [CLAUDE_TOOL, GROK_TOOL]) {
  H.gets = {
    "/build/app/harnesses": { harnesses: tools },
    "/build/app/sessions": { sessions },
    "/editor/options": { editors: [], preferred: "" },
  };
}

const frames = (events: Record<string, unknown>[]) => events.map((e) => ({ event: String(e.type), data: e }));

function waitingOn(scenario: string, id = "g1") {
  const all = SC[scenario];
  const upTo = all.findIndex((e) => e.type === "approval");
  const events = all.slice(0, upTo + 1);
  seed([sess(id)]);
  H.gets[`/build/app/sessions/${id}`] = { session: sess(id), events, running: "turn-1", pending: [] };
  H.scripts = [frames(events)];
  H.hold = new Promise<void>((res) => {
    H.release = res;
  });
}

beforeEach(() => {
  H.posts = [];
  H.postReply = {};
  H.streams = [];
  H.scripts = [];
  H.hold = null;
  H.release = null;
  seed();
});

afterEach(() => {
  H.release?.();
  cleanup();
});

/* ======================================================= the contract */

describe("Grok's events from the real parser", () => {
  it("every scenario's every event decodes, and each turn ends once with no card left open", () => {
    expect(Object.keys(SC).length).toBe(9);
    for (const [name, evs] of Object.entries(SC)) {
      const got = decodeAll(evs);
      expect(got.length, name).toBe(evs.length);
      expect(got.filter((e) => e.type === "done").length, name).toBe(1);
      const conv = foldConversation(got);
      expect(conv.settled, name).toBe(true);
      expect(conv.asks, name).toEqual([]);
    }
  });

  it("an edit card carries its diff and Allow for this session; a command card is Allow once only", () => {
    const edit = decodeAll(SC.grok_asks_before_an_edit).find((e) => e.type === "approval");
    expect(edit?.type === "approval" && edit.kind).toBe("edit");
    expect(edit?.type === "approval" && edit.title).toBe("Write notes.md");
    expect(edit?.type === "approval" && edit.diff).toContain("+banana");
    expect(edit?.type === "approval" && edit.canSession).toBe(true);
    const cmd = decodeAll(SC.grok_command_allowed_once).find((e) => e.type === "approval");
    expect(cmd?.type === "approval" && cmd.kind).toBe("command");
    expect(cmd?.type === "approval" && cmd.command).toBe("python --version");
    expect(cmd?.type === "approval" && cmd.canSession).toBe(false);
  });

  it("a Deny ends the reply in words; Stop says the command was stopped and Grok closed", () => {
    const denied = decodeAll(SC.grok_command_denied);
    expect(denied.some((e) => e.type === "notice" && e.text.startsWith("You declined, so Grok stopped"))).toBe(true);
    expect(denied.at(-1)?.type === "done" && (denied.at(-1) as { stop: string }).stop).toBe("completed");
    const stop = decodeAll(SC.grok_stop);
    expect(stop.at(-1)?.type === "done" && (stop.at(-1) as { stop: string }).stop).toBe("interrupted");
    expect(stop.some((e) => e.type === "notice" && e.text.includes("Grok was closed to end it"))).toBe(true);
    const steer = decodeAll(SC.grok_note_while_it_works).filter((e) => e.type === "steer");
    expect(steer.map((e) => (e.type === "steer" ? e.state : ""))).toEqual(["sent", "read"]);
  });

  it("Grok's cost is the cost Grok reported, never list price", () => {
    const usage = decodeAll(SC.grok_reads_a_file).find((e) => e.type === "usage");
    expect(usage?.type === "usage" && usage.listPrice).toBe(false);
    expect(usageWords({ input: 10, output: 5, costUsd: 0.25, listPrice: false, costReportedBy: "Grok" })).toBe(
      "10 in · 5 out · $0.25, the cost Grok reported",
    );
  });
});

/* ======================================================= words */

describe("Grok's words", () => {
  it("is named, sold by xAI, says no windows, and keeps its last model", () => {
    expect(toolLabel("grok")).toBe("Grok");
    expect(toolVendor("grok")).toBe("xAI");
    expect(NO_WINDOWS_TOOLS).toContain("grok");
    expect(keepsLastTitle("grok")).toContain("Grok");
    expect(keepsLastTitle("grok")).not.toContain("Pi");
  });

  it("is listed only when installed, and the start line names what is listed", () => {
    const listed = decodeCodingTools({ harnesses: [CLAUDE_TOOL, GROK_TOOL] });
    expect(listed.map((t) => t.id)).toEqual(["claude-code", "grok"]);
    expect(toolChoiceWords(listed)).toBe("Claude Code, Codex, Pi, OpenCode or Grok");
    const missing = decodeCodingTools({ harnesses: [CLAUDE_TOOL, { ...GROK_TOOL, installed: false }] });
    expect(missing.map((t) => t.id)).toEqual(["claude-code"]);
    // The four before Grok stay listed (with "not installed" said) as before.
    const oldTool = decodeCodingTools({ harnesses: [{ ...CLAUDE_TOOL, installed: false }] });
    expect(oldTool.map((t) => t.id)).toEqual(["claude-code"]);
    expect(toolChoiceWords(null)).toBe("Claude Code, Codex, Pi or OpenCode");
  });

  it("a terminal where Grok has finished offers Open in Code view", () => {
    const t = { id: "t1", alive: true };
    expect(codeViewOfferFor(t, { agent_cli: "grok", state: "idle", can_open_in_code: true } as never)?.toolName).toBe(
      "Grok",
    );
    expect(codeViewOfferFor(t, { agent_cli: "grokker", state: "idle", can_open_in_code: true } as never)).toBeNull();
  });
});

/* ======================================================= the view */

describe("the view", () => {
  it("the mode menu offers Grok's three presets and says why Auto edits and Plan are not ones", async () => {
    seed([sess("g1")]);
    H.gets["/build/app/sessions/g1"] = { session: sess("g1"), events: [], running: null, pending: [], commands: [] };
    render(<BuildCodeView initialSession="g1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session");
    fireEvent.click(screen.getByTestId("code-mode-chip-button"));
    const menu = screen.getByTestId("code-mode-chip-menu");
    expect(within(menu).getAllByRole("menuitemradio").map((b) => b.dataset.testid)).toEqual([
      "code-mode-chip-option-ask",
      "code-mode-chip-option-auto",
      "code-mode-chip-option-full",
    ]);
    expect(within(menu).getByTestId("code-mode-chip-hidden-edits").textContent).toContain("no setting");
    expect(within(menu).getByTestId("code-mode-chip-hidden-plan").textContent).toContain("plan mode");
  });

  it("a command card offers no Allow for this session", async () => {
    waitingOn("grok_command_allowed_once");
    render(<BuildCodeView initialSession="g1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    expect(within(card).getByTestId("code-ask-allow")).toBeInTheDocument();
    expect(within(card).queryByTestId("code-ask-allow-session")).toBeNull();
  });

  it("an edit card offers Allow for this session, and posts it", async () => {
    waitingOn("grok_asks_before_an_edit");
    render(<BuildCodeView initialSession="g1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    fireEvent.click(within(card).getByTestId("code-ask-allow-session"));
    await waitFor(() =>
      expect(H.posts.find((p) => p.path === "/build/app/sessions/g1/answer")?.body).toEqual({
        request_id: "ask-1",
        decision: "allow_session",
      }),
    );
  });

  it("New session: Grok is in the tool list when installed, and the line names it", async () => {
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    await waitFor(() =>
      expect(screen.getByTestId("code-start").textContent).toContain("Claude Code, Codex, Pi, OpenCode or Grok"),
    );
    fireEvent.click(await screen.findByTestId("code-start-tool"));
    expect(await screen.findByTestId("code-start-tool-grok")).toBeInTheDocument();
  });

  it("New session: Grok is not offered when it is not installed", async () => {
    seed([], [CLAUDE_TOOL, { ...GROK_TOOL, installed: false }]);
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    await waitFor(() =>
      expect(screen.getByTestId("code-start").textContent).toContain("Claude Code, Codex, Pi or OpenCode"),
    );
    fireEvent.click(await screen.findByTestId("code-start-tool"));
    await screen.findByTestId("code-start-tool-menu");
    expect(screen.queryByTestId("code-start-tool-grok")).toBeNull();
    expect(screen.getByTestId("code-start").textContent).not.toContain("Grok");
  });
});
