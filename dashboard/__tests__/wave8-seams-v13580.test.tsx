/**
 * v1.358.0 integrate — the wave-8 seams on the dashboard side.
 *
 * (a) ONE VERDICT PER RUN: the Schedules row, the chat's run card, the
 *     mission rail and the bell read a run that needs the user, partly
 *     failed or failed in ONE set of words (lib/statusWords, lane 8A) — and
 *     a parked scheduled workflow the person answered reads as the daemon
 *     now restamps it (fixtures/wave8-seams-v13580.json, written by the REAL
 *     daemon in tests/test_integrate_wave8_v13580.py).
 * (b) ONE RECEIPT: the chat's full run card (lane 8C) headlines in lane 8A's
 *     table words, the same word the Schedules row uses for the same verdict.
 * (d) ONE PLACE NAME: the chat's Connections switches sit in the composer's
 *     Tools menu (the wrench chip); every sentence that sends the user there
 *     says "Tools › Connections", never a "+" menu.
 * (e) ONE DOOR KIND: a Base URL that is another Jarvis's task door picks the
 *     task API for the user (it cannot be anything else), even after a
 *     different kind was chosen.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

const api = vi.hoisted(() => ({ posts: [] as { path: string; body: unknown }[] }));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status = 500;
  }
  return {
    ApiError,
    API_BASE: "http://test",
    ijToken: () => "tok-1",
    get: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    post: (p: string, body?: unknown) => {
      api.posts.push({ path: p, body });
      if (p === "/agents/remote") return Promise.resolve({ name: "desk", another_jarvis: true });
      return Promise.resolve({ reverted: [], skipped: [], considered: 0 });
    },
  };
});

import outcomes from "./fixtures/outcomes-v13580.json";
import seams from "./fixtures/wave8-seams-v13580.json";
import { RunResultCard, resultHeadline, type RunResult } from "@/components/chat/RunResultCard";
import { RemoteAgentsSection } from "@/components/agents/SetupCard";
import { missionRowWords } from "@/lib/mission";
import { isShortSchedule, scheduleDetail } from "@/lib/scheduleOutcome";
import { scheduleRunWords, statusWords, toneTextClass } from "@/lib/statusWords";
import { TASK_DOOR_PATH } from "@/lib/anotherJarvis";
import { USE_IN_CHAT } from "@/lib/addPackWords";

const ROOT = path.resolve(__dirname, "..");
const read = (rel: string) => readFileSync(path.join(ROOT, rel), "utf8").replace(/\r\n/g, "\n");

function result(over: Partial<RunResult>): RunResult {
  return {
    found: true,
    session_id: "session_seam",
    status: "completed",
    task: "Tidy the scans",
    summary: "Moved 3 files.",
    steps: 4,
    tools_used: [{ tool: "write_file", count: 1 }],
    tools_failed: [],
    files_created: [],
    files_changed: [],
    errors: [],
    revertable: 0,
    duration_s: 12,
    ...over,
  };
}

beforeEach(() => {
  api.posts = [];
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/* ------------------------------------------- (a) + (b) one verdict, one word */

describe("(a)+(b) one verdict per run: the Schedules row, the run card and the mission rail say one word", () => {
  it("needs you: the same word on all three", () => {
    const row = scheduleRunWords("needs_you").word;
    expect(row).toBe("Needs you");
    expect(resultHeadline(result({ unanswered_asks: 1 }))).toBe(row);
    expect(resultHeadline(result({ outcome: "needs_you" }))).toBe(row);
    expect(missionRowWords({ status: "completed", outcome: "needs_you", waiting: false } as never).word).toBe(row);
    expect(statusWords("completed", "needs_you").word).toBe(row);
  });

  it("part failed: the same word on all three", () => {
    const row = scheduleRunWords("partial").word;
    expect(row).toBe("Part failed");
    expect(resultHeadline(result({ outcome: "completed_with_failures" }))).toBe(row);
    expect(
      missionRowWords({ status: "completed", outcome: "completed_with_failures", waiting: false } as never).word,
    ).toBe(row);
  });

  it("the daemon's failed-after-an-ask row reads Failed, like the run's own card", () => {
    const failed = outcomes.schedule_rows.find((r) => r.case === "failed-asked")!;
    expect(scheduleRunWords(failed.last_status).word).toBe("Failed");
    expect(resultHeadline(result({ status: failed.completion!.status, summary: "Stopped." }))).toBe("Failed");
  });

  it("a parked workflow the person answered reads Done, calm, and no longer needs a look", () => {
    const row = seams.parked_then_answered;
    const parked = outcomes.schedule_rows.find((r) => r.case === "workflow-parked")!;
    expect(row.name).toBe(parked.name); // the same schedule, before and after
    expect(isShortSchedule(parked.last_status)).toBe(true);
    const words = scheduleRunWords(row.last_status);
    expect(words.word).toBe("Done");
    expect(toneTextClass(words.tone)).toBe("");
    expect(isShortSchedule(row.last_status)).toBe(false);
    expect(scheduleDetail(row.last_detail)).toBe(row.last_detail);
    expect(row.last_detail).toMatch(/finished after your answer/);
  });

  it("the full run card in chat headlines in the table's word (rendered)", () => {
    render(<RunResultCard result={result({ unanswered_asks: 2 })} />);
    const card = screen.getByTestId("run-result-card");
    expect(within(card).getByText(scheduleRunWords("needs_you").word)).toBeTruthy();
    expect(card.textContent).not.toMatch(/Task complete|Done · needs you/);
  });
});

