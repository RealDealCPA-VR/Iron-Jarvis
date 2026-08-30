import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * The agent rail (v1.178.0, P2) — the Agents page as a ROOM, not a form.
 *
 * WHAT THESE TESTS GUARD:
 *  - the faces stand in a persistent LEFT RAIL: one per roster agent, drawn
 *    face or stored portrait, offline said in words and not just in colour;
 *  - clicking a face SELECTS that agent and the selection drives the page —
 *    THE WORK IS AIMED AT IT, and an existing 1:1 thread with exactly that
 *    agent opens ("continue working with"). It must never CREATE one: a click
 *    on a portrait is not consent to POST;
 *
 * RETARGETED FOR v1.180.0. "The work is aimed at it" used to be read off the
 * job-post card's "Who takes it" select, and that standalone surface is gone
 * from this page — dispatch moved INTO the thread composer, because "if i
 * choose to start a thread with an agent that would be the start of posting a
 * new job". The BEHAVIOUR is unchanged and so are these tests' claims; only the
 * place the answer is read from moved. The RoundTable double below reports the
 * page's `assign` prop back onto the DOM as `data-assign="<kind>:<name>"`, the
 * same idiom agents-layout-v1180.test.tsx uses, so "the composer is aimed at
 * agent X" stays assertable from the page. The pre-rail (older daemon) path
 * still renders JobPostCard, so the degradation test below still reads the
 * card's own select — there, it is still the surface.
 *  - the selection is ANNOUNCED (aria-current), not just tinted;
 *  - the gear-with-a-face opens BOTH setup doors (local + remote) through the
 *    card's own disclosure, so its persisted state stays honest;
 *  - an older daemon that doesn't serve /agents/roster leaves the page exactly
 *    as it was — no rail, no gear, no empty column, no error.
 */

const hooks = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: unknown }>,
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError,
    API_BASE: "http://test",
    ijToken: () => "tok-1",
    get: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    post: (path: string, body: unknown) => {
      hooks.posts.push({ path, body });
      return Promise.resolve({ id: "s-new", status: "active" });
    },
  };
});

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial",
    "animate",
    "exit",
    "transition",
    "variants",
    "whileHover",
  ]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  return {
    AnimatePresence: ({ children }: { children?: unknown }) =>
      createElement(Fragment, null, children as never),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => tagFor(String(tag)),
    }),
  };
});

// The round-table is a live surface with its own polling; what these tests need
// from it is WHICH thread the page told it to open and — since v1.180.0 — WHO
// the page aimed the work at, because the dispatch target lives in the composer
// now. Same double as agents-layout-v1180.test.tsx: one idiom, not two.
vi.mock("@/components/agents/RoundTable", () => ({
  RoundTable: ({
    threadId,
    assign,
    roster,
  }: {
    threadId: string;
    assign?: { kind: string; name: string } | null;
    roster?: unknown[];
  }) => (
    <div
      data-testid="round-table"
      data-assign={assign ? `${assign.kind}:${assign.name}` : ""}
      data-roster={String(roster?.length ?? 0)}
    >
      {threadId}
    </div>
  ),
}));

import { RosterStrip, type RosterEntry } from "@/components/agents/RosterStrip";
import AgentsPage from "@/app/agents/page";

const ROSTER: RosterEntry[] = [
  {
    name: "supervisor",
    kind: "builtin",
    description: "Plans and delegates",
    delegable: false,
    healthy: true,
    stats: null,
  },
  {
    name: "builder",
    kind: "builtin",
    description: "Builds things",
    delegable: true,
    healthy: true,
    stats: null,
  },
  {
    name: "custom:analyst",
    kind: "dynamic",
    description: "Your analyst",
    delegable: true,
    healthy: true,
    stats: null,
  },
  {
    name: "remote:opus-box",
    kind: "remote",
    description: "The other machine",
    delegable: true,
    healthy: true,
    stats: null,
    last_active: "2026-08-14T09:00:00+00:00",
    avatar: "/agents/opus-box/avatar",
  },
  {
    name: "remote:down-box",
    kind: "remote",
    description: "Currently unreachable",
    delegable: true,
    healthy: false,
    stats: null,
  },
];

