/**
 * Next leaps 16-a (build-out plan R2.11, idea 16): search by meaning, offered
 * with what it costs here.
 *
 * Pinned here:
 *  - lib/meaningSearch reads `GET /memory/meaning` as untrusted input and
 *    names only the setting's own two decision routes for Improvements;
 *  - the card under a reply (`MeaningSearchOffer`): which model, what it
 *    costs here (the daemon's measured words), Turn it on as the ONE main
 *    button, Not now, Use another model (the list, each with its cost); it
 *    draws nothing once the offer was answered anywhere, or for an older
 *    daemon; with nothing found it says so and offers Get a model, Use a
 *    model server you run and, last, the terminal line;
 *  - the plain row (`MeaningSearchRow`, File Search and Settings): how it
 *    stands, Choose a search model / Use another model / Turn off, the model
 *    in use marked; never a Turn it on button of its own;
 *  - while a look for models runs it reads again, a bounded number of times;
 *  - the real chat page: a reply whose file_search step carried the offer
 *    (its own frame) draws the card and SAVES `meaningOffer` on the reply;
 *    a reply without it draws none.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      gets: [] as string[],
      posts: [] as { path: string; body: unknown }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: {
      tools: [] as unknown[],
      version: 0,
      listeners: new Set<() => void>(),
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  flattenDetail: (d: unknown) => String(d),
  get: async (path: string) => {
    H.api.gets.push(path);
    const key = path.startsWith("/memory/meaning") ? "/memory/meaning" : path;
    const r = H.api.getResponses[key];
    if (typeof r === "function") return (r as () => unknown)();
    if (r instanceof Error) throw r;
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body: unknown) => {
    H.api.posts.push({ path, body });
    const r = H.api.postResponses[path];
    if (r instanceof Error) throw r;
    return r ?? {};
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/useChatStream")>();
  const React = await import("react");
  const subscribe = (cb: () => void) => {
    H.stream.listeners.add(cb);
    return () => {
      H.stream.listeners.delete(cb);
    };
  };
  return {
    ...real,
    StreamError: H.FakeStreamError,
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    streamSSE: async function* () {},
    useChatStream: () => {
      React.useSyncExternalStore(subscribe, () => H.stream.version);
      return {
        streaming: false,
        text: "",
        tools: H.stream.tools,
        approval: null,
        run: async (_body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
          await new Promise<void>((r) => setTimeout(r, 0));
          onDelta("I searched your folders.", "I searched your folders.");
          return { reply: "I searched your folders." };
        },
        abort: () => {},
      };
    },
  };
});
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
const DAEMON = {
  online: true,
  checking: false,
  health: { default_provider: "mock", default_model: "mock-1", providers: [] },
  refresh: () => {},
};
vi.mock("@/lib/daemon", () => ({ useDaemon: () => DAEMON }));
const MODELS = {
  models: [{ provider: "claude-cli", model: "opus", label: "Opus 5.5", available: true }],
  usable: [{ provider: "claude-cli", model: "opus", label: "Opus 5.5", available: true }],
  loading: false,
};
vi.mock("@/lib/useModels", () => ({ useModels: () => MODELS }));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div data-testid="tree" /> }));

import ChatPage from "@/app/chat/page";
import { MeaningSearchOffer, MeaningSearchRow } from "@/components/memory/MeaningSearch";
import { sseEventFrom, upsertTool, type ToolCard } from "@/lib/useChatStream";
import { isDecisionPath } from "@/lib/improvements";
import {
  LOOK_REREADS_MAX,
  NOT_NOW_SAID,
  OFF_WORDS,
  decodeMeaning,
  extractMeaningOffer,
} from "@/lib/meaningSearch";

const CHOICE = "ollama:nomic-embed-text:latest @ http://127.0.0.1:11434";
const LAN = "openai:text-embedding-nomic-embed-text-v1.5 @ http://192.168.1.40:1234";
const COST = "Uses about 300 MB of memory while it works. Already on your disk.";

function candidate(over: Record<string, unknown> = {}) {
  return {
    choice: CHOICE, model: "nomic-embed-text:latest", server: "Ollama on this PC", where_words: "on this PC",
    this_pc: true, cost_words: COST, works: true, ...over,
  };
}

/** The daemon's view (memory/meaning.view), as the wire carries it. */
function wire(over: Record<string, unknown> = {}) {
  const lan = candidate({
    choice: LAN, model: "text-embedding-nomic-embed-text-v1.5", server: "Studio PC",
    where_words: "on Studio PC (your network)", this_pc: false,
    cost_words: "Not measured: models like this usually need about their file size in memory.",
  });
  return {
    state: "off", value: null, on: false, words: OFF_WORDS, offer: true, best: candidate(),
    candidates: [candidate(), lan], looking: false, none_found: "No search model was found on this PC or your network.",
    terminal_line: "ollama pull nomic-embed-text", ...over,
  };
}