/* -------------------------------------------- (d) the Tools menu, by name */

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) out.push(...sourceFiles(full));
    else if (/\.(ts|tsx)$/.test(name)) out.push(full);
  }
  return out;
}

describe("(d) the chat's Connections switches are in the Tools menu, and every sentence says so", () => {
  it("the composer's Connections submenu lives inside the Tools chip's menu", () => {
    const page = read("app/chat/page.tsx");
    const menu = page.indexOf('data-testid="composer-tools-menu"');
    const toggle = page.indexOf('plusSub === "connectors" ? null : "connectors"');
    expect(menu).toBeGreaterThan(0);
    expect(toggle).toBeGreaterThan(menu);
    expect(page.indexOf("{toolMenuOpen && (")).toBeLessThan(menu);
  });

  it("no user-facing sentence sends anyone to a '+' menu for it", () => {
    const files = [...sourceFiles(path.join(ROOT, "app")), ...sourceFiles(path.join(ROOT, "components")), ...sourceFiles(path.join(ROOT, "lib"))];
    expect(files.length).toBeGreaterThan(100); // anti-vacuity
    const hits = files.filter((f) => /["`>][^"`<]*\+ › Connections/.test(readFileSync(f, "utf8")));
    expect(hits.map((f) => path.relative(ROOT, f))).toEqual([]);
    expect(USE_IN_CHAT).toBe("Use it in a chat: Tools › Connections");
  });
});

/* ------------------------------------------------- (e) the door picks its kind */

describe("(e) another Jarvis's door address picks the task API for the user", () => {
  const DOOR_URL = `http://192.168.1.20:8787${TASK_DOOR_PATH}`;

  it("a door address sends http-task even after a chat kind was chosen, and the select says it is picked", async () => {
    render(
      <RemoteAgentsSection remotes={[]} faces={{}} facesSupported={false} onChanged={() => {}} onFaceChanged={() => {}} />,
    );
    const form = await screen.findByTestId("connect-remote-form");
    const kind = within(form).getByLabelText("Remote kind") as HTMLSelectElement;
    fireEvent.change(kind, { target: { value: "openai-chat" } });
    expect(kind.value).toBe("openai-chat");
    fireEvent.change(within(form).getByLabelText("Remote agent name"), { target: { value: "desk" } });
    fireEvent.change(within(form).getByLabelText("Base URL"), { target: { value: DOOR_URL } });
    expect(kind.value).toBe("http-task");
    expect(kind.disabled).toBe(true);
    expect(within(form).getByTestId("connect-remote-jarvis-hint").textContent).toMatch(/picked for you/);
    fireEvent.change(within(form).getByLabelText("Bearer secret"), { target: { value: "door-token" } });
    fireEvent.click(within(form).getByRole("button", { name: /Connect remote/ }));
    await within(form).findByText(/"@desk" in a chat hands it a task/);
    expect(api.posts.find((p) => p.path === "/agents/remote")?.body).toMatchObject({
      kind: "http-task",
      base_url: DOOR_URL,
      model: "",
    });
  });

  it("CONTROL: an ordinary address keeps the user's own choice", async () => {
    render(
      <RemoteAgentsSection remotes={[]} faces={{}} facesSupported={false} onChanged={() => {}} onFaceChanged={() => {}} />,
    );
    const form = await screen.findByTestId("connect-remote-form");
    const kind = within(form).getByLabelText("Remote kind") as HTMLSelectElement;
    fireEvent.change(within(form).getByLabelText("Base URL"), { target: { value: "http://192.168.1.50:8080/v1" } });
    fireEvent.change(kind, { target: { value: "openai-chat" } });
    expect(kind.value).toBe("openai-chat");
    expect(kind.disabled).toBe(false);
  });
});
