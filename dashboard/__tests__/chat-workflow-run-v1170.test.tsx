/**
 * Chat runs workflows v1.170.0 (P5) — the WorkflowRunChip + draft-card honesty.
 *
 * What carries weight here:
 *
 *  - the chip's POLL is the AUTHORITY: a run record that says completed/failed
 *    settles the chip even when no step event ever arrived (the WS has no
 *    replay) — without this, a reloaded thread's chip spins forever;
 *  - `waiting` and `resuming` are NOT terminal: the poll must continue through
 *    an ask-gate, or the run finishes and the chip never learns (this is the
 *    exact drift the shared WORKFLOW_RUN_TERMINAL set exists to kill);
 *  - the ask gate answers to POST /workflows/runs/{id}/answer and a STALE
 *    replay of the SAME question must not resurrect the banner — while a
 *    later, DIFFERENT question still shows;
 *  - the draft card says what a non-agent step really does: kind chip, tool
 *    name + args preview, the ask/notify message — a draft the user cannot
 *    read is a draft they cannot trust before running it;
 *  - "Run once" still posts the draft's OWN steps with an explicit
 *    project_id ("" = force-unpinned) — the never-inherit-a-saved-pin rule;
 *  - page.tsx CALL-SITE pins (the v1.163.0 lesson: a mutation deleting the
 *    real call site left every component test green): both chat lanes read
 *    `workflow_run` (contract 2), the chip renders under messages, the "+"
 *    menu runs a saved workflow NAME-ONLY (contract 1);
 *  - source hygiene: no control bytes in the touched files.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { WORKFLOW_RUN_TERMINAL, type IJEvent } from "@/lib/types";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    gets: [] as string[],
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
    api.gets.push(path);
    const r = api.responses[path];
    if (r === undefined)
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    return Promise.resolve(r);
  },
  post: (path: string, body: unknown) => {
    api.posts.push({ path, body });
    const r = api.postResponses[path];
    if (r === undefined) return Promise.resolve({});
    if (r instanceof api.FakeApiError) return Promise.reject(r);
    return Promise.resolve(r);
  },
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

const routerPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
}));

import {
  RUN_RECORD_GONE,
  runIsLive,
  toolArgsPreview,
  WorkflowDraftCard,
  WorkflowRunChip,
} from "@/components/chat/WorkflowDraftCard";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
  api.gets.length = 0;
  api.posts.length = 0;
  for (const k of Object.keys(api.responses)) delete api.responses[k];
  for (const k of Object.keys(api.postResponses)) delete api.postResponses[k];
});

function ev(
  id: string,
  type: string,
  payload: Record<string, unknown>,
): IJEvent {
  return { id, type, session_id: null, ts: "2026-08-13T00:00:00Z", payload };
}

function runRecord(over: Record<string, unknown> = {}) {
  return { id: "r1", workflow_name: "close-books", status: "running", ...over };
}

/* ------------------------------------------------------------------------- */
describe("WORKFLOW_RUN_TERMINAL — the ONE terminal set", () => {
  it("contains exactly the five terminal statuses", () => {
    for (const s of ["completed", "failed", "cancelled", "interrupted", "error"])
      expect(WORKFLOW_RUN_TERMINAL.has(s)).toBe(true);
  });

  it("does NOT treat waiting/resuming as terminal — polling must continue", () => {
    expect(WORKFLOW_RUN_TERMINAL.has("waiting")).toBe(false);
    expect(WORKFLOW_RUN_TERMINAL.has("resuming")).toBe(false);
    expect(WORKFLOW_RUN_TERMINAL.has("running")).toBe(false);
  });
});

/* ------------------------------------------------------------------------- */
describe("toolArgsPreview", () => {
  it("renders key=value pairs, JSON for non-strings", () => {
    expect(toolArgsPreview({ path: "docs", n: 3 })).toBe("path=docs · n=3");
  });

  it("is empty for absent/empty args", () => {
    expect(toolArgsPreview(undefined)).toBe("");
    expect(toolArgsPreview(null)).toBe("");
    expect(toolArgsPreview({})).toBe("");
  });

  it("clips — a glance, not an inspector", () => {
    expect(toolArgsPreview({ a: "x".repeat(500) }).length).toBeLessThanOrEqual(
      160,
    );
  });
});

