import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * The agents room (v1.179.0) — the user's own report, point by point.
 *
 *   "the give work part at the top shouldnt be there because it should simply
 *    open when a specific agent is selected and be treated more like a thread
 *    with that individual agent"
 *   "there seems to be a redundant agent on the left pane for vr-assistant,
 *    however i do like that it has a little remote indicator on it so any
 *    remote agents should come with that"
 *   "the set up agents should all be contained in the new agent gear face on
 *    the left pane and not shown unless the user decided to configure an agent"
 *
 * WHAT THESE TESTS GUARD:
 *  - ONE rendering of an agent in the left pane. The roster never served a
 *    duplicate — the strip drew the rail and then re-drew the selected agent in
 *    a detail block underneath it. The detail now lives on the selected ROW;
 *    restoring the block puts the name on screen twice and fails here;
 *  - a remote agent carries its indicator ON its rail row, always, and an
 *    unreachable one still says "offline" in words rather than in colour;
 *  - setup is NOT in the document until the gear-with-a-face is clicked — not
 *    even when the card's own persisted state says it was open before — and the
 *    gear folds it away again;
 *  - giving work to ONE agent is still one click from the rail;
 *  - an older daemon with no /agents/roster keeps the pre-rail page: no rail,
 *    no gear, and — since there is no gear to hold them — Give work and Set up
 *    agents still standing in the flow.
 *
 * WHAT v1.180.0 TOOK OUT OF THIS FILE, and where each guarantee now lives.
 * The "Post a job" disclosure this file guarded the POSITION of is gone: not
 * the capability — the separate surface. "The post a job seems to be redundant
 * because if i choose to start a thread with an agent that would be the start
 * of posting a new job." Four tests here asserted that surface ITSELF, so they
 * were removed rather than rewritten, each against a named replacement:
 *   - "is not a form on first paint" → agents-layout-v1180.test.tsx
 *     "has no standalone job-post disclosure left on the page" (a stronger
 *     claim: the form is not on the page at all, folded or otherwise), and the
 *     conversation-is-what-paints half by agent-rail-v1178.test.tsx
 *     "continues an existing 1:1 thread with exactly that agent";
 *   - "sits BELOW the conversation, never above it" → there is no panel to sit
 *     anywhere; the surviving order claim is agents-layout-v1180.test.tsx
 *     "renders the conversation after the roster in document order";
 *   - "the TEAM case is one click away and defaults to the team" →
 *     thread-dispatch-v1180.test.tsx "hands the job to the TEAM when the user
 *     picks it in a 1:1 thread" (the reachability half — added by adversarial
 *     review, because the two tests originally named here, "offers the team and
 *     the roster's delegable agents" and "dispatches a multi-agent panel to the
 *     team", assert only that the Team is LISTED and that a multi-agent thread
 *     defaults to it: an inert target <select> passed both), plus
 *     agents-layout-v1180.test.tsx "leaves no STALE target behind when the next
 *     pick cannot take work" for the page-level reset;
 *   - "folds by HIDING, so a half-typed job survives the collapse" → the job
 *     form now lives in the composer, whose Job-options panel keeps its state
 *     in the THREAD rather than in the DOM, so the equivalent guarantee is
 *     thread-dispatch-v1180.test.tsx "still explains a blocked budget after Job
 *     options is folded away": fold the panel and the budget you typed is still
 *     in force (the dispatch button stays disabled and the reason is hoisted
 *     out). Mutation-proven — clearing the inputs on collapse turns it red. The
 *     roster's own fold is a HIDE and is asserted separately by
 *     agents-layout-v1180.test.tsx "folds away and comes back".
 * The rail's Give-work test below was RETARGETED, not removed: that behaviour
 * is unchanged, it just aims the thread composer now.
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
      return Promise.resolve({
        id: "t-new",
        title: "Talk with builder",
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

// Since v1.180.0 the composer owns the dispatch target, so the double reports
// the page's `assign` prop back onto the DOM — the same idiom as
// agents-layout-v1180.test.tsx, so there is one way to ask "who is the work
// aimed at" and not two.
vi.mock("@/components/agents/RoundTable", () => ({
  RoundTable: ({
    threadId,
    assign,
  }: {
    threadId: string;
    assign?: { kind: string; name: string } | null;
  }) => (
    <div
      data-testid="round-table"
      data-assign={assign ? `${assign.kind}:${assign.name}` : ""}
    >
      {threadId}
    </div>
  ),
}));

import { RosterStrip, type RosterEntry } from "@/components/agents/RosterStrip";
import AgentsPage from "@/app/agents/page";

/** "vr-assistant" is the user's own remote — the row they reported seeing
 *  twice — so it is the one this file selects and counts. */
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
      {
        key: "remote:vr-assistant",
        source: "remote",
        name: "vr-assistant",
        role: "",
      },
    ],
    message_count: 5,
    updated_at: "2026-08-13T10:00:00Z",
  },
];

