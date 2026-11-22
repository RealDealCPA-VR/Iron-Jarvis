/**
 * v1.554.0 (next leaps 14-a, master plan R2.6): THE RUN PAGE LISTS WHAT THE
 * RUN WAS GIVEN.
 *
 * An agent run in a folder is now told what was done there before ("Earlier
 * work on record", the block a chat in that folder is told). The daemon keeps
 * that on the run's row (`Session.told_json`) and GET /sessions/{id} serves
 * it as `told: ["work"]`. ACCEPTANCE: the run's page shows one quiet line,
 * "Started with · Earlier work on record", only when the run was given it;
 * nothing for a run given nothing, an older daemon (no `told`), or a part it
 * has no words for (never an id on screen).
 *
 * Harness: the REAL session page with only the transport mocked
 * (run-page-where-v13601's).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return { responses: {} as Record<string, unknown>, FakeApiError };
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
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/lib/useApi", () => {
  const read = (path: string | null) => ({
    data: path ? api.responses[path] ?? null : null,
    error: null,
    loading: false,
    reload: () => {},
  });
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

import SessionDetailPage from "@/app/sessions/[id]/page";
import { STARTED_WITH_LABEL, TOLD_WORDS, startedWith } from "@/components/sessions/runPageWords";
import type { SessionView } from "@/lib/types";

const ID = "session_told_1548";
const WS = "C:/Users/me/Documents/Price lists";

function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

function setRun(over: Record<string, unknown> = {}) {
  const session = {
    id: ID,
    task: "Update the price list for Wednesday",
    agent_type: "builder",
    provider: "openai",
    model: "gpt-x",
    status: "completed",
    workspace_path: WS,
    summary: "Updated the price list.",
    created_at: "2026-11-20T09:00:00.000000",
    finished_at: "2026-11-20T09:02:05.000000",
    ...over,
  } as unknown as SessionView;
  api.responses[`/sessions/${ID}`] = {
    session,
    transcript: {
      runs: [{ id: "run_1", agent_type: "builder", state: "completed", steps: 2, parent_id: null, result: "done" }],
      tools: [],
    },
  };
}

function open() {
  return render(<SessionDetailPage params={fakeParams(ID)} />);
}

beforeEach(() => {
  H.run = { text: "", tools: [], phase: null, active: false };
  H.events = [];
});
afterEach(() => {
  cleanup();
  api.responses = {};
});

describe("the run page lists what the run was given", () => {
  it("a run told the folder's earlier work: one quiet line, in the record's words", async () => {
    setRun({ told: ["work"] });
    open();
    const line = await screen.findByTestId("run-started-with");
    expect(line.textContent).toBe("Earlier work on record");
    // Its label, as the other facts of the run (Agent, Model, Folder).
    expect(line.parentElement?.previousElementSibling?.textContent).toBe(STARTED_WITH_LABEL);
    expect(STARTED_WITH_LABEL).toBe("Started with");
  });

  it("a run given nothing, an older daemon, or a part with no words: no line", async () => {
    for (const told of [[], undefined, ["secret_part"], "work", [7]]) {
      cleanup();
      setRun(told === undefined ? {} : { told });
      open();
      // The page drew the run (its Folder fact) before the check.
      await waitFor(() => expect(screen.getByText("Folder", { selector: "div" })).toBeTruthy());
      expect(screen.queryByTestId("run-started-with")).toBeNull();
      expect(screen.queryByText(STARTED_WITH_LABEL)).toBeNull();
    }
  });

  it("the words come from one table, in the daemon's order, never an id", () => {
    expect(startedWith({ told: ["work", "work"] })).toEqual([TOLD_WORDS.work]);
    expect(startedWith({ told: ["toString", "__proto__", "constructor"] })).toEqual([]);
    expect(startedWith(null)).toEqual([]);
    expect(startedWith({})).toEqual([]);
  });
});
