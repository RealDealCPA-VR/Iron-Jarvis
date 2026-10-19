/**
 * v1.358.0 (wave 8, lane 8C) — the Sessions page's bulk delete asks first
 * with a CLEAR danger cue on the armed press.
 *
 * The regression (re-audit, minor): when ConfirmButton went calm by default
 * (v1.356.0, G80), the Sessions page kept its old override
 * `!text-zinc-400 hover:!text-accent-soft`, which painted over the ARMED
 * press too: "Delete them all?" read grey on a faint red fill, hovering cyan.
 * Now the page's neutral look rides `idleClassName` (the first press only)
 * and the armed press wears the calm danger cue (tone-danger text on its
 * tone-danger fill — theme tokens, no border).
 *
 * Real page, real ConfirmButton; only the network is mocked (the
 * labels-v13560 harness).
 */
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    FakeApiError,
    responses: {} as Record<string, unknown>,
    posts: [] as { path: string; body?: unknown }[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  sseUrl: (p: string) => p,
  get: (path: string) => {
    const r = H.responses[path];
    return r === undefined ? Promise.reject(new H.FakeApiError(`unmocked GET ${path}`, 404)) : Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    H.posts.push({ path, body });
    if (path === "/sessions/clear") return Promise.resolve({ cleared: 3 });
    return Promise.resolve({});
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (H.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (H.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));
vi.mock("@/lib/useReviews", () => ({ useReviews: () => ({ reviews: {}, loading: false, reload: () => {} }) }));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useDocumentVisible", () => ({ useDocumentVisible: () => true }));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], loading: false, error: null, reload: () => {} }) }));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: null, refresh: () => {}, epoch: 0 }),
}));
vi.mock("@/components/NewSessionForm", () => ({ NewSessionForm: () => null, NEW_SESSION_TASK_ID: "new-task" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/sessions",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial", "animate", "exit", "transition", "variants", "layout",
    "whileHover", "whileTap", "whileInView", "viewport",
  ]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  return {
    // v1.250.0 mock contract: every framer-motion mock exports `m`.
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: ReactNode }) => createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

import SessionsPage from "@/app/sessions/page";
import { CALM_CONFIRM, ConfirmButton } from "@/components/ui";
import type { SessionView } from "@/lib/types";

function sv(over: Partial<SessionView> = {}): SessionView {
  return {
    id: "s-1",
    task: "triage the inbox",
    agent_type: "coder",
    provider: "mock",
    model: "mock-model",
    status: "completed",
    workspace_path: "C:/w",
    summary: "",
    created_at: "2026-10-01T10:00:00Z",
    finished_at: null,
    ...over,
  } as SessionView;
}

const tokens = (el: Element) => (el.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);

beforeEach(() => {
  H.responses = {};
  H.posts.length = 0;
});

afterEach(() => {
  cleanup();
});

describe("the Sessions bulk delete's ARMED press reads as danger (calm, tone token)", () => {
  it("first press: the page's neutral look; second (armed) press: tone-danger text on its fill, nothing grey painted over it", async () => {
    H.responses["/sessions"] = { sessions: [sv()] };
    H.responses["/projects"] = { projects: [] };
    render(<SessionsPage />);
    const clear = screen.getByRole("button", { name: /Clear finished, failed and stopped/ });
    // Idle: the page action rests neutral and hovers in the accent (as before).
    expect(clear.getAttribute("data-armed")).toBe("false");
    expect(tokens(clear)).toContain("hover:!text-accent-soft");
    expect(tokens(clear)).toContain("text-zinc-400");
    fireEvent.click(clear);
    const armed = screen.getByRole("button", { name: /Delete them all\?/ });
    expect(armed.getAttribute("data-armed")).toBe("true");
    // The armed press wears the calm danger cue, and NO override paints over it.
    const cls = tokens(armed);
    for (const c of CALM_CONFIRM.armed.split(/\s+/)) expect(cls, c).toContain(c);
    expect(cls).toContain("text-tone-danger");
    expect(cls.filter((c) => /^!text-|^hover:!text-/.test(c))).toEqual([]);
    expect(cls).not.toContain("text-zinc-400");
    // Still two presses: nothing was cleared by the first.
    expect(H.posts.some((p) => p.path === "/sessions/clear")).toBe(false);
    fireEvent.click(armed);
    await waitFor(() => expect(H.posts.some((p) => p.path === "/sessions/clear")).toBe(true));
  });
});

describe("ConfirmButton's idleClassName styles only the unarmed press", () => {
  it("calm (the default): applied while idle, gone once armed", () => {
    render(<ConfirmButton onConfirm={() => {}} label="Clear" confirmLabel="Sure?" idleClassName="hover:!text-accent-soft" />);
    const idle = screen.getByRole("button", { name: "Clear" });
    expect(tokens(idle)).toContain("hover:!text-accent-soft");
    fireEvent.click(idle);
    const armed = screen.getByRole("button", { name: "Sure?" });
    expect(tokens(armed)).not.toContain("hover:!text-accent-soft");
    expect(tokens(armed)).toContain("text-tone-danger");
  });

  it("bordered: the same rule; without idleClassName the class string is exactly what it was", () => {
    render(
      <ConfirmButton variant="bordered" onConfirm={() => {}} label="Close" confirmLabel="Close it?" idleClassName="opacity-90" />,
    );
    const idle = screen.getByRole("button", { name: "Close" });
    expect(tokens(idle)).toContain("opacity-90");
    fireEvent.click(idle);
    expect(tokens(screen.getByRole("button", { name: "Close it?" }))).not.toContain("opacity-90");
    cleanup();
    render(<ConfirmButton variant="bordered" onConfirm={() => {}} label="Delete" />);
    expect(screen.getByRole("button", { name: "Delete" }).className).toBe(
      "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-50 border-white/10 text-zinc-400 hover:border-tone-danger/30 hover:text-tone-danger ",
    );
  });
});
