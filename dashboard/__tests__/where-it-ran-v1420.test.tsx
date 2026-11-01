/**
 * v1.420.0 (C6.7 part + C6.9 dashboard half) — where a run ran, and an orb
 * that tells waiting from working.
 *
 *  - the mission receipt says "· local" / "· cloud" (TurnReceipt's replyWhere
 *    rule) from the catalog row's `on_this_network` for THAT exact pair;
 *    nothing when the catalog does not know the pair, never for the demo;
 *  - a helper on the user's own model server names the MACHINE it ran on
 *    (lib/buildHelpers workerModelName, the lib/ranOnWords rule); a
 *    subscription keeps its model name;
 *  - the orb says "Waiting for you" (amber) when every running reply is
 *    parked on the user (/health `replies.waiting` == the running count), and
 *    behaves as before when the field is absent.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const H = vi.hoisted(() => ({
  online: true,
  health: null as unknown,
  models: [] as unknown[],
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    cancelled = false;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError,
    API_BASE: "",
    ijToken: () => "",
    sseUrl: (p: string) => p,
    wsUrl: (p: string) => `ws://daemon${p}`,
    get: () => Promise.resolve({}),
    post: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
  };
});
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: H.online,
    unauthorized: false,
    requestError: false,
    health: H.health,
    checking: false,
    epoch: 0,
    refresh: () => {},
    provided: true,
  }),
}));
vi.mock("@/lib/useModels", () => ({
  useModels: () => ({ models: H.models, usable: H.models, data: null, error: null, loading: false, reload: () => {} }),
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { MoodOrb, repliesWaiting } from "@/components/MoodOrb";
import { EventsHub, EventsProvider } from "@/lib/useEvents";
import { MissionOutput } from "@/components/agents/mission/MissionOutput";
import { WHERE_LOCAL_TITLE } from "@/components/chat/TurnReceipt";
import { onThisNetworkOf } from "@/lib/runWhere";
import { workerModelName } from "@/lib/buildHelpers";
import type { MissionView } from "@/lib/mission";
import type { MissionLive, MissionLiveStore } from "@/lib/useMission";

class FakeWS {
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {}
  close() {}
}

beforeEach(() => {
  H.online = true;
  H.health = null;
  H.models = [];
  vi.stubGlobal("WebSocket", FakeWS);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/* ------------------------------------------------------------------ orb */

function orb() {
  render(
    <EventsProvider hub={new EventsHub()}>
      <MoodOrb onPress={() => {}} />
    </EventsProvider>,
  );
  return screen.getByTestId("mood-orb");
}
const health = (replies: Record<string, unknown>) => ({
  status: "ok",
  version: "x",
  default_provider: "claude-cli",
  default_model: "",
  providers: [],
  replies,
});

describe("the orb tells waiting from working (C6.9)", () => {
  it("every running reply parked on the user: Waiting for you, amber, no spin", () => {
    H.health = health({ chat: 1, build: 1, build_app: 0, waiting: 2 });
    const o = orb();
    expect(o.getAttribute("data-mood")).toBe("waiting");
    expect(o.getAttribute("aria-label")).toMatch(/^Running now: Waiting for you/);
    expect(o.querySelector(".animate-spin-slow")).toBeNull();
    expect(o.querySelector("svg")?.getAttribute("stroke")).toBe("#fbbf24");
  });

  it("one of two still working: Working", () => {
    H.health = health({ chat: 1, build: 1, waiting: 1 });
    expect(orb().getAttribute("data-mood")).toBe("thinking");
  });

  it("an Iron Jarvis that sends no `waiting` behaves exactly as before", () => {
    H.health = health({ chat: 1, build: 0 });
    expect(orb().getAttribute("data-mood")).toBe("thinking");
    expect(repliesWaiting(health({ chat: 1 }) as never)).toBe(0);
    expect(repliesWaiting(health({ chat: 1, waiting: "2" }) as never)).toBe(0);
    expect(repliesWaiting(null)).toBe(0);
  });

  it("nothing running: idle even with a stale waiting count", () => {
    H.health = health({ chat: 0, build: 0, waiting: 0 });
    expect(orb().getAttribute("data-mood")).toBe("idle");
  });
});

