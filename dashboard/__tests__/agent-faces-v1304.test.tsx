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

import { RoundTable, ungroundedNote } from "@/components/agents/RoundTable";
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

function renderTable(over: Partial<Parameters<typeof RoundTable>[0]> = {}) {
  return render(
    <RoundTable
      threadId="t1"
      reloadNonce={0}
      onEditPanel={() => {}}
      onRoundDone={() => {}}
      roster={ROSTER}
      {...over}
    />,
  );
}

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

describe("seats at the table", () => {
  it("seats every participant with the RIGHT portrait at 72px — uploaded wins, generated otherwise", async () => {
    api.responses["/agents/threads/t1"] = thread(TALK);
    renderTable();
    await screen.findByText("hello there");
    const table = screen.getByTestId("table-seats");
    expect(table.getAttribute("data-layout")).toBe("table");
    expect(within(table).getAllByRole("listitem")).toHaveLength(PANEL.length);
    // builder has an uploaded portrait → an <img> at the seat size, token-
    // signed with the roster's cache key.
    const img = faceIn(seat("builtin:builder"));
    expect(img.tagName.toLowerCase()).toBe("img");
    expect(img.getAttribute("src")).toBe(
      "http://127.0.0.1:8787/agents/builder/avatar?v=" +
        encodeURIComponent("2026-10-01T00:00:00Z"),
    );
    expect(img.getAttribute("width")).toBe(String(SEAT_PX));
    expect(SEAT_PX).toBe(72);
    // remy has none → the generated face, same size, never a broken image.
    const drawn = faceIn(seat("dynamic:remy"));
    expect(drawn.tagName.toLowerCase()).toBe("svg");
    expect(drawn.getAttribute("width")).toBe("72");
    // The seat's name and role are visible under the portrait.
    expect(within(seat("builtin:builder")).getByText("builder")).toBeInTheDocument();
    expect(within(seat("builtin:builder")).getByText("lead")).toBeInTheDocument();
  });

  it("bubbles carry 40px portraits (the uploaded one where stored)", async () => {
    api.responses["/agents/threads/t1"] = thread(TALK);
    renderTable();
    await screen.findByText("hello there");
    const transcript = screen.getByTestId("thread-transcript");
    const imgs = transcript.querySelectorAll("img");
    expect(imgs).toHaveLength(1);
    expect(imgs[0].getAttribute("width")).toBe("40");
    expect(imgs[0].getAttribute("src")).toContain("/agents/builder/avatar");
  });

  it("the empty room shows each participant ONCE — the seats, never a second row of faces", async () => {
    api.responses["/agents/threads/t1"] = thread([]);
    renderTable();
    const empty = await screen.findByTestId("thread-empty");
    // The greeting keeps its sentence...
    expect(within(empty).getByText(/Ask the panel anything/)).toBeInTheDocument();
    // ...and draws no faces: the seats above already show everyone.
    expect(empty.querySelectorAll("img, svg[data-testid='agent-face']")).toHaveLength(0);
    expect(within(screen.getByTestId("table-seats")).getAllByRole("listitem")).toHaveLength(
      PANEL.length,
    );
    for (const p of PANEL) expect(screen.getByTestId(`seat-${p.key}`)).toBeInTheDocument();
  });

  it("each ring says the roster's truth: paused, waiting on you, working, error, idle", async () => {
    api.responses["/agents/threads/t1"] = thread(TALK);
    renderTable();
    await screen.findByText("hello there");
    const status = (k: string) => seat(k).getAttribute("data-seat-status");
    expect(status("dynamic:pat")).toBe("paused"); // a day off
    expect(status("dynamic:remy")).toBe("waiting"); // a blocked job waits on you
    expect(status("builtin:planner")).toBe("working"); // the daemon says busy
    expect(status("remote:box")).toBe("error"); // offline remote
    expect(status("builtin:builder")).toBe("idle");
    expect(status("builtin:guide")).toBe("idle"); // no row: no claim
    // The ring element carries the same word (what the colour is keyed on).
    expect(
      within(seat("dynamic:pat")).getByTestId("status-ring").getAttribute("data-status"),
    ).toBe("paused");
    // ...and it is SAID, not only coloured.
    expect(seat("dynamic:remy").getAttribute("aria-label")).toMatch(/waiting on you/);
    expect(within(seat("dynamic:remy")).getByTestId("seat-caption").textContent).toBe(
      "waiting on you",
    );
  });

  it("the room's own facts outrank the roster: a failed line is error, a remote's question waits on you", () => {
    const roster = new Map<string, RosterEntry>([["builtin:builder", ROSTER[0]]]);
    const seats = buildSeats({
      participants: [P("builtin", "builder"), P("remote", "box")],
      rosterByKey: roster,
      messages: [
        { who: "builtin:builder", content: "", at: "t", error: "builder couldn't answer" },
        { who: "remote:box", content: "which year?", at: "t", inbound: true, kind: "question" },
      ],
      speakingKey: null,
      roundKeys: [],
      answeredKeys: [],
    });
    expect(seats.map((s) => s.status)).toEqual(["error", "waiting"]);
    // A paused agent stays paused even mid-round — it never speaks.
    expect(seatStatus(ROSTER[2], "speaking", undefined).status).toBe("paused");
    expect(seatStatus(null, "speaking", undefined).status).toBe("working");
    expect(
      seatStatus(null, null, { who: "remote:box", content: "", at: "t", pending: true }).status,
    ).toBe("working");
    expect(rosterStatus({ ...ROSTER[0], health: { ...HEALTH, last_outcome: "failed" } }).status).toBe(
      "error",
    );
  });
});

