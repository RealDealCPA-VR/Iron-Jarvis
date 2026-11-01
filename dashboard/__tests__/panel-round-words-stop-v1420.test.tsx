/**
 * v1.420.0 (C6.4) — an @-agent round says why it failed, can be stopped, and
 * never shows a raw word.
 *
 *  1. a seat that could not answer shows the daemon's PLAIN words
 *     (`error_words`) as its line, beside "couldn't answer" — not "(no
 *     reply)", never the raw error record;
 *  2. a line's `panelKind` is said through lib/panelWords: "done" and a plain
 *     message show no label, "question" reads "has a question", an unknown
 *     kind shows nothing (it used to be printed as it was);
 *  3. while a round runs the composer shows Stop; Stop tells the daemon to end
 *     the round BY NAME (`POST /chat/panel/{round_id}/stop`, the id the round
 *     was posted with), stops waiting, and leaves a "Stopped." line with no
 *     error.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    cancelled: boolean;
    constructor(message: string, status = 500, cancelled = false) {
      super(message);
      this.status = status;
      this.cancelled = cancelled;
      this.name = "ApiError";
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    const r = H.api.getResponses[path];
    return r === undefined ? {} : r;
  },
  post: async (path: string, body: Record<string, unknown>, opts?: { signal?: AbortSignal }) => {
    H.api.posts.push({ path, body });
    const r = H.api.postResponses[path];
    if (r === "hang") {
      // A round that never answers on its own: only an abort ends the wait,
      // the way lib/api answers a caller's own abort.
      return await new Promise((_, reject) => {
        opts?.signal?.addEventListener("abort", () =>
          reject(new H.FakeApiError("cancelled", 0, true)),
        );
      });
    }
    if (r instanceof Error) throw r;
    if (typeof r === "function") return (r as (b: Record<string, unknown>) => unknown)(body);
    return r ?? {};
  },
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
    run: async () => ({ reply: "x", route: null, tools_used: [], remembered: [] }),
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
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
import { panelKindWords } from "@/lib/panelWords";

const BUILDER = {
  mention: "builder",
  name: "builder",
  kind: "builtin",
  source: "builtin",
  description: "Builds things",
  healthy: true,
  delegable: true,
};
const WORDS = "Claude Code is busy. Try again shortly, or pick another model on its card in Agents.";

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [BUILDER] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function box() {
  return screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement;
}
async function catalogReady() {
  await waitFor(() => expect(box()).toBeTruthy());
  fireEvent.change(box(), { target: { value: "@bu" } });
  await screen.findByRole("option", { name: /builder/ });
  fireEvent.change(box(), { target: { value: "" } });
}
async function type(text: string) {
  fireEvent.change(box(), { target: { value: text } });
  fireEvent.keyDown(box(), { key: "Enter" });
}

describe("the kind words (lib/panelWords)", () => {
  it("never passes a raw kind through", () => {
    expect(panelKindWords("progress")).toBe("working…");
    expect(panelKindWords("pending")).toBe("working, will report back");
    expect(panelKindWords("question")).toBe("has a question");
    expect(panelKindWords("done")).toBe("");
    expect(panelKindWords("message")).toBe("");
    expect(panelKindWords("SOME_ENGINE_WORD")).toBe("");
    expect(panelKindWords(undefined)).toBe("");
  });
});

describe("a seat that could not answer says why (v1.420.0)", () => {
  it("shows the daemon's plain words, not (no reply) and not the raw error", async () => {
    H.api.postResponses["/chat/panel"] = () => ({
      mode: "panel",
      thread_id: "athr1",
      entries: [
        { who: "builtin:builder", content: "", error: "builder couldn't answer: ProviderError 503 raw", error_words: WORDS },
      ],
      spoke: ["builtin:builder"],
      skipped: [],
      unknown_mentions: [],
    });
    render(<ChatPage />);
    await catalogReady();
    await type("@builder draft the brief");
    await screen.findByText(WORDS);
    expect(screen.getByTestId("panel-error-label")).toHaveTextContent("couldn't answer");
    expect(screen.queryByText("(no reply)")).toBeNull();
    expect(document.body.textContent).not.toContain("ProviderError");
    // the saved copy carries the words too
    const saved = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/")).at(-1);
    const last = ((saved?.body.messages ?? []) as { content: string; panelError?: boolean }[]).at(-1);
    expect(last?.content).toBe(WORDS);
    expect(last?.panelError).toBe(true);
  });
});

describe("a saved line's kind is said in words (v1.420.0)", () => {
  function open(kind: string) {
    H.api.getResponses["/chat/threads"] = {
      threads: [{ id: "t9", title: "Brief", updated_at: "2026-09-20T10:00:00Z", messages: 2 }],
    };
    H.api.getResponses["/chat/threads/t9"] = {
      id: "t9",
      title: "Brief",
      messages: [
        { role: "user", content: "@hermes do it" },
        { role: "assistant", content: "Here it is.", panelWho: "remote:hermes", panelKind: kind },
      ],
    };
    window.history.replaceState({}, "", "/chat?thread=t9");
    render(<ChatPage />);
  }
  it("a finished reply shows no label", async () => {
    open("done");
    await screen.findByText("Here it is.");
    expect(screen.queryByTestId("panel-kind")).toBeNull();
    expect(screen.queryByText("done")).toBeNull();
  });
  it("a question says so in words", async () => {
    open("question");
    await screen.findByText("Here it is.");
    expect(screen.getByTestId("panel-kind")).toHaveTextContent("has a question");
  });
  it("an unknown kind shows nothing", async () => {
    open("internal_state_x");
    await screen.findByText("Here it is.");
    expect(screen.queryByTestId("panel-kind")).toBeNull();
    expect(document.body.textContent).not.toContain("internal_state_x");
  });
});

describe("Stop ends an @-round (v1.420.0)", () => {
  it("shows Stop while the round runs, ends it by name, and leaves a Stopped line", async () => {
    H.api.postResponses["/chat/panel"] = "hang";
    render(<ChatPage />);
    await catalogReady();
    await type("@builder draft the brief");
    const stop = await screen.findByRole("button", { name: "Stop" });
    const roundId = H.api.posts.find((p) => p.path === "/chat/panel")?.body.round_id as string;
    expect(roundId).toMatch(/^pr_/);
    fireEvent.click(stop);
    await screen.findByText("Stopped.");
    expect(H.api.posts.some((p) => p.path === `/chat/panel/${roundId}/stop`)).toBe(true);
    // a Stop is not a failure: no error line, and the box is free again
    expect(screen.queryByRole("alert")).toBeNull();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
  });
});
