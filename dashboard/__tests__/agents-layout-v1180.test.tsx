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

describe("the roster sits BELOW the round-table", () => {
  it("renders the roster after the conversation in document order", async () => {
    // INVERTED in v1.182.0, and the reason is the FOLD, not taste. With the
    // roster above, expanding the list pushed the conversation DOWN the
    // page — opening the roster moved the thing the user was reading, so
    // the fold cost something every time it was used. Below, the list grows
    // into empty space and the transcript never shifts.
    render(<AgentsPage />);
    await waitFor(() => expect(table()).toBeTruthy());
    expect(
      table().compareDocumentPosition(pane()) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("puts them in ONE column, not side by side", async () => {
    // Document order alone is not the ask: the old page ALSO had the rail
    // first in the DOM and then painted it as a left grid column beside the
    // transcript. The container is the mechanism, so the container is asserted
    // — a `grid-cols-[15rem_…]` back on this wrapper is the regression.
    render(<AgentsPage />);
    await waitFor(() => expect(table()).toBeTruthy());
    expect(within(stack()).getByTestId("roster-pane")).toBeTruthy();
    expect(within(stack()).getByTestId("round-table")).toBeTruthy();
    expect(stack().className).not.toMatch(/grid-cols/);
  });

  it("keeps the roster narrow and left, with the conversation at full width", async () => {
    // ADVERSARIAL REVIEW (v1.180.0): stacking alone is only half the ask. "I
    // enjoy the roster on the left" survives because the roster column is
    // width-capped — drop the cap and the roster becomes a full-width banner
    // sitting on top of the transcript, which is a different page from the one
    // the report asked for and which NO other assertion here would catch (the
    // order still holds, and the container still has no grid-cols).
    render(<AgentsPage />);
    await waitFor(() => expect(table()).toBeTruthy());
    const column = screen.getByTestId("roster-column");
    expect(column.className).toMatch(/w-\[17rem\]/);
    expect(within(column).getByTestId("roster-pane")).toBeTruthy();
    // ...and the conversation is OUTSIDE that cap, or "full width" would be a
    // 17rem transcript.
    expect(within(column).queryByTestId("round-table")).toBeNull();
  });
});

/* -------------------------------------------------------------- the fold --- */

describe("the roster list collapses", () => {
  it("starts open — a list nobody asked to hide stays visible", async () => {
    render(<AgentsPage />);
    await waitFor(() => expect(fold().getAttribute("aria-expanded")).toBe("true"));
    expect(rail()).toBeVisible();
  });

  it("folds away and comes back", async () => {
    render(<AgentsPage />);
    await waitFor(() => expect(fold().getAttribute("aria-expanded")).toBe("true"));
    fireEvent.click(fold());
    await waitFor(() => expect(fold().getAttribute("aria-expanded")).toBe("false"));
    // Hidden, not unmounted: the narrow <select>'s value and a long rail's
    // scroll position survive a fold, and `hidden` still takes the controls out
    // of the a11y tree.
    expect(rail()).not.toBeVisible();
    expect(screen.getByLabelText("Choose an agent")).not.toBeVisible();
    fireEvent.click(fold());
    await waitFor(() => expect(rail()).toBeVisible());
  });

  it("remembers the choice across visits", async () => {
    render(<AgentsPage />);
    await waitFor(() => expect(fold().getAttribute("aria-expanded")).toBe("true"));
    fireEvent.click(fold());
    await waitFor(() => expect(window.localStorage.getItem(ROSTER_KEY)).toBe("0"));
    cleanup();

    // A second visit, same browser: the page comes back folded.
    render(<AgentsPage />);
    await waitFor(() => expect(fold().getAttribute("aria-expanded")).toBe("false"));
    expect(rail()).not.toBeVisible();
    // ...and reopening is remembered too, or the fold would be a one-way door.
    fireEvent.click(fold());
    await waitFor(() => expect(window.localStorage.getItem(ROSTER_KEY)).toBe("1"));
  });

  it("says what it is hiding while folded, including who is selected", async () => {
    render(<AgentsPage />);
    await waitFor(() => expect(rail()).toBeVisible());
    fireEvent.click(railButton("analyst"));
    fireEvent.click(fold());
    await waitFor(() =>
      expect(fold().getAttribute("aria-expanded")).toBe("false"),
    );
    // The count is always true; the name only appears for a REAL pick.
    expect(fold().textContent).toContain(`Roster · ${ROSTER.length}`);
    expect(fold().textContent).toContain("analyst selected");
  });

  it("keeps the GEAR reachable while folded", async () => {
    // Configuration lives behind the gear and nowhere else (v1.179.0). If the
    // fold swallowed it, tidying the page would delete the only door to
    // creating an agent.
    render(<AgentsPage />);
    fireEvent.click(fold());
    await waitFor(() => expect(rail()).not.toBeVisible());
    expect(gear()).toBeVisible();
    fireEvent.click(gear());
    await waitFor(() => expect(screen.getByText("Create an agent")).toBeTruthy());
    // And the list is still folded — the gear opened setup, not the roster.
    expect(fold().getAttribute("aria-expanded")).toBe("false");
  });

  it("offers no fold on the pre-rail composition", () => {
    // A `collapsed` with no toggle would be a section with no way back, so the
    // strip ignores it outright.
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
    await waitFor(() => expect(rail()).toBeVisible());
    fireEvent.click(railButton("analyst"));
    fireEvent.click(railGiveWork());
    // Both facts inside the waitFor: the POST and the state it drives land in
    // separate commits, and asserting the second after awaiting the first is
    // the shape that flaked CI twice.
    await waitFor(() => {
      expect(hooks.posts.map((p) => p.path)).toEqual(["/agents/threads"]);
      expect(table().textContent).toBe("t-new");
      expect(table().getAttribute("data-assign")).toBe("dynamic:analyst");
      // ...and the page moves the user TO the surface it just armed. Carried
      // over from job-post-v1166's deleted page test, which asserted the same
      // scroll onto the job card this replaced: a Give-work that arms a
      // composer three sections down, silently, is a button that did nothing
      // the user can see.
      expect(window.HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
    });
    // The composer also gets the page's own roster rows, so its target list
    // never disagrees with the rail beside it.
    expect(table().getAttribute("data-roster")).toBe(String(ROSTER.length));
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
    await waitFor(() => expect(rail()).toBeVisible());
    fireEvent.click(railButton("analyst"));
    await waitFor(() => expect(hooks.arms).toEqual(["dynamic:analyst"]));
    fireEvent.click(railGiveWork());
    await waitFor(() =>
      expect(hooks.arms).toEqual(["dynamic:analyst", "dynamic:analyst"]),
    );
  });

  it("a face click aims the composer without creating anything", async () => {
    // The preselect that used to land in the job-post card's "Who takes it"
    // select now lands in the composer — same page state, new home. Clicking a
    // FACE still POSTs nothing: that stays behind Talk and Give work.
    render(<AgentsPage />);
    await waitFor(() => expect(rail()).toBeVisible());
    fireEvent.click(railButton("analyst"));
    await waitFor(() =>
      expect(table().getAttribute("data-assign")).toBe("dynamic:analyst"),
    );
    expect(hooks.posts).toHaveLength(0);
  });

  it("leaves no STALE target behind when the next pick cannot take work", async () => {
    // Carried over from the v1.178.0 adversarial review, which asserted it
    // through the job card that no longer exists here. The bug is the same
    // wherever the target lives: pick a workable agent, then an offline one,
    // and the rail's highlight moves while the work stays aimed at the first.
    // An un-workable pick RESETS the target to the Team — the honest answer,
    // since neither the supervisor nor an offline remote can take a session.
    render(<AgentsPage />);
    await waitFor(() => expect(rail()).toBeVisible());
    fireEvent.click(railButton("analyst"));
    await waitFor(() =>
      expect(table().getAttribute("data-assign")).toBe("dynamic:analyst"),
    );
    fireEvent.click(railButton(/down-box/));
    await waitFor(() => {
      expect(railButton(/down-box/).getAttribute("aria-current")).toBe("true");
      expect(table().getAttribute("data-assign")).toBe("builtin:__team__");
    });
    // Same for the non-delegable supervisor, reached from a live target.
    fireEvent.click(railButton("analyst"));
    await waitFor(() =>
      expect(table().getAttribute("data-assign")).toBe("dynamic:analyst"),
    );
    fireEvent.click(railButton("supervisor"));
    await waitFor(() =>
      expect(table().getAttribute("data-assign")).toBe("builtin:__team__"),
    );
  });

  it("the narrow-width picker aims the composer exactly as the faces do", async () => {
    // The <select> IS the rail below md, not a second control with its own
    // idea of who is selected. (Carried over from v1.178.0, which asserted it
    // through the job card that no longer stands on this page.)
    render(<AgentsPage />);
    await waitFor(() => expect(rail()).toBeVisible());
    fireEvent.change(screen.getByLabelText("Choose an agent"), {
      target: { value: "custom:analyst" },
    });
    await waitFor(() => {
      expect(table().getAttribute("data-assign")).toBe("dynamic:analyst");
      expect(railButton("analyst").getAttribute("aria-current")).toBe("true");
    });
  });

  it("Give work reuses the existing 1:1 thread instead of posting a second one", async () => {
    render(<AgentsPage />);
    await waitFor(() => expect(rail()).toBeVisible());
    fireEvent.click(railButton(/vr-assistant/));
    fireEvent.click(railGiveWork());
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
