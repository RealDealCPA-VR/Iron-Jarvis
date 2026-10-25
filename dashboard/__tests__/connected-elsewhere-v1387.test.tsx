/**
 * Readiness 12B (G15, the page half): an app the chat offered to connect,
 * connected SOMEWHERE ELSE, lets the work go on — with a press.
 *
 * Before: Continue lived only on the chat's own key card. A user who took the
 * chat's "Connect Google Drive in Settings" door and signed in there, who
 * connected GitHub on the Directory instead of pasting into the card, or who
 * saved a Pixio key in Settings › Connections came back to a chat that never
 * noticed: no Continue, the question left behind.
 *
 * Driven through the REAL chat page with the REAL stream reader
 * (lib/useChatStream: the daemon's done frame, as SSE, from
 * fixtures/connected-elsewhere-v1387.json — written by the REAL daemon in
 * tests/test_connected_elsewhere_v1387.py) and the REAL Directory rows the
 * daemon answered before and after each way in. Only the transport is a stub.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

type Done = { reply: string; doors: unknown[]; config_cards: unknown[]; tools_used: string[] };
type Way = { done: Done; before: Record<string, unknown>; after: Record<string, unknown> };
const FIX = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "connected-elsewhere-v1387.json"), "utf8").replace(/\r\n/g, "\n"),
) as { github: Way; drive: Way; pixio: Way };

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    gets: [] as string[],
    posts: [] as { path: string; body: Record<string, unknown> }[],
    getResponses: {} as Record<string, unknown>,
    /** Each POST /chat/stream body, in order. */
    streamBodies: [] as Record<string, unknown>[],
    /** The done frames the stream answers, in order. */
    dones: [] as Record<string, unknown>[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://d",
  ijToken: () => "tok",
  flattenDetail: (d: unknown) => String(d),
  get: async (path: string) => {
    H.gets.push(path);
    const r = H.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return typeof r === "function" ? (r as () => unknown)() : r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.posts.push({ path, body });
    return {};
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
  del: async () => ({}),
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
    readAloud: () => {}, readingKey: null,
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import {
  APPS_DOOR_HREF,
  connectOffer,
  doorAppNames,
  nowConnected,
  offeredApps,
} from "@/lib/connectedElsewhere";
import { continueText } from "@/lib/appConnect";

/** The daemon's /chat/stream: one `done` frame per call (the fixture's). */
function sse(done: Record<string, unknown>): Response {
  const text = `event: done\ndata: ${JSON.stringify(done)}\n\n`;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(new TextEncoder().encode(text));
      c.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

beforeEach(() => {
  H.gets.length = 0;
  H.posts.length = 0;
  H.streamBodies.length = 0;
  H.dones.length = 0;
  H.getResponses = {
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
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).startsWith("http://d/chat/stream")) {
        H.streamBodies.push(JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>);
        return sse(H.dones.shift() ?? { reply: "ok" });
      }
      return new Response("{}", { status: 404 });
    }),
  );
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const lastUser = (body: Record<string, unknown> | undefined): string => {
  const msgs = (body?.messages as { role: string; content: string }[] | undefined) ?? [];
  return [...msgs].reverse().find((m) => m.role === "user")?.content ?? "";
};
const connectorReads = () => H.gets.filter((g) => g === "/connectors").length;

async function ask(question: string, way: Way) {
  H.getResponses["/connectors"] = { connectors: [way.before] };
  H.dones.push({ ...way.done, route: { provider: "mock", model: "mock" } });
  const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: question } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(H.streamBodies.length).toBe(1));
  // The reply is on screen, and the page read the Directory once for its offer.
  await waitFor(() => expect(connectorReads()).toBe(1));
}

/** The user connects the app somewhere else, then comes back to this window. */
async function connectElsewhereAndReturn(way: Way, how: "focus" | "visible") {
  H.getResponses["/connectors"] = { connectors: [way.after] };
  await act(async () => {
    if (how === "focus") window.dispatchEvent(new Event("focus"));
    else {
      Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    }
  });
}

