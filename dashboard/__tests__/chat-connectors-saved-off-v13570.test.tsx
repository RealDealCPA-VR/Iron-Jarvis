/**
 * v1.357.0 integrate (wave-6 follow-up 2): a chat whose SAVED connections
 * include an app the user has since switched off keeps that app in
 * + › Connections as an Off row that says why — never silently dropped from
 * the list (the chat-connectors-off-v13560 rule still holds for a chat that
 * never had it on: such an app shows nowhere).
 *
 * The row is not a switch (turning it on for the chat would arm nothing:
 * the daemon's `chat_turn._resolve_connectors` skips an id with no loaded
 * tools, tests/test_integrate_wave7_v13570.py), it keeps its place in the
 * chat's saved set (so switching the app back on brings it back), and it can
 * be removed from this chat.
 *
 * The REAL chat page (the chat-connectors-off-v13560 harness) fed the REAL
 * daemon's Directory row for a switched-off pack
 * (`fixtures/wave6-seams-v13560.json`).
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
    stream: { context: null as Record<string, unknown> | null, bodies: [] as Record<string, unknown>[] },
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
    run: (body: Record<string, unknown>) => {
      H.stream.bodies.push(body);
      return Promise.resolve({
        reply: "Noted.",
        tools_used: [],
        ...(H.stream.context ? { context: H.stream.context } : {}),
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
import { PACK_OFF_IN_CHAT_LINE, PACK_OFF_LINE } from "@/lib/extensionWords";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const FIXTURE = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "wave6-seams-v13560.json"), "utf8"),
) as { directory_card: Record<string, unknown> };

const OFF_ID = String(FIXTURE.directory_card.id);
const OFF_NAME = String(FIXTURE.directory_card.name);
const ON_APP = {
  ...FIXTURE.directory_card,
  id: "github",
  name: "GitHub",
  enabled: true,
  status: "connected",
  tools_loaded: 5,
};

function openSavedChat(connectors: string[]) {
  H.api.responses["/chat/threads/t7"] = {
    id: "t7",
    title: "saved",
    messages: [
      { role: "user", content: "hi" },
      { role: "assistant", content: "yo" },
    ],
    setup: { connectors },
  };
  window.history.replaceState({}, "", "/chat?thread=t7");
}

async function openConnections() {
  await screen.findByText("yo");
  fireEvent.click(within(screen.getByTestId("composer-toolbar")).getByRole("button", { name: /^Tools: / }));
  const menu = await screen.findByTestId("composer-tools-menu");
  fireEvent.click(within(menu).getByRole("button", { name: /Connections/ }));
  await screen.findByRole("switch", { name: /GitHub/ }, { timeout: 10_000 });
}

async function send(text: string): Promise<Record<string, unknown>> {
  const n = H.stream.bodies.length;
  const box = (await screen.findByRole("textbox", { name: "Message" })) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(H.stream.bodies.length).toBe(n + 1));
  return H.stream.bodies[n];
}

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.posts.length = 0;
  H.stream.bodies.length = 0;
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
    "/connectors": { connectors: [FIXTURE.directory_card, ON_APP] },
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

describe("+ › Connections: an app this chat has on, switched off since, reads Off", () => {
  it("the fixture is the daemon's switched-off row (anti-vacuity)", () => {
    expect(FIXTURE.directory_card.enabled).toBe(false);
  });

  it("lists it as an Off row that says why, never a switch, never dropped", async () => {
    openSavedChat([OFF_ID, "github"]);
    render(<ChatPage />);
    await openConnections();
    const row = await screen.findByTestId(`connector-off-${OFF_ID}`);
    expect(row.textContent).toContain(OFF_NAME);
    expect(row.textContent).toContain("Off");
    expect(row.getAttribute("aria-label")).toBe(`${OFF_NAME}: Off`);
    // Says why, in the Tools page's own off words, and where to turn it on.
    const why = document.getElementById(String(row.getAttribute("aria-describedby")));
    expect(why?.textContent).toBe(PACK_OFF_IN_CHAT_LINE);
    expect(PACK_OFF_IN_CHAT_LINE.startsWith(PACK_OFF_LINE)).toBe(true);
    expect(PACK_OFF_IN_CHAT_LINE).toContain("Settings › Tools");
    // Not a switch: turning it on for the chat would arm nothing.
    expect(screen.queryByRole("switch", { name: new RegExp(OFF_NAME, "i") })).toBeNull();
    // The connected app keeps its switch, on for this chat.
    expect(screen.getByRole("switch", { name: /GitHub/ }).getAttribute("aria-checked")).toBe("true");
    // And no "Nothing connected yet" line pretends the list is empty.
    expect(screen.queryByText(/Nothing connected yet/)).toBeNull();
  });

  it("stays in the chat's saved set, so it comes back when the app is turned on", async () => {
    openSavedChat([OFF_ID, "github"]);
    render(<ChatPage />);
    await screen.findByText("yo");
    const body = await send("what changed?");
    expect(body.connectors).toEqual([OFF_ID, "github"]);
  });

  it("Remove from this chat takes it out of the list and out of what the next message carries", async () => {
    openSavedChat([OFF_ID, "github"]);
    render(<ChatPage />);
    await openConnections();
    fireEvent.click(await screen.findByRole("button", { name: `Remove ${OFF_NAME} from this chat` }));
    await waitFor(() => expect(screen.queryByTestId(`connector-off-${OFF_ID}`)).toBeNull());
    const body = await send("and now?");
    expect(body.connectors).toEqual(["github"]);
  });

  it("control: a chat that never had it on shows it nowhere (the v1.356.0 rule)", async () => {
    openSavedChat(["github"]);
    render(<ChatPage />);
    await openConnections();
    expect(screen.queryByTestId(`connector-off-${OFF_ID}`)).toBeNull();
    expect(screen.queryByTestId("connectors-saved-off")).toBeNull();
  });
});
