/**
 * v1.360.1 (product-readiness G21, the last remainder): THE RUN PAGE SAYS
 * WHETHER THE WORK STAYED ON THIS NETWORK.
 *
 * After v1.360.0 the run page named the machine ("DGX Spark · llama-3-70b")
 * but never said "local" or "cloud", while the chat receipt under a reply
 * does (TurnReceipt.replyWhere). ACCEPTANCE: a finished run's Model line ends
 * in the SAME words ("· local" / "· cloud") by the SAME predicate (the run's
 * own receipt `on_this_network`, else the /models row for its provider +
 * model), says nothing when that is unknown, and never for the demo model.
 *
 * Harness: the REAL session page (app/sessions/[id]/page.tsx) with only the
 * transport mocked (run-page-v13540's). The receipt is the read SessionFiles
 * already makes (GET /sessions/{id}/result); the page adds no request of its
 * own, and that is pinned too.
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
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? api.responses[path] ?? null : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? api.responses[path] ?? null : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));
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
import { replyWhere, WHERE_LOCAL_TITLE } from "@/components/chat/TurnReceipt";
import { runWhere } from "@/components/sessions/runPageWords";
import type { SessionView } from "@/lib/types";

const ID = "session_where_13601";
const WS = "C:/Users/me/Documents/Iron Jarvis/2026-10-20 where";
const RESULT = `/sessions/${ID}/result`;

function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

function setRun(over: Partial<SessionView> & Record<string, unknown> = {}) {
  const session = {
    id: ID,
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
  api.responses[`/sessions/${ID}`] = {
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
  api.responses[RESULT] = {
    found: true,
    session_id: ID,
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
  api.responses = {};
});

describe("the run page says whether the work stayed on this network", () => {
  it("a local run: the Model line ends '· local', in the chat receipt's words", async () => {
    setRun({ provider: "fleet-a1b2c3", model: "llama-3-70b" });
    api.responses["/models"] = {
      models: [{ provider: "fleet-a1b2c3", model: "llama-3-70b", name: "DGX Spark" }],
    };
    setReceipt("fleet-a1b2c3", true);
    open();
    await waitFor(() => expect(modelValue().textContent).toBe("DGX Spark · llama-3-70b · local"));
    const where = screen.getByTestId("run-where");
    // The receipt's own words on hover, not a second table.
    expect(where.getAttribute("title")).toBe(WHERE_LOCAL_TITLE);
    // Quiet, in the receipt's zinc; no box.
    expect(where.className).toBe("text-zinc-500");
    // The raw stamp is still the record, on the model's own words.
    expect(modelValue().querySelector("[title]")?.getAttribute("title")).toBe("fleet-a1b2c3 / llama-3-70b");
    // No request of its own: the receipt is SessionFiles' one read.
    expect(api.gets.filter((p) => p === RESULT)).toHaveLength(1);
  });

  it("a cloud run: the Model line ends '· cloud', with the receipt's cloud words on hover", async () => {
    setRun({ provider: "openai", model: "gpt-x" });
    setReceipt("openai", false);
    open();
    await waitFor(() => expect(modelValue().textContent).toBe("OpenAI · gpt-x · cloud"));
    expect(screen.getByTestId("run-where").getAttribute("title")).toBe(
      replyWhere({ provider: "openai", on_this_network: false })?.title,
    );
    expect(modelValue().textContent).not.toContain("local");
  });

  it("the run's own receipt decides over the catalog row", async () => {
    // The menu's row says local; the run's receipt (judged for this very
    // provider) says cloud. The receipt is what the run did.
    setRun({ provider: "custom", model: "glm-4.6" });
    api.responses["/models"] = {
      models: [{ provider: "custom", model: "glm-4.6", on_this_network: true }],
    };
    setReceipt("custom", false);
    open();
    await waitFor(() => expect(screen.getByTestId("run-where").textContent).toBe(" · cloud"));
  });

  it("with no receipt, the /models row for the run's provider + model decides", async () => {
    setRun({ provider: "anthropic", model: "claude-opus-5-5" });
    api.responses["/models"] = {
      models: [
        { provider: "anthropic", model: "claude-opus-5-5", on_this_network: false },
        { provider: "ollama", model: "qwen3:8b", on_this_network: true },
      ],
    };
    open();
    await waitFor(() => expect(screen.getByTestId("run-where").textContent).toBe(" · cloud"));
    cleanup();
    setRun({ provider: "ollama", model: "qwen3:8b" });
    open();
    await waitFor(() => expect(screen.getByTestId("run-where").textContent).toBe(" · local"));
  });

  it("a receipt judged for ANOTHER provider (read before the served pair was written) is not used", async () => {
    setRun({ provider: "anthropic", model: "claude-opus-5-5" });
    api.responses["/models"] = {
      models: [{ provider: "anthropic", model: "claude-opus-5-5", on_this_network: false }],
    };
    setReceipt("ollama", true);
    open();
    await screen.findByTestId("session-files");
    await waitFor(() => expect(screen.getByTestId("run-where").textContent).toBe(" · cloud"));
  });

  it("unknown says NEITHER word: no flag on the receipt, a row without one (never guessed from `kind`)", async () => {
    setRun({ provider: "fleet-a1b2c3", model: "llama-3-70b" });
    api.responses["/models"] = {
      models: [{ provider: "fleet-a1b2c3", model: "llama-3-70b", name: "DGX Spark", kind: "local" }],
    };
    setReceipt("fleet-a1b2c3", null);
    open();
    // The receipt has landed (its file row is on screen) before the absence
    // is judged.
    await screen.findByTestId("session-files");
    expect(modelValue().textContent).toBe("DGX Spark · llama-3-70b");
    expect(screen.queryByTestId("run-where")).toBeNull();
    expect(modelValue().textContent).not.toMatch(/local|cloud/);
  });

  it("the demo model says NEITHER word, whatever the receipt and the row say", async () => {
    setRun({ provider: "mock", model: "mock-1" });
    api.responses["/models"] = {
      models: [{ provider: "mock", model: "mock-1", on_this_network: true }],
    };
    setReceipt("mock", true);
    open();
    await screen.findByTestId("session-files");
    expect(modelValue().textContent).toBe("Demo model (scripted)");
    expect(screen.queryByTestId("run-where")).toBeNull();
    expect(modelValue().textContent).not.toMatch(/local|cloud/);
  });

  it("a run still going says nothing yet (its served model is written when it ends)", async () => {
    setRun({ provider: "openai", model: "gpt-x", status: "running", finished_at: null });
    api.responses["/models"] = {
      models: [{ provider: "openai", model: "gpt-x", on_this_network: false }],
    };
    setReceipt("openai", false);
    open();
    await screen.findByTestId("session-files");
    expect(modelValue().textContent).toBe("OpenAI · gpt-x");
    expect(screen.queryByTestId("run-where")).toBeNull();
  });
});

describe("runWhere (the page's helper) is replyWhere's rule, not a second one", () => {
  it("hands replyWhere the flag and returns ITS words", () => {
    expect(runWhere("openai", "gpt-x", [], { provider: "openai", on_this_network: false })).toEqual(
      replyWhere({ provider: "openai", model: "gpt-x", on_this_network: false }),
    );
    expect(runWhere("ollama", "qwen3:8b", [{ provider: "ollama", model: "qwen3:8b", on_this_network: true }])?.tail).toBe(
      "local",
    );
    expect(runWhere("mock", "mock-1", [], { provider: "mock", on_this_network: true })).toBeNull();
    expect(runWhere("ollama", "qwen3:8b", [{ provider: "ollama", model: "qwen3:8b" }])).toBeNull();
    expect(runWhere("", "", [], { provider: "", on_this_network: true })).toBeNull();
  });
});
