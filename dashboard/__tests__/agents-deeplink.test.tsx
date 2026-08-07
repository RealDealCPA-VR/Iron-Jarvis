import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * `/agents?thread=<id>` opens THAT round-table (v1.142.0).
 *
 * The palette's "In your conversations" lane sends round-table hits to this
 * URL. Before the page honoured the param it auto-selected the NEWEST thread
 * instead — so a search result for one conversation opened a different one,
 * with nothing on screen admitting it. That is the failure this file exists to
 * stop coming back, and it is invisible to every other test: the page looks
 * perfectly healthy while showing the wrong thing.
 */

const hooks = vi.hoisted(() => ({
  threads: [
{
      id: "r-newest",
      title: "Newest thread",
      participants: [],
      message_count: 2,
      updated_at: "2026-08-01T10:00:00Z",
    },
    {
      id: "r-older",
      title: "Older thread",
      participants: [],
      message_count: 1,
      updated_at: "2026-07-01T10:00:00Z",
    },
  ] as unknown[],
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
  post: () => Promise.resolve({}),
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

// The round-table itself is a live surface with its own polling; all this test
// needs from it is WHICH thread it was told to render.
vi.mock("@/components/agents/RoundTable", () => ({
  RoundTable: ({ threadId }: { threadId: string }) => (
    <div data-testid="round-table">{threadId}</div>
  ),
}));
vi.mock("@/components/agents/SetupCard", () => ({ SetupCard: () => null }));
vi.mock("@/components/agents/RosterStrip", () => ({ RosterStrip: () => null }));
vi.mock("@/components/agents/PanelPicker", () => ({ PanelPicker: () => null }));

import AgentsPage from "@/app/agents/page";

/** jsdom won't let you assign location.search, so replace the whole object. */
function atUrl(search: string) {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, search, href: `http://localhost/agents${search}` },
  });
}

const openThread = () => screen.queryByTestId("round-table")?.textContent;

beforeEach(() => {
  hooks.threads = [
{
      id: "r-newest",
      title: "Newest thread",
      participants: [],
      message_count: 2,
      updated_at: "2026-08-01T10:00:00Z",
    },
    {
      id: "r-older",
      title: "Older thread",
      participants: [],
      message_count: 1,
      updated_at: "2026-07-01T10:00:00Z",
    },
  ];
  atUrl("");
});
afterEach(cleanup);

describe("the round-table deep link", () => {
  it("opens the thread named in ?thread=, not the newest one", async () => {
    atUrl("?thread=r-older");
    render(<AgentsPage />);
    await waitFor(() => expect(openThread()).toBe("r-older"));
  });

  it("still auto-selects the newest thread when there is no ?thread=", async () => {
    render(<AgentsPage />);
    await waitFor(() => expect(openThread()).toBe("r-newest"));
  });

  it("ignores an unrelated query string", async () => {
    atUrl("?focus=add&tab=setup");
    render(<AgentsPage />);
    await waitFor(() => expect(openThread()).toBe("r-newest"));
  });

  it("opens an id the rail has never heard of rather than a different thread", async () => {
    // A conversation the poll has not caught up with (or one just deleted).
    // RoundTable reports a missing thread honestly; quietly substituting the
    // newest one is the exact lie this deep link was added to stop telling.
    atUrl("?thread=r-ghost");
    render(<AgentsPage />);
    await waitFor(() => expect(openThread()).toBe("r-ghost"));
  });

  it("hands the choice back to the auto-select once the deep link is spent", async () => {
    // Arriving by deep link must not disable the "never show a blank pane"
    // behaviour for the rest of the visit: delete the thread you arrived at and
    // the rail should advance, not sit on "pick a thread".
    atUrl("?thread=r-older");
    render(<AgentsPage />);
    await waitFor(() => expect(openThread()).toBe("r-older"));

    fireEvent.click(screen.getByLabelText("Delete Older thread")); // arm
    fireEvent.click(await screen.findByLabelText("Confirm delete")); // confirm
    await waitFor(() => expect(openThread()).toBe("r-newest"));
  });

  it("url-encoded ids survive the round trip", async () => {
    atUrl(`?thread=${encodeURIComponent("a b/c")}`);
    render(<AgentsPage />);
    await waitFor(() => expect(openThread()).toBe("a b/c"));
  });
});
