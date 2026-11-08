/**
 * v1.450.0 — Wave D, lane D3, item 38, on the REAL chat page (the steer
 * harness of chat-steer-v1278): a long paste folds and is sent whole; the box
 * says what Enter does mid-reply; Escape closes each composer menu (+, tools,
 * project, model, permission, @ and /) and never also stops the reply.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: {
      bodies: [] as Record<string, unknown>[],
      /** Set by the mock while a run is pending; the test resolves the turn. */
      release: null as null | ((r: Record<string, unknown>) => void),
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    const r = H.api.postResponses[path];
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
    run: (body: Record<string, unknown>) => {
      H.stream.bodies.push(body);
      return new Promise<Record<string, unknown>>((res) => {
        H.stream.release = res;
      });
    },
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

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.release = null;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
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
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const composer = async () => (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
const type = (el: HTMLTextAreaElement, text: string) => fireEvent.change(el, { target: { value: text } });
const lines = (n: number) => Array.from({ length: n }, (_, i) => `line ${i + 1}`).join("\n");

describe("38: a long pasted message folds; the text sent is never cut", () => {
  it("past 15 lines the box folds with Show all N lines; Show all / Show less; Send sends every line", async () => {
    render(<ChatPage />);
    const el = await composer();
    type(el, lines(15));
    expect(screen.queryByTestId("composer-fold")).toBeNull(); // 15 lines: not folded
    type(el, lines(40));
    const fold = await screen.findByTestId("composer-fold");
    expect(fold.textContent).toContain("Long message: 40 lines, all of them are sent.");
    const toggle = screen.getByTestId("composer-fold-toggle");
    expect(toggle.textContent).toBe("Show all 40 lines");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(el.style.maxHeight).toBe("160px");
    fireEvent.click(toggle);
    expect(toggle.textContent).toBe("Show less");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(Number.parseInt(el.style.maxHeight, 10)).toBeGreaterThan(160);
    fireEvent.click(toggle);
    expect(el.style.maxHeight).toBe("160px");
    // The fold is a view: the message sent holds all 40 lines.
    fireEvent.keyDown(el, { key: "Enter" });
    await waitFor(() => expect(H.stream.bodies.length).toBe(1));
    const msgs = H.stream.bodies[0].messages as { role: string; content: string }[];
    expect(msgs[msgs.length - 1].content).toBe(lines(40));
  });
});

describe("38: while a reply runs, the box says what Enter does", () => {
  it("the placeholder names the note and Ctrl+Enter", async () => {
    render(<ChatPage />);
    const el = await composer();
    type(el, "hello");
    fireEvent.keyDown(el, { key: "Enter" });
    await waitFor(() => expect(H.stream.release).not.toBeNull());
    const busy = (await screen.findByPlaceholderText(/Steer Jarvis mid-turn/)) as HTMLTextAreaElement;
    expect(busy.placeholder).toBe(
      "Steer Jarvis mid-turn: Enter sends a note to this reply; Ctrl+Enter queues a message",
    );
  });
});

describe("38: Escape closes every composer menu, one at a time, and stops nothing", () => {
  async function ready() {
    render(<ChatPage />);
    return composer();
  }

  it("the + menu", async () => {
    await ready();
    const plus = screen.getByRole("button", { name: "Open the chat menu" });
    fireEvent.click(plus);
    expect(plus.getAttribute("aria-expanded")).toBe("true");
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    await waitFor(() => expect(plus.getAttribute("aria-expanded")).toBe("false"));
  });

  it("the tools chip's menu", async () => {
    await ready();
    fireEvent.click(screen.getByTestId("composer-tools"));
    expect(await screen.findByTestId("composer-tools-menu")).toBeInTheDocument();
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("composer-tools-menu")).toBeNull());
  });

  it("the project menu", async () => {
    await ready();
    const chip = screen.getAllByRole("button", { name: "Switch project" })[0];
    fireEvent.click(chip);
    expect(chip.getAttribute("aria-expanded")).toBe("true");
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    await waitFor(() => expect(chip.getAttribute("aria-expanded")).toBe("false"));
  });

  it("the model menu, from a row as well as from its find box", async () => {
    H.api.getResponses["/models"] = {
      models: [
        { provider: "claude-cli", model: "claude-opus-5-5", label: "Opus 5.5", available: true },
        { provider: "codex-cli", model: "gpt-6", label: "GPT-6", available: true },
      ],
    };
    await ready();
    const chip = (await screen.findByTestId("model-chip-words")).closest("button") as HTMLButtonElement;
    fireEvent.click(chip);
    const menu = await screen.findByTestId("model-menu");
    const row = Array.from(menu.querySelectorAll("button")).find((b) => !b.disabled) as HTMLButtonElement;
    row.focus();
    fireEvent.keyDown(row, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("model-menu")).toBeNull());
    fireEvent.click(chip);
    fireEvent.keyDown(await screen.findByTestId("model-filter"), { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("model-menu")).toBeNull());
  });

  it("the permission menu", async () => {
    await ready();
    const chip = screen.getByTestId("permission-chip");
    fireEvent.click(chip);
    const menu = await screen.findByRole("menu");
    fireEvent.keyDown(menu, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
  });

  it("the @ and / menus", async () => {
    H.api.getResponses["/skills"] = { skills: [{ name: "draft-email", description: "d" }] };
    const el = await ready();
    type(el, "/dr");
    fireEvent.keyUp(el);
    expect(await screen.findByText("draft-email")).toBeInTheDocument();
    fireEvent.keyDown(el, { key: "Escape" });
    await waitFor(() => expect(screen.queryByText("draft-email")).toBeNull());
  });

  it("an Escape that closes a menu never also stops the running reply", async () => {
    const el = await ready();
    type(el, "hello");
    fireEvent.keyDown(el, { key: "Enter" });
    await waitFor(() => expect(H.stream.release).not.toBeNull());
    fireEvent.click(screen.getByTestId("composer-tools"));
    await screen.findByTestId("composer-tools-menu");
    const busy = (await screen.findByPlaceholderText(/Steer Jarvis mid-turn/)) as HTMLTextAreaElement;
    // The harness's stream never "streams", so Stop itself is invisible here:
    // what is pinned is that the Escape is CAUGHT before the box (whose
    // Escape is Stop) or anything else on the page ever sees it.
    const reached: string[] = [];
    const boxSaw = (e: KeyboardEvent) => e.key === "Escape" && reached.push("box");
    const pageSaw = (e: KeyboardEvent) => e.key === "Escape" && reached.push("page");
    busy.addEventListener("keydown", boxSaw);
    window.addEventListener("keydown", pageSaw);
    try {
      fireEvent.keyDown(busy, { key: "Escape" });
    } finally {
      busy.removeEventListener("keydown", boxSaw);
      window.removeEventListener("keydown", pageSaw);
    }
    await waitFor(() => expect(screen.queryByTestId("composer-tools-menu")).toBeNull());
    expect(reached).toEqual([]);
    expect(H.api.posts.some((p) => /\/stop$/.test(p.path))).toBe(false);
  });
});
