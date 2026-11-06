/**
 * v1.439.0 — Google Antigravity (`agy acp`) in Build's Code view: the user's
 * REAL Antigravity, its own tools and settings on; Iron Jarvis turns every
 * question it asks into a card. Driven through the real view and lib/buildApp;
 * only the network (lib/api), the stream reader and the folder tree are
 * stand-ins.
 *
 * The events are THE CONTRACT's, written by the REAL daemon parser from
 * recorded agy 1.5.2 output (tests/test_build_app_antigravity_v1439.py,
 * IJ_REGEN_BUILD_APP_AGY=1): no new event kind. Antigravity's own words: its
 * own rules first, Ask first and Auto said with their reasons, every card Allow
 * once only, its own sign-in (no accounts), listed only when installed.
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
  ANTIGRAVITY_SIGN_IN_TITLE,
  decodeCodeEvent,
  decodeCodingTools,
  foldConversation,
  ownSignInTitle,
  signsInItself,
  toolChoiceWords,
  toolLabel,
  toolVendor,
  type CodeEvent,
} from "@/lib/buildApp";
import { codeViewOfferFor } from "@/lib/buildCodeOffer";
import { NO_WINDOWS_TOOLS } from "@/lib/codePanes";
import { PANE_CLI_NAMES } from "@/lib/paneAsks";

const REAL = join(process.cwd(), "__tests__", "fixtures", "build-app-events-v1439.json");
const SC = (JSON.parse(readFileSync(REAL, "utf8")) as { scenarios: Record<string, Record<string, unknown>[]> }).scenarios;
const decodeAll = (evs: Record<string, unknown>[]) => evs.map((e) => decodeCodeEvent(e)).filter(Boolean) as CodeEvent[];

const AGY_TOOL = {
  id: "antigravity",
  label: "Antigravity",
  installed: true,
  signed_in: null,
  version: null,
  own_sign_in: true,
  only_when_installed: true,
  sign_in_hint:
    "Antigravity signs in by itself, with its own Google sign-in: to change it, run agy in a Build terminal and sign in there. Iron Jarvis's accounts do not apply.",
  modes: [
    { id: "own", label: "Antigravity's own rules", dangerous: false, about: "Asks before it runs a command." },
    { id: "edits", label: "Auto edits", dangerous: false, about: "Changes files in the folder without asking." },
    { id: "plan", label: "Plan", dangerous: false, about: "Plans first, then works. Not read-only." },
    { id: "full", label: "Full access", dangerous: true, about: "Runs everything without asking." },
  ],
  hidden_modes: [
    {
      id: "ask",
      label: "Ask first",
      reason: 'Antigravity asks before every file change only when your own Antigravity settings say toolPermission "strict".',
    },
    { id: "auto", label: "Auto", reason: "Antigravity's auto mode is not offered over its agent protocol." },
  ],
  models: [
    { id: "", label: "Antigravity's default", efforts: ["low", "high"] },
    { id: "gemini-3.8-flash", label: "Gemini 3.8 Flash", efforts: ["low", "medium", "high"] },
    { id: "gemini-3.1-pro", label: "Gemini 3.1 Pro", efforts: ["low", "high"] },
  ],
  efforts: ["minimal", "low", "medium", "high", "xhigh", "max"],
  accounts: [],
  commands: [{ name: "compact", description: "Compact the conversation now." }],
};
const CLAUDE_TOOL = {
  ...AGY_TOOL,
  id: "claude-code",
  label: "Claude Code",
  own_sign_in: false,
  only_when_installed: false,
  sign_in_hint: "",
  models: [{ id: "", label: "Claude Code's default" }],
  commands: [],
};

const sess = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  harness: "antigravity",
  folder: "C:\\work\\shop",
  title: `Session ${id}`,
  account: "",
  model: "",
  effort: "",
  mode: "own",
  harness_session_id: "fa0048f0-6480-4831-bb4d-767c9955dc6a",
  created_at: "2026-11-06T09:00:00Z",
  updated_at: "2026-11-06T09:00:00Z",
  archived_at: null,
  last_error: "",
  ...over,
});

function seed(sessions: Record<string, unknown>[] = [], tools: unknown[] = [CLAUDE_TOOL, AGY_TOOL]) {
  H.gets = {
    "/build/app/harnesses": { harnesses: tools },
    "/build/app/sessions": { sessions },
    "/editor/options": { editors: [], preferred: "" },
  };
}

const frames = (events: Record<string, unknown>[]) => events.map((e) => ({ event: String(e.type), data: e }));

function waitingOn(scenario: string, type: "approval" | "question", id = "a1") {
  const all = SC[scenario];
  const upTo = all.findIndex((e) => e.type === type);
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

describe("Antigravity's events from the real parser", () => {
  it("every scenario's every event decodes, and each turn ends once with no card left open", () => {
    expect(Object.keys(SC).length).toBe(17);
    for (const [name, evs] of Object.entries(SC)) {
      const got = decodeAll(evs);
      expect(got.length, name).toBe(evs.length);
      expect(got.filter((e) => e.type === "done").length, name).toBe(1);
      const conv = foldConversation(got);
      expect(conv.settled, name).toBe(true);
      expect(conv.asks, name).toEqual([]);
    }
  });

  it("a command card is Allow once only; a Deny ends the reply in words", () => {
    const cmd = decodeAll(SC.antigravity_command_allowed_once).find((e) => e.type === "approval");
    expect(cmd?.type === "approval" && cmd.kind).toBe("command");
    expect(cmd?.type === "approval" && cmd.command).toBe("echo hello-from-agy");
    expect(cmd?.type === "approval" && cmd.canSession).toBe(false);
    const denied = decodeAll(SC.antigravity_command_denied_ends_the_reply);
    expect(denied.some((e) => e.type === "notice" && e.text.startsWith("You declined, so Antigravity stopped"))).toBe(
      true,
    );
  });

  it("a failure names Antigravity; a retry is a notice; a note is read", () => {
    const failed = decodeAll(SC.antigravity_failed_reply);
    const err = failed.find((e) => e.type === "error");
    expect(err?.type === "error" && err.title).toContain("Antigravity");
    expect(failed.at(-1)?.type === "done" && (failed.at(-1) as { stop: string }).stop).toBe("failed");
    const retry = decodeAll(SC.antigravity_retries_the_model);
    expect(retry.some((e) => e.type === "notice" && e.text.startsWith("Antigravity is trying the model again"))).toBe(
      true,
    );
    const steer = decodeAll(SC.antigravity_note_while_it_works).filter((e) => e.type === "steer");
    expect(steer.map((e) => (e.type === "steer" ? e.state : ""))).toEqual(["sent", "read"]);
    const signin = decodeAll(SC.antigravity_not_signed_in).find((e) => e.type === "error");
    expect(signin?.type === "error" && signin.title).toContain("run agy");
  });
});

/* ======================================================= words */

