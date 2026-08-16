import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * Job SIZING on the "Give work" card (v1.174.0, Contract 4).
 *
 * WHY THE CARD NEEDS A BOX AT ALL: a real run — "rename all files in this
 * folder" over 26 entries — died at `stopped: reached max steps before
 * completion` with zero files renamed. The only budget was
 * `config.max_agent_steps`, which is global: sizing that one job meant
 * resizing every small one.
 *
 * WHAT THESE TESTS GUARD, in order of how quietly it fails:
 *   1. blank must send NO `max_steps` key — `null`/`0` is a DIFFERENT request,
 *      and every pre-v1.174.0 dispatch has to stay byte-identical;
 *   2. an out-of-range value must not be silently dropped (the user would
 *      watch the job stop at the default having asked for 500) — it blocks the
 *      post and says why;
 *   3. the spawn route takes no budget, so the box is DISABLED there rather
 *      than posting a field the daemon drops on the floor.
 */

const hooks = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: Record<string, unknown> }>,
  postResult: { id: "s-new", status: "active" } as unknown,
  postFail: null as string | null,
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
    get: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    post: (path: string, body: Record<string, unknown>) => {
      hooks.posts.push({ path, body });
      return hooks.postFail
        ? Promise.reject(new ApiError(hooks.postFail))
        : Promise.resolve(hooks.postResult);
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

import {
  JobPostCard,
  jobMaxSteps,
  jobRequest,
  supportsMaxSteps,
  MAX_JOB_STEPS,
  MIN_JOB_STEPS,
  JOB_ORIGIN,
  TEAM_TARGET,
} from "@/components/agents/JobPostCard";
import type { RosterEntry } from "@/components/agents/RosterStrip";

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
    description: "Your analyst",
    delegable: true,
    healthy: true,
    stats: null,
  },
];

beforeEach(() => {
  hooks.api = {};
  hooks.posts = [];
  hooks.postResult = { id: "s-new", status: "active" };
  hooks.postFail = null;
});
afterEach(cleanup);

const taskBox = () => screen.getByLabelText("Job") as HTMLTextAreaElement;
const targetSelect = () =>
  screen.getByLabelText("Who takes it") as HTMLSelectElement;
const stepsBox = () =>
  screen.getByLabelText("Max steps (optional)") as HTMLInputElement;
const postButton = () =>
  screen.getByRole("button", { name: /Post job/ }) as HTMLButtonElement;

/* ------------------------------------------------------- jobMaxSteps (pure) */

describe("jobMaxSteps — text box → wire value", () => {
  it("blank (and whitespace) means the configured default: null, not 0", () => {
    expect(jobMaxSteps("")).toBeNull();
    expect(jobMaxSteps("   ")).toBeNull();
  });

  it("accepts whole numbers inside the daemon's own 1..200 bounds", () => {
    expect(MIN_JOB_STEPS).toBe(1);
    expect(MAX_JOB_STEPS).toBe(200);
    expect(jobMaxSteps("1")).toBe(1);
    expect(jobMaxSteps("40")).toBe(40);
    expect(jobMaxSteps(" 60 ")).toBe(60);
    expect(jobMaxSteps("200")).toBe(200);
  });

  it("refuses everything the daemon would 422 — and never clamps it", () => {
    // Clamping 1000 → 200 would run a job against a budget nobody chose.
    for (const bad of ["0", "201", "1000", "-5", "2.5", "12abc", "abc", "1e3"])
      expect(jobMaxSteps(bad)).toBeNull();
  });
});

describe("supportsMaxSteps — which route reads a budget", () => {
  it("POST /sessions does; the dynamic-agent spawn route does not", () => {
    expect(supportsMaxSteps(TEAM_TARGET)).toBe(true);
    expect(supportsMaxSteps("builder")).toBe(true);
    expect(supportsMaxSteps("remote:opus-box")).toBe(true);
    expect(supportsMaxSteps("custom:analyst")).toBe(false);
  });
});

/* ------------------------------------------------- jobRequest (the shapes) */

describe("jobRequest — max_steps in the body", () => {
  it("omitting the argument leaves every pre-v1.174.0 body byte-identical", () => {
    expect(jobRequest(TEAM_TARGET, "Ship the report", "")).toEqual({
      path: "/sessions",
      body: {
        task: "Ship the report",
        agent_type: "supervisor",
        wait: false,
        origin: JOB_ORIGIN,
      },
    });
  });

  it("a blank box adds NO key at all (absent ≠ null ≠ 0)", () => {
    const body = jobRequest(TEAM_TARGET, "t", "", "").body;
    expect("max_steps" in body).toBe(false);
  });

  it("a valid budget rides as a NUMBER on the /sessions body", () => {
    expect(jobRequest(TEAM_TARGET, "Rename them all", "", "40")).toEqual({
      path: "/sessions",
      body: {
        task: "Rename them all",
        agent_type: "supervisor",
        wait: false,
        origin: JOB_ORIGIN,
        max_steps: 40,
      },
    });
    expect(jobRequest("builder", "t", "p1", "60").body).toEqual({
      task: "t",
      agent_type: "builder",
      wait: false,
      origin: JOB_ORIGIN,
      project_id: "p1",
      max_steps: 60,
    });
  });

  it("a remote job keeps its wrapper prefix AND carries the budget", () => {
    const body = jobRequest("remote:opus-box", "Ship it", "", "30").body;
    expect(body.agent_type).toBe("supervisor");
    expect(body.max_steps).toBe(30);
    expect(String(body.task)).toContain("Delegate this job to the remote agent");
  });

  it("an out-of-range value is left out rather than clamped", () => {
    expect("max_steps" in jobRequest(TEAM_TARGET, "t", "", "1000").body).toBe(false);
    expect("max_steps" in jobRequest(TEAM_TARGET, "t", "", "0").body).toBe(false);
  });

  it("the spawn route never carries it — a field it drops is a budget lost", () => {
    expect(jobRequest("custom:analyst", "Crunch it", "", "40")).toEqual({
      path: "/agents/analyst/spawn",
      body: { task: "Crunch it", wait: false, origin: JOB_ORIGIN },
    });
  });
});

/* --------------------------------------------------------- the card itself */

describe("JobPostCard — the Max steps box", () => {
  it("starts blank and posts nothing extra", async () => {
    render(<JobPostCard roster={ROSTER} />);
    expect(stepsBox().value).toBe("");
    fireEvent.change(taskBox(), { target: { value: "Ship it" } });
    fireEvent.click(postButton());
    await waitFor(() => expect(hooks.posts.length).toBe(1));
    expect("max_steps" in hooks.posts[0].body).toBe(false);
  });

  it("a typed budget lands in the dispatch", async () => {
    render(<JobPostCard roster={ROSTER} />);
    fireEvent.change(taskBox(), {
      target: { value: "Rename all files in this folder" },
    });
    fireEvent.change(stepsBox(), { target: { value: "45" } });
    fireEvent.click(postButton());
    await waitFor(() => expect(hooks.posts.length).toBe(1));
    expect(hooks.posts[0].body.max_steps).toBe(45);
  });

  it("an out-of-range value BLOCKS the post and says the range", () => {
    render(<JobPostCard roster={ROSTER} />);
    fireEvent.change(taskBox(), { target: { value: "Ship it" } });
    fireEvent.change(stepsBox(), { target: { value: "1000" } });
    expect(postButton().disabled).toBe(true);
    expect(stepsBox().getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByText(/Enter a whole number from 1 to 200/)).toBeTruthy();
    fireEvent.click(postButton());
    expect(hooks.posts).toHaveLength(0);
  });

  it("clearing an invalid value re-enables the post", async () => {
    render(<JobPostCard roster={ROSTER} />);
    fireEvent.change(taskBox(), { target: { value: "Ship it" } });
    fireEvent.change(stepsBox(), { target: { value: "-3" } });
    expect(postButton().disabled).toBe(true);
    fireEvent.change(stepsBox(), { target: { value: "" } });
    expect(postButton().disabled).toBe(false);
    fireEvent.click(postButton());
    await waitFor(() => expect(hooks.posts.length).toBe(1));
    expect("max_steps" in hooks.posts[0].body).toBe(false);
  });

  it("the box is DISABLED for a custom agent and explains why", () => {
    render(<JobPostCard roster={ROSTER} />);
    fireEvent.change(targetSelect(), { target: { value: "custom:analyst" } });
    expect(stepsBox().disabled).toBe(true);
    expect(
      screen.getByText(/this route takes no step budget/i),
    ).toBeTruthy();
  });

  it("a value typed before switching to a custom agent is never posted", async () => {
    render(<JobPostCard roster={ROSTER} />);
    fireEvent.change(taskBox(), { target: { value: "Crunch it" } });
    fireEvent.change(stepsBox(), { target: { value: "40" } });
    fireEvent.change(targetSelect(), { target: { value: "custom:analyst" } });
    fireEvent.click(postButton());
    await waitFor(() => expect(hooks.posts.length).toBe(1));
    expect(hooks.posts[0].path).toBe("/agents/analyst/spawn");
    expect("max_steps" in hooks.posts[0].body).toBe(false);
  });

  it("an invalid value on an unsupported target does not block the post", () => {
    render(<JobPostCard roster={ROSTER} />);
    fireEvent.change(taskBox(), { target: { value: "Crunch it" } });
    fireEvent.change(stepsBox(), { target: { value: "1000" } });
    fireEvent.change(targetSelect(), { target: { value: "custom:analyst" } });
    // The box does not apply here, so it cannot veto the job.
    expect(postButton().disabled).toBe(false);
  });

  it("the budget is cleared with the task — sizing belongs to the job it sized", async () => {
    // It used to survive the post, so a 200-step budget typed for one big job
    // rode silently along on the next quick one: a setting outliving its job.
    render(<JobPostCard roster={ROSTER} />);
    fireEvent.change(taskBox(), { target: { value: "Job one" } });
    fireEvent.change(stepsBox(), { target: { value: "50" } });
    fireEvent.click(postButton());
    // Wait for THE CLEAR, not for the post. `submit` records the request and
    // only then — after its `await post(...)` resolves — calls setTask("") /
    // setMaxSteps(""). So `posts.length === 1` becomes true one render BEFORE
    // the boxes empty, and asserting the values right after it is a race: green
    // on a fast machine, red on a contended CI runner (it was, on v1.177.0).
    // Putting the real assertions inside waitFor lets them retry until the
    // state settles, which is what this test always meant to check.
    await waitFor(() => {
      expect(hooks.posts.length).toBe(1);
      expect(taskBox().value).toBe("");
      expect(stepsBox().value).toBe("");
    });
    // …and the next job posts NO budget rather than inheriting one.
    fireEvent.change(taskBox(), { target: { value: "Job two" } });
    fireEvent.click(postButton());
    await waitFor(() => expect(hooks.posts.length).toBe(2));
    expect("max_steps" in hooks.posts[1].body).toBe(false);
  });

  it("the success note states the budget the dispatch actually carried", async () => {
    // Nothing else echoes it back: a user who typed 60 and later reads
    // "reached max steps" must be able to tell spent from ignored.
    render(<JobPostCard roster={ROSTER} />);
    fireEvent.change(taskBox(), { target: { value: "Rename all of them" } });
    fireEvent.change(stepsBox(), { target: { value: "60" } });
    fireEvent.click(postButton());
    await waitFor(() => expect(hooks.posts.length).toBe(1));
    expect(hooks.posts[0].body.max_steps).toBe(60);
    await screen.findByText(/Running with the 60-step budget you set\./);
  });

  it("a default-budget job says THAT, rather than naming a number nobody set", async () => {
    render(<JobPostCard roster={ROSTER} />);
    fireEvent.change(taskBox(), { target: { value: "Quick one" } });
    fireEvent.click(postButton());
    await waitFor(() => expect(hooks.posts.length).toBe(1));
    await screen.findByText(/Running on the configured default step budget\./);
  });

  it("a spawn job reports the default — the route dropped the box's value", async () => {
    // The budget cannot reach POST /agents/<slug>/spawn, so claiming one here
    // would be a budget the user set, never got, and was told they had.
    render(<JobPostCard roster={ROSTER} />);
    fireEvent.change(taskBox(), { target: { value: "Crunch it" } });
    fireEvent.change(stepsBox(), { target: { value: "40" } });
    fireEvent.change(targetSelect(), { target: { value: "custom:analyst" } });
    fireEvent.click(postButton());
    await waitFor(() => expect(hooks.posts.length).toBe(1));
    await screen.findByText(/Running on the configured default step budget\./);
  });
});