function frameCard(data: Record<string, unknown> | undefined, name = "file_search", ok = true): ToolCard {
  const ev = sseEventFrom("tool_call", { id: "c1", name, status: "finished", ok, output: "…", ...(data ? { data } : {}) });
  return upsertTool([], ev as Parameters<typeof upsertTool>[1])[0];
}

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.tools = [];
  H.api.postResponses = { "/documents/folder-check": { problem: null, checked: true } };
  H.api.getResponses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
    "/memory/meaning": wire(),
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

/* ----------------------------------------------------------- lib/meaningSearch */

describe("the daemon's answer, read as untrusted input", () => {
  it("decodes the view and refuses what is not one", () => {
    const v = decodeMeaning(wire())!;
    expect(v.state).toBe("off");
    expect(v.best?.costWords).toBe(COST);
    expect(v.candidates.map((c) => c.model)).toEqual(["nomic-embed-text:latest", "text-embedding-nomic-embed-text-v1.5"]);
    expect(decodeMeaning({ state: "maybe" })).toBeNull();
    expect(decodeMeaning({ results: [] })).toBeNull(); // an older daemon
    const long = decodeMeaning(wire({ best: candidate({ model: "x".repeat(500) }) }))!;
    expect(long.best?.model.length).toBe(120);
    expect(decodeMeaning(wire({ on: true, value: CHOICE }))?.current).toBe(CHOICE);
    expect(decodeMeaning(wire({ on: false, value: CHOICE }))?.current).toBe("");
  });

  it("Improvements may press only the setting's own two routes", () => {
    expect(isDecisionPath("/memory/meaning/choose")).toBe(true);
    expect(isDecisionPath("/memory/meaning/decline")).toBe(true);
    expect(isDecisionPath("/memory/meaning/off")).toBe(false);
    expect(isDecisionPath("/memory/meaning/choose/../../settings")).toBe(false);
    expect(isDecisionPath("/memory/meaning")).toBe(false);
  });

  it("only a finished file_search step that said so carries the offer", () => {
    expect(extractMeaningOffer([frameCard({ meaning_offer: true })])).toBe(true);
    expect(extractMeaningOffer([frameCard({ meaning_offer: false })])).toBe(false);
    expect(extractMeaningOffer([frameCard({ meaning_offer: "yes" })])).toBe(false);
    expect(extractMeaningOffer([frameCard({ meaning_offer: true }, "read_file")])).toBe(false);
    expect(extractMeaningOffer([frameCard(undefined)])).toBe(false);
    expect(frameCard({ meaning_offer: true }).meaningOffer).toBe(true);
    // Its own checks (review note): a step that failed, is still running, or
    // is another tool's never carries it, whatever its card says.
    expect(extractMeaningOffer([frameCard({ meaning_offer: true }, "file_search", false)])).toBe(false);
    expect(extractMeaningOffer([{ name: "file_search", status: "running", ok: true, meaningOffer: true }])).toBe(false);
    expect(extractMeaningOffer([{ name: "read_file", status: "done", ok: true, meaningOffer: true }])).toBe(false);
    expect(extractMeaningOffer([{ name: "file_search", status: "done", ok: true, meaningOffer: true }])).toBe(true);
  });
});