/** Two threads: the newest (auto-selected) is a panel of nobody, and the 1:1
 *  with the analyst is NOT the default — so "the rail opened it" can't pass by
 *  accident. */
const THREADS = [
  {
    id: "t-other",
    title: "Newest thread",
    participants: [],
    message_count: 3,
    updated_at: "2026-08-14T10:00:00Z",
  },
  {
    id: "t-analyst",
    title: "Talk with analyst",
    participants: [
      { key: "dynamic:analyst", source: "dynamic", name: "analyst", role: "" },
    ],
    message_count: 5,
    updated_at: "2026-08-13T10:00:00Z",
  },
];

const rail = () => screen.getByTestId("roster-rail");
const railButton = (name: RegExp | string) =>
  within(rail()).getByRole("button", { name });
/**
 * WHERE THE ROSTER LIVES SINCE v1.214.0. The rail on the PAGE is the thread
 * list now; the faces moved into the agents room, a real dialog behind the
 * icon at the foot of that rail. `RosterStrip` itself is unchanged and the
 * component-level tests above still render it directly — these page-level ones
 * open the room first and click the same names in it.
 */
const openRoom = () => {
  fireEvent.click(screen.getByTestId("roster-gear"));
  return screen.getByTestId("agents-modal");
};
const roomButton = (name: RegExp | string) =>
  within(screen.getByTestId("agents-modal-list")).getByRole("button", { name });
const table = () => screen.getByTestId("round-table");
/** WHO THE WORK IS AIMED AT, as the page tells the composer (v1.180.0).
 *  "" = nobody named yet, i.e. the thread's own agent decides. */
const aimedAt = () => table().getAttribute("data-assign");
/** Only the pre-rail composition still carries the standalone card. */
const targetSelect = () =>
  screen.getByLabelText("Who takes it") as HTMLSelectElement;

beforeEach(() => {
  hooks.api = {
    "/agents": { builtin: ["supervisor", "builder"], dynamic: [] },
    "/agents/remote": { agents: [] },
    "/models": { models: [] },
    "/agents/roster": { roster: ROSTER },
    "/agents/threads": { threads: THREADS },
    "/sessions": { sessions: [] },
    "/projects": { projects: [] },
  };
  hooks.posts = [];
  // jsdom has no scrollIntoView; the page's handlers call it.
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  window.localStorage.clear();
});
afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

/* ------------------------------------------------------------- the column --- */

