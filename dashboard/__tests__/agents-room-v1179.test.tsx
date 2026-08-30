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
/** The narrow-width twin of the rail (below md the column is display:none). */
const narrowPick = () =>
  screen.getByLabelText("Choose an agent") as HTMLSelectElement;

/* v1.214.0 — the roster moved into a dialog behind the thread rail's icon.
   The component-level tests below still render `RosterStrip` directly (it is
   unchanged, and the older-daemon page still uses it); the page-level ones
   open the room. */
const room = () => screen.getByTestId("agents-modal");
const openRoom = () => {
  fireEvent.click(screen.getByTestId("roster-gear"));
  return room();
};
const roomButton = (name: RegExp | string) =>
  within(screen.getByTestId("agents-modal-list")).getByRole("button", { name });
const detail = () => within(room()).getByTestId(/^agent-detail-/);

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

describe("the agents room draws each agent exactly once", () => {
  // RETARGETED FOR v1.214.0. The report this suite was written for —
  // "there seems to be a redundant agent on the left pane for vr-assistant" —
  // was about a rail that drew a face for every agent AND re-drew the selected
  // one in a detail block stacked directly beneath it: two portraits of one
  // agent, in one narrow column, one above the other. The roster is now a
  // master–detail DIALOG, where a list column and a detail pane are two
  // different places by construction. What still has to hold is that the LIST
  // has one row per agent and the DETAIL pane speaks about exactly one.

  it("lists every roster agent once", async () => {
    render(<AgentsPage />);
    openRoom();
    const list = screen.getByTestId("agents-modal-list");
    // One row per agent, plus the New-agent row at the foot.
    expect(within(list).getAllByRole("button")).toHaveLength(ROSTER.length + 1);
    expect(within(list).getAllByTestId("agent-face")).toHaveLength(ROSTER.length);
  });

  it("shows detail for ONE agent — the one that is open", async () => {
    render(<AgentsPage />);
    openRoom();
    fireEvent.click(roomButton("analyst"));
    await waitFor(() =>
      expect(within(room()).getByTestId("agent-detail-analyst")).toBeTruthy(),
    );
    expect(within(room()).queryByTestId("agent-detail-builder")).toBeNull();
    // The detail carries what the old rail's block did: kind, honest stats,
    // and the description when there is no recorded activity.
    expect(
      within(detail()).getByTestId("roster-kind-analyst").textContent,
    ).toBe("Yours");
    expect(within(detail()).getByText("87% over 23 runs")).toBeTruthy();
    expect(within(detail()).getByText("Your analyst")).toBeTruthy();

    fireEvent.click(roomButton("builder"));
    await waitFor(() =>
      expect(within(room()).getByTestId("agent-detail-builder")).toBeTruthy(),
    );
    expect(within(room()).queryByTestId("agent-detail-analyst")).toBeNull();
  });

  it("carries the messenger preview for an agent that has spoken", async () => {
    render(<AgentsPage />);
    openRoom();
    fireEvent.click(roomButton(/vr-assistant/));
    await waitFor(() =>
      expect(screen.getByTestId("roster-preview").textContent).toContain(
        "Reviewed the draft — two issues flagged.",
      ),
    );
    // ONE preview: the list rows carry no detail of their own.
    expect(screen.getAllByTestId("roster-preview")).toHaveLength(1);
  });

  it("offers the same two controls for a BUILT-IN agent as for one of yours", async () => {
    // The point of the release: "every agent should be customizable including
    // the predefined agents ... with the ability for the user to choose a
    // custom image for any of the agents." The daemon always allowed it —
    // storage is `avatars/<slug>.png` keyed by name — and only the UI drew the
    // line at agents the user had created.
    render(<AgentsPage />);
    openRoom();
    fireEvent.click(roomButton("builder"));
    await waitFor(() =>
      expect(within(room()).getByTestId("avatar-row-builder")).toBeTruthy(),
    );
    const row = within(room()).getByTestId("avatar-row-builder");
    expect(within(row).getByText("Upload")).toBeTruthy();
    expect(within(row).getByRole("button", { name: /Generate/ })).toBeTruthy();
    // ...and the face picker, on the same agent.
    expect(within(room()).getByTestId("face-picker-builder")).toBeTruthy();
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

describe("configuration lives behind the icon and nowhere else", () => {
  it("is absent until the icon is clicked, and closes again", async () => {
    render(<AgentsPage />);
    expect(screen.queryByTestId("agents-modal")).toBeNull();
    expect(screen.queryByText("Create an agent")).toBeNull();
    expect(screen.queryByText("Connect a remote agent")).toBeNull();
    fireEvent.click(screen.getByTestId("roster-gear"));
    await waitFor(() => expect(room()).toBeTruthy());
    // One door, both kinds — one click further in.
    fireEvent.click(screen.getByTestId("agents-modal-new"));
    await waitFor(() => expect(screen.getByText("Create an agent")).toBeTruthy());
    expect(screen.getByText("Connect a remote agent")).toBeTruthy();
    fireEvent.click(screen.getByTestId("roster-gear"));
    await waitFor(() => expect(screen.queryByTestId("agents-modal")).toBeNull());
  });

  it("stays absent even when the older card's persisted state says 'open'", async () => {
    // "Not shown unless the user decided to configure an agent" means this
    // visit, not a visit last week — and the flag the older stacked page
    // persists must not reach a dialog at all.
    window.localStorage.setItem("ij_agents_setup_open", "1");
    render(<AgentsPage />);
    expect(screen.queryByTestId("agents-modal")).toBeNull();
    expect(screen.queryByText("Create an agent")).toBeNull();
    fireEvent.click(screen.getByTestId("roster-gear"));
    await waitFor(() => expect(room()).toBeTruthy());
  });

  it("is a DIALOG, portalled out of the page it was opened from", async () => {
    // THE BUG THIS RELEASE EXISTS FOR, asserted structurally. `.card-surface`
    // carries `backdrop-filter`, which makes an element the containing block
    // for `position: fixed` descendants — so an overlay rendered inside one is
    // sized to that card and clipped by its `overflow-hidden`. Reported as the
    // add-agent popup being "bound by the size of the thread (chat window)".
    // A portal to <body> is the only fix available from inside, so the escape
    // is what gets pinned: the dialog must not sit under the page root.
    render(<AgentsPage />);
    openRoom();
    const dialog = await screen.findByRole("dialog");
    expect(dialog.parentElement?.parentElement).toBe(document.body);
    expect(screen.getByTestId("agents-room").contains(dialog)).toBe(false);
  });

  it("announces the state it controls", () => {
    render(<AgentsPage />);
    const gear = screen.getByTestId("roster-gear");
    expect(gear.getAttribute("aria-haspopup")).toBe("dialog");
    expect(gear.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(gear);
    expect(screen.getByTestId("roster-gear").getAttribute("aria-expanded")).toBe(
      "true",
    );
  });
});

/* ---------------------------------------------------------- give work ------ */

describe("give work is reachable, but it is not the page header", () => {
  it("the room's Give-work button opens the thread with that agent, work armed", async () => {
    // RETARGETED for v1.180.0, then again for v1.214.0. Give-work used to
    // reveal the job-post disclosure with the agent preselected; it opens the
    // 1:1 THREAD with that agent and aims the composer at them — "if i choose
    // to start a thread with an agent that would be the start of posting a new
    // job". Same button, same one-click guarantee, one surface instead of two.
    //
    // ADVERSARIAL REVIEW (v1.180.0): the first retarget drove vr-assistant and
    // asserted NOTHING. Give-work can only be reached after picking an agent,
    // and picking vr-assistant ALREADY opens its 1:1 thread (soloThreadWith)
    // and ALREADY arms it — so with the whole `assignWork` body replaced by a
    // no-op this test still passed. Measured, not reasoned about. The agent
    // driven here therefore has NO existing 1:1 thread, which makes opening one
    // something only the button can have done: the pick leaves t-other on
    // screen, and t-new can only arrive through Give-work's POST.
    render(<AgentsPage />);
    openRoom();
    fireEvent.click(roomButton("analyst"));
    // The selection alone changes nothing about which thread is open.
    await waitFor(() => expect(table().textContent).toBe("t-other"));
    expect(hooks.posts).toHaveLength(0);
    fireEvent.click(within(room()).getByRole("button", { name: /^Give work$/ }));
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
    openRoom();
    fireEvent.click(roomButton("builder"));
    expect(hooks.posts).toHaveLength(0);
    fireEvent.click(within(room()).getByRole("button", { name: /^Talk$/ }));
    await waitFor(() =>
      expect(hooks.posts.map((p) => p.path)).toEqual(["/agents/threads"]),
    );
    // ...and the room gets out of the way of the conversation it just opened.
    await waitFor(() => expect(screen.queryByTestId("agents-modal")).toBeNull());
  });

  it("offers no action for an agent that cannot take one", async () => {
    // The supervisor is non-delegable and an offline remote cannot take a
    // session — so neither gets a button that would quietly fall back to
    // something else at dispatch time. Unlike the rail (v1.179.0), the room
    // needs no "has anyone really picked" clause on top: its buttons are handed
    // the agent explicitly, beside that agent's portrait and name in full.
    render(<AgentsPage />);
    openRoom();
    fireEvent.click(roomButton("supervisor"));
    await waitFor(() =>
      expect(within(room()).getByTestId("agent-detail-supervisor")).toBeTruthy(),
    );
    expect(within(room()).queryByRole("button", { name: /^Talk$/ })).toBeNull();
    expect(within(room()).queryByRole("button", { name: /^Give work$/ })).toBeNull();
    fireEvent.click(roomButton(/down-box/));
    await waitFor(() =>
      expect(within(room()).getByTestId("agent-detail-down-box")).toBeTruthy(),
    );
    expect(within(room()).queryByRole("button", { name: /^Give work$/ })).toBeNull();
    // A healthy, delegable one does get them.
    fireEvent.click(roomButton("builder"));
    await waitFor(() =>
      expect(within(room()).getByRole("button", { name: /^Give work$/ })).toBeTruthy(),
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
