/**
 * v1.390.0 REVIEW: a return to a coding session mid-reply carries its
 * Timeline on — driven through the REAL session screen, end to end (the
 * first cut proved it on the store alone).
 *
 * The user leaves the session while it works (the screen unmounts: its
 * stream is closed, the reply is kept in lib/turnCache WITH its Timeline as
 * folded so far) and comes back: the session asks for ONLY the frames after
 * where it was (`?ids=1&after=…`, v1.384.0) and the Timeline draws the whole
 * reply — every step, the helper and its model — never half of it, never a
 * step twice.
 */
import React from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
  const answer = (method: string, p: string) => {
    const hit = method === "GET" ? api.gets[p] : undefined;
    if (hit instanceof Error) return Promise.reject(hit);
    if (hit === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${p}`, 404));
    return Promise.resolve(hit ?? {});
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
    health: { default_provider: "anthropic", default_model: "claude-sonnet-4-5", providers: [] },
    refresh: () => {},
  }),
}));
const catalog = vi.hoisted(() => ({ models: [] as unknown[], usable: [] as unknown[], loading: false }));
vi.mock("@/lib/useModels", () => ({ useModels: () => catalog }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));

import { HarnessSession } from "@/components/harness/HarnessSession";
import { turnFor } from "@/lib/turnCache";

const FIXTURE = JSON.parse(
  readFileSync(path.join(__dirname, "fixtures", "build-timeline-v1390.json"), "utf8").replace(/\r\n/g, "\n"),
) as { frames: [string, Record<string, unknown>][] };
const BODY = FIXTURE.frames.filter(([e]) => e !== "done");
/** The rows the whole recorded reply draws (the first cut's own saved-line
 *  pin: harness-timeline-live-v1390). */
const WHOLE = ["Sonnet 4.5", "Read app.py", "Read util.py", "The builder · Sonnet 4.5", "Read util.py", "Made made.py"];

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1", title: "T", updated_at: "2026-10-25T10:00:00", running: false, waiting: false, archived: false,
    folder: "C:\\work\\shop", folder_name: "shop", is_project: false, project_id: null, mode: "act", plan: null,
    setup: { provider: "anthropic", model: "claude-sonnet-4-5" }, messages: [], session_id: "build:s1", queued: [],
    turn_id: "", ...over,
  };
}

function controlledStream() {
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctl = c;
    },
  });
  const enc = new TextEncoder();
  return {
    response: new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    push: (event: string, data: unknown, id?: string) =>
      ctl.enqueue(enc.encode(`${id ? `id: ${id}\n` : ""}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
  };
}

const terminal = { find: () => null, open: async () => null, render: () => <div />, close: () => {} };
const screenOf = () => (
  <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />
);
const labels = () =>
  screen.getAllByTestId("timeline-row").map((r) => r.querySelector("span[title]")?.getAttribute("title"));

beforeEach(() => {
  api.gets = { "/build/sessions/s1": view(), "/build/accounts": new api.ApiError("no such route", 404) };
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("a return mid-reply carries the Timeline on", () => {
  it("draws the whole reply after a return that asked for only the frames after where it was", async () => {
    const first = controlledStream();
    const again = controlledStream();
    const fetchMock = vi.fn().mockResolvedValueOnce(first.response).mockResolvedValueOnce(again.response);
    vi.stubGlobal("fetch", fetchMock);
    const { unmount } = render(screenOf());
    await screen.findByTestId("harness-empty");
    const box = screen.getByTestId("harness-input") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "read both files, ask a helper, make made.py" } });
    fireEvent.keyDown(box, { key: "Enter" });
    const half = Math.floor(BODY.length / 2);
    await act(async () => {
      BODY.slice(0, half).forEach(([e, d], i) => first.push(e, d, `${i}:0`));
    });
    await waitFor(() => expect(turnFor("s1")?.lastId).toBe(`${half - 1}:0`));
    const sent = JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body)) as { turn_id: string };
    // The user leaves; the reply goes on in the daemon.
    unmount();
    api.gets["/build/sessions/s1"] = view({ running: true, turn_id: sent.turn_id });
    render(screenOf());
    // The return asks for ONLY what came after where the screen was.
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(String(fetchMock.mock.calls[1][0])).toContain(`after=${encodeURIComponent(`${half - 1}:0`)}`);
    await act(async () => {
      again.push("resume", { resumed: true, running: true });
      BODY.slice(half).forEach(([e, d], j) => again.push(e, d, `${half + j}:0`));
      again.push("caught_up", { id: `${BODY.length - 1}:0` });
    });
    fireEvent.click(await screen.findByTestId("harness-pane-tab-timeline"));
    await waitFor(() => expect(labels()).toEqual(WHOLE), { timeout: 5_000 });
  });
});
