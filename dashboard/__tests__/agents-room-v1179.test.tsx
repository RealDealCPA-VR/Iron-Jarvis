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
 *  - the job-post card is not a page header on first paint, and every
 *    capability that used to sit up there is still reachable: the TEAM case
 *    through the disclosure, one agent through the rail's Give-work button;
 *  - an older daemon with no /agents/roster keeps the pre-rail page: no rail,
 *    no gear, and — since there is no gear to hold them — Give work and Set up
 *    agents still standing in the flow.
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

vi.mock("@/components/agents/RoundTable", () => ({
  RoundTable: ({ threadId }: { threadId: string }) => (
    <div data-testid="round-table">{threadId}</div>
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
// The disclosure's accessible name is its whole two-line header ("Give work
// Post a job — …"); the rail's button is exactly "Give work". Two different
// controls, and every query below has to say which one it means.
const jobToggle = () => screen.getByRole("button", { name: /Post a job/ });
const railGiveWork = () => screen.getByRole("button", { name: /^Give work$/ });
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
  it("is not a form on first paint — the conversation is", async () => {
    render(<AgentsPage />);
    // Folded: nothing of the form is on screen or in the a11y tree (the panel
    // carries `hidden`, which is why these are queried and then asserted
    // invisible rather than queried and expected absent — the card stays
    // mounted so a half-typed job survives a collapse).
    expect(screen.getByLabelText("Job")).not.toBeVisible();
    expect(screen.getByLabelText("Who takes it")).not.toBeVisible();
    expect(jobToggle().getAttribute("aria-expanded")).toBe("false");
    // The thread with the selected agent is what the page opens on.
    await waitFor(() =>
      expect(screen.getByTestId("round-table").textContent).toBe("t-other"),
    );
  });

  it("sits BELOW the conversation, never above it", () => {
    // "The give work part at the top shouldnt be there." Position, asserted as
    // position: the round-table comes first in the document, so no layout
    // tweak can quietly float the job form back over the thread.
    render(<AgentsPage />);
    const panel = document.getElementById("job-post-panel") as HTMLElement;
    const table = screen.getByTestId("round-table");
    expect(
      panel.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_PRECEDING,
    ).toBeTruthy();
  });

  it("the TEAM case is one click away and defaults to the team", async () => {
    render(<AgentsPage />);
    fireEvent.click(jobToggle());
    await waitFor(() => expect(targetSelect().value).toBe("__team__"));
    expect(screen.getByLabelText("Job")).toBeTruthy();
  });

  it("the rail's Give-work button opens it with that agent selected", async () => {
    render(<AgentsPage />);
    fireEvent.click(railButton("analyst"));
    fireEvent.click(railGiveWork());
    // BOTH assertions live inside the waitFor: revealing the panel and the
    // card's own preselect effect are separate commits, and asserting the
    // second one after awaiting the first is the shape that flaked CI twice.
    // Visibility is asserted too — a preselected target inside a still-folded
    // panel is a button that did nothing the user can see.
    await waitFor(() => {
      expect(targetSelect()).toBeVisible();
      expect(targetSelect().value).toBe("custom:analyst");
    });
  });

  it("folds by HIDING, so a half-typed job survives the collapse", async () => {
    // Collapsing unmounts nothing once the card has been opened: an
    // `unmount-on-collapse` disclosure throws away whatever is in the Job box,
    // which is the one thing on this page a user cannot get back. `hidden`
    // also takes the form out of the a11y tree, so a folded panel is not a set
    // of controls a screen reader can still tab into.
    //
    // (The typed VALUE cannot be asserted here: the framer-motion double
    // returns a fresh component identity on every render, so every child of a
    // Reveal remounts constantly under test. What is assertable — and what the
    // mechanism actually is — is that the form stays in the document.)
    render(<AgentsPage />);
    fireEvent.click(jobToggle());
    expect(await screen.findByLabelText("Job")).toBeVisible();
    fireEvent.click(jobToggle());
    const panel = document.getElementById("job-post-panel") as HTMLElement;
    await waitFor(() => expect(panel.hasAttribute("hidden")).toBe(true));
    expect(within(panel).getByLabelText("Job")).toBeTruthy();
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
