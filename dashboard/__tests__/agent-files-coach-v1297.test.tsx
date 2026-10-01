/**
 * v1.297.0 — an agent's folder (instructions + revisions + notebook) and the
 * reflection coach (report, ask, proposals with a diff, accept/decline).
 *
 * WHAT THESE TESTS GUARD — the WIRE and the honest degradations:
 *  - `diffLines` lives in @/lib/diff now and DocPreview still re-exports the
 *    SAME function (the preview's compare view did not fork a copy);
 *  - AgentFiles renders the folder/preview from GET /agents/{name}/files,
 *    Edit → Save PUTs …/files/instructions {text, reason}, the notebook PUTs
 *    …/files/notes {text}, View GETs a revision and diffs it against the
 *    current text, Restore confirms inline then POSTs …/restore;
 *  - AgentCoach says the run summary and cluster chips, Ask POSTs and shows
 *    the daemon's reason when there is no proposal, a pending proposal
 *    renders its diff with added/removed lines, Accept / Decline POST, a
 *    409 on accept shows the sentence AND refetches, a coach.proposal event
 *    for this agent refetches;
 *  - both render NOTHING on a 404 (older daemon), even with a stale payload;
 *  - AgentDetail mounts folder then coach under the inbox for a custom agent
 *    (and neither for a builtin);
 *  - the bell maps coach.proposal → /agents, GraduationCap, rationale ≤ 140.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const hooks = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  errors: {} as Record<string, { status: number; message: string }>,
  gets: [] as string[],
  posts: [] as Array<{ path: string; body: Record<string, unknown> | undefined }>,
  puts: [] as Array<{ path: string; body: Record<string, unknown> | undefined }>,
  postResults: {} as Record<string, unknown>,
  postErrors: {} as Record<string, { status: number; message: string }>,
  events: [] as unknown[],
  reloads: 0,
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: path ? (hooks.errors[path] ?? null) : null,
    loading: false,
    reload: () => {
      hooks.reloads += 1;
    },
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: path ? (hooks.errors[path] ?? null) : null,
    loading: false,
    reload: () => {
      hooks.reloads += 1;
    },
  }),
}));

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: hooks.events, connected: true }),
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
    API_BASE: "",
    ijToken: () => "",
    get: (path: string) => {
      hooks.gets.push(path);
      if (path in hooks.errors) {
        const e = hooks.errors[path];
        return Promise.reject(new ApiError(e.message, e.status));
      }
      return Promise.resolve(hooks.api[path] ?? {});
    },
    put: (path: string, body?: Record<string, unknown>) => {
      hooks.puts.push({ path, body });
      return Promise.resolve({});
    },
    del: () => Promise.resolve({}),
    post: (path: string, body?: Record<string, unknown>) => {
      hooks.posts.push({ path, body });
      if (path in hooks.postErrors) {
        const e = hooks.postErrors[path];
        return Promise.reject(new ApiError(e.message, e.status));
      }
      return Promise.resolve(path in hooks.postResults ? hooks.postResults[path] : {});
    },
    patch: () => Promise.resolve({}),
  };
});

vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (prev: string, chunk: string) => prev + chunk,
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "whileHover"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  return {
    // v1.250.0 mock contract: every framer-motion mock exports `m`.
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: unknown }) =>
      createElement(Fragment, null, children as never),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => tagFor(String(tag)),
    }),
  };
});

import { GraduationCap } from "lucide-react";
import { diffLines, diffTexts, textLines } from "@/lib/diff";
import { diffLines as diffLinesFromPreview } from "@/components/chat/DocPreview";
import { AgentFiles, bytesLabel } from "@/components/agents/AgentFiles";
import { AgentCoach, clusterTitle, coachSummary, topClusters } from "@/components/agents/AgentCoach";
import { AgentsModal } from "@/components/agents/AgentsModal";
import type { RosterEntry } from "@/components/agents/RosterStrip";
import { toActivity } from "@/components/NotificationBell";
import type { AgentCoachView, AgentFilesView, CoachProposal, CoachRun, IJEvent } from "@/lib/types";

const FILES_PATH = "/agents/analyst/files";
const COACH_PATH = "/agents/analyst/coach";

const INSTRUCTIONS = ["# Analyst", "Read first.", "Cite sources.", "Keep it short.", "Ask when unsure.", "Never guess.", "Line seven.", "Line eight."].join("\n");

function filesView(over: Partial<AgentFilesView> = {}): AgentFilesView {
  return {
    name: "analyst",
    instructions: INSTRUCTIONS,
    notes: "Q3 ledger lives in /data/q3",
    revisions: [{ id: "r1", at: "2026-10-01T08:00:00Z", reason: "tightened the tone", bytes: 120 }],
    folder: "C:\\ij\\agents\\analyst",
    ...over,
  };
}

function run(over: Partial<CoachRun>): CoachRun {
  return {
    session_id: "s1",
    outcome: "completed",
    score: 0.9,
    tools_failed: {},
    denials: 0,
    unanswered_asks: 0,
    steps: 5,
    max_steps: 20,
    down_feedback: 0,
    duration_s: 12,
    summary: "",
    ...over,
  };
}

function proposal(over: Partial<CoachProposal> = {}): CoachProposal {
  return {
    id: "p1",
    agent: "analyst",
    kind: "instructions",
    target: "INSTRUCTIONS.md",
    before: "a\nb\nc",
    after: "a\nx\nc",
    rationale: "It keeps calling the shell tool for things the file tool does.",
    evidence: [],
    signature: "sig",
    status: "pending",
    created_at: "2026-10-01T09:00:00Z",
    decided_at: null,
    ...over,
  };
}

function coachView(over: Partial<AgentCoachView> = {}): AgentCoachView {
  return {
    report: {
      agent: "analyst",
      runs: [run({ session_id: "s1" }), run({ session_id: "s2" }), run({ session_id: "s3", outcome: "needs_you" }), run({ session_id: "s4", outcome: "failed" })],
      clusters: [
        { category: "tool-misuse", count: 3, weight: 0.8, evidence: [] },
        { category: "denials", count: 1, weight: 0.2, evidence: [] },
      ],
    },
    proposals: [proposal()],
    last_reason: "",
    ...over,
  };
}

beforeEach(() => {
  hooks.api = {};
  hooks.errors = {};
  hooks.gets = [];
  hooks.posts = [];
  hooks.puts = [];
  hooks.postResults = {};
  hooks.postErrors = {};
  hooks.events = [];
  hooks.reloads = 0;
});

afterEach(() => cleanup());

/* ------------------------------------------------------------- lib/diff --- */

