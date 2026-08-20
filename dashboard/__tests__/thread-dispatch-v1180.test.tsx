import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * v1.180.0 — THE THREAD IS WHERE WORK STARTS.
 *
 * Reported verbatim: "the post a job seems to be redundant because if i choose
 * to start a thread with an agent that would be the start of posting a new
 * job." Today those are two DIFFERENT ACTS with different costs — a thread
 * round (`AgentThreads.run_round`) is a conversation where each participant
 * answers, while a job (`POST /sessions`) is a session that runs the
 * perceive→act loop, uses tools and produces files. The fix is chat's: one
 * surface, and the surface SAYS which act it performed.
 *
 * WHAT THESE TESTS GUARD, and why each one is invisible on screen when wrong:
 *
 *  - THE TARGET. A one-agent thread dispatches to THAT agent; a panel (or the
 *    supervisor) dispatches to the Team. Every wrong target still posts
 *    *something*: a dynamic agent sent to POST /sessions is silently downgraded
 *    to Builder, and a remote sent anywhere but through the supervisor wrapper
 *    quietly reroutes to a local agent. Only the request body shows it, so the
 *    body is what is pinned.
 *  - THE CARRIED CONTROLS. Project grounding and the step budget moved behind a
 *    disclosure. A control that is reachable but does not REACH THE REQUEST is
 *    the worst of both: the user sets a 40-step budget, watches the run stop at
 *    the default, and is told "reached max steps".
 *  - THE TWO ACTS NEVER LOOK ALIKE. A dispatch that renders like a round leaves
 *    the user unsure whether a session exists, which is the one thing this
 *    surface may not do.
 *  - A FAILED DISPATCH STARTS NOTHING and loses nothing: the daemon's reason on
 *    screen, the typed task still in the box, no receipt claiming a session.
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
    /** GET /projects and GET /sessions payloads; null = the route 404s. */
    projects: null as unknown,
    sessions: null as unknown,
    /** Every POST, in order — path AND body (the body is the evidence). */
    posts: [] as { path: string; body: Record<string, unknown> }[],
    /** Per-test POST responder; defaults to "the daemon started the session". */
    onPost: null as null | ((path: string, body: Record<string, unknown>) => unknown),
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  get: (path: string) => {
    if (path === "/agents/threads/t1") return Promise.resolve(api.thread);
    // `null` = this daemon does not serve the route (or is gone). The status
    // matters: 0 is "daemon offline" and anything else is the daemon's own
    // words, which lib/api hands through and this UI must relay verbatim.
    if (path === "/projects")
      return api.projects === null
        ? Promise.reject(new api.FakeApiError("Not Found", 404))
        : Promise.resolve(api.projects);
    if (path === "/sessions")
      return api.sessions === null
        ? Promise.reject(new api.FakeApiError("", 0))
        : Promise.resolve(api.sessions);
    return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
  },
  post: (path: string, body?: unknown) => {
    const b = (body ?? {}) as Record<string, unknown>;
    api.posts.push({ path, body: b });
    try {
      return Promise.resolve(
        api.onPost ? api.onPost(path, b) : { id: "s-new", status: "active" },
      );
    } catch (e) {
      return Promise.reject(e);
    }
  },
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

// Imported transitively through JobPostCard (whose request shapes this surface
// reuses); nothing here renders it, but the module is loaded.
vi.mock("@/lib/useApi", () => ({
  useApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
  usePolledApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));

vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [] }) }));

// next/link reaches for the App Router context, which does not exist in a bare
// render — a plain anchor that keeps the href is all these assertions need.
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

// The markdown pipeline is not under test here and only slows the run down.
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import {
  RoundTable,
  dispatchLabel,
  dispatchTarget,
} from "@/components/agents/RoundTable";
import { JOB_ORIGIN, TEAM_TARGET } from "@/components/agents/JobPostCard";
import type { Participant } from "@/components/agents/identity";
import type { RosterEntry } from "@/components/agents/RosterStrip";

// jsdom has no scrollIntoView; the RoundTable pins its transcript with it.
window.HTMLElement.prototype.scrollIntoView = () => {};

