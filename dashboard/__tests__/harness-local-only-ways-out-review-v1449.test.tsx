/**
 * v1.449.0 review of lane B4 — a Local only refusal's "Send to … once" needs a
 * provider to send to.
 *
 * `routes/build.recorded_turn` stamps `errorProvider` only when the refusal
 * named one; a reply line with `errorKind: "local_only"` and NO provider used
 * to draw "Send to this model once", whose press called `sendOnceReply(…, "")`
 * — which drops an empty provider on the floor. A dead button. Now the line
 * offers "Choose a local model" alone there; with a provider both presses
 * stay (the builder's file pins that).
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
    gets: {} as Record<string, unknown>,
    calls: [] as { method: string; path: string; body?: unknown }[],
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const value = method === "GET" ? api.gets[path] : undefined;
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

const NET = { timeout: 5000 };
const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(messages: Record<string, unknown>[]) {
  return {
    id: "s1",
    title: "Add a cart",
    updated_at: "2026-11-07T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "C:\\work\\shop",
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: { provider: "openai", model: "gpt-6", local_only: true },
    messages,
    session_id: "build:s1",
    queued: [],
    turn_id: "",
  };
}

const QUESTION = { role: "user", content: "Add a cart page", turnId: "t1", at: "2026-11-07T10:05:00" };
/** A Local only line the daemon stamped with NO provider. */
const NO_PROVIDER = {
  role: "assistant",
  content: "",
  turnId: "t1",
  error: "This chat is local only, so nothing was sent off this PC.",
  errorKind: "local_only",
};

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.gets = {
    "/build/accounts": new api.ApiError("no such route", 404),
    "/skills": { skills: [] },
    "/build/sessions/s1/skills": { skills: [], note: "" },
  };
  api.calls = [];
  localStorage.clear();
  fetchMock = vi.fn(async () => new Response("", { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("a Local only refusal with no provider named", () => {
  it("the rule: the action keeps an empty provider (nothing to allow)", () => {
    expect(failureAction({ fixKind: "local_only", fixProvider: "" }, null, "openai")).toEqual({
      kind: "local_only",
      provider: "",
    });
  });

  it("offers Choose a local model alone — no dead Send once, no Retry", async () => {
    api.gets["/build/sessions/s1"] = view([QUESTION, NO_PROVIDER]);
    render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    await screen.findByTestId("harness-reply-error", {}, NET);
    const failure = screen.getByTestId("harness-reply-failure");
    expect(within(failure).queryByTestId("harness-local-send-once")).toBeNull();
    expect(within(failure).queryByTestId("harness-retry")).toBeNull();
    const choose = within(failure).getByTestId("harness-local-choose");
    fireEvent.click(choose);
    await screen.findByTestId("model-menu", {}, NET);
    // Nothing was sent: the only way out here is a local model.
    await waitFor(() => {
      const turns = (fetchMock.mock.calls as unknown as [string][]).filter(([u]) => String(u).endsWith("/turn"));
      expect(turns).toHaveLength(0);
    });
  });
});
