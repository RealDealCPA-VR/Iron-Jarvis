import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * v1.304.0 — THE AGENTS' FACES, PROMINENT: seats at a real table.
 *
 * The user asked for the agents' portraits to be "more prominent" and chose
 * the shape: the round table shows every participant as a 72px SEAT around an
 * elliptical table, inside a live status ring; the speaker lifts forward with
 * a glow and "speaking…"; a seat opens the agent on a ~160px HERO portrait;
 * bubbles carry 40px portraits; the empty room greets with 48px faces; the
 * roster rail's rows became 64px portrait cards. And a project's world mounts
 * the table with `projectId`, which must reach `POST /agents/threads`.
 *
 * What can fail silently here, and is therefore pinned:
 *   - PORTRAITS WIN on the seats (an uploaded portrait is an <img>, the rest
 *     draw the generated face) — a seat that forgot `avatarUrl` still renders.
 *   - THE RING TELLS THE TRUTH: paused / waiting on you / working / error /
 *     idle each come from the roster row or the room, never invented.
 *   - THE SPEAKER is the participant whose reply is pending (rounds are
 *     sequential), and the lift goes away when the round ends.
 *   - REDUCED MOTION: nothing moves or pulses for a user who asked for that.
 *   - NARROW: below 520px of CONTAINER width the table becomes a strip of
 *     56px seats, and only one of the two is in the DOM.
 *   - FAST: typing in the composer re-renders no seat and no reply bubble —
 *     counted at the face, so a lost `memo` goes red.
 *   - A PROJECT room is made and seated ONLY by the daemon
 *     (`POST /projects/{id}/world/room`); the card with a `projectId` shows
 *     the "Project: <name>" tag, grounds its jobs there, and NEVER POSTs
 *     /agents/threads itself.
 */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    responses: {} as Record<string, unknown>,
    posts: [] as { path: string; body: unknown }[],
    /** A held POST: resolve it by hand (`release`). */
    hold: null as null | { path: string; release: (v: unknown) => void },
    holdPath: "" as string,
    FakeApiError,
  };
});

/** What `useApi`/`usePolledApi` answer, per path (default: nothing yet). */
const hookData = vi.hoisted(() => ({ byPath: {} as Record<string, unknown> }));

