import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * The agents page, tidied (v1.180.0) — the user's own report, point by point.
 *
 *   "The agents module appears to be a bit cluttered. I enjoy the roster on the
 *    left, but it seems the round table card could be below the roster. The
 *    roster list should be collapsable for a cleaner look. The post a job seems
 *    to be redundant because if i choose to start a thread with an agent that
 *    would be the start of posting a new job."
 *
 * WHAT THESE TESTS GUARD:
 *  - THE STACK. The conversation comes AFTER the roster in the document and in
 *    the SAME column — the page is no longer a two-column grid with the
 *    transcript squeezed beside a 15rem rail. Both halves are asserted: the
 *    order (so nothing floats the roster back beside it) and the container (so
 *    "after in the DOM" cannot be satisfied by a grid that paints it to the
 *    right anyway);
 *  - THE FOLD. The roster collapses and reopens, the choice survives leaving the
 *    page, and the default is OPEN — a roster nobody asked to hide must not come
 *    back hidden. Folded, the header still says what it is hiding;
 *  - THE GEAR SURVIVES THE FOLD. Agent configuration lives behind it and nowhere
 *    else since v1.179.0; tidying the list must not make creating an agent
 *    unreachable;
 *  - NO SECOND FRONT DOOR. The standalone "Post a job" disclosure is gone from
 *    this page, and the capability is not: the rail's Give-work opens the THREAD
 *    with that agent and arms the composer's dispatch target, which is the
 *    "start a thread = start a job" the report asked for;
 *  - an older daemon with no /agents/roster keeps the pre-rail page exactly:
 *    no rail, no gear, no fold — and Give work + Set up agents still standing in
 *    the flow, because there is no rail there to hang them off.
 */

const hooks = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  /** Per-path failures, so a daemon that predates one route can be modelled
   *  without faking the others (the thread routes 404 below). */
  errors: {} as Record<string, { status: number; message: string } | undefined>,
  posts: [] as Array<{ path: string; body: unknown }>,
  /** Every DISTINCT assign object the composer was handed, in order.
   *  Identity, not value: RoundTable re-arms its target from an effect keyed on
   *  the `assign` OBJECT, so "the page aimed the work again at the agent it was
   *  already aimed at" is a real, and otherwise invisible, event. Tracked in a
   *  WeakSet because the test double re-renders constantly (see the
   *  framer-motion note) and counting renders would count noise. */
  arms: [] as string[],
  armed: new WeakSet<object>(),
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: (path && hooks.errors[path]) || null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: (path && hooks.errors[path]) || null,
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
      return Promise.resolve({
        id: "t-new",
        title: "Talk with analyst",
        participants: [],
        message_count: 0,
        updated_at: "2026-08-15T10:00:00Z",
        status: "active",
      });
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

// The framer-motion double remounts children on every render — which is what
// made the v1.178.0 selection bug visible, so it stays.
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

/**
 * The thread surface stands in for RoundTable — which owns the composer, and
 * as of v1.180.0 the job dispatch inside it (Doer B). What this page is
 * responsible for is WHICH thread is open and WHO the work is aimed at, so the
 * double reports exactly those two props back onto the DOM.
 */
