/**
 * Review of lane P (v1.420.0: C6.4, C6.6, C6.7, C6.12). Each block was red on
 * the lane's first cut.
 *
 *  1. STOP ON A ROUND THAT ALREADY ENDED. Stop aborted the page's wait at
 *     once, whatever the daemon said. When the round had ALREADY answered (the
 *     daemon's stop answers 404 "That round isn't running.", the replies are
 *     on their way / already appended to the chat), the page dropped the
 *     reply and wrote "Stopped." — the chat then held a "Stopped." line beside
 *     the agent's reply the daemon had saved. A 404 from the daemon's own stop
 *     now lets the answer land.
 *  2. THE SERVER TYPE. The choice showed only after a Fetch that could not
 *     tell. A server that answers BOTH ways (a LiteLLM proxy lists models the
 *     OpenAI way and serves /v1/messages too — v1.329.0 proved the Anthropic
 *     way live against the user's own proxy) read "OpenAI" and the user could
 *     no longer pick Anthropic at all; and a type picked by hand, then hidden
 *     by a new address, was still SENT with no sign of it on screen.
 *  3. A MISSION RECEIPT AFTER A FAILOVER. "· local" is read from the catalog
 *     row of the pair the session was STAMPED with; when a route note says
 *     another model answered instead, "· local" claimed the local box did the
 *     work. Nothing is said about where when the route moved.
 *  4. THE FLEET KEY. A node saved with a key was DETECTED keyless (the key is
 *     wired after the node exists), so a keyed gateway kept the kind a 401
 *     gives; the form now asks Re-detect once the key is on the node.
 *  5. THE FREE TIER note says only what Google's own pages say, citing them.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    posts: [] as { path: string; body: Record<string, unknown> }[],
    patches: [] as { path: string; body: Record<string, unknown> }[],
    gets: {} as Record<string, unknown>,
    /** path -> answer (a function gets the body and the signal). */
    answers: {} as Record<string, unknown>,
  };
});

async function answer(path: string, body: Record<string, unknown>, signal?: AbortSignal) {
  const r = H.answers[path];
  if (r instanceof Error) throw r;
  if (typeof r === "function") return await (r as (b: unknown, s?: AbortSignal) => unknown)(body, signal);
  return r ?? { ok: true };
}

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  sseUrl: (p: string) => p,
  wsUrl: (p: string) => `ws://daemon${p}`,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  get: async (path: string) => {
    const r = H.gets[path];
    return r === undefined ? {} : r;
  },
  post: async (path: string, body: Record<string, unknown>, opts?: { signal?: AbortSignal }) => {
    H.posts.push({ path, body });
    return answer(path, body, opts?.signal);
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async (path: string, body: Record<string, unknown>) => {
    H.patches.push({ path, body });
    return {};
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
vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: false }),
  EventsProvider: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
}));
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
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: () => {},
  }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/connections/RestHookups", () => ({ RestHookups: () => null }));
vi.mock("@/components/connections/IronProxyCard", () => ({ IronProxyCard: () => null }));
vi.mock("@/components/BrandGlyph", () => ({ ProviderMark: () => null, BrandGlyph: () => null }));

import ChatPage from "@/app/chat/page";
import ConnectionsPage from "@/components/settings/pages/ConnectionsPage";
import FleetPage from "@/app/fleet/page";
import { MissionOutput } from "@/components/agents/mission/MissionOutput";
import { FREE_TIERS, freeTierNote } from "@/lib/freeTiers";
import type { MissionView } from "@/lib/mission";
import type { MissionLive, MissionLiveStore } from "@/lib/useMission";