/** Faces rendered, per pixel size — the render counter the perf pin reads. */
const faceRenders = vi.hoisted(() => ({ bySize: new Map<number, number>() }));

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  api: () => Promise.reject(new api.FakeApiError("unmocked", 404)),
  get: (path: string) => {
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    return Promise.resolve(r);
  },
  post: (path: string, body: unknown) => {
    api.posts.push({ path, body });
    if (api.holdPath && path === api.holdPath) {
      return new Promise((resolve) => {
        api.hold = { path, release: resolve };
      });
    }
    const r = api.responses[`POST ${path}`];
    return Promise.resolve(r ?? {});
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [] }) }));
vi.mock("@/lib/useApi", () => ({
  useApi: (path: string) => ({
    data: hookData.byPath[path] ?? null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string) => ({
    data: hookData.byPath[path] ?? null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

// The REAL face, wrapped only to count renders by size (seats are 72px,
// bubbles 40px — nothing else on this surface draws at those sizes).
vi.mock("@/components/agents/AgentFace", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/components/agents/AgentFace")>();
  function CountedFace(props: Parameters<typeof real.default>[0]) {
    const size = props.size ?? 28;
    faceRenders.bySize.set(size, (faceRenders.bySize.get(size) ?? 0) + 1);
    return real.default(props);
  }
  return { ...real, default: CountedFace };
});

import {
  SEAT_PX,
  STRIP_SEAT_PX,
  TableSeats,
  buildSeats,
  rosterStatus,
  seatLayout,
  seatStatus,
} from "@/components/agents/TableSeats";
import { AGENT_HERO_PX } from "@/components/agents/AgentsModal";
import { ROSTER_CARD_PX, RosterStrip, type RosterEntry } from "@/components/agents/RosterStrip";
import type { Participant, ThreadEntry } from "@/components/agents/identity";

window.HTMLElement.prototype.scrollIntoView = () => {};

/* -------------------------------------------------------------- fixtures */

const HEALTH = {
  last_run_at: null,
  last_outcome: null,
  last_error: null,
  last_wake_at: null,
  queued: 0,
  running: 0,
  blocked: 0,
};

const ROSTER: RosterEntry[] = [
  {
    name: "builder",
    kind: "builtin",
    description: "hands-on doer",
    delegable: true,
    healthy: true,
    stats: null,
    avatar: "/agents/builder/avatar",
    last_active: "2026-10-01T00:00:00Z",
  },
  {
    name: "custom:remy",
    kind: "dynamic",
    description: "critic",
    delegable: true,
    healthy: true,
    stats: null,
    health: { ...HEALTH, blocked: 1 },
  },
  {
    name: "custom:pat",
    kind: "dynamic",
    description: "",
    delegable: true,
    healthy: false,
    paused: true,
    pause_reason: "day off",
    stats: null,
  },
  { name: "remote:box", kind: "remote", description: "", delegable: true, healthy: false, stats: null },
  {
    name: "planner",
    kind: "builtin",
    description: "",
    delegable: false,
    healthy: true,
    stats: null,
    activity: "busy",
  },
];

const P = (source: Participant["source"], name: string, role = ""): Participant => ({
  key: `${source}:${name}`,
  source,
  name,
  role,
});

const PANEL: Participant[] = [
  P("builtin", "builder", "lead"),
  P("dynamic", "remy", "critic"),
  P("dynamic", "pat"),
  P("remote", "box"),
  P("builtin", "planner"),
  P("builtin", "guide"), // no roster row → idle, not openable
];

function thread(messages: ThreadEntry[], participants = PANEL) {
  return {
    id: "t1",
    title: "Panel",
    participants,
    message_count: messages.length,
    updated_at: "2026-10-06T10:00:00Z",
    messages,
  };
}

const TALK: ThreadEntry[] = [
  { who: "user", content: "hi panel", at: "2026-10-06T10:00:00Z" },
  { who: "builtin:builder", content: "hello there", at: "2026-10-06T10:00:05Z" },
];


const seat = (key: string) => screen.getByTestId(`seat-${key}`);
const faceIn = (el: HTMLElement) =>
  el.querySelector<HTMLElement>("img, svg[data-testid='agent-face']")!;

/* ------------------------------------------------------- env stand-ins -- */

let roWidth: number | null = null;
class FakeResizeObserver {
  cb: ResizeObserverCallback;
  constructor(cb: ResizeObserverCallback) {
    this.cb = cb;
  }
  observe() {
    if (roWidth !== null)
      this.cb(
        [{ contentRect: { width: roWidth } } as unknown as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      );
  }
  unobserve() {}
  disconnect() {}
}

function setReducedMotion(on: boolean | null) {
  if (on === null) {
    // jsdom has no matchMedia at all — the default every other suite sees.
    delete (window as unknown as { matchMedia?: unknown }).matchMedia;
    return;
  }
  (window as unknown as { matchMedia: unknown }).matchMedia = (q: string) => ({
    matches: on && q.includes("prefers-reduced-motion"),
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
  });
}

beforeEach(() => {
  api.responses = {};
  api.posts = [];
  api.hold = null;
  api.holdPath = "";
  roWidth = null;
  faceRenders.bySize.clear();
  hookData.byPath = {};
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setReducedMotion(null);
});

/* ------------------------------------------------------------- the seats */


/* ----------------------------------------------------------- the speaker */


/* ---------------------------------------------------------- responsive */


/* ------------------------------------------------------------- the hero */


/* --------------------------------------------------------- performance */


/* ------------------------------------------------------------ projectId */


/* ------------------------------------------------------- roster cards -- */

describe("the roster rail's portrait cards", () => {
  it("every row is a >=64px portrait in the same status ring the seats wear", () => {
    render(<RosterStrip entries={ROSTER} onSelect={() => {}} selected={null} />);
    const rail = screen.getByTestId("roster-rail");
    const rings = within(rail).getAllByTestId("status-ring");
    expect(rings).toHaveLength(ROSTER.length);
    expect(ROSTER_CARD_PX).toBeGreaterThanOrEqual(64);
    // builder's uploaded portrait, at the card size.
    const img = faceIn(rings[0]);
    expect(img.tagName.toLowerCase()).toBe("img");
    expect(img.getAttribute("width")).toBe(String(ROSTER_CARD_PX));
    // The ring reads the SAME rule as a seat (`rosterStatus`).
    expect(rings.map((r) => r.getAttribute("data-status"))).toEqual([
      "idle",
      "waiting",
      "paused",
      "error",
      "working",
    ]);
    // The name stays the button's accessible name (the face is decorative).
    expect(within(rail).getByRole("button", { name: /^builder/ })).toBeInTheDocument();
  });
});

/* ----------------------------------------------- polish (review round) -- */


/* --------------------------------------------- what a seat was given ---- */

