/**
 * v1.232.0, audit Wave 6 (R4 + T5) — two states the user could not see.
 *
 *  - R4: the router's circuit breaker gates the PRIMARY now and refuses a turn
 *    with "in cooldown, retry in N s". `/health` carries the same row
 *    (`circuit: {open, retry_in_s}`), so the PreflightNote says it ABOVE the
 *    composer — before the user types a paragraph into a model that will not
 *    be asked. Unreachable still wins over cooldown when both are true: "it is
 *    not there" is the bigger fact.
 *  - T5: with Docker unavailable the shell falls back to the native runtime and
 *    the sandbox policy is advisory, not enforced. That used to live only in a
 *    paragraph inside the first shell result; the ledger rows now carry
 *    `confinement`, and the session page folds them into ONE chip.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

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
    posts: [] as { path: string; body: unknown }[],
    responses: {} as Record<string, unknown>,
    FakeApiError,
  };
});

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
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    return Promise.resolve({});
  },
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
  useRunStream: () => ({
    text: "",
    tools: [],
    phase: null,
    active: false,
    start: () => {},
    stop: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [] }) }));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    enabled: false,
    supported: false,
    toggle: () => {},
    speak: () => {},
    stop: () => {},
  }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: () => {} }) }));
vi.mock("@/components/ReviewPanel", () => ({ ReviewPanel: () => null }));
vi.mock("@/components/TracesPanel", () => ({ TracesPanel: () => null }));
vi.mock("@/components/SessionFeedback", () => ({ SessionFeedback: () => null }));
vi.mock("@/components/TimeTravelFeed", () => ({ TimeTravelFeed: () => null }));
vi.mock("@/components/chat/DocPreview", () => ({
  DocPreview: ({ path }: { path: string }) => <div data-testid="doc-preview">{path}</div>,
  appLabelFor: () => "app",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({
      href,
      children,
      ...rest
    }: {
      href: string;
      children?: React.ReactNode;
    }) => createElement("a", { href, ...rest }, children),
  };
});
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial",
    "animate",
    "exit",
    "transition",
    "variants",
    "layout",
    "whileHover",
    "whileTap",
    "whileInView",
    "viewport",
  ]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) {
      if (!MOTION_ONLY.has(k)) rest[k] = v;
    }
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  return {
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

import { PreflightNote } from "@/components/chat/PreflightNote";
import SessionDetailPage from "@/app/sessions/[id]/page";
import type { SessionView, ToolInvocation } from "@/lib/types";

afterEach(() => {
  cleanup();
  api.gets = [];
  api.posts = [];
  api.responses = {};
});

/* ------------------------------------------------- R4: the cooldown note */

describe("PreflightNote — a provider in cooldown", () => {
  it("says the seconds left, before the user types", () => {
    render(<PreflightNote provider="fleet-custom" available={true} cooldownS={23} />);
    const note = screen.getByTestId("ij-preflight-note");
    expect(note).toHaveAttribute("data-kind", "cooldown");
    expect(note).toHaveTextContent("fleet-custom is in cooldown, retry in 23 s");
  });

  it("stays silent for a healthy provider with a closed circuit", () => {
    render(<PreflightNote provider="fleet-custom" available={true} cooldownS={0} />);
    expect(screen.queryByTestId("ij-preflight-note")).not.toBeInTheDocument();
    cleanup();
    render(<PreflightNote provider="fleet-custom" available={true} />);
    expect(screen.queryByTestId("ij-preflight-note")).not.toBeInTheDocument();
  });

  it("unreachable wins over cooldown when both are true", () => {
    render(<PreflightNote provider="fleet-custom" available={false} cooldownS={23} />);
    const note = screen.getByTestId("ij-preflight-note");
    expect(note).toHaveAttribute("data-kind", "unreachable");
    expect(note).toHaveTextContent("isn't reachable right now");
    expect(note).not.toHaveTextContent("cooldown");
  });
});

/* ------------------------------------- T5: the per-session confinement chip */

function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

function tool(over: Partial<ToolInvocation> = {}): ToolInvocation {
  return {
    id: `tool_${Math.random().toString(16).slice(2)}`,
    session_id: "s-1",
    agent_run_id: "r-1",
    tool: "shell",
    args_json: "{}",
    verdict: "allow",
    ok: true,
    output: "hi",
    created_at: "2026-09-04T10:00:00Z",
    ...over,
  };
}

function renderPage(tools: ToolInvocation[]) {
  const session: SessionView = {
    id: "s-1",
    task: "run a build",
    agent_type: "coder",
    provider: "mock",
    model: "mock-model",
    status: "completed",
    workspace_path: "C:/w",
    summary: "",
    created_at: "2026-09-04T10:00:00Z",
    finished_at: "2026-09-04T10:05:00Z",
  };
  api.responses["/sessions/s-1"] = { session, transcript: { runs: [], tools } };
  return render(<SessionDetailPage params={fakeParams("s-1")} />);
}

describe("sessions/[id] — Shell ran unconfined", () => {
  it("wears ONE chip when any call ran native-unconfined, however many did", async () => {
    renderPage([
      tool({ confinement: "native-unconfined" }),
      tool({ confinement: "native-unconfined" }),
      tool({ tool: "read_file", confinement: null }),
    ]);
    await act(async () => {});
    const chips = screen.getAllByTestId("confinement-chip");
    expect(chips).toHaveLength(1);
    expect(chips[0]).toHaveTextContent("Shell ran unconfined (Docker unavailable)");
  });

  it("renders nothing when every call was sandboxed or had no runtime to report", async () => {
    renderPage([tool({ confinement: "sandbox" }), tool({ tool: "read_file" })]);
    await act(async () => {});
    expect(screen.queryByTestId("confinement-chip")).not.toBeInTheDocument();
  });
});
