/**
 * v1.227.0 wave 1 (A1) — ONE CARD PER PENDING ASK on the chat page.
 *
 * Converted from __audit_20260904__/chat-session-approvals.audit.test.tsx
 * (D1 / D1b), which were RED at v1.226.0 for this reason: the mid-run
 * approval state was a single slot (`sessionApproval`), filled by a
 * newest-first scan that `break`s on the first approval event it meets. The
 * runtime asks in PARALLEL batches — the measured job published 4-5
 * `approval.requested` in the same microsecond — so the user saw ONE card,
 * answered it, and the siblings stayed pending invisibly until each expired
 * 300 s later as "denied by the clock".
 *
 * The page now folds the pending set from the events (a resolve closes its
 * request; everything else for the awaited session is a card) and renders
 * one ApprovalCard per id. Harness copied from chat-durable-turns-v1226 with
 * a useEvents mock the test can PUSH events into.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  const setters = new Set<(e: unknown[]) => void>();
  return {
    FakeApiError,
    api: {
      gets: [] as string[],
      posts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: { result: { reply: "streamed reply" } as Record<string, unknown> },
    setters,
    emit(events: unknown[]) {
      for (const s of setters) s(events);
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.api.gets.push(path);
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    if (r instanceof Error) throw r;
    return r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    const r = H.api.postResponses[path];
    if (r instanceof Error) throw r;
    return r ?? {};
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => {
  class StreamError extends Error {
    status = 0;
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    StreamError,
    useChatStream: () => ({
      streaming: false,
      text: "",
      tools: [],
      approval: null,
      run: async (
        _body: Record<string, unknown>,
        onDelta: (delta: string, full: string) => void,
      ) => {
        await new Promise<void>((r) => setTimeout(r, 0));
        onDelta("streamed", "streamed");
        return H.stream.result;
      },
      abort: () => {},
    }),
  };
});

vi.mock("@/lib/useEvents", async () => {
  const React = await import("react");
  return {
    useEvents: () => {
      const [events, setEvents] = React.useState<unknown[]>([]);
      React.useEffect(() => {
        H.setters.add(setEvents);
        return () => {
          H.setters.delete(setEvents);
        };
      }, []);
      return { events, connected: true };
    },
  };
});
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({
    text: "",
    tools: [],
    phase: null,
    active: false,
    start: () => {},
    stop: () => {},
  }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false,
    reason: null,
    engine: null,
    listening: false,
    processing: false,
    transcript: "",
    interim: "",
    error: null,
    start: () => {},
    stop: () => {},
    reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false,
    enabled: false,
    speaking: false,
    enable: () => {},
    disable: () => {},
    toggle: () => {},
    speak: () => {},
    resetStream: () => {},
    speakMore: () => {},
    cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({
    byProvider: {},
    defaultProvider: "",
    loading: false,
    stale: false,
    refresh: () => {},
  }),
}));
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";

function resetApi() {
  H.api.gets.length = 0;
  H.api.posts.length = 0;
  for (const k of Object.keys(H.api.getResponses)) delete H.api.getResponses[k];
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses["/models"] = { models: [] };
  H.api.getResponses["/chat/personas"] = { personas: [] };
  H.api.getResponses["/chat/threads"] = { threads: [] };
  H.api.getResponses["/settings"] = { settings: {} };
  H.api.getResponses["/projects"] = { projects: [] };
  H.api.getResponses["/agents/mentionable"] = { agents: [] };
  H.api.getResponses["/skills"] = { skills: [] };
  H.api.getResponses["/workflows"] = { workflows: [] };
  H.api.getResponses["/tools"] = { tools: [] };
  H.api.getResponses["/undo?session_id=chat"] = { actions: [] };
  H.api.getResponses["/chat/approvals/pending"] = { approvals: [] };
  H.stream.result = { reply: "streamed reply" };
}

beforeEach(() => {
  resetApi();
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function typeAndSend(text: string) {
  const box = await screen.findByPlaceholderText(/Message Iron Jarvis/);
  fireEvent.change(box, { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

/** Escalate a turn into session s1 and wait until the page is awaiting it. */
async function escalateIntoS1() {
  H.stream.result = { reply: "", escalate: true, escalateReason: "needs the agent" };
  H.api.postResponses["/sessions"] = { id: "s1", status: "running" };
  H.api.getResponses["/sessions/s1"] = { session: { id: "s1", status: "running" } };
  render(<ChatPage />);
  await typeAndSend("rename the files in this folder");
  await waitFor(() => expect(H.api.posts.some((p) => p.path === "/sessions")).toBe(true));
  // The page polls /sessions/s1 every 1.5 s while awaiting — proof it is waiting.
  await waitFor(
    () => expect(H.api.gets.filter((g) => g === "/sessions/s1").length).toBeGreaterThan(0),
    { timeout: 4000 },
  );
}