describe("lib/diff — diffLines moved out of DocPreview", () => {
  it("DocPreview re-exports the very same function (no fork)", () => {
    expect(diffLinesFromPreview).toBe(diffLines);
  });

  it("marks a replaced line as removed then added, keeps context", () => {
    expect(diffLines(["a", "b", "c"], ["a", "x", "c"])).toEqual([
      { kind: "same", text: "a" },
      { kind: "removed", text: "b" },
      { kind: "added", text: "x" },
      { kind: "same", text: "c" },
    ]);
    expect(diffLines([], ["a"])).toEqual([{ kind: "added", text: "a" }]);
    expect(diffLines(["a"], [])).toEqual([{ kind: "removed", text: "a" }]);
  });

  it("textLines is CRLF-tolerant and diffTexts composes it", () => {
    expect(textLines("a\r\nb")).toEqual(["a", "b"]);
    expect(textLines(null)).toEqual([""]);
    expect(diffTexts("a\r\nb", "a\nb")).toEqual([
      { kind: "same", text: "a" },
      { kind: "same", text: "b" },
    ]);
  });

  it("bytesLabel", () => {
    expect(bytesLabel(120)).toBe("120 B");
    expect(bytesLabel(2048)).toBe("2.0 KB");
    expect(bytesLabel(20480)).toBe("20 KB");
  });
});

/* ----------------------------------------------------------- AgentFiles --- */