describe("Antigravity's words", () => {
  it("is named, made by Google, signs in by itself and reports no limits here", () => {
    expect(toolLabel("antigravity")).toBe("Antigravity");
    expect(toolVendor("antigravity")).toBe("Google");
    expect(signsInItself("antigravity")).toBe(true);
    expect(ownSignInTitle("antigravity")).toBe(ANTIGRAVITY_SIGN_IN_TITLE);
    expect(ownSignInTitle("antigravity")).not.toContain("Pi");
    expect(NO_WINDOWS_TOOLS).toContain("antigravity");
    expect(PANE_CLI_NAMES.agy).toBe("Antigravity");
  });

  it("is listed only when installed, and the start line names what is listed", () => {
    const listed = decodeCodingTools({ harnesses: [CLAUDE_TOOL, AGY_TOOL] });
    expect(listed.map((t) => t.id)).toEqual(["claude-code", "antigravity"]);
    expect(listed[1].ownSignIn).toBe(true);
    expect(toolChoiceWords(listed)).toBe("Claude Code, Codex, Pi, OpenCode or Antigravity");
    const missing = decodeCodingTools({ harnesses: [CLAUDE_TOOL, { ...AGY_TOOL, installed: false }] });
    expect(missing.map((t) => t.id)).toEqual(["claude-code"]);
  });

  it("a terminal where Antigravity has finished offers Open in Code view", () => {
    const t = { id: "t1", alive: true };
    expect(codeViewOfferFor(t, { agent_cli: "agy", state: "idle", can_open_in_code: true } as never)?.toolName).toBe(
      "Antigravity",
    );
    expect(codeViewOfferFor(t, { agent_cli: "agyx", state: "idle", can_open_in_code: true } as never)).toBeNull();
  });
});

