/**
 * v1.342.0 ("the pack says why"), lane F3: the bell maps `mcp.pack_failed`.
 *
 * The daemon publishes `mcp.pack_failed` {name, error, reason, fix} ONCE per
 * pack per episode (a failed load, or the first call-time failure after a
 * success). The bell shows it as a plain activity row, "Pack <name> is
 * failing: <reason>", linking to the Tools page, with the fix as its body.
 * Nothing heavy rides along: the bell must not pull framer.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ASIDE, readSrc } from "./helpers/dashGuard";

const { getMock, postMock, putMock, notifyMock, eventsRef } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  putMock: vi.fn(),
  notifyMock: vi.fn(),
  eventsRef: { current: [] as unknown[] },
}));

vi.mock("@/lib/api", () => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: MockApiError,
    get: getMock,
    post: postMock,
    put: putMock,
    patch: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
    API_BASE: "",
    ijToken: () => "",
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

import { BELL_EVENT_TYPES, NotificationBell, toActivity } from "@/components/NotificationBell";
import type { IJEvent } from "@/lib/types";

function ev(payload: Record<string, unknown>, id = "evt-pack-1"): IJEvent {
  return {
    id,
    type: "mcp.pack_failed",
    session_id: null,
    ts: "2026-10-16T09:00:00",
    payload,
  } as unknown as IJEvent;
}

const FAILED = ev({
  name: "brave",
  error: "MCPServerStopped: pack 'brave': the MCP server closed the connection (exit code 1)",
  reason: "It needs BRAVE_API_KEY and none was given",
  fix: "Add BRAVE_API_KEY in Settings > Tools, then Retry",
});

beforeEach(() => {
  getMock.mockReset();
  getMock.mockResolvedValue({});
  postMock.mockReset();
  postMock.mockResolvedValue({ ok: true });
  putMock.mockReset();
  putMock.mockResolvedValue({ changed: [] });
  notifyMock.mockReset();
  eventsRef.current = [];
  document.title = "Iron Jarvis";
});

afterEach(() => cleanup());

describe("mcp.pack_failed in the bell", () => {
  it("is a type the bell subscribes to", () => {
    expect(BELL_EVENT_TYPES).toContain("mcp.pack_failed");
  });

  it("maps to 'Pack <name> is failing: <reason>' linking to the Tools page, the fix as the body", () => {
    const item = toActivity(FAILED);
    expect(item).not.toBeNull();
    expect(item!.title).toBe("Pack brave is failing: It needs BRAVE_API_KEY and none was given");
    expect(item!.href).toBe("/tools");
    expect(item!.body).toBe("Add BRAVE_API_KEY in Settings > Tools, then Retry");
    expect(item!.id).toBe("evt-pack-1");
    expect(item!.title).not.toMatch(ASIDE);
    expect(item!.body).not.toMatch(ASIDE);
  });

  it("with no reason it names the error; with neither it still names the pack", () => {
    const noReason = toActivity(ev({ name: "brave", error: "exit code 1", reason: "", fix: "" }))!;
    expect(noReason.title).toBe("Pack brave is failing: exit code 1");
    expect(noReason.body).toBe("");
    const bare = toActivity(ev({ name: "brave" }))!;
    expect(bare.title).toMatch(/^Pack brave is failing: \S/);
    const nameless = toActivity(ev({}))!;
    expect(nameless.title).toMatch(/^Pack a pack is failing: /);
  });

  it("the pack's error text never reaches the row as its title (the reason does)", () => {
    // The error can carry a masked tail; the TITLE is the plain reason.
    const item = toActivity(FAILED)!;
    expect(item.title).not.toContain("MCPServerStopped");
  });

  it("renders as a Recent activity row that opens /tools", async () => {
    eventsRef.current = [FAILED];
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
    const title = await screen.findByText(
      "Pack brave is failing: It needs BRAVE_API_KEY and none was given",
    );
    const link = title.closest("a") as HTMLAnchorElement;
    expect(link).toBeTruthy();
    expect(link.getAttribute("href")).toBe("/tools");
    expect(link.textContent).toContain("Add BRAVE_API_KEY in Settings > Tools, then Retry");
    // Informational: it does not count as waiting on the user.
    expect(screen.queryByRole("button", { name: "1 notifications" })).toBeNull();
  });
});

describe("the bell stays light", () => {
  it("imports nothing from framer-motion or the motion wrapper", () => {
    const src = readSrc("components/NotificationBell.tsx");
    expect(src).not.toMatch(/from\s+["']framer-motion["']/);
    expect(src).not.toMatch(/from\s+["']@\/components\/motion["']/);
  });
});