describe("AgentFiles — the folder", () => {
  it("renders the folder path and a 6-line preview with Show all", () => {
    hooks.api[FILES_PATH] = filesView();
    render(<AgentFiles name="analyst" />);
    const panel = screen.getByTestId("files-analyst");
    expect(within(panel).getByTitle("C:\\ij\\agents\\analyst")).toBeInTheDocument();
    const pre = within(panel).getByTestId("files-preview-analyst");
    expect(pre.textContent).toContain("Never guess.");
    expect(pre.textContent).not.toContain("Line seven.");
    fireEvent.click(within(panel).getByRole("button", { name: /Show all/ }));
    expect(within(panel).getByTestId("files-preview-analyst").textContent).toContain("Line eight.");
    expect(within(panel).getByTestId("files-notes-analyst")).toHaveValue("Q3 ledger lives in /data/q3");
    expect(within(panel).getByText(/trimmed to 4,000 characters/)).toBeInTheDocument();
  });

  it("renders NOTHING on a 404 — even with a stale payload in hand", () => {
    // Both set on purpose: the 404 must win over cached data, otherwise an
    // older daemon shows a folder it cannot save to.
    hooks.api[FILES_PATH] = filesView();
    hooks.errors[FILES_PATH] = { status: 404, message: "Not Found" };
    const { container } = render(<AgentFiles name="analyst" />);
    expect(screen.queryByTestId("files-analyst")).toBeNull();
    expect(container.innerHTML).toBe("");
  });

  it("Edit → Save PUTs …/files/instructions with the text and the reason", async () => {
    hooks.api[FILES_PATH] = filesView();
    render(<AgentFiles name="analyst" />);
    fireEvent.click(screen.getByTestId("files-edit-analyst"));
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "# Analyst\nBe brief." } });
    fireEvent.change(screen.getByPlaceholderText("why (optional)"), { target: { value: "shorter" } });
    fireEvent.click(screen.getByTestId("files-save-analyst"));
    await waitFor(() => expect(screen.getByText(/Saved — the old text is kept in History/)).toBeInTheDocument());
    expect(hooks.puts).toEqual([
      { path: `${FILES_PATH}/instructions`, body: { text: "# Analyst\nBe brief.", reason: "shorter" } },
    ]);
    expect(hooks.reloads).toBe(1);
    // Back in read mode.
    expect(screen.getByTestId("files-edit-analyst")).toBeInTheDocument();
  });

  it("an empty reason is NOT sent (the daemon keeps its own default)", async () => {
    hooks.api[FILES_PATH] = filesView();
    render(<AgentFiles name="analyst" />);
    fireEvent.click(screen.getByTestId("files-edit-analyst"));
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "new" } });
    fireEvent.click(screen.getByTestId("files-save-analyst"));
    await waitFor(() => expect(hooks.puts.length).toBe(1));
    expect(hooks.puts[0].body).toEqual({ text: "new" });
    expect("reason" in (hooks.puts[0].body ?? {})).toBe(false);
  });

  it("Notebook Save PUTs …/files/notes {text}", async () => {
    hooks.api[FILES_PATH] = filesView();
    render(<AgentFiles name="analyst" />);
    const save = screen.getByTestId("files-notes-save-analyst");
    expect(save).toBeDisabled(); // nothing changed yet
    fireEvent.change(screen.getByTestId("files-notes-analyst"), { target: { value: "Q3 ledger moved to /data/2026" } });
    fireEvent.click(save);
    await waitFor(() => expect(screen.getByText("Notebook saved.")).toBeInTheDocument());
    expect(hooks.puts).toEqual([{ path: `${FILES_PATH}/notes`, body: { text: "Q3 ledger moved to /data/2026" } }]);
  });

  it("History: View GETs the revision and diffs it against now; Restore confirms then POSTs", async () => {
    hooks.api[FILES_PATH] = filesView({ instructions: "a\nx\nc" });
    hooks.api[`${FILES_PATH}/revisions/r1`] = { id: "r1", at: "2026-10-01T08:00:00Z", reason: "tightened the tone", text: "a\nb\nc" };
    render(<AgentFiles name="analyst" />);
    const row = screen.getByTestId("files-revision-r1");
    expect(row.textContent).toContain("tightened the tone");
    expect(row.textContent).toContain("120 B");
    fireEvent.click(within(row).getByRole("button", { name: "View" }));
    await waitFor(() => expect(screen.getByTestId("files-revision-text-r1")).toBeInTheDocument());
    expect(hooks.gets).toContain(`${FILES_PATH}/revisions/r1`);
    expect(screen.getByTestId("files-revision-text-r1").textContent).toBe("a\nb\nc");
    const diff = screen.getByTestId("files-revision-diff-r1");
    const removed = diff.querySelectorAll('[data-kind="removed"]');
    const added = diff.querySelectorAll('[data-kind="added"]');
    expect(Array.from(removed).map((n) => n.textContent)).toEqual(["− b"]);
    expect(Array.from(added).map((n) => n.textContent)).toEqual(["+ x"]);

    // Restore: the first click only arms.
    const restore = screen.getByTestId("files-restore-r1");
    fireEvent.click(restore);
    expect(hooks.posts).toEqual([]);
    expect(restore.textContent).toBe("Restore this one?");
    fireEvent.click(restore);
    await waitFor(() => expect(hooks.reloads).toBe(1));
    expect(hooks.posts).toEqual([{ path: `${FILES_PATH}/revisions/r1/restore`, body: undefined }]);
  });

  it("Open: offered, and hidden once the daemon 404s the open route", async () => {
    hooks.api[FILES_PATH] = filesView();
    hooks.postErrors[`${FILES_PATH}/open`] = { status: 404, message: "Not Found" };
    render(<AgentFiles name="analyst" />);
    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Open" })).toBeNull());
    expect(hooks.posts).toEqual([{ path: `${FILES_PATH}/open`, body: undefined }]);
  });
});

