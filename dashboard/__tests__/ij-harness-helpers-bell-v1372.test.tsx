// v1.372.0 REVIEW — a Build session's HELPER asking in the bell opens THAT
// Build session.
//
// A helper agent a Build turn started runs under its own session id (origin
// "job:build-helper"); its ask is listed by GET /chat/approvals/pending with
// `build_session` (the Build session's thread id, from the helper's run
// options). The bell row's links used to open `/sessions/<helper>` — a page
// the user never asked for — while the helper's row, with this same question
// on it, is in Build. Now the row says "Open the Build session" and opens
// `/terminals?session=<id>`; every other run's row is unchanged.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
}));

function apiState(path: string | null) {
  return { data: path ? (H.api[path] ?? null) : null, error: null, loading: false, reload: () => {} };
}

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => apiState(path),
  usePolledApi: (path: string | null) => apiState(path),
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    cancelled = false;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError,
    API_BASE: "",
    ijToken: () => "",
    sseUrl: (p: string) => p,
    get: (path: string) => Promise.resolve(H.api[path] ?? {}),
    post: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/daemon", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    epoch: 0,
    refresh: () => {},
    provided: true,
    health: { status: "ok", version: "1.372.0", default_provider: "", default_model: "", providers: [] },
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: () => {},
  }),
}));
vi.mock("@/lib/useDocumentVisible", () => ({ useDocumentVisible: () => true }));
vi.mock("@/lib/useReviews", () => ({
  useReviews: () => ({ reviews: {}, loading: false, reload: () => {} }),
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "whileHover"]);
  const cache = new Map<string, (props: Record<string, unknown>) => unknown>();
  const tagFor = (tag: string) => {
    let c = cache.get(tag);
    if (!c) {
      c = (props: Record<string, unknown>) => {
        const rest: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
        return createElement(tag, rest);
      };
      cache.set(tag, c);
    }
    return c;
  };
  return {
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: unknown }) => createElement(Fragment, null, children as never),
    useReducedMotion: () => true,
    motion: new Proxy({} as Record<string, unknown>, { get: (_t, tag) => tagFor(String(tag)) }),
  };
});

import { NotificationBell } from "@/components/NotificationBell";

const ASK = {
  id: "apr_helper01",
  tool: "shell",
  session_id: "session_helper1",
  requested_at: "2026-10-22T10:00:00",
  mission_id: null,
  project_id: null,
  task: "Run the tests",
  origin: "job:build-helper",
};

async function openBellRow() {
  render(<NotificationBell />);
  fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
  return await screen.findByTestId("bell-agent-approval");
}

beforeEach(() => {
  H.api = {};
});
afterEach(() => {
  cleanup();
});

describe("a Build session's helper asking in the bell", () => {
  it("opens THAT Build session (its row and card are there)", async () => {
    H.api["/chat/approvals/pending"] = { approvals: [{ ...ASK, build_session: "bs_42" }] };
    const row = await openBellRow();
    const link = within(row).getByTestId("bell-approval-build");
    expect(link.textContent).toMatch(/open the build session/i);
    expect(link.getAttribute("href")).toBe("/terminals?session=bs_42");
    // It is still answered from here.
    expect(within(row).getByRole("button", { name: "Allow once" })).toBeTruthy();
    // ...and says whose ask it is in words.
    expect(row.textContent).toMatch(/A helper in your Build session/);
    // v1.404.1: the wider answer is the coding SESSION's (every helper of it).
    expect(within(row).getByRole("button", { name: "Allow for this session" })).toBeTruthy();
    expect(within(row).queryByRole("button", { name: "Allow for this run" })).toBeNull();
  });

  it("control: any other run's row has no Build link (an older daemon sends no key)", async () => {
    H.api["/chat/approvals/pending"] = { approvals: [ASK] };
    const row = await openBellRow();
    expect(within(row).queryByTestId("bell-approval-build")).toBeNull();
    expect(row.querySelector('a[href="/sessions/session_helper1"]')).not.toBeNull();
    // ...and its wider answer stays the run's.
    expect(within(row).getByRole("button", { name: "Allow for this run" })).toBeTruthy();
  });
});
