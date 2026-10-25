/**
 * Readiness 12A — a failed RUN reads in plain words on every surface, with the
 * raw text folded (G28), and the run page agrees with the receipt on where a
 * dispatched-then-failed run went (0f.3).
 *
 * G28: `failure_words.plain_reason` fell back to the failure's own text, so a
 * run that ended on an untyped CLI error read "The run failed: codex-cli: CLI
 * exited 1: error: unexpected status 400 Bad Request: model is not supported"
 * on its run page, its Schedules row and the bell. The daemon now writes one
 * plain sentence ("Codex couldn't finish this task. …") and keeps the raw text
 * apart (`Session.failure_detail`, GET /sessions/{id} only), which the run
 * page folds under "Details".
 *
 * 0f.3: the run page decided "local / cloud" from its transcript alone, so a
 * cloud call that went out and then failed before any step read "cloud" on
 * the receipt and nothing on the page. The receipt now carries the daemon's
 * own `reached_a_model`.
 *
 * Both halves are fed the REAL daemon's answers (contract fixtures written by
 * tests/test_run_failure_words_v1387.py and tests/test_run_where_dispatched_v1387.py)
 * through the REAL run page, the real Schedules words and the real bell.
 * Harness: run-page-reached-v13610's, copied (a test file's mocks are its own).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    gets: [] as string[],
    // Every path the page's data hooks read (useApi / usePolledApi): a
    // second read of the receipt through a hook is a request too.
    hooks: [] as string[],
    responses: {} as Record<string, unknown>,
    FakeApiError,
  };
});
const H = vi.hoisted(() => ({
  run: {
    text: "",
    tools: [] as Record<string, unknown>[],
    phase: null as null | { phase: string; detail: string },
    active: false,
  },
  events: [] as Record<string, unknown>[],
  push: vi.fn(),
  back: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    api.gets.push(path);
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    }
    // A function is a read the test holds: it answers when the test says so.
    return typeof r === "function" ? (r as () => Promise<unknown>)() : Promise.resolve(r);
  },
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/lib/useApi", () => {
  const read = (path: string | null) => {
    if (path) api.hooks.push(path);
    return {
      data: path ? api.responses[path] ?? null : null,
      error: null,
      loading: false,
      reload: () => {},
    };
  };
  return { useApi: read, usePolledApi: read };
});
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ ...H.run, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: H.events }) }));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({ enabled: false, supported: false, toggle: () => {}, speak: () => {}, stop: () => {} }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: H.push, back: H.back }) }));
vi.mock("@/components/ReviewPanel", () => ({ ReviewPanel: () => null }));
vi.mock("@/components/TracesPanel", () => ({ TracesPanel: () => null }));
vi.mock("@/components/SessionFeedback", () => ({ SessionFeedback: () => null }));
vi.mock("@/components/TimeTravelFeed", () => ({
  TimeTravelFeed: () => <div data-testid="time-travel-feed" />,
}));
vi.mock("@/components/chat/DocPreview", () => ({ DocPreview: () => null, appLabelFor: () => "app" }));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "layout"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  return {
    // Mock contract (v1.250.0): every framer-motion mock exports `m`.
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

import { readFileSync } from "node:fs";
import { join } from "node:path";
import SessionDetailPage from "@/app/sessions/[id]/page";
import { failureDetailShown, runReachedAModel } from "@/components/sessions/runPageWords";
import { scheduleDetail, failureReason } from "@/lib/scheduleOutcome";
import { toActivity } from "@/components/NotificationBell";
import type { IJEvent } from "@/lib/types";

const fixture = (name: string) =>
  JSON.parse(readFileSync(join(__dirname, "fixtures", name), "utf-8").replace(/\r\n/g, "\n"));
const WORDS = fixture("run-failure-words-v1387.json");
const WHERE = fixture("run-where-dispatched-v1387.json");

/** The audit's raw text (G28), none of which a person may read outside the fold. */
const RAW = ["codex-cli", "CLI exited", "unexpected status", "Bad Request", "400"];

const ID = "session_g28_v1387";

function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

function open(id: string) {
  return render(<SessionDetailPage params={fakeParams(id)} />);
}

/** The failed run as GET /sessions/{id} answers it (the fixture's session, its id). */
function setFailedRun(over: Record<string, unknown> = {}) {
  api.responses[`/sessions/${ID}`] = {
    session: {
      id: ID,
      agent_type: "builder",
      workspace_path: "",
      created_at: "2026-10-25T09:00:00",
      finished_at: "2026-10-25T09:00:04",
      ...WORDS.session,
      ...over,
    },
    transcript: {
      runs: [{ id: "run_1", agent_type: "builder", state: "failed", steps: 0, parent_id: null, result: "" }],
      tools: [],
    },
  };
}

beforeEach(() => {
  H.run = { text: "", tools: [], phase: null, active: false };
  H.events = [];
});
afterEach(() => {
  cleanup();
  api.gets = [];
  api.hooks = [];
  api.responses = {};
});