const pane = () => screen.getByTestId("roster-pane");
const rail = () => screen.getByTestId("roster-rail");
const railButton = (name: RegExp | string) =>
  within(rail()).getByRole("button", { name });
const railGiveWork = () => screen.getByRole("button", { name: /^Give work$/ });
const table = () => screen.getByTestId("round-table");
/** WHO THE WORK IS AIMED AT, as the page tells the composer (v1.180.0). */
const aimedAt = () => table().getAttribute("data-assign");
/** Only the pre-rail composition still carries the standalone card. */
const targetSelect = () =>
  screen.getByLabelText("Who takes it") as HTMLSelectElement;
/** The narrow-width twin of the rail (below lg the column is display:none). */
const narrowPick = () =>
  screen.getByLabelText("Choose an agent") as HTMLSelectElement;

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
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  window.localStorage.clear();
});
afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

/* ------------------------------------------------- one agent, drawn once --- */

describe("the left pane draws each agent exactly once", () => {
  it("names the selected agent once — on its rail row, not again below it", () => {
    render(<AgentsPage />);
    fireEvent.click(railButton(/vr-assistant/));
    // getAllByText matches on an element's OWN text nodes, so this counts
    // renderings of the name, not ancestors of one. Two = the detail block is
    // back and the user is looking at the same agent twice.
    expect(within(pane()).getAllByText("vr-assistant")).toHaveLength(1);
    // Nothing was lost with the block: its detail is on the selected row.
    expect(screen.getByTestId("roster-preview").textContent).toContain(
      "Reviewed the draft — two issues flagged.",
    );
  });

  it("draws one face per agent and no second portrait of the selected one", () => {
    render(<AgentsPage />);
    fireEvent.click(railButton("analyst"));
    // Five roster rows, five drawn faces — a detail block would make six.
    expect(within(pane()).getAllByTestId("agent-face")).toHaveLength(
      ROSTER.length,
    );
    // The selected row carries what the block used to say: kind, stats, and
    // the description when there is no recorded activity.
    expect(within(pane()).getByTestId("roster-kind-analyst").textContent).toBe(
      "Yours",
    );
    expect(screen.getByText("87% over 23 runs")).toBeTruthy();
    expect(screen.getByText("Your analyst")).toBeTruthy();
  });

  it("shows detail for the selected row only", () => {
    render(<AgentsPage />);
    // Nobody picked yet: no detail anywhere (the fallback is a preview of the
    // first entry, and previewing is not selecting).
    expect(screen.queryByText("Your analyst")).toBeNull();
    fireEvent.click(railButton("analyst"));
    expect(screen.getByText("Your analyst")).toBeTruthy();
    expect(screen.queryByText("Builds things")).toBeNull();
    fireEvent.click(railButton("builder"));
    expect(screen.getByText("Builds things")).toBeTruthy();
    expect(screen.queryByText("Your analyst")).toBeNull();
  });
});

/* -------------------------------------------------- the remote indicator --- */

describe("the remote indicator rides on the rail row", () => {
  it("every remote row carries it, selected or not", () => {
    render(<RosterStrip entries={ROSTER} onSelect={vi.fn()} />);
    for (const name of ["vr-assistant", "down-box"]) {
      const row = within(rail()).getByRole("button", {
        name: new RegExp(name),
      });
      expect(within(row).getByText("Remote")).toBeTruthy();
    }
    // Local agents keep a clean row — their kind shows on the selected row's
    // detail line, and a pill on all five rows at 15rem is noise.
    expect(
      within(within(rail()).getByRole("button", { name: "builder" })).queryByText(
        "Remote",
      ),
    ).toBeNull();
  });

  it("still says offline in WORDS, ahead of the provenance", () => {
    render(<RosterStrip entries={ROSTER} onSelect={vi.fn()} />);
    // The accessible name carries the state: a rose icon reaches nobody using
    // a screen reader, and health is the more urgent fact of the two.
    expect(railButton(/down-box\s*offline\s*Remote/)).toBeTruthy();
    expect(railButton(/vr-assistant\s*Remote/)).toBeTruthy();
  });
});

/* --------------------------------------------------------------- the gear --- */

