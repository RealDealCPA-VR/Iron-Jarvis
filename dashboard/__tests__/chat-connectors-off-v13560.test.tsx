/**
 * v1.356.0 integrate (G78, lane 6A's review note 3): the chat's + › Connections
 * list never offers an app the user switched off as connected (turning it
 * on for a chat would arm nothing) nor as one to connect (it is saved, with
 * its key; it is turned on in Settings › Tools or its Directory card).
 *
 * The REAL chat page (the chat-words-v13560 harness) fed the REAL daemon's
 * Directory row for a switched-off pack
 * (`fixtures/wave6-seams-v13560.json`, tests/test_integrate_wave6_v13560.py).
 */
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
      posts: [] as { path: string; body: Record<string, unknown> }[],
      responses: {} as Record<string, unknown>,
    },
    stream: { context: null as Record<string, unknown> | null },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (path: string) => {
    H.api.gets.push(path);
    const r = H.api.responses[path];
    if (r instanceof Error) throw r;
    return r === undefined ? {} : r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    if (path === "/chat/compact") return { covers: 4, stripped: 0, summary: "The start.", trigger: "manual" };
    return {};
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
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
    run: () =>
      Promise.resolve({
        reply: "Noted.",
        tools_used: [],
        ...(H.stream.context ? { context: H.stream.context } : {}),
      }),
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: true, reason: null, engine: null, listening: false, processing: false,
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
  useProviderHealth: () => ({
    byProvider: {}, signedOutByProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {},
  }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: ReactNode; href: string } & Record<string, unknown>) => (
    <a href={href} {...(rest as Record<string, never>)}>
      {children}
    </a>
  ),
}));
vi.mock("@/components/project/ProjectSurfaces", () => ({
  ProjectSurface: ({ view }: { view: string }) => <div data-testid="surface-stub">surface:{view}</div>,
}));

import ChatPage from "@/app/chat/page";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const FIXTURE = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "wave6-seams-v13560.json"), "utf8"),
) as { directory_card: Record<string, unknown> };

const ON_APP = {
  ...FIXTURE.directory_card,
  id: "github",
  name: "GitHub",
  enabled: true,
  status: "connected",
  tools_loaded: 5,
};
const NEW_APP = { ...FIXTURE.directory_card, id: "notion", name: "Notion", connected: false, status: "disconnected", enabled: true };

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.posts.length = 0;
  H.api.responses = {
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
    "/connectors": { connectors: [FIXTURE.directory_card, ON_APP, NEW_APP] },
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = function scrollIntoView() {} as unknown as Element["scrollIntoView"];
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("+ › Connections: a switched-off app is neither connected nor 'connect'", () => {
  it("lists the connected app as a switch, the new one as a Directory link, and the off one nowhere", async () => {
    render(<ChatPage />);
    await screen.findByRole("textbox", { name: "Message" });
    fireEvent.click(within(screen.getByTestId("composer-toolbar")).getByRole("button", { name: /^Tools: / }));
    const menu = await screen.findByTestId("composer-tools-menu");
    fireEvent.click(within(menu).getByRole("button", { name: /Connections/ }));
    const on = await screen.findByRole("switch", { name: /GitHub/ }, { timeout: 10_000 });
    expect(on.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByTitle("Connect Notion in the Directory")).toBeTruthy();
    // The daemon's off Slack row: no switch, no "connect" teaser.
    expect(screen.queryByRole("switch", { name: /Slack/ })).toBeNull();
    expect(screen.queryByTitle("Connect Slack in the Directory")).toBeNull();
  });
});