describe("G28: a failed run's summary is plain words; the raw text is folded on the run page only", () => {
  it("the fixture is the daemon's real answer: a plain summary, the raw text apart", () => {
    expect(WORDS.session.status).toBe("failed");
    expect(WORDS.session.summary).toBe(
      "The run failed: Codex couldn't finish this task. Run it again. If it happens again, choose another model in Settings.",
    );
    for (const raw of RAW) expect(WORDS.session.summary).not.toContain(raw);
    expect(WORDS.session.failure_detail).toContain("codex-cli: CLI exited 1");
  });

  it("the run page shows the plain summary, and the raw text only inside a closed Details fold", async () => {
    setFailedRun();
    open(ID);
    const summary = await screen.findByTestId("session-summary");
    expect(summary.textContent).toContain("Codex couldn't finish this task.");
    for (const raw of RAW) expect(summary.textContent).not.toContain(raw);
    const fold = screen.getByTestId("session-failure-details") as HTMLDetailsElement;
    expect(fold.tagName).toBe("DETAILS");
    expect(fold.open).toBe(false);
    expect(fold.querySelector("summary")?.textContent).toBe("Details");
    expect(screen.getByTestId("session-failure-technical").textContent).toBe(WORDS.session.failure_detail);
  });

  it("an older daemon (no failure_detail) and a run that did not fail show no fold", async () => {
    setFailedRun({ failure_detail: undefined });
    open(ID);
    await screen.findByTestId("session-summary");
    expect(screen.queryByTestId("session-failure-details")).toBeNull();
    cleanup();
    setFailedRun({ status: "completed", summary: "Wrote the digest." });
    open(ID);
    await screen.findByTestId("session-summary");
    expect(screen.queryByTestId("session-failure-details")).toBeNull();
  });

  it("failureDetailShown: failed + present + not already said", () => {
    expect(failureDetailShown(WORDS.session)).toBe(WORDS.session.failure_detail);
    expect(failureDetailShown({ ...WORDS.session, status: "completed" })).toBeNull();
    expect(failureDetailShown({ ...WORDS.session, failure_detail: "" })).toBeNull();
    expect(failureDetailShown({ ...WORDS.session, failure_detail: 42 })).toBeNull();
    expect(failureDetailShown({ status: "failed", summary: "The run failed: boom", failure_detail: "boom" })).toBeNull();
    expect(failureDetailShown(null)).toBeNull();
    expect(failureDetailShown({ ...WORDS.session, failure_detail: "x".repeat(5000) })!.length).toBe(2000);
  });

  it("the Schedules row says the same plain sentence (no raw text)", () => {
    const row = WORDS.schedule_row;
    expect(row.last_status).toBe("error");
    expect(scheduleDetail(row.last_detail)).toBe(WORDS.session.summary);
    expect(failureReason(row.last_detail)).toBe(
      "Codex couldn't finish this task. Run it again. If it happens again, choose another model in Settings.",
    );
    for (const raw of RAW) expect(scheduleDetail(row.last_detail)).not.toContain(raw);
  });

  it("the bell's row for the run says it in the same words (no raw text)", () => {
    const item = toActivity({
      id: "ev_g28",
      ts: "2026-10-25T09:00:04Z",
      session_id: ID,
      type: WORDS.event.type,
      payload: WORDS.event.payload,
    } as unknown as IJEvent);
    expect(item).not.toBeNull();
    expect(item!.body).toContain("Codex couldn't finish this task.");
    for (const raw of RAW) expect(`${item!.title} ${item!.body}`).not.toContain(raw);
  });

  it("the phone's line says it too", () => {
    expect(WORDS.phone).toHaveLength(1);
    expect(WORDS.phone[0]).toContain("Codex couldn't finish this task.");
    for (const raw of RAW) expect(WORDS.phone[0]).not.toContain(raw);
  });
});

describe("0f.3: the run page agrees with the receipt on a dispatched-then-failed run", () => {
  function setFrom(which: "dispatched" | "refused", receipt: unknown) {
    const reads = WHERE[which];
    const id = reads.detail.session.id as string;
    api.responses[`/sessions/${id}`] = reads.detail;
    api.responses[`/sessions/${id}/result`] = receipt;
    api.responses["/models"] = {
      models: [{ provider: "openrouter", model: "meta/llama-3-8b", on_this_network: false }],
    };
    return id;
  }

  it("a cloud call that went out and failed before any step says cloud, as its receipt does", async () => {
    const id = setFrom("dispatched", WHERE.dispatched.result);
    expect(WHERE.dispatched.result.reached_a_model).toBe(true);
    const t = WHERE.dispatched.detail.transcript;
    expect(runReachedAModel(t.runs, t.tools)).toBe(false);
    open(id);
    await waitFor(() => expect(screen.getByTestId("run-where").textContent).toBe(" · cloud"));
  });

  it("a run the router refused before sending anything still says nothing", async () => {
    let answered = 0;
    const id = setFrom("refused", () => {
      answered += 1;
      return Promise.resolve(WHERE.refused.result);
    });
    expect(WHERE.refused.result.reached_a_model).toBe(false);
    open(id);
    await waitFor(() => expect(answered).toBeGreaterThan(0));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.queryByTestId("run-where")).toBeNull();
  });

  it("an older daemon's receipt (no reached_a_model) is not taken as proof", () => {
    const older: Record<string, unknown> = { ...WHERE.dispatched.result };
    delete older.reached_a_model;
    const t = WHERE.dispatched.detail.transcript;
    expect(runReachedAModel(t.runs, t.tools, older)).toBe(false);
    expect(runReachedAModel(t.runs, t.tools, { reached_a_model: "true" })).toBe(false);
    expect(runReachedAModel(t.runs, t.tools, WHERE.dispatched.result)).toBe(true);
    expect(runReachedAModel(t.runs, t.tools, null)).toBe(false);
  });
});