vi.mock("@/components/agents/RoundTable", () => ({
  RoundTable: ({
    threadId,
    assign,
    roster,
  }: {
    threadId: string;
    assign?: { kind: string; name: string } | null;
    roster?: unknown[];
  }) => {
    if (assign && !hooks.armed.has(assign)) {
      hooks.armed.add(assign);
      hooks.arms.push(`${assign.kind}:${assign.name}`);
    }
    return (
    <div
      data-testid="round-table"
      data-assign={assign ? `${assign.kind}:${assign.name}` : ""}
      data-roster={String(roster?.length ?? 0)}
    >
      {threadId}
    </div>
    );
  },
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
    stats: { sessions: 23, success_rate: 0.87, avg_score: null, trend: null },
  },
  {
    name: "remote:vr-assistant",
    kind: "remote",
    description: "The other machine",
    delegable: true,
    healthy: true,
    stats: null,
    last_active: "2026-08-14T09:00:00+00:00",
    last_message: "Reviewed the draft — two issues flagged.",
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

const THREADS = [
  {
    id: "t-other",
    title: "Newest thread",
    participants: [],
    message_count: 3,
    updated_at: "2026-08-14T10:00:00Z",
  },
  {
    id: "t-assistant",
    title: "Talk with vr-assistant",
    participants: [
      { key: "remote:vr-assistant", source: "remote", name: "vr-assistant", role: "" },
    ],
    message_count: 5,
    updated_at: "2026-08-13T10:00:00Z",
  },
];

const ROSTER_KEY = "ij_agents_roster_open";

const stack = () => screen.getByTestId("agents-stack");
const pane = () => screen.getByTestId("roster-pane");
const rail = () => screen.getByTestId("roster-rail");
const fold = () => screen.getByTestId("roster-toggle");
const gear = () => screen.getByTestId("roster-gear");
const table = () => screen.getByTestId("round-table");
const railButton = (name: RegExp | string) =>
  within(rail()).getByRole("button", { name });
const railGiveWork = () => screen.getByRole("button", { name: /^Give work$/ });

/* v1.214.0 — WHERE THE ROSTER LIVES NOW. The page's left card is the THREAD
   list; the faces are in a dialog behind the icon at its foot. `RosterStrip`
   itself is unchanged, so the component-level tests in this file still drive
   it directly; the page-level ones open the room first. */
const room = () => screen.getByTestId("agents-modal");
const openRoom = () => {
  fireEvent.click(screen.getByTestId("roster-gear"));
  return room();
};
const roomButton = (name: RegExp | string) =>
  within(screen.getByTestId("agents-modal-list")).getByRole("button", { name });
const roomGiveWork = () =>
  within(room()).getByRole("button", { name: /^Give work$/ });
const threadRail = () => screen.getByTestId("agents-thread-rail");

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
  hooks.errors = {};
  hooks.posts = [];
  hooks.arms = [];
  hooks.armed = new WeakSet<object>();
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  window.localStorage.clear();
});
afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

/* ------------------------------------------------------------- the stack --- */

describe("the left card is the THREAD list, and it fills the app", () => {
  // REPLACES "the roster sits BELOW the round-table" (v1.180.0/v1.184.0).
  // Those tests pinned a composition this release deliberately retires: the
  // roster was the rail and the threads were a SECOND 16rem rail beside the
  // conversation. Reported: the left pane should be "a new fixed full lenth
  // and scrollable left card that is the height length of the app below the
  // very top pane", holding the threads. What is asserted here is the shape
  // that replaced it, at the same level of detail — the container is the
  // mechanism, so the container is what gets checked.

  it("puts the thread rail BESIDE the conversation, and nothing else on the page", async () => {
    render(<AgentsPage />);
    await waitFor(() => expect(table()).toBeTruthy());
    const module = screen.getByTestId("agents-room");
    expect(module.contains(threadRail())).toBe(true);
    expect(module.contains(table())).toBe(true);
    // A row at md and up, so the rail is a column and not a band above the
    // transcript; a plain stack below it, where 17rem beside a transcript is
    // two unusable columns.
    expect(module.className).toMatch(/md:flex-row/);
    // The ROSTER is not on the page at all any more — it is behind the icon.
    expect(screen.queryByTestId("roster-pane")).toBeNull();
    expect(screen.queryByTestId("roster-rail")).toBeNull();
  });

  it("is the height of the app, with only its list scrolling", async () => {
    // The two halves of "fixed full length and scrollable": the module row is
    // pinned to the window height, and INSIDE the card exactly one region
    // scrolls. Both matter — a card that is full height but scrolls as a whole
    // carries its own footer away, which is the failure this replaces (a long
    // thread list used to push the roster's gear, the only door to agent
    // configuration, off the bottom of the page).
    render(<AgentsPage />);
    await waitFor(() => expect(table()).toBeTruthy());
    expect(screen.getByTestId("agents-room").className).toMatch(
      /md:h-\[calc\(100vh-4\.5rem\)\]/,
    );
    // Every thread row lives inside ONE scroll region...
    const scrollers = new Set(
      within(threadRail())
        .getAllByTitle(/Talk with|Thread /)
        .map((b) => b.closest("[class*='overflow-y-auto']"))
        .filter(Boolean),
    );
    expect(scrollers.size).toBe(1);
    // ...and the icon is NOT in it. Asserted against THAT region rather than
    // "any scrolling ancestor": in the real app the layout's own <main> is
    // `overflow-y-auto`, so the looser check would be true of every element on
    // the page and would pass for a reason that has nothing to do with this.
    const list = [...scrollers][0] as HTMLElement;
    expect(list.contains(screen.getByTestId("roster-gear"))).toBe(false);
    expect(threadRail().contains(screen.getByTestId("roster-gear"))).toBe(true);
  });

  it("draws each thread's panel as the agents' own faces, layered", async () => {
    // "the image of the related agent or agents (layered as they are now)".
    // The layering was already right; the contents were coloured initials
    // while every other surface in the app drew the agent.
    render(<AgentsPage />);
    await waitFor(() => expect(table()).toBeTruthy());
    // By TITLE, not by accessible name: the row's delete button is named
    // "Delete Talk with vr-assistant", so a name match finds two.
    const row = within(threadRail()).getByTitle("Talk with vr-assistant");
    expect(within(row).getAllByTestId("agent-face").length).toBeGreaterThan(0);
  });
});

/* ------------------------------------------- one door to a new thread ----- */

describe("starting a thread has exactly one door", () => {
  // Reported (v1.214.1): "in the agents module there are 2 areas to start a
  // new thread and it should be one." There were THREE — the page header, the
  // thread rail's own header, and the empty conversation panel — and NOTHING
  // in this suite touched any of them, which is how three of one control
  // accumulated without anyone noticing. The rail keeps it: starting a thread
  // is a list operation and the rail is on screen at every width and in every
  // state, including the empty one.

  it("offers ONE control, and it is in the thread rail", async () => {
    render(<AgentsPage />);
    await waitFor(() => expect(threadRail()).toBeTruthy());
    const doors = screen.getAllByTitle("Start a new agent thread");
    expect(doors).toHaveLength(1);
    expect(threadRail().contains(doors[0])).toBe(true);
    // ...and no second one wearing different words anywhere on the page.
    expect(screen.queryByRole("button", { name: /New thread/ })).toBeNull();
  });

  it("that one control opens the panel picker", async () => {
    render(<AgentsPage />);
    await waitFor(() => expect(threadRail()).toBeTruthy());
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByTitle("Start a new agent thread"));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.getAttribute("aria-label")).toBe("New agent thread");
  });

  it("the empty conversation panel points AT the rail instead of repeating it", async () => {
    // v1.180.0 put a button here so the empty state was "not a dead end". The
    // dead end it was closing does not exist any more: the rail — with its New
    // — is beside this panel at md and above it below md, in the same view. So
    // the panel says where to go rather than being a third of the same button.
    hooks.api["/agents/threads"] = { threads: [] };
    render(<AgentsPage />);
    await waitFor(() => expect(threadRail()).toBeTruthy());
    expect(screen.getByText(/Press New in the rail/)).toBeTruthy();
    expect(screen.getAllByTitle("Start a new agent thread")).toHaveLength(1);
  });

  it("the module's name is top-left INSIDE the thread rail", async () => {
    // v1.214.3: "the title Agents should be on the top left inside the card of
    // the threads and the chat box pushed up so it looks more clean." It used
    // to be a PageHeader spanning the conversation column.
    render(<AgentsPage />);
    await waitFor(() => expect(threadRail()).toBeTruthy());
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1.textContent).toContain("Agents");
    expect(threadRail().contains(h1)).toBe(true);
    // ONE h1. Rendering the rail's title while leaving the page header
    // standing would be a tidier-looking page with a broken outline.
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("nothing stands above the conversation any more", async () => {
    // The other half of the report — "the chat box pushed up". The transcript
    // used to begin a heading's height below the top of its column while the
    // rail beside it began at zero, so the two never lined up. Asserted
    // structurally: the conversation's column carries no heading at all, and
    // the transcript is its FIRST element.
    // The column is addressed by TESTID, not by `closest("div.flex-1")` — the
    // transcript's own wrapper carries `flex-1` too, so that selector stopped
    // one level short and the assertion could never have seen a heading above
    // it. Caught by mutation: restoring the page header left this green while
    // its two siblings went red.
    render(<AgentsPage />);
    await waitFor(() => expect(table()).toBeTruthy());
    const column = screen.getByTestId("agents-conversation");
    expect(column.contains(table())).toBe(true);
    expect(within(column).queryByRole("heading")).toBeNull();
    // ...and the transcript is the column's FIRST child, so nothing at all
    // stands between the top of the module and the conversation.
    expect(column.firstElementChild!.contains(table())).toBe(true);
  });

  it("the name still explains itself on demand, the same way every module does", async () => {
    // Moving the title must not cost it the popover that replaced the printed
    // subtitle in v1.214.1 — it is the SAME `ModuleTitle`, so this is really a
    // guard against someone re-writing a plain <h1> into the rail.
    render(<AgentsPage />);
    await waitFor(() => expect(threadRail()).toBeTruthy());
    const trigger = screen.getByTestId("page-title");
    const tip = screen.getByTestId("page-subtitle");
    expect(threadRail().contains(trigger)).toBe(true);
    expect(tip.getAttribute("data-open")).toBe("false");
    // ...and it is wired as the title's description even while invisible.
    expect(trigger.getAttribute("aria-describedby")).toBe(tip.getAttribute("id"));
    fireEvent.mouseEnter(trigger);
    await waitFor(() => expect(tip.getAttribute("data-open")).toBe("true"));
    expect(tip.textContent).toMatch(/round-table of agents/);
  });
});