/* ----------------------------------------------------------- the speaker */

describe("the speaker lifts forward", () => {
  function startRound() {
    fireEvent.change(screen.getByLabelText("Message the panel"), {
      target: { value: "what next?" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Ask the panel/ }));
  }

  it("marks the participant whose reply is pending as speaking, the rest up next — and clears when the round ends", async () => {
    setReducedMotion(false);
    const panel = [P("builtin", "builder", "lead"), P("dynamic", "remy", "critic")];
    api.responses["/agents/threads/t1"] = thread(TALK, panel);
    api.holdPath = "/agents/threads/t1/say";
    renderTable();
    await screen.findByText("hello there");
    startRound();
    await waitFor(() =>
      expect(seat("builtin:builder").getAttribute("data-speaking")).toBe("true"),
    );
    const speaker = seat("builtin:builder");
    expect(within(speaker).getByTestId("seat-caption").textContent).toBe("speaking…");
    expect(speaker.getAttribute("data-seat-status")).toBe("working");
    expect(speaker.className).toMatch(/scale-110/);
    expect(within(speaker).getByTestId("seat-halo")).toBeInTheDocument();
    expect(speaker.getAttribute("aria-label")).toMatch(/speaking/);
    // The table's core is lit while a round runs.
    expect(screen.getByTestId("round-table-surface").getAttribute("data-live")).toBe("true");
    const next = seat("dynamic:remy");
    expect(next.getAttribute("data-speaking")).toBeNull();
    expect(next.getAttribute("data-phase")).toBe("next");
    expect(within(next).getByTestId("seat-caption").textContent).toBe("up next");
    expect(next.className).not.toMatch(/scale-110/);

    // The round lands: nobody is speaking any more.
    await act(async () => {
      api.hold!.release({
        entries: [
          { who: "user", content: "what next?", at: "2026-10-06T10:01:00Z" },
          { who: "builtin:builder", content: "ship it", at: "2026-10-06T10:01:05Z" },
          { who: "dynamic:remy", content: "test first", at: "2026-10-06T10:01:09Z" },
        ],
      });
    });
    await waitFor(() => expect(screen.getByText("test first")).toBeInTheDocument());
    expect(seat("builtin:builder").getAttribute("data-speaking")).toBeNull();
    expect(screen.queryByTestId("seat-halo")).toBeNull();
  });

  it("the NEXT speaker is the first expected one without a landed reply", () => {
    const seats = buildSeats({
      participants: [P("builtin", "builder"), P("dynamic", "remy")],
      rosterByKey: new Map(),
      messages: [],
      speakingKey: "dynamic:remy",
      roundKeys: ["builtin:builder", "dynamic:remy"],
      answeredKeys: ["builtin:builder"],
    });
    expect(seats.map((s) => s.phase)).toEqual(["answered", "speaking"]);
  });

  it("REDUCED MOTION: the speaker does not move or pulse — the glow and the words carry it", async () => {
    setReducedMotion(true);
    const panel = [P("builtin", "builder", "lead"), P("dynamic", "remy", "critic")];
    api.responses["/agents/threads/t1"] = thread(TALK, panel);
    api.holdPath = "/agents/threads/t1/say";
    renderTable();
    await screen.findByText("hello there");
    startRound();
    await waitFor(() =>
      expect(seat("builtin:builder").getAttribute("data-speaking")).toBe("true"),
    );
    const speaker = seat("builtin:builder");
    expect(speaker.getAttribute("data-motion")).toBe("reduced");
    expect(speaker.className).not.toMatch(/scale-|translate-|transition/);
    expect(within(speaker).queryByTestId("seat-halo")).toBeNull();
    // Still unmistakable: the caption and the glow.
    expect(within(speaker).getByTestId("seat-caption").textContent).toBe("speaking…");
    expect(within(speaker).getByTestId("status-ring").getAttribute("style")).toMatch(
      /box-shadow/,
    );
  });
});

