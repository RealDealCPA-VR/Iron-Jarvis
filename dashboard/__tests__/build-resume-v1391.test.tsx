/**
 * v1.391.0 (the long-jobs plan's Phase 2): a coding session's reply that a
 * restart cut off WHILE IT WORKED is carried on by itself, and a reply a crash
 * cut off keeps its Timeline.
 *
 * On the screen (lib/buildResume, the real HarnessSession, the Timeline's own
 * readers and the bell's mapper):
 *  - the reply that carried on says so in one plain line;
 *  - the cut-off reply above it no longer says "press Retry" — the work went
 *    on below;
 *  - a cut-off reply nobody carried on (it was waiting on the user, or it
 *    could not be resumed) keeps v1.389.0's line and Retry, unchanged;
 *  - the Timeline draws a crash-cut reply from the line the DAEMON really
 *    rebuilt (fixture build-cutoff-timeline-v1391.json, written by
 *    tests/test_build_cutoff_timeline_v1391.py from a real crashed turn), and
 *    names a cut-off and a carried-on reply;
 *  - the bell says a coding session carried on after a restart.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string) => {
    const value = method === "GET" ? api.gets[path] : {};
    if (value instanceof Error) return Promise.reject(value);
    if (value === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(value ?? {});
  };
  return {
    ApiError: api.ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    flattenDetail: (d: unknown) => String(d),
    get: (p: string) => answer("GET", p),
    post: (p: string) => answer("POST", p),
    put: (p: string) => answer("PUT", p),
    patch: (p: string) => answer("PATCH", p),
    del: (p: string) => answer("DELETE", p),
  };
});

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    checking: false,
    health: { default_provider: "claude-cli", default_model: "opus", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import {
  CARRIED_CHOICE,
  CUT_CARRIED_ON,
  RESUMED_LINE,
  TIMELINE_CUT,
  TIMELINE_CUT_CARRIED,
  TIMELINE_RESUMED,
  carriedOn,
  carriedOnTurns,
  cutByStop,
  resumedFrom,
  timelineResumeNote,
} from "@/lib/buildResume";
import { decodeMessage, type BuildMessage } from "@/lib/buildSessions";
import { noTimelineReason, replyChoices, replyTimeline } from "@/lib/buildTimeline";
import { toActivity } from "@/components/NotificationBell";
import type { IJEvent } from "@/lib/types";

const NET = { timeout: 5000 };
const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

/** The line the daemon's boot rebuilt from a REALLY crashed turn. */
const REBUILT = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "build-cutoff-timeline-v1391.json"), "utf-8"),
).line as Record<string, unknown>;

const INTERRUPTED_DETAIL =
  "Iron Jarvis stopped while answering (a restart or a crash). What it had written so far is kept; press Retry to ask again.";

const QUESTION = { role: "user", content: "Add a cart page", turnId: "t1", at: "2026-10-25T10:05:00" };
const CUT = {
  role: "assistant",
  content: "I started on the cart",
  turnId: "t1",
  interrupted: true,
  error: INTERRUPTED_DETAIL,
};
const CARRY_Q = {
  role: "user",
  content: "Carry on with the reply that stopped when Iron Jarvis restarted, from where it stopped.",
  turnId: "t2",
  continuation: true,
  resumedFrom: "t1",
};
const CARRIED_REPLY = { role: "assistant", content: "The cart is done.", turnId: "t2", resumedFrom: "t1" };

function view(messages: Record<string, unknown>[]) {
  return {
    id: "s1",
    title: "Add a cart",
    updated_at: "2026-10-25T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "C:\\work\\shop",
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: { provider: "claude-cli", model: "opus" },
    messages,
    session_id: "build:s1",
    queued: [],
    turn_id: "",
  };
}

const m = (x: Record<string, unknown>) => x as unknown as BuildMessage;

beforeEach(() => {
  api.gets = {
    "/build/accounts": new api.ApiError("no such route", 404),
    "/skills": { skills: [] },
    "/build/sessions/s1/skills": { skills: [], note: "" },
  };
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 200 })));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderSession() {
  return render(
    <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
  );
}