/* -------------------------------------------------------------- the card */

describe("the card under the reply", () => {
  it("says which model and what it costs here, with one main button", async () => {
    render(<MeaningSearchOffer />);
    const card = await screen.findByTestId("meaning-offer");
    expect(card).toHaveTextContent(OFF_WORDS);
    expect(within(card).getByTestId("meaning-offer-model")).toHaveTextContent("nomic-embed-text:latest is already on this PC.");
    expect(within(card).getByTestId("meaning-offer-cost")).toHaveTextContent(COST);
    expect(card.querySelectorAll(".btn-accent")).toHaveLength(1);
    expect(within(card).getByTestId("meaning-offer-on")).toHaveTextContent("Turn it on");
    expect(within(card).getByTestId("meaning-offer-not-now")).toHaveTextContent("Not now");
    expect(within(card).getByTestId("meaning-offer-another")).toHaveTextContent("Use another model");
  });

  it("Turn it on presses the setting's own route with that model, and says what the route said", async () => {
    H.api.postResponses["/memory/meaning/choose"] = { ok: true, note: "Search by meaning: on, with nomic-embed-text:latest on this PC." };
    render(<MeaningSearchOffer />);
    fireEvent.click(await screen.findByTestId("meaning-offer-on"));
    await waitFor(() =>
      expect(screen.getByTestId("meaning-offer-said")).toHaveTextContent("Search by meaning: on, with nomic-embed-text:latest on this PC."),
    );
    expect(H.api.posts).toEqual([{ path: "/memory/meaning/choose", body: { choice: CHOICE } }]);
    expect(screen.queryByTestId("meaning-offer")).toBeNull();
  });

  it("Not now is an answer, and says it is final", async () => {
    render(<MeaningSearchOffer />);
    fireEvent.click(await screen.findByTestId("meaning-offer-not-now"));
    await waitFor(() => expect(screen.getByTestId("meaning-offer-said")).toHaveTextContent(NOT_NOW_SAID));
    expect(H.api.posts.map((p) => p.path)).toEqual(["/memory/meaning/decline"]);
  });

  it("Use another model lists every model found with its cost, and measures them all", async () => {
    H.api.postResponses["/memory/meaning/choose"] = { ok: true, note: "Search by meaning: on." };
    render(<MeaningSearchOffer />);
    fireEvent.click(await screen.findByTestId("meaning-offer-another"));
    await waitFor(() => expect(screen.getAllByTestId("meaning-model")).toHaveLength(2));
    expect(H.api.gets).toContain("/memory/meaning?all=1");
    const lan = screen.getAllByTestId("meaning-model")[1];
    expect(lan).toHaveTextContent("on Studio PC (your network)");
    expect(within(lan).getByTestId("meaning-cost")).toHaveTextContent(/^Not measured:/);
    fireEvent.click(within(lan).getByTestId("meaning-use"));
    await waitFor(() => expect(H.api.posts).toEqual([{ path: "/memory/meaning/choose", body: { choice: LAN } }]));
  });

  it("a model that did not answer cannot be chosen", async () => {
    H.api.getResponses["/memory/meaning"] = wire({
      candidates: [candidate(), candidate({ choice: LAN, model: "broken-embed", works: false, cost_words: "It did not answer here." })],
    });
    render(<MeaningSearchOffer />);
    fireEvent.click(await screen.findByTestId("meaning-offer-another"));
    await waitFor(() => expect(screen.getAllByTestId("meaning-use")).toHaveLength(2));
    expect(screen.getAllByTestId("meaning-use")[1]).toBeDisabled();
  });

  it("a model whose cost is not measured yet waits for its measure, and the list reads again until it has one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const measuring = candidate({
      choice: LAN, model: "mxbai-embed-large", server: "Studio PC", where_words: "on Studio PC (your network)",
      this_pc: false, works: null, cost_words: "Its cost here is being measured.",
    });
    let measured = false;
    H.api.getResponses["/memory/meaning"] = () =>
      wire({
        candidates: [
          candidate(),
          measured
            ? { ...measuring, works: true, cost_words: "Uses about 700 MB of memory on that computer while it works." }
            : measuring,
        ],
      });
    H.api.postResponses["/memory/meaning/choose"] = { ok: true, note: "Search by meaning: on." };
    render(<MeaningSearchOffer />);
    fireEvent.click(await screen.findByTestId("meaning-offer-another"));
    await waitFor(() => expect(screen.getAllByTestId("meaning-use")).toHaveLength(2));
    const use = () => screen.getAllByTestId("meaning-use")[1];
    expect(use()).toBeDisabled(); // no Yes with no cost on screen (G10)
    expect(within(screen.getAllByTestId("meaning-model")[1]).getByTestId("meaning-cost")).toHaveTextContent(
      "Its cost here is being measured.",
    );
    fireEvent.click(use());
    expect(H.api.posts).toEqual([]);
    const listReads = () => H.api.gets.filter((p) => p === "/memory/meaning?all=1").length;
    const before = listReads();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100);
    });
    expect(listReads()).toBe(before + 1); // not looking, but a cost is missing: it reads again
    measured = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100);
    });
    await waitFor(() => expect(use()).not.toBeDisabled());
    expect(within(screen.getAllByTestId("meaning-model")[1]).getByTestId("meaning-cost")).toHaveTextContent(
      "Uses about 700 MB of memory on that computer while it works.",
    );
    const settled = listReads();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(listReads()).toBe(settled); // every cost measured: no more reads
    fireEvent.click(use());
    await waitFor(() => expect(H.api.posts).toEqual([{ path: "/memory/meaning/choose", body: { choice: LAN } }]));
  });

  it("a refused press shows the route's own words", async () => {
    H.api.postResponses["/memory/meaning/choose"] = new H.FakeApiError(
      "Search by meaning: nomic-embed-text:latest did not answer on this PC (connection refused), so search by meaning was not changed.",
      400,
    );
    render(<MeaningSearchOffer />);
    fireEvent.click(await screen.findByTestId("meaning-offer-on"));
    await waitFor(() => expect(screen.getByTestId("meaning-offer-refused")).toHaveTextContent(/did not answer on this PC/));
  });

  it("draws nothing once the offer was answered anywhere, or for an older daemon", async () => {
    for (const state of ["on", "declined"]) {
      H.api.getResponses["/memory/meaning"] = wire({ state, offer: false });
      const { unmount } = render(<MeaningSearchOffer />);
      await act(async () => {});
      expect(screen.queryByTestId("meaning-offer")).toBeNull();
      unmount();
    }
    H.api.getResponses["/memory/meaning"] = new H.FakeApiError("unmocked", 404);
    render(<MeaningSearchOffer />);
    await act(async () => {});
    expect(screen.queryByTestId("meaning-offer")).toBeNull();
  });

  it("with nothing found it says so and offers the ways to get one, the terminal line last", async () => {
    H.api.getResponses["/memory/meaning"] = wire({ offer: false, best: null, candidates: [] });
    render(<MeaningSearchOffer />);
    const none = await screen.findByTestId("meaning-none");
    expect(none).toHaveTextContent("No search model was found on this PC or your network.");
    expect(within(none).getByTestId("meaning-get-model").getAttribute("href")).toBe("/fleet?view=models");
    expect(within(none).getByTestId("meaning-own-server").getAttribute("href")).toBe("/fleet");
    expect(none.textContent?.indexOf("ollama pull nomic-embed-text")).toBeGreaterThan(none.textContent!.indexOf("Use a model server you run"));
    expect(screen.queryByTestId("meaning-offer-on")).toBeNull();
  });

  it("reads again while a look for models runs, a bounded number of times", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    H.api.getResponses["/memory/meaning"] = wire({ looking: true, best: null, candidates: [], offer: false });
    render(<MeaningSearchOffer />);
    for (let i = 0; i < LOOK_REREADS_MAX + 5; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3100);
      });
    }
    const reads = H.api.gets.filter((p) => p.startsWith("/memory/meaning")).length;
    expect(reads).toBe(1 + LOOK_REREADS_MAX);
  });
});

