/**
 * AN review of v1.450.0 Wave D lane D3, item 38, on the REAL chat page:
 *
 *  - Escape with NO menu open still stops a running reply (the capture-phase
 *    menu handler must only take Escape while a menu is open);
 *  - with a menu open, the same Escape closes it and stops nothing;
 *  - a question that took the composer's place (DockAsk) is still declined by
 *    Escape even when a composer menu had been left open behind it (the
 *    capture handler swallowed that Escape);
 *  - a 40-line paste is sent whole and the fold is a view.
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
      aborts: 0,
      approval: null as null | Record<string, unknown>,
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
    get streaming() {
      return H.stream.release !== null;
    },
    text: "",
    tools: [],
    get approval() {
      return H.stream.approval;
    },
    run: (body: Record<string, unknown>) => {
      H.stream.bodies.push(body);
      return new Promise<Record<string, unknown>>((res) => {
        H.stream.release = res;
      });
    },
    abort: () => {
      H.stream.aborts += 1;
    },
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
  H.stream.aborts = 0;
  H.stream.approval = null;
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

async function running() {
  render(<ChatPage />);
  const el = await composer();
  type(el, "hello");
  fireEvent.keyDown(el, { key: "Enter" });
  await waitFor(() => expect(H.stream.release).not.toBeNull());
  return (await screen.findByPlaceholderText(/Steer Jarvis mid-turn/)) as HTMLTextAreaElement;
}

// The page's Stop posts the turn's stop by name (v1.312.0): that POST is the
// signal (the harness's stream.abort also runs when the saved chat is named).
const stopPosts = () => H.api.posts.filter((p) => /^\/chat\/turns\/[^/]+\/stop$/.test(p.path)).length;

describe("review: Escape stops a reply only when no composer menu is open", () => {
  it("no menu open: Escape in the box stops the running reply", async () => {
    const box = await running();
    expect(stopPosts()).toBe(0);
    fireEvent.keyDown(box, { key: "Escape" });
    await waitFor(() => expect(stopPosts()).toBe(1));
  });

  it("a menu open: the same Escape closes it and does not stop the reply", async () => {
    const box = await running();
    fireEvent.click(screen.getByTestId("composer-tools"));
    await screen.findByTestId("composer-tools-menu");
    fireEvent.keyDown(box, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("composer-tools-menu")).toBeNull());
    expect(stopPosts()).toBe(0);
    // ...and the NEXT Escape, with no menu open, stops it.
    fireEvent.keyDown(box, { key: "Escape" });
    await waitFor(() => expect(stopPosts()).toBe(1));
  });
});

describe("review: a docked question is still declined by Escape with a menu left open behind it", () => {
  it("Escape declines the card (POST deny), even though the + menu was open when it arrived", async () => {
    H.api.postResponses["/chat/approvals/ap1"] = { ok: true };
    render(<ChatPage />);
    const el = await composer();
    const plus = screen.getByRole("button", { name: "Open the chat menu" });
    fireEvent.click(plus);
    expect(plus.getAttribute("aria-expanded")).toBe("true");
    H.stream.approval = {
      id: "ap1",
      tool: "write_file",
      args: { path: "notes.txt", content: "hi" },
      mode: "ask",
    };
    type(el, "save it");
    fireEvent.keyDown(el, { key: "Enter" });
    await screen.findByTestId("dock-ask");
    // Past the card's key guard, as a person would be.
    await new Promise((r) => setTimeout(r, 700));
    const target = document.activeElement && document.activeElement !== document.body ? document.activeElement : document.body;
    fireEvent.keyDown(target, { key: "Escape" });
    await waitFor(() =>
      expect(
        H.api.posts.some((p) => p.path === "/chat/approvals/ap1" && (p.body as { decision?: string }).decision === "deny"),
      ).toBe(true),
    );
  });
});

describe("review: the fold is a view, the message sent is whole", () => {
  it("a 40-line paste, typed into and sent, keeps every line", async () => {
    render(<ChatPage />);
    const el = await composer();
    const text = Array.from({ length: 40 }, (_, i) => `row ${i + 1} | value ${i * 3}`).join("\n");
    type(el, text);
    await screen.findByTestId("composer-fold");
    expect(el.value).toBe(text);
    fireEvent.keyDown(el, { key: "Enter" });
    await waitFor(() => expect(H.stream.bodies.length).toBe(1));
    const msgs = H.stream.bodies[0].messages as { role: string; content: string }[];
    expect(msgs[msgs.length - 1].content).toBe(text);
    expect(msgs[msgs.length - 1].content.split(String.fromCharCode(10))).toHaveLength(40);
  });
});
