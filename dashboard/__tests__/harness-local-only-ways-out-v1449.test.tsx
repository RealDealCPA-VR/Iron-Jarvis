/**
 * v1.449.0 (Wave B, G-N item 8) — a LOCAL ONLY refusal in Chat › Code has the
 * chat page's two ways out.
 *
 * The chat page's refusal line offers "Choose a local model" and "Send to
 * <model> once" (v1.332.0); a coding session's failure line read the
 * refusal's sentence beside a Retry that would only be refused again. Now,
 * on the REAL HarnessSession (the mocks are harness-gaps-failure-receipt-
 * v1389's), a reply the daemon stamped `errorKind: "local_only"` +
 * `errorProvider` (routes/build.recorded_turn) shows:
 *  - the refusal's sentence as it is (no headline, no Retry);
 *  - "Choose a local model" — opens the session's own model menu;
 *  - "Send to <model> once" — the reply's question again with
 *    `allow_cloud_once: <provider>` on the turn (the daemon honours it for
 *    that turn only); NEVER while an app holds the session Local only
 *    (`setup.local_only_holds`, the chat page's rule).
 * The words are the chat page's own (lib/localOnly), pinned equal.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
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
    health: { default_provider: "ollama", default_model: "llama3", providers: [] },
    refresh: () => {},
  }),
}));
const MODELS = [
  { provider: "ollama", model: "llama3", label: "Llama 3", available: true, kind: "local", on_this_network: true },
  { provider: "openai", model: "gpt-6", label: "GPT-6", available: true, kind: "cloud", on_this_network: false },
];
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: MODELS, usable: MODELS, loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { failureAction } from "@/lib/failureActions";
import { retryOffered } from "@/lib/buildRetry";
import { decodeSessionView, type BuildMessage } from "@/lib/buildSessions";
import { CHOOSE_LOCAL_WORDS, sendOnceWords } from "@/lib/localOnly";

const FOLDER = "C:\\work\\shop";
const NET = { timeout: 5000 };
const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(messages: Record<string, unknown>[], setup: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Add a cart",
    updated_at: "2026-11-07T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: FOLDER,
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: { provider: "openai", model: "gpt-6", local_only: true, ...setup },
    messages,
    session_id: "build:s1",
    queued: [],
    turn_id: "",
  };
}

const QUESTION = {
  role: "user",
  content: "Add a cart page",
  turnId: "t1",
  at: "2026-11-07T10:05:00",
  filePaths: ["C:\\work\\shop\\app.py"],
};

/** The line the daemon writes for a LocalOnlyRefused (routes/build.recorded_turn). */
const LOCAL_ONLY = {
  role: "assistant",
  content: "",
  turnId: "t1",
  error: "This chat is local only, so nothing was sent to OpenAI. Pick a local model, or allow it for this one message.",
  errorKind: "local_only",
  errorProvider: "openai",
  route: { provider: "openai", model: "gpt-6" },
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

describe("the rules", () => {
  const m = (x: Record<string, unknown>) => x as unknown as BuildMessage;
  it("a Local only reply line is its own kind of failure, with the refused provider", () => {
    expect(failureAction({ fixKind: "local_only", fixProvider: "openai" }, null, "openai")).toEqual({
      kind: "local_only",
      provider: "openai",
    });
    // Retry would only be refused again: never offered on it.
    expect(retryOffered(m(LOCAL_ONLY), { newest: true, busy: false, archived: false })).toBe(false);
    // Control: the same reply with no kind is an ordinary failure with Retry.
    const plain = { ...LOCAL_ONLY, errorKind: undefined, errorProvider: undefined };
    expect(failureAction({ fixKind: "", fixProvider: "" }, null, "openai")).toBeNull();
    expect(retryOffered(m(plain), { newest: true, busy: false, archived: false })).toBe(true);
  });

  it("the session view decodes the apps holding it Local only", () => {
    const v = decodeSessionView(view([], { local_only_holds: ["microsoft_365", 7, ""] }));
    expect(v?.setup.localOnlyHolds).toEqual(["microsoft_365"]);
    expect(decodeSessionView(view([]))?.setup.localOnlyHolds).toEqual([]);
  });

  it("the words are the chat page's own (app/chat/page.tsx says them too)", () => {
    const page = readFileSync(join(process.cwd(), "app", "chat", "page.tsx"), "utf8").replace(/\r\n/g, "\n");
    expect(page).toMatch(new RegExp(`>\\s*${CHOOSE_LOCAL_WORDS}\\s*<`));
    // "Send to {name} once": the sentence around the name.
    const [lead, tail] = sendOnceWords("X").split("X");
    expect(page).toMatch(new RegExp(`${lead}\\{cloudModelName\\([^)]*\\)\\}${tail}`));
  });
});

describe("a Local only refusal in a coding session", () => {
  it("offers Choose a local model and Send once, no Retry; Send once asks again with the provider allowed", async () => {
    api.gets["/build/sessions/s1"] = view([QUESTION, LOCAL_ONLY]);
    renderSession();
    const line = await screen.findByTestId("harness-reply-error", {}, NET);
    expect(line.textContent?.trim()).toBe(LOCAL_ONLY.error);
    const failure = screen.getByTestId("harness-reply-failure");
    expect(within(failure).queryByTestId("harness-retry")).toBeNull();
    expect(within(failure).getByTestId("harness-local-choose").textContent).toBe(CHOOSE_LOCAL_WORDS);
    const once = within(failure).getByTestId("harness-local-send-once");
    // The line keeps the refused PROVIDER (the chat page's words with the
    // provider's plain name — the reply line carries no model id).
    expect(once.textContent).toBe(sendOnceWords("OpenAI"));
    fireEvent.click(once);
    await waitFor(() => expect(turnBodies()).toHaveLength(1), NET);
    const body = turnBodies()[0];
    expect(body.text).toBe("Add a cart page");
    expect(body.files).toEqual(["C:\\work\\shop\\app.py"]);
    expect(body.allow_cloud_once).toBe("openai");
  });

  it("an app holding the session Local only takes Send once away; Choose a local model stays", async () => {
    api.gets["/build/sessions/s1"] = view([QUESTION, LOCAL_ONLY], { local_only_holds: ["microsoft_365"] });
    renderSession();
    await screen.findByTestId("harness-reply-error", {}, NET);
    const failure = screen.getByTestId("harness-reply-failure");
    expect(within(failure).queryByTestId("harness-local-send-once")).toBeNull();
    expect(within(failure).getByTestId("harness-local-choose")).toBeTruthy();
  });

  it("Choose a local model opens the session's model menu", async () => {
    api.gets["/build/sessions/s1"] = view([QUESTION, LOCAL_ONLY]);
    renderSession();
    await screen.findByTestId("harness-reply-error", {}, NET);
    expect(screen.queryByTestId("model-menu")).toBeNull();
    fireEvent.click(screen.getByTestId("harness-local-choose"));
    await screen.findByTestId("model-menu", {}, NET);
    expect(turnBodies()).toHaveLength(0);
  });

  it("an ordinary Retry never carries allow_cloud_once (control)", async () => {
    const generic = { ...LOCAL_ONLY, errorKind: undefined, errorProvider: undefined, error: "OpenAI couldn't finish this answer. Press Retry." };
    api.gets["/build/sessions/s1"] = view([QUESTION, generic]);
    renderSession();
    await screen.findByTestId("harness-reply-error", {}, NET);
    expect(screen.queryByTestId("harness-local-send-once")).toBeNull();
    fireEvent.click(screen.getByTestId("harness-retry"));
    await waitFor(() => expect(turnBodies()).toHaveLength(1), NET);
    expect("allow_cloud_once" in turnBodies()[0]).toBe(false);
  });
});
