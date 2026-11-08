/**
 * v1.455.0 (readiness Wave F, item 13): "No, tell it what to do instead" on
 * every surface that answers an ask.
 *
 *  - Chat › Code's dock (HarnessSession) passes `onRedirect`: the card
 *    declines the step, then the words go to the running reply as a note
 *    (POST /chat/turns/{id}/steer), or, when the reply already ended, as the
 *    next message — the chat page's `redirectAfterDeny`, in the session's
 *    terms.
 *  - An agent run's ask carries `why`/`asker` once the daemon sends them
 *    (lane F2); `lib/askWhy.askWhyOf` reads them defensively off the event,
 *    the listing row, `waiting_on` and the objective's member, so those
 *    surfaces draw the short card with the redirect. Absent = the old card.
 *    (The run page's pin lives in approval-session-page-v13520.)
 */
import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    cancelled = false;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown>,
    postErrors: {} as Record<string, unknown>,
    calls: [] as { method: string; path: string; body?: unknown }[],
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    if (method === "POST" && api.postErrors[path] instanceof Error) return Promise.reject(api.postErrors[path]);
    const hit = method === "GET" ? api.gets[path] : undefined;
    if (hit instanceof Error) return Promise.reject(hit);
    if (hit === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(hit ?? {});
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
    health: { default_provider: "mock", default_model: "mock-1", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <div data-testid="tree" />,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { askWhyOf } from "@/lib/askWhy";
import { missionAskApproval } from "@/components/agents/mission/MissionOutput";

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

beforeEach(() => {
  api.gets = {};
  api.postErrors = {};
  api.calls = [];
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const posts = (pred: (p: string) => boolean) =>
  api.calls.filter((c) => c.method === "POST" && pred(c.path)).map((c) => ({ path: c.path, body: c.body }));

async function openSessionWithAsk() {
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start(c) { ctl = c; } });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } })),
  );
  api.gets["/build/sessions/s1"] = {
    id: "s1", title: "Shop", updated_at: "2026-11-08T10:00:00", running: false, waiting: false, archived: false,
    folder: "C:\\work\\shop", folder_name: "shop", is_project: false, project_id: null, mode: "act", plan: null,
    setup: {}, messages: [], session_id: "build:s1", queued: [], turn_id: "",
  };
  api.gets["/build/accounts"] = new api.ApiError("no such route", 404);
  render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
  await screen.findByTestId("harness-empty");
  const box = screen.getByTestId("harness-input") as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: "tidy the shop" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.length).toBe(1));
  const frame = {
    id: "ap_r", call_id: "c1", tool: "shell", args: { command: "rm -rf build" },
    why: "Running a command can change files in your folder.", asker: "model",
  };
  await act(async () => {
    ctl.enqueue(new TextEncoder().encode(`event: approval\ndata: ${JSON.stringify(frame)}\n\n`));
  });
  const card = await screen.findByTestId("chat-approval-card");
  return { card, ctl };
}

describe("Chat › Code's dock: No, tell it what to do instead", () => {
  it("declines, then the words reach the running reply as a note", async () => {
    const { card, ctl } = await openSessionWithAsk();
    fireEvent.click(within(card).getByTestId("approval-redirect"));
    fireEvent.change(within(card).getByTestId("approval-redirect-input"), {
      target: { value: "Only delete the dist folder" },
    });
    fireEvent.click(within(card).getByTestId("approval-redirect-send"));
    await waitFor(() =>
      expect(posts((p) => p.endsWith("/steer")).map((p) => p.body)).toEqual([{ text: "Only delete the dist folder" }]),
    );
    expect(posts((p) => p === "/chat/approvals/ap_r").map((p) => p.body)).toEqual([{ decision: "deny" }]);
    // The deny went first.
    const order = api.calls.filter((c) => c.method === "POST").map((c) => c.path);
    expect(order.indexOf("/chat/approvals/ap_r")).toBeLessThan(order.findIndex((p) => p.endsWith("/steer")));
    await act(async () => {
      ctl.close();
    });
  });

  it("a note the reply could not take (it was finishing) never loses the words: they wait in the box", async () => {
    const { card, ctl } = await openSessionWithAsk();
    // Every steer answers 404: the reply is finishing as the press lands.
    const steer404 = new api.ApiError("no such turn", 404);
    api.postErrors = new Proxy({} as Record<string, unknown>, {
      get: (_t, key) => (String(key).endsWith("/steer") ? steer404 : undefined),
    });
    fireEvent.click(within(card).getByTestId("approval-redirect"));
    fireEvent.change(within(card).getByTestId("approval-redirect-input"), {
      target: { value: "Use the cleanup script" },
    });
    fireEvent.click(within(card).getByTestId("approval-redirect-send"));
    await waitFor(() => expect(posts((p) => p.endsWith("/steer"))).toHaveLength(1));
    await waitFor(() =>
      expect((screen.getByTestId("harness-input") as HTMLTextAreaElement).value).toBe("Use the cleanup script"),
    );
    await act(async () => {
      ctl.close();
    });
  });
});

describe("agent-run asks read why/asker defensively (lib/askWhy)", () => {
  it("keeps a trimmed why (cut to 400) and only the two askers", () => {
    expect(askWhyOf({ why: "  It writes a file.  ", asker: "model" })).toEqual({
      why: "It writes a file.",
      asker: "model",
    });
    expect(askWhyOf({ why: "x".repeat(500), asker: "iron_jarvis" }).why).toHaveLength(400);
    expect(askWhyOf({ why: "   ", asker: "someone" })).toEqual({});
    expect(askWhyOf({ why: 3, asker: null })).toEqual({});
    expect(askWhyOf(null)).toEqual({});
    expect(askWhyOf("why")).toEqual({});
  });

  it("the objective screen's card carries them (so it is the short card with the redirect)", () => {
    const a = missionAskApproval(
      { approval_id: "ap_m", tool: "write_file", why: "Saving changes your folder.", asker: "model" },
      null,
    );
    expect(a.why).toBe("Saving changes your folder.");
    expect(a.asker).toBe("model");
    const old = missionAskApproval({ approval_id: "ap_m", tool: "write_file" }, null);
    expect("why" in old).toBe(false);
  });

  it("every agent-run answer surface reads them and passes a redirect", () => {
    const read = (f: string) => readFileSync(join(__dirname, "..", f), "utf8");
    const chat = read("app/chat/page.tsx");
    // The event fold and the listing poll both read them.
    expect(chat.match(/\.\.\.askWhyOf\(/g)?.length ?? 0).toBeGreaterThanOrEqual(2);
    expect(chat).toMatch(/\.\.\.\(ask\.why \? \{ why: ask\.why \} : \{\}\)/);
    const runPage = read("app/sessions/[id]/page.tsx");
    expect(runPage).toMatch(/\.\.\.askWhyOf\(session\.waiting_on\)/);
    expect(runPage).toMatch(/onRedirect=\{redirectRun\}/);
    const mission = read("components/agents/mission/MissionOutput.tsx");
    expect(mission).toMatch(/onRedirect=\{onRedirect\}/);
    expect(mission.match(/onRedirect=\{redirectAsk\}/g)?.length).toBe(2);
    const harness = read("components/harness/HarnessSession.tsx");
    expect(harness).toMatch(/onRedirect=\{redirectAfterDeny\}/);
  });
});