describe("connected somewhere else, then Continue", () => {
  it("GitHub: the chat showed its card, the user connected it on the Directory", async () => {
    const Q = "list my open PRs on GitHub";
    render(<ChatPage />);
    await ask(Q, FIX.github);
    // The card is there, unsaved; GitHub reads "no tools" (waiting for a
    // token) — not connected, so nothing is offered yet.
    await screen.findByTestId("credential-card");
    expect(screen.queryByTestId("connected-elsewhere")).toBeNull();

    await connectElsewhereAndReturn(FIX.github, "focus");
    const line = await screen.findByTestId("connected-elsewhere");
    expect(line.textContent).toContain("GitHub connected · 1 tool");
    // NOTHING was sent by itself.
    expect(H.streamBodies.length).toBe(1);

    fireEvent.click(screen.getByTestId("connected-elsewhere-continue"));
    await waitFor(() => expect(H.streamBodies.length).toBe(2));
    expect(lastUser(H.streamBodies[1])).toBe(continueText("GitHub", Q));
    // The extension is on for this chat, as the card's Continue does.
    expect(H.streamBodies[1].connectors).toContain(String(FIX.github.after.id));
    await waitFor(() => expect(screen.queryByTestId("connected-elsewhere")).toBeNull());
  });

  it("Google Drive: the chat's Connect door, then a sign-in through the browser", async () => {
    const Q = "search my Google Drive for the lease";
    render(<ChatPage />);
    await ask(Q, FIX.drive);
    expect(screen.getByText("Connect Google Drive in Settings")).toBeTruthy();
    expect(screen.queryByTestId("connected-elsewhere")).toBeNull();

    await connectElsewhereAndReturn(FIX.drive, "visible");
    const line = await screen.findByTestId("connected-elsewhere");
    expect(line.textContent).toContain("Google Drive connected");
    expect(line.textContent).not.toContain("tool");
    expect(H.streamBodies.length).toBe(1);

    fireEvent.click(screen.getByTestId("connected-elsewhere-continue"));
    await waitFor(() => expect(H.streamBodies.length).toBe(2));
    expect(lastUser(H.streamBodies[1])).toBe(continueText("Google Drive", Q));
    // A drive is not an extension: no chat switch to turn on.
    expect((H.streamBodies[1].connectors as string[] | undefined) ?? []).not.toContain("google_drive");
  });

  it("Pixio: the chat showed its key card, the user saved the key in Settings", async () => {
    const Q = "make me a picture of a lighthouse with pixio";
    render(<ChatPage />);
    await ask(Q, FIX.pixio);
    expect(screen.queryByTestId("connected-elsewhere")).toBeNull();
    await connectElsewhereAndReturn(FIX.pixio, "focus");
    expect((await screen.findByTestId("connected-elsewhere")).textContent).toContain("Pixio connected");
    fireEvent.click(screen.getByTestId("connected-elsewhere-continue"));
    await waitFor(() => expect(H.streamBodies.length).toBe(2));
    expect(lastUser(H.streamBodies[1])).toBe(continueText("Pixio", Q));
  });
});

describe("only the newest offer, only when it really works, read only when looked at", () => {
  it("a later message means the work went on: no Continue for the older offer", async () => {
    render(<ChatPage />);
    await ask("search my Google Drive for the lease", FIX.drive);
    H.dones.push({ reply: "Sure, here is a poem.", route: { provider: "mock", model: "mock" } });
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "write me a poem" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await screen.findByText("Sure, here is a poem.");
    await connectElsewhereAndReturn(FIX.drive, "focus");
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("connected-elsewhere")).toBeNull();
  });

  it("an app the Directory does not read as working offers nothing", async () => {
    render(<ChatPage />);
    await ask("list my open PRs on GitHub", FIX.github);
    H.getResponses["/connectors"] = {
      connectors: [{ ...FIX.github.after, status: "failed", tools_loaded: 0 }],
    };
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(connectorReads()).toBe(2));
    expect(screen.queryByTestId("connected-elsewhere")).toBeNull();
    // Switched off on the Directory: not working either.
    H.getResponses["/connectors"] = { connectors: [{ ...FIX.github.after, enabled: false }] };
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(connectorReads()).toBe(3));
    expect(screen.queryByTestId("connected-elsewhere")).toBeNull();
  });

  it("no offer, no read: a reply that offered nothing never asks the Directory", async () => {
    render(<ChatPage />);
    H.dones.push({ reply: "Hello!", route: { provider: "mock", model: "mock" } });
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "hi" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await screen.findByText("Hello!");
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    expect(connectorReads()).toBe(0);
  });
});

