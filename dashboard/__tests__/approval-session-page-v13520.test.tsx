/**
 * v1.352.0 integrate — the session page's ask speaks the one approval
 * vocabulary and says whether the action can be undone.
 *
 * The page builds its ApprovalCard from the session row's `waiting_on`. The
 * row here is the one the REAL daemon sent for a paused file save
 * (`fixtures/receipt-seams-v13520.json` `asks.waiting_on`, written by
 * tests/test_receipt_seams_v13520.py): an agent run's ask reads "Allow for
 * this run" (never "this conversation") and "You can undo this." — the same
 * words as the chat card, the bell, the mission card, a project's task and a
 * Build pane (receipt-seams-v13520.test.tsx). Harness: the real session page
 * with the transport mocked (cooldown-confinement-v1232's).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, within } from "@testing-library/react";

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
    // v1.250.0 (S-08): components animate with framer's slim `m.*` under the
    // layout's LazyMotion, so this mock must export `m` too — otherwise every
    // mocked surface throws "No \"m\" export is defined on the mock".
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
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

import fixture from "./fixtures/receipt-seams-v13520.json";
import SessionDetailPage from "@/app/sessions/[id]/page";
import { APPROVAL_LABELS, undoLine } from "@/lib/approvalWords";
import type { SessionView } from "@/lib/types";

afterEach(() => {
  cleanup();
  api.gets = [];
  api.posts = [];
  api.responses = {};
});

function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

function renderPaused(waitingOn: Record<string, unknown>) {
  const session = {
    id: "session_brief",
    task: "Write the nightly brief",
    agent_type: "builder",
    provider: "mock",
    model: "mock-model",
    status: "active",
    workspace_path: "C:/w",
    summary: "",
    created_at: "2026-10-18T09:00:00Z",
    finished_at: null,
    waiting_on: waitingOn,
  } as unknown as SessionView;
  api.responses["/sessions/session_brief"] = { session, transcript: { runs: [], tools: [] } };
  return render(<SessionDetailPage params={fakeParams("session_brief")} />);
}

describe("sessions/[id] — a paused run's ask", () => {
  it("answers in the run's words and says the save can be undone (the daemon's own row)", async () => {
    renderPaused(fixture.asks.waiting_on);
    await act(async () => {});
    const card = within(screen.getByTestId("session-approval")).getByTestId("chat-approval-card");
    const names = within(card).getAllByRole("button").map((b) => (b.textContent ?? "").trim());
    expect(names).toEqual([APPROVAL_LABELS.once, APPROVAL_LABELS.run, APPROVAL_LABELS.deny]);
    expect(card.textContent).not.toMatch(/conversation/);
    expect(within(card).getByTestId("approval-undo").textContent).toBe(undoLine(true));
  });

  it("a row from an older daemon (no verdict) shows no undo line", async () => {
    const { reversible: _drop, ...older } = fixture.asks.waiting_on;
    void _drop;
    renderPaused(older);
    await act(async () => {});
    const card = within(screen.getByTestId("session-approval")).getByTestId("chat-approval-card");
    expect(within(card).queryByTestId("approval-undo")).toBeNull();
  });
});
