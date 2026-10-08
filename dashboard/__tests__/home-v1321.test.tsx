/**
 * Calm UI redesign S8 — the home IS a new chat (brief T1, AUDIT §4.1/Q1).
 *
 * `/` renders the chat surface: the composer focused, no dashboard, stat
 * tiles or module grid. Status lives in Everything › Status; when something
 * needs the user, ONE conditional line above the composer says so (AUDIT
 * §4.5 R2). The mock-downgrade warning moved to the shell.
 *
 * Harness: the edit-and-resend page harness.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

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
      replies: [] as string[],
      extras: [] as Record<string, unknown>[],
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
    run: async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
      H.stream.bodies.push(body);
      await new Promise<void>((r) => setTimeout(r, 0));
      const reply = H.stream.replies.shift() ?? "done";
      const extra = H.stream.extras.shift() ?? {};
      onDelta(reply, reply);
      return { reply, ...extra };
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

import HomePage from "@/app/page";
import { homeLineItems } from "@/components/chat/HomeLine";

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.replies.length = 0;
  H.stream.extras.length = 0;
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

describe("T1 — the home is a chat (unit half; e2e/t1 runs it in a browser)", () => {
  it("/ renders the composer, focused, and no dashboard, stat tiles or module grid", async () => {
    render(<HomePage />);
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    await waitFor(() => expect(document.activeElement).toBe(box));
    expect(screen.queryByTestId("app-desk")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Overview" })).toBeNull();
    expect(screen.queryByText("Problems today")).toBeNull();
    expect(document.querySelector("[data-testid^='group-']")).toBeNull();
  });

  it("the home and /chat are the same surface", async () => {
    const src = readFileSync(join(__dirname, "..", "app", "page.tsx"), "utf-8");
    expect(src).toMatch(/import ChatPage from "\.\/chat\/page"/);
    expect(src).toMatch(/return <ChatPage \/>/);
  });
});

describe("the one conditional line", () => {
  it("orders by urgency and folds the rest", () => {
    expect(homeLineItems({ interrupted: 2, failingLoops: 1, failingPacks: 0, running: 3, waiting: 1 }).map((i) => i.kind)).toEqual([
      "interrupted",
      "failing",
      "working",
    ]);
    expect(homeLineItems({ interrupted: 0, failingLoops: 0, failingPacks: 0, running: 0, waiting: 0 })).toEqual([]);
    expect(homeLineItems({ interrupted: 0, failingLoops: 0, failingPacks: 0, running: 1, waiting: 1 })[0].text).toBe(
      "1 task running · 1 waiting for you",
    );
    expect(homeLineItems({ interrupted: 1, failingLoops: 0, failingPacks: 0, running: 0, waiting: 0 })[0].text).toBe(
      "1 job was interrupted by the last restart.",
    );
  });

  it("shows ONE line — the most urgent — with +N more, opening Everything › Status", async () => {
    H.api.getResponses["/ui/status-line"] = { interrupted: 1, failing_loops: 2, failing_packs: 0, running: 1 };
    H.api.getResponses["/chat/approvals/pending"] = { approvals: [] };
    render(<HomePage />);
    const line = await screen.findByTestId("home-line");
    expect(screen.getAllByTestId("home-line")).toHaveLength(1);
    expect(line.getAttribute("data-kind")).toBe("interrupted");
    expect(line.textContent).toContain("+2 more");
    expect(within(line).getByRole("link", { name: "Open" }).getAttribute("href")).toBe("/everything#status");
  });

  it("says nothing when there is nothing to say", async () => {
    H.api.getResponses["/ui/status-line"] = { interrupted: 0, failing_loops: 0, failing_packs: 0, running: 0 };
    H.api.getResponses["/chat/approvals/pending"] = { approvals: [] };
    render(<HomePage />);
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(screen.queryByTestId("home-line")).toBeNull();
  });
});

describe("the shell carries the downgrade warning (it was the Overview's alone)", () => {
  it("the layout mounts ProviderDowngradeBanner; the Status view no longer does", () => {
    const layout = readFileSync(join(__dirname, "..", "app", "layout.tsx"), "utf-8");
    expect(layout).toContain("<ProviderDowngradeBanner />");
    const status = readFileSync(join(__dirname, "..", "components", "overview", "StatusOverview.tsx"), "utf-8");
    expect(status).not.toContain("<ProviderDowngradeBanner />");
  });
});
