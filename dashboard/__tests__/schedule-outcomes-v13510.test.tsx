/**
 * v1.351.0 lane 1A — a schedule's last run, said truthfully where the user
 * reads it (readiness audit G01 + G02).
 *
 *  - lib/scheduleOutcome is the ONE table of words + tones: needs_you reads
 *    "finished · needs you", partial "finished · part failed", an unknown
 *    value reads as itself (it used to fall through to "not run yet");
 *  - the Schedules page row: amber words for a run that finished short, the
 *    open-session link, the detail as plain words (an older row's flattened
 *    "# Final Report ## …" too); Run now answers in AMBER for those, never
 *    "Ran ✓";
 *  - a project's Schedules list says the same words;
 *  - the home line above the composer offers "1 scheduled job needs a look"
 *    whose Open goes to /schedules, ranked under a waiting ask and above work
 *    that is merely running.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
import { HomeLine, homeLineItems } from "@/components/chat/HomeLine";
import {
  failureReason,
  isShortSchedule,
  runNowNote,
  scheduleDetail,
  scheduleStatusWords,
} from "@/lib/scheduleOutcome";
import type { Schedule } from "@/lib/types";

afterEach(() => {
  cleanup();
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
    last_run: "2026-10-18T02:00:00Z",
    trigger_type: "cron",
    payload_json: JSON.stringify({ task: "Review the week's memory.", project_id: "proj_1" }),
    project_id: "proj_1",
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

const NEEDS_YOU_DETAIL =
  "It asked for your OK and nobody answered in time, so some steps did not run. I reviewed 12 conversations.";

/* ------------------------------------------------------------- the words */

describe("lib/scheduleOutcome — one table of words", () => {
  it("names every verdict the daemon writes, and an unknown one as itself", () => {
    expect(scheduleStatusWords("ok")).toEqual({ label: "✓ ok", tone: "success" });
    expect(scheduleStatusWords("error")).toEqual({ label: "✗ failed", tone: "danger" });
    expect(scheduleStatusWords("needs_you")).toEqual({ label: "finished · needs you", tone: "warn" });
    expect(scheduleStatusWords("partial")).toEqual({ label: "finished · part failed", tone: "warn" });
    expect(scheduleStatusWords("cancelled")).toEqual({ label: "stopped", tone: "warn" });
    expect(scheduleStatusWords("skipped").tone).toBe("warn");
    expect(scheduleStatusWords("missed").tone).toBe("warn");
    expect(scheduleStatusWords("")).toEqual({ label: "not run yet", tone: "muted" });
    expect(scheduleStatusWords(undefined).label).toBe("not run yet");
    // The old lie: any value it did not know read "not run yet".
    expect(scheduleStatusWords("brand_new").label).toBe("brand new");
  });

  it("counts failed and short runs, never a fine or skipped one", () => {
    expect(["error", "needs_you", "partial"].every(isShortSchedule)).toBe(true);
    expect(["ok", "skipped", "missed", "cancelled", "", undefined].some(isShortSchedule)).toBe(false);
  });

  it("reads a detail as plain words, an older flattened report too", () => {
    expect(scheduleDetail("# Final Report ## What happened I reviewed **12** notes.")).toBe(
      "Final Report What happened I reviewed 12 notes.",
    );
    expect(failureReason("The run failed: the endpoint is down")).toBe("the endpoint is down");
    expect(failureReason("provider unreachable")).toBe("provider unreachable");
  });

  it("Run now's note is amber for a short run, red for a failure, green only for ok", () => {
    expect(runNowNote("n", "needs_you", NEEDS_YOU_DETAIL)).toEqual({
      tone: "warn",
      text: `"n" finished, but it needs you — ${NEEDS_YOU_DETAIL}`,
    });
    expect(runNowNote("n", "partial", "").text).toBe('"n" finished, but part of it failed');
    expect(runNowNote("n", "skipped", "Emergency stop is on, so this run was skipped.").tone).toBe("warn");
    expect(runNowNote("n", "cancelled", "").tone).toBe("warn");
    expect(runNowNote("n", "error", "The run failed: x")).toEqual({
      tone: "danger",
      text: '"n" failed — The run failed: x',
    });
    expect(runNowNote("n", "ok", "Done.")).toEqual({ tone: "success", text: 'Ran "n" ✓ — Done.' });
  });
});