describe("setup lives behind the gear and nowhere else", () => {
  it("is absent until the gear is clicked, and folds away again", async () => {
    render(<AgentsPage />);
    expect(screen.queryByText("Create an agent")).toBeNull();
    expect(screen.queryByText("Connect a remote agent")).toBeNull();
    fireEvent.click(screen.getByTestId("roster-gear"));
    await waitFor(() => expect(screen.getByText("Create an agent")).toBeTruthy());
    // One door, both kinds.
    expect(screen.getByText("Connect a remote agent")).toBeTruthy();
    fireEvent.click(screen.getByTestId("roster-gear"));
    await waitFor(() => expect(screen.queryByText("Create an agent")).toBeNull());
  });

  it("stays absent even when the card's own persisted state says 'open'", async () => {
    // The card remembers being opened; the PAGE decides whether it is on
    // screen. "Not shown unless the user decided to configure an agent" means
    // this visit, not a visit last week.
    window.localStorage.setItem("ij_agents_setup_open", "1");
    render(<AgentsPage />);
    expect(screen.queryByText("Create an agent")).toBeNull();
    // And the gear still reveals it OPEN rather than mounted-but-collapsed.
    fireEvent.click(screen.getByTestId("roster-gear"));
    await waitFor(() => expect(screen.getByText("Create an agent")).toBeTruthy());
  });

  it("reveals it OPEN even when it was left COLLAPSED last visit", async () => {
    /* v1.185.0, and the case the sibling above cannot reach: it seeds "1", so
     * the disclosure is already open and a gear that failed to expand would
     * look identical. Seeding "0" is the only state that tells the two apart —
     * a user who folded the card away, then came back and pressed the gear.
     *
     * The guarantee itself is v1.179.0's ("the gear reveals it OPEN rather than
     * mounted-but-collapsed"); what changed is where it comes from. It used to
     * be a side effect of the page writing localStorage BEFORE mounting the
     * card, which the card then hydrated — the ordering was the mechanism. The
     * page now holds the value and sets it directly, so this asserts the
     * promise rather than the handshake that used to deliver it. */
    window.localStorage.setItem("ij_agents_setup_open", "0");
    render(<AgentsPage />);
    expect(screen.queryByText("Create an agent")).toBeNull();

    fireEvent.click(screen.getByTestId("roster-gear"));
    await waitFor(() => expect(screen.getByText("Create an agent")).toBeTruthy());
  });

  it("keeps the card's own fold, and remembers it", async () => {
    /* The other half of one-source-of-truth: the gear and the card's chevron
     * are different controls over different things (on screen at all / body
     * disclosed), and folding the body must not evict the card — otherwise the
     * chevron is a second Close button and the header it sits on vanishes with
     * the click that used it. */
    render(<AgentsPage />);
    fireEvent.click(screen.getByTestId("roster-gear"));
    await waitFor(() => expect(screen.getByText("Create an agent")).toBeTruthy());

    const header = screen.getByRole("button", { name: /set up agents/i });
    fireEvent.click(header);
    await waitFor(() => expect(screen.queryByText("Create an agent")).toBeNull());
    // Folded, NOT removed — the card is still there to unfold.
    expect(screen.getByRole("button", { name: /set up agents/i })).toBeTruthy();
    expect(window.localStorage.getItem("ij_agents_setup_open")).toBe("0");
  });

  it("announces the state it controls", () => {
    render(<AgentsPage />);
    const gear = screen.getByTestId("roster-gear");
    expect(gear.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(gear);
    expect(screen.getByTestId("roster-gear").getAttribute("aria-expanded")).toBe(
      "true",
    );
  });
});

/* ---------------------------------------------------------- give work ------ */

describe("give work is reachable, but it is not the page header", () => {
  it("the rail's Give-work button opens the thread with that agent, work armed", async () => {
    // RETARGETED for v1.180.0. Give-work used to reveal the job-post
    // disclosure with the agent preselected; it now opens the 1:1 THREAD with
    // that agent and aims the composer at them — "if i choose to start a
    // thread with an agent that would be the start of posting a new job". Same
    // button, same one-click guarantee, one surface instead of two.
    //
    // ADVERSARIAL REVIEW (v1.180.0): the first retarget drove vr-assistant and
    // asserted NOTHING. Give-work can only be reached after picking a face, and
    // picking vr-assistant's face ALREADY opens its 1:1 thread (soloThreadWith)
    // and ALREADY arms it — so with the whole `assignWork` body replaced by a
    // no-op this test still passed. Measured, not reasoned about. The agent
    // driven here therefore has NO existing 1:1 thread, which makes opening one
    // something only the button can have done: the face click leaves t-other on
    // screen, and t-new can only arrive through Give-work's POST.
    render(<AgentsPage />);
    fireEvent.click(railButton("analyst"));
    // The selection alone changes nothing about which thread is open.
    await waitFor(() => expect(table().textContent).toBe("t-other"));
    expect(hooks.posts).toHaveLength(0);
    fireEvent.click(railGiveWork());
    // ALL THREE assertions live inside the waitFor: the POST, opening the
    // thread and arming the composer land in separate commits, and asserting
    // the later ones after awaiting the first is the shape that flaked CI twice.
    await waitFor(() => {
      expect(hooks.posts.map((p) => p.path)).toEqual(["/agents/threads"]);
      expect(table().textContent).toBe("t-new");
      expect(aimedAt()).toBe("dynamic:analyst");
    });
  });

  it("Talk still starts a thread the selection alone would not", async () => {
    // Selecting only OPENS an existing 1:1 thread — a click on a portrait is
    // not consent to POST. Starting one stays an explicit button.
    render(<AgentsPage />);
    fireEvent.click(railButton("builder"));
    expect(hooks.posts).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: /^Talk$/ }));
    await waitFor(() =>
      expect(hooks.posts.map((p) => p.path)).toEqual(["/agents/threads"]),
    );
  });

  it("offers no actions for an agent nobody picked", () => {
    // `selected` falls back to the first entry so the narrow <select> has a
    // value — a PREVIEW, not a selection (the same distinction the v1.178.0
    // aria-current gate drew). Acting on it would hand work to whoever the
    // roster happens to list first. The supervisor is filtered out here on
    // purpose: it is non-delegable, so leaving it first would hide the buttons
    // for an unrelated reason and the assertion would prove nothing.
    const entries = ROSTER.filter((e) => e.name !== "supervisor");
    render(
      <RosterStrip
        entries={entries}
        onSelect={vi.fn()}
        onTalk={vi.fn()}
        onAssign={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /^Talk$/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Give work$/ })).toBeNull();
    fireEvent.click(within(rail()).getByRole("button", { name: "builder" }));
    expect(screen.getByRole("button", { name: /^Talk$/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Give work$/ })).toBeTruthy();
  });

  it("the narrow picker does not claim entries[0] before anyone has picked", () => {
    // ADVERSARIAL REVIEW. Below lg the rail is display:none and this <select>
    // is the ONLY picker. If it shows entries[0] while `actionable` demands a
    // real pick, the buttons are hidden — and re-choosing the option a select
    // is ALREADY showing fires no `change`, so the first roster agent could
    // never be reached at a narrow width. Unpicked, the box says so.
    const entries = ROSTER.filter((e) => e.name !== "supervisor");
    render(
      <RosterStrip entries={entries} onSelect={vi.fn()} onAssign={vi.fn()} />,
    );
    expect(narrowPick().value).toBe("");
    // ...which makes picking the FIRST entry a genuine change, and the actions
    // reachable without first detouring through some other agent.
    fireEvent.change(narrowPick(), { target: { value: entries[0].name } });
    expect(narrowPick().value).toBe(entries[0].name);
    expect(screen.getByRole("button", { name: /^Give work$/ })).toBeTruthy();
  });

  it("the standalone composition keeps its old default selection", () => {
    // No rail (no `onSelect`) ⇒ the <select> IS the selection, entries[0] is a
    // legitimate default, and `actionable` never asked for a pick there.
    render(<RosterStrip entries={ROSTER} onAssign={vi.fn()} />);
    expect(narrowPick().value).toBe("supervisor");
  });
});

/* ------------------------------------------------------ honest degradation --- */

describe("an older daemon that does not serve /agents/roster", () => {
  beforeEach(() => {
    delete hooks.api["/agents/roster"];
  });

  it("keeps the pre-rail page: no rail, no gear, and both surfaces in the flow", async () => {
    render(<AgentsPage />);
    expect(screen.queryByTestId("roster-rail")).toBeNull();
    expect(screen.queryByTestId("roster-gear")).toBeNull();
    expect(screen.queryByTestId("roster-pane")).toBeNull();
    // With no gear to hold them, hiding these would delete two capabilities
    // from the daemons least able to spare them.
    expect(targetSelect().value).toBe("__team__");
    expect(screen.getByText("Set up agents")).toBeTruthy();
    // And no error over a feature the daemon predates.
    expect(screen.queryByText(/not found/i)).toBeNull();
    await waitFor(() =>
      expect(screen.getByTestId("round-table").textContent).toBe("t-other"),
    );
  });
});