describe("lib/buildResume: the one reading", () => {
  it("names the replies a later line carried on", () => {
    const all = [QUESTION, CUT, CARRY_Q, CARRIED_REPLY].map(m);
    expect([...carriedOnTurns(all)]).toEqual(["t1"]);
    expect(resumedFrom(m(CARRIED_REPLY))).toBe("t1");
    expect(resumedFrom(m(CUT))).toBe("");
    expect(carriedOn(m(CUT), carriedOnTurns(all))).toBe(true);
    // A reply the USER stopped has no error: never "cut by a stop".
    expect(cutByStop(m({ role: "assistant", content: "x", interrupted: true }))).toBe(false);
    expect(carriedOn(m(CUT), new Set())).toBe(false);
  });

  it("the line's key survives the decoder (the daemon's LINE_KEYS)", () => {
    expect(decodeMessage(CARRIED_REPLY)?.resumedFrom).toBe("t1");
    expect(decodeMessage(CARRY_Q)?.resumedFrom).toBe("t1");
  });

  it("the Timeline's note and its list name both", () => {
    const carried = new Set(["t1"]);
    expect(timelineResumeNote(m(CARRIED_REPLY), carried)).toBe(TIMELINE_RESUMED);
    expect(timelineResumeNote(m(CUT), carried)).toBe(TIMELINE_CUT_CARRIED);
    expect(timelineResumeNote(m(CUT), new Set())).toBe(TIMELINE_CUT);
    expect(timelineResumeNote(m({ role: "assistant", content: "fine" }), carried)).toBeNull();
    const labels = replyChoices([QUESTION, CUT, CARRY_Q, CARRIED_REPLY].map(m), null).map((c) => c.label);
    expect(labels[0]).toContain(`Add a cart page ${CARRIED_CHOICE}`);
    expect(labels[1]).not.toContain(CARRIED_CHOICE);
  });
});

describe("a crash-cut reply keeps its Timeline (the daemon's real rebuild)", () => {
  it("draws what ran, cut off — never 'times were not kept'", () => {
    expect(REBUILT.interrupted).toBe(true);
    const line = m(REBUILT);
    expect(noTimelineReason(line)).not.toBe("cut");
    const t = replyTimeline(line);
    expect(t).not.toBeNull();
    expect(t!.state).toBe("stopped");
    expect(t!.steps.map((s) => s.name)).toEqual(["write_file", "write_file"]);
    expect(t!.steps.every((s) => s.start !== null)).toBe(true);
    expect(t!.calls).toHaveLength(3);
    expect(t!.calls.filter((c) => c.state === "stopped")).toHaveLength(1);
    expect(t!.durationMs).toBe((REBUILT.timing as { ms: number }).ms);
  });

  it("control: a cut-off line with no times still reads 'cut'", () => {
    const old = { ...CUT };
    expect(noTimelineReason(m(old))).toBe("cut");
    expect(replyTimeline(m(old))).toBeNull();
  });
});

describe("the session's screen", () => {
  it("a reply carried on says so, and the cut-off one above it no longer asks for Retry", async () => {
    api.gets["/build/sessions/s1"] = view([QUESTION, CUT, CARRY_Q, CARRIED_REPLY]);
    renderSession();
    const resumed = await screen.findByTestId("harness-resumed-line", {}, NET);
    expect(resumed.textContent).toBe(RESUMED_LINE);
    const carried = screen.getByTestId("harness-reply-carried");
    expect(carried.textContent).toContain(CUT_CARRIED_ON);
    expect(document.body.textContent).not.toContain("press Retry");
    expect(screen.queryByTestId("harness-retry")).toBeNull();
    // The hidden carry-on question is not shown.
    expect(document.body.textContent).not.toContain("Carry on with the reply that stopped");
    await waitFor(() => expect(screen.getAllByTestId("harness-reply")).toHaveLength(2), NET);
  });

  it("control: a cut-off reply nobody carried on keeps v1.389.0's line and Retry", async () => {
    api.gets["/build/sessions/s1"] = view([QUESTION, CUT]);
    renderSession();
    const line = await screen.findByTestId("harness-reply-error", {}, NET);
    expect(line.textContent).toContain("press Retry");
    expect(screen.getByTestId("harness-retry")).toBeTruthy();
    expect(screen.queryByTestId("harness-resumed-line")).toBeNull();
    expect(screen.queryByTestId("harness-reply-carried")).toBeNull();
  });
});

describe("the bell", () => {
  function ev(type: string, payload: Record<string, unknown>, session_id: string | null = null): IJEvent {
    return { id: `e-${type}`, type, ts: "2026-10-25T12:00:00Z", session_id, payload } as unknown as IJEvent;
  }

  it("says a coding session carried on after a restart, and opens it", () => {
    const item = toActivity(
      ev("build.resumed", { thread_id: "bs_1", title: "Add a cart", reason: "restart", resumed_from: "t1" }, "build:bs_1"),
    )!;
    expect(item.title).toBe("A coding session carried on after Iron Jarvis restarted");
    expect(item.href).toBe("/terminals?session=bs_1");
    expect(item.body).toBe("Add a cart");
    expect(item.attention).toBeUndefined();
  });

  it("control: an account's Resume when ready keeps its own words", () => {
    const item = toActivity(ev("build.resumed", { account_name: "Work", title: "Fix the nav" }, "build:bs_1"))!;
    expect(item.title).toBe("A Build session resumed on the Work account");
  });
});