/* ======================================================= the view */

describe("the view", () => {
  it("the mode menu leads with Antigravity's own rules and says why Ask first and Auto are not ones", async () => {
    seed([sess("a1")]);
    H.gets["/build/app/sessions/a1"] = { session: sess("a1"), events: [], running: null, pending: [], commands: [] };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session");
    fireEvent.click(screen.getByTestId("code-mode-chip-button"));
    const menu = screen.getByTestId("code-mode-chip-menu");
    expect(within(menu).getAllByRole("menuitemradio").map((b) => b.dataset.testid)).toEqual([
      "code-mode-chip-option-own",
      "code-mode-chip-option-edits",
      "code-mode-chip-option-plan",
      "code-mode-chip-option-full",
    ]);
    expect(within(menu).getByTestId("code-mode-chip-hidden-about-ask").textContent).toContain("strict");
    expect(within(menu).getByTestId("code-mode-chip-hidden-about-auto").textContent).toContain("auto mode");
  });

  it("a command card offers no Allow for this session", async () => {
    waitingOn("antigravity_command_allowed_once", "approval");
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask", {}, { timeout: 10_000 });
    expect(within(card).getByTestId("code-ask-allow")).toBeInTheDocument();
    expect(within(card).queryByTestId("code-ask-allow-session")).toBeNull();
  });

  it("its question is a card with its choices and a typed answer, and posts the label", async () => {
    waitingOn("antigravity_question_answered", "question");
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask", {}, { timeout: 10_000 });
    expect(within(card).getByTestId("code-ask-option-0-1").textContent).toContain("Red");
    expect(within(card).getByTestId("code-ask-other-0")).toBeInTheDocument();
    fireEvent.click(within(card).getByTestId("code-ask-option-0-1"));
    fireEvent.click(within(card).getByTestId("code-ask-send"));
    await waitFor(() =>
      expect(H.posts.find((p) => p.path === "/build/app/sessions/a1/answer")?.body).toEqual({
        request_id: "ask-1",
        decision: "allow",
        answers: { "Which color?": "Red" },
      }),
    );
  });

  it("New session: Antigravity is in the tool list when installed", async () => {
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    await waitFor(() =>
      expect(screen.getByTestId("code-start").textContent).toContain("Claude Code, Codex, Pi, OpenCode or Antigravity"),
    );
    await waitFor(() => expect(screen.getByTestId("code-start-tool")).not.toBeDisabled(), { timeout: 10_000 });
    fireEvent.click(screen.getByTestId("code-start-tool"));
    expect(await screen.findByTestId("code-start-tool-antigravity")).toBeInTheDocument();
  });

  it("New session: Antigravity is not offered when it is not installed", async () => {
    seed([], [CLAUDE_TOOL, { ...AGY_TOOL, installed: false }]);
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    await waitFor(() =>
      expect(screen.getByTestId("code-start").textContent).toContain("Claude Code, Codex, Pi or OpenCode"),
    );
    await waitFor(() => expect(screen.getByTestId("code-start-tool")).not.toBeDisabled(), { timeout: 10_000 });
    fireEvent.click(screen.getByTestId("code-start-tool"));
    await screen.findByTestId("code-start-tool-menu");
    expect(screen.queryByTestId("code-start-tool-antigravity")).toBeNull();
  });
});
