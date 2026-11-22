/**
 * 26-a (R2.16) — in a whole Chat › Code session, review comments that
 * arrived in two reads make two lines, but the view has ONE card and ONE
 * Answer these (G4): the newest line carries every comment still to answer,
 * the older line is its sentence alone, and only the card reads the
 * comments (lib/prComments cardLineIndex, HarnessSession → HarnessMessageRow
 * `prCard`).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return { ApiError, gets: {} as Record<string, unknown>, calls: [] as { method: string; path: string }[] };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string) => {
    api.calls.push({ method, path });
    if (method === "POST" && path === "/chat/changes") return Promise.resolve({ changes: [] });
    if (method !== "GET") return Promise.resolve({});
    const hit = api.gets[path];
    if (hit instanceof Error) return Promise.reject(hit);
    if (hit === undefined) return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(hit);
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
const DAEMON = {
  online: true,
  checking: false,
  health: { default_provider: "mock", default_model: "mock-1", providers: [] },
  refresh: () => {},
};
const MODELS = { models: [], usable: [], loading: false };
vi.mock("@/lib/daemon", () => ({ useDaemon: () => DAEMON }));
vi.mock("@/lib/useModels", () => ({ useModels: () => MODELS }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div data-testid="dir-tree" /> }));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";

const FOLDER = "C:\\work\\shop";
const terminal: SessionTerminalApi = { find: () => null, open: async () => null, render: () => null, close: () => {} };

function comment(id: string, author: string) {
  return {
    id: `s1:${id}`, kind: "line", author, author_kind: "member", ticked: true, path: "app.py",
    abs: `${FOLDER}\\app.py`, line: 2, start_line: 0, side: "new", outdated: false, body: `Words of ${author}.`,
    quote: "+y = 3", url: "", state: "new", answer_files: [], place: "app.py, line 2",
    github_created_at: "2026-11-20T10:00:00Z",
  };
}

const RAW = {
  id: "s1",
  title: "Cart",
  updated_at: "2026-11-22T10:00:00",
  running: false,
  waiting: false,
  archived: false,
  folder: FOLDER,
  folder_name: "shop",
  is_project: false,
  project_id: null,
  mode: "act",
  plan: null,
  setup: {},
  messages: [
    { role: "user", content: "Add a cart", at: "2026-11-22T09:00:00", turnId: "t1" },
    { role: "assistant", content: "Done.", at: "2026-11-22T09:01:00", turnId: "t1" },
    { role: "assistant", content: "1 review comment arrived on this session's pull request.",
      at: "2026-11-22T09:30:00", prComments: { ids: ["s1:c1"], n: 1, authors: ["sam"] } },
    { role: "assistant", content: "1 review comment arrived on this session's pull request.",
      at: "2026-11-22T09:45:00", prComments: { ids: ["s1:c2"], n: 1, authors: ["pat"] } },
  ],
  session_id: "build:s1",
  queued: [],
  turn_id: "",
};

beforeEach(() => {
  api.calls = [];
  api.gets = {
    "/build/sessions/s1": RAW,
    "/build/sessions/s1/programs": { programs: [], others: 0 },
    "/build/sessions/s1/pr-comments": {
      pr_url: "https://github.com/acme/shop/pull/7", comments: [comment("c1", "sam"), comment("c2", "pat")],
      quiet: false, done: "", max_answer: 20,
    },
    "/build/accounts": new api.ApiError("no such route", 404),
    "/browser/status": { paired: false },
  };
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 404 })));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("a session with review comments from two reads", () => {
  it("has one card on the newest line, holding both comments, and one Answer these", async () => {
    render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    const rows = await screen.findAllByTestId("pr-comment");
    expect(rows.map((r) => r.getAttribute("data-id"))).toEqual(["s1:c1", "s1:c2"]);
    const lines = screen.getAllByTestId("pr-comments-line");
    expect(lines).toHaveLength(2);
    // The older line is its sentence alone; the newest carries the card.
    expect(lines[0].textContent).toBe("1 review comment from sam arrived.");
    expect(within(lines[0]).queryByTestId("pr-comment")).toBeNull();
    expect(within(lines[1]).getAllByTestId("pr-comment")).toHaveLength(2);
    expect(screen.getAllByTestId("pr-comments-answer")).toHaveLength(1);
    expect(screen.getByTestId("pr-comments-answer").textContent).toBe("Answer these (2)");
    await waitFor(() => expect(api.calls.some((c) => c.path === "/build/sessions/s1/pr-comments")).toBe(true));
  });
});
