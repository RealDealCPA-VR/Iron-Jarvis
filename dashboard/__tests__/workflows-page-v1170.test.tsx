import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * v1.170.0 P7 — workflows page: starter catalog + run-history resume.
 *
 * What is guarded, each with a silent failure mode:
 *
 *  - the STARTER CATALOG is internally consistent: unique names, unique step
 *    names per starter (templating keys on them), every {{ref}} points at an
 *    EARLIER step in the same starter (a typo'd ref resolves to nothing at run
 *    time and the step silently gets an empty value), every starter teaches an
 *    ask/notify kind, and exactly one carries `expect` (contract 8);
 *  - loading a starter dispatches `ij:load-workflow` with a DEEP-copied
 *    detail — nothing is saved (suggest-don't-act), and canvas edits must
 *    never mutate the shared catalog;
 *  - prominence is honest: no saved workflows → expanded cards; some →
 *    collapsed Templates section; loading/offline → collapsed too, because
 *    "you have nothing yet" on an unanswered fetch is a guess, not a fact;
 *  - run history: `interrupted` rows get a Resume button that POSTs contract
 *    4's route and reloads; a 409 shows the server's message instead of
 *    pretending; `resuming` renders live (cyan) and `waiting`/`interrupted`
 *    amber while terminal statuses keep their shared tones;
 *  - the table keeps itself fresh while any run is LIVE per the shared
 *    WORKFLOW_RUN_TERMINAL set (a resumed run emits no completion event until
 *    the very end), and stops polling when everything is terminal;
 *  - the page actually MOUNTS the new section (deleting the call site must
 *    fail a test, not just the unit specs).
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
    calls: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    responses: {} as Record<string, unknown>,
    postResponses: {} as Record<string, unknown>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    api.calls.push(path);
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    }
    if (r instanceof api.FakeApiError) return Promise.reject(r);
    return Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    const r = api.postResponses[path];
    if (r instanceof api.FakeApiError) return Promise.reject(r);
    return Promise.resolve(r ?? {});
  },
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: true }),
}));