describe("the rail — a face per agent", () => {
  it("draws one clickable face per roster agent", () => {
    render(<RosterStrip entries={ROSTER} onSelect={vi.fn()} />);
    const rows = within(rail()).getAllByRole("button");
    expect(rows).toHaveLength(ROSTER.length);
    // v1.179.0: a remote row now also carries its "Remote" pill — the user
    // asked for that indicator on every remote agent after it moved off the
    // deleted detail block. Strip the provenance words too, so this still
    // pins the NAMES and their order rather than being loosened away.
    expect(
      rows.map((b) =>
        b.textContent?.replace(/offline/gi, "").replace(/remote/gi, "").trim(),
      ),
    ).toEqual([
      "supervisor",
      "builder",
      "analyst",
      "opus-box",
      "down-box",
    ]);
    // ...and the pill is genuinely there, on the remote rows only.
    const remoteRows = rows.filter((b) => /remote/i.test(b.textContent ?? ""));
    expect(remoteRows).toHaveLength(2); // opus-box + down-box
    // Four drawn faces + the one agent with a stored portrait = five faces.
    expect(within(rail()).getAllByTestId("agent-face")).toHaveLength(4);
    // The portrait is queried by NODE, not by alt text: beside its own visible
    // name it is decorative, so it carries alt="" and aria-hidden — a portrait
    // that announced the name a second time is a screen-reader stutter.
    const portraits = rail().querySelectorAll("img");
    expect(portraits).toHaveLength(1);
    expect((portraits[0] as HTMLImageElement).src).toContain(
      "http://test/agents/opus-box/avatar",
    );
    expect(portraits[0].getAttribute("alt")).toBe("");
  });

  it("says 'offline' in words, not only in colour", () => {
    render(<RosterStrip entries={ROSTER} onSelect={vi.fn()} />);
    // The accessible name of the unreachable remote's row carries the state —
    // a rose-tinted icon alone reaches nobody using a screen reader.
    expect(railButton(/down-box\s*offline/)).toBeTruthy();
    expect(within(rail()).getByRole("button", { name: "builder" })).toBeTruthy();
  });

  it("hands back the kind, the BARE name, and whether it can take work", () => {
    const onSelect = vi.fn();
    render(<RosterStrip entries={ROSTER} onSelect={onSelect} />);
    fireEvent.click(railButton("analyst"));
    expect(onSelect).toHaveBeenCalledWith("dynamic", "analyst", true);
    // Non-delegable and unhealthy are BOTH "can't take work" — the page must
    // not have to re-derive that rule.
    fireEvent.click(railButton("supervisor"));
    expect(onSelect).toHaveBeenLastCalledWith("builtin", "supervisor", false);
    fireEvent.click(railButton(/down-box/));
    expect(onSelect).toHaveBeenLastCalledWith("remote", "down-box", false);
  });
});

describe("the rail — the selection is announced", () => {
  it("marks the clicked face aria-current, and only that one", () => {
    render(<RosterStrip entries={ROSTER} onSelect={vi.fn()} />);
    fireEvent.click(railButton("analyst"));
    expect(railButton("analyst").getAttribute("aria-current")).toBe("true");
    const marked = within(rail())
      .getAllByRole("button")
      .filter((b) => b.getAttribute("aria-current") === "true");
    expect(marked).toHaveLength(1);
    // And the detail below follows the same selection (one source of truth).
    expect(screen.getByText("Your analyst")).toBeTruthy();
  });

  it("announces nobody as current until somebody is actually picked", () => {
    // ADVERSARIAL REVIEW. `selected` falls back to entries[0] so the detail
    // block below has something to preview — but a PREVIEW is not a SELECTION.
    // Marking the fallback row active tinted the first face and announced it
    // aria-current on first paint, and the roster's first entry is the
    // supervisor: `delegable: false`, the one agent that can never take work.
    // A user reading "supervisor is current" then typed a job and Post sent it
    // to the Team. Nothing is current until a face (or the narrow picker) says
    // so.
    render(<RosterStrip entries={ROSTER} onSelect={vi.fn()} />);
    expect(
      within(rail())
        .getAllByRole("button")
        .filter((b) => b.getAttribute("aria-current") === "true"),
    ).toHaveLength(0);
    fireEvent.click(railButton("builder"));
    expect(railButton("builder").getAttribute("aria-current")).toBe("true");
  });

  it("renders no column at all for a page with no selection to drive", () => {
    // The older in-flow composition: a column of faces nobody can select is
    // decoration, so it simply isn't drawn.
    render(<RosterStrip entries={ROSTER} />);
    expect(screen.queryByTestId("roster-rail")).toBeNull();
    expect(screen.getByLabelText("Choose an agent")).toBeTruthy();
  });
});

/* ------------------------------------------------- selection drives the page */

