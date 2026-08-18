import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * v1.179.0 — THE THREAD IS THE ROOM: adding an agent to the one you are in.
 *
 * The user asked for a thread with an individual agent "with the ability to
 * add an agent to the thread on the bottom right". The daemon already accepts
 * it — but through a route that SETS the panel, so the wiring is the whole
 * risk and every way of getting it wrong looks fine on screen:
 *
 *  - THE PUT CARRIES THE WHOLE PANEL. `PUT /agents/threads/{id}/participants`
 *    replaces the list (`update_participants`), so a body holding just the
 *    newly-picked agent EVICTS everyone else — and the screen afterwards, fed
 *    by the response, looks perfectly consistent. Only the request body proves
 *    the add was additive, so the body is what is asserted.
 *  - A FAILED ADD ADDS NOBODY. The reason must be on screen and the panel must
 *    still read exactly as it did — an optimistic chip for an agent the daemon
 *    refused is a lie the next round then contradicts.
 *  - THE ALREADY-SEATED ARE NOT OFFERED AGAIN. The daemon rejects a repeated
 *    key outright ("X is already in this thread"), so a picker that treats a
 *    seated agent as addable turns a normal click into a 400.
 *  - ALL THREE SOURCES ARE THERE (built-in, yours, remote), because a panel
 *    you can only extend with built-ins is not the roster the rail shows.
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
    thread: null as unknown,
    /** GET /agents/roster's payload, or a thrown error for the older-daemon
     *  and offline paths. */
    roster: null as unknown,
    rosterFails: null as null | (() => never),
    /** The pre-roster lists an older daemon still serves. */
    agents: null as unknown,
    remoteAgents: null as unknown,
    /** Every PUT, in order — path AND body (the body is the evidence). */
    puts: [] as { path: string; body: Record<string, unknown> }[],
    /** Per-test PUT responder; defaults to "the daemon accepted it". */
    onPut: null as null | ((body: Record<string, unknown>) => unknown),
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  get: (path: string) => {
    if (path === "/agents/threads/t1") return Promise.resolve(api.thread);
    if (path === "/agents/roster") {
      if (api.rosterFails) {
        try {
          api.rosterFails();
        } catch (e) {
          return Promise.reject(e);
        }
      }
      return Promise.resolve(api.roster);
    }
    // The older-daemon lists. Checked AFTER the two more specific /agents/*
    // paths above, and only when the test armed them — an unarmed route 404s
    // exactly like a daemon that never had it.
    if (path === "/agents/remote" && api.remoteAgents !== null)
      return Promise.resolve(api.remoteAgents);
    if (path === "/agents" && api.agents !== null) return Promise.resolve(api.agents);
    return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
  },
  post: () => Promise.resolve({}),
  put: (path: string, body?: unknown) => {
    const b = (body ?? {}) as Record<string, unknown>;
    api.puts.push({ path, body: b });
    try {
      return Promise.resolve(api.onPut ? api.onPut(b) : {});
    } catch (e) {
      return Promise.reject(e);
    }
  },
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [] }) }));

// The markdown pipeline is not under test here and only slows the run down.
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import { RoundTable } from "@/components/agents/RoundTable";

// jsdom has no scrollIntoView; the RoundTable pins its transcript with it.
window.HTMLElement.prototype.scrollIntoView = () => {};

const PARTICIPANTS_PATH = "/agents/threads/t1/participants";

const SEATED = [
  { key: "builtin:builder", source: "builtin", name: "builder", role: "lead" },
  { key: "dynamic:remy", source: "dynamic", name: "remy", role: "critic" },
];

const THREAD = {
  id: "t1",
  title: "Pricing",
  participants: SEATED,
  message_count: 2,
  updated_at: "2026-08-16T10:00:00Z",
  messages: [
    { who: "user", content: "flat rate or per-seat?", at: "2026-08-16T10:00:00Z" },
    { who: "builtin:builder", content: "hello there", at: "2026-08-16T10:00:05Z" },
  ],
};

/** The roster the rail shows — all three sources, roster-shaped names
 *  ("custom:"/"remote:" prefixed) exactly as agents/roster.py emits them. */
