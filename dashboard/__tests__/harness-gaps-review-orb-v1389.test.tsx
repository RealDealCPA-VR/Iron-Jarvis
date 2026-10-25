/**
 * Harness gaps REVIEW — the status orb and a reply count it can no longer
 * trust.
 *
 * Fix 1 made the orb turn "Working — something is running" while /health
 * counts a chat's or a coding session's reply (`replies`). lib/daemon KEEPS
 * the last /health it got while Iron Jarvis is offline (the banner says
 * offline; `health` is not cleared), so a crash in the middle of a coding
 * session's reply left the orb spinning "Working" for as long as Iron Jarvis
 * stayed down — a count of replies on a process that is not there. The
 * count is read only while Iron Jarvis answers.
 *
 * Only the network, the daemon hook and the /events socket are doubles.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const H = vi.hoisted(() => ({
  online: true,
  health: null as unknown,
}));

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

import { MoodOrb } from "@/components/MoodOrb";
import { EventsHub, EventsProvider } from "@/lib/useEvents";

class FakeWS {
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {}
  close() {}
}

const HEALTH = {
  status: "ok",
  version: "x",
  default_provider: "claude-cli",
  default_model: "",
  providers: [],
  replies: { chat: 0, build: 1 },
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
  H.health = HEALTH;
  vi.stubGlobal("WebSocket", FakeWS);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the orb never counts replies on an Iron Jarvis that is not answering", () => {
  it("CONTROL: a coding session's reply counted by a daemon that answers turns it 'working'", () => {
    expect(orb().getAttribute("data-mood")).toBe("thinking");
  });

  it("offline, the last /health's count is not a reply being written: the orb is idle", () => {
    H.online = false; // lib/daemon keeps the last /health while offline
    const o = orb();
    expect(o.getAttribute("data-mood")).toBe("idle");
    expect(o.getAttribute("aria-label")).toBe("Running now: Idle — nothing running");
  });
});
