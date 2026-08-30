import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * The job-post card (v1.166.0) — "Give work" on the Agents page.
 *
 * WHAT THESE TESTS GUARD: the DISPATCH SHAPES. Each target kind posts a
 * different request, and getting one wrong fails silently on screen:
 *   - a dynamic agent posted to /sessions is silently downgraded to Builder;
 *   - a remote posted anywhere but through the supervisor wrapper either
 *     404s or (worse) quietly reroutes to a local agent;
 *   - a missing origin makes the job invisible to the recent-jobs list.
 * So the bodies are pinned with toEqual — a dropped field is a failure, not
 * a "still posts something" pass.
 */

const hooks = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: Record<string, unknown> }>,
  postResult: { id: "s-new", status: "active" } as unknown,
  postFail: null as string | null,
  /** Milliseconds before the mocked POST settles. 0 = immediately. */
  postDelayMs: 0,
  // Every usePolledApi registration: the mock must CAPTURE the interval, or
  // the cadence is untestable — a mutation turning 8s into 80ms (hammering
  // the daemon) or 800s (a stale list) would pass every dispatch test.
  pollIntervals: [] as Array<{ path: string | null; interval: number }>,
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null, intervalMs = 5000) => {
    hooks.pollIntervals.push({ path, interval: intervalMs });
    return {
      data: path ? (hooks.api[path] ?? null) : null,
      error: null,
      loading: false,
      reload: () => {},
    };
  },
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
    get: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    post: (path: string, body: Record<string, unknown>) => {
      hooks.posts.push({ path, body });
      // `postDelayMs` exists for ONE test (see "still in flight" below): it
      // reproduces a loaded runner, where the card is still busy on the line
      // after a `postJob`. Zero by default, so every other test keeps the
      // immediate resolution it was written against.
      const settle = hooks.postDelayMs
        ? new Promise((r) => setTimeout(r, hooks.postDelayMs))
        : Promise.resolve();
      return settle.then(() =>
        hooks.postFail
          ? Promise.reject(new ApiError(hooks.postFail))
          : hooks.postResult,
      );
    },
  };
});

// next/link reaches for the App Router context, which does not exist in a
// bare render — swap in a plain anchor that keeps the href.
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

import {
  JobPostCard,
  jobRequest,
  jobSessions,
  wireTarget,
  JOB_ORIGIN,
  TEAM_TARGET,
} from "@/components/agents/JobPostCard";
import { RosterStrip, type RosterEntry } from "@/components/agents/RosterStrip";
import type { SessionView } from "@/lib/types";

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

function sessionRow(over: Partial<SessionView>): SessionView {
  return {
    id: "s-x",
    task: "a task",
    agent_type: "builder",
    provider: "mock",
    model: "m",
    status: "completed",
    workspace_path: "",
    summary: "",
    origin: null,
    created_at: "2026-08-01T10:00:00Z",
    finished_at: null,
    ...over,
  };
}