/* ------------------------------------------------------------------------- */
describe("WorkflowRunChip — live narration from events", () => {
  it("shows the name, the run label, and a live spinner before any truth", () => {
    api.responses["/workflows/runs/r1"] = runRecord();
    render(<WorkflowRunChip runId="r1" name="close-books" events={[]} />);
    expect(screen.getByText("close-books")).toBeInTheDocument();
    expect(screen.getByText("workflow run")).toBeInTheDocument();
    expect(
      screen.getByText(/steps light up here as they happen/i),
    ).toBeInTheDocument();
  });

  it("folds THIS run's step events into rows and ignores other runs'", async () => {
    api.responses["/workflows/runs/r1"] = runRecord();
    const { rerender } = render(
      <WorkflowRunChip runId="r1" name="close-books" events={[]} />,
    );
    rerender(
      <WorkflowRunChip
        runId="r1"
        name="close-books"
        events={[
          ev("e2", "workflow.step_started", { run_id: "r1", step: "Reconcile" }),
          ev("e1", "workflow.step_started", { run_id: "OTHER", step: "Nope" }),
        ]}
      />,
    );
    expect(await screen.findByText("Reconcile")).toBeInTheDocument();
    expect(screen.queryByText("Nope")).not.toBeInTheDocument();
    rerender(
      <WorkflowRunChip
        runId="r1"
        name="close-books"
        events={[
          ev("e3", "workflow.step_completed", {
            run_id: "r1",
            step: "Reconcile",
            status: "completed",
            summary: "books balanced",
          }),
          ev("e2", "workflow.step_started", { run_id: "r1", step: "Reconcile" }),
        ]}
      />,
    );
    expect(await screen.findByText("books balanced")).toBeInTheDocument();
  });
});