/* ------------------------------------------------ the icon, bottom left ---- */

describe("the agents icon at the foot of the rail", () => {
  // REPLACES "the roster list collapses" (v1.180.0). The fold existed because
  // the roster was a long list in the page and the user wanted it out of the
  // way; with the roster behind a dialog there is no list in the page to fold.
  // The guarantee the fold was protecting — "keeps the GEAR reachable" — is
  // what survives, and it is now structural rather than a state to maintain.

  it("opens the room, and the room carries every kind of agent", async () => {
    render(<AgentsPage />);
    await waitFor(() => expect(threadRail()).toBeTruthy());
    expect(screen.queryByTestId("agents-modal")).toBeNull();
    openRoom();
    await waitFor(() => expect(room()).toBeTruthy());
    for (const name of ["supervisor", "builder", "analyst"]) {
      expect(roomButton(name)).toBeTruthy();
    }
    expect(roomButton(/vr-assistant/)).toBeTruthy();
  });

  it("stays put however long the thread list gets", async () => {
    // The fold's real job, kept: configuration must never become unreachable
    // by having too many threads. The icon is outside the scrolling region, so
    // this holds by construction instead of by a persisted flag.
    hooks.api["/agents/threads"] = {
      threads: Array.from({ length: 60 }, (_, i) => ({
        id: `t-${i}`,
        title: `Thread ${i}`,
        participants: [],
        message_count: 1,
        updated_at: "2026-08-20T10:00:00Z",
      })),
    };
    render(<AgentsPage />);
    await waitFor(() => expect(screen.getByTestId("roster-gear")).toBeVisible());
    const list = within(threadRail())
      .getAllByTitle(/Thread /)[0]
      .closest("[class*='overflow-y-auto']") as HTMLElement;
    expect(list).toBeTruthy();
    expect(list.contains(screen.getByTestId("roster-gear"))).toBe(false);
  });

  it("names who the page is working with, so the selection is still visible", async () => {
    // The pick used to show as a highlighted row in the roster rail. With the
    // roster behind a door, a selection with nothing on screen to show it is
    // page state the user cannot see — so the rail says it.
    render(<AgentsPage />);
    await waitFor(() => expect(threadRail()).toBeTruthy());
    expect(screen.getByTestId("rail-picked").textContent).toMatch(/nobody picked/);
    openRoom();
    fireEvent.click(roomButton("analyst"));
    await waitFor(() =>
      expect(screen.getByTestId("rail-picked").textContent).toMatch(
        /working with analyst/,
      ),
    );
  });

  it("offers no fold on the pre-rail composition", () => {
    // A `collapsed` with no toggle would be a section with no way back, so the
    // strip ignores it outright. (Component-level: `RosterStrip` still ships
    // the fold for the older-daemon page, which is the only page that has a
    // roster list in the flow.)
    render(<RosterStrip entries={ROSTER} collapsed />);
    expect(screen.queryByTestId("roster-toggle")).toBeNull();
    expect(screen.getByLabelText("Choose an agent")).toBeVisible();
  });
});

