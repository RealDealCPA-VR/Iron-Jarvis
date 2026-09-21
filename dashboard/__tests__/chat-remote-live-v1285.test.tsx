/**
 * v1.285.0 — a remote agent's lines that arrive through its inbound door land
 * in the chat they belong to.
 *
 * The daemon owns the ROOM (the panel thread @-rounds live in); the browser
 * owns the chat thread. So a remote's inbound entry is mirrored by the page:
 * on open (it may have messaged while the page was closed) and on the room's
 * `agent_thread.updated` / `remote.message` event — deduped by the entry's
 * timestamp, saved with the thread, a quiet line for `progress`, a reply with
 * a kind pill for `done`/`question`, and its files into the Files rail.
 *
 * Harness: the v1.284.0 chat-page mocks; `useEvents` here is a real hook over
 * a hoisted store so a test can push an event and watch the page react.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
    offline = false;
    partial = "";
  }
  type Ev = { id: string; type: string; payload: Record<string, unknown> };
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      gets: [] as string[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
    },
    events: {
      list: [] as Ev[],
      setters: new Set<(l: Ev[]) => void>(),
      push(e: Ev) {
        this.list = [e, ...this.list];
        for (const s of this.setters) s(this.list);
      },
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
    return r === undefined ? {} : r;
  },
  post: async () => ({}),
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));
vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async () => ({ reply: "Noted.", route: { requested: "", provider: "mock", model: "mock", reason: "default" }, tools_used: [], remembered: [] }),
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", async () => {
  const React = await import("react");
  return {
    useEvents: () => {
      const [list, setList] = React.useState(H.events.list);
      React.useEffect(() => {
        H.events.setters.add(setList);
        return () => {
          H.events.setters.delete(setList);
        };
      }, []);
      return { events: list, connected: true };
    },
  };
});
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false, reason: null, engine: null, listening: false, processing: false,
    transcript: "", interim: "", error: null, start: () => {}, stop: () => {}, reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";

const ROOM = "athr9";
const THREAD = {
  id: "t9",
  title: "Ledger",
  messages: [
    { role: "user", content: "@hermes total the ledger" },
    { role: "assistant", content: "hermes has the task and will report back here when it has something.", panelWho: "remote:hermes", panelThreadId: ROOM },
  ],
};
function room(entries: Record<string, unknown>[]) {
  return {
    id: ROOM,
    title: "Ledger",
    participants: [{ key: "remote:hermes", source: "remote", name: "hermes", role: "participant" }],
    message_count: entries.length + 2,
    updated_at: "2026-09-21T10:00:00Z",
    messages: [
      { who: "user", content: "@hermes total the ledger", at: "2026-09-21T09:59:00Z" },
      { who: "remote:hermes", content: "hermes has the task…", at: "2026-09-21T09:59:05Z", pending: true },
      ...entries,
    ],
  };
}
const PROGRESS = { who: "remote:hermes", content: "Half way through the ledger.", at: "2026-09-21T10:00:00Z", inbound: true, kind: "progress" };
const DONE = {
  who: "remote:hermes",
  content: "Done — the total is 1.2M.\n\nFiles from hermes:\n- C:\\inbox\\ledger.xlsx",
  at: "2026-09-21T10:05:00Z",
  inbound: true,
  kind: "done",
  documents: ["C:\\inbox\\ledger.xlsx"],
};

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.puts.length = 0;
  H.events.list = [];
  H.api.getResponses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [{ id: "t9", title: "Ledger", updated_at: "2026-09-21T10:00:00Z", messages: 2 }] },
    "/chat/threads/t9": THREAD,
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.history.replaceState({}, "", "/chat?thread=t9");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function saved() {
  const last = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/")).at(-1);
  return (last?.body.messages ?? []) as { content: string; panelWho?: string; panelKind?: string; panelAt?: string; documents?: string[] }[];
}

describe("a remote's inbound lines reach the chat (v1.285.0)", () => {
  it("mirrors what arrived while the page was closed — once — and saves it", async () => {
    H.api.getResponses[`/agents/threads/${ROOM}`] = room([PROGRESS, DONE]);
    render(<ChatPage />);
    // the quiet progress line and the attributed reply with its kind
    const progress = await screen.findByTestId("panel-progress");
    expect(progress).toHaveTextContent("hermes");
    expect(progress).toHaveTextContent("Half way through the ledger.");
    await screen.findByText(/Done — the total is 1\.2M\./);
    expect(screen.getByTestId("panel-kind")).toHaveTextContent("done");
    // saved with the thread, keyed by the room entry's timestamp
    await waitFor(() => expect(saved().at(-1)?.panelAt).toBe("2026-09-21T10:05:00Z"));
    const rows = saved();
    expect(rows.at(-2)?.panelKind).toBe("progress");
    expect(rows.at(-1)?.documents).toEqual(["C:\\inbox\\ledger.xlsx"]);
    // the file reached the conversation's rail
    expect((await screen.findAllByText("ledger.xlsx")).length).toBeGreaterThan(0);
    // DEDUPE: the room read again with the SAME two lines plus one NEW line —
    // wait for the new line (a positive signal set at the END of the mirror,
    // the repo's waitFor rule), then the old ones must still be there once.
    const before = saved().length;
    const LATER = { who: "remote:hermes", content: "One more thing.", at: "2026-09-21T10:06:00Z", inbound: true, kind: "message" };
    H.api.getResponses[`/agents/threads/${ROOM}`] = room([PROGRESS, DONE, LATER]);
    await act(async () => {
      H.events.push({ id: "e1", type: "agent_thread.updated", payload: { thread_id: ROOM, who: "remote:hermes", entries: 5 } });
    });
    await screen.findByText("One more thing.");
    await waitFor(() => expect(saved().length).toBe(before + 1));
    expect(screen.getAllByTestId("panel-progress")).toHaveLength(1);
    expect(screen.getAllByText(/Done — the total is 1\.2M\./)).toHaveLength(1);
    expect(saved().filter((m) => m.panelAt === "2026-09-21T10:00:00Z")).toHaveLength(1);
  });

  it("a room event for OUR room appends the new line live; another room's does not", async () => {
    H.api.getResponses[`/agents/threads/${ROOM}`] = room([]);
    render(<ChatPage />);
    await screen.findByText(/will report back here/);
    expect(screen.queryByTestId("panel-progress")).toBeNull();
    // another room's remote line: ignored (no fetch of ours beyond the open)
    const reads = () => H.api.gets.filter((p) => p === `/agents/threads/${ROOM}`).length;
    const atOpen = reads();
    await act(async () => {
      H.events.push({ id: "e0", type: "agent_thread.updated", payload: { thread_id: "athr_other", who: "remote:hermes", entries: 1 } });
    });
    expect(reads()).toBe(atOpen);
    // ours: the room now holds a progress line → it appears
    H.api.getResponses[`/agents/threads/${ROOM}`] = room([PROGRESS]);
    await act(async () => {
      H.events.push({ id: "e1", type: "remote.message", payload: { thread_id: ROOM, agent: "hermes", kind: "progress" } });
    });
    expect(await screen.findByTestId("panel-progress")).toHaveTextContent("Half way through the ledger.");
    // a round entry (not inbound) from a remote does not get mirrored twice —
    // the round's own response already put it on screen
    expect(screen.getAllByText(/will report back here/)).toHaveLength(1);
  });
});