describe("the page — clicking a face in the agents room", () => {
  // RETARGETED FOR v1.214.0. Every claim below is the same claim v1.178.0
  // made; only the surface the face is clicked ON moved. The roster used to
  // stand as a rail in the page and now lives in a dialog behind the rail's
  // icon, because the left card is the THREAD list ("a new fixed full lenth
  // and scrollable left card"). What must not change is that a click on a
  // portrait selects, aims the work, opens an existing 1:1 thread — and
  // creates nothing.

  it("aims the work at that agent", async () => {
    render(<AgentsPage />);
    await waitFor(() => expect(aimedAt()).toBe(""));
    openRoom();
    fireEvent.click(roomButton("analyst"));
    await waitFor(() => expect(aimedAt()).toBe("dynamic:analyst"));
  });

  it("never aims the work at an agent that cannot take it", async () => {
    render(<AgentsPage />);
    openRoom();
    fireEvent.click(roomButton(/down-box/));
    // An unreachable remote (and the non-delegable supervisor) leave the work
    // aimed at the Team rather than naming a target the composer would quietly
    // fall back from at dispatch time.
    await waitFor(() => expect(aimedAt()).toBe("builtin:__team__"));
    fireEvent.click(roomButton("supervisor"));
    await waitFor(() => expect(aimedAt()).toBe("builtin:__team__"));
  });

  it("leaves no STALE target behind when the next pick cannot take work", async () => {
    // ADVERSARIAL REVIEW (v1.178.0). Skipping the preselect for an un-workable
    // agent is only half the job: pick a workable one FIRST and the old target
    // survives the next pick. Measured before the fix — the highlight on
    // "down-box", the work still aimed at "custom:analyst", so dispatching
    // would have sent the job to the analyst while the page's own selection
    // named the offline box. Still asserted, because the bug lives in the
    // page's state and is indifferent to which surface the click came from.
    render(<AgentsPage />);
    openRoom();
    fireEvent.click(roomButton("analyst"));
    await waitFor(() => expect(aimedAt()).toBe("dynamic:analyst"));
    fireEvent.click(roomButton(/down-box/));
    await waitFor(() => {
      expect(roomButton(/down-box/).getAttribute("aria-current")).toBe("true");
      expect(aimedAt()).toBe("builtin:__team__");
    });
    // Same for the non-delegable supervisor, reached from a live target.
    fireEvent.click(roomButton("analyst"));
    await waitFor(() => expect(aimedAt()).toBe("dynamic:analyst"));
    fireEvent.click(roomButton("supervisor"));
    await waitFor(() => expect(aimedAt()).toBe("builtin:__team__"));
  });

  it("continues an existing 1:1 thread with exactly that agent", async () => {
    render(<AgentsPage />);
    // The auto-selected thread is the newest one, not the analyst's.
    await waitFor(() =>
      expect(screen.getByTestId("round-table").textContent).toBe("t-other"),
    );
    openRoom();
    fireEvent.click(roomButton("analyst"));
    await waitFor(() =>
      expect(screen.getByTestId("round-table").textContent).toBe("t-analyst"),
    );
  });

  it("keeps the highlight through the re-render the click itself causes", async () => {
    // THE PAGE OWNS THE SELECTION. When the roster kept it internally, the
    // highlight was lost the moment the click's own state updates remounted
    // the strip: it then pointed at one agent while the work was aimed at
    // another — the page disagreeing with itself, silently. (The
    // framer-motion test double makes that remount happen every render, which
    // is exactly why this test can see it at all.)
    render(<AgentsPage />);
    openRoom();
    fireEvent.click(roomButton("analyst"));
    // BOTH assertions live INSIDE the waitFor. The highlight is page state and
    // the target reaches the composer one commit downstream of it; asserting
    // the second one after the await is the exact shape that flaked CI in
    // v1.177.1 — it happens to be synchronous under act() today, and "today"
    // is not a guarantee.
    await waitFor(() => {
      expect(roomButton("analyst").getAttribute("aria-current")).toBe("true");
      expect(aimedAt()).toBe("dynamic:analyst");
    });
  });

  it("creates nothing — an agent with no thread just becomes the selection", async () => {
    render(<AgentsPage />);
    openRoom();
    fireEvent.click(roomButton("builder"));
    await waitFor(() => expect(aimedAt()).toBe("builtin:builder"));
    // The open thread is untouched and no POST went out: starting a
    // conversation stays behind the explicit Talk button.
    expect(table().textContent).toBe("t-other");
    expect(hooks.posts).toHaveLength(0);
  });

  it("stays open while you browse — selecting is not a dismissal", async () => {
    // The room is where portraits and faces are chosen, so clicking one agent
    // must not shut the surface the user is configuring in. (Talk DOES close
    // it — see agents-room-v1214: it opens the thread the click asked for, and
    // leaving a dialog on top of that would hide the answer.)
    render(<AgentsPage />);
    openRoom();
    fireEvent.click(roomButton("analyst"));
    await waitFor(() => expect(aimedAt()).toBe("dynamic:analyst"));
    expect(screen.getByTestId("agents-modal")).toBeTruthy();
  });
});

