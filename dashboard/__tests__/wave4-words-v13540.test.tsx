/**
 * v1.354.0 integrate, seam (a): ONE set of words for one cause, wherever the
 * user meets it — read from what the REAL daemon said
 * (`__tests__/fixtures/wave4-seams-v13540.json`, written by
 * `tests/test_integrate_wave4_v13540.py`).
 *
 * - A refused key: the chat stream's error frame decodes to a StreamError
 *   carrying `title` / `fix` / `technical`; the chat line is `title. fix`
 *   (lib/failureLine); a failed run's summary — the run page prints it, the
 *   bell row's body is it — ends with that same sentence.
 * - A restart: the reconcile's summary IS the run page's restart sentence,
 *   and the bell's interrupted rows and desktop ping say the same words.
 * - Iron Jarvis not answering: the banner, a panel's "couldn't load" line,
 *   the in-page hint, a failed request and the chat's offline frame all say
 *   lib/offlineWords' one sentence.
 *
 * The chat page itself renders the line in `wave4-seams-v13540.test.tsx`.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { toActivity } from "@/components/NotificationBell";
import { decodeSSE, StreamError } from "@/lib/useChatStream";
import { failureSentence, turnErrorTechnical, turnErrorText } from "@/lib/failureLine";
import { askedText, RESTART_SUMMARY, RESTART_WORDS, runTitle, shownSummary } from "@/components/sessions/runPageWords";
import { CONTINUE_AFTER_RESTART } from "@/components/InterruptedJobs";
import { OFFLINE_TITLE, OFFLINE_WORDS } from "@/lib/offlineWords";
import { couldntLoad, NOT_RESPONDING } from "@/lib/loadFailed";
import { LoadFailedLine } from "@/components/LoadFailedLine";
import type { IJEvent } from "@/lib/types";

const ROOT = process.cwd();
const src = (rel: string) => readFileSync(join(ROOT, rel), "utf8").replace(/\r\n/g, "\n");
const SEAMS = JSON.parse(src("__tests__/fixtures/wave4-seams-v13540.json")) as {
  refused_key: {
    detail: string;
    title: string;
    fix: string;
    technical: string;
    sentence: string;
    summary: string;
    phone_line: string;
  };
  restart: { summary: string; goal_reason: string; asked: string; continuation_task: string };
};
const K = SEAMS.refused_key;

function ev(type: string, payload: Record<string, unknown>, session_id = "session_1"): IJEvent {
  return { id: `e-${type}`, type, ts: "2026-10-19T07:00:00Z", session_id, payload } as unknown as IJEvent;
}

describe("a refused key: one sentence in the chat line, on the run, in the bell", () => {
  it("the stream's error frame decodes the daemon's plain words beside the unchanged detail", () => {
    const frame = decodeSSE(
      "error",
      JSON.stringify({ detail: K.detail, title: K.title, fix: K.fix, technical: K.technical }),
    );
    expect(frame).toEqual({ type: "error", detail: K.detail, title: K.title, fix: K.fix, technical: K.technical });
  });

  it("the chat line is `title. fix` — the sentence the run's summary ends with; the technical text is hover only", () => {
    const e = new StreamError(K.detail, 502, false, false, "", { title: K.title, fix: K.fix, technical: K.technical });
    expect(turnErrorText(e)).toBe(K.sentence);
    expect(K.summary).toBe(`The run failed: ${K.sentence}`);
    expect(turnErrorText(e)).not.toContain("No auth credentials");
    expect(turnErrorTechnical(e)).toBe(K.technical);
    // The join is the daemon's own (failure_words.plain_reason).
    expect(failureSentence(K.title, K.fix)).toBe(K.sentence);
    const py = src("../src/iron_jarvis/providers/failure_words.py");
    expect(py).toContain('return f"{words[0]}. {words[1]}"');
  });

  it("a failure without the plain words keeps its own message, and no hover", () => {
    const e = new StreamError("Office Spark isn't connected right now.", 503, false, false);
    expect(turnErrorText(e)).toBe("Office Spark isn't connected right now.");
    expect(turnErrorTechnical(e)).toBe("");
  });

  it("the bell row for that run carries the same summary (a schedule's run and a mission)", () => {
    const sched = toActivity(
      ev("session.completed", { status: "failed", ok: false, summary: K.summary, origin: "schedule:morning brief", outcome: null }),
    )!;
    expect(sched.title).toBe("Your schedule morning brief could not finish");
    expect(sched.body).toBe(K.summary);
    expect(sched.body.endsWith(K.sentence)).toBe(true);
    const mission = toActivity(
      ev("session.completed", { status: "failed", ok: false, summary: K.summary, origin: "job:mission", outcome: null }),
    )!;
    expect(mission.body).toBe(K.summary);
    // The phone line (the daemon's notifier) carries the very same summary.
    expect(K.phone_line.endsWith(K.summary)).toBe(true);
  });
});

describe("a restart: one sentence on the run, in the chat line, in the bell", () => {
  it("the reconcile's summary IS the run page's restart sentence (old stored rows still read the same)", () => {
    expect(RESTART_SUMMARY).toBe(SEAMS.restart.summary);
    expect(RESTART_SUMMARY).toBe(`${RESTART_WORDS}.`);
    expect(shownSummary(SEAMS.restart.summary, false)).toBe(RESTART_SUMMARY);
    expect(shownSummary("interrupted by a daemon restart", false)).toBe(RESTART_SUMMARY);
    expect(SEAMS.restart.goal_reason).toBe(RESTART_WORDS.charAt(0).toLowerCase() + RESTART_WORDS.slice(1));
  });

  it("a restart Continue's run is titled by what the cut-off run was asked, not by the app's message", () => {
    // The REAL continue door's task (Python repr of the earlier task).
    const task = SEAMS.restart.continuation_task;
    expect(task.startsWith(CONTINUE_AFTER_RESTART)).toBe(true);
    expect(askedText(task)).toBe(SEAMS.restart.asked);
    expect(runTitle(task).title).toBe("Summarise the lease");
    // An ordinary follow-up keeps its own words as the title.
    expect(runTitle(task.replace(CONTINUE_AFTER_RESTART, "make it shorter")).title).toBe("make it shorter");
  });

  it("the bell's interrupted rows and its desktop ping say the same words", () => {
    const words = RESTART_WORDS.charAt(0).toLowerCase() + RESTART_WORDS.slice(1);
    expect(src("components/InterruptedJobs.tsx")).toContain(`A task ${words}`);
    expect(src("components/NotificationBell.tsx")).toContain(`"s"} ${words}\``);
    expect(src("components/NotificationBell.tsx")).not.toContain("stopped by a restart");
  });
});

describe("Iron Jarvis not answering: one sentence on every surface", () => {
  it("a panel's line, the in-page hint and a failed request all say the banner's words", () => {
    expect(NOT_RESPONDING).toBe(OFFLINE_TITLE);
    expect(couldntLoad("The board", { status: 0 })).toBe(`The board couldn't load. ${OFFLINE_TITLE}`);
    render(<LoadFailedLine thing="The timeline" error={{ status: 0, message: "" }} testId="line" />);
    expect(screen.getByTestId("line").textContent).toBe(`The timeline couldn't load. ${OFFLINE_TITLE}`);
    // The chat stream's offline frame and every status-0 ApiError say it.
    expect(src("lib/useChatStream.ts")).toContain('detail: OFFLINE_WORDS, status: 0, offline: true');
    expect(src("lib/api.ts")).toMatch(/OFFLINE_WORDS/);
    expect(OFFLINE_TITLE).toBe(`${OFFLINE_WORDS}.`);
  });

  it("no surface the integrator reworded still says 'daemon' to the user", () => {
    for (const [file, gone] of [
      ["components/TimeTravelFeed.tsx", "Daemon offline — cannot load the timeline."],
      ["components/terminal/PaneChat.tsx", "Daemon offline — this pane's conversation could not be loaded."],
      ["app/workflows/page.tsx", "Daemon offline. Run history is unavailable."],
      ["app/workflows/page.tsx", "The daemon looks offline. Start it and try again."],
      ["components/agents/world/WorldBoard.tsx", "The daemon looks offline."],
      ["components/project/KnowledgePanel.tsx", "The daemon looks offline."],
      ["components/agents/world/TeamEditor.tsx", "The daemon looks offline"],
      ["components/chat/WorkflowDraftCard.tsx", "no longer on the daemon"],
      ["components/terminal/FilesPanel.tsx", "needs the daemon running"],
      ["components/settings/DaemonTokenCard.tsx", "Daemon access token"],
      ["app/self-dev/page.tsx", "Restart daemon"],
      ["app/help/page.tsx", "“Daemon offline” in the dashboard"],
    ] as const) {
      expect(src(file), `${file} still says ${gone}`).not.toContain(gone);
    }
  });
});