/* ----------------------------------------------------------- AgentCoach --- */

describe("AgentCoach — helpers", () => {
  it("coachSummary counts outcomes in order of first appearance", () => {
    expect(coachSummary(coachView().report.runs)).toBe("Last 4 runs: 2 completed · 1 needs you · 1 failed");
    expect(coachSummary([])).toBe("No runs yet");
    expect(coachSummary([run({})])).toBe("Last 1 run: 1 completed");
  });

  it("topClusters sorts by weight and drops empty ones; clusterTitle has definitions", () => {
    const got = topClusters([
      { category: "denials", count: 1, weight: 0.2, evidence: [] },
      { category: "tool-misuse", count: 3, weight: 0.8, evidence: [] },
      { category: "slow", count: 0, weight: 0.9, evidence: [] },
    ]);
    expect(got.map((c) => c.category)).toEqual(["tool-misuse", "denials"]);
    expect(clusterTitle("tool-misuse")).toMatch(/tool was called wrongly/);
    expect(clusterTitle("tool_misuse")).toMatch(/tool was called wrongly/);
    expect(clusterTitle("something-new")).toBe("something-new");
    // The DAEMON's taxonomy wins over the local table (its eight categories
    // are the ones that arrive: verifier-miss, avoidable-rework, …).
    const defs = { "tool-misuse": "a call was denied, or a tool failed on a missing required argument", "stale-context": "the run re-read the same file three or more times" };
    expect(clusterTitle("tool-misuse", defs)).toBe(defs["tool-misuse"]);
    expect(clusterTitle("stale-context", defs)).toBe(defs["stale-context"]);
    expect(clusterTitle("stale-context", null)).toBe("stale-context");
  });
});

