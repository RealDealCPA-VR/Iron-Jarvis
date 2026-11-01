/**
 * v1.420.0 review (fix plan C2.10): a REMEMBERED collapse is in force from
 * the session's FIRST frame. A return to a session paints the kept view at
 * once (lib/turnCache), so the pane column was drawn OPEN for one frame and
 * the remembered tab's reads (the Session tab's programs, the Changes read)
 * started for nothing before an effect collapsed it — on every return.
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
import { PANES_LAYOUT_KEY, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { decodeSessionView } from "@/lib/buildSessions";
import { keepView } from "@/lib/turnCache";

const FOLDER = "C:\\work\\shop";
const terminal: SessionTerminalApi = { find: () => null, open: async () => null, render: () => null, close: () => {} };
const RAW = {
  id: "s1",
  title: "Cart",
  updated_at: "2026-10-31T10:00:00",
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
  messages: [{ role: "user", content: "Add a cart", at: "2026-10-31T10:00:00", turnId: "t1" }],
  session_id: "build:s1",
  queued: [],
  turn_id: "",
};

beforeEach(() => {
  api.calls = [];
  api.gets = {
    "/build/sessions/s1": RAW,
    "/build/sessions/s1/programs": { programs: [], others: 0 },
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

describe("a remembered collapse holds from the first frame of a return", () => {
  it("the Session tab's reads never start behind a collapsed column", async () => {
    localStorage.setItem("ij.build.pane", "session");
    localStorage.setItem(PANES_LAYOUT_KEY, JSON.stringify({ collapsed: true, wide: false }));
    const v = decodeSessionView(RAW);
    if (!v) throw new Error("bad fixture");
    keepView("s1", v); // a return: the view is painted on the first render
    render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    const aside = await screen.findByTestId("harness-panes-aside");
    expect(aside.getAttribute("data-panes")).toBe("collapsed");
    // Let the session's own re-read land.
    await waitFor(() => expect(api.calls.some((c) => c.path === "/build/sessions/s1")).toBe(true));
    await new Promise((r) => setTimeout(r, 30));
    expect(api.calls.filter((c) => c.path.endsWith("/programs"))).toEqual([]);
    expect(screen.queryByTestId("pane-session")).toBeNull();
  });
});
