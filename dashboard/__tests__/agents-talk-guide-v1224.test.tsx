import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

/**
 * v1.224.0 — `/agents?talk=guide&ask=…`: the Help page's "Ask the Guide" lands
 * on the Agents page, which opens (or starts) the 1:1 thread with the built-in
 * Guide and prefills the composer with the question — never sends it. Pinned:
 *
 *  - with no existing 1:1 thread, ONE POST /agents/threads with exactly the
 *    builtin guide as participant, and the new thread becomes the open one;
 *  - with an existing 1:1 Guide thread, NO thread is created — it is reused;
 *  - the question reaches the round-table composer as `initialInput`;
 *  - the params are stripped from the URL so a refresh does not re-open.
 */

const hooks = vi.hoisted(() => ({
  threads: [] as unknown[],
  posts: [] as { path: string; body: unknown }[],
}));

vi.mock("@/lib/useApi", () => ({
  useApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
  usePolledApi: (path: string) => ({
    data: path === "/agents/threads" ? { threads: hooks.threads } : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

vi.mock("@/lib/api", () => ({
  get: () => Promise.resolve({}),
  post: (path: string, body: unknown) => {
    hooks.posts.push({ path, body });
    if (path === "/agents/threads") {
      return Promise.resolve({
        id: "r-guide-new",
        title: "Talk with guide",
        participants: [{ key: "builtin:guide", source: "builtin", name: "guide", role: "" }],
        message_count: 0,
        updated_at: "2026-09-02T10:00:00Z",
        messages: [],
      });
    }
    return Promise.resolve({});
  },
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
  ApiError: class ApiError extends Error {
    status = 0;
  },
}));

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "whileHover"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  return {
    AnimatePresence: ({ children }: { children?: unknown }) =>
      createElement(Fragment, null, children as never),
    motion: new Proxy({} as Record<string, unknown>, { get: (_t, tag) => tagFor(String(tag)) }),
  };
});

vi.mock("@/components/agents/RoundTable", () => ({
  RoundTable: ({ threadId, initialInput }: { threadId: string; initialInput?: string }) => (
    <div data-testid="round-table" data-initial={initialInput ?? ""}>
      {threadId}
    </div>
  ),
}));
vi.mock("@/components/agents/SetupCard", () => ({ SetupCard: () => null }));
vi.mock("@/components/agents/RosterStrip", () => ({ RosterStrip: () => null }));
vi.mock("@/components/agents/PanelPicker", () => ({ PanelPicker: () => null }));

import AgentsPage from "@/app/agents/page";

function atUrl(search: string) {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, search, href: `http://localhost/agents${search}` },
  });
}

const openThread = () => screen.queryByTestId("round-table");

beforeEach(() => {
  hooks.posts = [];
  hooks.threads = [
    {
      id: "r-other",
      title: "Some other thread",
      participants: [{ key: "builtin:builder", source: "builtin", name: "builder", role: "" }],
      message_count: 2,
      updated_at: "2026-08-01T10:00:00Z",
    },
  ];
  window.history.replaceState = vi.fn();
  // jsdom elements have no scrollIntoView; Talk scrolls the table into view.
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

describe("/agents?talk=guide", () => {
  it("starts a 1:1 thread with the builtin Guide and prefills the question", async () => {
    atUrl("?talk=guide&ask=where%20is%20my%20month-end%20workflow");
    render(<AgentsPage />);
    await waitFor(() =>
      expect(hooks.posts.filter((p) => p.path === "/agents/threads")).toHaveLength(1),
    );
    const body = hooks.posts[0].body as { participants: { source: string; name: string }[] };
    expect(body.participants).toEqual([{ source: "builtin", name: "guide", role: "" }]);
    await waitFor(() => expect(openThread()?.textContent).toBe("r-guide-new"));
    expect(openThread()).toHaveAttribute("data-initial", "where is my month-end workflow");
    // Nothing was sent: no /say.
    expect(hooks.posts.some((p) => p.path.endsWith("/say"))).toBe(false);
    // The params are stripped so a refresh does not re-open.
    expect(window.history.replaceState).toHaveBeenCalled();
  });

  it("reuses an existing 1:1 Guide thread instead of creating another", async () => {
    hooks.threads = [
      ...hooks.threads,
      {
        id: "r-guide-old",
        title: "Talk with guide",
        participants: [{ key: "builtin:guide", source: "builtin", name: "guide", role: "" }],
        message_count: 4,
        updated_at: "2026-08-15T10:00:00Z",
      },
    ];
    atUrl("?talk=guide");
    render(<AgentsPage />);
    await waitFor(() => expect(openThread()?.textContent).toBe("r-guide-old"));
    expect(hooks.posts.filter((p) => p.path === "/agents/threads")).toHaveLength(0);
  });

  it("without ?talk= nothing is created", async () => {
    atUrl("");
    render(<AgentsPage />);
    await waitFor(() => expect(openThread()).not.toBeNull());
    expect(hooks.posts.filter((p) => p.path === "/agents/threads")).toHaveLength(0);
  });
});