describe("AgentCoach — the panel", () => {
  it("says the summary and the cluster chips with their definitions", () => {
    hooks.api[COACH_PATH] = coachView();
    render(<AgentCoach name="analyst" />);
    expect(screen.getByTestId("coach-summary-analyst").textContent).toBe(
      "Last 4 runs: 2 completed · 1 needs you · 1 failed",
    );
    const chip = screen.getByTestId("coach-cluster-tool-misuse");
    expect(chip.textContent).toBe("tool-misuse ×3");
    expect(chip.getAttribute("title")).toMatch(/tool was called wrongly/);
  });

  it("a chip's title is the daemon's definition when the report carries its taxonomy", () => {
    const v = coachView();
    v.report.categories = { "tool-misuse": "a call was denied, or a tool failed on a missing required argument" };
    hooks.api[COACH_PATH] = v;
    render(<AgentCoach name="analyst" />);
    expect(screen.getByTestId("coach-cluster-tool-misuse").getAttribute("title")).toBe(
      "a call was denied, or a tool failed on a missing required argument",
    );
  });

  it("renders NOTHING on a 404 — even with a stale payload in hand", () => {
    hooks.api[COACH_PATH] = coachView();
    hooks.errors[COACH_PATH] = { status: 404, message: "Not Found" };
    const { container } = render(<AgentCoach name="analyst" />);
    expect(screen.queryByTestId("coach-analyst")).toBeNull();
    expect(container.innerHTML).toBe("");
  });

  it("Ask the coach POSTs and shows the reason when there is no proposal", async () => {
    hooks.api[COACH_PATH] = coachView({ proposals: [] });
    hooks.postResults[COACH_PATH] = { proposal: null, reason: "Nothing recurring in the last 10 runs." };
    render(<AgentCoach name="analyst" />);
    fireEvent.click(screen.getByTestId("coach-ask-analyst"));
    await waitFor(() =>
      expect(screen.getByTestId("coach-reason-analyst").textContent).toBe("Nothing recurring in the last 10 runs."),
    );
    expect(hooks.posts).toEqual([{ path: COACH_PATH, body: undefined }]);
    expect(hooks.reloads).toBe(0);
  });

  it("Ask the coach refetches when a proposal comes back", async () => {
    hooks.api[COACH_PATH] = coachView({ proposals: [] });
    hooks.postResults[COACH_PATH] = { proposal: proposal(), reason: "" };
    render(<AgentCoach name="analyst" />);
    fireEvent.click(screen.getByTestId("coach-ask-analyst"));
    await waitFor(() => expect(hooks.reloads).toBe(1));
    expect(screen.queryByTestId("coach-reason-analyst")).toBeNull();
  });

  it("a pending proposal renders its rationale and a diff with removed/added lines; a decided one does not", () => {
    hooks.api[COACH_PATH] = coachView({
      proposals: [proposal(), proposal({ id: "p2", status: "declined" }), proposal({ id: "p3", status: "stale" })],
    });
    render(<AgentCoach name="analyst" />);
    const card = screen.getByTestId("coach-proposal-p1");
    expect(card.textContent).toContain("It keeps calling the shell tool");
    const diff = within(card).getByTestId("coach-diff-p1");
    const kinds = Array.from(diff.children).map((n) => [n.getAttribute("data-kind"), n.textContent]);
    expect(kinds).toEqual([
      ["same", "  a"],
      ["removed", "− b"],
      ["added", "+ x"],
      ["same", "  c"],
    ]);
    expect(diff.querySelector('[data-kind="added"]')?.className).toMatch(/emerald/);
    expect(diff.querySelector('[data-kind="removed"]')?.className).toMatch(/rose/);
    expect(screen.queryByTestId("coach-proposal-p2")).toBeNull();
    expect(screen.queryByTestId("coach-proposal-p3")).toBeNull();
  });

  it("Accept / Decline POST and refetch", async () => {
    hooks.api[COACH_PATH] = coachView();
    render(<AgentCoach name="analyst" />);
    fireEvent.click(screen.getByTestId("coach-accept-p1"));
    await waitFor(() => expect(hooks.reloads).toBe(1));
    expect(hooks.posts).toEqual([{ path: "/coach/proposals/p1/accept", body: undefined }]);
    fireEvent.click(screen.getByTestId("coach-decline-p1"));
    await waitFor(() => expect(hooks.reloads).toBe(2));
    expect(hooks.posts[1]).toEqual({ path: "/coach/proposals/p1/decline", body: undefined });
  });

  it("a 409 on Accept shows the daemon's sentence AND refetches", async () => {
    hooks.api[COACH_PATH] = coachView();
    hooks.postErrors["/coach/proposals/p1/accept"] = {
      status: 409,
      message: "The instructions changed since this was written.",
    };
    render(<AgentCoach name="analyst" />);
    fireEvent.click(screen.getByTestId("coach-accept-p1"));
    await waitFor(() =>
      expect(screen.getByText("The instructions changed since this was written.")).toBeInTheDocument(),
    );
    expect(hooks.reloads).toBe(1);
  });

  it("a plain failure on Accept shows the error and does NOT refetch", async () => {
    hooks.api[COACH_PATH] = coachView();
    hooks.postErrors["/coach/proposals/p1/accept"] = { status: 500, message: "disk full" };
    render(<AgentCoach name="analyst" />);
    fireEvent.click(screen.getByTestId("coach-accept-p1"));
    await waitFor(() => expect(screen.getByText("disk full")).toBeInTheDocument());
    expect(hooks.reloads).toBe(0);
  });

  it("refetches on a coach.proposal event for THIS agent only", () => {
    hooks.api[COACH_PATH] = coachView();
    hooks.events = [{ id: "e1", type: "coach.proposal", session_id: null, ts: "", payload: { agent: "other" } }];
    const { unmount } = render(<AgentCoach name="analyst" />);
    expect(hooks.reloads).toBe(0);
    unmount();
    hooks.events = [{ id: "e2", type: "coach.proposal", session_id: null, ts: "", payload: { agent: "analyst" } }];
    render(<AgentCoach name="analyst" />);
    expect(hooks.reloads).toBe(1);
  });
});

