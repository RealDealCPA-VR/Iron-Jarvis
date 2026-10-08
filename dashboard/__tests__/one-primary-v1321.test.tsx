/**
 * Calm UI redesign S11 — one primary action per view (AUDIT §8): exactly one
 * solid accent button per view region. Settings' Save (shown only while
 * there are unsaved changes) is pinned in settings-home-v1321.
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
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
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
import EverythingPage from "@/app/everything/page";
import { AppSidebar } from "@/components/AppSidebar";

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



/** Solid primaries actually drawn on a desktop: an ancestor with `hidden`
 *  hides one, unless that ancestor shows itself again from a breakpoint up
 *  (the sidebar is `hidden md:flex`). */
function primaries(root: ParentNode = document): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(".btn-accent")).filter((el) => {
    for (let n: HTMLElement | null = el; n; n = n.parentElement) {
      const cls = typeof n.className === "string" ? n.className.split(/\s+/) : [];
      const hidden = cls.includes("hidden") && !cls.some((c) => /^(sm|md|lg):(flex|block|grid|inline-flex)$/.test(c));
      if (hidden || n.hidden) return false;
    }
    return true;
  });
}

describe("one primary action per view (AUDIT §8, redesign S11)", () => {
  it("home: the composer's Send is the only solid primary in the conversation", async () => {
    render(<HomePage />);
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    const room = (box.closest("[data-testid='chat-room']") ?? document) as ParentNode;
    // Nothing to send yet: no solid primary competes for the eye.
    expect(primaries(room).length).toBeLessThanOrEqual(1);
    fireEvent.change(box, { target: { value: "hello" } });
    await waitFor(() => expect(primaries(room)).toHaveLength(1));
    expect(primaries(room)[0].getAttribute("aria-label")).toBe("Send");
  });

  it("the sidebar: New chat is its one solid primary", () => {
    render(<AppSidebar />);
    const solid = primaries(screen.getByTestId("app-sidebar"));
    expect(solid.map((b) => b.getAttribute("data-testid"))).toEqual(["sidebar-new-chat"]);
  });

  it("Everything: a directory, no solid primary at all", () => {
    render(<EverythingPage />);
    expect(primaries(screen.getByTestId("everything-grid"))).toHaveLength(0);
  });
});
