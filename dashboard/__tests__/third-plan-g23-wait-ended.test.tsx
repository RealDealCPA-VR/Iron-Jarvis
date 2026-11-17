/**
 * Third north-star plan, group 23 (RB6) — the screen's half: a "Resume when
 * ready" wait that ENDED without carrying the reply on (its carry-on was
 * refused for good, or for a whole day) says why on the session's limit card
 * — the daemon's sentence (`account_limit.wait_ended`), never a card that
 * silently offers Resume again.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return { ApiError, gets: {} as Record<string, unknown> };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string) => {
    const value = method === "GET" ? api.gets[path] : {};
    if (value === undefined) return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(value);
  };
  return {
    ApiError: api.ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    flattenDetail: (d: unknown) => String(d),
    get: (p: string) => answer("GET", p),
    post: (p: string) => answer("POST", p),
    put: (p: string) => answer("PUT", p),
    patch: (p: string) => answer("PATCH", p),
    del: (p: string) => answer("DELETE", p),
  };
});
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    checking: false,
    health: { default_provider: "mock", default_model: "mock-1", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { decodeAccountLimit } from "@/lib/buildAccounts";

const ENDED =
  "Iron Jarvis stopped waiting to carry this reply on, because it cannot start: This session's folder " +
  "cannot be used now: folder does not exist on this machine: C:\\work\\shop. Once that is fixed, press " +
  "Resume when ready, or send a message.";

const CARD = {
  account: "work",
  account_name: "Work",
  provider: "claude-cli",
  vendor: "claude",
  kind: "usage",
  resets_at: "2030-01-01T16:12:00Z",
  resumable: true,
  turn_id: "x",
};

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(limit: Record<string, unknown>) {
  return {
    id: "s1",
    title: "Add a cart",
    updated_at: "2026-10-22T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "C:\\work\\shop",
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: { provider: "claude-cli", account: "work", account_name: "Work" },
    messages: [],
    session_id: "build:s1",
    queued: [],
    turn_id: "",
    account: { id: "work", name: "Work", vendor: "claude", provider: "claude-cli", status: "ready" },
    account_vendor: "claude",
    account_limit: limit,
    account_wait: null,
  };
}

beforeEach(() => {
  api.gets = { "/build/accounts": { claude: [], codex: [], iron_proxy: { enabled: true, running: true } } };
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 200 })));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("a wait that ended without carrying the reply on", () => {
  it("is decoded from the card, and absent when nothing ended it", () => {
    expect(decodeAccountLimit({ ...CARD, wait_ended: ENDED })?.waitEnded).toBe(ENDED);
    expect(decodeAccountLimit(CARD)).not.toHaveProperty("waitEnded");
    expect(decodeAccountLimit({ ...CARD, wait_ended: 7 })).not.toHaveProperty("waitEnded");
  });

  it("says why on the session's limit card", async () => {
    api.gets["/build/sessions/s1"] = view({ ...CARD, wait_ended: ENDED });
    render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    const card = await screen.findByTestId("account-limit-card");
    await waitFor(() => expect(card.textContent).toContain(ENDED));
  });

  it("says nothing more on a card no wait ended", async () => {
    api.gets["/build/sessions/s1"] = view(CARD);
    render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    const card = await screen.findByTestId("account-limit-card");
    expect(card.textContent).not.toContain("stopped waiting");
  });
});