/* -------------------------------------------------------- AgentsModal --- */

const ROSTER: RosterEntry[] = [
  { name: "builder", kind: "builtin", description: "doer", delegable: true, healthy: true, stats: null },
  { name: "custom:analyst", kind: "dynamic", description: "numbers", delegable: true, healthy: true, stats: null },
];

function renderModal(selected: { kind: "builtin" | "dynamic"; name: string }) {
  return render(
    <AgentsModal
      roster={ROSTER}
      dynamic={[]}
      remotes={[]}
      models={[]}
      selected={selected}
      onSelect={() => {}}
      onAgentsChanged={() => {}}
      onRemotesChanged={() => {}}
      onClose={() => {}}
    />,
  );
}

describe("AgentDetail mounts the folder then the coach for a custom agent", () => {
  it("custom agent: inbox, then folder, then coach — in that order", () => {
    // The inbox keys by the ROSTER name, URL-encoded ("custom%3Aanalyst").
    hooks.api["/agents/custom%3Aanalyst/inbox"] = {
      assignee: "custom:analyst",
      inbox: { queued: [], claimed: [], running: [], blocked: [], recent: [] },
      health: null,
    };
    hooks.api[FILES_PATH] = filesView();
    hooks.api[COACH_PATH] = coachView();
    renderModal({ kind: "dynamic", name: "analyst" });
    const detail = screen.getByTestId("agent-detail-analyst");
    const inbox = within(detail).getByTestId("agent-inbox-custom:analyst");
    const files = within(detail).getByTestId("files-analyst");
    const coach = within(detail).getByTestId("coach-analyst");
    // DOM order: inbox before folder before coach.
    expect(inbox.compareDocumentPosition(files) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(files.compareDocumentPosition(coach) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("builtin agent: neither the folder nor the coach", () => {
    hooks.api["/agents/builder/files"] = filesView({ name: "builder" });
    hooks.api["/agents/builder/coach"] = coachView();
    renderModal({ kind: "builtin", name: "builder" });
    expect(screen.getByTestId("agent-detail-builder")).toBeInTheDocument();
    expect(screen.queryByTestId("files-builder")).toBeNull();
    expect(screen.queryByTestId("coach-builder")).toBeNull();
  });
});

/* ------------------------------------------------------------- the bell --- */

describe("toActivity maps coach.proposal", () => {
  const ev = (payload: Record<string, unknown>): IJEvent => ({
    id: "e1",
    type: "coach.proposal",
    session_id: null,
    ts: "2026-10-01T09:00:00Z",
    payload,
  });

  it("→ /agents, GraduationCap, the agent in the title, the rationale as body", () => {
    expect(
      toActivity(ev({ id: "p1", agent: "analyst", categories: ["tool-misuse"], rationale: "Use the file tool." })),
    ).toMatchObject({
      href: "/agents",
      icon: GraduationCap,
      title: "The coach has a suggestion for analyst",
      body: "Use the file tool.",
    });
  });

  it("cuts the rationale at 140 characters", () => {
    const long = "x".repeat(200);
    const got = toActivity(ev({ id: "p1", agent: "analyst", rationale: long }));
    expect(got?.body.length).toBe(140);
    expect(got?.body.endsWith("…")).toBe(true);
    expect(toActivity(ev({ id: "p1", agent: "analyst", rationale: "y".repeat(140) }))?.body).toBe("y".repeat(140));
  });
});