/* ---------------------------------------------------------- responsive */

describe("narrow rooms", () => {
  it("below 520px of container width the table becomes a strip of 56px seats — one layout in the DOM", async () => {
    roWidth = 360;
    api.responses["/agents/threads/t1"] = thread(TALK);
    renderTable();
    await screen.findByText("hello there");
    const strip = await screen.findByTestId("table-seats");
    await waitFor(() => expect(strip.getAttribute("data-layout")).toBe("strip"));
    expect(screen.getAllByTestId("table-seats")).toHaveLength(1);
    expect(screen.queryByTestId("round-table-surface")).toBeNull();
    expect(faceIn(seat("builtin:builder")).getAttribute("width")).toBe(String(STRIP_SEAT_PX));
    expect(STRIP_SEAT_PX).toBe(56);
  });

  it("a wide room keeps the table, seats on one ring up to six, a second ring past that", async () => {
    roWidth = 900;
    api.responses["/agents/threads/t1"] = thread(TALK);
    renderTable();
    await screen.findByText("hello there");
    expect(screen.getByTestId("table-seats").getAttribute("data-layout")).toBe("table");
    expect(screen.getByTestId("table-seats").getAttribute("data-rings")).toBe("1");
    cleanup();
    const eight = [...PANEL, P("builtin", "reviewer"), P("builtin", "researcher")];
    api.responses["/agents/threads/t1"] = thread(TALK, eight);
    renderTable();
    await screen.findByText("hello there");
    expect(screen.getByTestId("table-seats").getAttribute("data-rings")).toBe("2");
  });
});

/* ------------------------------------------------------------- the hero */

describe("a seat opens the agent on a hero portrait", () => {
  it("click → the agent's detail with a ~160px portrait; Give work arms THIS composer", async () => {
    api.responses["/agents/threads/t1"] = thread(TALK);
    renderTable();
    await screen.findByText("hello there");
    fireEvent.click(seat("builtin:builder"));
    const modal = await screen.findByTestId("agent-seat-modal");
    const hero = await within(modal).findByTestId("agent-hero");
    const portrait = faceIn(hero);
    expect(portrait.tagName.toLowerCase()).toBe("img");
    expect(portrait.getAttribute("width")).toBe(String(AGENT_HERO_PX));
    expect(AGENT_HERO_PX).toBeGreaterThanOrEqual(150);
    expect(hero.getAttribute("aria-label")).toMatch(/^builder — Built-in, idle$/);
    expect(within(modal).getByTestId("agent-detail-builder")).toBeInTheDocument();
    // Give work from the hero aims the room's own dispatch at that agent.
    fireEvent.click(within(modal).getByRole("button", { name: /Give work/ }));
    await waitFor(() => expect(screen.queryByTestId("agent-seat-modal")).toBeNull());
    expect(screen.getByRole("button", { name: /Give it to builder/ })).toBeInTheDocument();
  });

  it("a seat with no roster row is not a button (nothing to open)", async () => {
    api.responses["/agents/threads/t1"] = thread(TALK);
    renderTable();
    await screen.findByText("hello there");
    expect(seat("builtin:guide").tagName.toLowerCase()).toBe("div");
    expect(seat("builtin:builder").tagName.toLowerCase()).toBe("button");
  });
});

/* --------------------------------------------------------- performance */

describe("typing never redraws the table", () => {
  it("five keystrokes re-render zero seats and zero reply bubbles", async () => {
    api.responses["/agents/threads/t1"] = thread(TALK);
    renderTable();
    await screen.findByText("hello there");
    // Let the seats settle (ResizeObserver / reduced-motion effects).
    await act(async () => {});
    const seats72 = faceRenders.bySize.get(72) ?? 0;
    const bubbles40 = faceRenders.bySize.get(40) ?? 0;
    expect(seats72).toBeGreaterThan(0); // anti-vacuity: the counter counts
    expect(bubbles40).toBeGreaterThan(0);
    const box = screen.getByLabelText("Message the panel");
    for (const v of ["h", "he", "hel", "hell", "hello"]) {
      fireEvent.change(box, { target: { value: v } });
    }
    expect((box as HTMLTextAreaElement).value).toBe("hello"); // the page DID re-render
    expect(faceRenders.bySize.get(72) ?? 0).toBe(seats72);
    expect(faceRenders.bySize.get(40) ?? 0).toBe(bubbles40);
  });
});

