/**
 * v1.374.0 review (IJ harness part 2, Phase 3) — a finish notice the user
 * WATCHED is never counted again.
 *
 * The bell keeps a watched turn's row seen (never on the badge) — but only in
 * memory: closing the window to the tray (which destroys the page) or a
 * reload brought it back under "While you were away" (the bell's replay of
 * GET /audit since it was last OPENED) and counted it on the badge, for a turn
 * the user had watched end. The watched ids are now kept with the "looking"
 * note (lib/buildWatching), shared by every window of the app.
 *
 * (The harness below is build-finish-notice-v1374's, copied: a test file is
 * not a module other tests import.)
 */
import type { ReactNode } from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

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

import { NotificationBell } from "@/components/NotificationBell";
import { __resetLooking, noteLooking, stopLooking } from "@/lib/buildWatching";
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
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
});

const DONE = () => C("build-turn-finished");

async function bellWith(...events: (IJEvent & { ts: string })[]) {
  eventsRef.current = events;
  return render(<NotificationBell />);
}

describe("a watched finish is not news after a reload", () => {
  it("watched live, then the window came back: no badge, no 'While you were away' row", async () => {
    const ev = DONE().event;
    noteLooking("bs_long");
    await bellWith(ev);
    await screen.findByRole("button", { name: "Notifications" });
    await new Promise((r) => setTimeout(r, 30));
    expect(notifyMock).not.toHaveBeenCalled();
    cleanup();
    // The window was closed to the tray and opened again: a NEW page, no
    // live events, and the bell replays what it maps since it was last
    // opened (it never was) — the watched turn among them.
    stopLooking();
    __resetLooking();
    getMock.mockImplementation(async (p: unknown) => {
      if (String(p).startsWith("/audit")) {
        return { entries: [{ ...ev, ts: ev.ts }], next_cursor: null, total: 1 };
      }
      if (String(p) === "/chat/approvals/pending") return { approvals: [] };
      return {};
    });
    await bellWith();
    await new Promise((r) => setTimeout(r, 50));
    expect(getMock.mock.calls.some(([p]) => String(p).startsWith("/audit"))).toBe(true);
    expect(screen.queryByRole("button", { name: "1 notifications" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
    expect(screen.queryByText(DONE().title!)).toBeNull();
    expect(notifyMock).not.toHaveBeenCalled();
  });

  it("a finish nobody watched still comes back counted", async () => {
    const ev = DONE().event;
    getMock.mockImplementation(async (p: unknown) => {
      if (String(p).startsWith("/audit")) return { entries: [ev], next_cursor: null, total: 1 };
      if (String(p) === "/chat/approvals/pending") return { approvals: [] };
      return {};
    });
    await bellWith();
    await screen.findByRole("button", { name: "1 notifications" });
    fireEvent.click(screen.getByRole("button", { name: "1 notifications" }));
    expect(await screen.findByText(DONE().title!)).toBeTruthy();
  });
});