const BUILDER: Participant = {
  key: "builtin:builder",
  source: "builtin",
  name: "builder",
  role: "lead",
};
const REMY: Participant = {
  key: "dynamic:remy",
  source: "dynamic",
  name: "remy",
  role: "critic",
};
const SUPERVISOR: Participant = {
  key: "builtin:supervisor",
  source: "builtin",
  name: "supervisor",
  role: "lead",
};
const VR: Participant = {
  key: "remote:vr-assistant",
  source: "remote",
  name: "vr-assistant",
  role: "researcher",
};

function threadWith(participants: Participant[]) {
  return {
    id: "t1",
    title: "Pricing",
    participants,
    message_count: 2,
    updated_at: "2026-08-16T10:00:00Z",
    messages: [
      { who: "user", content: "flat rate or per-seat?", at: "2026-08-16T10:00:00Z" },
      { who: participants[0]?.key ?? "builtin:builder", content: "hello there", at: "2026-08-16T10:00:05Z" },
    ],
  };
}

const ROSTER: RosterEntry[] = [
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
    description: "Crunches numbers",
    delegable: true,
    healthy: true,
    stats: null,
  },
  {
    name: "remote:down-box",
    kind: "remote",
    description: "Agent on the mac mini",
    delegable: true,
    healthy: false,
    stats: null,
  },
];

const SESSIONS = {
  sessions: [
    {
      id: "s-old",
      task: "Rename the 26 files",
      agent_type: "supervisor",
      provider: "mock",
      model: "m",
      status: "completed",
      workspace_path: "/w",
      summary: "",
      origin: JOB_ORIGIN,
      created_at: "2026-08-16T09:00:00Z",
      finished_at: "2026-08-16T09:10:00Z",
    },
    {
      id: "s-chat",
      task: "A chat escalation, not a job",
      agent_type: "builder",
      provider: "mock",
      model: "m",
      status: "active",
      workspace_path: "/w",
      summary: "",
      origin: "chat",
      created_at: "2026-08-16T09:30:00Z",
      finished_at: null,
    },
  ],
};

function renderTable(props: Partial<React.ComponentProps<typeof RoundTable>> = {}) {
  return render(
    <RoundTable
      threadId="t1"
      reloadNonce={0}
      onEditPanel={() => {}}
      onRoundDone={() => {}}
      {...props}
    />,
  );
}

/** Render, wait for the transcript, type the task into the ONE composer. */
async function openThread(text = "Rename all 26 files in this folder") {
  const view = renderTable();
  expect(await screen.findByText("hello there")).toBeInTheDocument();
  const box = screen.getByLabelText("Message the panel") as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: text } });
  return { ...view, box };
}

/** The dispatch button, whatever it is currently named. */
function giveButton() {
  return screen.getByRole("button", { name: /give it to/i });
}

/** The one POST that started a session (any of the dispatch routes). */
function dispatchCall() {
  return api.posts.find((p) => p.path === "/sessions" || p.path.endsWith("/spawn"));
}