/* ------------------------------------------------------------ projectId */

describe("a project's round table", () => {
  const roomCreates = () => api.posts.filter((p) => p.path === "/agents/threads");

  it("shows the project's tag and NEVER creates a room itself — not on load, not in a round", async () => {
    api.responses["/agents/threads/t1"] = thread(TALK);
    api.responses["POST /agents/threads/t1/say"] = { entries: [] };
    renderTable({ projectId: "p1", projectName: "Alpha" });
    await screen.findByText("hello there");
    expect(screen.getByTestId("room-project-tag").textContent).toBe("Project: Alpha");
    fireEvent.change(screen.getByLabelText("Message the panel"), {
      target: { value: "go" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Ask the panel/ }));
    await waitFor(() =>
      expect(api.posts.some((p) => p.path === "/agents/threads/t1/say")).toBe(true),
    );
    expect(roomCreates()).toHaveLength(0);
  });

  it("with no room id yet it says so, fetches nothing and POSTs nothing", async () => {
    renderTable({ threadId: "", projectId: "p1", projectName: "Alpha" });
    expect(screen.getByTestId("room-not-open")).toBeInTheDocument();
    await act(async () => {});
    expect(roomCreates()).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /open the round table/i })).toBeNull();
  });

  it("a job given out from a project room is grounded in that project by default", async () => {
    api.responses["/agents/threads/t1"] = thread(TALK);
    api.responses["/projects"] = { projects: [] };
    api.responses["POST /sessions"] = { id: "s1" };
    renderTable({ projectId: "p1", projectName: "Alpha" });
    await screen.findByText("hello there");
    fireEvent.change(screen.getByLabelText("Message the panel"), {
      target: { value: "write the summary" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Give it to/ }));
    await waitFor(() =>
      expect(
        api.posts.find((p) => p.path !== "/agents/threads/t1/say" && p.path !== "/agents/threads"),
      ).toBeTruthy(),
    );
    const job = api.posts.find(
      (p) => p.path !== "/agents/threads/t1/say" && p.path !== "/agents/threads",
    )!;
    expect((job.body as { project_id?: string }).project_id).toBe("p1");
    expect(roomCreates()).toHaveLength(0);
  });

  it("the tag reads the project's NAME when the caller gave only the id — never the id", async () => {
    api.responses["/agents/threads/t1"] = thread(TALK);
    api.responses["/projects/p1"] = { id: "p1", name: "Beta" };
    renderTable({ projectId: "p1" });
    await waitFor(() =>
      expect(screen.getByTestId("room-project-tag").textContent).toBe("Project: Beta"),
    );
    expect(screen.getByTestId("room-project-tag").textContent).not.toContain("p1");
  });

  it("a general room has no tag", async () => {
    api.responses["/agents/threads/t1"] = thread(TALK);
    renderTable();
    await screen.findByText("hello there");
    expect(screen.queryByTestId("room-project-tag")).toBeNull();
  });
});

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