describe("the reading, directly", () => {
  it("reads a door by the daemon's own words and address", () => {
    expect(APPS_DOOR_HREF).toBe((FIX.drive.done.doors[0] as { href: string }).href);
    expect(doorAppNames("Connect Google Drive in Settings")).toEqual(["Google Drive"]);
    expect(doorAppNames("Connect Dropbox and Google Drive in Settings")).toEqual(["Dropbox", "Google Drive"]);
    expect(doorAppNames("Connect Gmail or Microsoft 365 in Settings")).toEqual(["Gmail", "Microsoft 365"]);
    expect(doorAppNames("Connect A, B and 2 more apps in Settings")).toEqual(["A", "B"]);
    expect(doorAppNames("Connect your apps in Settings")).toEqual([]);
    expect(doorAppNames("Open the canvas")).toEqual([]);
  });

  it("a key card counts only when app_connect showed it and it is not working yet", () => {
    const card = (FIX.github.done.config_cards as Record<string, unknown>[]).find((c) => c.kind === "secret")!;
    const reply = { role: "assistant", toolsUsed: ["app_connect"], configCards: [card] };
    expect(offeredApps(reply)).toEqual([{ id: "github" }]);
    // A card a model asked for some other way (config_secret: a token renewal).
    expect(offeredApps({ ...reply, toolsUsed: ["config_secret"] })).toEqual([]);
    // Saved here and working: its own card says so, with its own Continue.
    expect(offeredApps({ ...reply, configCards: [{ ...card, saved: "stored", connected: { app: "github", tools: 3 } }] })).toEqual([]);
    // Saved here but it did not start: fixing it elsewhere still counts.
    expect(offeredApps({ ...reply, configCards: [{ ...card, saved: "stored", connected: { app: "github", tools: 0 } }] })).toEqual([{ id: "github" }]);
    expect(offeredApps({ ...reply, configCards: [{ ...card, undone: true }] })).toEqual([]);
    expect(offeredApps({ ...reply, role: "user" })).toEqual([]);
  });

  it("matches the Directory's own rows: by name, by id (or the alias it was lit by), by provider", () => {
    const after = [FIX.drive.after, FIX.github.after, FIX.pixio.after];
    expect(nowConnected([{ name: "Google Drive" }], after)?.name).toBe("Google Drive");
    expect(nowConnected([{ id: "github" }], after)).toEqual({ app: "github", name: "GitHub", tools: 1, mcp: true });
    expect(nowConnected([{ id: "github" }], [{ ...FIX.github.after, id: "gh-server", catalog_id: "github" }])?.app).toBe("gh-server");
    expect(nowConnected([{ provider: "pixio" }], after)?.mcp).toBe(false);
    expect(nowConnected([{ id: "github" }], [FIX.github.before])).toBeNull(); // "connected: true" but no tools
    expect(nowConnected([{ name: "Google Drive" }], "nope")).toBeNull();
  });

  it("the offer is the newest message's, keyed by what it offers", () => {
    const door = FIX.drive.done.doors[0];
    const msgs = [{ role: "user" }, { role: "assistant", doors: [door] }];
    const offer = connectOffer(msgs)!;
    expect(offer.index).toBe(1);
    // (Review) A door is kept WHOLE and read against the Directory's own
    // names — a catalog name may hold ", " itself (connected-elsewhere-doors).
    expect(offer.apps).toEqual([{ door: "Google Drive" }]);
    expect(connectOffer([...msgs, { role: "user" }])).toBeNull();
    expect(connectOffer([])).toBeNull();
  });
});
