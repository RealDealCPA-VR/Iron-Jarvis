/**
 * v1.455.0 (readiness Wave F, item 11): the orb counts a workflow run with
 * no agent step and a media job as running.
 *
 * Running now listed a tool-only workflow run and a picture being made, while
 * the orb beside it said "Idle — nothing running" (it read session events and
 * /health.replies only). /health now carries `background: {workflows, media}`
 * (daemon/background_counts.py, in memory); the orb reads it. An older Iron
 * Jarvis (no field) behaves exactly as before.
 *
 * Only the network, the daemon hook and the /events socket are doubles.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const H = vi.hoisted(() => ({ online: true, health: null as unknown }));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  }
  return {
    API_BASE: "",
    ApiError,
    ijToken: () => "",
    wsUrl: (p: string) => `ws://daemon${p}`,
    get: async (path: string) => {
      throw new ApiError(`unmocked GET ${path}`, 404);
    },
    post: async () => ({}),
    put: async () => ({}),
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

import { backgroundRunning, MoodOrb } from "@/components/MoodOrb";
import { EventsHub, EventsProvider } from "@/lib/useEvents";

class FakeWS {
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {}
  close() {}
}

const BASE = {
  status: "ok",
  version: "x",
  default_provider: "claude-cli",
  default_model: "",
  providers: [],
  replies: { chat: 0, build: 0, build_app: 0, waiting: 0 },
};

function orb() {
  render(
    <EventsProvider hub={new EventsHub()}>
      <MoodOrb onPress={() => {}} />
    </EventsProvider>,
  );
  return screen.getByTestId("mood-orb");
}

beforeEach(() => {
  H.online = true;
  H.health = BASE;
  vi.stubGlobal("WebSocket", FakeWS);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the orb counts background work Running now lists", () => {
  it("CONTROL: nothing running reads idle", () => {
    H.health = { ...BASE, background: { workflows: 0, media: 0 } };
    expect(orb().getAttribute("data-mood")).toBe("idle");
  });

  it("a tool-only workflow run turns the orb 'working'", () => {
    H.health = { ...BASE, background: { workflows: 1, media: 0 } };
    expect(orb().getAttribute("data-mood")).toBe("thinking");
  });

  it("a media job being made turns the orb 'working'", () => {
    H.health = { ...BASE, background: { workflows: 0, media: 1 } };
    const o = orb();
    expect(o.getAttribute("data-mood")).toBe("thinking");
    expect(o.getAttribute("aria-label")).toMatch(/Working/);
  });

  it("a reply parked on the user beside a running render is not 'waiting': something works", () => {
    H.health = {
      ...BASE,
      replies: { chat: 1, build: 0, build_app: 0, waiting: 1 },
      background: { workflows: 0, media: 1 },
    };
    expect(orb().getAttribute("data-mood")).toBe("thinking");
  });

  it("an older Iron Jarvis (no field, junk) counts nothing", () => {
    expect(backgroundRunning({ ...BASE } as never)).toBe(0);
    expect(backgroundRunning({ ...BASE, background: "x" } as never)).toBe(0);
    expect(backgroundRunning({ ...BASE, background: { workflows: -2, media: NaN } } as never)).toBe(0);
    expect(backgroundRunning({ ...BASE, background: { workflows: 2, media: 1 } } as never)).toBe(3);
  });

  it("offline, the last /health's background count is not believed", () => {
    H.online = false;
    H.health = { ...BASE, background: { workflows: 1, media: 1 } };
    expect(orb().getAttribute("data-mood")).toBe("idle");
  });
});
