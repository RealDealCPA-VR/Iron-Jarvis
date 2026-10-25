/**
 * IJ harness part 3, Phase 2 — the FINISH CHECK on the screen.
 *
 * A Build reply that changed files is checked once before it ends (the
 * daemon's harness/finish); the reply line keeps what the check did as
 * `finish {tests, command, removed, kept, points, unmet, after[, why]}`.
 *
 *  1. The words: ONE sentence through lib/stepWords — "Checked: tests
 *     passed, 1 scratch file removed", "Checked: tests failed, then fixed" —
 *     built only from the record (never a claim it does not make).
 *  2. The decoder whitelists it (lib/buildSessions `decodeFinish`, and the
 *     line whitelist keeps `finish`); anything else dies.
 *  3. The work line shows it under itself in its own quiet words and marks;
 *     an older daemon's line (no `finish`) reads EXACTLY as before.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return { ApiError, gets: {} as Record<string, unknown> };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string) => {
    const value = method === "GET" ? api.gets[path] : undefined;
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
    health: { default_provider: "mock", default_model: "mock-1", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { WorkLine } from "@/components/chat/WorkLine";
import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { decodeFinish, decodeSessionView } from "@/lib/buildSessions";
import { finishTitle, finishWarn, finishWords, type FinishRecord } from "@/lib/stepWords";

const rec = (over: Partial<FinishRecord> = {}): FinishRecord => ({
  tests: "passed",
  command: "python -m unittest -q test_calc",
  removed: 0,
  kept: 0,
  points: 9,
  unmet: 0,
  after: "",
  ...over,
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/* ----------------------------------------------------------- 1. the words */

describe("the finish check's one sentence", () => {
  it.each([
    [rec(), "Checked: tests passed"],
    [rec({ removed: 1 }), "Checked: tests passed, 1 scratch file removed"],
    [rec({ removed: 3, kept: 1 }), "Checked: tests passed, 3 scratch files removed, 1 new file kept"],
    [rec({ tests: "failed", after: "passed" }), "Checked: tests failed, then fixed"],
    [rec({ tests: "failed" }), "Checked: tests failed"],
    [rec({ tests: "failed", after: "failed" }), "Checked: tests failed"],
    [rec({ tests: "passed", after: "failed" }), "Checked: tests passed, then failed"],
    [rec({ tests: "none", command: "" }), "Checked: no tests found"],
    [rec({ tests: "not_run", why: "you declined it" }), "Checked: tests not run"],
    [rec({ tests: "not_run", after: "passed" }), "Checked: tests passed after the check"],
    // Review: no test command found, then the model ran its tests — never
    // "no tests found" over that run.
    [rec({ tests: "none", command: "", after: "passed" }), "Checked: tests passed after the check"],
    [rec({ tests: "none", command: "", after: "failed" }), "Checked: tests failed after the check"],
    [rec({ unmet: 2, tests: "failed", after: "passed" }), "Checked: tests failed, then fixed, 2 points needed more work"],
    [rec({ unmet: 1 }), "Checked: tests passed, 1 point needed more work"],
  ])("%j reads %s", (r, words) => {
    expect(finishWords(r)).toBe(words);
  });

  it("warns only while the tests were last seen failing", () => {
    expect(finishWarn(rec())).toBe(false);
    expect(finishWarn(rec({ tests: "failed" }))).toBe(true);
    expect(finishWarn(rec({ tests: "failed", after: "passed" }))).toBe(false);
    expect(finishWarn(rec({ after: "failed" }))).toBe(true);
    expect(finishWarn(rec({ tests: "none" }))).toBe(false);
    expect(finishWarn(rec({ tests: "not_run" }))).toBe(false);
  });

  it("its hover names the command, and why the tests did not run", () => {
    expect(finishTitle(rec())).toBe("Ran: python -m unittest -q test_calc");
    // Review: a command that did NOT run is never "Ran:" (the hover said
    // "Ran: X" above "Not run: you declined it").
    expect(finishTitle(rec({ tests: "not_run", why: "you declined it" }))).toBe(
      "Not run: python -m unittest -q test_calc\nWhy: you declined it",
    );
    expect(finishTitle(rec({ tests: "not_run", command: "", why: "low trust" }))).toBe("Why: low trust");
    expect(finishTitle(rec({ tests: "none", command: "" }))).toBeUndefined();
  });
});

/* -------------------------------------------------------- 2. the decoder */

