/**
 * v1.358.0 lane 8A — outcomes say the truth (the dashboard half).
 *
 * `__tests__/fixtures/outcomes-v13580.json` holds what the REAL daemon wrote
 * (tests/test_outcomes_say_the_truth_v13580.py drives real fires, a real
 * mission and reads the harness's own status list, and fails when the daemon
 * stops sending it). This file feeds that same fixture through the REAL
 * Schedules page, Run now, a project's Schedules list, the bell's mapper, the
 * objectives rail (MissionComposer) and the mission headline, so a new user
 * reads ONE verdict for each run:
 *
 *  - (1) the Schedules row LEADS with the outcome — "Needs you" for a
 *        workflow parked on the user, "Skipped" for a refused goal
 *        iteration, "Failed" for a run that failed after an ask expired —
 *        each in its warn/danger tone token ON THE WORD, never a grey "Done"
 *        with only a coloured dot; Run now, the notice the daemon delivered
 *        and the bell say the same verdict;
 *  - (2) a mission whose worklist kept a failed item: the bell says "part of
 *        it failed" (never "Your objective is done"), the screen says
 *        "Finished, but part of it failed", the objectives rail says "Part
 *        failed" in the warn tone (never a plain "Done").
 *
 * Only the network (lib/api) is stubbed; next/link and the page shell are
 * the usual thin stand-ins.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    return Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    const r = api.postResponses[path];
    return Promise.resolve(r === undefined ? {} : r);
  },
  patch: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

import SchedulesPage from "@/app/schedules/page";
import { ProjectSchedules } from "@/components/project/ProjectSchedules";
import { MissionComposer } from "@/components/agents/mission/MissionComposer";
import { toActivity } from "@/components/NotificationBell";
import { __resetApiCache } from "@/lib/apiCache";
import {
  PART_FAILED_HEADLINE,
  missionHeadline,
  missionRowWords,
  type MissionView,
} from "@/lib/mission";
import { runNowNote, scheduleDetail } from "@/lib/scheduleOutcome";
import { scheduleRunWords, statusWords, toneTextClass } from "@/lib/statusWords";
import type { IJEvent, Schedule } from "@/lib/types";

interface FixtureRow {
  case: string;
  name: string;
  last_status: string;
  last_detail: string;
  notice: string;
  completion?: Record<string, unknown>;
}
const FIXTURE = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "outcomes-v13580.json"), "utf-8"),
) as {
  schedule_rows: FixtureRow[];
  mission_part_failed: Record<string, unknown>;
  browser_statuses: string[];
};

afterEach(() => {
  cleanup();
  __resetApiCache();
  api.posts = [];
  api.responses = {};
  api.postResponses = {};
});

function row(over: Partial<Schedule> = {}): Schedule {
  return {
    name: "nightly",
    cron: "0 2 * * *",
    kind: "task",
    enabled: true,
    next_run: null,
    last_run: "2026-10-19T02:00:00Z",
    trigger_type: "cron",
    payload_json: JSON.stringify({ task: "Review the week." }),
    project_id: null,
    last_status: "ok",
    last_detail: "",
    last_session_id: "",
    ...over,
  } as Schedule;
}

function mountPage(schedules: Schedule[]) {
  api.responses["/schedules"] = { schedules };
  api.responses["/workflows"] = { workflows: [] };
  api.responses["/projects"] = { projects: [] };
  api.responses["/comm/channels"] = { channels: [] };
  api.responses["/agents"] = { builtin: ["builder"], dynamic: [] };
  return render(<SchedulesPage />);
}

/** The row's leading word for each verdict the fixture's fires wrote. */
const LEAD: Record<string, { word: string; ink: string }> = {
  needs_you: { word: "Needs you", ink: "!text-tone-warn" },
  skipped: { word: "Skipped", ink: "!text-tone-warn" },
  error: { word: "Failed", ink: "!text-tone-danger" },
};

/** The verdict a delivered notice states: "Schedule “x”: <head> — …". */
function noticeHead(notice: string): string {
  const after = notice.slice(notice.indexOf("”: ") + 3);
  return after.split(" — ")[0];
}

/* ======================================== (1) the Schedules row leads */