const ask = (id: string, n: number) => ({
  id: `e${n}`,
  type: "approval.requested",
  session_id: "s1",
  payload: { approval_id: id, tool: "rename_file", args: { source: `${n}.pdf` }, timeout_s: 300 },
  created_at: "2026-09-04T00:00:00",
});

const resolved = (id: string, n: number, decision = "once") => ({
  id: `e${n}`,
  type: "approval.resolved",
  session_id: "s1",
  payload: { approval_id: id, tool: "rename_file", decision },
});

describe("wave 1 A1 — a batch of parallel asks on the chat page", () => {
  it("two approval.requested events for the awaited session render TWO cards", async () => {
    await escalateIntoS1();
    // Newest-first, as useEvents delivers them (the runtime publishes one per
    // call inside the gather — session_7e56 published 4-5 per turn).
    await act(async () => H.emit([ask("apr_2", 2), ask("apr_1", 1)]));
    const cards = await screen.findAllByRole("button", { name: /Allow once/ });
    expect(cards.length, "one card per pending ask").toBe(2);
    // Oldest ask first, so the order the run asked in is the order shown.
    const dialogs = screen.getAllByRole("alertdialog");
    expect(dialogs[0].textContent).toContain("1.pdf");
    expect(dialogs[1].textContent).toContain("2.pdf");
  });

  it("answering the newest ask leaves the older, still-pending one visible", async () => {
    await escalateIntoS1();
    await act(async () => H.emit([ask("apr_2", 2), ask("apr_1", 1)]));
    await screen.findAllByRole("button", { name: /Allow once/ });
    // apr_2 resolved (the user clicked, or the bell did); apr_1 is STILL pending.
    await act(async () =>
      H.emit([resolved("apr_2", 3), ask("apr_2", 2), ask("apr_1", 1)]),
    );
    await waitFor(() => {
      expect(
        screen.queryAllByRole("button", { name: /Allow once/ }).length,
        "apr_1 still needs an answer",
      ).toBe(1);
    });
    expect(screen.getByRole("alertdialog").textContent).toContain("1.pdf");
  });

  it("once every ask is resolved, no card remains — a resolve is never lost", async () => {
    await escalateIntoS1();
    await act(async () => H.emit([ask("apr_2", 2), ask("apr_1", 1)]));
    await screen.findAllByRole("button", { name: /Allow once/ });
    await act(async () =>
      H.emit([
        resolved("apr_1", 4, "timeout"),
        resolved("apr_2", 3),
        ask("apr_2", 2),
        ask("apr_1", 1),
      ]),
    );
    await waitFor(() => {
      expect(screen.queryAllByRole("button", { name: /Allow once/ }).length).toBe(0);
    });
  });

  it("a card's answer posts to the shared /chat/approvals/{id} route with THAT id", async () => {
    await escalateIntoS1();
    await act(async () => H.emit([ask("apr_2", 2), ask("apr_1", 1)]));
    const buttons = await screen.findAllByRole("button", { name: /Allow once/ });
    fireEvent.click(buttons[1]); // the second card is apr_2 (oldest first)
    await waitFor(() =>
      expect(
        H.api.posts.some(
          (p) => p.path === "/chat/approvals/apr_2" && p.body.decision === "once",
        ),
      ).toBe(true),
    );
    // Clicking one card never answers the other.
    expect(H.api.posts.some((p) => p.path === "/chat/approvals/apr_1")).toBe(false);
  });
});