const ROSTER = {
  roster: [
    { name: "builder", kind: "builtin", description: "hands-on doer", healthy: true },
    { name: "planner", kind: "builtin", description: "breaks goals into plans", healthy: true },
    { name: "custom:remy", kind: "dynamic", description: "the skeptic", healthy: true },
    {
      name: "remote:vr-assistant",
      kind: "remote",
      description: "agent on the mac mini",
      healthy: true,
    },
  ],
};

function renderTable() {
  return render(
    <RoundTable threadId="t1" reloadNonce={0} onEditPanel={() => {}} onRoundDone={() => {}} />,
  );
}

/** Render, wait for the transcript, click Add an agent, wait for the picker. */
async function openAdd() {
  const view = renderTable();
  expect(await screen.findByText("hello there")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /add an agent/i }));
  const dialog = await screen.findByRole("dialog");
  return { ...view, dialog };
}

beforeEach(() => {
  api.thread = THREAD;
  api.roster = ROSTER;
  api.rosterFails = null;
  api.agents = null;
  api.remoteAgents = null;
  api.puts = [];
  // The daemon answers a successful PUT with the thread view it just stored.
  api.onPut = (body) => ({
    ...THREAD,
    participants: (body.participants as { source: string; name: string; role: string }[]).map(
      (p) => ({ ...p, key: `${p.source}:${p.name}`, role: p.role || "participant" }),
    ),
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/* ------------------------------------------------------------- the control */

describe("the thread offers a way to bring somebody in", () => {
  it("renders an accessibly-named add control on an open thread", async () => {
    renderTable();
    expect(await screen.findByText("hello there")).toBeInTheDocument();
    const add = screen.getByRole("button", { name: /add an agent/i });
    expect(add).toBeInTheDocument();
    expect(add).toBeEnabled();
    // …and it SAYS what it will do — the panel is extended, not replaced.
    expect(add.getAttribute("title")).toMatch(/everyone already here stays/i);
  });

  it("offers all three sources the app supports", async () => {
    const { dialog } = await openAdd();
    const panel = within(dialog);
    // Built-in, yours (dynamic), and an agent on another computer — the same
    // roster the rail shows, with the "custom:"/"remote:" prefixes stripped to
    // the bare registry names the thread routes accept.
    expect(panel.getByTitle("Add planner to the panel")).toBeInTheDocument();
    expect(panel.getByTitle("Add vr-assistant to the panel")).toBeInTheDocument();
    expect(panel.getByText("Built-in")).toBeInTheDocument();
    expect(panel.getByText("Yours")).toBeInTheDocument();
    expect(panel.getByText("Remote")).toBeInTheDocument();
  });

  /* v1.185.0. THE BUTTON'S PROMISE HAS TO SURVIVE THE CLICK. The control says
   * "everyone already here stays" (asserted above) and the PUT genuinely is
   * additive (asserted below) — but the picker in between said "Edit the
   * panel", listed the seated agents as if they were fresh choices, and
   * offered "Save panel". A promise contradicted by the surface it opens is
   * the promise the user believes second, and the fix belongs in the shared
   * component rather than in the caller that happens to have noticed. */
  it("frames itself as adding to a panel, not re-seating one", async () => {
    const { dialog } = await openAdd();
    const panel = within(dialog);

    expect(panel.getByRole("heading", { name: /add to the panel/i })).toBeInTheDocument();
    expect(panel.queryByRole("heading", { name: /^edit the panel$/i })).toBeNull();
    // The count is the claim: two are seated and they stay.
    expect(panel.getByText(/2 agents are already on this panel and stay/i)).toBeInTheDocument();
    // …and the exception is named in the same breath, because unpicking IS a
    // real removal and a surface that only promised "everyone stays" would be
    // lying the moment somebody used the × that is right there.
    expect(panel.getByText(/unpicking someone already seated removes them/i)).toBeInTheDocument();
  });

  it("says which agents are new, and warns before a save would evict anyone", async () => {
    const { dialog } = await openAdd();
    const panel = within(dialog);
    fireEvent.click(panel.getByTitle("Add planner to the panel"));

    // Only the newcomer is badged — the footer's job is "what am I changing?"
    expect(panel.getByText("new")).toBeInTheDocument();
    expect(panel.getByRole("button", { name: /add 1 agent/i })).toBeInTheDocument();
    expect(panel.queryByText(/saving also removes/i)).toBeNull();

    // Unpick a SEATED agent and the removal is stated BEFORE the click, rather
    // than discovered as a missing face in the thread afterwards.
    fireEvent.click(panel.getByTitle("Remove builder from the panel"));
    expect(panel.getByText(/saving also removes builder from this thread/i)).toBeInTheDocument();
  });
});

/* -------------------------------------------------------------- additive */

describe("adding is additive", () => {
  it("PUTs the whole panel — everyone seated plus the newcomer", async () => {
    const { dialog } = await openAdd();
    fireEvent.click(within(dialog).getByTitle("Add planner to the panel"));
    fireEvent.click(within(dialog).getByRole("button", { name: /add 1 agent/i }));
    await waitFor(() => {
      const call = api.puts.find((p) => p.path === PARTICIPANTS_PATH);
      expect(call).toBeTruthy();
      const sent = call!.body.participants as { name: string; role: string }[];
      // THE WHOLE POINT: this route SETS the panel. A body of just "planner"
      // would evict builder and remy, and the screen afterwards would look
      // entirely reasonable.
      expect(sent.map((p) => p.name)).toEqual(["builder", "remy", "planner"]);
      // …with the seated agents' ROLES intact — a re-seat that blanks them
      // silently demotes the lead of a running panel.
      expect(sent.map((p) => p.role)).toEqual(["lead", "critic", ""]);
    });
  });

  it("shows the newcomer only once the daemon has it, and says who joined", async () => {
    const { dialog } = await openAdd();
    fireEvent.click(within(dialog).getByTitle("Add planner to the panel"));
    fireEvent.click(within(dialog).getByRole("button", { name: /add 1 agent/i }));
    await waitFor(() => {
      // The header chip carries the participant's own title — proof this is
      // the THREAD's panel and not a leftover card in the picker.
      expect(screen.getByTitle("planner — participant (builtin)")).toBeInTheDocument();
    });
    expect(screen.getByText(/planner joined this thread/i)).toBeInTheDocument();
    expect(screen.getByTitle("builder — lead (builtin)")).toBeInTheDocument();
    expect(screen.getByTitle("remy — critic (dynamic)")).toBeInTheDocument();
  });

  /* REVIEWER-ADDED. The receipt is a CLAIM about what landed, so it has to be
   * read off the daemon's answer — not off the picker, which only holds what
   * was asked for. Derived from the picker it survives a response that seated
   * somebody else (or none), and nothing downstream ever re-checks it: the
   * chips would say one thing and the sentence under them another. */
  it("names in the receipt only who the daemon actually seated", async () => {
    // The write is accepted, but the panel that comes back is unchanged — a
    // server-side reconciliation, a concurrent edit, a proxy dropping a field.
    api.onPut = () => ({ ...THREAD, participants: SEATED });
    const { dialog } = await openAdd();
    fireEvent.click(within(dialog).getByTitle("Add planner to the panel"));
    fireEvent.click(within(dialog).getByRole("button", { name: /add 1 agent/i }));
    await waitFor(() => {
      expect(screen.getByText(/panel saved/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/planner joined this thread/i)).toBeNull();
    expect(screen.queryByTitle(/^planner —/)).toBeNull();
  });

  it("never offers a seated agent a second time", async () => {
    const { dialog } = await openAdd();
    const panel = within(dialog);
    // builder and remy are already at this table: their cards read as seated
    // (removable), never as an add. The daemon rejects a repeated key with a
    // 400, so an "Add builder" that existed here would be a click that fails.
    expect(panel.queryByTitle("Add builder to the panel")).toBeNull();
    expect(panel.queryByTitle("Add remy to the panel")).toBeNull();
    const seated = panel.getByTitle("Remove builder from the panel");
    expect(seated).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(panel.getByTitle("Add planner to the panel"));
    fireEvent.click(panel.getByRole("button", { name: /add 1 agent/i }));
    await waitFor(() => {
      const call = api.puts.find((p) => p.path === PARTICIPANTS_PATH);
      expect(call).toBeTruthy();
      const sent = call!.body.participants as { source: string; name: string }[];
      const keys = sent.map((p) => `${p.source}:${p.name}`);
      expect(new Set(keys).size).toBe(keys.length); // no duplicate seat
    });
  });
});

/* -------------------------------------------------------- honest failure */

describe("a failed add changes nothing", () => {
  it("shows the daemon's reason and leaves the panel exactly as it was", async () => {
    api.onPut = () => {
      throw new api.FakeApiError("unknown agent source 'builtin'", 400);
    };
    const { dialog } = await openAdd();
    fireEvent.click(within(dialog).getByTitle("Add planner to the panel"));
    fireEvent.click(within(dialog).getByRole("button", { name: /add 1 agent/i }));
    await waitFor(() => {
      expect(screen.getByText(/unknown agent source/i)).toBeInTheDocument();
    });
    // NOBODY WAS ADDED: the thread's own chips still read builder + remy, and
    // no receipt claims a join. (The picker is still open with planner picked
    // — the retry is one click — so the assertion is scoped to the panel.)
    expect(screen.queryByTitle(/^planner —/)).toBeNull();
    expect(screen.getByTitle("builder — lead (builtin)")).toBeInTheDocument();
    expect(screen.getByTitle("remy — critic (dynamic)")).toBeInTheDocument();
    expect(screen.queryByText(/joined this thread/i)).toBeNull();
  });

  /* REVIEWER-ADDED. The fallback to GET /agents + GET /agents/remote is ~25
   * lines of degrade for daemons older than /agents/roster, and every test
   * above drives the roster path — so the whole fallback could be replaced
   * with an empty catalog and this file stayed green (mutation-proven). A
   * degrade nobody exercises is a degrade nobody knows is broken, and this
   * one carries the brief's "all three sources" promise on old installs. */
  it("feeds the picker from the raw lists on a daemon older than the roster", async () => {
    api.rosterFails = () => {
      throw new api.FakeApiError("Not Found", 404); // no /agents/roster here
    };
    api.agents = {
      builtin: ["builder", "planner"],
      dynamic: [{ name: "remy", description: "the skeptic" }],
    };
    api.remoteAgents = {
      agents: [{ name: "vr-assistant", base_url: "http://x", kind: "claude-code", enabled: true }],
    };
    const { dialog } = await openAdd();
    const panel = within(dialog);
    // All three sources still reach the picker — a pre-roster daemon must not
    // shrink to built-ins only.
    expect(panel.getByTitle("Add planner to the panel")).toBeInTheDocument();
    expect(panel.getByTitle("Add vr-assistant to the panel")).toBeInTheDocument();
    // …and the seated two are still recognised as seated on this path too.
    expect(panel.queryByTitle("Add builder to the panel")).toBeNull();
    expect(panel.queryByTitle("Add remy to the panel")).toBeNull();

    fireEvent.click(panel.getByTitle("Add vr-assistant to the panel"));
    fireEvent.click(panel.getByRole("button", { name: /add 1 agent/i }));
    await waitFor(() => {
      const call = api.puts.find((p) => p.path === PARTICIPANTS_PATH);
      expect(call).toBeTruthy();
      const sent = call!.body.participants as { source: string; name: string }[];
      expect(sent.map((p) => `${p.source}:${p.name}`)).toEqual([
        "builtin:builder",
        "dynamic:remy",
        "remote:vr-assistant",
      ]);
    });
  });

  /* REVIEWER-ADDED. The roster branch refuses an empty `roster` array; the
   * fallback had no such gate, so a daemon answering `/agents` with `{}` (or a
   * roster of rows with an unrecognised kind) opened a picker whose three
   * groups each state there are none of that source — "you have no agents",
   * asserted confidently, when the truth is "this daemon told us nothing". */
  it("refuses to open a picker with nothing in it", async () => {
    api.rosterFails = () => {
      throw new api.FakeApiError("Not Found", 404);
    };
    api.agents = {}; // reachable, but it listed nothing
    api.remoteAgents = {};
    renderTable();
    expect(await screen.findByText("hello there")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /add an agent/i }));
    await waitFor(() => {
      expect(screen.getByText(/listed no agents at all/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByText(/No built-in agents available/i)).toBeNull();
    expect(api.puts).toHaveLength(0);
  });

  it("does not fake a picker when the agent list never arrived", async () => {
    api.rosterFails = () => {
      throw new api.FakeApiError("", 0); // lib/api maps a dead fetch to 0
    };
    renderTable();
    expect(await screen.findByText("hello there")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /add an agent/i }));
    await waitFor(() => {
      expect(screen.getByText(/Daemon offline/i)).toBeInTheDocument();
    });
    // An empty picker would tell the user they have no agents at all.
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.puts).toHaveLength(0);
  });
});