describe("the decoder", () => {
  it("keeps the known keys with their types and drops the rest", () => {
    expect(
      decodeFinish({ tests: "failed", command: "pytest", removed: 1, kept: 0, points: 4, unmet: 1, after: "passed",
                     output: "never this", why: "not for a run that ran" }),
    ).toEqual({ tests: "failed", command: "pytest", removed: 1, kept: 0, points: 4, unmet: 1, after: "passed" });
    expect(decodeFinish({ tests: "not_run", why: "you declined it" })?.why).toBe("you declined it");
    expect(decodeFinish({ tests: "passed", removed: -2, kept: 1.5, unmet: "3", after: "maybe" })).toEqual({
      tests: "passed", command: "", removed: 0, kept: 0, points: 0, unmet: 0, after: "",
    });
  });

  it("refuses anything that is not a record", () => {
    for (const raw of [null, undefined, "passed", 3, [], { tests: "great" }, { removed: 1 }]) {
      expect(decodeFinish(raw)).toBeNull();
    }
  });

  it("the line whitelist keeps `finish` on a reply", () => {
    const v = decodeSessionView({
      id: "s1", title: "t", messages: [{ role: "assistant", content: "x", finish: rec(), secret: "no" }],
    });
    expect(v?.messages[0].finish).toEqual(rec());
    expect((v?.messages[0] as unknown as Record<string, unknown>).secret).toBeUndefined();
  });
});

/* ----------------------------------------------------- 3. the work line */

const steps = [{ name: "write_file", ok: true, ms: 12, target: "app.py" }, { name: "shell", ok: true, ms: 800 }];
const timing = { startedAt: 1_000, firstTokenAt: 1_500, endedAt: 4_000 };

describe("the work line", () => {
  it("shows the sentence under itself, with the check mark, and the command on hover", () => {
    render(<WorkLine steps={steps} timing={timing} finish={rec({ removed: 1 })} />);
    const line = screen.getByTestId("work-line-finish");
    expect(line).toHaveTextContent("Checked: tests passed, 1 scratch file removed");
    expect(line.getAttribute("data-outcome")).toBe("ok");
    expect(line.getAttribute("title")).toBe("Ran: python -m unittest -q test_calc");
  });

  it("carries the theme's warn mark when the tests were last seen failing", () => {
    render(<WorkLine steps={steps} timing={timing} finish={rec({ tests: "failed" })} />);
    const line = screen.getByTestId("work-line-finish");
    expect(line.getAttribute("data-outcome")).toBe("warn");
    expect(line.querySelector(".text-tone-warn")).not.toBeNull();
  });

  it("with no finish (the chat, an older daemon) the line is EXACTLY as before", () => {
    const before = render(<WorkLine steps={steps} timing={timing} />).container.innerHTML;
    cleanup();
    const withNull = render(<WorkLine steps={steps} timing={timing} finish={null} />).container.innerHTML;
    expect(withNull).toBe(before);
    expect(screen.queryByTestId("work-line-finish")).toBeNull();
  });
});

/* ------------------------------------------- 4. a session's saved reply */

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(messages: unknown[]) {
  return {
    id: "s1", title: "Calc", updated_at: "2026-10-25T10:00:00", running: false, waiting: false, archived: false,
    folder: "C:\\work\\calc", folder_name: "calc", is_project: false, project_id: null, mode: "act", plan: null,
    setup: {}, messages, session_id: "build:s1", queued: [], turn_id: "",
  };
}

const reply = (over: Record<string, unknown>) => ({
  role: "assistant", content: "Implemented evaluate.", turnId: "t1", steps, timing, ...over,
});

describe("a Build session's saved reply", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 200 })));
  });

  it("reads its finish check's sentence on the work line", async () => {
    api.gets = {
      "/build/sessions/s1": view([
        { role: "user", content: "implement calc", turnId: "t1" },
        reply({ finish: { tests: "failed", command: "pytest", removed: 1, kept: 0, points: 9, unmet: 1, after: "passed" } }),
      ]),
      "/build/accounts": new api.ApiError("no such route", 404),
    };
    render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    const line = await screen.findByTestId("work-line-finish");
    expect(line).toHaveTextContent("Checked: tests failed, then fixed, 1 scratch file removed, 1 point needed more work");
  });

  it("an older daemon's line (no finish) reads as before: no sentence", async () => {
    api.gets = {
      "/build/sessions/s1": view([{ role: "user", content: "implement calc", turnId: "t1" }, reply({})]),
      "/build/accounts": new api.ApiError("no such route", 404),
    };
    render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    await screen.findByTestId("work-line");
    expect(screen.queryByTestId("work-line-finish")).toBeNull();
  });
});