/* ------------------------------------------- one front door, not two ------- */

describe("posting a job is starting a thread", () => {
  it("has no standalone job-post disclosure left on the page", async () => {
    render(<AgentsPage />);
    await waitFor(() => expect(table()).toBeTruthy());
    expect(screen.queryByRole("button", { name: /Post a job/ })).toBeNull();
    expect(document.getElementById("job-post-panel")).toBeNull();
    // The form itself is not mounted-and-hidden either — it moved into the
    // composer, it did not go into a fold.
    expect(screen.queryByLabelText("Who takes it")).toBeNull();
    expect(screen.queryByLabelText("Job")).toBeNull();
  });

  it("Give work opens the thread with that agent and arms the composer", async () => {
    render(<AgentsPage />);
    await waitFor(() => expect(threadRail()).toBeTruthy());
    openRoom();
    fireEvent.click(roomButton("analyst"));
    fireEvent.click(roomGiveWork());
    // Both facts inside the waitFor: the POST and the state it drives land in
    // separate commits, and asserting the second after awaiting the first is
    // the shape that flaked CI twice.
    await waitFor(() => {
      expect(hooks.posts.map((p) => p.path)).toEqual(["/agents/threads"]);
      expect(table().textContent).toBe("t-new");
      expect(table().getAttribute("data-assign")).toBe("dynamic:analyst");
      // ...and the page moves the user TO the surface it just armed. Carried
      // over from job-post-v1166's deleted page test: a Give-work that arms a
      // composer somewhere off screen, silently, is a button that did nothing
      // the user can see.
      expect(window.HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
    });
    // The composer also gets the page's own roster rows, so its target list
    // never disagrees with the room the job was aimed from.
    expect(table().getAttribute("data-roster")).toBe(String(ROSTER.length));
    // The room GETS OUT OF THE WAY once it has opened a thread — leaving a
    // dialog on top of the conversation it just started would hide the answer.
    expect(screen.queryByTestId("agents-modal")).toBeNull();
  });

  it("Give work re-aims the composer even at the agent already selected", async () => {
    // FOUND BY MUTATION while retargeting the v1.178.0/v1.179.0 tests: deleting
    // `setAssign` from the page's Give-work handler left every page test green,
    // because the FACE CLICK that precedes it has already armed the same agent.
    // The gap is real and invisible on screen: the composer re-reads its target
    // from an effect keyed on the assign OBJECT, so a user who picked the
    // analyst, then changed the target by hand in Job options, then pressed
    // Give work on the analyst again would watch the button do nothing at all —
    // their manual choice silently outranking the control they just used.
    // Identity is therefore what is counted; the value alone cannot see it.
    render(<AgentsPage />);
    await waitFor(() => expect(threadRail()).toBeTruthy());
    openRoom();
    fireEvent.click(roomButton("analyst"));
    await waitFor(() => expect(hooks.arms).toEqual(["dynamic:analyst"]));
    fireEvent.click(roomGiveWork());
    await waitFor(() =>
      expect(hooks.arms).toEqual(["dynamic:analyst", "dynamic:analyst"]),
    );
  });

  it("a face click aims the composer without creating anything", async () => {
    // The preselect that used to land in the job-post card's "Who takes it"
    // select now lands in the composer — same page state, new home. Clicking a
    // FACE still POSTs nothing: that stays behind Talk and Give work.
    render(<AgentsPage />);
    await waitFor(() => expect(threadRail()).toBeTruthy());
    openRoom();
    fireEvent.click(roomButton("analyst"));
    await waitFor(() =>
      expect(table().getAttribute("data-assign")).toBe("dynamic:analyst"),
    );
    expect(hooks.posts).toHaveLength(0);
  });

  it("leaves no STALE target behind when the next pick cannot take work", async () => {
    // Carried over from the v1.178.0 adversarial review, which asserted it
    // through the job card that no longer exists here. The bug is the same
    // wherever the target lives: pick a workable agent, then an offline one,
    // and the highlight moves while the work stays aimed at the first.
    // An un-workable pick RESETS the target to the Team — the honest answer,
    // since neither the supervisor nor an offline remote can take a session.
    render(<AgentsPage />);
    await waitFor(() => expect(threadRail()).toBeTruthy());
    openRoom();
    fireEvent.click(roomButton("analyst"));
    await waitFor(() =>
      expect(table().getAttribute("data-assign")).toBe("dynamic:analyst"),
    );
    fireEvent.click(roomButton(/down-box/));
    await waitFor(() => {
      expect(roomButton(/down-box/).getAttribute("aria-current")).toBe("true");
      expect(table().getAttribute("data-assign")).toBe("builtin:__team__");
    });
    // Same for the non-delegable supervisor, reached from a live target.
    fireEvent.click(roomButton("analyst"));
    await waitFor(() =>
      expect(table().getAttribute("data-assign")).toBe("dynamic:analyst"),
    );
    fireEvent.click(roomButton("supervisor"));
    await waitFor(() =>
      expect(table().getAttribute("data-assign")).toBe("builtin:__team__"),
    );
  });

  it("Give work reuses the existing 1:1 thread instead of posting a second one", async () => {
    render(<AgentsPage />);
    await waitFor(() => expect(threadRail()).toBeTruthy());
    openRoom();
    fireEvent.click(roomButton(/vr-assistant/));
    fireEvent.click(roomGiveWork());
    await waitFor(() => {
      expect(table().textContent).toBe("t-assistant");
      expect(table().getAttribute("data-assign")).toBe("remote:vr-assistant");
    });
    expect(hooks.posts).toHaveLength(0);
  });
});

/* ------------------------------------------------------ honest degradation --- */

describe("an older daemon that does not serve /agents/roster", () => {
  beforeEach(() => {
    delete hooks.api["/agents/roster"];
  });

  it("keeps the pre-rail page: no rail, no gear, no fold, both surfaces in the flow", async () => {
    render(<AgentsPage />);
    expect(screen.queryByTestId("roster-rail")).toBeNull();
    expect(screen.queryByTestId("roster-gear")).toBeNull();
    expect(screen.queryByTestId("roster-pane")).toBeNull();
    expect(screen.queryByTestId("roster-toggle")).toBeNull();
    // With no rail to hang them off, hiding these would delete two
    // capabilities from the daemons least able to spare them.
    expect(
      (screen.getByLabelText("Who takes it") as HTMLSelectElement).value,
    ).toBe("__team__");
    expect(screen.getByText("Set up agents")).toBeTruthy();
    expect(screen.queryByText(/not found/i)).toBeNull();
    await waitFor(() => expect(table().textContent).toBe("t-other"));
  });
});

describe("a daemon that serves the roster but not the thread routes", () => {
  beforeEach(() => {
    hooks.errors["/agents/threads"] = { status: 404, message: "Not Found" };
  });

  it("still has somewhere to give work, since there is no composer", async () => {
    // The one combination where "dispatch moved into the composer" could mean
    // "dispatch is gone": a rail with a Give-work button and no thread surface
    // anywhere on the page. The job card stands in for the missing composer.
    render(<AgentsPage />);
    await waitFor(() => expect(rail()).toBeVisible());
    expect(screen.queryByTestId("round-table")).toBeNull();
    expect(
      (screen.getByLabelText("Who takes it") as HTMLSelectElement).value,
    ).toBe("__team__");
    // And Give work aims THAT card rather than POSTing to routes the daemon
    // does not have.
    fireEvent.click(railButton("analyst"));
    fireEvent.click(railGiveWork());
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Who takes it") as HTMLSelectElement).value,
      ).toBe("custom:analyst"),
    );
    expect(hooks.posts).toHaveLength(0);
  });
});