/* -------------------------------------------------------------- the row */

describe("the plain settings row (File Search, Settings)", () => {
  it("off: how it stands and Choose a search model, never an offer of its own", async () => {
    render(<MeaningSearchRow />);
    expect(await screen.findByTestId("meaning-row-words")).toHaveTextContent(OFF_WORDS);
    expect(screen.queryByText("Turn it on")).toBeNull();
    expect(screen.queryByTestId("meaning-off")).toBeNull();
    fireEvent.click(screen.getByTestId("meaning-choose"));
    expect(screen.getByTestId("meaning-choose")).toHaveTextContent("Choose a search model");
    await waitFor(() => expect(screen.getAllByTestId("meaning-model")).toHaveLength(2));
  });

  it("on: the model in use is marked, Use another model and Turn off", async () => {
    H.api.getResponses["/memory/meaning"] = wire({
      state: "on", on: true, value: CHOICE, offer: false, words: "Search by meaning: on, with nomic-embed-text:latest on this PC.",
    });
    H.api.postResponses["/memory/meaning/off"] = { ok: true, note: "Search by meaning is off. Searches match words." };
    render(<MeaningSearchRow />);
    expect(await screen.findByTestId("meaning-row-words")).toHaveTextContent("on, with nomic-embed-text:latest on this PC");
    fireEvent.click(screen.getByTestId("meaning-choose"));
    expect(screen.getByTestId("meaning-choose")).toHaveTextContent("Use another model");
    await waitFor(() => expect(screen.getByTestId("meaning-in-use")).toHaveTextContent("In use"));
    expect(screen.getAllByTestId("meaning-use")).toHaveLength(1); // only the other model
    fireEvent.click(screen.getByTestId("meaning-off"));
    await waitFor(() => expect(screen.getByTestId("meaning-note")).toHaveTextContent("Search by meaning is off."));
    expect(H.api.posts.map((p) => p.path)).toEqual(["/memory/meaning/off"]);
  });
});

