/**
 * v1.374.0 (IJ harness part 2, Phase 3) — a long Build turn's FINISH NOTICE,
 * under the one-notice rules (v1.360.0 / v1.361.0).
 *
 * `build.turn_finished` is a bell row that rings and counts on the badge
 * ("Your Build session finished", the fixture's words — the daemon and the
 * desktop app's tray watcher read the same fixture). ONE notice:
 *
 *  - a window is open and the user is elsewhere in the app → the bell pings;
 *  - the user is LOOKING at that session (shown, in front, focused — in this
 *    window or another of the app's) → nothing is said and nothing counted;
 *  - the window is in the background (hidden, or not focused) → the bell
 *    pings;
 *  - a popped-out window that does not speak for the app → no ping (it
 *    records it); the one pop-out that speaks → pings.
 *
 * Real NotificationBell; the network, the event socket and the desktop
 * notification hook are stubbed (one-notice-v13600's harness).
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
import { LOOKING_FRESH_MS, LOOKING_KEY, __resetLooking, noteLooking } from "@/lib/buildWatching";
import { buildTookWords } from "@/lib/buildLinks";
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

describe("the row: the fixture's words, opening Build on that session", () => {
  it("rings, counts, and names the session and how long it took", () => {
    const c = DONE();
    const item = toActivity(c.event)!;
    expect(item.quiet).toBeFalsy();
    expect(item.attention).toBe(true);
    expect([item.title, item.body, item.href]).toEqual([c.title, c.body, c.href]);
    expect(item.build).toBe("bs_long");
    expect(toActivity(C("build-turn-failed").event)!.title).toBe("Your Build session could not finish");
    expect(buildTookWords(59)).toBe("took 59 s");
    expect(buildTookWords(3600)).toBe("took 60 min");
  });
});

describe("one notice per finished turn", () => {
  it("elsewhere in the app: the bell's ONE ping, in the row's words, and the badge counts it", async () => {
    await bellWith(DONE().event);
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
    expect(notifyMock.mock.calls[0].slice(0, 2)).toEqual([DONE().title, DONE().body]);
    await screen.findByRole("button", { name: "1 notifications" });
  });

  it("looking at THAT session in this window: nothing said, nothing counted (the row is kept)", async () => {
    noteLooking("bs_long");
    await bellWith(DONE().event);
    await screen.findByRole("button", { name: "Notifications" });
    // Give the ping effect every chance to run.
    await new Promise((r) => setTimeout(r, 30));
    expect(notifyMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "1 notifications" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
    expect(await screen.findByText(DONE().title!)).toBeTruthy();
  });

  it("looking at ANOTHER session: the bell pings", async () => {
    noteLooking("bs_other");
    await bellWith(DONE().event);
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
  });

  it("the window showing it is in the background (hidden or not focused): the bell pings", async () => {
    noteLooking("bs_long");
    visibility = "hidden";
    await bellWith(DONE().event);
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
    cleanup();
    notifyMock.mockReset();
    visibility = "visible";
    (document.hasFocus as unknown as ReturnType<typeof vi.fn>).mockReturnValue(false);
    noteLooking("bs_long");
    await bellWith(DONE().event);
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
  });

  it("looking at it in ANOTHER window of the app: that window's fresh note keeps this bell quiet; a stale one does not", async () => {
    window.localStorage.setItem(LOOKING_KEY, JSON.stringify({ thread: "bs_long", win: "wOther", at: Date.now() }));
    await bellWith(DONE().event);
    await screen.findByRole("button", { name: "Notifications" });
    await new Promise((r) => setTimeout(r, 30));
    expect(notifyMock).not.toHaveBeenCalled();
    cleanup();
    window.localStorage.setItem(
      LOOKING_KEY,
      JSON.stringify({ thread: "bs_long", win: "wOther", at: Date.now() - LOOKING_FRESH_MS - 1000 }),
    );
    await bellWith(DONE().event);
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
  });

  it("a popped-out window that does not speak records it; the one that speaks pings", async () => {
    const w = window as unknown as { ironjarvis?: unknown };
    w.ironjarvis = { popout: { open: () => {}, isPopout: true, speaks: () => false } };
    await bellWith(DONE().event);
    await screen.findByRole("button", { name: "1 notifications" });
    await new Promise((r) => setTimeout(r, 30));
    expect(notifyMock).not.toHaveBeenCalled();
    cleanup();
    w.ironjarvis = { popout: { open: () => {}, isPopout: true, speaks: () => true } };
    await bellWith(DONE().event);
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
  });
});