describe("WorkflowRunChip — the poll is the authority", () => {
  it("settles a finished run from the RECORD alone (no events at all)", async () => {
    api.responses["/workflows/runs/r1"] = runRecord({
      status: "completed",
      outputs_json: JSON.stringify({
        Reconcile: { status: "completed", summary: "balanced" },
        Report: { status: "skipped" },
      }),
    });
    render(<WorkflowRunChip runId="r1" name="close-books" events={[]} />);
    expect(
      await screen.findByText("Run finished — every step completed."),
    ).toBeInTheDocument();
    expect(screen.getByText("Reconcile")).toBeInTheDocument();
    expect(screen.getByText("balanced")).toBeInTheDocument();
    expect(screen.getByText("Report")).toBeInTheDocument();
  });

  it("names a failed run honestly", async () => {
    api.responses["/workflows/runs/r1"] = runRecord({
      status: "failed",
      outputs_json: JSON.stringify({
        Reconcile: { status: "failed", summary: "ledger locked" },
      }),
    });
    render(<WorkflowRunChip runId="r1" name="close-books" events={[]} />);
    expect(
      await screen.findByText(
        "Run failed — the step marked above is where it stopped.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("ledger locked")).toBeInTheDocument();
  });

  it("keeps polling THROUGH waiting and settles when the run later finishes", async () => {
    vi.useFakeTimers();
    api.responses["/workflows/runs/r1"] = runRecord({
      status: "waiting",
      waiting_json: JSON.stringify({ question: "Post the entry?" }),
    });
    render(<WorkflowRunChip runId="r1" name="close-books" events={[]} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0); // flush the immediate poll
    });
    expect(screen.getByText("Post the entry?")).toBeInTheDocument();
    expect(screen.getByText("waiting on you")).toBeInTheDocument();
    // A mutation folding "waiting" into the terminal set would stop the poll
    // right here — the flip below would never be observed.
    api.responses["/workflows/runs/r1"] = runRecord({ status: "completed" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });
    expect(
      screen.getByText("Run finished — every step completed."),
    ).toBeInTheDocument();
  });

  it("stops polling once the record confirmed a terminal state", async () => {
    vi.useFakeTimers();
    api.responses["/workflows/runs/r1"] = runRecord({ status: "completed" });
    render(<WorkflowRunChip runId="r1" name="close-books" events={[]} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    const settled = api.gets.filter((p) => p === "/workflows/runs/r1").length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(
      api.gets.filter((p) => p === "/workflows/runs/r1").length,
    ).toBe(settled);
  });
});

describe("WorkflowRunChip — a PRUNED run record settles, never spins", () => {
  // v1.170.0 introduces run pruning (P2): a chip persisted in an old thread
  // may poll a run record that no longer exists. A 404 is not "try again".
  it("runIsLive: live while running/waiting/resuming, settled on outcome or gone", () => {
    expect(runIsLive(null)).toBe(true);
    expect(runIsLive("running")).toBe(true);
    expect(runIsLive("waiting")).toBe(true);
    expect(runIsLive("resuming")).toBe(true);
    expect(runIsLive("completed")).toBe(false);
    expect(runIsLive(RUN_RECORD_GONE)).toBe(false);
  });

  it("shows the honest pruned note and STOPS polling on a 404", async () => {
    vi.useFakeTimers();
    // No response mocked for this id — the api mock rejects with a 404.
    render(<WorkflowRunChip runId="gone1" name="old-run" events={[]} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(
      screen.getByText(/record is no longer on the daemon/i),
    ).toBeInTheDocument();
    const settled = api.gets.filter((p) => p === "/workflows/runs/gone1").length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(
      api.gets.filter((p) => p === "/workflows/runs/gone1").length,
    ).toBe(settled);
  });

  it("a 404 never overwrites a terminal outcome already learned from events", async () => {
    render(
      <WorkflowRunChip
        runId="gone2"
        name="old-run"
        events={[
          ev("c1", "workflow.completed", { run_id: "gone2", status: "failed" }),
        ]}
      />,
    );
    expect(
      await screen.findByText(
        "Run failed — the step marked above is where it stopped.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/record is no longer on the daemon/i),
    ).not.toBeInTheDocument();
  });
});

describe("WorkflowRunChip — the ask gate", () => {
  it("answers through POST /workflows/runs/{id}/answer and clears the box", async () => {
    api.responses["/workflows/runs/r1"] = runRecord({
      status: "waiting",
      waiting_json: JSON.stringify({ question: "Post the entry?" }),
    });
    render(<WorkflowRunChip runId="r1" name="close-books" events={[]} />);
    expect(await screen.findByText("Post the entry?")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Answer the workflow"), {
      target: { value: "yes, post it" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));
    await waitFor(() =>
      expect(screen.queryByText("Post the entry?")).not.toBeInTheDocument(),
    );
    expect(api.posts).toContainEqual({
      path: "/workflows/runs/r1/answer",
      body: { answer: "yes, post it" },
    });
  });

  it("a stale replay of the SAME question does not resurrect the banner — a NEW one shows", async () => {
    api.responses["/workflows/runs/r1"] = runRecord({
      status: "waiting",
      waiting_json: JSON.stringify({ question: "Post the entry?" }),
    });
    const { rerender } = render(
      <WorkflowRunChip runId="r1" name="close-books" events={[]} />,
    );
    expect(await screen.findByText("Post the entry?")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Answer the workflow"), {
      target: { value: "yes" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));
    await waitFor(() =>
      expect(screen.queryByText("Post the entry?")).not.toBeInTheDocument(),
    );
    // The SAME question replayed via a stale event: must stay gone.
    rerender(
      <WorkflowRunChip
        runId="r1"
        name="close-books"
        events={[
          ev("w1", "workflow.waiting", {
            run_id: "r1",
            question: "Post the entry?",
          }),
        ]}
      />,
    );
    expect(screen.queryByText("Post the entry?")).not.toBeInTheDocument();
    // A LATER, DIFFERENT ask still shows.
    rerender(
      <WorkflowRunChip
        runId="r1"
        name="close-books"
        events={[
          ev("w2", "workflow.waiting", {
            run_id: "r1",
            question: "Which account?",
          }),
          ev("w1", "workflow.waiting", {
            run_id: "r1",
            question: "Post the entry?",
          }),
        ]}
      />,
    );
    expect(await screen.findByText("Which account?")).toBeInTheDocument();
  });

  it("surfaces a rejected answer instead of swallowing it", async () => {
    api.responses["/workflows/runs/r1"] = runRecord({
      status: "waiting",
      waiting_json: JSON.stringify({ question: "Proceed?" }),
    });
    api.postResponses["/workflows/runs/r1/answer"] = new api.FakeApiError(
      "run is not waiting",
      409,
    );
    render(<WorkflowRunChip runId="r1" name="close-books" events={[]} />);
    expect(await screen.findByText("Proceed?")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Answer the workflow"), {
      target: { value: "go" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));
    expect(await screen.findByText("run is not waiting")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------------- */
describe("WorkflowDraftCard — non-agent steps are readable", () => {
  const draft = {
    name: "intake",
    description: "client intake",
    steps: [
      { name: "Build", agent: "builder", task: "assemble the packet" },
      {
        name: "Fetch",
        agent: "builder",
        task: "",
        kind: "tool",
        tool: "list_files",
        args: { path: "docs" },
      } as never,
      { name: "Check", agent: "builder", task: "", kind: "ask", message: "Proceed?" },
      { name: "Ping", agent: "builder", task: "", kind: "notify", message: "Packet ready" },
    ],
  };

  it("renders kind chips, the tool name, args preview, and ask/notify messages", () => {
    render(<WorkflowDraftCard draft={draft} events={[]} />);
    expect(screen.getByText("Builder")).toBeInTheDocument();
    expect(screen.getByText("assemble the packet")).toBeInTheDocument();
    expect(screen.getByText("Tool call")).toBeInTheDocument();
    expect(screen.getByText("list_files")).toBeInTheDocument();
    expect(screen.getByText("path=docs")).toBeInTheDocument();
    expect(screen.getByText("Ask you")).toBeInTheDocument();
    expect(screen.getByText("Proceed?")).toBeInTheDocument();
    expect(screen.getByText("Notify")).toBeInTheDocument();
    expect(screen.getByText("Packet ready")).toBeInTheDocument();
  });

  it("Run once posts the draft's OWN steps with an explicit empty pin", async () => {
    api.postResponses["/workflows/run"] = { id: "r9", status: "running" };
    api.responses["/workflows/runs/r9"] = { id: "r9", status: "running" };
    render(<WorkflowDraftCard draft={draft} events={[]} />);
    fireEvent.click(screen.getByRole("button", { name: /run once/i }));
    await waitFor(() => expect(api.posts.length).toBe(1));
    const { path, body } = api.posts[0];
    expect(path).toBe("/workflows/run");
    const b = body as { name: string; steps: unknown[]; project_id: string };
    expect(b.name).toBe("intake");
    expect(b.steps).toHaveLength(4);
    // "" = force-unpinned: without it the run would inherit the pin of any
    // SAVED workflow sharing this model-chosen name.
    expect(b.project_id).toBe("");
  });

  it("a draft carrying a project pin sends it", async () => {
    api.postResponses["/workflows/run"] = { id: "r9", status: "running" };
    api.responses["/workflows/runs/r9"] = { id: "r9", status: "running" };
    render(
      <WorkflowDraftCard draft={{ ...draft, project_id: "p1" }} events={[]} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /run once/i }));
    await waitFor(() => expect(api.posts.length).toBe(1));
    expect((api.posts[0].body as { project_id: string }).project_id).toBe("p1");
  });

  it("a finished run settles the card through the SAME poll authority", async () => {
    api.postResponses["/workflows/run"] = { id: "r9", status: "running" };
    api.responses["/workflows/runs/r9"] = {
      id: "r9",
      status: "completed",
      outputs_json: JSON.stringify({
        Build: { status: "completed", summary: "packet assembled" },
      }),
    };
    render(<WorkflowDraftCard draft={draft} events={[]} />);
    fireEvent.click(screen.getByRole("button", { name: /run once/i }));
    expect(
      await screen.findByText("Run finished — every step completed."),
    ).toBeInTheDocument();
    expect(screen.getByText("packet assembled")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------------- */
describe("source pins — the call sites the component tests cannot see", () => {
  const pageSrc = readFileSync(
    join(__dirname, "..", "app", "chat", "page.tsx"),
    "utf8",
  );
  const cardSrc = readFileSync(
    join(__dirname, "..", "components", "chat", "WorkflowDraftCard.tsx"),
    "utf8",
  );

  it("chat page imports the chip and renders it for m.workflowRun", () => {
    expect(pageSrc).toContain("WorkflowRunChip");
    expect(pageSrc).toContain("{m.workflowRun && (");
    // Rendered in BOTH the draft branch and the generic assistant branch.
    expect(pageSrc.match(/<WorkflowRunChip/g)?.length).toBeGreaterThanOrEqual(2);
  });

  it("the chip lives INSIDE the generic assistant branch — no forked return", () => {
    // The v1.170 review found a dedicated `if (m.workflowRun) return (...)`
    // branch that silently dropped every standard reply affordance (hover
    // actions, sources, viaProvider chip, interrupted note). The fork must
    // stay dead: the chip renders inline so those affordances coexist.
    expect(pageSrc).not.toMatch(/if \(m\.workflowRun\)/);
    // The GENERIC branch's chip is the LAST occurrence (the draft-card branch
    // renders one earlier in the file). Everything a normal reply gets must
    // appear after it, inside the same return.
    const chipAt = pageSrc.lastIndexOf("{m.workflowRun && (");
    expect(chipAt).toBeGreaterThan(-1);
    const sameReturn = pageSrc.slice(chipAt, chipAt + 7000);
    for (const affordance of [
      "TurnReceipt", // accountability
      "SourcesRow", // URLs the turn's web tools returned
      "viaProvider", // legacy honesty chip for pre-route messages
      "CopyIconButton", // v1.168 hover actions…
      "PromoteKnowledgeButton",
      "Regenerate reply",
    ]) {
      expect(sameReturn).toContain(affordance);
    }
  });

  it("a FAILED /workflows fetch never claims 'no saved workflows'", () => {
    // The empty-state copy is a positive factual claim; a dead daemon must
    // render the distinct error copy instead, and the catch must not plant [].
    const start = pageSrc.indexOf("function ensureWorkflows");
    expect(start).toBeGreaterThan(-1);
    const fn = pageSrc.slice(start, pageSrc.indexOf("async function runSavedWorkflow"));
    expect(fn).toContain('setSavedWorkflows("error")');
    expect(fn).not.toContain("setSavedWorkflows([])");
    expect(pageSrc).toContain(
      "Couldn&apos;t load workflows — reopen to retry.",
    );
    // And the flyout branches on the sentinel BEFORE the genuine-empty check.
    expect(pageSrc).toMatch(
      /savedWorkflows === "error"[\s\S]{0,400}savedWorkflows\.length === 0/,
    );
  });

  it("messaging threads cannot launch a run card the next sync would delete", () => {
    // queueSave no-ops for daemon-owned threads and refetchCommThread is
    // replace-only, so a comm-thread card row dies mid-run. The "+" entry is
    // disabled there (same guard regenerate uses)…
    expect(pageSrc).toMatch(
      /disabled=\{Boolean\(commMeta\)\}[\s\S]{0,800}Run a workflow…/,
    );
    // …and runSavedWorkflow refuses defensively rather than run unwatched.
    expect(pageSrc).toMatch(
      /async function runSavedWorkflow[\s\S]{0,900}if \(commMetaRef\.current\)[\s\S]{0,300}return;/,
    );
  });

  it("BOTH chat lanes read the contract-2 payload", () => {
    // Stream lane (coordinator, v1.170.0): useChatStream now decodes the done
    // frame's workflow_run into the TYPED workflowRun result field — the page
    // reads that first, keeping the raw-cast as one-release belt-and-braces.
    // (The original raw-cast-only read was the CRITICAL defect: the hook
    // dropped the field, so the streaming lane never delivered the payload.)
    expect(pageSrc).toMatch(
      /workflowRunFrom\(\s*streamRes\.workflowRun \?\?\s*\(streamRes as unknown as \{ workflow_run\?: unknown \}\)\.workflow_run,?\s*\)/,
    );
    // The hook side of the seam: the done-case decodes the frame field.
    const hookSrc = readFileSync(
      join(__dirname, "..", "lib", "useChatStream.ts"),
      "utf8",
    );
    expect(hookSrc).toContain("ev.workflow_run = data.workflow_run");
    expect(hookSrc).toContain("workflowRun: ev.workflow_run");
    // POST lane: off the /chat response.
    expect(pageSrc).toContain("workflowRunFrom(res.workflow_run)");
  });

  it("a payload without a run id renders NOTHING (never a chip that can't reconcile)", () => {
    // The guard lives in workflowRunFrom — pin its null return on a blank id.
    expect(pageSrc).toMatch(/if \(!runId\) return null;/);
  });

  it("the + menu runs a saved workflow NAME-ONLY (contract 1)", () => {
    expect(pageSrc).toContain("Run a workflow…");
    expect(pageSrc).toContain('post<WorkflowRun>("/workflows/run", { name })');
    expect(pageSrc).toContain("No saved workflows yet — draft one by asking,");
    expect(pageSrc).toContain("or open the editor.");
  });

  it("the draft card reads the SHARED terminal set, not a local copy", () => {
    expect(cardSrc).toContain("WORKFLOW_RUN_TERMINAL");
    expect(cardSrc).not.toMatch(/const TERMINAL = new Set/);
  });

  it("no control bytes in the touched sources", () => {
    // A literal NUL once made git classify chat/page.tsx as BINARY.
    const control = new RegExp("[\u0000-\u0008\u000B\u000C\u000E-\u001F]");
    for (const src of [pageSrc, cardSrc]) {
      expect(control.test(src)).toBe(false);
    }
  });
});
