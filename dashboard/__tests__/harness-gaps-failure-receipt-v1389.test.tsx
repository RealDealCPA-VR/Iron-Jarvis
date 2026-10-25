/**
 * Harness gaps, fix 2 and fix 3 — a Build coding session's failed reply gives
 * a way forward and keeps its details; its receipt says what the chat's does.
 *
 * Fix 2 (the v1.388.0 re-audit, G.3 / G.8 / H3): the session said "Press
 * Retry" with no Retry, kept no technical text and had no Sign in. Now, on
 * the REAL HarnessSession (the mocks are provider-refusal-harness-v1378's):
 *  - the NEWEST failed or cut-off reply offers Retry, which sends that
 *    reply's question again (its words, "@" files, uploads and "/" skill) to
 *    the session's own turn route — never while a reply runs, never on an
 *    older reply, never on a safety refusal;
 *  - the server's words are folded under a real Details toggle (a button:
 *    keyboard and touch reach it) — read off the SAVED line, so a reload
 *    still has them;
 *  - a signed-out Claude Code reply reads its headline alone beside the ONE
 *    Sign in press (POST /terminals/launch on this PC's own login); a refused
 *    key gets the link to where keys live;
 *  - a turn that fails live leaves ONE failure (its reply line), not the
 *    line plus a boxed note.
 *
 * Fix 3 (A22): a saved reply's `approved` and `refit` reach the receipt (a
 * real Build reply carried two approvals and the screen showed none), and
 * the Build terminal's chat saves and shows them too, live and reopened.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    calls: [] as { method: string; path: string; body?: unknown }[],
    postAnswers: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : api.postAnswers[`${method} ${path}`];
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
import { retryItemFor, retryOffered } from "@/lib/buildRetry";
import { receiptFacts } from "@/lib/receiptFacts";
import { BUILD_TERMINAL_EVENT } from "@/lib/buildLinks";
import type { BuildMessage } from "@/lib/buildSessions";

const FOLDER = "C:\\work\\shop";
const NET = { timeout: 5000 };
const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(messages: Record<string, unknown>[], over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Add a cart",
    updated_at: "2026-10-25T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: FOLDER,
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: { provider: "claude-cli", model: "opus" },
    messages,
    session_id: "build:s1",
    queued: [],
    turn_id: "",
    ...over,
  };
}

const QUESTION = {
  role: "user",
  content: "Add a cart page",
  turnId: "t1",
  at: "2026-10-25T10:05:00",
  filePaths: ["C:\\work\\shop\\app.py"],
  attachmentPaths: ["C:\\up\\mock.png"],
  skill: "frontend",
  skillSource: "yours",
};

/** A signed-out Claude Code reply, exactly as the daemon now writes it. */
const SIGNED_OUT = {
  role: "assistant",
  content: "",
  turnId: "t1",
  error: "Claude Code isn't signed in. Press Sign in beside Claude Code in Settings › Connections, then retry.",
  errorTitle: "Claude Code isn't signed in",
  errorKind: "sign_in",
  errorProvider: "claude-cli",
  errorDetail: "claude-cli: Not logged in · Please run /login",
  route: { provider: "claude-cli", model: "opus" },
};

/** A failure no press fixes (the generic words keep "Press Retry"). */
const GENERIC = {
  role: "assistant",
  content: "",
  turnId: "t1",
  error: "Claude Code couldn't finish this answer. Press Retry. If it happens again, pick another model for this chat.",
  errorTitle: "Claude Code couldn't finish this answer",
  errorDetail: "claude-cli: CLI exited 1: unexpected status 500",
};

/** The line a restart leaves (the daemon's INTERRUPTED_DETAIL). */
const RESTART_CUT = {
  role: "assistant",
  content: "I started on the cart",
  turnId: "t1",
  interrupted: true,
  error:
    "Iron Jarvis stopped while answering (a restart or a crash). What it had written so far is kept; press Retry to ask again.",
};