// The heavy neighbors — this file pins the page's new sections and their
// WIRING, not the reactflow editor or the arrival animation.
vi.mock("@/components/workflow/WorkflowCanvas", () => ({
  default: () => <div data-testid="canvas-stub" />,
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

// A Next page file may export NOTHING beyond its default (the .next/types
// build check rejects extra exports), so the pure helpers live in starters.ts
// and the sections are exercised through the page's default export.
import WorkflowsPage from "@/app/workflows/page";
import {
  STARTERS,
  isLiveRun,
  runBadgeTone,
  starterKindSummary,
  starterLoadDetail,
  stepKindHint,
} from "@/components/workflow/starters";
import { WORKFLOW_RUN_TERMINAL, type WorkflowRun } from "@/lib/types";

const RUNS_PATH = "/workflows/runs?limit=50";

beforeEach(() => {
  window.scrollTo = vi.fn();
  // jsdom elements have no scrollTo — the builder-chat thread autoscrolls.
  window.HTMLElement.prototype.scrollTo = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  api.calls = [];
  api.posts = [];
  api.responses = {};
  api.postResponses = {};
});

/* ------------------------------------------------------- catalog integrity */

describe("starter catalog", () => {
  it("ships 5 starters with unique names and 2+ named steps each", () => {
    expect(STARTERS).toHaveLength(5);
    expect(new Set(STARTERS.map((s) => s.name)).size).toBe(5);
    for (const s of STARTERS) {
      expect(s.title).toBeTruthy();
      expect(s.description).toBeTruthy();
      expect(s.blurb).toBeTruthy();
      expect(s.steps.length).toBeGreaterThanOrEqual(2);
      for (const st of s.steps) expect(st.name.trim()).toBeTruthy();
    }
  });

  it("step names are unique within a starter — templating keys on them", () => {
    for (const s of STARTERS) {
      const names = s.steps.map((st) => st.name);
      expect(new Set(names).size).toBe(names.length);
    }
  });

  it("every step kind is one of the engine's four", () => {
    for (const s of STARTERS)
      for (const st of s.steps)
        expect(["agent", "tool", "ask", "notify"]).toContain(st.kind ?? "agent");
  });

  it("every {{ref}} in a task/message/args points at an EARLIER step", () => {
    const rx = /\{\{\s*([^{}]+?)\s*\}\}/g;
    for (const s of STARTERS) {
      const seen = new Set<string>();
      for (const st of s.steps) {
        const text = [st.task ?? "", st.message ?? "", JSON.stringify(st.args ?? {})].join(
          "\n",
        );
        for (const m of text.matchAll(rx)) {
          expect(seen, `${s.name} / ${st.name} references {{${m[1]}}}`).toContain(m[1]);
        }
        seen.add(st.name);
      }
    }
  });

  it("every starter includes an ask or notify step; tool steps name a tool", () => {
    for (const s of STARTERS) {
      expect(
        s.steps.some((st) => st.kind === "ask" || st.kind === "notify"),
        s.name,
      ).toBe(true);
      for (const st of s.steps)
        if (st.kind === "tool") expect(st.tool, `${s.name} / ${st.name}`).toBeTruthy();
    }
  });

  it("exactly one starter carries an expect block (contract 8), on its writing step", () => {
    const withExpect = STARTERS.filter((s) => s.steps.some((st) => st.expect));
    expect(withExpect.map((s) => s.name)).toEqual(["client-intake-triage"]);
    const step = withExpect[0].steps.find((st) => st.expect)!;
    expect(step.expect).toEqual({ files: ["intake-summary.md"] });
    // The expectation names the very file the step's task promises to write.
    expect(step.task).toContain("intake-summary.md");
  });

  it("starterKindSummary tells the card what the run will do", () => {
    const intake = STARTERS.find((s) => s.name === "client-intake-triage")!;
    expect(starterKindSummary(intake)).toBe(
      "5 steps · asks you · notifies you · verified output",
    );
    const research = STARTERS.find((s) => s.name === "research-then-review")!;
    expect(starterKindSummary(research)).toBe("4 steps · asks you");
  });

  it("starterLoadDetail deep-copies — canvas edits can never mutate the catalog", () => {
    const s = STARTERS[0];
    const detail = starterLoadDetail(s);
    expect(detail.name).toBe(s.name);
    expect(detail.steps).toEqual(s.steps); // expect survives the copy
    expect(detail.steps).not.toBe(s.steps);
    detail.steps[0].name = "mutated";
    (detail.steps[0].args as Record<string, unknown>).path = "elsewhere";
    expect(s.steps[0].name).not.toBe("mutated");
    expect((s.steps[0].args as Record<string, unknown>).path).toBe(".");
  });

  it("every list_folder step points at the run workspace root ('.')", () => {
    // The engine gives an UNPINNED run a fresh empty temp dir as its tool
    // workspace (engine.py tool_workspace) and a pinned run the project's
    // folder — NEITHER contains a subfolder like "uploads", so a relative
    // subpath + on_failure halt deterministically kills the run at step 1 on
    // every default install. A starter must run out of the box: "." (the
    // workspace itself) is the only path that exists in both cases.
    for (const s of STARTERS)
      for (const st of s.steps)
        if (st.kind === "tool" && st.tool === "list_folder")
          expect(
            (st.args as Record<string, unknown> | undefined)?.path,
            `${s.name} / ${st.name}`,
          ).toBe(".");
  });

  it("no template ever references a step that can be skipped over", () => {
    // render_template resolves {{Step}} to outputs[step].summary REGARDLESS of
    // status — a step with on_failure "skip" that fails leaves its ERROR text
    // as the summary, and any later step interpolating it ships that failure
    // text as if it were the deliverable (e.g. a notify labeled "Weekly
    // digest:" carrying "expectation failed: …"). Skippable steps therefore
    // must never be referenced downstream in the catalog.
    const rx = /\{\{\s*([^{}]+?)\s*\}\}/g;
    for (const s of STARTERS) {
      const skippable = new Set(
        s.steps.filter((st) => st.on_failure === "skip").map((st) => st.name),
      );
      for (const st of s.steps) {
        const text = [st.task ?? "", st.message ?? "", JSON.stringify(st.args ?? {})].join(
          "\n",
        );
        for (const m of text.matchAll(rx)) {
          const ref = m[1].replace(/\.data$/, "");
          expect(
            skippable.has(ref),
            `${s.name} / ${st.name} references skippable {{${ref}}}`,
          ).toBe(false);
        }
      }
    }
  });

  it("StepExpect has ONE declaration — starters re-exports P6's, no local copy", () => {
    // Two byte-identical interface copies drift exactly the way the five
    // WorkflowStep copies did; runtime can't see a type, so pin the SOURCE.
    // vitest runs with cwd = dashboard/ (import.meta.url is jsdom-rewritten
    // to an http: URL here, so resolve from cwd instead).
    const src = readFileSync(
      resolve(process.cwd(), "components", "workflow", "starters.ts"),
      "utf8",
    );
    expect(src).not.toMatch(/interface\s+StepExpect/);
    expect(src).toMatch(
      /export type \{ StepExpect \} from "@\/components\/workflow\/agents"/,
    );
  });
});

/* --------------------------------------------------------- pure page helpers */

describe("isLiveRun", () => {
  it("live for every non-terminal status, via the SHARED terminal set", () => {
    for (const s of ["running", "waiting", "resuming"]) {
      expect(isLiveRun({ status: s })).toBe(true);
      expect(WORKFLOW_RUN_TERMINAL.has(s)).toBe(false);
    }
    for (const s of WORKFLOW_RUN_TERMINAL) expect(isLiveRun({ status: s })).toBe(false);
  });

  it("absent/blank status is NOT live — a malformed row must not poll forever", () => {
    expect(isLiveRun({})).toBe(false);
    expect(isLiveRun({ status: "" })).toBe(false);
  });
});

describe("runBadgeTone", () => {
  it("resuming is live-cyan; waiting/interrupted need the user (amber)", () => {
    expect(runBadgeTone("resuming")).toBe("cyan");
    expect(runBadgeTone("waiting")).toBe("amber");
    expect(runBadgeTone("interrupted")).toBe("amber");
  });

  it("everything else keeps the shared default tone (returns undefined)", () => {
    expect(runBadgeTone("completed")).toBeUndefined();
    expect(runBadgeTone("running")).toBeUndefined();
    expect(runBadgeTone("failed")).toBeUndefined();
    expect(runBadgeTone(undefined)).toBeUndefined();
  });
});

describe("stepKindHint", () => {
  it("names what a non-agent step IS; agent steps get no hint", () => {
    expect(stepKindHint({ name: "s", kind: "tool", tool: "list_folder" })).toBe(
      "tool: list_folder",
    );
    expect(stepKindHint({ name: "s", kind: "tool" })).toBe("tool");
    expect(stepKindHint({ name: "s", kind: "ask" })).toBe("asks you");
    expect(stepKindHint({ name: "s", kind: "notify" })).toBe("notify");
    expect(stepKindHint({ name: "s", kind: "agent" })).toBeNull();
    expect(stepKindHint({ name: "s" })).toBeNull();
  });
});

/* -------------------------------------------------------- StarterTemplates */

describe("StarterTemplates (via the page)", () => {
  it("no saved workflows → expanded cards, and a click loads (NOT saves) a starter", async () => {
    api.responses["/workflows"] = { workflows: [] };
    api.responses[RUNS_PATH] = { runs: [] };
    const seen: unknown[] = [];
    const onLoad = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener("ij:load-workflow", onLoad);
    try {
      render(<WorkflowsPage />);

      expect(await screen.findByText(/No saved workflows yet/)).toBeInTheDocument();
      const buttons = screen.getAllByText("Load into editor");
      expect(buttons).toHaveLength(STARTERS.length);
      expect(screen.getByText("Client intake triage")).toBeInTheDocument();
      expect(
        screen.getByText("5 steps · asks you · notifies you · verified output"),
      ).toBeInTheDocument();

      fireEvent.click(buttons[0]);
      expect(seen).toHaveLength(1);
      const detail = seen[0] as { name: string; steps: unknown[] };
      expect(detail.name).toBe("client-intake-triage");
      expect(detail.steps).toHaveLength(5);
      // Loading NEVER saves — no POST left this component.
      expect(api.posts).toHaveLength(0);
      expect(window.scrollTo).toHaveBeenCalled();
      expect(
        screen.getByText("Loaded above — press Save to keep it."),
      ).toBeInTheDocument();
    } finally {
      window.removeEventListener("ij:load-workflow", onLoad);
    }
  });

  it("auto-expanded empty state can still be collapsed by hand", async () => {
    api.responses["/workflows"] = { workflows: [] };
    api.responses[RUNS_PATH] = { runs: [] };
    render(<WorkflowsPage />);
    expect(await screen.findByText(/No saved workflows yet/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Hide"));
    expect(screen.queryByText("Load into editor")).toBeNull();
    expect(screen.getByText(/starter workflows — client intake/)).toBeInTheDocument();
  });

  it("with saved workflows the section is collapsed but reachable", async () => {
    api.responses["/workflows"] = { workflows: [{ name: "mine" }] };
    api.responses[RUNS_PATH] = { runs: [] };
    render(<WorkflowsPage />);

    const show = await screen.findByText(`Show ${STARTERS.length}`);
    expect(screen.queryByText("Load into editor")).toBeNull();
    fireEvent.click(show);
    expect(screen.getAllByText("Load into editor")).toHaveLength(STARTERS.length);
    // The empty-state claim must NOT show — the user HAS workflows.
    expect(screen.queryByText(/No saved workflows yet/)).toBeNull();
  });

  it("offline stays collapsed — 'you have nothing yet' must not be guessed", async () => {
    // /workflows unmocked → the fetch rejects with status 0.
    render(<WorkflowsPage />);
    await waitFor(() => expect(api.calls).toContain("/workflows"));
    expect(screen.queryByText("Load into editor")).toBeNull();
    // Reachable by hand, but even then without the empty-state claim.
    fireEvent.click(screen.getByText(`Show ${STARTERS.length}`));
    expect(screen.getAllByText("Load into editor")).toHaveLength(STARTERS.length);
    expect(screen.queryByText(/No saved workflows yet/)).toBeNull();
  });
});

/* ------------------------------------------------------------- RunHistory */

function run(over: Partial<WorkflowRun> = {}): WorkflowRun {
  return {
    id: "r1",
    workflow_name: "intake",
    status: "completed",
    started_at: "2026-08-12T10:00:00Z",
    steps_json: JSON.stringify([
      { name: "Scan", kind: "tool", tool: "list_folder" },
      { name: "Triage", kind: "agent", agent: "planner" },
      { name: "Check", kind: "ask", message: "ok?" },
    ]),
    outputs_json: "{}",
    session_ids_json: "[]",
    ...over,
  } as WorkflowRun;
}

/** Render the page with the Templates section collapsed (saved defs exist) so
 *  only the run-history strings are on screen for these assertions. */
function renderHistory() {
  api.responses["/workflows"] = { workflows: [{ name: "mine" }] };
  return render(<WorkflowsPage />);
}

describe("RunHistory (via the page)", () => {
  it("interrupted rows get Resume: POST contract 4's route, then reload", async () => {
    api.responses[RUNS_PATH] = { runs: [run({ status: "interrupted" })] };
    renderHistory();

    const resume = await screen.findByText("Resume");
    const before = api.calls.filter((p) => p === RUNS_PATH).length;
    fireEvent.click(resume);
    await waitFor(() =>
      expect(api.posts.map((p) => p.path)).toContain("/workflows/runs/r1/resume"),
    );
    // Success reloads the table so the new status shows.
    await waitFor(() =>
      expect(api.calls.filter((p) => p === RUNS_PATH).length).toBeGreaterThan(before),
    );
  });

  it("a 409 shows the server's honest message AND reloads the stale row", async () => {
    api.responses[RUNS_PATH] = { runs: [run({ status: "interrupted" })] };
    api.postResponses["/workflows/runs/r1/resume"] = new api.FakeApiError(
      "run r1 is not interrupted (status: completed)",
      409,
    );
    renderHistory();

    const resume = await screen.findByText("Resume");
    const before = api.calls.filter((p) => p === RUNS_PATH).length;
    fireEvent.click(resume);
    expect(
      await screen.findByText("run r1 is not interrupted (status: completed)"),
    ).toBeInTheDocument();
    // The 409 is proof the local record is stale ("interrupted" is terminal —
    // no poll ever corrects it): without a refetch the row keeps rendering an
    // active Resume button that contradicts the error message beside it.
    await waitFor(() =>
      expect(api.calls.filter((p) => p === RUNS_PATH).length).toBeGreaterThan(before),
    );
  });

  it("completed rows offer no Resume button", async () => {
    api.responses[RUNS_PATH] = { runs: [run({ status: "completed" })] };
    renderHistory();
    expect(await screen.findByText("intake")).toBeInTheDocument();
    expect(screen.queryByText("Resume")).toBeNull();
  });

  it("resuming renders live-cyan; waiting amber; completed keeps green", async () => {
    api.responses[RUNS_PATH] = {
      runs: [
        run({ id: "a", status: "resuming" }),
        run({ id: "b", status: "waiting" }),
        run({ id: "c", status: "completed" }),
      ],
    };
    renderHistory();

    const resuming = await screen.findByText("resuming");
    expect(resuming.closest("span")?.className).toContain("text-accent-soft");
    expect(screen.getByText("waiting").closest("span")?.className).toContain(
      "text-amber-300",
    );
    expect(screen.getByText("completed").closest("span")?.className).toContain(
      "text-emerald-300",
    );
  });

  it("polls while a run is live, using the shared terminal set", async () => {
    // Fake timers BEFORE render — the interval must be scheduled on the fake
    // clock or advancing it proves nothing.
    vi.useFakeTimers();
    api.responses[RUNS_PATH] = { runs: [run({ status: "running" })] };
    renderHistory();
    await act(async () => {}); // flush the initial fetch
    expect(screen.getByText("intake")).toBeInTheDocument();

    const before = api.calls.filter((p) => p === RUNS_PATH).length;
    await act(async () => {
      vi.advanceTimersByTime(5100);
    });
    await act(async () => {}); // flush the reload fetch
    expect(api.calls.filter((p) => p === RUNS_PATH).length).toBeGreaterThan(before);
  });

  it("does NOT poll when every run is terminal", async () => {
    vi.useFakeTimers();
    api.responses[RUNS_PATH] = {
      runs: [run({ id: "a", status: "completed" }), run({ id: "b", status: "interrupted" })],
    };
    renderHistory();
    await act(async () => {});
    expect(screen.getAllByText("intake")).toHaveLength(2);

    const before = api.calls.filter((p) => p === RUNS_PATH).length;
    await act(async () => {
      vi.advanceTimersByTime(12000);
    });
    await act(async () => {});
    expect(api.calls.filter((p) => p === RUNS_PATH).length).toBe(before);
  });

  it("an expanded row names what each step IS (tool / ask hints)", async () => {
    api.responses[RUNS_PATH] = { runs: [run({ status: "interrupted" })] };
    renderHistory();

    fireEvent.click(await screen.findByText("intake"));
    expect(await screen.findByText(/tool: list_folder/)).toBeInTheDocument();
    expect(screen.getByText(/asks you/)).toBeInTheDocument();
    expect(screen.getByText(/planner/)).toBeInTheDocument();
    expect(
      screen.getByText(/Resume continues from the first unfinished step/),
    ).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------- page wiring */

describe("WorkflowsPage", () => {
  it("mounts canvas, templates, builder chat and run history", async () => {
    api.responses["/workflows"] = { workflows: [] };
    api.responses[RUNS_PATH] = { runs: [] };
    render(<WorkflowsPage />);

    expect(screen.getByTestId("canvas-stub")).toBeInTheDocument();
    expect(await screen.findByText(/No saved workflows yet/)).toBeInTheDocument();
    expect(screen.getByText("Build with chat")).toBeInTheDocument();
    expect(await screen.findByText(/No workflow runs yet/)).toBeInTheDocument();
  });
});

/* --------------------------------------------- v1.222.0: saved list mounts */

describe("saved workflows list (v1.222.0)", () => {
  it("the page MOUNTS the saved list with a visible Delete per row", async () => {
    api.responses["/workflows"] = {
      workflows: [{ name: "mine", steps_json: "[]" }],
    };
    api.responses[RUNS_PATH] = { runs: [] };
    render(<WorkflowsPage />);
    expect(await screen.findByTestId("saved-workflows")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete mine" })).toBeInTheDocument();
  });
});

/* ------------------------------------------- v1.225.0: run honesty notes */

describe("run notes (v1.225.0)", () => {
  it("a run whose pinned folder was missing says so when expanded", async () => {
    api.responses["/workflows"] = { workflows: [{ name: "mine", steps_json: "[]" }] };
    api.responses[RUNS_PATH] = {
      runs: [
        run({
          status: "completed",
          notes_json: JSON.stringify([
            "project “Acme” has no folder at C:\gone any more — its steps ran in a scratch workspace, NOT in the project folder; update the folder on the project page and run again",
          ]),
        }),
      ],
    };
    render(<WorkflowsPage />);
    expect(screen.queryByTestId("run-note")).not.toBeInTheDocument();
    fireEvent.click(await screen.findByText("intake"));
    expect(await screen.findByTestId("run-note")).toHaveTextContent(/scratch workspace/);
  });

  it("an older daemon without notes_json shows nothing extra", async () => {
    api.responses["/workflows"] = { workflows: [{ name: "mine", steps_json: "[]" }] };
    api.responses[RUNS_PATH] = { runs: [run({ status: "completed" })] };
    render(<WorkflowsPage />);
    fireEvent.click(await screen.findByText("intake"));
    await waitFor(() => expect(screen.getByText("Scan")).toBeInTheDocument());
    expect(screen.queryByTestId("run-note")).not.toBeInTheDocument();
  });
});
