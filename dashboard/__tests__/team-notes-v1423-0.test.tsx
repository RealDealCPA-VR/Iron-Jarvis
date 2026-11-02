/**
 * v1.423.0 (Wave 3, Team notes Phase 2): a model team member's WORKING
 * HABITS, the dashboard half.
 *
 * Pinned here:
 *  - the decoders read a GET /team row's `notes` defensively (an older daemon
 *    sends none → null; junk reads as zero) and the member card's ONE quiet
 *    line says "Working habits: 2 · 1 suggested", leaving out a zero part,
 *    and nothing at all when it has none and none waits;
 *  - Settings › Model team links to the agent's coach card
 *    (/agents?view=team&agent=custom:<name>), lists the accepted habits, and
 *    Remove takes TWO presses before it posts /team/notes/{id}/remove; a hand
 *    edit of TEAM-NOTES.md is said;
 *  - the coach card renders a `team_note` proposal as the habit and the
 *    findings it cites IN PLAIN WORDS (never a diff), Accept / Decline post
 *    the coach's own routes, and what the daemon says about the file (kept
 *    as you edited it) stays on screen after the card leaves; an
 *    `instructions` proposal still renders its diff;
 *  - the bell makes a suggested habit a QUIET row (never a desktop ping).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const hooks = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  posts: [] as string[],
  postResults: {} as Record<string, unknown>,
  reloads: 0,
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {
      hooks.reloads += 1;
    },
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {
      hooks.reloads += 1;
    },
  }),
}));

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: true }),
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
    get: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    post: (path: string) => {
      hooks.posts.push(path);
      return Promise.resolve(path in hooks.postResults ? hooks.postResults[path] : {});
    },
    patch: () => Promise.resolve({}),
  };
});

import { GraduationCap } from "lucide-react";
import { ModelTeam } from "@/components/settings/ModelTeam";
import { AgentCoach, TEAM_NOTE_KIND, citedFindings } from "@/components/agents/AgentCoach";
import { toActivity } from "@/components/NotificationBell";
import { decodeNotes, decodeTeam, findingWords, notesWords } from "@/lib/modelTeam";
import type { AgentCoachView, CoachProposal, IJEvent } from "@/lib/types";

function member(name: string, notes?: unknown): Record<string, unknown> {
  return {
    name,
    roster_name: `custom:${name}`,
    description: "",
    provider: "ollama",
    model: "qwen3:8b",
    place: "this_pc",
    cost: "free",
    roles: ["code"],
    account: "",
    account_name: "",
    vendor: "",
    usage: null,
    available: true,
    paused: false,
    ...(notes === undefined ? {} : { notes }),
  };
}

const NOTES = {
  accepted: [
    { id: "tn1", text: "Run the module's tests before reporting a piece as done", findings: 2, accepted_at: "2026-11-01T10:00:00" },
    { id: "tn2", text: "Name every file you changed in the report", findings: 3, accepted_at: "2026-11-01T11:00:00" },
  ],
  suggested: 1,
  hand_edited: false,
};

const F1 = { id: "f1", path: "src/app.py", line: 12, what: "X is 1", change: "set it to 2", scope: "build:a" };
const F2 = { id: "f2", path: "", line: null, what: "no test covers X", change: "", scope: "build:b" };

function habit(over: Partial<CoachProposal> = {}): CoachProposal {
  return {
    id: "h1",
    agent: "custom:qwen",
    kind: TEAM_NOTE_KIND,
    target: "qwen",
    before: "",
    after: "Run the module's tests before reporting a piece as done",
    rationale: "From 2 confirmed findings across 2 sessions",
    evidence: [F1, F2],
    signature: "sig",
    status: "pending",
    created_at: "2026-11-01T09:00:00Z",
    decided_at: null,
    ...over,
  };
}

function coachView(proposals: CoachProposal[]): AgentCoachView {
  return {
    report: { agent: "custom:qwen", runs: [], clusters: [], categories: {} },
    proposals,
    last_reason: "",
  } as unknown as AgentCoachView;
}

beforeEach(() => {
  hooks.api = {};
  hooks.posts = [];
  hooks.postResults = {};
  hooks.reloads = 0;
});
afterEach(() => cleanup());

/* -------------------------------------------------------------- decoders --- */