beforeEach(() => {
  api.thread = threadWith([BUILDER]);
  api.projects = { projects: [{ id: "p1", name: "Tax season", brief: "", root: "", status: "active", created_at: "2026-01-01T00:00:00Z" }] };
  api.sessions = SESSIONS;
  api.posts = [];
  api.onPost = null;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/* -------------------------------------------------------- the target rule */

describe("dispatchTarget — who a thread hands its work to", () => {
  it("sends a lone builtin agent's thread to that agent", () => {
    expect(dispatchTarget([BUILDER])).toBe("builder");
    expect(dispatchLabel(dispatchTarget([BUILDER]))).toBe("builder");
  });

  it("sends a lone dynamic agent's thread to that agent's own route", () => {
    // "custom:" is what jobRequest keys off to reach /agents/<slug>/spawn —
    // a bare "remy" would post to /sessions and be silently downgraded to
    // Builder, which looks like a working dispatch and is not one.
    expect(dispatchTarget([REMY])).toBe("custom:remy");
    expect(dispatchLabel("custom:remy")).toBe("remy");
  });

  it("sends a panel of several agents to the team", () => {
    expect(dispatchTarget([BUILDER, REMY])).toBe(TEAM_TARGET);
    expect(dispatchLabel(TEAM_TARGET)).toBe("the team");
  });

  it("sends a supervisor thread to the team — it IS the team", () => {
    expect(dispatchTarget([SUPERVISOR])).toBe(TEAM_TARGET);
  });

  it("routes a lone remote through the supervisor bridge, never a local agent", () => {
    expect(dispatchTarget([VR])).toBe("remote:vr-assistant");
  });
});

/* ------------------------------------------------------------ dispatching */

describe("a thread dispatches real work", () => {
  it("posts a session targeted at the thread's one agent", async () => {
    await openThread();
    expect(giveButton()).toHaveTextContent("Give it to builder");
    fireEvent.click(giveButton());
    await waitFor(() => {
      const call = dispatchCall();
      expect(call).toBeTruthy();
      expect(call!.path).toBe("/sessions");
      // THE BODY IS THE EVIDENCE — pinned whole, because a dropped field
      // fails silently (no origin ⇒ invisible to every recent-jobs list).
      expect(call!.body).toEqual({
        task: "Rename all 26 files in this folder",
        agent_type: "builder",
        wait: false,
        origin: JOB_ORIGIN,
      });
    });
  });

  it("posts a lone dynamic agent's job to its own spawn route", async () => {
    api.thread = threadWith([REMY]);
    await openThread();
    fireEvent.click(giveButton());
    await waitFor(() => {
      const call = dispatchCall();
      expect(call).toBeTruthy();
      expect(call!.path).toBe("/agents/remy/spawn");
      expect(call!.body.task).toBe("Rename all 26 files in this folder");
      expect(call!.body.origin).toBe(JOB_ORIGIN);
    });
  });

  it("dispatches a multi-agent panel to the team", async () => {
    api.thread = threadWith([BUILDER, REMY]);
    await openThread();
    expect(giveButton()).toHaveTextContent("Give it to the team");
    fireEvent.click(giveButton());
    await waitFor(() => {
      const call = dispatchCall();
      expect(call).toBeTruthy();
      expect(call!.path).toBe("/sessions");
      // The Team means a SUPERVISOR session that plans and delegates — not a
      // builder, and not N parallel sessions.
      expect(call!.body.agent_type).toBe("supervisor");
    });
  });

  it("hands the job to the TEAM when the user picks it in a 1:1 thread", async () => {
    // RESTORED BY ADVERSARIAL REVIEW (v1.180.0). agents-room-v1179's deleted
    // "the TEAM case is one click away and defaults to the team" guarded the
    // one capability the removed disclosure had no other home for: posting to
    // the supervisor. Its named replacements assert that the Team is LISTED
    // ("offers the team and the roster's delegable agents") and that a
    // multi-agent thread DEFAULTS to it ("dispatches a multi-agent panel to the
    // team") — and neither notices if picking it does nothing. Measured:
    // replacing this select's onChange with `() => {}` left all 105 tests in
    // the five agents files green. That is JobPostCard's own lesson reached
    // from the other side — the select saying one thing while the button posts
    // another — and on a 1:1 thread (the common case) choosing the Team is the
    // ONLY way to reach the supervisor at all.
    //
    // Driven with NO roster on purpose: the Team is the first option on every
    // daemon, so this also pins that the oldest one can still reach it.
    await openThread("Plan the whole migration");
    fireEvent.click(screen.getByRole("button", { name: /job options/i }));
    await waitFor(() =>
      expect(screen.getByLabelText("Who takes the job")).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText("Who takes the job"), {
      target: { value: TEAM_TARGET },
    });
    // The select and the BUTTON are one claim — a target the button does not
    // name is a target the user did not agree to. Both inside the waitFor, and
    // the select re-queried each pass rather than held across commits.
    await waitFor(() => {
      expect(
        (screen.getByLabelText("Who takes the job") as HTMLSelectElement).value,
      ).toBe(TEAM_TARGET);
      expect(giveButton()).toHaveTextContent("Give it to the team");
    });
    fireEvent.click(giveButton());
    await waitFor(() => {
      const call = dispatchCall();
      expect(call).toBeTruthy();
      expect(call!.path).toBe("/sessions");
      // The Team means a SUPERVISOR session that plans and delegates — not the
      // builder this thread happens to be with.
      expect(call!.body.agent_type).toBe("supervisor");
      expect(call!.body.task).toBe("Plan the whole migration");
    });
  });

  it("refuses to dispatch an empty task", async () => {
    await openThread("");
    expect(giveButton()).toBeDisabled();
    fireEvent.click(giveButton());
    expect(dispatchCall()).toBeUndefined();
  });
});

/* -------------------------------------------- the controls that moved here */

describe("project grounding and the step budget reach the request", () => {
  async function openOptions() {
    const view = await openThread();
    fireEvent.click(screen.getByRole("button", { name: /job options/i }));
    // The controls, not a proxy for them.
    await screen.findByLabelText("Project (optional)");
    return view;
  }

  it("carries the chosen project and budget into the body", async () => {
    await openOptions();
    // Wait for the OPTION, not just the select. The project list arrives from
    // an async fetch, and setting a <select> to a value with no matching
    // <option> is a silent no-op in jsdom — so on a contended runner the change
    // did nothing, the body omitted project_id, and this went red on CI while
    // passing everywhere else (2026-08-20). Waiting for the label was waiting
    // for a proxy; the option is the thing being acted on.
    await screen.findByRole("option", { name: "Tax season" });
    fireEvent.change(screen.getByLabelText("Project (optional)"), {
      target: { value: "p1" },
    });
    // The select really holds it before we dispatch.
    expect(
      (screen.getByLabelText("Project (optional)") as HTMLSelectElement).value,
    ).toBe("p1");
    fireEvent.change(screen.getByLabelText("Max steps (optional)"), {
      target: { value: "40" },
    });
    fireEvent.click(giveButton());
    await waitFor(() => {
      const call = dispatchCall();
      expect(call).toBeTruthy();
      expect(call!.body.project_id).toBe("p1");
      expect(call!.body.max_steps).toBe(40);
    });
    // …and the receipt states what the dispatch CARRIED, by name — an id the
    // user never saw would explain nothing.
    expect(
      await screen.findByText(/Running with the 40-step budget you set/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Grounded in Tax season/i)).toBeInTheDocument();
  });

  it("adds no key at all when neither is set", async () => {
    await openOptions();
    fireEvent.click(giveButton());
    await waitFor(() => {
      const call = dispatchCall();
      expect(call).toBeTruthy();
      // Blank means "the configured default", which is a DIFFERENT request
      // from project_id:"" or max_steps:0.
      expect("project_id" in call!.body).toBe(false);
      expect("max_steps" in call!.body).toBe(false);
    });
    expect(
      await screen.findByText(/configured default step budget/i),
    ).toBeInTheDocument();
  });

  it("blocks a budget the daemon would reject instead of posting it", async () => {
    await openOptions();
    fireEvent.change(screen.getByLabelText("Max steps (optional)"), {
      target: { value: "1000" },
    });
    expect(giveButton()).toBeDisabled();
    fireEvent.click(giveButton());
    expect(dispatchCall()).toBeUndefined();
    expect(screen.getByText(/whole number from 1 to 200/i)).toBeInTheDocument();
  });

  it("still explains a blocked budget after Job options is folded away", async () => {
    // The budget lives behind the disclosure but disables the DISPATCH BUTTON
    // globally. Closing the panel unmounts the hint that explains it, so
    // without a hoisted line the user is left with a dead button and a hover
    // title — the "silently blocked" twin of the silently-dropped field this
    // whole control exists to prevent.
    await openOptions();
    fireEvent.change(screen.getByLabelText("Max steps (optional)"), {
      target: { value: "1000" },
    });
    fireEvent.click(screen.getByRole("button", { name: /job options/i }));
    await waitFor(() => {
      expect(screen.queryByLabelText("Max steps (optional)")).toBeNull();
    });
    expect(giveButton()).toBeDisabled();
    expect(
      screen.getByText(/not a whole number from 1 to 200/i),
    ).toBeInTheDocument();
  });

  it("says the project list did not load rather than 'you have none'", async () => {
    api.projects = null; // this daemon 404s the route
    await openThread();
    fireEvent.click(screen.getByRole("button", { name: /job options/i }));
    await waitFor(() => {
      // The daemon's OWN reason, relayed — not replaced by a friendlier
      // sentence, and not swallowed into an innocent-looking empty select.
      expect(screen.getByText("Not Found")).toBeInTheDocument();
    });
    // A silent empty select would assert the user has no projects.
    expect(
      (screen.getByLabelText("Project (optional)") as HTMLSelectElement).options,
    ).toHaveLength(1);
  });
});

/* ---------------------------------------------------- the recent-jobs list */

describe("the recent-jobs list is still reachable", () => {
  it("lists the dispatched sessions, and only those", async () => {
    await openThread();
    fireEvent.click(screen.getByRole("button", { name: /job options/i }));
    const row = await screen.findByTitle("Rename the 26 files");
    expect(row).toHaveAttribute("href", "/sessions/s-old");
    // origin "chat" is not a job — the origin stamp is the whole filter.
    expect(screen.queryByTitle(/A chat escalation/)).toBeNull();
  });

  it("says the list did not load rather than showing an empty one", async () => {
    api.sessions = null; // a dead fetch — lib/api maps it to status 0
    await openThread();
    fireEvent.click(screen.getByRole("button", { name: /job options/i }));
    await waitFor(() => {
      expect(screen.getByText(/job list didn't load/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/No jobs have been dispatched yet/i)).toBeNull();
  });
});

/* --------------------------------------------- talking vs. doing, honestly */

describe("a dispatch is visibly distinct from a chat round", () => {
  it("names the session it started and says nobody spoke in the thread", async () => {
    await openThread();
    fireEvent.click(giveButton());
    await waitFor(() => {
      expect(screen.getByText(/Session started/i)).toBeInTheDocument();
    });
    const receipt = screen.getByRole("status");
    expect(receipt).toHaveTextContent(/builder is doing the work/i);
    // THE SENTENCE THAT MAKES THE TWO ACTS DISTINGUISHABLE.
    expect(receipt).toHaveTextContent(/not a round/i);
    expect(receipt).toHaveTextContent(/nobody spoke in the thread/i);
    // …and what did NOT go with it: the body's `task` is the composer text and
    // nothing else, so a user who has been talking here for ten messages is
    // told the transcript stayed behind rather than assuming it rode along.
    expect(receipt).toHaveTextContent(/only the text you typed went with it/i);
    expect(screen.getByRole("link", { name: /watch it run/i })).toHaveAttribute(
      "href",
      "/sessions/s-new",
    );
    // A dispatch is NOT a round: /say was never called and the transcript
    // gained nothing. (The optimistic user bubble a round renders would be
    // here otherwise, claiming the panel was asked.)
    expect(api.posts.some((p) => p.path.endsWith("/say"))).toBe(false);
    expect(screen.queryByText("Rename all 26 files in this folder")).toBeNull();
  });

  it("a round starts no session and shows no dispatch receipt", async () => {
    await openThread("what do you think?");
    fireEvent.click(screen.getByRole("button", { name: /ask the panel/i }));
    await waitFor(() => {
      expect(api.posts.some((p) => p.path.endsWith("/say"))).toBe(true);
    });
    expect(dispatchCall()).toBeUndefined();
    expect(screen.queryByText(/Session started/i)).toBeNull();
  });

  it("says so plainly when the daemon answers without a session id", async () => {
    api.onPost = () => ({ status: "active" }); // accepted, but no id came back
    await openThread();
    fireEvent.click(giveButton());
    await waitFor(() => {
      expect(screen.getByText(/returned no session id/i)).toBeInTheDocument();
    });
    // …and no link to /sessions/undefined is rendered in its place.
    expect(screen.queryByRole("link", { name: /watch it run/i })).toBeNull();
  });
});

/* -------------------------------------------------------- honest failure */

describe("a failed dispatch starts nothing", () => {
  it("shows the daemon's reason, keeps the task, and claims no session", async () => {
    api.onPost = () => {
      throw new api.FakeApiError("unknown agent type 'builder'", 400);
    };
    const { box } = await openThread();
    fireEvent.click(giveButton());
    await waitFor(() => {
      expect(screen.getByText(/unknown agent type/i)).toBeInTheDocument();
    });
    // NO RECEIPT, no link, and exactly one attempt — nothing was started.
    expect(screen.queryByText(/Session started/i)).toBeNull();
    expect(screen.queryByRole("link", { name: /watch it run/i })).toBeNull();
    expect(api.posts).toHaveLength(1);
    // …and the typed task is still in the box: the retry is one click.
    expect(box.value).toBe("Rename all 26 files in this folder");
  });

  it("names the offline daemon rather than an empty message", async () => {
    api.onPost = () => {
      throw new api.FakeApiError("", 0); // lib/api maps a dead fetch to 0
    };
    await openThread();
    fireEvent.click(giveButton());
    await waitFor(() => {
      expect(screen.getByText(/no session was started/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/Session started/i)).toBeNull();
  });
});

/* ------------------------------------------- the rail's Give-work, carried */

describe("the roster can still hand work to an agent", () => {
  it("arms the target from an assign, and says it is not in this thread", async () => {
    renderTable({
      roster: ROSTER,
      assign: { kind: "dynamic", name: "analyst", nonce: 1 },
    });
    expect(await screen.findByText("hello there")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Message the panel"), {
      target: { value: "Crunch the numbers" },
    });
    expect(giveButton()).toHaveTextContent("Give it to analyst");
    expect(screen.getByText(/analyst is not in this thread/i)).toBeInTheDocument();
    fireEvent.click(giveButton());
    await waitFor(() => {
      const call = dispatchCall();
      expect(call).toBeTruthy();
      expect(call!.path).toBe("/agents/analyst/spawn");
    });
  });

  it("falls back VISIBLY to the thread's agent when the armed one can't take it", async () => {
    // An offline remote cannot take a session. Both the button and the select
    // must read the same value — a select showing one target while the button
    // posts another is the split JobPostCard learned the hard way.
    renderTable({
      roster: ROSTER,
      assign: { kind: "remote", name: "down-box", nonce: 2 },
    });
    expect(await screen.findByText("hello there")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Message the panel"), {
      target: { value: "Do the thing" },
    });
    expect(giveButton()).toHaveTextContent("Give it to builder");
    fireEvent.click(screen.getByRole("button", { name: /job options/i }));
    // The VALUE is what is waited on, not the label that carries it — see the
    // note on the next test for why awaiting the control and then asserting
    // its value is the shape this repo bans.
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Who takes the job") as HTMLSelectElement).value,
      ).toBe("builder"),
    );
    fireEvent.click(giveButton());
    await waitFor(() => {
      expect(dispatchCall()?.body.agent_type).toBe("builder");
    });
  });

  it("keeps the thread's own agent pickable when the daemon serves no roster", async () => {
    // JobPostCard's exact lesson, and it is invisible when broken: a
    // controlled <select> whose value has no rendered <option> shows BLANK
    // (selectedIndex -1) while the button happily posts to that invisible
    // target. On a daemon with no /agents/roster the thread's own agent is the
    // ONLY target there is, so it must be an option — the select and the
    // button have to read the same thing.
    //
    // THE ASSERTION IS THE WAIT (CLAUDE.md). This test was intermittent, and
    // its shape is the reason: `findByLabelText` waits for the DISCLOSURE TO
    // OPEN — a proxy signal that lands on the click's own commit — and the
    // value inside it is a DIFFERENT fact, derived from the thread detail and
    // rendered on whichever commit the fetch settles into. Worse, the value
    // was read off a node captured BEFORE those commits, so a re-render could
    // leave the assertion looking at a stale reference. Both halves are fixed
    // the way v1.177.1 and v1.178.0 were: everything real happens inside one
    // `waitFor`, and the node is re-queried each pass rather than held.
    renderTable(); // roster defaults to []
    expect(await screen.findByText("hello there")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /job options/i }));
    await waitFor(() => {
      const select = screen.getByLabelText("Who takes the job") as HTMLSelectElement;
      // A controlled <select> whose value has no rendered <option> shows BLANK
      // (selectedIndex -1), so the value and the option list are one claim and
      // are asserted together.
      expect(select.value).toBe("builder");
      expect(
        (within(select).getAllByRole("option") as HTMLOptionElement[]).map(
          (o) => o.value,
        ),
      ).toContain("builder");
      expect(giveButton()).toHaveTextContent("Give it to builder");
    });
  });

  it("offers the team and the roster's delegable agents", async () => {
    renderTable({ roster: ROSTER });
    expect(await screen.findByText("hello there")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /job options/i }));
    // Same shape rule as the test above: the option list is what is waited on,
    // re-queried each pass, never read off a node captured earlier.
    await waitFor(() => {
      const select = screen.getByLabelText("Who takes the job") as HTMLSelectElement;
      const opts = within(select).getAllByRole("option") as HTMLOptionElement[];
      expect(opts.map((o) => o.value)).toEqual([
        TEAM_TARGET,
        "builder",
        "custom:analyst",
        "remote:down-box",
      ]);
      // Listed but unpickable — hiding it would look like it does not exist.
      expect(opts.find((o) => o.value === "remote:down-box")!.disabled).toBe(true);
    });
  });
});
