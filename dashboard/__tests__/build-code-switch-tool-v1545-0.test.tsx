/**
 * v1.545.0 — Build › Code: one session goes on with ANOTHER coding tool
 * (`POST /build/app/sessions/{id}/switch-tool`; hosted/tool_switch.py).
 *
 *   - the session's picker lists the tools ONLY when the view says
 *     `switches_tool` and no reply runs; otherwise the tool stays fixed;
 *   - a press on another tool asks (`confirm: false`) and shows the daemon's
 *     own sentences; nothing is PATCHed; Cancel and Esc send nothing more;
 *   - the second press sends `confirm: true` with the SAME tool, model and
 *     effort, and the session becomes the new tool's;
 *   - a refusal of the first press is said in the confirmation's own place; a
 *     refusal of the second where the view says every refused press; nothing
 *     changes either way; an answer for a session no longer on screen is
 *     dropped;
 *   - each reply keeps the tool that wrote it (the fold's five-step rule), so
 *     earlier receipts still name the old tool after a switch;
 *   - the summary the new tool was given is a folded row under the message it
 *     rode: closed by default, opened to the EXACT text, never Markdown/HTML;
 *   - one quiet line says a summary waits for the next message;
 *   - the limit card offers "Continue on another tool" only when the session
 *     can switch, and that press only opens the tool list;
 *   - the decoders refuse or trim what is not the contract;
 *   - both presses carry what the user would START that tool with in this
 *     folder (`prefer_model` / `prefer_effort`: the folder's defaults for the
 *     tool, else its last pick), never as a pick, and the picker ticks what
 *     the daemon's plan says will run;
 *   - the second press sends the shown plan's token (`expect`); when the
 *     daemon says the plan changed, the plan is read again by itself, the
 *     sentence shows above the new lines, and switching takes a fresh press.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

type Answer = unknown | ((body: Record<string, unknown>) => unknown);

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  got: [] as string[],
  posts: [] as { path: string; body: Record<string, unknown> }[],
  patches: [] as { path: string; body: unknown }[],
  answers: {} as Record<string, unknown>,
  copied: [] as string[],
  hold: false,
  /** Frames the next reply's stream delivers (then it ends, unless `hold`). */
  frames: [] as { name: string; data: unknown }[],
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    body?: unknown;
    constructor(message: string, status = 0, body?: unknown) {
      super(message);
      this.status = status;
      this.name = "ApiError";
      if (body !== undefined) this.body = body;
    }
  }
  return {
    ApiError: FakeApiError,
    get: (path: string) => {
      H.got.push(path);
      const held = H.gets[path];
      // (A function answers each read in turn.)
      const r = typeof held === "function" ? (held as () => unknown)() : held;
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      if (r instanceof Error) return Promise.reject(r);
      return Promise.resolve(r);
    },
    post: (path: string, body?: Record<string, unknown>) => {
      H.posts.push({ path, body: body ?? {} });
      const a = H.answers[path];
      const r = typeof a === "function" ? (a as (b: Record<string, unknown>) => unknown)(body ?? {}) : a;
      if (r instanceof Error) return Promise.reject(r);
      return Promise.resolve(r ?? {});
    },
    patch: (path: string, body?: unknown) => {
      H.patches.push({ path, body });
      return Promise.resolve({});
    },
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  // A reply's stream delivers `H.frames`, then ends — or keeps running (`H.hold`).
  streamSSE: async function* (
    _path: string,
    _body: unknown,
    _signal: unknown,
    opts?: { onFrame?: (name: string, data: unknown) => void },
  ) {
    for (const f of H.frames.splice(0)) opts?.onFrame?.(f.name, f.data);
    if (H.hold) await new Promise(() => {});
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <span />,
}));

vi.mock("@/components/settings/MaintenanceTools", () => ({
  copyPlain: (text: string) => {
    H.copied.push(text);
    return Promise.resolve("copied");
  },
}));

import { ACCOUNT_NOW_TITLE, BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { PICKER_HINT, PICKER_HINT_WITH_QUESTION } from "@/components/buildCode/ModelPicker";
import { CodeConversation } from "@/components/buildCode/CodeConversation";
import { HandoffRow, HandoffWaiting, SwitchConfirm } from "@/components/buildCode/ToolSwitch";
import { LAST_PICK_KEY } from "@/components/buildCode/StartComposer";
import { resetFolderDefaultsCache } from "@/lib/folderDefaults";
import {
  HANDOFF_TEXT_MAX,
  conversationMarkdown,
  decodeCodeEvent,
  decodeCodingTools,
  decodeCodeSessionView,
  foldConversation,
  foldConversationFrom,
  receiptLine,
  type CodeEvent,
  type ConvItem,
  type FoldMemo,
} from "@/lib/buildApp";
import {
  CONTINUE_ON_ANOTHER_TOOL,
  SWITCH_ANSWER_LOST,
  SWITCH_ANSWER_UNREAD,
  SWITCH_CANCEL,
  SWITCH_CLOSE,
  SWITCH_LINES_MAX,
  SWITCH_LINE_CHARS,
  SWITCH_REARM_GUARD_MS,
  SWITCH_STOPS_MAX,
  SWITCH_TOKEN_CHARS,
  decodeHandoffPending,
  decodeSwitchAnswer,
  decodeSwitchPlan,
  handoffRowWords,
  handoffWaitingWords,
  isChangedRefusal,
  isSwitchRefusal,
  pendingFromPlan,
  severalToolsWrote,
  switchBody,
  switchHeading,
  switchPreference,
  switchPress,
  switchRefusedHeading,
  toolStartWants,
} from "@/lib/buildAppSwitch";

// ── fixtures ────────────────────────────────────────────────────────────────

const tool = (id: string, label: string, models: unknown[] = []) => ({
  id,
  label,
  installed: true,
  signed_in: true,
  modes: [{ id: "ask", label: "Ask first", dangerous: false }],
  models,
  efforts: [],
  accounts: [],
  commands: [],
});
const TOOLS = {
  harnesses: [
    tool("claude-code", "Claude Code", [{ id: "claude-opus-5-5", label: "Opus 5.5", efforts: ["low", "high"] }]),
    tool("codex", "Codex", [
      { id: "gpt-6-astra", label: "GPT-6 Astra", efforts: ["low", "high"] },
      { id: "gpt-6-mini", label: "GPT-6 Mini", efforts: ["low"] },
    ]),
    tool("pi", "Pi"),
  ],
};

const sess = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  harness: "claude-code",
  folder: "C:\\work\\shop",
  title: `Session ${id}`,
  account: "",
  model: "",
  effort: "",
  mode: "ask",
  harness_session_id: `hid-${id}`,
  created_at: "2026-11-19T09:00:00Z",
  updated_at: "2026-11-19T09:00:00Z",
  archived_at: null,
  last_error: "",
  source: "app",
  ...over,
});

const EVENTS = [
  { type: "user", at: "2026-11-19T10:00:00Z", text: "first one", files: [], tool: "claude-code", turn: "t1" },
  { type: "text_done", at: "2026-11-19T10:00:01Z", item: "a", text: "reply one", turn: "t1" },
  { type: "done", at: "2026-11-19T10:00:02Z", stop: "completed", turn: "t1" },
];

const SWITCH = "/build/app/sessions/a1/switch-tool";
/** Where the session's folder's defaults are read (sess().folder). */
const DEFAULTS = `/build/folders/defaults?folder=${encodeURIComponent("C:\\work\\shop")}`;
const lastPicked = (toolId: string, model: string, effort = "") =>
  window.localStorage.setItem(LAST_PICK_KEY + toolId, JSON.stringify({ model, effort }));
const VIEW = (id: string) => `/build/app/sessions/${id}`;

const LINES = [
  "Account: Work.",
  "Mode: Ask first.",
  "It will be given a summary of this conversation with your next message.",
  "The summary is sent to Codex's own service.",
];
/** What a plan's token looks like (the daemon's: 20 hex characters). */
const TOKEN = "0123456789abcdef0123";
const PLAN = {
  token: TOKEN,
  tool: "codex",
  label: "Codex",
  from: "claude-code",
  from_label: "Claude Code",
  account: "acc-2",
  account_name: "Work",
  mode: "ask",
  mode_label: "Ask first",
  model: "",
  effort: "",
  resumes: false,
  cloud: true,
  lines: LINES,
  stops: [],
};
const NOTE = "Switched from Claude Code to Codex.";
/** The very event the switch wrote (the answer carries it). */
const NOTICE = {
  type: "notice",
  at: "2026-11-19T10:05:00Z",
  text: NOTE,
  switch: { from: "claude-code", to: "codex", resumes: false, since: "2026-11-19T10:00:02Z" },
};
/** A refusal the daemon wrote (a 4xx with its own body). */
async function refusal(detail: string, status = 409, more: Record<string, unknown> = {}) {
  const { ApiError } = (await import("@/lib/api")) as unknown as {
    ApiError: new (m: string, s: number, b?: unknown) => Error;
  };
  return new ApiError(detail, status, { detail, ...more });
}
async function apiError() {
  return ((await import("@/lib/api")) as unknown as { ApiError: new (m: string, s: number, b?: unknown) => Error }).ApiError;
}
const CODEX_VIEW = (more: Record<string, unknown> = {}) => ({
  session: sess("a1", { harness: "codex", account: "acc-2" }),
  events: [...EVENTS, NOTICE],
  running: null,
  pending: [],
  switches_tool: true,
  switched_tools: true,
  ...more,
});

/** Open session `id` in the Code view (`view`: what its read adds or changes). */
function openView(id: string, view: Record<string, unknown> = {}, ids: string[] = [id]) {
  H.gets = {
    "/build/app/harnesses": TOOLS,
    "/build/app/sessions": { sessions: ids.map((x) => sess(x)) },
    "/editor/options": { editors: [], preferred: "" },
  };
  for (const x of ids) {
    H.gets[VIEW(x)] = { session: sess(x), events: EVENTS, running: null, pending: [], switches_tool: true, ...(x === id ? view : {}) };
  }
}

/** The daemon's two answers: a plan for the first press, the switch for the second. */
function answersSwitch(over: { confirm?: Answer } = {}) {
  H.answers[SWITCH] = (body: Record<string, unknown>) => {
    if (body.confirm !== true) return { switched: false, plan: PLAN };
    if (over.confirm !== undefined) return typeof over.confirm === "function" ? (over.confirm as () => unknown)() : over.confirm;
    return {
      switched: true,
      session: sess("a1", { harness: "codex", account: "acc-2" }),
      plan: PLAN,
      note: NOTE,
      notice: NOTICE,
    };
  };
}

async function openPicker() {
  const chip = await screen.findByTestId("code-model-chip", {}, { timeout: 10_000 });
  fireEvent.click(chip);
  return chip;
}

const switchPosts = () => H.posts.filter((p) => p.path.endsWith("/switch-tool"));
const chipTool = () => screen.getByTestId("code-model-chip").getAttribute("data-tool");

const ev = (raw: Record<string, unknown>): CodeEvent => {
  const e = decodeCodeEvent(raw);
  if (!e) throw new Error(`not an event: ${JSON.stringify(raw)}`);
  return e;
};
const fold = (raws: Record<string, unknown>[]): ConvItem[] => foldConversation(raws.map(ev)).items;
const receipts = (items: ConvItem[]) =>
  items.filter((it): it is Extract<ConvItem, { kind: "done" }> => it.kind === "done").map((d) => d.receipt?.tool);
const userTools = (items: ConvItem[]) =>
  items.filter((it): it is Extract<ConvItem, { kind: "user" }> => it.kind === "user").map((u) => u.tool);

/** One whole turn (a message, a reply, its end) as raw events. */
function turn(n: number, over: { tool?: string; session?: string; model?: string } = {}): Record<string, unknown>[] {
  const at = (s: number) => `2026-11-19T10:0${n}:0${s}Z`;
  return [
    { type: "user", at: at(0), text: `ask ${n}`, files: [], turn: `t${n}`, ...(over.tool ? { tool: over.tool } : {}) },
    ...(over.session
      ? [{ type: "session", at: at(1), harness: over.session, harness_session_id: "h", model: over.model ?? "", turn: `t${n}` }]
      : []),
    { type: "text_done", at: at(2), item: `i${n}`, text: `reply ${n}`, turn: `t${n}` },
    { type: "done", at: at(3), stop: "completed", turn: `t${n}` },
  ];
}
const switched = (n: number, from: string, to: string, resumes = false): Record<string, unknown> => ({
  type: "notice",
  at: `2026-11-19T10:0${n}:09Z`,
  text: `Switched from ${from} to ${to}.`,
  switch: { from, to, resumes },
});