/* -------------------------------------------------------------- runWhere */

describe("onThisNetworkOf reads the exact pair only (C6.7)", () => {
  const CAT = [
    { provider: "custom", model: "glm", on_this_network: true },
    { provider: "custom", model: "frontier", on_this_network: false },
    { provider: "fleet-ollama", model: "qwen3:8b", on_this_network: true },
    { provider: "claude-cli", model: "claude-opus-5" },
  ];
  it("answers for the pair, its fleet twin, and nothing else", () => {
    expect(onThisNetworkOf("custom", "glm", CAT)).toBe(true);
    expect(onThisNetworkOf("custom", "frontier", CAT)).toBe(false); // a proxy's cloud name
    expect(onThisNetworkOf("ollama", "qwen3:8b", CAT)).toBe(true); // config slot -> fleet twin
    expect(onThisNetworkOf("custom", "unknown-model", CAT)).toBeNull(); // never another row's answer
    expect(onThisNetworkOf("claude-cli", "claude-opus-5", CAT)).toBeNull(); // no flag = say nothing
    expect(onThisNetworkOf("mock", "mock", CAT)).toBeNull();
    expect(onThisNetworkOf("custom", "glm", null)).toBeNull();
  });
});

/* -------------------------------------------------------- mission receipt */

function view(provider: string, model: string): MissionView {
  return {
    session: {
      id: "s1",
      task: "t",
      objective: "t",
      status: "completed",
      outcome: null,
      project_id: null,
      created_at: null,
      finished_at: null,
      interrupted: false,
      continued_as: null,
      provider,
      model,
      route_note: "",
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
const renderMission = (v: MissionView) =>
  render(<MissionOutput view={v} liveStore={liveStore()} objective="" onChanged={() => {}} onOpen={() => {}} />);

describe("the mission receipt says local or cloud (C6.7)", () => {
  it("a local model: · local, with the receipt's own title", () => {
    H.models = [{ provider: "custom", model: "glm", name: "DGX Spark", on_this_network: true }];
    renderMission(view("custom", "glm"));
    const where = screen.getByTestId("mission-receipt-where");
    expect(where.textContent).toMatch(/· local$/);
    expect(where.getAttribute("title")).toBe(WHERE_LOCAL_TITLE);
    expect(screen.getByTestId("mission-receipt").textContent).toMatch(/glm on DGX Spark · local/);
  });

  it("a cloud model: · cloud", () => {
    H.models = [{ provider: "claude-cli", model: "claude-opus-5", on_this_network: false }];
    renderMission(view("claude-cli", "claude-opus-5"));
    expect(screen.getByTestId("mission-receipt-where").textContent).toMatch(/· cloud$/);
  });

  it("an unknown pair or the demo model says neither", () => {
    H.models = [{ provider: "custom", model: "other", on_this_network: true }];
    renderMission(view("custom", "glm"));
    expect(screen.queryByTestId("mission-receipt-where")).toBeNull();
    cleanup();
    H.models = [{ provider: "mock", model: "mock", on_this_network: true }];
    renderMission(view("mock", "mock"));
    expect(screen.queryByTestId("mission-receipt-where")).toBeNull();
  });
});

/* ------------------------------------------------------------ helper row */

describe("a helper on the user's own server names the machine (C6.7)", () => {
  it("a NAMED server: model on machine; unnamed server or subscription: the model as before", () => {
    const cat = [{ provider: "fleet-custom", model: "glm", name: "DGX Spark" }];
    expect(workerModelName("custom", "glm", cat)).toBe("glm on DGX Spark");
    expect(workerModelName("fleet-custom", "glm", cat)).toBe("glm on DGX Spark");
    expect(workerModelName("claude-cli", "claude-sonnet-4-5", [])).not.toMatch(/ on /);
    // an unnamed server's generic place word adds nothing: the model alone
    expect(workerModelName("ollama", "qwen3:8b", [])).not.toMatch(/ on /);
    expect(workerModelName("mock", "mock", [])).toBe(workerModelName("mock", "", []));
  });
});