function sse(frames: [string, unknown][]): Response {
  const text = frames.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("");
  return new Response(
    new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode(text));
        c.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.gets = {
    "/build/accounts": new api.ApiError("no such route", 404),
    "/skills": { skills: [] },
    "/build/sessions/s1/skills": { skills: [], note: "" },
  };
  api.calls = [];
  api.postAnswers = {};
  localStorage.clear();
  fetchMock = vi.fn(async () => sse([["done", { reply: "ok" }]]));
  vi.stubGlobal("fetch", fetchMock);
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

const turnBodies = () =>
  (fetchMock.mock.calls as unknown as [string, RequestInit][])
    .filter(([url]) => String(url).endsWith("/build/sessions/s1/turn"))
    .map(([, init]) => JSON.parse(String(init.body)));

describe("lib/buildRetry: which reply offers Retry, and what it sends", () => {
  const m = (x: Record<string, unknown>) => x as unknown as BuildMessage;
  it("only the newest failed or cut-off reply, never busy / archived / a refusal / a limit card", () => {
    const o = { newest: true, busy: false, archived: false };
    expect(retryOffered(m(GENERIC), o)).toBe(true);
    expect(retryOffered(m({ role: "assistant", content: "x", interrupted: true }), o)).toBe(true);
    expect(retryOffered(m({ role: "assistant", content: "fine" }), o)).toBe(false);
    expect(retryOffered(m(GENERIC), { ...o, newest: false })).toBe(false);
    expect(retryOffered(m(GENERIC), { ...o, busy: true })).toBe(false);
    expect(retryOffered(m(GENERIC), { ...o, archived: true })).toBe(false);
    expect(retryOffered(m({ ...GENERIC, errorKind: "refusal" }), o)).toBe(false);
    expect(retryOffered(m({ ...GENERIC, accountLimit: { kind: "limit" } }), o)).toBe(false);
  });

  it("sends the reply's own question with what rode it; a hidden carry-on question sends 'continue'", () => {
    const shown = [m(QUESTION), m(GENERIC)];
    expect(retryItemFor(shown, 1, shown, FOLDER)).toEqual({
      text: "Add a cart page",
      skill: { name: "frontend", source: "yours" },
      files: [{ path: "C:\\work\\shop\\app.py", rel: "app.py", name: "app.py" }],
      attachments: [{ name: "mock.png", path: "C:\\up\\mock.png", bytes: 0, label: "mock.png" }],
      // Moved on purpose (harness gaps review): a retried question is SENT
      // as it was, never read for a "/" command again, and names the Claude
      // Code command it ran ("" = none) — harness-gaps-review-v1389.
      resend: { command: "" },
    });
    const carry = { role: "user", content: "Carry on with the task", turnId: "t2", continuation: true };
    const all = [m(QUESTION), m({ ...GENERIC, turnId: "t1" }), m(carry), m({ ...GENERIC, turnId: "t2" })];
    const visible = all.filter((x) => x.continuation !== true);
    expect(retryItemFor(visible, 2, all, FOLDER)).toEqual({ text: "continue" });
  });
});

describe("a failed reply in a coding session: Retry, Details, Sign in", () => {
  it("a signed-out Claude Code reply: the headline alone, the one Sign in press, Retry and Details — after a reload", async () => {
    api.gets["/build/sessions/s1"] = view([QUESTION, SIGNED_OUT]);
    api.postAnswers["POST /terminals/launch"] = { id: "pane-9" };
    renderSession();
    const line = await screen.findByTestId("harness-reply-error", {}, NET);
    expect(line.textContent?.trim()).toBe("Claude Code isn't signed in");
    const failure = screen.getByTestId("harness-reply-failure");
    // The one Sign in press (lib/cliSignIn): a Build terminal on this PC's
    // own login.
    const signIn = within(failure).getByRole("button", { name: /Sign in to Claude Code/ });
    fireEvent.click(signIn);
    await waitFor(
      () =>
        expect(api.calls.find((c) => c.method === "POST" && c.path === "/terminals/launch")?.body).toEqual({
          cli: "claude",
          account: "default",
        }),
      NET,
    );
    // The terminal it opened: on Build, its link is announced so Build shows
    // the terminal (a same-page link changes nothing by itself).
    const hint = await within(failure).findByRole("link", { name: "Finish signing in there" }, NET);
    expect(hint.getAttribute("href")).toBe("/terminals?focus=pane-9");
    const heard: string[] = [];
    const on = (e: Event) => heard.push(String((e as CustomEvent<{ id: string }>).detail?.id));
    window.addEventListener(BUILD_TERMINAL_EVENT, on);
    window.history.replaceState(null, "", "/terminals");
    try {
      fireEvent.click(hint);
    } finally {
      window.removeEventListener(BUILD_TERMINAL_EVENT, on);
      window.history.replaceState(null, "", "/");
    }
    expect(heard).toEqual(["pane-9"]);
    // Details: a real toggle, closed, then the masked words.
    const toggle = within(failure).getByTestId("harness-reply-error-details-toggle");
    expect(toggle.tagName).toBe("BUTTON");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("harness-reply-error-details")).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("harness-reply-error-details").textContent).toBe(SIGNED_OUT.errorDetail);
    expect(within(failure).getByTestId("harness-retry")).toBeTruthy();
  });

  it("Retry re-sends that question (words, files, uploads, skill) to the session's turn route", async () => {
    api.gets["/build/sessions/s1"] = view([QUESTION, GENERIC]);
    renderSession();
    const line = await screen.findByTestId("harness-reply-error", {}, NET);
    // No press fixes it: the whole sentence, which says "Press Retry" — and
    // the press is right there.
    expect(line.textContent).toContain("Press Retry");
    expect(screen.queryByRole("button", { name: /Sign in/ })).toBeNull();
    fireEvent.click(screen.getByTestId("harness-retry"));
    await waitFor(() => expect(turnBodies()).toHaveLength(1), NET);
    const body = turnBodies()[0];
    expect(body.text).toBe("Add a cart page");
    expect(body.files).toEqual(["C:\\work\\shop\\app.py"]);
    expect(body.attachments).toEqual(["C:\\up\\mock.png"]);
    expect(body.skill).toBe("frontend");
    // The box was left alone.
    expect((screen.getByTestId("harness-input") as HTMLTextAreaElement).value).toBe("");
  });

  it("a reply a restart cut off offers Retry (its words promise one)", async () => {
    api.gets["/build/sessions/s1"] = view([QUESTION, RESTART_CUT]);
    renderSession();
    const line = await screen.findByTestId("harness-reply-error", {}, NET);
    expect(line.textContent).toContain("press Retry");
    expect(screen.getByTestId("harness-retry")).toBeTruthy();
    // Nothing technical was kept for it: no Details.
    expect(screen.queryByTestId("harness-reply-error-details-toggle")).toBeNull();
  });

  it("controls: an OLDER failed reply offers no Retry; a safety refusal never does", async () => {
    api.gets["/build/sessions/s1"] = view([
      QUESTION,
      GENERIC,
      { role: "user", content: "Try something else", turnId: "t2", at: "2026-10-25T10:09:00" },
      { role: "assistant", content: "Done.", turnId: "t2" },
    ]);
    const first = renderSession();
    await screen.findByTestId("harness-reply-error", {}, NET);
    await waitFor(() => expect(screen.getAllByTestId("harness-reply")).toHaveLength(2), NET);
    expect(screen.queryByTestId("harness-retry")).toBeNull();
    // Details stay on the older line (folded; nothing is lost).
    expect(screen.getByTestId("harness-reply-error-details-toggle")).toBeTruthy();
    first.unmount();
    api.gets["/build/sessions/s1"] = view([
      QUESTION,
      { ...GENERIC, errorKind: "refusal", error: "Claude Code declined to answer this conversation. Rewind…" },
    ]);
    renderSession();
    await screen.findByTestId("harness-reply-error", {}, NET);
    expect(screen.queryByTestId("harness-retry")).toBeNull();
    expect(screen.queryAllByRole("button", { name: /Retry/ })).toHaveLength(0);
  });

  it("a turn that fails LIVE leaves ONE failure — its reply line with Retry and Details — never a second boxed note", async () => {
    api.gets["/build/sessions/s1"] = view([]);
    renderSession();
    await screen.findByTestId("harness-empty", {}, NET);
    fetchMock.mockImplementation(async (_url: string, init: RequestInit) => {
      const id = JSON.parse(String(init.body)).turn_id;
      api.gets["/build/sessions/s1"] = view([
        { ...QUESTION, turnId: id, filePaths: undefined, attachmentPaths: undefined, skill: undefined },
        { ...GENERIC, turnId: id },
      ]);
      return sse([
        ["error", {
          detail: "claude-cli: CLI exited 1: unexpected status 500",
          title: "Claude Code couldn't finish this answer",
          fix: "Press Retry. If it happens again, pick another model for this chat.",
          technical: "claude-cli: CLI exited 1: unexpected status 500",
        }],
      ]);
    });
    fireEvent.change(screen.getByTestId("harness-input"), { target: { value: "Add a cart page" } });
    fireEvent.keyDown(screen.getByTestId("harness-input"), { key: "Enter" });
    // The failure lands on the reply line (with its Retry) once the session
    // is read again — and the same words are not ALSO in a note below.
    await waitFor(() => expect(screen.getByTestId("harness-retry")).toBeTruthy(), NET);
    expect(screen.getAllByText(/couldn't finish this answer/)).toHaveLength(1);
    expect(screen.queryByTestId("harness-error-details-toggle")).toBeNull();
  });
});

describe("the Build receipt says what the chat's says (fix 3)", () => {
  const REPLY = {
    role: "assistant",
    content: "Added the cart page.",
    turnId: "t1",
    route: { provider: "claude-cli", model: "opus", reason: "explicit" },
    toolsUsed: ["shell"],
    approved: [
      { tool: "shell", decision: "conversation" },
      { tool: "process_start", decision: "conversation" },
    ],
    refit: { dropped: 3 },
  };

  it("lib/receiptFacts decodes the saved facts the chat page's way", () => {
    expect(receiptFacts(REPLY)).toEqual({ approved: REPLY.approved, refit: { dropped: 3 } });
    expect(receiptFacts({ approved: "junk", refit: { dropped: -1 }, remembered: [1, "  "] })).toEqual({});
    expect(receiptFacts({ adapted: { changes: ["tool_cap:6"] }, remembered: ["Keep it short"] })).toEqual({
      adapted: { changes: ["tool_cap:6"] },
      remembered: ["Keep it short"],
    });
  });

  it("a coding session's reply shows the refit on its receipt and the approvals given when opened", async () => {
    api.gets["/build/sessions/s1"] = view([{ ...QUESTION, filePaths: undefined }, REPLY]);
    renderSession();
    const receipt = await screen.findByTestId("turn-receipt", {}, NET);
    expect(within(receipt).getByTestId("turn-refit").textContent).toBe("Left out 3 older messages to fit this model");
    fireEvent.click(receipt);
    const rows = await screen.findAllByTestId("turn-approved", {}, NET);
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toMatch(/^You approved: /);
  });
});