describe("the table is a table, and the chat stays at the front", () => {
  /** Where a seat's portrait centre is relative to the table ellipse:
   *  r > 1 is outside the rim; `gap` is the px distance to the NEAREST point
   *  of the rim (sampled every half degree). */
  function rimOf(l: ReturnType<typeof seatLayout>, i: number) {
    const sp = l.seats[i];
    const dx = sp.dx;
    const dy = sp.y - l.cy;
    const r = Math.hypot(dx / l.rx, dy / l.ry);
    let gap = Infinity;
    for (let k = 0; k < 720; k++) {
      const t = (k * Math.PI) / 360;
      gap = Math.min(gap, Math.hypot(dx - l.rx * Math.cos(t), dy - l.ry * Math.sin(t)));
    }
    return { r, gap };
  }

  it("GEOMETRY: every seat sits just OUTSIDE the rim, inside the room, the near side open", () => {
    for (const width of [560, 720, 1000, 1400]) {
      for (let n = 1; n <= 10; n++) {
        const l = seatLayout(n, width);
        expect(l.seats).toHaveLength(n);
        // A real table: wide (the ~68% asked for once there is room).
        if (width >= 1000 && n <= 6) expect((2 * l.rx) / width).toBeGreaterThanOrEqual(0.66);
        // Narrowed only as far as the side seats need (two rings at 560px).
        expect((2 * l.rx) / width).toBeGreaterThanOrEqual(n <= 6 ? 0.45 : 0.35);
        for (let i = 0; i < n; i++) {
          const { r, gap } = rimOf(l, i);
          const sp = l.seats[i];
          const where = `n=${n} w=${width} seat ${i}`;
          expect(r, where).toBeGreaterThan(1); // outside the table...
          expect(gap, where).toBeGreaterThanOrEqual(24); // ...AT it: within a band (the 36px-radius portrait may overlap the rim a little)
          expect(gap, where).toBeLessThanOrEqual(sp.ring ? 110 : 56);
          // Inside the room: the 120px seat box and the 72px portrait.
          expect(Math.abs(sp.dx) + 60, where).toBeLessThanOrEqual(width / 2);
          expect(sp.y - 40 - (sp.above ? 32 : 0), where).toBeGreaterThanOrEqual(0);
          expect(sp.y + 40 + (sp.above ? 0 : 32), where).toBeLessThanOrEqual(l.height);
          // The viewer's side (straight down) stays open.
          const dy = sp.y - l.cy;
          expect(dy > 0 && Math.abs(sp.dx) < l.rx * 0.3, where).toBe(false);
        }
      }
    }
  });

  it("COMPACT: one ring is at most 300px tall, two at most 390 — the transcript starts in a 900px window", async () => {
    for (const width of [560, 720, 1000, 1400]) {
      for (let n = 1; n <= 6; n++) expect(seatLayout(n, width).height).toBeLessThanOrEqual(300);
      for (let n = 7; n <= 10; n++) expect(seatLayout(n, width).height).toBeLessThanOrEqual(390);
    }
    // And the rendered table really is that tall (style height).
    roWidth = 1000;
    api.responses["/agents/threads/t1"] = thread(TALK);
    renderTable();
    await screen.findByText("hello there");
    const h = parseFloat(screen.getByTestId("table-seats").style.height);
    expect(h).toBeGreaterThan(150);
    expect(h).toBeLessThanOrEqual(300);
    // The surface is a SOLID filled ellipse, ~68% of the room.
    const surface = screen.getByTestId("round-table-surface");
    expect(surface.className).toMatch(/bg-ink-800/);
    // Measured width lands after the observer reports (a passive effect).
    await waitFor(() =>
      expect(parseFloat(surface.style.width) / 1000).toBeGreaterThanOrEqual(0.66),
    );
  });

  it("CAPTIONS: the role only when it differs from the name, else the status word", () => {
    const seat = (role: string, status: "idle" | "waiting" = "idle") => ({
      key: "builtin:reviewer",
      name: "reviewer",
      role,
      avatarUrl: null,
      status,
      note: "",
      phase: null,
      openable: false,
    });
    const captionOf = (role: string) => {
      const { unmount } = render(<TableSeats seats={[seat(role)]} layout="strip" />);
      const text = screen.getByTestId("seat-caption").textContent;
      unmount();
      return text;
    };
    expect(captionOf("reviewer")).toBe("idle"); // never "reviewer / reviewer"
    expect(captionOf("Reviewer")).toBe("idle");
    expect(captionOf("")).toBe("idle");
    expect(captionOf("critic")).toBe("critic");
    // A live status still outranks the role.
    render(<TableSeats seats={[seat("critic", "waiting")]} layout="strip" />);
    expect(screen.getByTestId("seat-caption").textContent).toBe("waiting on you");
  });

  it("NO CHIPS ROW: the seats show the panel; the header keeps its buttons", async () => {
    api.responses["/agents/threads/t1"] = thread(TALK);
    renderTable();
    await screen.findByText("hello there");
    expect(screen.getByTestId("table-seats")).toBeInTheDocument();
    // The old chip titles ("<name> — <role> (<source>)") are gone...
    expect(screen.queryByTitle(/\((builtin|dynamic|remote)\)$/)).toBeNull();
    // ...each name is said once, at its seat (the bubble names the speaker).
    expect(screen.getAllByText("remy")).toHaveLength(1);
    expect(within(seat("dynamic:remy")).getByText("remy")).toBeInTheDocument();
    // The header's own controls stay.
    expect(screen.getByRole("button", { name: /Edit panel/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Extract and add to memory/ })).toBeInTheDocument();
  });

  it("A PROJECT ROOM's seat detail assigns work to that project by default", async () => {
    hookData.byPath["/projects"] = {
      projects: [
        { id: "p0", name: "Another", status: "active" },
        { id: "p1", name: "Alpha", status: "active" },
      ],
    };
    hookData.byPath["/agents/builder/inbox"] = {
      inbox: { queued: [], claimed: [], running: [], blocked: [], recent: [] },
      health: null,
    };
    api.responses["/agents/threads/t1"] = thread(TALK);
    renderTable({ projectId: "p1", projectName: "Alpha" });
    await screen.findByText("hello there");
    fireEvent.click(seat("builtin:builder"));
    const modal = await screen.findByTestId("agent-seat-modal");
    const select = (await within(modal).findByLabelText("Project (optional)")) as HTMLSelectElement;
    expect(select.value).toBe("p1");
  });

  it("…and a GENERAL room's seat detail still defaults to no project", async () => {
    hookData.byPath["/projects"] = {
      projects: [{ id: "p1", name: "Alpha", status: "active" }],
    };
    hookData.byPath["/agents/builder/inbox"] = {
      inbox: { queued: [], claimed: [], running: [], blocked: [], recent: [] },
      health: null,
    };
    api.responses["/agents/threads/t1"] = thread(TALK);
    renderTable();
    await screen.findByText("hello there");
    fireEvent.click(seat("builtin:builder"));
    const modal = await screen.findByTestId("agent-seat-modal");
    const select = (await within(modal).findByLabelText("Project (optional)")) as HTMLSelectElement;
    expect(select.value).toBe("");
  });
});