/* -------------------------------------------------------- the Schedules page */

describe("the Schedules page row", () => {
  it("a run that needs the user is amber 'finished · needs you' with its session link — never ✓ ok", async () => {
    mountPage([row({ last_status: "needs_you", last_detail: NEEDS_YOU_DETAIL, last_session_id: "session_abc" })]);
    const word = await screen.findByTestId("schedule-status-nightly");
    expect(word.textContent).toBe("finished · needs you");
    expect(word.className).toMatch(/text-tone-warn/);
    expect(screen.queryByText("✓ ok")).toBeNull();
    expect(screen.queryByText("not run yet")).toBeNull();
    const cell = word.parentElement as HTMLElement;
    expect(within(cell).getByText(/open session/).closest("a")?.getAttribute("href")).toBe(
      "/sessions/session_abc",
    );
    expect(screen.getByTestId("schedule-detail-nightly").textContent).toBe(NEEDS_YOU_DETAIL);
  });

  it("a partly failed run reads 'finished · part failed'", async () => {
    mountPage([row({ last_status: "partial", last_detail: "Some of its steps failed." })]);
    const word = await screen.findByTestId("schedule-status-nightly");
    expect(word.textContent).toBe("finished · part failed");
    expect(word.className).toMatch(/text-tone-warn/);
  });

  it("a failed run keeps its red word and reads 'The run failed: <reason>'", async () => {
    mountPage([row({ last_status: "error", last_detail: "The run failed: the endpoint is down" })]);
    const word = await screen.findByTestId("schedule-status-nightly");
    expect(word.textContent).toBe("✗ failed");
    expect(word.className).toMatch(/text-rose-300/);
    expect(screen.getByTestId("schedule-detail-nightly").textContent).toBe(
      "The run failed: the endpoint is down",
    );
  });

  it("the detail is plain words, never the report's markdown", async () => {
    mountPage([row({ last_detail: "# Final Report ## What happened I reviewed **12** notes." })]);
    const detail = await screen.findByTestId("schedule-detail-nightly");
    expect(detail.textContent).not.toMatch(/[#*]/);
    expect(detail.getAttribute("title")).not.toMatch(/[#*]/);
  });

  it("the unchanged words: ✓ ok, skipped, missed, not run yet", async () => {
    mountPage([
      row({ name: "a", last_status: "ok" }),
      row({ name: "b", last_status: "skipped" }),
      row({ name: "c", last_status: "missed" }),
      row({ name: "d", last_status: "" }),
    ]);
    expect((await screen.findByTestId("schedule-status-a")).textContent).toBe("✓ ok");
    expect(screen.getByTestId("schedule-status-a").className).toMatch(/text-emerald-300/);
    expect(screen.getByTestId("schedule-status-b").textContent).toBe("skipped");
    expect(screen.getByTestId("schedule-status-c").textContent).toBe("missed");
    expect(screen.getByTestId("schedule-status-d").textContent).toBe("not run yet");
  });

  it("Run now on a run that needs the user answers in amber, never 'Ran ✓'", async () => {
    mountPage([row()]);
    api.postResponses["/schedules/nightly/run"] = {
      ran: "nightly",
      last_status: "needs_you",
      last_detail: NEEDS_YOU_DETAIL,
    };
    fireEvent.click(await screen.findByTitle("Run now"));
    const note = await screen.findByTestId("schedule-run-warn");
    expect(note.textContent).toContain('"nightly" finished, but it needs you');
    expect(note.getAttribute("role")).toBe("status");
    expect(note.className).toMatch(/text-tone-warn/);
    expect(screen.queryByText(/Ran "nightly"/)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("Run now while Emergency stop is on says it did not run, in amber", async () => {
    mountPage([row()]);
    api.postResponses["/schedules/nightly/run"] = {
      ran: "nightly",
      last_status: "skipped",
      last_detail: "Emergency stop is on, so this run was skipped.",
    };
    fireEvent.click(await screen.findByTitle("Run now"));
    const note = await screen.findByTestId("schedule-run-warn");
    expect(note.textContent).toContain('"nightly" did not run — Emergency stop is on');
  });

  it("Run now on a clean run still says Ran ✓ (control)", async () => {
    mountPage([row()]);
    api.postResponses["/schedules/nightly/run"] = { ran: "nightly", last_status: "ok", last_detail: "Done." };
    fireEvent.click(await screen.findByTitle("Run now"));
    expect(await screen.findByText('Ran "nightly" ✓ — Done.')).toBeInTheDocument();
    expect(screen.queryByTestId("schedule-run-warn")).toBeNull();
  });
});

/* --------------------------------------------------- a project's Schedules */

describe("a project's Schedules list", () => {
  it("says the same amber words, with the session link", async () => {
    api.responses["/schedules"] = {
      schedules: [row({ last_status: "needs_you", last_detail: NEEDS_YOU_DETAIL, last_session_id: "session_abc" })],
    };
    render(<ProjectSchedules projectId="proj_1" />);
    const word = await screen.findByTestId("project-schedule-status-nightly");
    expect(word.textContent).toBe("finished · needs you");
    expect(word.className).toMatch(/text-tone-warn/);
    expect(word.getAttribute("title")).toBe(NEEDS_YOU_DETAIL);
    expect(screen.queryByText("not run yet")).toBeNull();
    expect(screen.getByText(/open session/).closest("a")?.getAttribute("href")).toBe("/sessions/session_abc");
  });

  it("a failure names its reason once", async () => {
    api.responses["/schedules"] = {
      schedules: [row({ last_status: "error", last_detail: "The run failed: the endpoint is down" })],
    };
    render(<ProjectSchedules projectId="proj_1" />);
    expect(await screen.findByText("✗ failed: the endpoint is down")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------ the home line */

describe("the home line names a scheduled job that needs a look", () => {
  const base = { interrupted: 0, failingLoops: 0, failingPacks: 0, running: 0, waiting: 0 };

  it("one line, its words, ranked under a waiting ask and above running work", () => {
    expect(homeLineItems({ ...base, shortSchedules: 1 })).toEqual([
      { kind: "schedules", text: "1 scheduled job needs a look" },
    ]);
    expect(homeLineItems({ ...base, shortSchedules: 3 })[0].text).toBe("3 scheduled jobs need a look");
    expect(homeLineItems({ ...base, shortSchedules: 1, running: 2 }).map((i) => i.kind)).toEqual([
      "schedules",
      "working",
    ]);
    expect(homeLineItems({ ...base, shortSchedules: 1, waiting: 1 }).map((i) => i.kind)).toEqual([
      "working",
      "schedules",
    ]);
    expect(homeLineItems({ ...base, shortSchedules: 1, interrupted: 1, failingLoops: 1 }).map((i) => i.kind)).toEqual([
      "interrupted",
      "failing",
      "schedules",
    ]);
    // Absent (an older daemon) or zero: nothing new.
    expect(homeLineItems(base)).toEqual([]);
  });

  it("offers '1 scheduled job needs a look · Open' and Open goes to /schedules", async () => {
    api.responses["/ui/status-line"] = {
      interrupted: 0,
      failing_loops: 0,
      failing_packs: 0,
      running: 0,
      short_schedules: 1,
    };
    api.responses["/chat/approvals/pending"] = { approvals: [] };
    render(<HomeLine />);
    const line = await screen.findByTestId("home-line");
    await waitFor(() => expect(line.textContent).toContain("1 scheduled job needs a look"));
    expect(line.getAttribute("data-kind")).toBe("schedules");
    expect(within(line).getByRole("link", { name: "Open" }).getAttribute("href")).toBe("/schedules");
  });
});