describe("(1) a schedule's row, Run now, the notice and the bell say ONE verdict", () => {
  it("the fixture covers the three cases the re-audit found reading 'Done'", () => {
    expect(FIXTURE.schedule_rows.map((r) => [r.case, r.last_status])).toEqual([
      ["workflow-parked", "needs_you"],
      ["goal-skipped", "skipped"],
      ["failed-asked", "error"],
    ]);
  });

  for (const r of FIXTURE.schedule_rows) {
    it(`${r.case}: the row LEADS with "${LEAD[r.last_status]?.word}" in its tone, never Done`, async () => {
      mountPage([row({ name: r.name, last_status: r.last_status, last_detail: r.last_detail })]);
      const word = await screen.findByTestId(`schedule-status-${r.name}`);
      const lead = LEAD[r.last_status];
      expect(word.textContent).toBe(lead.word);
      expect(word.textContent).not.toMatch(/Done/);
      // The tone is on the WORD (a theme token), not only on the dot.
      const inks = word.className.split(/\s+/).filter((c) => /text-tone-/.test(c));
      expect(inks).toEqual([lead.ink]);
      expect(word.className).not.toMatch(/amber|rose|emerald|#[0-9a-f]{3}/i);
      // The detail is the daemon's plain sentence.
      expect(screen.getByTestId(`schedule-detail-${r.name}`).textContent).toBe(scheduleDetail(r.last_detail));
      // The notice the daemon DELIVERED says the same verdict.
      expect(noticeHead(r.notice).toLowerCase()).toBe(lead.word.toLowerCase());
      expect(r.notice).not.toMatch(/: done|finished, but it needs you/);
    });

    it(`${r.case}: Run now says the row's verdict, never "Ran ✓" or "finished"`, async () => {
      mountPage([row({ name: r.name, last_status: "ok" })]);
      api.postResponses[`/schedules/${r.name}/run`] = {
        ran: r.name,
        last_status: r.last_status,
        last_detail: r.last_detail,
      };
      fireEvent.click(await screen.findByTitle("Run now"));
      const note = runNowNote(r.name, r.last_status, r.last_detail);
      await waitFor(() => expect(screen.getByText(note.text)).toBeTruthy());
      expect(note.tone).toBe(r.last_status === "error" ? "danger" : "warn");
      expect(note.text).not.toMatch(/Ran "|finished/);
      expect(screen.queryByText(/Ran "/)).toBeNull();
    });
  }

  it("failed-asked: the bell row for the SAME run says it could not finish, like the row's Failed", () => {
    const r = FIXTURE.schedule_rows.find((x) => x.case === "failed-asked")!;
    const item = toActivity({
      id: "e-failed-asked",
      type: "session.completed",
      ts: "2026-10-19T07:00:05+00:00",
      session_id: "sess-failed-asked",
      payload: { ...r.completion, summary: "Stopped at the folder it may not open." },
    } as IJEvent)!;
    expect(item.title).toBe(`Your schedule ${r.name} could not finish`);
    expect(item.title).not.toMatch(/needs you/);
    expect(item.attention).toBe(true);
  });

  it("a project's Schedules list leads with the same word in the same tone", async () => {
    const r = FIXTURE.schedule_rows.find((x) => x.case === "workflow-parked")!;
    api.responses["/schedules"] = {
      schedules: [
        row({
          name: r.name,
          last_status: r.last_status,
          last_detail: r.last_detail,
          project_id: "proj_1",
          payload_json: JSON.stringify({ workflow: "gated", project_id: "proj_1" }),
        }),
      ],
    };
    render(<ProjectSchedules projectId="proj_1" />);
    const word = await screen.findByTestId(`project-schedule-status-${r.name}`);
    expect(word.textContent).toBe("Needs you");
    expect(word.className).toMatch(/text-tone-warn/);
  });

  it("anti-vacuity: a clean run is a calm grey 'Done' (no ink on the word); a stop is grey too", async () => {
    mountPage([row({ name: "a", last_status: "ok" }), row({ name: "b", last_status: "cancelled" })]);
    const ok = await screen.findByTestId("schedule-status-a");
    expect(ok.textContent).toBe("Done");
    expect(ok.className).not.toMatch(/text-tone-/);
    const stopped = screen.getByTestId("schedule-status-b");
    expect(stopped.textContent).toBe("Stopped");
    expect(stopped.className).not.toMatch(/text-tone-/);
  });

  it("the one table: a session that fell short LEADS with the same words a schedule does", () => {
    expect(statusWords("completed", "needs_you").word).toBe(scheduleRunWords("needs_you").word);
    expect(statusWords("completed", "completed_with_failures").word).toBe(scheduleRunWords("partial").word);
    expect(scheduleRunWords("partial")).toEqual({ word: "Part failed", tone: "amber" });
    expect(toneTextClass("amber")).toBe("!text-tone-warn");
    expect(toneTextClass("red")).toBe("!text-tone-danger");
    expect(toneTextClass("green")).toBe("");
    expect(toneTextClass("slate")).toBe("");
  });
});

/* ============================== (2) one verdict for a mission everywhere */

describe("(2) a mission whose worklist kept a failed item reads part-failed everywhere", () => {
  const m = FIXTURE.mission_part_failed;

  it("the fixture is the daemon's part-failed mission event", () => {
    expect(m.origin).toBe("job:mission");
    expect(m.status).toBe("completed");
    expect(m.outcome).toBe("completed_with_failures");
  });

  it("the bell says part of it failed, never 'Your objective is done'", () => {
    const item = toActivity({
      id: "e-mission",
      type: "session.completed",
      ts: "2026-10-19T07:00:05+00:00",
      session_id: "m1",
      payload: { ...m, summary: "# Report\nAll done." },
    } as IJEvent)!;
    expect(item.title).toBe("Your objective finished, but part of it failed");
    expect(item.title).not.toMatch(/is done/);
    // The screen's headline for the same mission: the same words.
    const head = missionHeadline({
      session: { status: m.status, outcome: m.outcome },
    } as unknown as MissionView);
    expect(head).toBe(PART_FAILED_HEADLINE);
    expect(item.title.endsWith(head.replace(/^Finished/, "finished"))).toBe(true);
    // The body is the report in plain words, never its markdown.
    expect(item.body).toBe("Report All done.");
  });

  it("the objectives rail shows 'Part failed' in the warn tone — never a plain 'Done'", async () => {
    api.responses["/projects"] = { projects: [] };
    api.responses["/missions"] = {
      missions: [
        { id: "m1", objective: "rename the scans", status: m.status, outcome: m.outcome, waiting: false, project_id: null, created_at: "2026-10-19T07:00:00", finished_at: "2026-10-19T07:05:00" },
        { id: "m2", objective: "file the receipts", status: "completed", outcome: "needs_you", waiting: false, project_id: null, created_at: "2026-10-19T06:00:00", finished_at: "2026-10-19T06:05:00" },
        { id: "m3", objective: "sort the inbox", status: "completed", outcome: "completed", waiting: false, project_id: null, created_at: "2026-10-19T05:00:00", finished_at: "2026-10-19T05:05:00" },
        { id: "m4", objective: "draft the memo", status: "active", outcome: null, waiting: true, project_id: null, created_at: "2026-10-19T04:00:00", finished_at: null },
      ],
    };
    render(<MissionComposer onStarted={() => {}} onOpen={() => {}} />);
    const partFailed = await screen.findByTestId("mission-recent-status-m1");
    expect(partFailed.textContent).toBe("Part failed");
    expect(partFailed.className).toMatch(/text-tone-warn/);
    const needsYou = screen.getByTestId("mission-recent-status-m2");
    expect(needsYou.textContent).toBe("Needs you");
    expect(needsYou.className).toMatch(/text-tone-warn/);
    // Anti-vacuity: an objective that earned its Done reads a calm Done.
    const done = screen.getByTestId("mission-recent-status-m3");
    expect(done.textContent).toBe("Done");
    expect(done.className).not.toMatch(/text-tone-/);
    // A parked one still reads "Needs you" (unchanged).
    expect(screen.getByTestId("mission-recent-status-m4").textContent).toBe("Needs you");
  });

  it("the rail reads the ONE status table (no table of its own)", () => {
    expect(missionRowWords({ status: "completed", outcome: "completed_with_failures", waiting: false })).toEqual(
      statusWords("completed", "completed_with_failures"),
    );
    expect(missionRowWords({ status: "failed", outcome: "needs_you", waiting: false }).word).toBe("Failed");
    expect(missionRowWords({ status: "cancelled", outcome: null, waiting: false }).word).toBe("Stopped");
    const src = readFileSync(join(__dirname, "..", "components", "agents", "mission", "MissionComposer.tsx"), "utf-8").replace(/\r\n/g, "\n");
    expect(src).not.toMatch(/const STATUS_WORD\b/);
    expect(src).toMatch(/missionRowWords\(/);
  });
});
