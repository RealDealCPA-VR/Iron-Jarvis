/**
 * v1.393.0 REVIEW — the bell's rows for a coding reply that WAITS for its
 * account's reset by itself, and one carried on by ANOTHER account of its
 * group, under the one-notice rules (v1.360.0 / v1.374.0).
 *
 * Both rows are news (they ping, never counted). The v1.374.0 rule for a
 * Build row holds for them too: when the user is LOOKING at that session the
 * screen already says it (the waiting card, the move line), so the bell says
 * nothing; elsewhere it pings once. The words are the one-notice fixture's.
 *
 * Real NotificationBell; the network, the event socket and the desktop
 * notification hook are stubbed (build-finish-notice-v1374's harness).
 */
import type { ReactNode } from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const { getMock, notifyMock, eventsRef } = vi.hoisted(() => ({
  getMock: vi.fn(),
  notifyMock: vi.fn(),
  eventsRef: { current: [] as unknown[] },
}));

vi.mock("@/lib/api", () => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: MockApiError,
    get: getMock,
    post: vi.fn(async () => ({})),
    put: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
    API_BASE: "",
    ijToken: () => "",
    sseUrl: (p: string) => p,
  };
});
vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: eventsRef.current, connected: true }),
}));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: notifyMock,
  }),
}));
vi.mock("@/lib/useApi", () => ({
  useApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
  usePolledApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/useReviews", () => ({ useReviews: () => ({ reviews: {}, loading: false, reload: () => {} }) }));
vi.mock("@/lib/useDocumentVisible", () => ({ useDocumentVisible: () => true }));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: null, refresh: () => {}, epoch: 0 }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/chat",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import { NotificationBell, toActivity } from "@/components/NotificationBell";
import { __resetLooking, noteLooking } from "@/lib/buildWatching";
import type { IJEvent } from "@/lib/types";

interface Case {
  case: string;
  event: IJEvent & { ts: string };
  title?: string;
  body?: string;
  href?: string;
}
const CASES: Case[] = (
  JSON.parse(
    readFileSync(path.join(__dirname, "fixtures", "one-notice-v13600.json"), "utf-8").replace(/\r\n/g, "\n"),
  ) as { cases: Case[] }
).cases;
const C = (name: string) => CASES.find((c) => c.case === name)!;

let visibility: DocumentVisibilityState = "visible";

beforeEach(() => {
  getMock.mockReset();
  getMock.mockImplementation(async (p: unknown) => {
    if (String(p).startsWith("/audit")) return { entries: [], next_cursor: null, total: 0 };
    if (String(p) === "/chat/approvals/pending") return { approvals: [] };
    return {};
  });
  notifyMock.mockReset();
  eventsRef.current = [];
  window.localStorage.clear();
  __resetLooking();
  visibility = "visible";
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function bellWith(...events: (IJEvent & { ts: string })[]) {
  eventsRef.current = events;
  return render(<NotificationBell />);
}

const WAITING = () => C("build-limit-waiting");
const MOVED = () => C("build-moved-within-group");

describe("a self-waited limit and a move within a group", () => {
  for (const name of ["build-limit-waiting", "build-moved-within-group"]) {
    it(`${name}: elsewhere in the app the bell pings ONCE in the fixture's words, never counted`, async () => {
      const c = C(name);
      await bellWith(c.event);
      await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
      expect(notifyMock.mock.calls[0].slice(0, 2)).toEqual([c.title, c.body]);
      expect(screen.queryByRole("button", { name: "1 notifications" })).toBeNull();
    });

    it(`${name}: looking at THAT session, nothing is said (the screen says it); the row is kept`, async () => {
      const c = C(name);
      noteLooking("bs_lim");
      await bellWith(c.event);
      await screen.findByRole("button", { name: "Notifications" });
      await new Promise((r) => setTimeout(r, 30));
      expect(notifyMock).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
      expect(await screen.findByText(c.title!)).toBeTruthy();
    });
  }

  it("looking at ANOTHER session: the bell pings", async () => {
    noteLooking("bs_other");
    await bellWith(WAITING().event, MOVED().event);
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
  });

  it("a limit that waits for the PRESS still rings and counts (a question), as before", () => {
    const ev = { ...WAITING().event, id: "evt_q", payload: { ...WAITING().event.payload, waiting: false } };
    const item = toActivity(ev)!;
    expect(item.attention).toBe(true);
  });
});
