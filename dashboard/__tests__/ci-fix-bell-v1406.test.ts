/**
 * v1.406.0 part 3 — a coding session's pull request: every step of fixing its
 * failing checks is a QUIET bell row (`build.ci_step`: no ping, no badge),
 * and only the news (`build.ci_done`: Iron Jarvis stopped fixing, or the
 * checks pass now) rings and counts. The words are the daemon's own
 * (routes/ci_watch); the row opens the coding session. The ringing cases are
 * in fixtures/one-notice-v13600.json (the daemon and the tray read them too).
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  get: vi.fn(async () => ({})),
  post: vi.fn(async () => ({})),
  put: vi.fn(async () => ({})),
  patch: vi.fn(async () => ({})),
  del: vi.fn(async () => ({})),
  API_BASE: "",
  ijToken: () => "",
  sseUrl: (p: string) => p,
}));

import { BELL_ACTIVITY_TYPES, BELL_EVENT_TYPES, toActivity } from "@/components/NotificationBell";
import { decodeMessage } from "@/lib/buildSessions";
import type { IJEvent } from "@/lib/types";

const ev = (type: string, kind: string): IJEvent =>
  ({
    id: `evt_${kind}`,
    type,
    session_id: "build:bs_ci",
    ts: "2026-10-29T14:05:00",
    payload: {
      thread_id: "bs_ci",
      kind,
      title: kind === "pushed" ? "Pushed the reviewed fix to your pull request" : "Checks failed on your pull request",
      body: "Fix the parser · Pushed the reviewed fix to ij/fix-1a2b (commit abc123).",
      round: 1,
      session_title: "Fix the parser",
    },
  }) as unknown as IJEvent;

describe("the pull-request watch in the bell", () => {
  it("a step is a quiet row that opens the coding session", () => {
    for (const kind of ["checks_failed", "fix_started", "review", "pushed", "side_branch", "asking", "stopped"]) {
      const item = toActivity(ev("build.ci_step", kind))!;
      expect(item, kind).toBeTruthy();
      expect(item.quiet, kind).toBe(true);
      expect(item.attention, kind).toBeFalsy();
      expect(item.href).toBe("/chat?view=code&session=bs_ci");
    }
    const pushed = toActivity(ev("build.ci_step", "pushed"))!;
    expect(pushed.title).toBe("Pushed the reviewed fix to your pull request");
    expect(pushed.body).toBe("Fix the parser · Pushed the reviewed fix to ij/fix-1a2b (commit abc123).");
  });

  it("the news rings and counts", () => {
    for (const kind of ["gave_up", "passing"]) {
      const item = toActivity(ev("build.ci_done", kind))!;
      expect(item.quiet, kind).toBeFalsy();
      expect(item.attention, kind).toBe(true);
    }
  });

  it("a row with no session says nothing", () => {
    const e = ev("build.ci_step", "pushed") as unknown as Record<string, unknown>;
    expect(toActivity({ ...e, session_id: null, payload: { kind: "pushed" } } as unknown as IJEvent)).toBeNull();
  });

  it("the bell reads both types, and a session's line keeps its ci record", () => {
    expect(BELL_ACTIVITY_TYPES).toEqual(expect.arrayContaining(["build.ci_step", "build.ci_done"]));
    expect(BELL_EVENT_TYPES).toEqual(expect.arrayContaining(["build.ci_step", "build.ci_done"]));
    const line = decodeMessage({ role: "assistant", content: "Pushed.", ci: { kind: "pushed", round: 1 } }) as unknown as
      Record<string, unknown>;
    expect(line.ci).toEqual({ kind: "pushed", round: 1 });
  });
});