beforeEach(() => {
  H.posts.length = 0;
  H.patches.length = 0;
  H.answers = {};
  H.gets = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": {
      agents: [
        {
          mention: "builder", name: "builder", kind: "builtin", source: "builtin",
          description: "Builds things", healthy: true, delegable: true,
        },
      ],
    },
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

/* ------------------------------------------------------------------ 1 */

describe("Stop on an @-round that already answered", () => {
  const box = () => screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement;

  it("lets the reply land and writes no Stopped line when the daemon says the round ended", async () => {
    let release: (v: unknown) => void = () => {};
    H.answers["/chat/panel"] = (_b: unknown, signal?: AbortSignal) =>
      new Promise((resolve, reject) => {
        release = resolve;
        signal?.addEventListener("abort", () => reject(new H.FakeApiError("cancelled", 0, true)));
      });
    render(<ChatPage />);
    await waitFor(() => expect(box()).toBeTruthy());
    fireEvent.change(box(), { target: { value: "@bu" } });
    await screen.findByRole("option", { name: /builder/ });
    fireEvent.change(box(), { target: { value: "@builder draft the brief" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    const stop = await screen.findByRole("button", { name: "Stop" });
    const roundId = H.posts.find((p) => p.path === "/chat/panel")?.body.round_id as string;
    // The round already ended on the daemon: its stop says so.
    H.answers[`/chat/panel/${roundId}/stop`] = new H.FakeApiError("That round isn't running.", 404);
    fireEvent.click(stop);
    await waitFor(() => expect(H.posts.some((p) => p.path === `/chat/panel/${roundId}/stop`)).toBe(true));
    await new Promise((r) => setTimeout(r, 20));
    release({
      mode: "panel",
      thread_id: "athr1",
      entries: [{ who: "builtin:builder", content: "Here is the brief." }],
      spoke: ["builtin:builder"],
      skipped: [],
      unknown_mentions: [],
    });
    await screen.findByText("Here is the brief.");
    expect(screen.queryByText("Stopped.")).toBeNull();
  });
});

/* ------------------------------------------------------------------ 2 */

const QWENS = { models: ["qwen3", "llama3"], error: null, reason: null };
const CANNOT_TELL = { models: [], error: "nothing set", reason: "failed" };

function seedConnections() {
  H.gets["/connections"] = {
    connections: [
      {
        provider: "custom", display_name: "Custom endpoint", method: "api_key",
        supports_oauth: false, supports_api_key: true, oauth_help: "", connected: false,
        status: "disconnected", account: "", source: "", scopes: [],
      },
    ],
  };
  H.gets["/routing/quality"] = { bar: 0.75, min_samples: 3, rows: [] };
  H.gets["/fleet"] = { nodes: [] };
}

async function openEndpointForm() {
  seedConnections();
  H.answers["/fleet/nodes"] = { node: { id: "relay", label: "Relay" } };
  render(<ConnectionsPage />);
  const card = (await screen.findByText("Model servers on your network")).closest("#conn-card-custom") as HTMLElement;
  fireEvent.click(within(card).getByRole("button", { name: /Add an endpoint/ }));
  fireEvent.change(within(card).getByPlaceholderText("http://localhost:1234/v1"), {
    target: { value: "http://spark.lan:4000" },
  });
  return card;
}
const saved = () => H.posts.find((p) => p.path === "/fleet/nodes")?.body;

describe("the server type stays reachable", () => {
  it("after Fetch read the OpenAI way, the user can still choose Anthropic (a proxy answers both)", async () => {
    const card = await openEndpointForm();
    H.answers["/connections/endpoints/models"] = QWENS;
    fireEvent.click(within(card).getByTestId("endpoint-fetch-models"));
    const panel = await within(card).findByTestId("endpoint-model-picker");
    fireEvent.click(within(panel).getByText("qwen3"));
    const change = await within(card).findByTestId("endpoint-protocol-change");
    fireEvent.click(change);
    fireEvent.click(within(card).getByTestId("endpoint-protocol-anthropic"));
    fireEvent.click(within(card).getByRole("button", { name: /Save endpoint/ }));
    await waitFor(() => expect(saved()?.protocol).toBe("anthropic"));
  });

  it("a type chosen by hand stays on screen when a new address hides the Fetch result", async () => {
    const card = await openEndpointForm();
    H.answers["/connections/endpoints/models"] = CANNOT_TELL;
    fireEvent.click(within(card).getByTestId("endpoint-fetch-models"));
    await within(card).findByTestId("endpoint-protocol");
    fireEvent.click(within(card).getByTestId("endpoint-protocol-anthropic"));
    fireEvent.change(within(card).getByPlaceholderText("http://localhost:1234/v1"), {
      target: { value: "http://other.lan:4000" },
    });
    // it will still be SENT as Anthropic, so it is still SHOWN
    const group = await within(card).findByTestId("endpoint-protocol");
    expect((within(group).getByTestId("endpoint-protocol-anthropic") as HTMLInputElement).checked).toBe(true);
  });
});

/* ------------------------------------------------------------------ 3 */

function missionView(note: string): MissionView {
  return {
    session: {
      id: "s1", task: "t", objective: "t", status: "completed", outcome: null, project_id: null,
      created_at: null, finished_at: null, interrupted: false, continued_as: null,
      provider: "custom", model: "glm", route_note: note,
    },
    coordinator: { name: "Jarvis", status: "done", waiting_on: null, steps: 1 },
    members: [],
    progress: { done: 0, total: 0 },
    activity: [],
    deliverable: { text: "", documents: [], worklist: null },
  } as MissionView;
}
const EMPTY: MissionLive = { coordinator: "", coordinatorPhase: "", coordinatorNarration: "", members: {}, latest: null };
const liveStore = (): MissionLiveStore => ({ get: () => EMPTY, subscribe: () => () => {} });

describe("the mission receipt after a failover", () => {
  it("says nothing about where when a route note says another model answered", async () => {
    H.gets["/models"] = { models: [{ provider: "custom", model: "glm", name: "DGX Spark", on_this_network: true }] };
    const note = "Jarvis's model custom failed (unreachable); claude-cli answered instead.";
    render(<MissionOutput view={missionView(note)} liveStore={liveStore()} objective="" onChanged={() => {}} onOpen={() => {}} />);
    await screen.findByText(note);
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("mission-receipt-where")).toBeNull();
  });

  it("control: with no route note the catalog's local still shows", async () => {
    H.gets["/models"] = { models: [{ provider: "custom", model: "glm", name: "DGX Spark", on_this_network: true }] };
    render(<MissionOutput view={missionView("")} liveStore={liveStore()} objective="" onChanged={() => {}} onOpen={() => {}} />);
    await waitFor(() => expect(screen.getByTestId("mission-receipt-where").textContent).toMatch(/local/));
  });
});

/* ------------------------------------------------------------------ 4 */

describe("the Fleet key", () => {
  it("saves the key under the Connections form's name, wires it, then re-detects with it", async () => {
    H.gets["/fleet"] = { nodes: [] };
    H.gets["/fleet/usage"] = {};
    H.answers["/fleet/probe"] = { kind: "unknown", snapshot: null, children: [], error: "asked for a key" };
    H.answers["/fleet/nodes"] = { node: { id: "gw" } };
    window.history.replaceState({}, "", "/fleet");
    render(<FleetPage />);
    const url = await screen.findByPlaceholderText(/http/);
    fireEvent.change(url, { target: { value: "http://gw.lan:4000" } });
    fireEvent.change(screen.getByTestId("fleet-add-key"), { target: { value: "sk-master-0123456789" } });
    fireEvent.submit(url.closest("form") as HTMLFormElement);
    await waitFor(() => expect(H.posts.some((p) => p.path === "/fleet/nodes/gw/detect")).toBe(true));
    const secret = H.posts.find((p) => p.path === "/secrets");
    expect(secret?.body.name).toBe("endpoint_gw_key");
    expect(H.patches.find((p) => p.path === "/fleet/nodes/gw")?.body).toEqual({ api_key_name: "endpoint_gw_key" });
    // order: the key is on the node BEFORE detection asks with it
    const detectAt = H.posts.findIndex((p) => p.path === "/fleet/nodes/gw/detect");
    expect(detectAt).toBeGreaterThan(H.posts.findIndex((p) => p.path === "/secrets"));
    // the key never rides the node itself
    expect(JSON.stringify(H.posts.find((p) => p.path === "/fleet/nodes")?.body)).not.toContain("sk-master");
  });
});

/* ------------------------------------------------------------------ 5 */

describe("the free-tier note", () => {
  it("says only what Google's pricing page and API terms say, and names both", () => {
    const g = FREE_TIERS.google;
    expect(g.noteSources).toEqual([
      "https://ai.google.dev/gemini-api/docs/pricing",
      "https://ai.google.dev/gemini-api/terms",
    ]);
    expect(g.checked).toBe("2026-10-31");
    expect(freeTierNote("google")).toBe(
      "On the free tier Google uses what you send to improve its products, and people at Google may read it.",
    );
    // never the hedged "may use" from memory
    expect(freeTierNote("google")).not.toMatch(/may use/);
  });
});
