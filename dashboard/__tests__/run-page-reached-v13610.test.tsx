/**
 * v1.361.0 lane 11C (5): the run page says "local" / "cloud" only for a run
 * whose work was SENT to a model.
 *
 * v1.360.1's ship guard read "an agent run exists in the transcript" as
 * "reached a model". But the AgentRun row is saved BEFORE the first model
 * call, so a run the router refused for an unconnected cloud provider had
 * one failed run, and the Model line gained "· cloud" with the hover "Ran in
 * the cloud: this message left your network for OpenRouter." — false. At
 * v1.359.0 the Model line said nothing about where the run went. Now the
 * page asks its own transcript (runPageWords.runReachedAModel: a step a model
 * answered, its tokens, or a tool call a model asked for), and the daemon's
 * receipt says null for the same run (agents/outcome.reached_a_model,
 * tests/test_run_where_reached_v13610.py).
 *
 * Harness: the REAL session page with only the transport mocked — the
 * run-page-where-v13601 harness, copied (not imported: a test file's mocks
 * are its own).
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

import SessionDetailPage from "@/app/sessions/[id]/page";
import { runReachedAModel } from "@/components/sessions/runPageWords";
import type { SessionView } from "@/lib/types";

const ID = "session_reached_13610";
const WS = "C:/Users/me/Documents/Iron Jarvis/2026-10-20 reached";
const RESULT = `/sessions/${ID}/result`;

function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

function setRun(over: Partial<SessionView> & Record<string, unknown> = {}) {
  setRunFor(ID, over);
}

function setRunFor(id: string, over: Partial<SessionView> & Record<string, unknown> = {}) {
  const session = {
    id,
    task: "Summarise the engagement letter",
    agent_type: "builder",
    provider: "openai",
    model: "gpt-x",
    status: "completed",
    workspace_path: WS,
    summary: "Wrote a one-page summary to summary.md",
    created_at: "2026-10-20T09:00:00.000000",
    finished_at: "2026-10-20T09:02:05.000000",
    ...over,
  } as unknown as SessionView;
  api.responses[`/sessions/${id}`] = {
    session,
    transcript: {
      runs: [{ id: "run_1", agent_type: "builder", state: "completed", steps: 3, parent_id: null, result: "done" }],
      tools: [{ id: "t_1", tool: "write_file", verdict: "allow", ok: true, output: "wrote summary.md" }],
    },
  };
}

/** The run's receipt as GET /sessions/{id}/result serves it (receipt=True).
 *  It carries one file so the files panel renders: its rows on screen are
 *  the proof the receipt LANDED, which the "says nothing" cases wait for. */
function setReceipt(provider: string, onThisNetwork: boolean | null) {
  api.responses[RESULT] = receiptFor(ID, provider, onThisNetwork);
}

function receiptFor(id: string, provider: string, onThisNetwork: boolean | null) {
  return {
    found: true,
    session_id: id,
    status: "completed",
    provider,
    model: "",
    on_this_network: onThisNetwork,
    files_created: ["summary.md"],
    files_changed: [],
    documents: [`${WS}/summary.md`],
  };
}

function open() {
  return render(<SessionDetailPage params={fakeParams(ID)} />);
}

/** The Model field's value element (the grid cell under the "Model" label). */
function modelValue(): HTMLElement {
  return screen.getByText("Model", { selector: "div" }).nextElementSibling as HTMLElement;
}

beforeEach(() => {
  H.run = { text: "", tools: [], phase: null, active: false };
  H.events = [];
  H.push.mockReset();
  H.back.mockReset();
});
afterEach(() => {
  cleanup();
  api.gets = [];
  api.hooks = [];
  api.responses = {};
});

const REFUSED_RUN = { id: "run_1", agent_type: "builder", state: "failed", steps: 0, input_tokens: 0, output_tokens: 0, parent_id: null, result: "" };

/** A failed run on OpenRouter whose transcript holds `runs` / `tools`. */
function setRefused(runs: Record<string, unknown>[], tools: Record<string, unknown>[] = []) {
  setRun({
    provider: "openrouter",
    model: "meta/llama-3-8b",
    status: "failed",
    summary: "openrouter isn't connected right now, so this turn was not answered.",
  });
  const detail = api.responses[`/sessions/${ID}`] as { transcript: { runs: unknown[]; tools: unknown[] } };
  detail.transcript.runs = runs;
  detail.transcript.tools = tools;
  // The menu's row says cloud (an OpenRouter model always leaves the network).
  api.responses["/models"] = {
    models: [{ provider: "openrouter", model: "meta/llama-3-8b", on_this_network: false }],
  };
}

describe("a run the router refused before sending anything claims neither local nor cloud", () => {
  it("one failed run, no step, no tokens, no tool call: no '· cloud', whatever the receipt or the row says", async () => {
    setRefused([REFUSED_RUN]);
    // An older Iron Jarvis judged the stored provider anyway (false); the
    // page must not take its word for a run that reached no model.
    setReceipt("openrouter", false);
    open();
    await screen.findByTestId("session-files");
    expect(screen.queryByTestId("run-where")).toBeNull();
    expect(modelValue().textContent).not.toMatch(/local|cloud/);
    expect(modelValue().textContent).toMatch(/meta\/llama-3-8b$/);
  });

  it("the daemon's own answer for that run (null) says nothing either", async () => {
    setRefused([REFUSED_RUN]);
    setReceipt("openrouter", null);
    open();
    await screen.findByTestId("session-files");
    expect(screen.queryByTestId("run-where")).toBeNull();
  });

  it("OTHER DIRECTION: a run a model answered still says where — by its tokens, its steps or a tool call", async () => {
    for (const [label, runs, tools] of [
      ["tokens", [{ ...REFUSED_RUN, input_tokens: 412 }], []],
      ["output tokens", [{ ...REFUSED_RUN, output_tokens: 3 }], []],
      ["a step", [{ ...REFUSED_RUN, steps: 1 }], []],
      ["a tool call", [REFUSED_RUN], [{ id: "t_1", tool: "read_file", verdict: "allow", ok: true, output: "" }]],
    ] as [string, Record<string, unknown>[], Record<string, unknown>[]][]) {
      setRefused(runs, tools);
      setReceipt("openrouter", false);
      open();
      await waitFor(() => expect(screen.getByTestId("run-where").textContent, label).toBe(" · cloud"));
      cleanup();
    }
  });
});

describe("runReachedAModel (the page's helper)", () => {
  it("a step, tokens or a tool call is a model call; an empty or refused transcript is not", () => {
    expect(runReachedAModel([REFUSED_RUN], [])).toBe(false);
    expect(runReachedAModel([], [])).toBe(false);
    expect(runReachedAModel(undefined, undefined)).toBe(false);
    expect(runReachedAModel([{ steps: "3" }, { input_tokens: Number.NaN }, { output_tokens: -1 }])).toBe(false);
    expect(runReachedAModel([REFUSED_RUN, { steps: 2 }])).toBe(true);
    expect(runReachedAModel([{ input_tokens: 1 }])).toBe(true);
    expect(runReachedAModel([{ output_tokens: 1 }])).toBe(true);
    expect(runReachedAModel([REFUSED_RUN], [{ id: "t" }])).toBe(true);
  });
});