/* ------------------------------------------------- one roster, never two --- */

describe("the roster renders exactly once", () => {
  it("lives in the room, and nowhere in the page behind it", async () => {
    // v1.184.0's guard, restated for the surface that replaced its subject.
    // The failure it exists to catch is unchanged: a roster rendered in two
    // places is two lists that can disagree about who exists.
    render(<AgentsPage />);
    await waitFor(() => expect(table()).toBeTruthy());
    expect(screen.queryByTestId("roster-pane")).toBeNull();
    openRoom();
    await waitFor(() => expect(room()).toBeTruthy());
    expect(screen.getAllByTestId("agents-modal-list")).toHaveLength(1);
    // Still nothing in the page itself — the dialog is portalled OUT of it.
    expect(screen.queryByTestId("roster-rail")).toBeNull();
  });

  it("falls back to the page flow when the daemon has no thread routes", async () => {
    // v1.184.0 REGRESSION GUARD, carried forward. Moving the roster into a
    // surface that needs threads made it vanish on every path without them —
    // a daemon serving no thread routes lost its roster entirely, and only the
    // degraded-path test noticed. The room needs BOTH the roster and the
    // thread routes; without either, the older stacked page keeps the roster
    // where it has always been.
    hooks.errors["/agents/threads"] = { status: 404, message: "Not Found" };
    render(<AgentsPage />);
    await waitFor(() => expect(screen.getByTestId("roster-pane")).toBeTruthy());
    expect(screen.getAllByTestId("roster-pane")).toHaveLength(1);
    expect(screen.queryByTestId("round-table")).toBeNull();
    expect(screen.queryByTestId("agents-room")).toBeNull();
  });
});