describe("the notes decoders", () => {
  it("reads a row's notes and the one quiet line", () => {
    const n = decodeNotes(NOTES);
    expect(n?.accepted.map((x) => x.id)).toEqual(["tn1", "tn2"]);
    expect(n?.accepted[1].findings).toBe(3);
    expect(notesWords(n)).toBe("Working habits: 2 · 1 suggested");
    expect(notesWords(decodeNotes({ accepted: [], suggested: 1 }))).toBe("Working habits: 1 suggested");
    expect(notesWords(decodeNotes({ accepted: [NOTES.accepted[0]], suggested: 0 }))).toBe("Working habits: 1");
    expect(notesWords(decodeNotes({ accepted: [], suggested: 0 }))).toBe("");
  });

  it("is defensive: an older daemon sends none, junk is dropped or zero", () => {
    expect(decodeNotes(undefined)).toBeNull();
    expect(decodeNotes("x")).toBeNull();
    const n = decodeNotes({ accepted: [{ id: "", text: "x" }, { id: "a" }, 7, { id: "b", text: "kept" }], suggested: "lots" });
    expect(n).toEqual({ accepted: [{ id: "b", text: "kept", findings: 0, acceptedAt: null }], suggested: 0, handEdited: false });
    expect(decodeTeam({ members: [member("qwen")] }).members[0].notes).toBeNull();
  });

  it("says a cited finding in plain words", () => {
    expect(findingWords(F1)).toBe("src/app.py line 12: X is 1 — set it to 2");
    expect(findingWords(F2)).toBe("General: no test covers X");
    expect(findingWords({ path: "a.py" })).toBe("");
    expect(citedFindings([F1, "junk", F2])).toEqual([
      "src/app.py line 12: X is 1 — set it to 2",
      "General: no test covers X",
    ]);
  });
});

/* ------------------------------------------------------- the member card --- */

describe("Settings › Model team: working habits", () => {
  it("one quiet line, a link to the coach card, the accepted habits", () => {
    hooks.api["/team"] = { roles: [], members: [member("qwen", NOTES)] };
    render(<ModelTeam />);
    const box = screen.getByTestId("team-notes-qwen");
    expect(within(box).getByTestId("team-notes-line-qwen").textContent).toBe("Working habits: 2 · 1 suggested");
    const link = within(box).getByTestId("team-notes-link-qwen");
    expect(link.getAttribute("href")).toBe("/agents?view=team&agent=custom%3Aqwen");
    expect(link.textContent).toBe("Review the suggestions");
    expect(within(box).getByTestId("team-note-tn1").textContent).toContain("Run the module's tests");
    expect(within(box).getByTestId("team-note-tn2").textContent).toContain("Name every file");
    expect(screen.queryByTestId("team-notes-hand-qwen")).toBeNull();
  });

  it("Remove takes two presses, then posts and reads the team again", async () => {
    hooks.api["/team"] = { roles: [], members: [member("qwen", NOTES)] };
    render(<ModelTeam />);
    const row = screen.getByTestId("team-note-tn1");
    fireEvent.click(within(row).getByRole("button", { name: "Remove" }));
    expect(hooks.posts).toEqual([]);
    fireEvent.click(within(row).getByRole("button", { name: "Remove?" }));
    await waitFor(() => expect(hooks.posts).toEqual(["/team/notes/tn1/remove"]));
    await waitFor(() => expect(hooks.reloads).toBeGreaterThan(0));
  });

  it("says a hand edit is kept, and draws nothing with no habits or an older daemon", () => {
    hooks.api["/team"] = {
      roles: [],
      members: [
        member("qwen", { ...NOTES, suggested: 0, hand_edited: true }),
        member("kimi", { accepted: [], suggested: 0, hand_edited: false }),
        member("old"),
      ],
    };
    render(<ModelTeam />);
    expect(screen.getByTestId("team-notes-hand-qwen").textContent).toContain("changed by hand");
    expect(screen.getByTestId("team-notes-link-qwen").textContent).toBe("Open its card");
    expect(screen.queryByTestId("team-notes-kimi")).toBeNull();
    expect(screen.queryByTestId("team-notes-old")).toBeNull();
  });
});

