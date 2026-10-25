/**
 * v1.384.0 REVIEW — a kept reply is DROPPED when its turn ends.
 *
 * lib/turnCache keeps a running reply's state while the user is elsewhere
 * (seed: its text, thinking and every tool card). The doer dropped it only
 * when the SESSION was opened again and saw the reply had ended — a reply the
 * user never went back to kept its state for the life of the window (up to
 * MAX_TURNS of them). The session list (IjHarnessView, re-read while Build is
 * on screen) knows which sessions still answer: a kept reply of any session
 * it does not list as running is dropped there.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({ gets: {} as Record<string, unknown> }));
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    get: (p: string) => (p in api.gets ? Promise.resolve(api.gets[p]) : Promise.reject(new ApiError(`unmocked ${p}`, 404))),
    post: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div data-testid="tree" /> }));
vi.mock("@/components/harness/HarnessSession", () => ({
  HarnessSession: ({ threadId }: { threadId: string }) => <div data-testid="session-stub" data-id={threadId} />,
}));

import { IjHarnessView } from "@/components/harness/IjHarnessView";
import { keepTurn, turnFor } from "@/lib/turnCache";
import type { StreamSeed } from "@/lib/useChatStream";

const row = (id: string, running: boolean) => ({
  id,
  title: id,
  updated_at: "2026-10-24T10:00:00",
  running,
  waiting: false,
  archived: false,
});
const LIST = {
  folders: [
    {
      path: "C:\\work\\shop",
      name: "shop",
      is_project: false,
      project_id: null,
      sessions: [row("a1", false), row("b1", true)],
    },
  ],
};
const terminal = { find: () => null, open: async () => null, render: () => null, close: () => {} };

const seed: StreamSeed = {
  text: "Half done",
  thinking: "",
  tools: [],
  approval: null,
  mcpAsks: [],
  phase: "working",
  prepStep: null,
  startedAt: 1,
  committed: true,
  steered: [],
  askIds: [],
  thinkingMs: null,
  firstTokenAt: null,
  truncated: false,
};

beforeEach(() => {
  api.gets = { "/build/sessions": LIST };
  localStorage.clear();
  window.history.replaceState(null, "", "/terminals");
});
afterEach(() => cleanup());

describe("a kept reply is dropped when the list shows its turn ended", () => {
  it("a1 (ended) is dropped, b1 (running) and a session the list has not answered for are not", async () => {
    keepTurn({ threadId: "a1", turnId: "ta", lastId: "0:9", stream: seed, page: { helpers: [], questions: [], steerNotes: [] } });
    keepTurn({ threadId: "b1", turnId: "tb", lastId: "0:9", stream: seed, page: { helpers: [], questions: [], steerNotes: [] } });
    keepTurn({ threadId: "gone", turnId: "tg", lastId: "0:9", stream: seed, page: { helpers: [], questions: [], steerNotes: [] } });
    render(
      <IjHarnessView initialSession="b1" terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />,
    );
    await screen.findAllByTestId("harness-folder-group");
    await waitFor(() => expect(turnFor("a1")).toBeNull());
    expect(turnFor("b1")?.turnId).toBe("tb");
    // A session the list does not show (archived, deleted): its turn ended too.
    expect(turnFor("gone")).toBeNull();
  });

  it("a list that failed to load drops nothing", async () => {
    api.gets = {};
    keepTurn({ threadId: "a1", turnId: "ta", lastId: "0:9", stream: seed, page: { helpers: [], questions: [], steerNotes: [] } });
    render(
      <IjHarnessView initialSession="a1" terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />,
    );
    await new Promise((r) => setTimeout(r, 50));
    expect(turnFor("a1")?.turnId).toBe("ta");
  });
});
