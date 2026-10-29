/**
 * v1.406.0 part 1 — a running coding reply SURVIVES the Chat | Code switch.
 *
 * Through the REAL switch (components/chat/ChatCodeSurface), the REAL Code
 * view (components/harness/CodeView → IjHarnessView → HarnessSession): a
 * reply is streaming in Code; the user switches to Chat (the Code view
 * unmounts, exactly as Build's view did) and back. The reply is drawn at
 * once from this window's memory (lib/turnCache), the page asks the daemon
 * only for the frames after the last one it drew, and the words carry on —
 * never twice, never lost.
 */
import React, { useState } from "react";
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
  const answer = (method: string, path: string) => {
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
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/chat",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: () => <div data-testid="files-panel" />,
}));
// next/dynamic: the REAL module, loaded (the Code view is the subject here).
vi.mock("next/dynamic", async () => {
  const R = await vi.importActual<typeof import("react")>("react");
  return {
    default: (loader: () => Promise<React.ComponentType<Record<string, unknown>>>) => {
      let Loaded: React.ComponentType<Record<string, unknown>> | null = null;
      const ready = loader().then((c) => {
        Loaded = c;
      });
      return function Dyn(props: Record<string, unknown>) {
        const [, force] = R.useState(0);
        R.useEffect(() => {
          let live = true;
          void ready.then(() => live && force(1));
          return () => {
            live = false;
          };
        }, []);
        return Loaded ? R.createElement(Loaded, props) : null;
      };
    },
  };
});

import { ChatCodeSurface } from "@/components/chat/ChatCodeSurface";
import { resetViewMemory } from "@/lib/chatSurface";
import { turnFor } from "@/lib/turnCache";

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Long job",
    updated_at: "2026-10-24T10:00:00",
    running: true,
    waiting: false,
    archived: false,
    folder: "C:\\work\\shop",
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: {},
    messages: [{ role: "user", content: "a long job", turnId: "tr1" }],
    session_id: "build:s1",
    queued: [],
    turn_id: "tr1",
    ...over,
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
  const response = new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  return {
    push: (event: string, data: unknown, id?: string) =>
      ctl.enqueue(enc.encode(`${id ? `id: ${id}\n` : ""}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    end: () => ctl.close(),
    fetch: async (_url: string, init?: RequestInit) => {
      init?.signal?.addEventListener("abort", () => {
        try {
          ctl.error(new DOMException("The operation was aborted.", "AbortError"));
        } catch {
          /* already closed */
        }
      });
      return response;
    },
  };
}

function FakeChat() {
  const [draft, setDraft] = useState("");
  return <input aria-label="chat draft" value={draft} onChange={(e) => setDraft(e.target.value)} />;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  resetViewMemory();
  window.sessionStorage.clear();
  localStorage.clear();
  window.history.replaceState(null, "", "/chat?view=code&session=s1");
  api.gets = {
    "/build/sessions": {
      folders: [{ path: "C:\\work\\shop", name: "shop", sessions: [{ id: "s1", title: "Long job", running: true }] }],
    },
    "/build/sessions/s1": view(),
    "/build/accounts": new api.ApiError("no such route", 404),
    "/terminals": { terminals: [] },
    "/terminals/activity": { panes: [] },
    "/terminals/ai-clis": { clis: [] },
  };
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("a reply streaming in Code survives a switch to Chat and back", () => {
  it("is drawn at once on return and carries on from the last frame drawn", async () => {
    const s = controlledStream();
    fetchMock.mockImplementation(s.fetch);
    render(<ChatCodeSurface renderChat={() => <FakeChat />} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled(), { timeout: 30_000 });
    expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toBe("http://d/chat/turns/tr1/stream?ids=1");
    await act(async () => {
      s.push("resume", { resumed: false, running: true });
      s.push("caught_up", { id: "" });
      s.push("token", { text: "Half of the " }, "0:12");
      s.push("token", { text: "answer" }, "0:18");
      await new Promise((r) => setTimeout(r, 40));
    });
    expect(await screen.findByTestId("harness-live-text")).toHaveTextContent("Half of the answer");

    // To Chat: the Code view goes (as Build's view did); the reply is kept.
    fireEvent.change(screen.getByLabelText("chat draft"), { target: { value: "a draft" } });
    fireEvent.click(screen.getByTestId("chat-view-chat"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 40));
    });
    expect(screen.queryByTestId("ij-harness-view")).toBeNull();
    expect(turnFor("s1")?.lastId).toBe("0:18");

    // Back to Code: drawn on the first render, then only the frames after.
    const back = controlledStream();
    fetchMock.mockReset();
    fetchMock.mockImplementation(back.fetch);
    fireEvent.click(screen.getByTestId("chat-view-code"));
    expect(await screen.findByTestId("harness-live-text", {}, { timeout: 30_000 })).toHaveTextContent(
      "Half of the answer",
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalled(), { timeout: 30_000 });
    expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toBe("http://d/chat/turns/tr1/stream?after=0%3A18");
    await act(async () => {
      back.push("resume", { resumed: true, running: true });
      back.push("token", { text: " is here." }, "0:27");
      back.push("caught_up", { id: "0:27" });
      await new Promise((r) => setTimeout(r, 40));
    });
    expect(screen.getByTestId("harness-live-text").textContent).toBe("Half of the answer is here.");
    // And the chat's draft is still there behind it.
    expect((screen.getByLabelText("chat draft") as HTMLInputElement).value).toBe("a draft");
    await act(async () => {
      back.push("done", { reply: "Half of the answer is here." }, "1:0");
      back.end();
      await new Promise((r) => setTimeout(r, 20));
    });
  }, 90_000); // a cold dynamic import of the whole harness under a loaded run
});