beforeEach(() => {
  hooks.api = {};
  hooks.posts = [];
  hooks.postResult = { id: "s-new", status: "active" };
  hooks.postFail = null;
  hooks.postDelayMs = 0;
  hooks.pollIntervals = [];
  // jsdom has no scrollIntoView, and these components are rendered inside
  // pages that call it — stubbed so a stray call is never the failure.
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

const targetSelect = () =>
  screen.getByLabelText("Who takes it") as HTMLSelectElement;
const taskBox = () => screen.getByLabelText("Job") as HTMLTextAreaElement;
const postButton = () => screen.getByRole("button", { name: /Post job/ });

async function postJob(task: string, target?: string) {
  fireEvent.change(taskBox(), { target: { value: task } });
  if (target !== undefined)
    fireEvent.change(targetSelect(), { target: { value: target } });
  fireEvent.click(postButton());
  // NOTE: this resolves when the POST is ISSUED, not when it settles — which
  // is the whole reason the card can still be in its busy state on the line
  // after a `postJob`. See `successNote` below.
  await waitFor(() => expect(hooks.posts.length).toBeGreaterThan(0));
}

/** The live region that SAYS a given thing, rather than "the live region".
 *
 *  The card has two of them and they are both correct: `SuccessNote` and the
 *  Post button's `LoaderInline` (`role="status"` since v1.185.0 — a spinner
 *  frozen by `prefers-reduced-motion` has to announce itself in words). A
 *  role query alone therefore names whichever happens to be mounted, which is
 *  a timing question, and CI answered it "Posting…" once. */
function queryStatusSaying(text: RegExp): HTMLElement | null {
  return (
    screen.queryAllByRole("status").find((el) => text.test(el.textContent ?? "")) ??
    null
  );
}

/** …and the awaited form, for the success line. */
async function successNote(): Promise<HTMLElement> {
  let found: HTMLElement | null = null;
  await waitFor(() => {
    found = queryStatusSaying(/Job posted/);
    expect(found).not.toBeNull();
  });
  return found!;
}

/* ------------------------------------------------- jobRequest (the shapes) */

describe("jobRequest — the frozen dispatch shapes", () => {
  it("Team → POST /sessions as a supervisor session with the origin stamp", () => {
    expect(jobRequest(TEAM_TARGET, "Ship the report", "")).toEqual({
      path: "/sessions",
      body: {
        task: "Ship the report",
        agent_type: "supervisor",
        wait: false,
        origin: "job:agents",
      },
    });
  });

  it("builtin → POST /sessions with THAT agent_type", () => {
    const req = jobRequest("researcher", "Find sources", "");
    expect(req.path).toBe("/sessions");
    expect(req.body.agent_type).toBe("researcher");
    expect(req.body.task).toBe("Find sources");
    expect(req.body.origin).toBe(JOB_ORIGIN);
    expect(req.body.wait).toBe(false);
  });

  it("dynamic custom:<slug> → the spawn route, and NO agent_type in the body", () => {
    expect(jobRequest("custom:analyst", "Crunch it", "")).toEqual({
      path: "/agents/analyst/spawn",
      body: { task: "Crunch it", wait: false, origin: "job:agents" },
    });
  });

  it("the spawn slug is URL-encoded", () => {
    expect(jobRequest("custom:a b/c", "t", "").path).toBe(
      "/agents/a%20b%2Fc/spawn",
    );
  });

  it("remote → a supervisor session with the exact delegate-and-verify prefix", () => {
    const req = jobRequest("remote:opus-box", "Ship the report", "");
    expect(req.path).toBe("/sessions");
    expect(req.body.agent_type).toBe("supervisor");
    expect(req.body.task).toBe(
      'Delegate this job to the remote agent "remote:opus-box" via the ' +
        "delegate tool, verify its reply, and report honestly:\n\n" +
        "Ship the report",
    );
    expect(req.body.origin).toBe(JOB_ORIGIN);
  });

  it("a chosen project rides as project_id; no project means NO key at all", () => {
    expect(jobRequest(TEAM_TARGET, "t", "p1").body.project_id).toBe("p1");
    expect("project_id" in jobRequest(TEAM_TARGET, "t", "").body).toBe(false);
    expect(jobRequest("custom:analyst", "t", "p2").body.project_id).toBe("p2");
  });
});

describe("wireTarget", () => {
  it("maps kind + bare name back to the roster wire name", () => {
    expect(wireTarget("dynamic", "analyst")).toBe("custom:analyst");
    expect(wireTarget("remote", "opus-box")).toBe("remote:opus-box");
    expect(wireTarget("builtin", "builder")).toBe("builder");
  });
});

describe("jobSessions", () => {
  it("keeps only job:* origins, newest first", () => {
    const rows = jobSessions([
      sessionRow({ id: "old", origin: "job:agents", created_at: "2026-08-01T10:00:00Z" }),
      sessionRow({ id: "not-a-job", origin: "schedule:daily" }),
      sessionRow({ id: "no-origin", origin: null }),
      sessionRow({ id: "new", origin: "job:agents", created_at: "2026-08-02T10:00:00Z" }),
    ]);
    expect(rows.map((s) => s.id)).toEqual(["new", "old"]);
  });
});

/* --------------------------------------------------------- the card itself */

describe("JobPostCard — targets", () => {
  it("defaults to the Team option, worded exactly", () => {
    render(<JobPostCard roster={ROSTER} />);
    expect(targetSelect().value).toBe(TEAM_TARGET);
    expect(
      screen.getByRole("option", {
        name: "Team — supervisor plans & delegates",
      }),
    ).toBeTruthy();
  });

  it("lists only delegable roster entries (supervisor is Team-only)", () => {
    render(<JobPostCard roster={ROSTER} />);
    const names = Array.from(targetSelect().options).map((o) => o.value);
    expect(names).toEqual([
      TEAM_TARGET,
      "builder",
      "custom:analyst",
      "remote:opus-box",
      "remote:down-box",
    ]);
  });

  it("an offline remote is listed but disabled, and says so", () => {
    render(<JobPostCard roster={ROSTER} />);
    const down = screen.getByRole("option", {
      name: /down-box.*\(offline\)/,
    }) as HTMLOptionElement;
    expect(down.disabled).toBe(true);
    const up = screen.getByRole("option", {
      name: /opus-box/,
    }) as HTMLOptionElement;
    expect(up.disabled).toBe(false);
  });

  it("works with an empty roster (older daemon): Team is still there", () => {
    render(<JobPostCard />);
    expect(targetSelect().options).toHaveLength(1);
    expect(targetSelect().value).toBe(TEAM_TARGET);
  });

  it("the assign prop preselects the wire target", () => {
    render(
      <JobPostCard
        roster={ROSTER}
        assign={{ kind: "dynamic", name: "analyst", nonce: 1 }}
      />,
    );
    expect(targetSelect().value).toBe("custom:analyst");
  });

  // The card renders the PAGE's roster fetch while RosterStrip runs its own,
  // so a Give-work click can name an agent this card's option list doesn't
  // hold (page fetch failed or lagged). A controlled select with an unmatched
  // value renders BLANK while submit would still post to the invisible
  // target — the fallback must land on Team, VISIBLY and in the dispatch.
  it("an assign missing from the roster falls back to Team — never a blank select", () => {
    render(
      <JobPostCard
        roster={[]}
        assign={{ kind: "dynamic", name: "ghost", nonce: 1 }}
      />,
    );
    const sel = targetSelect();
    expect(sel.value).toBe(TEAM_TARGET);
    expect(sel.selectedIndex).toBe(0); // not -1: the shown option is real
  });

  it("posting after a mismatched assign dispatches to the Team the select shows", async () => {
    render(
      <JobPostCard
        roster={[]}
        assign={{ kind: "remote", name: "gone-box", nonce: 2 }}
      />,
    );
    await postJob("Ship it");
    expect(hooks.posts).toEqual([
      {
        path: "/sessions",
        body: {
          task: "Ship it",
          agent_type: "supervisor",
          wait: false,
          origin: "job:agents",
        },
      },
    ]);
  });
});

describe("JobPostCard — dispatch", () => {
  it("Team posts the exact /sessions body", async () => {
    render(<JobPostCard roster={ROSTER} />);
    await postJob("Ship the report");
    expect(hooks.posts).toEqual([
      {
        path: "/sessions",
        body: {
          task: "Ship the report",
          agent_type: "supervisor",
          wait: false,
          origin: "job:agents",
        },
      },
    ]);
  });

  it("a dynamic target posts to the spawn route", async () => {
    render(<JobPostCard roster={ROSTER} />);
    await postJob("Crunch it", "custom:analyst");
    expect(hooks.posts).toEqual([
      {
        path: "/agents/analyst/spawn",
        body: { task: "Crunch it", wait: false, origin: "job:agents" },
      },
    ]);
  });

  it("a remote target rides the supervisor wrapper with the honest prefix", async () => {
    render(<JobPostCard roster={ROSTER} />);
    await postJob("Ship the report", "remote:opus-box");
    expect(hooks.posts[0].path).toBe("/sessions");
    expect(hooks.posts[0].body.agent_type).toBe("supervisor");
    expect(hooks.posts[0].body.task).toBe(
      'Delegate this job to the remote agent "remote:opus-box" via the ' +
        "delegate tool, verify its reply, and report honestly:\n\n" +
        "Ship the report",
    );
  });

  it("a selected project lands in the body as project_id", async () => {
    hooks.api["/projects"] = {
      projects: [
        { id: "p1", name: "Taxes", status: "active" },
        { id: "p2", name: "Archived one", status: "archived" },
      ],
    };
    render(<JobPostCard roster={ROSTER} />);
    // Archived projects are not offered.
    const proj = screen.getByLabelText("Project (optional)") as HTMLSelectElement;
    expect(Array.from(proj.options).map((o) => o.value)).toEqual(["", "p1"]);
    fireEvent.change(proj, { target: { value: "p1" } });
    await postJob("Do the books");
    expect(hooks.posts[0].body.project_id).toBe("p1");
  });

  it("the task is trimmed before posting", async () => {
    render(<JobPostCard roster={ROSTER} />);
    await postJob("  padded task  ");
    expect(hooks.posts[0].body.task).toBe("padded task");
  });

  it("an empty task cannot be posted", () => {
    render(<JobPostCard roster={ROSTER} />);
    expect((postButton() as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(postButton());
    expect(hooks.posts).toHaveLength(0);
  });

  it("success shows a line linking to /sessions/<id> and clears the task", async () => {
    hooks.postResult = { id: "s-42", status: "active" };
    render(<JobPostCard roster={ROSTER} />);
    await postJob("Ship it");
    // NOT `findByRole("status")` — WENT RED ON CI (v1.214.0), and the reason
    // is worth keeping. TWO live regions carry that role: the SuccessNote
    // below, and the Post button's `LoaderInline`, which has had
    // `role="status"` since v1.185.0 (a stopped spinner with no text reads as
    // an idle icon, so it announces itself). `findByRole` resolves on the
    // FIRST match that exists, so which one it returns is a race between the
    // mocked POST settling and the query — and on a loaded runner the answer
    // is "Posting…". Green here for nine releases, then red once.
    // Naming the note by what it SAYS removes the ambiguity outright.
    const note = await successNote();
    expect(note.textContent).toContain("Job posted");
    const link = screen.getByRole("link", { name: "watch it run" });
    expect(link.getAttribute("href")).toBe("/sessions/s-42");
    expect(taskBox().value).toBe("");
  });

  it("finds the success line even when the POST is still in flight", async () => {
    /* THE REGRESSION GUARD for the v1.214.0 CI failure, and the reason the two
     * assertions above name their note by its TEXT.
     *
     * The mock above resolves immediately, so on a quiet machine the card is
     * already out of its busy state by the time the next line runs and a bare
     * `findByRole("status")` gets the success note by luck. Here the POST
     * settles LATE, which is what a loaded CI runner does to every test at
     * once — so the Post button's spinner (`LoaderInline`, `role="status"`
     * since v1.185.0) is definitely mounted when the query first runs.
     *
     * MUTATION-PROVEN: with `await screen.findByRole("status")` in place of
     * `successNote()`, this fails with exactly the message CI produced —
     * `expected 'Posting…' to contain 'Job posted'`. */
    hooks.postResult = { id: "s-slow", status: "active" };
    const immediate = hooks.postDelayMs;
    hooks.postDelayMs = 60;
    try {
      render(<JobPostCard roster={ROSTER} />);
      await postJob("Ship it");
      // The spinner IS up — this is the state the old assertion tripped on.
      expect(queryStatusSaying(/Posting/)).not.toBeNull();
      expect(queryStatusSaying(/Job posted/)).toBeNull();
      const note = await successNote();
      expect(note.textContent).toContain("Job posted");
    } finally {
      hooks.postDelayMs = immediate;
    }
  });

  it("a failed post shows the server's error and no success line", async () => {
    hooks.postFail = "supervisor is busy";
    render(<JobPostCard roster={ROSTER} />);
    await postJob("Ship it");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("supervisor is busy");
    // Same ambiguity, from the other side: a bare `queryByRole("status")`
    // would also match the Post button's spinner, so this asserted "the post
    // has finished" as much as "there is no success line". It is the second
    // one that matters here, and it is the one the test's name claims.
    expect(queryStatusSaying(/Job posted/)).toBeNull();
    // The task box keeps the text — the user should not have to retype it.
    expect(taskBox().value).toBe("Ship it");
  });
});

describe("JobPostCard — recent jobs", () => {
  it("lists only job:* sessions, newest first, each linking to its session", () => {
    hooks.api["/sessions"] = {
      sessions: [
        sessionRow({
          id: "j-old",
          task: "Old job",
          origin: "job:agents",
          created_at: "2026-08-01T10:00:00Z",
        }),
        sessionRow({ id: "not-a-job", task: "Scheduled", origin: "schedule:x" }),
        sessionRow({
          id: "j-new",
          task: "New job",
          status: "active",
          origin: "job:agents",
          created_at: "2026-08-02T10:00:00Z",
        }),
      ],
    };
    render(<JobPostCard roster={ROSTER} />);
    const hrefs = screen
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["/sessions/j-new", "/sessions/j-old"]);
    expect(screen.queryByText("Scheduled")).toBeNull();
    // The status chip carries a real value, not a bare dot.
    expect(screen.getByText("active")).toBeTruthy();
  });

  it("renders no recent-jobs section at all when there are none", () => {
    hooks.api["/sessions"] = {
      sessions: [sessionRow({ id: "x", origin: "schedule:x" })],
    };
    render(<JobPostCard roster={ROSTER} />);
    expect(screen.queryByText(/Recent jobs/)).toBeNull();
  });

  it("polls GET /sessions at the plan's 8s cadence — not 80ms, not 800s", () => {
    render(<JobPostCard roster={ROSTER} />);
    const sessions = hooks.pollIntervals.filter((p) => p.path === "/sessions");
    expect(sessions).toEqual([{ path: "/sessions", interval: 8000 }]);
  });

  it("clips at 8 and says so honestly", () => {
    hooks.api["/sessions"] = {
      sessions: Array.from({ length: 10 }, (_, i) =>
        sessionRow({
          id: `j-${i}`,
          task: `Job ${i}`,
          origin: "job:agents",
          created_at: `2026-08-0${(i % 9) + 1}T10:00:00Z`,
        }),
      ),
    };
    render(<JobPostCard roster={ROSTER} />);
    expect(screen.getAllByRole("link")).toHaveLength(8);
    expect(screen.getByText("showing the latest 8 of 10 jobs")).toBeTruthy();
  });
});

/* ------------------------------------------------ RosterStrip "Give work" */

describe("RosterStrip — the Give-work button", () => {
  beforeEach(() => {
    hooks.api["/agents/roster"] = { roster: ROSTER };
  });

  it("hands back the kind and the BARE name", () => {
    const onAssign = vi.fn();
    render(<RosterStrip onAssign={onAssign} />);
    fireEvent.change(screen.getByLabelText("Choose an agent"), {
      target: { value: "custom:analyst" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Give work/ }));
    expect(onAssign).toHaveBeenCalledTimes(1);
    expect(onAssign).toHaveBeenCalledWith("dynamic", "analyst");
  });

  it("a non-delegable entry (supervisor) stays chat-only: no button", () => {
    const onAssign = vi.fn();
    render(<RosterStrip onAssign={onAssign} />);
    // Default selection is the first entry — supervisor, delegable:false.
    expect(screen.queryByRole("button", { name: /Give work/ })).toBeNull();
  });

  it("an offline remote gets no Give-work button", () => {
    const onAssign = vi.fn();
    render(<RosterStrip onAssign={onAssign} />);
    fireEvent.change(screen.getByLabelText("Choose an agent"), {
      target: { value: "remote:down-box" },
    });
    expect(screen.queryByRole("button", { name: /Give work/ })).toBeNull();
  });

  it("without the prop no button renders (older page composition)", () => {
    render(<RosterStrip />);
    fireEvent.change(screen.getByLabelText("Choose an agent"), {
      target: { value: "builder" },
    });
    expect(screen.queryByRole("button", { name: /Give work/ })).toBeNull();
  });
});

/* -------------------------------------------- the page wires the two ends */

// REMOVED IN v1.180.0: "roster click lands in the card's target select".
//
// It asserted the WIRING between the rail and a surface this page no longer
// has: the standalone "Post a job" card was taken off the Agents page because
// "the post a job seems to be redundant because if i choose to start a thread
// with an agent that would be the start of posting a new job". The wiring
// itself is very much alive and is asserted against the surface that replaced
// it, in agents-layout-v1180.test.tsx:
//   - "Give work opens the thread with that agent and arms the composer"
//     (the same gesture this test drove, including the scroll onto the
//     surface it opened), and
//   - "the narrow-width picker aims the composer exactly as the faces do"
//     (the <select> half, which is the control this test used).
// Everything above in this file still holds: JobPostCard itself is unchanged
// and still rendered by the page on daemons that serve no roster or no thread
// routes — see agents-layout-v1180.test.tsx's two degradation describes, which
// drive Give-work into this very card.