/* ---------------------------------------------------------- the chat page */

async function box(): Promise<HTMLTextAreaElement> {
  return (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
}

const savedReply = () => {
  const saved = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/")).at(-1)?.body;
  const lines = (saved?.messages ?? []) as { role: string; meaningOffer?: boolean }[];
  return lines.filter((m) => m.role === "assistant").at(-1);
};

describe("the real chat page", () => {
  it("a reply whose by-meaning search carried the offer draws the card and saves it", async () => {
    H.stream.tools = [frameCard({ meaning_offer: true })];
    render(<ChatPage />);
    const input = await box();
    fireEvent.change(input, { target: { value: "find the angry email about shipping" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(await screen.findByTestId("meaning-offer")).toHaveTextContent(COST);
    await waitFor(() => expect(savedReply()?.meaningOffer).toBe(true));
  });

  it("a reply without it draws no card and saves nothing of it", async () => {
    H.stream.tools = [frameCard({ meaning_offer: false })];
    render(<ChatPage />);
    const input = await box();
    fireEvent.change(input, { target: { value: "find the angry email about shipping" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByText("I searched your folders.");
    await waitFor(() => expect(savedReply()).toBeTruthy());
    expect(savedReply()?.meaningOffer).toBeUndefined();
    expect(screen.queryByTestId("meaning-offer")).toBeNull();
    expect(H.api.gets.some((p) => p.startsWith("/memory/meaning"))).toBe(false);
  });
});
