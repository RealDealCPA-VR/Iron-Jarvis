/**
 * Wave F review harness: one-notice-v13600's (copied from build-finish-notice-v1374).
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
import { __resetChatAway, noteShowing } from "@/lib/chatAway";
import type { IJEvent } from "@/lib/types";

interface Case {
  case: string;
  event: IJEvent & { ts: string };
  title?: string;
}
const CASES: Case[] = (
  JSON.parse(
    readFileSync(path.join(__dirname, "fixtures", "one-notice-v13600.json"), "utf-8").replace(/\r\n/g, "\n"),
  ) as { cases: Case[] }
).cases;
const REPORT = () => CASES.find((c) => c.case === "remote-reported-back")!;

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
  __resetChatAway();
  visibility = "visible";
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  __resetChatAway();
});

async function bellWith(...events: (IJEvent & { ts: string })[]) {
  eventsRef.current = events;
  return render(<NotificationBell />);
}

/**
 * Wave F review — a remote agent's line (`remote.message`) now rings the bell
 * (readiness 0j.5 item 10). A line that came home to the chat the user is
 * LOOKING AT in this window is seen as it lands: no desktop ping, nothing
 * counted (the row is kept). Elsewhere, or with the window in the background,
 * the bell pings as before.
 */
describe("a remote's line in the chat in front of the user is not pinged", () => {
  it("the row names the chat it came home to", () => {
    expect(toActivity(REPORT().event)!.chat).toBe("chat_c1");
  });

  it("that chat open here: nothing said, nothing counted, the row kept", async () => {
    noteShowing("v1", "chat_c1");
    await bellWith(REPORT().event);
    await screen.findByRole("button", { name: "Notifications" });
    await new Promise((r) => setTimeout(r, 30));
    expect(notifyMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "1 notifications" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
    expect(await screen.findByText(REPORT().title!)).toBeTruthy();
  });

  it("another chat open here: the bell pings and counts", async () => {
    noteShowing("v1", "chat_other");
    await bellWith(REPORT().event);
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
    await screen.findByRole("button", { name: "1 notifications" });
  });

  it("that chat open but the window in the background: the bell pings", async () => {
    noteShowing("v1", "chat_c1");
    visibility = "hidden";
    await bellWith(REPORT().event);
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
  });
});
