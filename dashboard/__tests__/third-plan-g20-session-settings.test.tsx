/**
 * Third north-star fix plan, group 20 ("what the screen says is what
 * happens") — a coding session's own settings, on the REAL HarnessSession
 * (the mocks are third-plan-g19-coding-stop's):
 *  - RB3 (decision G14): a level or Plan changed WHILE a reply runs says
 *    beside the picker when it applies, in the daemon's answer (`applies`):
 *    stricter "from this reply's next step", looser "from your next message".
 *    Nothing is said while no reply runs, and the line goes when the reply
 *    ends.
 *  - MC6: the Browser box of Build › Code says that what the coding tool
 *    reads from your open tabs needs no card and goes to its vendor.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    gets: {} as Record<string, unknown | ((path: string) => unknown)>,
    patches: [] as { path: string; body: unknown }[],
    patchAnswer: (_body: unknown): unknown => ({}),
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    if (method === "PATCH") {
      api.patches.push({ path, body });
      return Promise.resolve(api.patchAnswer(body));
    }
    const hit = method === "GET" ? api.gets[path] : undefined;
    const value = typeof hit === "function" ? (hit as (p: string) => unknown)(path) : hit;
    if (value instanceof Error) return Promise.reject(value);
    if (value === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(value ?? {});
  };
  return {
    ApiError: api.ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    flattenDetail: (d: unknown) => String(d),
    get: (p: string) => answer("GET", p),
    post: (p: string, b?: unknown) => answer("POST", p, b),
    put: (p: string, b?: unknown) => answer("PUT", p, b),
    patch: (p: string, b?: unknown) => answer("PATCH", p, b),
    del: (p: string) => answer("DELETE", p),
  };
});

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    checking: false,
    health: { default_provider: "claude-cli", default_model: "opus", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import {
  LEVEL_APPLIES_WORDS,
  LOCAL_ONLY_APPLIES_ON,
  levelAppliesOf,
  localOnlyAppliesLine,
  patchSessionLevel,
} from "@/lib/buildSessions";
import { jarvisToolHint } from "@/lib/buildApp";

const NET = { timeout: 5000 };
const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(setup: Record<string, unknown> = {}, mode = "act") {
  return {
    id: "s1",
    title: "Write the notes",
    updated_at: "2026-11-17T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "C:\\work\\notes",
    folder_name: "notes",
    is_project: false,
    project_id: null,
    mode,
    plan: null,
    setup: { provider: "claude-cli", model: "opus", approval_mode: "yolo", ...setup },
    messages: [],
    session_id: "build:s1",
    queued: [],
    turn_id: "",
  };
}

/** A stream the test feeds frame by frame. */
class Feed {
  private ctl: ReadableStreamDefaultController<Uint8Array> | null = null;
  private enc = new TextEncoder();
  response(): Response {
    const body = new ReadableStream<Uint8Array>({
      start: (c) => {
        this.ctl = c;
      },
    });
    return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  }
  frame(event: string, data: unknown): void {
    this.ctl?.enqueue(this.enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
  }
  end(): void {
    this.ctl?.close();
  }
}

let feed: Feed;

beforeEach(() => {
  api.gets = {
    "/build/accounts": new api.ApiError("no such route", 404),
    "/skills": { skills: [] },
    "/build/sessions/s1/skills": { skills: [], note: "" },
    "/build/sessions/s1": view(),
  };
  api.patches = [];
  api.patchAnswer = () => view();
  localStorage.clear();
  feed = new Feed();
  vi.stubGlobal("fetch", vi.fn(async () => feed.response()));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderSession() {
  return render(
    <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
  );
}

async function pick(mode: string) {
  fireEvent.click(screen.getByTestId("permission-chip"));
  const menu = await screen.findByTestId("permission-menu");
  const row = within(menu).getAllByTestId("permission-item").find((r) => r.dataset.mode === mode)!;
  await act(async () => {
    fireEvent.click(row);
  });
}

async function startReply() {
  await screen.findByTestId("harness-empty", {}, NET);
  fireEvent.change(screen.getByTestId("harness-input"), { target: { value: "tidy the notes" } });
  await act(async () => {
    fireEvent.keyDown(screen.getByTestId("harness-input"), { key: "Enter" });
  });
  feed.frame("round", { round: 1 });
}

describe("a level changed while a reply runs says when it applies", () => {
  it("stricter: from this reply's next step; looser: from your next message; gone when the reply ends", async () => {
    renderSession();
    await startReply();
    api.patchAnswer = (body) => ({ ...view({ approval_mode: "always_ask" }), applies: "now", _sent: body });
    await pick("always_ask");
    const line = await screen.findByTestId("harness-level-applies", {}, NET);
    expect(line.textContent).toBe("Applies from this reply's next step.");
    expect(api.patches.at(-1)).toEqual({ path: "/build/sessions/s1", body: { approval_mode: "always_ask" } });

    api.patchAnswer = () => ({ ...view({ approval_mode: "yolo" }), applies: "next_reply" });
    await pick("yolo");
    await waitFor(
      () => expect(screen.getByTestId("harness-level-applies").textContent).toBe("Applies from your next message."),
      NET,
    );

    // The reply ends: the line goes.
    await act(async () => {
      feed.frame("done", { reply: "Done." });
      feed.end();
    });
    await waitFor(() => expect(screen.queryByTestId("harness-level-applies")).toBeNull(), NET);
  });

  it("Plan switched on while a reply runs says it applies from the next step", async () => {
    renderSession();
    await startReply();
    api.patchAnswer = () => ({ ...view({}, "plan"), applies: "now" });
    await pick("plan");
    expect(api.patches.at(-1)?.body).toEqual({ mode: "plan" });
    const line = await screen.findByTestId("harness-level-applies", {}, NET);
    expect(line.textContent).toBe(LEVEL_APPLIES_WORDS.now);
  });

  it("says nothing while no reply runs", async () => {
    renderSession();
    await screen.findByTestId("harness-empty", {}, NET);
    // Even an answer that carried a word is not shown with no reply running.
    api.patchAnswer = () => ({ ...view({ approval_mode: "always_ask" }), applies: "now" });
    await pick("always_ask");
    await waitFor(() => expect(api.patches).toHaveLength(1), NET);
    expect(screen.queryByTestId("harness-level-applies")).toBeNull();
  });

  it("reads only the daemon's two answers", () => {
    expect(levelAppliesOf({ applies: "now" })).toBe("now");
    expect(levelAppliesOf({ applies: "next_reply" })).toBe("next_reply");
    expect(levelAppliesOf({ applies: "later" })).toBeNull();
    expect(levelAppliesOf({})).toBeNull();
    expect(levelAppliesOf(null)).toBeNull();
  });
});

describe("Local only changed while a reply runs says it reaches the next message", () => {
  it("turning it on and off, in the daemon's answer; nothing when the daemon says nothing (no reply runs)", async () => {
    api.patchAnswer = () => ({ ...view({ local_only: true }), applies_local_only: "next_reply" });
    const on = await patchSessionLevel("s1", { local_only: true });
    expect(on.localOnlyLine).toBe(
      "Local only starts with your next message. This reply keeps the model it started with.",
    );
    expect(on.localOnlyLine).toBe(LOCAL_ONLY_APPLIES_ON);
    expect(api.patches.at(-1)).toEqual({ path: "/build/sessions/s1", body: { local_only: true } });

    api.patchAnswer = () => ({ ...view(), applies_local_only: "next_reply" });
    const off = await patchSessionLevel("s1", { local_only: false });
    expect(off.localOnlyLine).toBe("Applies from your next message.");

    // Idle: the daemon adds no key, so there is no line.
    api.patchAnswer = () => view({ local_only: true });
    expect((await patchSessionLevel("s1", { local_only: true })).localOnlyLine).toBe("");
    expect(localOnlyAppliesLine({ applies_local_only: "now" }, true)).toBe("");
    // The level's own answer is read apart from it.
    api.patchAnswer = () => ({ ...view(), applies: "now", applies_local_only: "next_reply" });
    const both = await patchSessionLevel("s1", { local_only: true, approval_mode: "always_ask" });
    expect(both.applies).toBe("now");
    expect(both.localOnlyLine).toBe(LOCAL_ONLY_APPLIES_ON);
  });
});

describe("the Browser box of Build › Code (MC6)", () => {
  it("says reading your open tabs asks nothing and where what it reads goes; clicks ask first", () => {
    const words = jarvisToolHint("browser", "Claude Code", "claude-code");
    expect(words).toBe(
      "Claude Code may use your browser through Iron Jarvis. It reads your open tabs without asking, and what it " +
        "reads goes to Anthropic with the conversation. Every click, typing or page change asks you first.",
    );
    expect(jarvisToolHint("browser", "Codex", "codex")).toContain("goes to OpenAI with the conversation");
  });
});