/* -------------------------------------------------------------- the icon --- */

describe("the gear with a face", () => {
  it("opens the agents room — every agent, and the door to a new one", async () => {
    render(<AgentsPage />);
    expect(screen.queryByTestId("agents-modal")).toBeNull();
    openRoom();
    await waitFor(() => expect(screen.getByTestId("agents-modal")).toBeTruthy());
    // Local AND remote still live behind this ONE door, one click further in.
    fireEvent.click(screen.getByTestId("agents-modal-new"));
    await waitFor(() => expect(screen.getByText("Create an agent")).toBeTruthy());
    expect(screen.getByText("Connect a remote agent")).toBeTruthy();
  });

  it("says what it does — the drawn gear is decorative", () => {
    render(<AgentsPage />);
    const gear = screen.getByRole("button", {
      name: /creating a new local or remote agent/i,
    });
    expect(gear).toBe(screen.getByTestId("roster-gear"));
    const drawn = within(gear).getByTestId("gear-face");
    // Decorative graphic: aria-hidden with NO role — the button around it
    // already carries the name.
    expect(drawn.getAttribute("aria-hidden")).toBe("true");
    expect(drawn.getAttribute("role")).toBeNull();
  });

  it("opens nothing on arrival, however the page was left last time", async () => {
    // v1.179.0 established that configuration is "not shown unless the user
    // decided to configure an agent", and the stored flag from the older
    // card's disclosure must not resurrect that on a page that now uses a
    // DIALOG. A modal that reopened itself because it was open last week is a
    // page that starts by interrupting you.
    window.localStorage.setItem("ij_agents_setup_open", "1");
    render(<AgentsPage />);
    await waitFor(() => expect(screen.getByTestId("roster-gear")).toBeTruthy());
    expect(screen.queryByTestId("agents-modal")).toBeNull();

    fireEvent.click(screen.getByTestId("roster-gear"));
    await waitFor(() => expect(screen.getByTestId("agents-modal")).toBeTruthy());
    // ...and it closes again, so the icon is a door, not a one-way reveal.
    fireEvent.click(screen.getByTestId("roster-gear"));
    await waitFor(() => expect(screen.queryByTestId("agents-modal")).toBeNull());
  });
});

/* ------------------------------------------------------ honest degradation --- */

describe("an older daemon that does not serve /agents/roster", () => {
  beforeEach(() => {
    delete hooks.api["/agents/roster"];
  });

  it("leaves the page working exactly as it did — no rail, no gear, no error", async () => {
    render(<AgentsPage />);
    expect(screen.queryByTestId("roster-rail")).toBeNull();
    expect(screen.queryByTestId("roster-gear")).toBeNull();
    // Everything the page had before is still there and still live.
    expect(screen.getByText("Give work")).toBeTruthy();
    expect(targetSelect().value).toBe("__team__");
    await waitFor(() =>
      expect(screen.getByTestId("round-table").textContent).toBe("t-other"),
    );
  });
});