/* ------------------------------------------------------- the coach card --- */

describe("the coach card renders a working habit", () => {
  const PATH = "/agents/qwen/coach";

  it("the habit and its findings in plain words, no diff", () => {
    hooks.api[PATH] = coachView([habit()]);
    render(<AgentCoach name="qwen" />);
    const card = screen.getByTestId("coach-proposal-h1");
    expect(within(card).getByTestId("coach-habit-h1").textContent).toBe(
      "Run the module's tests before reporting a piece as done",
    );
    expect(within(card).getByText("From 2 confirmed findings across 2 sessions")).toBeTruthy();
    const found = within(card).getByTestId("coach-habit-findings-h1");
    expect(found.textContent).toContain("src/app.py line 12: X is 1 — set it to 2");
    expect(found.textContent).toContain("General: no test covers X");
    expect(within(card).queryByTestId("coach-diff-h1")).toBeNull();
  });

  it("Accept posts the coach's route and keeps the daemon's sentence on screen", async () => {
    hooks.api[PATH] = coachView([habit()]);
    hooks.postResults["/coach/proposals/h1/accept"] = {
      proposal: habit({ status: "accepted" }),
      note: "TEAM-NOTES.md was changed by hand, so Iron Jarvis kept your file as it is.",
    };
    render(<AgentCoach name="qwen" />);
    fireEvent.click(screen.getByTestId("coach-accept-h1"));
    await waitFor(() => expect(hooks.posts).toEqual(["/coach/proposals/h1/accept"]));
    await waitFor(() =>
      expect(screen.getByTestId("coach-habit-note-qwen").textContent).toContain("kept your file"),
    );
    expect(hooks.reloads).toBeGreaterThan(0);
  });

  it("Decline posts the coach's route; an instructions proposal still shows its diff", async () => {
    hooks.api[PATH] = coachView([
      habit(),
      habit({ id: "i1", kind: "instructions", before: "a\nb", after: "a\nc", rationale: "r", evidence: [] }),
    ]);
    render(<AgentCoach name="qwen" />);
    fireEvent.click(screen.getByTestId("coach-decline-h1"));
    await waitFor(() => expect(hooks.posts).toEqual(["/coach/proposals/h1/decline"]));
    expect(screen.getByTestId("coach-diff-i1")).toBeTruthy();
    expect(screen.queryByTestId("coach-habit-i1")).toBeNull();
  });
});

/* -------------------------------------------------------------- the bell --- */

describe("the bell: a suggested habit is a quiet row", () => {
  const ev = (payload: Record<string, unknown>): IJEvent => ({
    id: "e1",
    type: "coach.proposal",
    session_id: null,
    ts: "2026-11-01T09:00:00Z",
    payload,
  });

  it("→ the agent on Your team, its name (never custom:), quiet", () => {
    const got = toActivity(
      ev({ id: "h1", agent: "custom:qwen", kind: "team_note", rationale: "From 2 confirmed findings across 2 sessions" }),
    );
    expect(got).toMatchObject({
      href: "/agents?view=team&agent=custom%3Aqwen",
      icon: GraduationCap,
      title: "A working habit is suggested for qwen",
      body: "From 2 confirmed findings across 2 sessions",
      quiet: true,
    });
    // An instructions proposal is unchanged: not quiet.
    const coach = toActivity(ev({ id: "p1", agent: "analyst", rationale: "Use the file tool." }));
    expect(coach?.title).toBe("The coach has a suggestion for analyst");
    expect(coach?.quiet).toBeUndefined();
  });
});