/* --------------------------------------------- what a seat was given ---- */

describe("a project room says what each seat was given", () => {
  const ROOM: ThreadEntry[] = [
    { who: "user", content: "status?", at: "2026-10-06T10:00:00Z" },
    { who: "builtin:builder", content: "on track", at: "2026-10-06T10:00:05Z" },
    {
      who: "remote:box",
      content: "no idea about the files",
      at: "2026-10-06T10:00:09Z",
      ungrounded: true,
      ungrounded_reason: "runs on claude-cli",
    } as ThreadEntry,
  ];

  it("an UNGROUNDED reply carries a quiet line naming why; a grounded one carries none", async () => {
    api.responses["/agents/threads/t1"] = thread(ROOM);
    renderTable({ projectId: "p1", projectName: "Alpha" });
    await screen.findByText("no idea about the files");
    const lines = screen.getAllByTestId("entry-ungrounded");
    expect(lines).toHaveLength(1);
    expect(lines[0].textContent).toBe("Not given the project's files — runs on claude-cli");
    // It sits under the remote's reply, not the builder's.
    expect(
      lines[0].closest("div")?.textContent?.includes("no idea about the files"),
    ).toBe(true);
  });

  it("the line's words: the daemon's reason, else the provider, else the bare fact; nothing unflagged", () => {
    const base = { who: "remote:box", content: "x", at: "t" } as ThreadEntry;
    expect(ungroundedNote(base)).toBe("");
    expect(ungroundedNote({ ...base, ungrounded: true, provider: "openai" } as ThreadEntry)).toBe(
      "Not given the project's files — runs on openai",
    );
    expect(ungroundedNote({ ...base, ungrounded: true } as ThreadEntry)).toBe(
      "Not given the project's files",
    );
    expect(
      ungroundedNote({ ...base, ungrounded: false, ungrounded_reason: "runs on x" } as ThreadEntry),
    ).toBe("");
  });

  it("a REMOTE seat in a project room says it sees only your messages; not in a general room, not a local seat", async () => {
    api.responses["/agents/threads/t1"] = thread(TALK);
    renderTable({ projectId: "p1", projectName: "Alpha" });
    await screen.findByText("hello there");
    expect(seat("remote:box").getAttribute("title")).toMatch(/sees only your messages$/);
    // Its own status note is kept in front of it (an offline remote).
    expect(seat("remote:box").getAttribute("title")).toMatch(/offline/);
    expect(seat("builtin:builder").getAttribute("title") ?? "").not.toMatch(/sees only/);
    cleanup();
    api.responses["/agents/threads/t1"] = thread(TALK);
    renderTable();
    await screen.findByText("hello there");
    expect(seat("remote:box").getAttribute("title") ?? "").not.toMatch(/sees only/);
  });
});