beforeEach(() => {
  window.localStorage.clear();
  resetFolderDefaultsCache();
  H.posts = [];
  H.patches = [];
  H.got = [];
  H.answers = {};
  H.copied = [];
  H.hold = false;
  H.frames = [];
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ── the decoders ────────────────────────────────────────────────────────────

describe("the decoders never trust the wire", () => {
  it("a plan keeps the daemon's sentences, bounded; one that names no tool is refused", () => {
    const p = decodeSwitchPlan(PLAN)!;
    expect(p.lines).toEqual(LINES);
    expect([p.tool, p.label, p.from, p.fromLabel, p.accountName, p.modeLabel]).toEqual([
      "codex",
      "Codex",
      "claude-code",
      "Claude Code",
      "Work",
      "Ask first",
    ]);
    expect(p.cloud).toBe(true);
    expect(decodeSwitchPlan({ ...PLAN, tool: "" })).toBeNull();
    expect(decodeSwitchPlan({ ...PLAN, tool: 7 })).toBeNull();
    expect(decodeSwitchPlan("codex")).toBeNull();
    expect(decodeSwitchPlan(null)).toBeNull();
    // At most 12 lines of 400 characters; what is not a sentence is dropped.
    const many = Array.from({ length: 30 }, (_, i) => `line ${i}`);
    const long = decodeSwitchPlan({ ...PLAN, lines: [7, null, "", "  ", "x".repeat(900), ...many], stops: many })!;
    expect(long.lines).toHaveLength(SWITCH_LINES_MAX);
    expect(SWITCH_LINES_MAX).toBe(12);
    expect(long.lines[0]).toHaveLength(SWITCH_LINE_CHARS);
    expect(SWITCH_LINE_CHARS).toBe(400);
    expect(long.lines[1]).toBe("line 0");
    expect(long.stops).toHaveLength(SWITCH_STOPS_MAX);
    expect(SWITCH_STOPS_MAX).toBe(20);
    expect(decodeSwitchPlan({ ...PLAN, lines: "Account: Work." })!.lines).toEqual([]);
    // `cloud` is true only when the daemon said true (null = not known).
    expect(decodeSwitchPlan({ ...PLAN, cloud: null })!.cloud).toBe(false);
    expect(decodeSwitchPlan({ ...PLAN, cloud: "yes" })!.cloud).toBe(false);
    // A missing label is the tool's own name, never blank.
    expect(decodeSwitchPlan({ tool: "codex" })!.label).toBe("Codex");
    // The token as given (never clipped: a cut one would never match).
    expect(decodeSwitchPlan(PLAN)!.token).toBe(TOKEN);
    expect(SWITCH_TOKEN_CHARS).toBe(64);
    expect(decodeSwitchPlan({ ...PLAN, token: "a".repeat(64) })!.token).toBe("a".repeat(64));
    // No token at all (an older Iron Jarvis): a plan, switched without `expect`.
    const { token: _none, ...OLD_PLAN } = PLAN;
    expect(decodeSwitchPlan(OLD_PLAN)!.token).toBe("");
    expect(decodeSwitchPlan({ ...PLAN, token: null })!.token).toBe("");
    // A token that is there but cannot be sent back as it is: NOT a plan (the
    // second press would go out unguarded).
    expect(decodeSwitchPlan({ ...PLAN, token: "a".repeat(65) })).toBeNull();
    expect(decodeSwitchPlan({ ...PLAN, token: "" })).toBeNull();
    expect(decodeSwitchPlan({ ...PLAN, token: 12345 })).toBeNull();
    expect(decodeSwitchPlan({ ...PLAN, token: ["0123"] })).toBeNull();
    expect(decodeSwitchAnswer({ switched: false, plan: { ...PLAN, token: "a".repeat(65) } })).toBeNull();
    // `summary` is false only when the daemon said false.
    expect(decodeSwitchPlan(PLAN)!.summary).toBe(true);
    expect(decodeSwitchPlan({ ...PLAN, summary: true })!.summary).toBe(true);
    expect(decodeSwitchPlan({ ...PLAN, summary: false })!.summary).toBe(false);
    expect(decodeSwitchPlan({ ...PLAN, summary: 0 })!.summary).toBe(true);
    expect(decodeSwitchPlan({ ...PLAN, summary: "false" })!.summary).toBe(true);
  });

  it("an answer is a plan or a switch; `switched` is true only when the daemon said so", () => {
    expect(decodeSwitchAnswer(null)).toBeNull();
    expect(decodeSwitchAnswer([])).toBeNull();
    expect(decodeSwitchAnswer({})).toBeNull();
    expect(decodeSwitchAnswer({ switched: false })).toBeNull();
    const asked = decodeSwitchAnswer({ switched: false, plan: PLAN, session: sess("a1", { harness: "codex" }) })!;
    expect(asked.switched).toBe(false);
    // (A plan carries no session, whatever the wire says.)
    expect(asked.session).toBeNull();
    expect(decodeSwitchAnswer({ switched: "true", plan: PLAN })!.switched).toBe(false);
    const done = decodeSwitchAnswer({ switched: true, session: sess("a1", { harness: "codex" }), plan: PLAN, note: NOTE })!;
    expect(done.switched).toBe(true);
    expect(done.session!.tool).toBe("codex");
    expect(done.note).toBe(NOTE);
    // A switch whose row cannot be read: said as it is (the caller reads again).
    expect(decodeSwitchAnswer({ switched: true, session: "codex" })!.session).toBeNull();
    // The line the switch wrote rides the answer: only a real switch line,
    // and only with an answer that switched (extra keys are let through).
    const withLine = decodeSwitchAnswer({ switched: true, session: sess("a1", { harness: "codex" }), notice: NOTICE })!;
    expect(withLine.notice).toEqual({
      type: "notice",
      at: NOTICE.at,
      text: NOTE,
      switch: { from: "claude-code", to: "codex", resumes: false },
    });
    expect(done.notice).toBeNull();
    expect(decodeSwitchAnswer({ switched: true, notice: { type: "notice", at: "", text: "Hello." } })!.notice).toBeNull();
    expect(decodeSwitchAnswer({ switched: true, notice: { ...NOTICE, type: "error" } })!.notice).toBeNull();
    expect(decodeSwitchAnswer({ switched: false, plan: PLAN, notice: NOTICE })!.notice).toBeNull();
  });

  it("a refusal is a 4xx the daemon wrote; anything else says nothing about the switch", () => {
    expect(isSwitchRefusal({ status: 409, body: { detail: "No." } })).toBe(true);
    expect(isSwitchRefusal({ status: 400, body: { detail: "No.", field: "tool" } })).toBe(true);
    expect(isSwitchRefusal({ status: 409 })).toBe(false); // no body of its own
    expect(isSwitchRefusal({ status: 409, body: "No." })).toBe(false);
    expect(isSwitchRefusal({ status: 500, body: { detail: "internal error" } })).toBe(false);
    expect(isSwitchRefusal({ status: 0, body: { detail: "x" } })).toBe(false);
    expect(isSwitchRefusal({ status: 399, body: { detail: "x" } })).toBe(false);
    expect(isSwitchRefusal(new Error("Failed to fetch"))).toBe(false);
    expect(isSwitchRefusal(null)).toBe(false);
  });

  it("what waits right after a switch is read off the plan", () => {
    expect(pendingFromPlan(decodeSwitchPlan(PLAN))).toEqual({
      to: "codex",
      toLabel: "Codex",
      from: "claude-code",
      fromLabel: "Claude Code",
      resumes: false,
    });
    expect(pendingFromPlan(decodeSwitchPlan({ ...PLAN, resumes: true }))!.resumes).toBe(true);
    // Nothing to hand over: nothing waits.
    expect(pendingFromPlan(decodeSwitchPlan({ ...PLAN, summary: false }))).toBeNull();
    expect(pendingFromPlan(null)).toBeNull();
  });

  it("the request sends the model and the effort only when one was picked", () => {
    expect(switchBody({ tool: "codex" }, false)).toEqual({ tool: "codex", confirm: false });
    expect(switchBody({ tool: "codex", model: "", effort: "high" }, true)).toEqual({
      tool: "codex",
      model: "",
      effort: "high",
      confirm: true,
    });
  });

  it("the shown plan's token rides only the switch; a refusal says when the plan changed", () => {
    expect(switchBody({ tool: "codex" }, true, {}, "tok")).toEqual({ tool: "codex", confirm: true, expect: "tok" });
    expect(switchBody({ tool: "codex" }, false, {}, "tok")).toEqual({ tool: "codex", confirm: false });
    expect(switchBody({ tool: "codex" }, true, {}, "")).toEqual({ tool: "codex", confirm: true });
    expect(switchBody({ tool: "codex" }, true)).toEqual({ tool: "codex", confirm: true });
    expect(isChangedRefusal({ body: { detail: "x", code: "changed" } })).toBe(true);
    expect(isChangedRefusal({ body: { detail: "x", code: "busy" } })).toBe(false);
    expect(isChangedRefusal({ body: { detail: "changed" } })).toBe(false);
    expect(isChangedRefusal({ body: "changed" })).toBe(false);
    expect(isChangedRefusal(new Error("changed"))).toBe(false);
    expect(isChangedRefusal(null)).toBe(false);
  });

  it("the one rule for what a new session of a tool starts with (the start composer's too)", () => {
    const codex = decodeCodingTools(TOOLS).find((t) => t.id === "codex")!;
    const last = { model: "gpt-6-astra", effort: "high" };
    expect(toolStartWants(codex, null, last)).toEqual(last);
    expect(toolStartWants(codex, { harness: "claude-code", model: "claude-opus-5-5" }, last)).toEqual(last);
    // The effort is what is WANTED (the caller checks it against its model).
    expect(toolStartWants(codex, { harness: "codex", model: "gpt-6-mini", effort: "high" }, last)).toEqual({
      model: "gpt-6-mini",
      effort: "high",
    });
    expect(toolStartWants(codex, { harness: "codex", model: "gpt-gone" }, last)).toEqual({ model: "", effort: "" });
  });

  it("what the user would start with rides beside the choice, and is never sent empty", () => {
    expect(switchBody({ tool: "codex" }, false, { model: "gpt-6-astra", effort: "high" })).toEqual({
      tool: "codex",
      prefer_model: "gpt-6-astra",
      prefer_effort: "high",
      confirm: false,
    });
    // Beside an explicit pick too (the pick wins at the daemon).
    expect(switchBody({ tool: "codex", model: "gpt-6-mini" }, true, { model: "gpt-6-astra" })).toEqual({
      tool: "codex",
      model: "gpt-6-mini",
      prefer_model: "gpt-6-astra",
      confirm: true,
    });
    expect(switchBody({ tool: "codex" }, false, {})).toEqual({ tool: "codex", confirm: false });
    expect(switchBody({ tool: "codex" }, false, { model: "", effort: "" })).toEqual({ tool: "codex", confirm: false });
  });

  it("the preference is the start composer's rule: the folder's defaults for THIS tool, else its last pick", () => {
    const codex = decodeCodingTools(TOOLS).find((t) => t.id === "codex")!;
    const none = { model: "", effort: "" };
    const last = { model: "gpt-6-astra", effort: "high" };
    expect(switchPreference(null, null, last)).toEqual({});
    expect(switchPreference(codex, null, none)).toEqual({});
    expect(switchPreference(codex, null, last)).toEqual({ model: "gpt-6-astra", effort: "high" });
    // The folder's own choice for this tool beats the last pick.
    expect(switchPreference(codex, { harness: "codex", model: "gpt-6-mini", effort: "low" }, last)).toEqual({
      model: "gpt-6-mini",
      effort: "low",
    });
    // …whole: a folder default that names no model does not borrow the last pick's.
    expect(switchPreference(codex, { harness: "codex" }, last)).toEqual({});
    // A folder default for ANOTHER tool says nothing about this one.
    expect(switchPreference(codex, { harness: "claude-code", model: "claude-opus-5-5", effort: "low" }, last)).toEqual({
      model: "gpt-6-astra",
      effort: "high",
    });
    // A model the tool no longer lists, an effort the model does not take: left out.
    expect(switchPreference(codex, { harness: "codex", model: "gpt-gone", effort: "low" }, last)).toEqual({});
    expect(switchPreference(codex, { harness: "codex", model: "gpt-6-mini", effort: "high" }, last)).toEqual({
      model: "gpt-6-mini",
    });
    expect(switchPreference(codex, null, { model: "gpt-6-mini", effort: "high" })).toEqual({ model: "gpt-6-mini" });
  });

  it("the view says whether the session can switch, and whether a summary waits", () => {
    const session = sess("a1");
    expect(decodeCodeSessionView({ session, events: [], switches_tool: true })!.switchesTool).toBe(true);
    expect(decodeCodeSessionView({ session, events: [] })!.switchesTool).toBe(false); // an older daemon
    expect(decodeCodeSessionView({ session, events: [], switches_tool: false })!.switchesTool).toBe(false);
    expect(decodeCodeSessionView({ session, events: [], switches_tool: "yes" })!.switchesTool).toBe(false);
    // …and whether it EVER switched (strictly true).
    expect(decodeCodeSessionView({ session, events: [], switched_tools: true })!.switchedTools).toBe(true);
    expect(decodeCodeSessionView({ session, events: [] })!.switchedTools).toBe(false);
    expect(decodeCodeSessionView({ session, events: [], switched_tools: 1 })!.switchedTools).toBe(false);
    expect(decodeCodeSessionView({ session, events: [], switched_tools: "true" })!.switchedTools).toBe(false);
    expect(decodeHandoffPending({ session })).toBeNull();
    expect(decodeHandoffPending({ handoff_pending: "codex" })).toBeNull();
    expect(decodeHandoffPending({ handoff_pending: { to: "", to_label: "Codex" } })).toBeNull();
    expect(
      decodeHandoffPending({
        handoff_pending: { to: "codex", to_label: "Codex", from: "claude-code", from_label: "Claude Code", resumes: true },
      }),
    ).toEqual({ to: "codex", toLabel: "Codex", from: "claude-code", fromLabel: "Claude Code", resumes: true });
    // No label: the tool's own name; `resumes` only when the daemon said true.
    expect(decodeHandoffPending({ handoff_pending: { to: "pi", resumes: 1 } })).toEqual({
      to: "pi",
      toLabel: "Pi",
      from: "",
      fromLabel: "",
      resumes: false,
    });
  });

  it("the events: a message's tool, the switch line's mark, the summary row", () => {
    expect(ev({ type: "user", at: "", text: "hi", files: [], tool: "codex" })).toMatchObject({ tool: "codex" });
    expect("tool" in ev({ type: "user", at: "", text: "hi", files: [] })).toBe(false);
    expect("tool" in ev({ type: "user", at: "", text: "hi", files: [], tool: 7 })).toBe(false);
    const line = ev({ type: "notice", at: "", text: NOTE, switch: { from: "claude-code", to: "codex", resumes: true } });
    expect(line).toMatchObject({ text: NOTE, switch: { from: "claude-code", to: "codex", resumes: true } });
    // A mark that does not say where it went is no mark: a plain line.
    expect("switch" in ev({ type: "notice", at: "", text: NOTE, switch: { from: "claude-code" } })).toBe(false);
    expect("switch" in ev({ type: "notice", at: "", text: NOTE, switch: "codex" })).toBe(false);
    expect("switch" in ev({ type: "notice", at: "", text: NOTE })).toBe(false);

    const h = ev({ type: "handoff", at: "x", turn: "t9", to: "codex", from: "claude-code", n: 2, resumes: true, text: "T", note: "N" });
    expect(h).toEqual({ type: "handoff", at: "x", turn: "t9", to: "codex", from: "claude-code", n: 2, resumes: true, text: "T", note: "N" });
    expect(decodeCodeEvent({ type: "handoff", at: "", to: "", text: "T" })).toBeNull();
    expect(decodeCodeEvent({ type: "handoff", at: "", to: "codex", text: "" })).toBeNull();
    expect(decodeCodeEvent({ type: "handoff", at: "", to: "codex", text: "   " })).toBeNull();
    expect(decodeCodeEvent({ type: "handoff", at: "", to: "codex", text: ["T"] })).toBeNull();
    // Bounded at 20,000 characters; `resumes` only when the daemon said true.
    const big = ev({ type: "handoff", at: "", to: "codex", text: "y".repeat(HANDOFF_TEXT_MAX + 500), resumes: "yes" });
    expect(HANDOFF_TEXT_MAX).toBe(20_000);
    expect(big.type === "handoff" && big.text.length).toBe(HANDOFF_TEXT_MAX);
    expect(big.type === "handoff" && big.resumes).toBe(false);
    // The frame's name stands in for a missing type (the stream).
    expect(decodeCodeEvent({ at: "", to: "codex", text: "T" }, "handoff")).toMatchObject({ type: "handoff", to: "codex" });
  });
});

// ── which tool wrote a turn ─────────────────────────────────────────────────

describe("each turn knows the coding tool that wrote it (the five steps)", () => {
  it("1. the message's own `tool` wins, whatever the turn says later", () => {
    const items = fold(turn(1, { tool: "codex", session: "claude-code" }));
    expect(userTools(items)).toEqual(["codex"]);
    expect(receipts(items)).toEqual(["codex"]);
  });

  it("2. else the FIRST `session` event of the turn names it, for every row of the turn", () => {
    const raws = turn(1, { session: "pi" });
    // A note sent before the tool said who it is, and a second `session` event.
    raws.splice(1, 0, { type: "steer", at: "2026-11-19T10:01:00Z", id: "n1", state: "sent", text: "also this", files: [], how: "" });
    raws.splice(3, 0, { type: "session", at: "2026-11-19T10:01:01Z", harness: "grok", harness_session_id: "h2", model: "" });
    const items = fold(raws);
    expect(userTools(items)).toEqual(["pi"]);
    expect(receipts(items)).toEqual(["pi"]);
    expect(items.find((it) => it.kind === "steer")).toMatchObject({ tool: "pi" });
  });

  it("3. else the tool current at that point: the last one a message, a `session` event or a switch line named", () => {
    // After a message that named its tool.
    expect(receipts(fold([...turn(1, { tool: "claude-code" }), ...turn(2)]))).toEqual(["claude-code", "claude-code"]);
    // After a `session` event of an earlier turn.
    expect(receipts(fold([...turn(1, { session: "pi" }), ...turn(2)]))).toEqual(["pi", "pi"]);
    // After a switch line: the tool it went TO.
    expect(
      receipts(fold([...turn(1, { tool: "claude-code" }), switched(1, "claude-code", "codex"), ...turn(2)])),
    ).toEqual(["claude-code", "codex"]);
  });

  it("4. else turns before the FIRST switch line take that line's `from`", () => {
    const items = fold([
      ...turn(1),
      ...turn(2),
      switched(2, "claude-code", "codex"),
      ...turn(3),
      switched(3, "codex", "pi"),
      ...turn(4),
    ]);
    expect(receipts(items)).toEqual(["claude-code", "claude-code", "codex", "pi"]);
    expect(userTools(items)).toEqual(["claude-code", "claude-code", "codex", "pi"]);
    // Only what the record had NOT named: a turn that said its tool keeps it.
    expect(receipts(fold([...turn(1, { tool: "grok" }), switched(1, "claude-code", "codex")]))).toEqual(["grok"]);
    // A later switch line names nothing before it.
    expect(
      receipts(fold([...turn(1), switched(1, "", "codex"), ...turn(2), switched(2, "claude-code", "pi")])),
    ).toEqual([undefined, "codex"]);
  });

  it("5. with nothing said at all the rows carry no tool (the screen uses the session's, as before)", () => {
    const items = fold([...turn(1), ...turn(2)]);
    expect(receipts(items)).toEqual([undefined, undefined]);
    expect(userTools(items)).toEqual([undefined, undefined]);
    expect(severalToolsWrote(items, "codex")).toBe(false);
  });

  it("a reply of the new tool is never named for the old tool's model", () => {
    const items = fold([
      ...turn(1, { tool: "claude-code", session: "claude-code", model: "claude-opus-5-5" }),
      switched(1, "claude-code", "codex"),
      ...turn(2, { tool: "codex" }),
    ]);
    const done = items.filter((it): it is Extract<ConvItem, { kind: "done" }> => it.kind === "done");
    expect(done[0].receipt!.model).toBe("claude-opus-5-5");
    expect(done[1].receipt!.model).toBe("");
    // The same when the switch line itself is not in what was read.
    const cut = fold([
      ...turn(1, { tool: "claude-code", session: "claude-code", model: "claude-opus-5-5" }),
      ...turn(2, { tool: "codex" }),
      ...turn(3, { tool: "codex", session: "codex", model: "gpt-6-astra" }),
      ...turn(4, { tool: "codex" }),
    ]).filter((it): it is Extract<ConvItem, { kind: "done" }> => it.kind === "done");
    expect(cut.map((d) => d.receipt!.model)).toEqual(["claude-opus-5-5", "", "gpt-6-astra", "gpt-6-astra"]);
  });

  it("folding event by event gives the same rows as folding whole (the names, the summary's place)", () => {
    const raws = [
      ...turn(1),
      switched(1, "claude-code", "codex"),
      { type: "user", at: "2026-11-19T10:02:00Z", text: "carry on", files: [], turn: "t2", tool: "codex" },
      { type: "thinking", at: "2026-11-19T10:02:01Z", item: "k", delta: "hm", turn: "t2" },
      { type: "text", at: "2026-11-19T10:02:02Z", item: "b", delta: "par", turn: "t2" },
      { type: "tool", at: "2026-11-19T10:02:02Z", item: "x", kind: "command", name: "sh", title: "ls", input: "ls", status: "running", turn: "t2" },
      { type: "handoff", at: "2026-11-19T10:02:03Z", turn: "t2", to: "codex", from: "claude-code", n: 1, resumes: false, text: "SUMMARY" },
      { type: "text", at: "2026-11-19T10:02:04Z", item: "b", delta: "tial", turn: "t2" },
      { type: "tool", at: "2026-11-19T10:02:05Z", item: "x", kind: "command", name: "sh", title: "ls", input: "ls", status: "done", output: "a", turn: "t2" },
      { type: "text_done", at: "2026-11-19T10:02:06Z", item: "b", text: "partial", turn: "t2" },
      { type: "usage", at: "2026-11-19T10:02:07Z", input: 1, output: 2 },
      { type: "done", at: "2026-11-19T10:02:08Z", stop: "completed", turn: "t2" },
    ].map(ev);
    let memo: FoldMemo | null = null;
    for (let n = 1; n <= raws.length; n += 1) {
      const step = foldConversationFrom(memo, raws.slice(0, n));
      memo = step.memo;
      expect(step.conversation.items).toEqual(foldConversation(raws.slice(0, n)).items);
    }
    // The summary sits right under the message it rode, though it came later;
    // the rows it moved are still updated in place.
    const kinds = foldConversation(raws).items.map((it) => it.kind);
    expect(kinds.slice(kinds.lastIndexOf("user"))).toEqual(["user", "handoff", "thinking", "text", "tool", "usage", "done"]);
    const items = foldConversation(raws).items;
    expect(items.find((it) => it.kind === "text" && it.text === "partial")).toBeTruthy();
    expect(items.find((it) => it.kind === "tool" && it.tool.status === "done")).toBeTruthy();
    expect(items.filter((it) => it.kind === "tool")).toHaveLength(1);
  });

  it("every row the summary moved is still updated in place", () => {
    const at = (s: number) => `2026-11-19T10:02:${String(s).padStart(2, "0")}Z`;
    const items = fold([
      { type: "user", at: at(0), text: "carry on", files: [], turn: "t2", tool: "codex" },
      { type: "thinking", at: at(1), item: "k", delta: "hm" },
      { type: "plan", at: at(2), steps: [{ text: "one", status: "pending" }] },
      { type: "steer", at: at(3), id: "n1", state: "sent", text: "also this", files: [], how: "" },
      { type: "text", at: at(4), item: "b", delta: "par" },
      { type: "tool", at: at(5), item: "x", kind: "command", name: "sh", title: "ls", input: "ls", status: "running" },
      { type: "usage", at: at(6), input: 11, output: 22 },
      // The summary's own line arrives only now, and goes up under the message.
      { type: "handoff", at: at(7), turn: "t2", to: "codex", from: "claude-code", n: 1, text: "SUMMARY" },
      { type: "thinking", at: at(8), item: "k", delta: "m more" },
      { type: "plan", at: at(9), steps: [{ text: "one", status: "completed" }] },
      { type: "steer", at: at(10), id: "n1", state: "read", text: "", files: [], how: "" },
      { type: "text_done", at: at(11), item: "b", text: "partial" },
      { type: "tool", at: at(12), item: "x", kind: "command", name: "sh", title: "ls", input: "ls", status: "done" },
      { type: "done", at: at(13), stop: "completed", turn: "t2" },
    ]);
    expect(items.map((it) => it.kind)).toEqual(["user", "handoff", "thinking", "plan", "steer", "text", "tool", "usage", "done"]);
    expect(items[2]).toMatchObject({ kind: "thinking", text: "hmm more" });
    expect(items[3]).toMatchObject({ kind: "plan", steps: [{ text: "one", status: "completed" }] });
    expect(items[4]).toMatchObject({ kind: "steer", state: "read", text: "also this" });
    expect(items[5]).toMatchObject({ kind: "text", text: "partial", done: true });
    expect(items[6]).toMatchObject({ kind: "tool", tool: { status: "done" } });
    // The turn's usage line folds into its receipt, as always.
    expect(items[7]).toMatchObject({ kind: "usage", input: 11, output: 22, folded: true });
    expect(items[8]).toMatchObject({ kind: "done", receipt: { usage: { input: 11, output: 22 }, tool: "codex" } });
  });

  it("a reply's end and a note with NO message before them take the tool current then", () => {
    // (What was read starts in the middle of a turn: its message is older.)
    const items = fold([
      { type: "session", at: "2026-11-19T10:00:00Z", harness: "pi", harness_session_id: "h", model: "" },
      { type: "steer", at: "2026-11-19T10:00:01Z", id: "n1", state: "read", text: "also this", files: [], how: "" },
      { type: "text_done", at: "2026-11-19T10:00:02Z", item: "a", text: "done" },
      { type: "usage", at: "2026-11-19T10:00:03Z", input: 1, output: 2 },
      { type: "done", at: "2026-11-19T10:00:04Z", stop: "completed" },
    ]);
    expect(items.find((it) => it.kind === "steer")).toMatchObject({ tool: "pi" });
    expect(receipts(items)).toEqual(["pi"]);
    // …and none at all when nothing was said.
    const bare = fold([
      { type: "steer", at: "2026-11-19T10:00:01Z", id: "n1", state: "read", text: "also this", files: [], how: "" },
      { type: "usage", at: "2026-11-19T10:00:03Z", input: 1, output: 2 },
      { type: "done", at: "2026-11-19T10:00:04Z", stop: "completed" },
    ]);
    expect("tool" in bare.find((it) => it.kind === "steer")!).toBe(false);
    expect(receipts(bare)).toEqual([undefined]);
  });

  it("a switch line inside an open turn nobody named gives that turn the line's `from`", () => {
    const raws = turn(1);
    raws.splice(2, 0, switched(1, "claude-code", "codex"));
    const items = fold(raws);
    expect(userTools(items)).toEqual(["claude-code"]);
    expect(receipts(items)).toEqual(["claude-code"]);
  });

  it("the summary goes under a message that names no turn (the one drawn when it was sent)", () => {
    const items = fold([
      ...turn(1, { tool: "claude-code" }),
      switched(1, "claude-code", "codex"),
      // The message as this window drew it when it was sent: no `turn`, no `tool`.
      { type: "user", at: "2026-11-19T10:02:00Z", text: "carry on", files: [] },
      { type: "text", at: "2026-11-19T10:02:01Z", item: "b", delta: "ok" },
      { type: "handoff", at: "2026-11-19T10:02:02Z", turn: "t2", to: "codex", from: "claude-code", n: 1, text: "SUMMARY" },
    ]);
    const kinds = items.map((it) => it.kind);
    expect(kinds.slice(kinds.lastIndexOf("user"))).toEqual(["user", "handoff", "text"]);
    // With no turn open and no turn to match, it stays where it came.
    const closed = fold([
      { type: "user", at: "2026-11-19T10:02:00Z", text: "carry on", files: [] },
      { type: "text_done", at: "2026-11-19T10:02:01Z", item: "b", text: "ok" },
      { type: "done", at: "2026-11-19T10:02:02Z", stop: "completed" },
      { type: "handoff", at: "2026-11-19T10:02:03Z", turn: "t2", to: "codex", from: "claude-code", n: 1, text: "SUMMARY" },
    ]);
    expect(closed.map((it) => it.kind)).toEqual(["user", "text", "done", "handoff"]);
  });

  it("a summary for a message that is not on screen is drawn where it came", () => {
    const items = fold([
      ...turn(1, { tool: "codex" }),
      { type: "handoff", at: "2026-11-19T10:05:00Z", turn: "t9", to: "codex", from: "claude-code", n: 1, text: "S" },
    ]);
    expect(items.map((it) => it.kind)).toEqual(["user", "text", "done", "handoff"]);
  });
});

// ── what each reply says ────────────────────────────────────────────────────

describe("each reply keeps the tool that wrote it", () => {
  const twoTools = () =>
    fold([
      ...turn(1, { tool: "claude-code", session: "claude-code", model: "claude-opus-5-5" }),
      switched(1, "claude-code", "codex"),
      ...turn(2, { tool: "codex" }),
    ]);

  it("after a switch the earlier receipt still names the old tool; the newer one names the new", () => {
    render(<CodeConversation items={twoTools()} folder="C:\\work" tool="codex" model="gpt-6-astra" account="Work" />);
    const lines = screen.getAllByTestId("code-receipt-line").map((l) => l.textContent ?? "");
    expect(lines).toHaveLength(2);
    expect(lines[0]).toContain("in Claude Code");
    expect(lines[0]).not.toContain("Codex");
    // The session's model and account are its CURRENT tool's: never on the old reply.
    expect(lines[0]).not.toContain("Work");
    expect(lines[0]).not.toMatch(/gpt/i);
    expect(lines[1]).toContain("in Codex");
    expect(lines[1]).toContain("on Work");
    expect(lines[1]).not.toContain("Claude Code");
    expect(lines[1]).not.toMatch(/opus/i);
    const tools = screen.getAllByTestId("code-receipt").map((r) => r.getAttribute("data-tool"));
    expect(tools).toEqual(["claude-code", "codex"]);
  });

  it("an earlier reply that recorded no model is not given the session's current one", () => {
    const items = fold([...turn(1, { tool: "claude-code" }), switched(1, "claude-code", "codex"), ...turn(2, { tool: "codex" })]);
    render(<CodeConversation items={items} folder="C:\\work" tool="codex" model="gpt-6-astra" account="Work" />);
    const lines = screen.getAllByTestId("code-receipt-line").map((l) => l.textContent ?? "");
    expect(lines[0]).toMatch(/^answered by Claude Code · /);
    expect(lines[0]).not.toMatch(/gpt|astra/i);
    // The new tool's reply takes the session's model, as a reply always did.
    expect(lines[1]).toMatch(/^answered by .*astra.* in Codex on Work · /i);
  });

  it("where it ran is the turn's own tool's too", () => {
    const items = fold([...turn(1, { tool: "pi" }), switched(1, "pi", "codex"), ...turn(2, { tool: "codex" })]);
    render(<CodeConversation items={items} folder="C:\\work" tool="codex" />);
    const rows = screen.getAllByTestId("code-receipt");
    // Pi picks its own provider (nothing said); Codex answers on its service.
    expect(within(rows[0]).queryByTestId("code-receipt-where")).toBeNull();
    expect(within(rows[1]).getByTestId("code-receipt-where").textContent).toBe("· cloud");
  });

  it("a note sent while the old tool worked still names the old tool", () => {
    const raws = turn(1, { tool: "claude-code" });
    raws.splice(1, 0, { type: "steer", at: "2026-11-19T10:01:01Z", id: "n1", state: "read", text: "also this", files: [], how: "" });
    const items = fold([...raws, switched(1, "claude-code", "codex"), ...turn(2, { tool: "codex" })]);
    render(<CodeConversation items={items} folder="C:\\work" tool="codex" />);
    expect(screen.getByTestId("code-steer-state").textContent).toContain("Claude Code");
    expect(screen.getByTestId("code-steer-state").textContent).not.toContain("Codex");
  });

  it("a conversation one tool wrote reads exactly as it always did", () => {
    const items = fold([...turn(1, { tool: "codex", session: "codex", model: "gpt-6-astra" }), ...turn(2, { tool: "codex" })]);
    expect(severalToolsWrote(items, "codex")).toBe(false);
    render(<CodeConversation items={items} folder="C:\\work" tool="codex" model="gpt-6-astra" account="Work" />);
    for (const l of screen.getAllByTestId("code-receipt-line")) {
      expect(l.textContent).toMatch(/^answered by .+ on Work · /);
      expect(l.textContent).not.toContain(" in ");
    }
    // The line's own words, with and without a tool's name.
    const r = { model: "Opus 5.5", seconds: null, actions: 0, failed: 0, files: 0, usage: null };
    expect(receiptLine(r, "Work")).toBe("answered by Opus 5.5 on Work");
    expect(receiptLine(r, "Work", "Claude Code")).toBe("answered by Opus 5.5 in Claude Code on Work");
    expect(receiptLine({ ...r, model: "" }, "", "Claude Code")).toBe("answered by Claude Code");
  });

  it("the export heads each reply with the tool that wrote it", () => {
    const raws = [
      ...turn(1, { tool: "claude-code" }),
      switched(1, "claude-code", "codex"),
      ...turn(2, { tool: "codex" }),
    ].map(ev);
    const name = (id: string) => (id === "claude-code" ? "Claude Code" : id === "codex" ? "Codex" : id);
    const md = conversationMarkdown("T", raws, "Codex", [], name);
    expect(md).toContain("## Claude Code\n\nreply 1");
    expect(md).toContain("## Codex\n\nreply 2");
    // Without the names it is the session's tool throughout, as before.
    expect(conversationMarkdown("T", raws, "Codex")).not.toContain("## Claude Code");
  });

  it("a switch after a failure ends the failure's presses (they act on the tool that failed)", () => {
    const limit = { type: "error", at: "2026-11-19T10:01:05Z", title: "The account hit its limit.", fix: "", technical: "", fix_kind: "account_limit" };
    const before = fold([...turn(1, { tool: "claude-code" }).slice(0, 2), limit]);
    const onContinueOn = vi.fn();
    const { unmount } = render(
      <CodeConversation items={before} folder="C:\\work" tool="claude-code" onContinueOn={onContinueOn} />,
    );
    expect(screen.getByTestId("code-continue-on")).toBeTruthy();
    unmount();
    const after = fold([...turn(1, { tool: "claude-code" }).slice(0, 2), limit, switched(1, "claude-code", "codex")]);
    render(<CodeConversation items={after} folder="C:\\work" tool="codex" onContinueOn={onContinueOn} />);
    expect(screen.getByTestId("code-error")).toBeTruthy();
    expect(screen.queryByTestId("code-continue-on")).toBeNull();
  });
});

// ── the summary row ─────────────────────────────────────────────────────────

describe("the summary the new tool was given", () => {
  const TEXT = "Iron Jarvis: this is a summary.\n\n> first one\n\n| <b>bold</b> and **x** and [a](http://x)\n| # not a heading";

  function drawn(over: Record<string, unknown> = {}) {
    const raws = turn(2, { tool: "codex" });
    raws.splice(1, 0, { type: "handoff", at: "2026-11-19T10:02:01Z", turn: "t2", to: "codex", from: "claude-code", n: 1, resumes: false, text: TEXT, ...over });
    return fold([...turn(1, { tool: "claude-code" }), switched(1, "claude-code", "codex"), ...raws]);
  }

  it("is closed by default, under the message it rode", () => {
    render(<CodeConversation items={drawn()} folder="C:\\work" tool="codex" />);
    const row = screen.getByTestId("code-handoff");
    expect(row.getAttribute("data-open")).toBe("false");
    expect(screen.getByTestId("code-handoff-toggle").getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByTestId("code-handoff-toggle").textContent).toBe("Summary given to Codex");
    expect(screen.queryByTestId("code-handoff-text")).toBeNull();
    expect(screen.queryByTestId("code-handoff-copy")).toBeNull();
    expect(document.body.textContent).not.toContain("this is a summary");
    // After the second message's bubble, before that turn's reply.
    const order = Array.from(
      screen.getByTestId("code-conversation").querySelectorAll('[data-testid="code-user"],[data-testid="code-handoff"],[data-testid="code-text"]'),
    ).map((n) => n.getAttribute("data-testid"));
    expect(order).toEqual(["code-user", "code-text", "code-user", "code-handoff", "code-text"]);
  });

  it("one press opens the EXACT text, as typed: never Markdown, never HTML", () => {
    render(<CodeConversation items={drawn()} folder="C:\\work" tool="codex" />);
    fireEvent.click(screen.getByTestId("code-handoff-toggle"));
    const text = screen.getByTestId("code-handoff-text");
    expect(text.tagName).toBe("PRE");
    expect(text.textContent).toBe(TEXT);
    expect(text.textContent).toContain("<b>bold</b>");
    expect(text.textContent).toContain("**x**");
    // One text node: nothing in it was parsed into an element.
    expect(text.children).toHaveLength(0);
    expect(text.querySelector("b, strong, a, h1, em, code")).toBeNull();
    expect(text.className).toContain("whitespace-pre-wrap");
    expect(text.className).toContain("select-text");
    expect(screen.getByTestId("code-handoff-toggle").getAttribute("aria-expanded")).toBe("true");
    // Pressed again it folds.
    fireEvent.click(screen.getByTestId("code-handoff-toggle"));
    expect(screen.queryByTestId("code-handoff-text")).toBeNull();
  });

  it("Copy copies that same text", async () => {
    render(<CodeConversation items={drawn()} folder="C:\\work" tool="codex" />);
    fireEvent.click(screen.getByTestId("code-handoff-toggle"));
    fireEvent.click(screen.getByTestId("code-handoff-copy"));
    await waitFor(() => expect(screen.getByTestId("code-handoff-copied").textContent).toBe("Copied"));
    expect(H.copied).toEqual([TEXT]);
  });

  it("a switch back says it is what happened since; the daemon's note shows under the text", () => {
    expect(handoffRowWords("Codex", false)).toBe("Summary given to Codex");
    expect(handoffRowWords("Claude Code", true)).toBe("Summary of what happened since, given to Claude Code");
    const NOTE_WORDS = "(Iron Jarvis could not save a copy of this hand-off: the disk is full)";
    render(<HandoffRow toLabel="Claude Code" resumes text={TEXT} note={NOTE_WORDS} />);
    expect(screen.getByTestId("code-handoff-toggle").textContent).toBe("Summary of what happened since, given to Claude Code");
    expect(screen.queryByTestId("code-handoff-note")).toBeNull();
    fireEvent.click(screen.getByTestId("code-handoff-toggle"));
    expect(screen.getByTestId("code-handoff-note").textContent).toBe(NOTE_WORDS);
    cleanup();
    render(<HandoffRow toLabel="Codex" resumes={false} text={TEXT} note="" />);
    fireEvent.click(screen.getByTestId("code-handoff-toggle"));
    expect(screen.queryByTestId("code-handoff-note")).toBeNull();
  });
});

// ── the waiting line ────────────────────────────────────────────────────────

describe("a summary waits for the next message", () => {
  const pending = (resumes: boolean) => ({
    to: "codex",
    to_label: "Codex",
    from: "claude-code",
    from_label: "Claude Code",
    resumes,
  });

  it("says so in one quiet line above the box, both ways", async () => {
    expect(handoffWaitingWords("Codex", false)).toBe("Codex gets a summary of this conversation with your next message.");
    expect(handoffWaitingWords("Codex", true)).toBe("Codex gets a summary of what happened since with your next message.");
    openView("a1", { handoff_pending: pending(false) });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const line = await screen.findByTestId("code-handoff-waiting", {}, { timeout: 10_000 });
    expect(line.textContent).toBe("Codex gets a summary of this conversation with your next message.");
    expect(line.getAttribute("role")).toBe("status");
    // Above the message box.
    expect(line.compareDocumentPosition(screen.getByTestId("code-box")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    cleanup();
    openView("a1", { handoff_pending: pending(true) });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    expect((await screen.findByTestId("code-handoff-waiting", {}, { timeout: 10_000 })).textContent).toBe(
      "Codex gets a summary of what happened since with your next message.",
    );
  });

  it("is not there when the view has no such field, and not while a reply runs", async () => {
    openView("a1");
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-model-chip", {}, { timeout: 10_000 });
    expect(screen.queryByTestId("code-handoff-waiting")).toBeNull();
    cleanup();
    H.hold = true;
    openView("a1", { handoff_pending: pending(false), running: "t-live" });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-stop", {}, { timeout: 10_000 });
    expect(screen.queryByTestId("code-handoff-waiting")).toBeNull();
    // The piece itself: nothing for nothing.
    cleanup();
    const { container } = render(<HandoffWaiting pending={null} />);
    expect(container.textContent).toBe("");
  });
});

// ── the picker in the Code view ─────────────────────────────────────────────

describe("the session's picker", () => {
  it("lists the coding tools only when the view says the session can switch", async () => {
    openView("a1");
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    expect(screen.getByTestId("code-pick-tools")).toBeTruthy();
    expect(screen.getByTestId("code-pick-tool-codex")).toBeTruthy();
    expect(screen.getByTestId("code-pick-tool-claude-code").getAttribute("aria-checked")).toBe("true");
    cleanup();
    // An older Iron Jarvis says nothing; a helper session says no.
    for (const flag of [undefined, false, "yes"]) {
      openView("a1", { switches_tool: flag });
      render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
      await openPicker();
      expect(screen.getByTestId("code-pick-tool-menu")).toBeTruthy();
      expect(screen.queryByTestId("code-pick-tools")).toBeNull();
      expect(screen.queryByTestId("code-pick-tool-codex")).toBeNull();
      cleanup();
    }
  });

  it("while a reply runs the tool stays fixed", async () => {
    H.hold = true;
    openView("a1", { running: "t-live" });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-stop", {}, { timeout: 10_000 });
    fireEvent.click(screen.getByTestId("code-model-chip"));
    expect(screen.getByTestId("code-pick-tool-menu")).toBeTruthy();
    expect(screen.queryByTestId("code-pick-tools")).toBeNull();
  });

  it("the first press asks (confirm: false), shows the daemon's sentences, and PATCHes nothing", async () => {
    openView("a1");
    answersSwitch();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    await waitFor(() => expect(screen.getAllByTestId("code-switch-line").map((l) => l.textContent)).toEqual(LINES));
    expect(switchPosts()).toEqual([{ path: SWITCH, body: { tool: "codex", confirm: false } }]);
    expect(H.patches).toEqual([]);
    expect(screen.getByTestId("code-switch-heading").textContent).toBe(switchHeading("Codex"));
    expect(screen.getByTestId("code-switch-heading").textContent).toBe("Switch to Codex?");
    expect(screen.getByTestId("code-switch-confirm").textContent).toBe(switchPress("Codex"));
    expect(screen.getByTestId("code-switch-confirm").textContent).toBe("Switch to Codex");
    expect(screen.getByTestId("code-switch-cancel").textContent).toBe(SWITCH_CANCEL);
    // The confirmation has the cursor (and keeps it: the picker does not
    // take it back a frame later); it sits in the picker's own panel.
    await act(async () => new Promise<void>((r) => requestAnimationFrame(() => r())));
    expect(document.activeElement).toBe(screen.getByTestId("code-switch-ask"));
    expect(screen.getByTestId("code-pick-tool-menu").contains(screen.getByTestId("code-switch-ask"))).toBe(true);
    // Nothing changed: the session is still Claude Code's.
    expect(chipTool()).toBe("claude-code");
    // The tool asked about shows its own models, its default first, chosen.
    expect(screen.getByTestId("code-pick-model-default").getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("code-pick-model-gpt-6-astra")).toBeTruthy();
    expect(screen.queryByTestId("code-pick-model-claude-opus-5-5")).toBeNull();
  });

  it("Cancel sends nothing more, closes the question and gives the cursor back to the picker", async () => {
    openView("a1");
    answersSwitch();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const chip = await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    await screen.findByTestId("code-switch-lines", {}, { timeout: 10_000 });
    fireEvent.click(screen.getByTestId("code-switch-cancel"));
    expect(screen.queryByTestId("code-switch-ask")).toBeNull();
    expect(screen.queryByTestId("code-pick-tool-menu")).toBeNull();
    expect(document.activeElement).toBe(chip);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(switchPosts()).toHaveLength(1);
    expect(H.patches).toEqual([]);
    expect(chipTool()).toBe("claude-code");
    // Opened again there is no question left over.
    fireEvent.click(chip);
    expect(screen.queryByTestId("code-switch-ask")).toBeNull();
    expect(screen.getByTestId("code-pick-model-claude-opus-5-5")).toBeTruthy();
  });

  it("Esc cancels too; Tab stays on the two presses instead of closing the panel", async () => {
    openView("a1");
    answersSwitch();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const chip = await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    await screen.findByTestId("code-switch-lines", {}, { timeout: 10_000 });
    const ask = screen.getByTestId("code-switch-ask");
    fireEvent.keyDown(ask, { key: "Tab" });
    expect(document.activeElement).toBe(screen.getByTestId("code-switch-cancel"));
    fireEvent.keyDown(document.activeElement!, { key: "Tab" });
    expect(document.activeElement).toBe(screen.getByTestId("code-switch-confirm"));
    fireEvent.keyDown(document.activeElement!, { key: "Tab" });
    expect(document.activeElement).toBe(screen.getByTestId("code-switch-cancel"));
    expect(screen.getByTestId("code-pick-tool-menu")).toBeTruthy();
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(screen.queryByTestId("code-pick-tool-menu")).toBeNull();
    expect(document.activeElement).toBe(chip);
    fireEvent.click(chip);
    expect(screen.queryByTestId("code-switch-ask")).toBeNull();
    expect(switchPosts()).toHaveLength(1);
  });

  it("the second press sends confirm: true with the same tool, and the session becomes the new tool's", async () => {
    openView("a1");
    answersSwitch();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    const go = await screen.findByTestId("code-switch-confirm", {}, { timeout: 10_000 });
    await waitFor(() => expect((go as HTMLButtonElement).disabled).toBe(false));
    // What the daemon's record holds after the switch.
    H.gets[VIEW("a1")] = {
      session: sess("a1", { harness: "codex", account: "acc-2" }),
      events: [...EVENTS, { type: "notice", at: "2026-11-19T10:05:00Z", text: NOTE, switch: { from: "claude-code", to: "codex", resumes: false } }],
      running: null,
      pending: [],
      switches_tool: true,
      handoff_pending: { to: "codex", to_label: "Codex", from: "claude-code", from_label: "Claude Code", resumes: false },
    };
    fireEvent.click(go);
    await waitFor(() => expect(chipTool()).toBe("codex"));
    expect(switchPosts()).toEqual([
      { path: SWITCH, body: { tool: "codex", confirm: false } },
      { path: SWITCH, body: { tool: "codex", confirm: true, expect: TOKEN } },
    ]);
    expect(H.patches).toEqual([]);
    // The conversation's own new line, and nothing more said about it.
    await waitFor(() => expect(screen.getByTestId("code-notice").textContent).toBe(NOTE));
    expect(screen.queryByTestId("code-info")).toBeNull();
    expect(screen.queryByTestId("code-note")).toBeNull();
    expect(screen.queryByTestId("code-pick-tool-menu")).toBeNull();
    expect(screen.queryByTestId("code-switch-ask")).toBeNull();
    expect(screen.getByTestId("code-box").getAttribute("aria-label")).toBe("Message Codex");
    // The summary now waits for the next message.
    expect((await screen.findByTestId("code-handoff-waiting")).textContent).toContain("Codex gets a summary");
    // The earlier reply is still Claude Code's.
    expect(screen.getByTestId("code-receipt").getAttribute("data-tool")).toBe("claude-code");
  });

  it("the answer's own row is what the session becomes, even when the read after it fails", async () => {
    const { ApiError } = (await import("@/lib/api")) as unknown as { ApiError: new (m: string, s: number) => Error };
    openView("a1");
    answersSwitch();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    const go = await screen.findByTestId("code-switch-confirm", {}, { timeout: 10_000 });
    await waitFor(() => expect((go as HTMLButtonElement).disabled).toBe(false));
    const reads = H.got.filter((p) => p === VIEW("a1")).length;
    H.gets[VIEW("a1")] = new ApiError("Iron Jarvis is not answering.", 0);
    fireEvent.click(go);
    await waitFor(() => expect(chipTool()).toBe("codex"));
    // …and the view WAS read again (that read is what brings the new line).
    await waitFor(() => expect(H.got.filter((p) => p === VIEW("a1")).length).toBe(reads + 1));
    expect(screen.queryByTestId("code-pick-tool-menu")).toBeNull();
  });

  it.each([
    ["a switch whose row cannot be read", { switched: true, session: "not a row" }],
    ["a plan where the switch should be", { switched: false, plan: PLAN, session: sess("a1", { harness: "codex" }) }],
  ])("an answer to the second press that is not a switch (%s) is said, and the session is read again", async (_name, wire) => {
    openView("a1");
    answersSwitch({ confirm: wire });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    const go = await screen.findByTestId("code-switch-confirm", {}, { timeout: 10_000 });
    await waitFor(() => expect((go as HTMLButtonElement).disabled).toBe(false));
    const reads = H.got.filter((p) => p === VIEW("a1")).length;
    // The read after it does not answer until we let it.
    let answerRead: (v: unknown) => void = () => {};
    const before = H.gets[VIEW("a1")];
    H.gets[VIEW("a1")] = new Promise((resolve) => (answerRead = resolve));
    fireEvent.click(go);
    await waitFor(() => expect(screen.getByTestId("code-note").textContent).toBe(SWITCH_ANSWER_UNREAD));
    await waitFor(() => expect(H.got.filter((p) => p === VIEW("a1")).length).toBe(reads + 1));
    // BEFORE the read answers nothing of the answer was applied: no row, no line.
    expect(chipTool()).toBe("claude-code");
    expect(screen.queryByTestId("code-notice")).toBeNull();
    expect(screen.queryByTestId("code-handoff-waiting")).toBeNull();
    // What the read says is what is shown: still Claude Code's.
    await act(async () => {
      answerRead(before);
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(chipTool()).toBe("claude-code");
  });

  it("a model and an effort picked under the other tool ride both presses", async () => {
    openView("a1");
    answersSwitch();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    await screen.findByTestId("code-switch-lines", {}, { timeout: 10_000 });
    fireEvent.click(screen.getByTestId("code-pick-model-gpt-6-astra"));
    // A new choice: its plan is read again before it can be confirmed.
    expect((screen.getByTestId("code-switch-confirm") as HTMLButtonElement).disabled).toBe(true);
    await waitFor(() => expect((screen.getByTestId("code-switch-confirm") as HTMLButtonElement).disabled).toBe(false));
    expect(screen.getByTestId("code-pick-model-gpt-6-astra").getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByTestId("code-pick-effort-high"));
    await waitFor(() => expect(switchPosts()).toHaveLength(3));
    await waitFor(() => expect((screen.getByTestId("code-switch-confirm") as HTMLButtonElement).disabled).toBe(false));
    expect(screen.getByTestId("code-pick-effort-high").getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByTestId("code-switch-confirm"));
    await waitFor(() => expect(switchPosts()).toHaveLength(4));
    expect(switchPosts().map((p) => p.body)).toEqual([
      { tool: "codex", confirm: false },
      { tool: "codex", model: "gpt-6-astra", confirm: false },
      { tool: "codex", model: "gpt-6-astra", effort: "high", confirm: false },
      { tool: "codex", model: "gpt-6-astra", effort: "high", confirm: true, expect: TOKEN },
    ]);
    // None of it was saved as a change to the session.
    expect(H.patches).toEqual([]);
  });

  it("what you would start the tool with rides BOTH presses, and the picker ticks what the plan says will run", async () => {
    lastPicked("codex", "gpt-6-astra", "high");
    openView("a1");
    // The daemon took the preferred model; it says another effort will run.
    const plan = { ...PLAN, model: "gpt-6-astra", effort: "low" };
    H.answers[SWITCH] = (body: Record<string, unknown>) =>
      body.confirm === true
        ? { switched: true, session: sess("a1", { harness: "codex" }), plan, note: NOTE }
        : { switched: false, plan };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    await screen.findByTestId("code-switch-lines", {}, { timeout: 10_000 });
    expect(switchPosts().map((p) => p.body)).toEqual([
      { tool: "codex", prefer_model: "gpt-6-astra", prefer_effort: "high", confirm: false },
    ]);
    // Ticked from the PLAN (what will run), not from what was preferred.
    expect(screen.getByTestId("code-pick-model-gpt-6-astra").getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("code-pick-model-default").getAttribute("aria-selected")).toBe("false");
    expect(screen.getByTestId("code-pick-effort-low").getAttribute("aria-checked")).toBe("true");
    expect(screen.getByTestId("code-pick-effort-high").getAttribute("aria-checked")).toBe("false");
    // The same tool pressed again is the same question: nothing is asked twice.
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    fireEvent.click(screen.getByTestId("code-switch-confirm"));
    await waitFor(() => expect(switchPosts()).toHaveLength(2));
    expect(switchPosts()[1].body).toEqual({
      tool: "codex",
      prefer_model: "gpt-6-astra",
      prefer_effort: "high",
      confirm: true,
      expect: TOKEN,
    });
    expect(H.patches).toEqual([]);
  });

  it("a model picked in the open picker wins, and the preference still rides beside it", async () => {
    lastPicked("codex", "gpt-6-astra", "high");
    openView("a1");
    answersSwitch();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    await screen.findByTestId("code-switch-lines", {}, { timeout: 10_000 });
    fireEvent.click(screen.getByTestId("code-pick-model-gpt-6-mini"));
    await waitFor(() => expect(switchPosts()).toHaveLength(2));
    await waitFor(() => expect((screen.getByTestId("code-switch-confirm") as HTMLButtonElement).disabled).toBe(false));
    expect(screen.getByTestId("code-pick-model-gpt-6-mini").getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByTestId("code-switch-confirm"));
    await waitFor(() => expect(switchPosts()).toHaveLength(3));
    const prefer = { prefer_model: "gpt-6-astra", prefer_effort: "high" };
    expect(switchPosts().map((p) => p.body)).toEqual([
      { tool: "codex", ...prefer, confirm: false },
      { tool: "codex", model: "gpt-6-mini", ...prefer, confirm: false },
      { tool: "codex", model: "gpt-6-mini", ...prefer, confirm: true, expect: TOKEN },
    ]);
    // Worked out once for the tool: the folder's defaults were read one time.
    expect(H.got.filter((g) => g === DEFAULTS)).toHaveLength(1);
  });

  it("the folder's defaults for that tool beat its last pick; another tool's say nothing", async () => {
    lastPicked("codex", "gpt-6-astra", "high");
    openView("a1");
    H.gets[DEFAULTS] = { defaults: { tool: { harness: "codex", model: "gpt-6-mini", effort: "low" } } };
    answersSwitch();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    await screen.findByTestId("code-switch-lines", {}, { timeout: 10_000 });
    expect(switchPosts().map((p) => p.body)).toEqual([
      { tool: "codex", prefer_model: "gpt-6-mini", prefer_effort: "low", confirm: false },
    ]);
    cleanup();
    resetFolderDefaultsCache();
    H.posts = [];
    openView("a1");
    H.gets[DEFAULTS] = { defaults: { tool: { harness: "claude-code", model: "claude-opus-5-5", effort: "low" } } };
    answersSwitch();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    await screen.findByTestId("code-switch-lines", {}, { timeout: 10_000 });
    expect(switchPosts().map((p) => p.body)).toEqual([
      { tool: "codex", prefer_model: "gpt-6-astra", prefer_effort: "high", confirm: false },
    ]);
  });

  it("with nothing known for the tool no preference is sent, on either press", async () => {
    // (A last pick for ANOTHER tool is not this tool's.)
    lastPicked("pi", "some-model", "high");
    lastPicked("claude-code", "claude-opus-5-5", "high");
    openView("a1");
    answersSwitch();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    const go = await screen.findByTestId("code-switch-confirm", {}, { timeout: 10_000 });
    await waitFor(() => expect((go as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(go);
    await waitFor(() => expect(switchPosts()).toHaveLength(2));
    expect(switchPosts().map((p) => p.body)).toEqual([
      { tool: "codex", confirm: false },
      { tool: "codex", confirm: true, expect: TOKEN },
    ]);
  });

  it("a plan with no token at all (an older Iron Jarvis) still switches, with no `expect`", async () => {
    const { token: _none, ...OLD_PLAN } = PLAN;
    openView("a1");
    H.answers[SWITCH] = (body: Record<string, unknown>) =>
      body.confirm === true
        ? { switched: true, session: sess("a1", { harness: "codex" }), plan: OLD_PLAN, note: NOTE }
        : { switched: false, plan: OLD_PLAN };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    const go = await screen.findByTestId("code-switch-confirm", {}, { timeout: 10_000 });
    await waitFor(() => expect((go as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(go);
    await waitFor(() => expect(switchPosts()).toHaveLength(2));
    expect(switchPosts().map((p) => p.body)).toEqual([
      { tool: "codex", confirm: false },
      { tool: "codex", confirm: true },
    ]);
  });

  it("a plan whose token cannot be sent back as it is is not a plan: nothing can be confirmed unguarded", async () => {
    openView("a1");
    H.answers[SWITCH] = { switched: false, plan: { ...PLAN, token: "a".repeat(65) } };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    const said = await screen.findByTestId("code-switch-refused", {}, { timeout: 10_000 });
    expect(said.textContent).toContain("could not be read");
    expect(screen.queryByTestId("code-switch-confirm")).toBeNull();
    expect(switchPosts()).toHaveLength(1);
  });

  it("a preference belongs to its tool: asking about another tool works it out again", async () => {
    lastPicked("codex", "gpt-6-astra", "high");
    openView("a1");
    H.answers[SWITCH] = (body: Record<string, unknown>) => ({
      switched: false,
      plan: { ...PLAN, tool: body.tool, label: body.tool === "pi" ? "Pi" : "Codex" },
    });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-pi"));
    await waitFor(() => expect(screen.getByTestId("code-switch-heading").textContent).toBe("Switch to Pi?"));
    await screen.findByTestId("code-switch-lines", {}, { timeout: 10_000 });
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    await waitFor(() => expect(switchPosts()).toHaveLength(2));
    expect(switchPosts().map((p) => p.body)).toEqual([
      { tool: "pi", confirm: false },
      { tool: "codex", prefer_model: "gpt-6-astra", prefer_effort: "high", confirm: false },
    ]);
  });

  it("a question taken back while the folder's defaults are read asks the daemon nothing", async () => {
    openView("a1");
    let answer: (v: unknown) => void = () => {};
    H.gets[DEFAULTS] = new Promise((resolve) => (answer = resolve));
    answersSwitch();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    expect(await screen.findByTestId("code-switch-checking")).toBeTruthy();
    fireEvent.click(screen.getByTestId("code-switch-cancel"));
    await act(async () => {
      answer({ defaults: { tool: { harness: "codex", model: "gpt-6-mini" } } });
      await new Promise((r) => setTimeout(r, 40));
    });
    expect(switchPosts()).toEqual([]);
    expect(screen.queryByTestId("code-switch-ask")).toBeNull();
  });

  it("the token of the plan that was shown rides the second press, and only that one", async () => {
    openView("a1");
    const plan = { ...PLAN, token: "0123456789abcdef0123" };
    H.answers[SWITCH] = (body: Record<string, unknown>) =>
      body.confirm === true
        ? { switched: true, session: sess("a1", { harness: "codex" }), plan, note: NOTE }
        : { switched: false, plan };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    await screen.findByTestId("code-switch-lines", {}, { timeout: 10_000 });
    // A model picked under it is a question again: still no token sent.
    fireEvent.click(screen.getByTestId("code-pick-model-gpt-6-mini"));
    await waitFor(() => expect(switchPosts()).toHaveLength(2));
    await waitFor(() => expect((screen.getByTestId("code-switch-confirm") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByTestId("code-switch-confirm"));
    await waitFor(() => expect(switchPosts()).toHaveLength(3));
    expect(switchPosts().map((p) => p.body)).toEqual([
      { tool: "codex", confirm: false },
      { tool: "codex", model: "gpt-6-mini", confirm: false },
      { tool: "codex", model: "gpt-6-mini", confirm: true, expect: "0123456789abcdef0123" },
    ]);
  });

  it("when the plan changed, it is read again by itself: the sentence, the new lines, and a fresh press with the new token", async () => {
    const { ApiError } = (await import("@/lib/api")) as unknown as {
      ApiError: new (m: string, s: number, b?: unknown) => Error;
    };
    const WHY = "What this switch would do has changed since you looked, so nothing was changed. Look again, then switch.";
    expect(SWITCH_REARM_GUARD_MS).toBe(400);
    const NEW_LINES = ["Account: Home.", "Mode: Ask first.", "Model: Codex's default; effort: the tool's default."];
    lastPicked("codex", "gpt-6-astra", "high");
    openView("a1");
    let clock = 1_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => clock);
    let confirms = 0;
    H.answers[SWITCH] = (body: Record<string, unknown>) => {
      if (body.confirm !== true) {
        return confirms === 0
          ? { switched: false, plan: { ...PLAN, token: "aaaaaaaaaaaaaaaaaaaa" } }
          : { switched: false, plan: { ...PLAN, lines: NEW_LINES, token: "bbbbbbbbbbbbbbbbbbbb" } };
      }
      confirms += 1;
      return confirms === 1
        ? new ApiError(WHY, 409, { detail: WHY, code: "changed" })
        : { switched: true, session: sess("a1", { harness: "codex" }), plan: PLAN, note: NOTE };
    };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    await screen.findByTestId("code-switch-lines", {}, { timeout: 10_000 });
    expect(screen.queryByTestId("code-switch-changed")).toBeNull();
    // (The press has the cursor, as it does for a real press.)
    screen.getByTestId("code-switch-confirm").focus();
    expect(document.activeElement).toBe(screen.getByTestId("code-switch-confirm"));
    fireEvent.click(screen.getByTestId("code-switch-confirm"));
    // The NEW lines, in the same confirmation, under the daemon's sentence.
    await waitFor(() => expect(screen.getAllByTestId("code-switch-line").map((l) => l.textContent)).toEqual(NEW_LINES));
    const said = screen.getByTestId("code-switch-changed");
    expect(said.textContent).toBe(WHY);
    expect(said.getAttribute("role")).toBe("status");
    expect(said.compareDocumentPosition(screen.getByTestId("code-switch-lines")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByTestId("code-switch-heading").textContent).toBe("Switch to Codex?");
    expect(document.activeElement).toBe(screen.getByTestId("code-switch-ask"));
    // Nothing switched, nothing said elsewhere, the panel is still there.
    expect(chipTool()).toBe("claude-code");
    expect(screen.queryByTestId("code-note")).toBeNull();
    expect(screen.queryByTestId("code-switch-refused")).toBeNull();
    expect(screen.getByTestId("code-pick-tool-menu")).toBeTruthy();
    // Asked again ONCE, the same choice and preference, and it waits for a press.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 40));
    });
    const prefer = { prefer_model: "gpt-6-astra", prefer_effort: "high" };
    expect(switchPosts().map((p) => p.body)).toEqual([
      { tool: "codex", ...prefer, confirm: false },
      { tool: "codex", ...prefer, confirm: true, expect: "aaaaaaaaaaaaaaaaaaaa" },
      { tool: "codex", ...prefer, confirm: false },
    ]);
    expect((screen.getByTestId("code-switch-confirm") as HTMLButtonElement).disabled).toBe(false);
    // A second click right on top of the first (a double-click) confirms
    // NOTHING: the new lines have only just appeared.
    clock += SWITCH_REARM_GUARD_MS - 1;
    fireEvent.click(screen.getByTestId("code-switch-confirm"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(switchPosts()).toHaveLength(3);
    expect(chipTool()).toBe("claude-code");
    // The fresh press sends the NEW plan's token.
    clock += 1;
    H.gets[VIEW("a1")] = { session: sess("a1", { harness: "codex" }), events: EVENTS, running: null, pending: [], switches_tool: true };
    fireEvent.click(screen.getByTestId("code-switch-confirm"));
    await waitFor(() => expect(chipTool()).toBe("codex"));
    expect(switchPosts()[3].body).toEqual({ tool: "codex", ...prefer, confirm: true, expect: "bbbbbbbbbbbbbbbbbbbb" });
    expect(switchPosts()).toHaveLength(4);
  });

  it("a changed plan whose panel was closed meanwhile is said as any refused press (nothing is asked behind a closed panel)", async () => {
    const { ApiError } = (await import("@/lib/api")) as unknown as {
      ApiError: new (m: string, s: number, b?: unknown) => Error;
    };
    const WHY = "What this switch would do has changed since you looked, so nothing was changed. Look again, then switch.";
    openView("a1");
    let refuse: (e: unknown) => void = () => {};
    answersSwitch({ confirm: () => new Promise((_resolve, reject) => (refuse = reject)) });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    const go = await screen.findByTestId("code-switch-confirm", {}, { timeout: 10_000 });
    await waitFor(() => expect((go as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(go);
    await waitFor(() => expect(switchPosts()).toHaveLength(2));
    fireEvent.keyDown(screen.getByTestId("code-switch-ask"), { key: "Escape" });
    expect(screen.queryByTestId("code-pick-tool-menu")).toBeNull();
    await act(async () => {
      refuse(new ApiError(WHY, 409, { detail: WHY, code: "changed" }));
      await new Promise((r) => setTimeout(r, 40));
    });
    expect(screen.getByTestId("code-note").textContent).toBe(WHY);
    expect(switchPosts()).toHaveLength(2);
    expect(chipTool()).toBe("claude-code");
  });

  it("the session's own model is still saved the old way, with the tool list open", async () => {
    openView("a1");
    answersSwitch();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-model-claude-opus-5-5"));
    await waitFor(() => expect(H.patches).toEqual([{ path: VIEW("a1"), body: { model: "claude-opus-5-5" } }]));
    expect(switchPosts()).toEqual([]);
  });

  it("back on the session's own tool the question is gone, and its models are saved the old way again", async () => {
    openView("a1");
    answersSwitch();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    await screen.findByTestId("code-switch-lines", {}, { timeout: 10_000 });
    fireEvent.click(screen.getByTestId("code-pick-tool-claude-code"));
    expect(screen.queryByTestId("code-switch-ask")).toBeNull();
    fireEvent.click(screen.getByTestId("code-pick-model-claude-opus-5-5"));
    await waitFor(() => expect(H.patches).toEqual([{ path: VIEW("a1"), body: { model: "claude-opus-5-5" } }]));
    expect(switchPosts()).toHaveLength(1);
  });

  it("a refused first press is said in the confirmation's place, in the daemon's words; nothing changes", async () => {
    const { ApiError } = (await import("@/lib/api")) as unknown as { ApiError: new (m: string, s: number) => Error };
    const WHY = "Pi never asks before it acts. Change this session's mode first, then switch.";
    openView("a1");
    H.answers[SWITCH] = new ApiError(WHY, 409);
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const chip = await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-pi"));
    const said = await screen.findByTestId("code-switch-refused", {}, { timeout: 10_000 });
    expect(said.textContent).toBe(WHY);
    expect(said.getAttribute("role")).toBe("alert");
    expect(screen.getByTestId("code-switch-heading").textContent).toBe(switchRefusedHeading("Pi"));
    expect(screen.getByTestId("code-switch-heading").textContent).toBe("Not switched to Pi");
    // Nothing to confirm; the lists stay open for another pick.
    expect(screen.queryByTestId("code-switch-confirm")).toBeNull();
    expect(screen.queryByTestId("code-switch-lines")).toBeNull();
    expect(screen.getByTestId("code-switch-cancel").textContent).toBe(SWITCH_CLOSE);
    expect(screen.getByTestId("code-pick-tools")).toBeTruthy();
    expect(chipTool()).toBe("claude-code");
    expect(H.patches).toEqual([]);
    expect(switchPosts()).toEqual([{ path: SWITCH, body: { tool: "pi", confirm: false } }]);
    // Another tool can be asked about right away.
    answersSwitch();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    await waitFor(() => expect(screen.getAllByTestId("code-switch-line")).toHaveLength(LINES.length));
    expect(screen.queryByTestId("code-switch-refused")).toBeNull();
    fireEvent.click(screen.getByTestId("code-switch-cancel"));
    expect(document.activeElement).toBe(chip);
  });

  it("a refused second press is said where the view says every refused press; nothing changes", async () => {
    const WHY = "This session is working on a message. Stop it or let it finish, then switch.";
    openView("a1");
    const no = await refusal(WHY);
    answersSwitch({ confirm: () => no });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    const go = await screen.findByTestId("code-switch-confirm", {}, { timeout: 10_000 });
    await waitFor(() => expect((go as HTMLButtonElement).disabled).toBe(false));
    const reads = H.got.filter((p) => p === VIEW("a1")).length;
    fireEvent.click(go);
    await waitFor(() => expect(screen.getByTestId("code-note").textContent).toBe(WHY));
    expect(chipTool()).toBe("claude-code");
    expect(screen.queryByTestId("code-pick-tool-menu")).toBeNull();
    expect(screen.queryByTestId("code-switch-ask")).toBeNull();
    expect(screen.queryByTestId("code-notice")).toBeNull();
    expect(H.patches).toEqual([]);
    // (A refusal changed nothing, so nothing is read again.)
    expect(H.got.filter((p) => p === VIEW("a1")).length).toBe(reads);
  });

  it("an answer that comes back after another session was opened is dropped", async () => {
    openView("a1", {}, ["a1", "b1"]);
    let answer: (v: unknown) => void = () => {};
    answersSwitch({ confirm: () => new Promise((resolve) => (answer = resolve)) });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    const go = await screen.findByTestId("code-switch-confirm", {}, { timeout: 10_000 });
    await waitFor(() => expect((go as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(go);
    await waitFor(() => expect(switchPosts()).toHaveLength(2));
    // The user opens the other session while the switch is out.
    fireEvent.click(screen.getByTestId("code-row-b1"));
    await waitFor(() => expect(screen.getByTestId("code-session").getAttribute("data-session")).toBe("b1"));
    await screen.findByTestId("code-model-chip", {}, { timeout: 10_000 });
    const readsA = H.got.filter((p) => p === VIEW("a1")).length;
    await act(async () => {
      answer({ switched: true, session: sess("a1", { harness: "codex", title: "STALE" }), plan: PLAN, note: NOTE });
      await new Promise((r) => setTimeout(r, 50));
    });
    // The session on screen is untouched, and the answer's row went nowhere.
    expect(screen.getByTestId("code-session").getAttribute("data-session")).toBe("b1");
    expect(chipTool()).toBe("claude-code");
    expect(screen.queryByTestId("code-note")).toBeNull();
    expect(document.body.textContent).not.toContain("STALE");
    expect(H.got.filter((p) => p === VIEW("a1")).length).toBe(readsA);
  });

  it("a plan that comes back after the question was taken back is not shown", async () => {
    openView("a1");
    let answer: (v: unknown) => void = () => {};
    H.answers[SWITCH] = () => new Promise((resolve) => (answer = resolve));
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const chip = await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    expect(await screen.findByTestId("code-switch-checking")).toBeTruthy();
    // Nothing can be confirmed before the plan is read: the press does nothing.
    expect((screen.getByTestId("code-switch-confirm") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId("code-switch-confirm"));
    fireEvent.click(screen.getByTestId("code-switch-cancel"));
    await act(async () => {
      answer({ switched: false, plan: PLAN });
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(screen.queryByTestId("code-switch-ask")).toBeNull();
    fireEvent.click(chip);
    expect(screen.queryByTestId("code-switch-ask")).toBeNull();
    expect(switchPosts()).toHaveLength(1);
  });
});

// ── the independent review (v1.545.0) ───────────────────────────────────────

describe("the second press says only what it knows", () => {
  async function toSecondPress() {
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    const go = await screen.findByTestId("code-switch-confirm", {}, { timeout: 10_000 });
    await waitFor(() => expect((go as HTMLButtonElement).disabled).toBe(false));
    return go;
  }

  it.each([
    ["no answer at all (status 0)", async () => new (await apiError())("Iron Jarvis isn't answering.", 0)],
    ["a problem inside Iron Jarvis (500)", async () => new (await apiError())("Iron Jarvis hit a problem doing that. Try again.", 500, { detail: "internal error [err_1]" })],
    ["a dropped connection", async () => new TypeError("Failed to fetch")],
    ["a 4xx nobody wrote a body for", async () => new (await apiError())("Conflict", 409)],
  ])("an answer that never arrived (%s) claims nothing: the session is read again and the screen follows it", async (_name, make) => {
    openView("a1");
    const lost = await make();
    answersSwitch({ confirm: () => lost });
    const go = await toSecondPress();
    const reads = H.got.filter((p) => p === VIEW("a1")).length;
    // The switch DID go through on the daemon: its next read says so.
    H.gets[VIEW("a1")] = CODEX_VIEW();
    fireEvent.click(go);
    await waitFor(() => expect(screen.getByTestId("code-note").textContent).toBe(SWITCH_ANSWER_LOST));
    expect(screen.getByTestId("code-note").textContent).not.toContain("Nothing was changed");
    await waitFor(() => expect(H.got.filter((p) => p === VIEW("a1")).length).toBe(reads + 1));
    // The chip, the box and the conversation follow what the daemon holds.
    await waitFor(() => expect(chipTool()).toBe("codex"));
    expect(screen.getByTestId("code-box").getAttribute("aria-label")).toBe("Message Codex");
    expect(screen.getByTestId("code-notice").textContent).toBe(NOTE);
    expect(screen.queryByTestId("code-pick-tool-menu")).toBeNull();
  });

  it("a lost answer to a switch that did NOT go through leaves the session as the read says", async () => {
    openView("a1");
    answersSwitch({ confirm: () => new TypeError("Failed to fetch") });
    const go = await toSecondPress();
    const reads = H.got.filter((p) => p === VIEW("a1")).length;
    fireEvent.click(go);
    await waitFor(() => expect(screen.getByTestId("code-note").textContent).toBe(SWITCH_ANSWER_LOST));
    await waitFor(() => expect(H.got.filter((p) => p === VIEW("a1")).length).toBe(reads + 1));
    expect(chipTool()).toBe("claude-code");
  });

  it("an answer that says it SWITCHED, to a press that only asked, is never said as nothing changed", async () => {
    openView("a1");
    H.answers[SWITCH] = { switched: true, session: sess("a1", { harness: "codex" }), plan: PLAN, note: NOTE, notice: NOTICE };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    const reads = H.got.filter((p) => p === VIEW("a1")).length;
    H.gets[VIEW("a1")] = CODEX_VIEW();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    await waitFor(() => expect(chipTool()).toBe("codex"));
    await waitFor(() => expect(H.got.filter((p) => p === VIEW("a1")).length).toBe(reads + 1));
    expect(screen.queryByTestId("code-switch-refused")).toBeNull();
    expect(document.body.textContent).not.toContain("Nothing was changed");
    expect(screen.queryByTestId("code-pick-tool-menu")).toBeNull();
    expect(switchPosts()).toHaveLength(1);
  });

  it("…and when its row cannot be read it says so and reads the session again", async () => {
    openView("a1");
    H.answers[SWITCH] = { switched: true, session: "not a row" };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    const reads = H.got.filter((p) => p === VIEW("a1")).length;
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    await waitFor(() => expect(screen.getByTestId("code-note").textContent).toBe(SWITCH_ANSWER_UNREAD));
    await waitFor(() => expect(H.got.filter((p) => p === VIEW("a1")).length).toBe(reads + 1));
    expect(screen.queryByTestId("code-switch-refused")).toBeNull();
  });

  it("another tool pressed, or the panel closed, while a second press that SUCCEEDS is out changes nothing about it", async () => {
    for (const during of ["press pi", "close the panel"] as const) {
      openView("a1");
      let answer: (v: unknown) => void = () => {};
      answersSwitch({ confirm: () => new Promise((resolve) => (answer = resolve)) });
      const go = await toSecondPress();
      fireEvent.click(go);
      await waitFor(() => expect(switchPosts()).toHaveLength(2));
      if (during === "press pi") fireEvent.click(screen.getByTestId("code-pick-tool-pi"));
      else fireEvent.keyDown(screen.getByTestId("code-switch-ask"), { key: "Escape" });
      await act(async () => {
        await new Promise((r) => setTimeout(r, 30));
      });
      // Nothing more was asked while it was out.
      expect(switchPosts()).toHaveLength(2);
      H.gets[VIEW("a1")] = CODEX_VIEW();
      await act(async () => {
        answer({ switched: true, session: sess("a1", { harness: "codex" }), plan: PLAN, note: NOTE, notice: NOTICE });
        await new Promise((r) => setTimeout(r, 30));
      });
      expect(chipTool()).toBe("codex");
      expect(screen.getByTestId("code-notice").textContent).toBe(NOTE);
      expect(screen.queryByTestId("code-pick-tool-menu")).toBeNull();
      expect(screen.queryByTestId("code-note")).toBeNull();
      expect(switchPosts()).toHaveLength(2);
      cleanup();
      H.posts = [];
    }
  });
});

describe("right after a switch, before the session is read again", () => {
  const LIMIT = {
    type: "error",
    at: "2026-11-19T10:00:03Z",
    title: "The account hit its limit.",
    fix: "Press Continue on another account, or wait until it resets.",
    technical: "",
    fix_kind: "account_limit",
    turn: "t1",
  };
  /** Open a1, press Codex, press the switch; the read after it is `after`. */
  async function switchNow(view: Record<string, unknown>, after: unknown) {
    openView("a1", view);
    answersSwitch();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    const go = await screen.findByTestId("code-switch-confirm", {}, { timeout: 10_000 });
    await waitFor(() => expect((go as HTMLButtonElement).disabled).toBe(false));
    H.gets[VIEW("a1")] = after;
    fireEvent.click(go);
    await waitFor(() => expect(chipTool()).toBe("codex"));
  }
  const never = () => new Promise(() => {});

  it("the switch's own line is in the conversation at once, and the summary's line above the box", async () => {
    await switchNow({}, never());
    expect(screen.getByTestId("code-notice").textContent).toBe(NOTE);
    expect(screen.getByTestId("code-notice").getAttribute("data-switch")).toBe("true");
    expect(screen.getByTestId("code-handoff-waiting").textContent).toBe(
      "Codex gets a summary of this conversation with your next message.",
    );
    // The earlier reply already names its own tool.
    expect(screen.getByTestId("code-receipt-line").textContent).toContain("Claude Code");
  });

  it("the line is there once, also after the read brings the same event", async () => {
    let answerRead: (v: unknown) => void = () => {};
    await switchNow({}, new Promise((resolve) => (answerRead = resolve)));
    expect(screen.getAllByTestId("code-notice")).toHaveLength(1);
    await act(async () => {
      answerRead(CODEX_VIEW());
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(screen.getAllByTestId("code-notice")).toHaveLength(1);
  });

  it("a summary with nothing to hand over waits for nothing; a switch back says what happened since", async () => {
    openView("a1");
    const plan = { ...PLAN, summary: false };
    H.answers[SWITCH] = (body: Record<string, unknown>) =>
      body.confirm === true
        ? { switched: true, session: sess("a1", { harness: "codex" }), plan, note: NOTE, notice: NOTICE }
        : { switched: false, plan };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    let go = await screen.findByTestId("code-switch-confirm", {}, { timeout: 10_000 });
    await waitFor(() => expect((go as HTMLButtonElement).disabled).toBe(false));
    H.gets[VIEW("a1")] = never();
    fireEvent.click(go);
    await waitFor(() => expect(chipTool()).toBe("codex"));
    expect(screen.queryByTestId("code-handoff-waiting")).toBeNull();
    cleanup();
    H.posts = [];
    openView("a1");
    const back = { ...PLAN, resumes: true };
    H.answers[SWITCH] = (body: Record<string, unknown>) =>
      body.confirm === true
        ? { switched: true, session: sess("a1", { harness: "codex" }), plan: back, note: NOTE, notice: NOTICE }
        : { switched: false, plan: back };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    go = await screen.findByTestId("code-switch-confirm", {}, { timeout: 10_000 });
    await waitFor(() => expect((go as HTMLButtonElement).disabled).toBe(false));
    H.gets[VIEW("a1")] = never();
    fireEvent.click(go);
    await waitFor(() => expect(chipTool()).toBe("codex"));
    expect(screen.getByTestId("code-handoff-waiting").textContent).toBe(
      "Codex gets a summary of what happened since with your next message.",
    );
  });

  it("the old tool's limit card offers nothing any more, even if that read never lands", async () => {
    const view = {
      events: [...EVENTS.slice(0, 2), LIMIT],
      account_wait: { account: "acc-1", account_name: "Work", resets_at: "2026-11-19T15:00:00Z", group: "" },
      account_limit_note: "It carries on by itself when the account resets.",
    };
    openView("a1", view);
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    // (Before the switch the card has its presses.)
    await screen.findByTestId("code-continue-on", {}, { timeout: 10_000 });
    expect(screen.getByTestId("code-limit-waiting")).toBeTruthy();
    cleanup();
    await switchNow(view, never());
    expect(screen.getByTestId("code-error")).toBeTruthy();
    expect(screen.queryByTestId("code-continue-on")).toBeNull();
    expect(screen.queryByTestId("code-limit-waiting")).toBeNull();
    expect(screen.queryByTestId("code-limit-note")).toBeNull();
    expect(screen.queryByTestId("code-wait-cancel")).toBeNull();
    expect(screen.queryByTestId("code-continue-other-tool")).toBeNull();
  });

  it("a limit the NEW tool then hits is not said to wait for the OLD tool's account", async () => {
    const view = {
      events: [...EVENTS.slice(0, 2), LIMIT],
      account_wait: { account: "acc-1", account_name: "Work", resets_at: "2026-11-19T15:00:00Z", group: "" },
      account_limit_note: "It carries on by itself when the account resets.",
    };
    await switchNow(view, never());
    // The next message, on Codex, hits Codex's own limit; the read after it never lands.
    H.frames = [
      { name: "user", data: { type: "user", at: "2026-11-19T10:06:00Z", text: "carry on", files: [], tool: "codex", turn: "t2" } },
      { name: "error", data: { ...LIMIT, at: "2026-11-19T10:06:01Z", turn: "t2" } },
      { name: "done", data: { type: "done", at: "2026-11-19T10:06:02Z", stop: "failed", turn: "t2" } },
    ];
    fireEvent.change(screen.getByTestId("code-box"), { target: { value: "carry on" } });
    fireEvent.click(screen.getByTestId("code-send"));
    await waitFor(() => expect(screen.getAllByTestId("code-error")).toHaveLength(2));
    await waitFor(() => expect(screen.queryByTestId("code-stop")).toBeNull());
    // Codex's card has its own press…
    expect(await screen.findByTestId("code-continue-on")).toBeTruthy();
    // …and nothing of Claude Code's wait or note.
    expect(screen.queryByTestId("code-limit-waiting")).toBeNull();
    expect(screen.queryByTestId("code-limit-note")).toBeNull();
  });

  it("the old tool's `/` commands are gone at once", async () => {
    const view = { commands: [{ name: "compact", description: "Compact the conversation" }] };
    openView("a1", view);
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box", {}, { timeout: 10_000 });
    fireEvent.change(box, { target: { value: "/" } });
    expect(await screen.findByTestId("code-command-compact")).toBeTruthy();
    cleanup();
    await switchNow(view, never());
    fireEvent.change(screen.getByTestId("code-box"), { target: { value: "/" } });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(screen.queryByTestId("code-command-compact")).toBeNull();
  });

  it("a read that was already out when the switch went through decides nothing", async () => {
    // The first reads answer at once; then one hangs (it is the OLD tool's
    // view), and the read after the switch answers the new tool's.
    openView("a1");
    const old = H.gets[VIEW("a1")];
    let answerStale: (v: unknown) => void = () => {};
    let phase: "now" | "hang" | "new" = "now";
    H.gets[VIEW("a1")] = () => {
      if (phase === "now") return old;
      if (phase === "hang") {
        phase = "new";
        return new Promise((resolve) => (answerStale = resolve));
      }
      return CODEX_VIEW();
    };
    answersSwitch();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    // A change of the session's own model reads the session again: that read hangs.
    phase = "hang";
    const reads = H.got.filter((p) => p === VIEW("a1")).length;
    fireEvent.click(screen.getByTestId("code-pick-model-claude-opus-5-5"));
    await waitFor(() => expect(H.got.filter((p) => p === VIEW("a1")).length).toBe(reads + 1));
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    await openPicker();
    if (!screen.queryByTestId("code-pick-tool-menu")) fireEvent.click(screen.getByTestId("code-model-chip"));
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    const go = await screen.findByTestId("code-switch-confirm", {}, { timeout: 10_000 });
    await waitFor(() => expect((go as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(go);
    await waitFor(() => expect(chipTool()).toBe("codex"));
    // Now the stale read lands: it is the old tool's, and older than the switch.
    await act(async () => {
      answerStale(old);
      await new Promise((r) => setTimeout(r, 40));
    });
    expect(chipTool()).toBe("codex");
    expect(screen.getByTestId("code-box").getAttribute("aria-label")).toBe("Message Codex");
  });

  it("a message sent before the read lands still has the switch's line above it", async () => {
    await switchNow({}, never());
    fireEvent.change(screen.getByTestId("code-box"), { target: { value: "carry on" } });
    fireEvent.click(screen.getByTestId("code-send"));
    await waitFor(() => expect(screen.getAllByTestId("code-user")).toHaveLength(2));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    const line = screen.getByTestId("code-notice");
    expect(line.textContent).toBe(NOTE);
    const mine = screen.getAllByTestId("code-user")[1];
    expect(line.compareDocumentPosition(mine) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("a tool change that a read finds (switched from another window)", () => {
  it("drops the old tool's `/` commands and its limit card's presses, and keeps the new tool's facts", async () => {
    const LIMIT = { type: "error", at: "2026-11-19T10:00:03Z", title: "The account hit its limit.", fix: "", technical: "", fix_kind: "account_limit", turn: "t1" };
    openView("a1", {
      commands: [{ name: "compact", description: "Compact the conversation" }],
      events: [...EVENTS.slice(0, 2), LIMIT],
      account_wait: { account: "acc-1", account_name: "Work", resets_at: "2026-11-19T15:00:00Z", group: "" },
    });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box", {}, { timeout: 10_000 });
    fireEvent.change(box, { target: { value: "/" } });
    expect(await screen.findByTestId("code-command-compact")).toBeTruthy();
    fireEvent.change(box, { target: { value: "" } });
    expect(screen.getByTestId("code-limit-waiting")).toBeTruthy();
    // Another window switched this session to Codex; the new view names what
    // CODEX keeps (its own model rule), with no `commands` of its own.
    H.gets[VIEW("a1")] = CODEX_VIEW({ events: [...EVENTS.slice(0, 2), LIMIT, NOTICE], keeps_last: true });
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-model-claude-opus-5-5"));
    await waitFor(() => expect(chipTool()).toBe("codex"));
    fireEvent.change(screen.getByTestId("code-box"), { target: { value: "/" } });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(screen.queryByTestId("code-command-compact")).toBeNull();
    expect(screen.queryByTestId("code-limit-waiting")).toBeNull();
    expect(screen.queryByTestId("code-continue-on")).toBeNull();
    // The NEW tool's own facts from that same read were kept, not wiped.
    expect(screen.getByTestId("code-model-chip").getAttribute("title")).toContain("keeps the model");
  });
});

describe("the summary's waiting line after it was delivered", () => {
  it("is gone the moment the summary's own row arrives, even when the read after the reply fails", async () => {
    const pending = { to: "codex", to_label: "Codex", from: "claude-code", from_label: "Claude Code", resumes: false };
    openView("a1", { session: sess("a1", { harness: "codex" }), handoff_pending: pending });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    expect(await screen.findByTestId("code-handoff-waiting", {}, { timeout: 10_000 })).toBeTruthy();
    // The reply's own stream brings the summary's row; then the read after it fails.
    H.frames = [
      { name: "user", data: { type: "user", at: "2026-11-19T10:06:00Z", text: "carry on", files: [], tool: "codex", turn: "t2" } },
      { name: "handoff", data: { type: "handoff", at: "2026-11-19T10:06:01Z", turn: "t2", to: "codex", from: "claude-code", n: 1, text: "SUMMARY" } },
      { name: "done", data: { type: "done", at: "2026-11-19T10:06:02Z", stop: "completed", turn: "t2" } },
    ];
    H.gets[VIEW("a1")] = new Error("Iron Jarvis is not answering.");
    fireEvent.change(screen.getByTestId("code-box"), { target: { value: "carry on" } });
    fireEvent.click(screen.getByTestId("code-send"));
    await screen.findByTestId("code-handoff", {}, { timeout: 10_000 });
    await waitFor(() => expect(screen.queryByTestId("code-stop")).toBeNull());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(screen.queryByTestId("code-handoff-waiting")).toBeNull();
  });

  it("a summary given to ANOTHER tool's turn (an old row replayed) does not clear it", async () => {
    const pending = { to: "codex", to_label: "Codex", from: "claude-code", from_label: "Claude Code", resumes: false };
    openView("a1", { session: sess("a1", { harness: "codex" }), handoff_pending: pending });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    expect(await screen.findByTestId("code-handoff-waiting", {}, { timeout: 10_000 })).toBeTruthy();
    H.frames = [
      { name: "user", data: { type: "user", at: "2026-11-19T10:06:00Z", text: "carry on", files: [], tool: "codex", turn: "t2" } },
      { name: "handoff", data: { type: "handoff", at: "2026-11-19T10:06:01Z", turn: "t2", to: "pi", from: "claude-code", n: 1, text: "SUMMARY" } },
      { name: "done", data: { type: "done", at: "2026-11-19T10:06:02Z", stop: "completed", turn: "t2" } },
    ];
    H.gets[VIEW("a1")] = new Error("Iron Jarvis is not answering.");
    fireEvent.change(screen.getByTestId("code-box"), { target: { value: "carry on" } });
    fireEvent.click(screen.getByTestId("code-send"));
    await screen.findByTestId("code-handoff", {}, { timeout: 10_000 });
    await waitFor(() => expect(screen.queryByTestId("code-stop")).toBeNull());
    expect(await screen.findByTestId("code-handoff-waiting")).toBeTruthy();
  });
});

describe("a session that ever switched (the view's flag)", () => {
  const CHANGES = {
    source: "git",
    words: "Every file in this folder that differs from where git was when the session started here.",
    since: "2026-11-19T09:00:00Z",
    changes: [
      { path: "C:\\work\\shop\\cart.py", rel: "cart.py", name: "cart.py", status: "modified", added: 1, removed: 1, diff: "--- a/cart.py\n+++ b/cart.py\n@@ -1,1 +1,1 @@\n-y = 2\n+y = 3", by_tool: true },
    ],
    files: 1,
    added: 1,
    removed: 1,
    truncated_files: false,
    by_tool: 1,
    no_undo: "No Undo here.",
  };

  it("the Changes pane says the edits are every tool's, in the REAL view", async () => {
    openView("a1", { switched_tools: true });
    H.gets["/build/app/sessions/a1/changes"] = CHANGES;
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const line = await screen.findByTestId("code-changes-by-tool", {}, { timeout: 10_000 });
    await waitFor(() => expect(line.textContent).toBe("1 of these was edited by this session's coding tools' own file tools."));
    cleanup();
    // A session that never switched names its tool, as always.
    openView("a1");
    H.gets["/build/app/sessions/a1/changes"] = CHANGES;
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    expect((await screen.findByTestId("code-changes-by-tool", {}, { timeout: 10_000 })).textContent).toBe(
      "1 of these was edited by Claude Code's own file tools.",
    );
  });

  it("each reply names its tool even when the switch line is behind Load earlier", async () => {
    // What this window holds is one tool's work; the session did switch.
    const items = fold([...turn(1, { tool: "codex" }), ...turn(2, { tool: "codex" })]);
    expect(severalToolsWrote(items, "codex")).toBe(false);
    const { unmount } = render(<CodeConversation items={items} folder="C:\\work" tool="codex" account="Work" severalTools />);
    for (const l of screen.getAllByTestId("code-receipt-line")) expect(l.textContent).toContain("answered by Codex on Work");
    unmount();
    render(<CodeConversation items={items} folder="C:\\work" tool="codex" account="Work" />);
    for (const l of screen.getAllByTestId("code-receipt-line")) expect(l.textContent).not.toContain("Codex");
    cleanup();
    // …and in the real view.
    openView("a1", { switched_tools: true });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const line = await screen.findByTestId("code-receipt-line", {}, { timeout: 10_000 });
    expect(line.textContent).toContain("answered by Claude Code");
  });

  it("the account chip no longer says the account was chosen when the session was made", async () => {
    openView("a1");
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-model-chip", {}, { timeout: 10_000 });
    expect(screen.getByTestId("code-account-chip").getAttribute("title")).toContain("chosen when it was made");
    cleanup();
    openView("a1", { switched_tools: true });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-model-chip", {}, { timeout: 10_000 });
    await waitFor(() => expect(screen.getByTestId("code-account-chip").getAttribute("title")).toBe(ACCOUNT_NOW_TITLE));
    expect(ACCOUNT_NOW_TITLE).not.toContain("when it was made");
  });
});

describe("a failure card's presses act on the session's tool NOW", () => {
  const limit = { type: "error", at: "2026-11-19T10:01:05Z", title: "The account hit its limit.", fix: "", technical: "", fix_kind: "account_limit" };

  it("a turn another tool failed offers none of them, with no switch line on screen yet", () => {
    const items = fold([...turn(1, { tool: "claude-code" }).slice(0, 2), limit]);
    const onContinueOn = vi.fn();
    const onRetry = vi.fn();
    const { unmount } = render(
      <CodeConversation items={items} folder="C:\\work" tool="codex" onContinueOn={onContinueOn} onRetry={onRetry} onAnotherTool={vi.fn()} />,
    );
    expect(screen.getByTestId("code-error")).toBeTruthy();
    expect(screen.queryByTestId("code-continue-on")).toBeNull();
    expect(screen.queryByTestId("code-continue-other-tool")).toBeNull();
    unmount();
    // The same turn while the session is still that tool's: offered.
    render(<CodeConversation items={items} folder="C:\\work" tool="claude-code" onContinueOn={onContinueOn} />);
    expect(screen.getByTestId("code-continue-on")).toBeTruthy();
    cleanup();
    // A turn the record does not name is the session's (as it always was).
    const unnamed = fold([...turn(1).slice(0, 2), limit]);
    render(<CodeConversation items={unnamed} folder="C:\\work" tool="claude-code" onContinueOn={onContinueOn} />);
    expect(screen.getByTestId("code-continue-on")).toBeTruthy();
    cleanup();
    // Retry on another kind of failure follows the same rule.
    const failed = fold([...turn(1, { tool: "claude-code" }).slice(0, 2), { ...limit, fix_kind: "" }]);
    render(<CodeConversation items={failed} folder="C:\\work" tool="codex" onRetry={onRetry} />);
    expect(screen.queryByTestId("code-retry")).toBeNull();
    cleanup();
    render(<CodeConversation items={failed} folder="C:\\work" tool="claude-code" onRetry={onRetry} />);
    expect(screen.getByTestId("code-retry")).toBeTruthy();
  });
});

describe("the keyboard in the confirmation", () => {
  it("Shift+Tab from the first press, and ArrowUp, go back to the lists; Tab from the lists comes to the question", async () => {
    openView("a1");
    answersSwitch();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    expect(screen.getByTestId("code-pick-hint").textContent).toBe(PICKER_HINT);
    expect(PICKER_HINT).toContain("Esc or Tab closes");
    fireEvent.click(screen.getByTestId("code-pick-tool-codex"));
    await screen.findByTestId("code-switch-lines", {}, { timeout: 10_000 });
    // The panel's own line about its keys is true while the question is there.
    expect(screen.getByTestId("code-pick-hint").textContent).toBe(PICKER_HINT_WITH_QUESTION);
    expect(PICKER_HINT_WITH_QUESTION).toContain("Tab goes to the question below");
    expect(PICKER_HINT_WITH_QUESTION).not.toContain("Tab closes");
    const search = screen.getByTestId("code-pick-model-search");
    const cancel = screen.getByTestId("code-switch-cancel");
    // From the confirmation itself.
    fireEvent.keyDown(screen.getByTestId("code-switch-ask"), { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(search);
    // Tab from the lists: to the question (never a close, which would drop it).
    fireEvent.keyDown(search, { key: "Tab" });
    expect(document.activeElement).toBe(cancel);
    expect(screen.getByTestId("code-pick-tool-menu")).toBeTruthy();
    // Shift+Tab from the first press: back to the lists.
    fireEvent.keyDown(cancel, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(search);
    // The other tool's models can now be picked by keyboard.
    fireEvent.keyDown(search, { key: "ArrowDown" });
    fireEvent.keyDown(search, { key: "Enter" });
    await waitFor(() => expect(switchPosts()).toHaveLength(2));
    expect(switchPosts()[1].body).toMatchObject({ tool: "codex", model: "gpt-6-astra", confirm: false });
    await waitFor(() => expect((screen.getByTestId("code-switch-confirm") as HTMLButtonElement).disabled).toBe(false));
    // Shift+Tab from the SECOND press stays inside (it goes to the first).
    screen.getByTestId("code-switch-confirm").focus();
    fireEvent.keyDown(screen.getByTestId("code-switch-confirm"), { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(screen.getByTestId("code-switch-cancel"));
    // ArrowUp too.
    fireEvent.keyDown(screen.getByTestId("code-switch-cancel"), { key: "ArrowUp" });
    expect(document.activeElement).toBe(search);
    // Esc still cancels.
    fireEvent.keyDown(search, { key: "Escape" });
    expect(screen.queryByTestId("code-pick-tool-menu")).toBeNull();
    fireEvent.click(screen.getByTestId("code-model-chip"));
    expect(screen.queryByTestId("code-switch-ask")).toBeNull();
  });

  it("a refused tool pressed again is asked again (what refused it may be gone)", async () => {
    openView("a1");
    let asks = 0;
    const no = await refusal("Pi never asks before it acts. Change this session's mode first, then switch.");
    H.answers[SWITCH] = (body: Record<string, unknown>) => {
      asks += 1;
      return asks === 1 ? no : { switched: false, plan: { ...PLAN, tool: body.tool, label: "Pi" } };
    };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await openPicker();
    fireEvent.click(screen.getByTestId("code-pick-tool-pi"));
    await screen.findByTestId("code-switch-refused", {}, { timeout: 10_000 });
    fireEvent.click(screen.getByTestId("code-pick-tool-pi"));
    await waitFor(() => expect(screen.getAllByTestId("code-switch-line")).toHaveLength(LINES.length));
    expect(screen.queryByTestId("code-switch-refused")).toBeNull();
    expect(switchPosts().map((p) => p.body)).toEqual([
      { tool: "pi", confirm: false },
      { tool: "pi", confirm: false },
    ]);
    // A tool whose plan IS shown, pressed again, asks nothing more.
    fireEvent.click(screen.getByTestId("code-pick-tool-pi"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(switchPosts()).toHaveLength(2);
  });
});

// ── the limit card ──────────────────────────────────────────────────────────

describe("the limit card's Continue on another tool", () => {
  const LIMIT = {
    type: "error",
    at: "2026-11-19T10:00:03Z",
    title: "The account hit its limit.",
    fix: "Press Continue on another account, or wait until it resets.",
    technical: "",
    fix_kind: "account_limit",
  };
  const limitItems = () => fold([...turn(1, { tool: "claude-code" }).slice(0, 2), LIMIT]);

  it("is offered only when the session can switch, and only on a limit", () => {
    const open = vi.fn();
    const { unmount } = render(<CodeConversation items={limitItems()} folder="C:\\work" tool="claude-code" onAnotherTool={open} />);
    const press = screen.getByTestId("code-continue-other-tool");
    expect(press.textContent).toBe(CONTINUE_ON_ANOTHER_TOOL);
    expect(press.textContent).toBe("Continue on another tool");
    fireEvent.click(press);
    expect(open).toHaveBeenCalledTimes(1);
    unmount();
    render(<CodeConversation items={limitItems()} folder="C:\\work" tool="claude-code" />);
    expect(screen.getByTestId("code-error")).toBeTruthy();
    expect(screen.queryByTestId("code-continue-other-tool")).toBeNull();
    cleanup();
    // Another kind of failure offers none.
    const other = fold([...turn(1, { tool: "claude-code" }).slice(0, 2), { ...LIMIT, fix_kind: "sign_in" }]);
    render(<CodeConversation items={other} folder="C:\\work" tool="claude-code" onAnotherTool={open} />);
    expect(screen.queryByTestId("code-continue-other-tool")).toBeNull();
  });

  it("in the Code view it opens the tool list with the cursor in it, and switches nothing", async () => {
    openView("a1", { events: [...EVENTS.slice(0, 2), LIMIT] });
    answersSwitch();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    fireEvent.click(await screen.findByTestId("code-continue-other-tool", {}, { timeout: 10_000 }));
    expect(screen.getByTestId("code-pick-tools")).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByTestId("code-pick-tool-claude-code"));
    expect(screen.getByTestId("code-pick-tools").contains(document.activeElement)).toBe(true);
    expect(H.posts).toEqual([]);
    expect(H.patches).toEqual([]);
    expect(chipTool()).toBe("claude-code");
    cleanup();
    // A session that cannot switch has no such press.
    openView("a1", { events: [...EVENTS.slice(0, 2), LIMIT], switches_tool: undefined });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-error", {}, { timeout: 10_000 });
    expect(screen.queryByTestId("code-continue-other-tool")).toBeNull();
  });
});

// ── the confirmation by itself ──────────────────────────────────────────────

describe("the confirmation", () => {
  it("shows the daemon's sentences exactly as given, one per line", () => {
    const lines = ["Account: <b>Work</b>.", "Mode: **Ask first**."];
    render(
      <SwitchConfirm label="Codex" plan={decodeSwitchPlan({ ...PLAN, lines })} busy={false} onConfirm={() => {}} onCancel={() => {}} />,
    );
    const drawn = screen.getAllByTestId("code-switch-line");
    expect(drawn.map((l) => l.textContent)).toEqual(lines);
    expect(screen.getByTestId("code-switch-lines").querySelector("b, strong")).toBeNull();
  });

  it("a long unbroken id cannot scroll the panel sideways, and the confirmation is brought into view", () => {
    const seen: unknown[] = [];
    const had = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element, arg?: unknown) {
      seen.push([this.getAttribute("data-testid"), arg]);
    } as typeof Element.prototype.scrollIntoView;
    try {
      const lines = [`Model: ${"x".repeat(300)}.`];
      render(
        <SwitchConfirm label="Codex" plan={decodeSwitchPlan({ ...PLAN, lines })} busy={false} onConfirm={() => {}} onCancel={() => {}} />,
      );
      expect(screen.getByTestId("code-switch-lines").className).toContain("break-words");
      expect(seen).toEqual([["code-switch-ask", { block: "nearest" }]]);
      cleanup();
      render(<SwitchConfirm label="Pi" plan={null} refused={"y".repeat(300)} busy={false} onConfirm={() => {}} onCancel={() => {}} />);
      expect(screen.getByTestId("code-switch-refused").className).toContain("break-words");
      cleanup();
      render(
        <SwitchConfirm label="Codex" plan={decodeSwitchPlan(PLAN)} changed={"z".repeat(300)} busy={false} onConfirm={() => {}} onCancel={() => {}} />,
      );
      expect(screen.getByTestId("code-switch-changed").className).toContain("break-words");
    } finally {
      Element.prototype.scrollIntoView = had;
    }
  });

  it("after a plan CHANGED the press that switches waits a moment once the new lines are shown (the clock is injected)", () => {
    const onConfirm = vi.fn();
    let clock = 5_000;
    const now = () => clock;
    const first = decodeSwitchPlan(PLAN);
    const { rerender } = render(
      <SwitchConfirm label="Codex" plan={first} busy={false} onConfirm={onConfirm} onCancel={() => {}} now={now} />,
    );
    // An ordinary plan confirms at once.
    fireEvent.click(screen.getByTestId("code-switch-confirm"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    // The plan changed: it is read again (no plan for a moment), then shown.
    rerender(<SwitchConfirm label="Codex" plan={null} changed="It changed." busy={false} onConfirm={onConfirm} onCancel={() => {}} now={now} />);
    clock += 10_000;
    const second = decodeSwitchPlan({ ...PLAN, token: "bbbbbbbbbbbbbbbbbbbb" });
    rerender(<SwitchConfirm label="Codex" plan={second} changed="It changed." busy={false} onConfirm={onConfirm} onCancel={() => {}} now={now} />);
    fireEvent.click(screen.getByTestId("code-switch-confirm"));
    clock += SWITCH_REARM_GUARD_MS - 1;
    fireEvent.click(screen.getByTestId("code-switch-confirm"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    clock += 1;
    // (Drawn again meanwhile: the wait is from when the lines appeared.)
    rerender(<SwitchConfirm label="Codex" plan={second} changed="It changed." busy={false} onConfirm={onConfirm} onCancel={() => {}} now={now} />);
    fireEvent.click(screen.getByTestId("code-switch-confirm"));
    expect(onConfirm).toHaveBeenCalledTimes(2);
  });

  it("Shift+Tab from the first press and ArrowUp leave for the lists; without lists to go to, Tab stays inside", () => {
    const onLeave = vi.fn();
    const { unmount } = render(
      <SwitchConfirm label="Codex" plan={decodeSwitchPlan(PLAN)} busy={false} onConfirm={() => {}} onCancel={() => {}} onLeave={onLeave} />,
    );
    const cancel = screen.getByTestId("code-switch-cancel");
    const confirm = screen.getByTestId("code-switch-confirm");
    cancel.focus();
    fireEvent.keyDown(cancel, { key: "Tab", shiftKey: true });
    expect(onLeave).toHaveBeenCalledTimes(1);
    confirm.focus();
    fireEvent.keyDown(confirm, { key: "Tab", shiftKey: true });
    expect(onLeave).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(cancel);
    fireEvent.keyDown(cancel, { key: "Tab" });
    expect(document.activeElement).toBe(confirm);
    fireEvent.keyDown(confirm, { key: "ArrowUp" });
    expect(onLeave).toHaveBeenCalledTimes(2);
    unmount();
    render(<SwitchConfirm label="Codex" plan={decodeSwitchPlan(PLAN)} busy={false} onConfirm={() => {}} onCancel={() => {}} />);
    const c2 = screen.getByTestId("code-switch-cancel");
    c2.focus();
    fireEvent.keyDown(c2, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(screen.getByTestId("code-switch-confirm"));
  });

  it("while the second press is out neither press does anything", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(<SwitchConfirm label="Codex" plan={decodeSwitchPlan(PLAN)} busy onConfirm={onConfirm} onCancel={onCancel} />);
    fireEvent.click(screen.getByTestId("code-switch-confirm"));
    fireEvent.click(screen.getByTestId("code-switch-cancel"));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onCancel).not.toHaveBeenCalled();
  });
});
