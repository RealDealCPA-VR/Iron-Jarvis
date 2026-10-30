/**
 * v1.409.0 — the bell row for Build's Code view: the real Claude Code (or
 * Codex) is waiting on an approval nobody is looking at. GET
 * /chat/approvals/pending lists it with `surface: "build_app"`; the bell
 * reads it as the coding tool, by name, links to Build's Code view on that
 * session, never offers Approve / Deny (the card is answered in the
 * session), never mistakes it for an agent's ask, and counts it.
 *
 * Driven through the REAL NotificationBell; only the network, the event
 * socket and the desktop notification hook are stand-ins.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const { getMock, notifyMock, H } = vi.hoisted(() => ({
  getMock: vi.fn(),
  notifyMock: vi.fn(),
  H: { pending: [] as unknown[], panes: [] as unknown[] },
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
    post: vi.fn(async () => ({})),
    put: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
    API_BASE: "",
    ijToken: () => "",
  };
});

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: true }),
}));

vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: notifyMock,
  }),
}));

import { NotificationBell, chatAskHref, chatAskRowWords, parseChatAsk } from "@/components/NotificationBell";
import { BUILD_CODE_EVENT } from "@/lib/buildApp";
import { BUILD_TERMINAL_EVENT } from "@/lib/buildLinks";
import { __resetApiCache } from "@/lib/apiCache";

/** The row exactly as the daemon lists it (hosted/service.py pending rows). */
const APP_ROW = {
  id: "build_app:a7",
  kind: "chat",
  thread_id: "a7",
  tool: "Bash",
  requested_at: "2026-10-29T10:00:00Z",
  mission_id: null,
  project_id: null,
  surface: "build_app",
  app_session: "a7",
  harness: "claude-code",
};

beforeEach(() => {
  __resetApiCache();
  getMock.mockReset();
  notifyMock.mockReset();
  H.pending = [];
  H.panes = [];
  window.localStorage.clear();
  getMock.mockImplementation(async (path: unknown) => {
    const p = String(path);
    if (p.startsWith("/audit")) return { entries: [], next_cursor: null, total: 0 };
    if (p === "/chat/approvals/pending") return { approvals: H.pending };
    if (p === "/terminals/activity") return { panes: H.panes };
    return {};
  });
});

afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
});

describe("a Build Code view session waiting on an approval", () => {
  it("reads as the coding tool by name, links to Build's Code view on that session, has no answer buttons, counts", async () => {
    H.pending = [APP_ROW];
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    fireEvent.click(screen.getByRole("button", { name: /notification/i }));
    const row = await screen.findByTestId("bell-chat-waiting");
    expect(row.dataset.surface).toBe("build_app");
    expect(row.textContent).toContain("Claude Code is waiting for your OK");
    expect(row.textContent).toContain("Open it in Build");
    expect(within(row).getByRole("link").getAttribute("href")).toBe("/terminals?view=code&code=a7");
    expect(within(row).queryByRole("button")).toBeNull();
    // Never an agent's ask (whose Approve would post to the wrong place).
    expect(screen.queryByTestId("bell-agent-approval")).toBeNull();
  });

  it("a row of ANOTHER kind is still never read as an agent's ask (no Approve that would post elsewhere)", async () => {
    H.pending = [{ ...APP_ROW, id: "build_app:a8", kind: "approval" }];
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    fireEvent.click(screen.getByRole("button", { name: /notification/i }));
    expect((await screen.findByTestId("bell-chat-waiting")).dataset.surface).toBe("build_app");
    expect(screen.queryByTestId("bell-agent-approval")).toBeNull();
  });

  it("parseChatAsk reads the row whatever its kind; Codex by name; the session from the row", () => {
    expect(parseChatAsk(APP_ROW)).toEqual({ id: "build_app:a7", threadId: "a7", app: { tool: "Claude Code" } });
    const codex = parseChatAsk({ id: "x", kind: "build_app", surface: "build_app", session_id: "a9", harness: "codex" })!;
    expect(chatAskRowWords(codex).title).toBe("Codex is waiting for your OK");
    expect(chatAskHref(codex)).toBe("/terminals?view=code&code=a9");
    // No session named: Build's Code view itself.
    expect(chatAskHref(parseChatAsk({ id: "y", surface: "build_app" })!)).toBe("/terminals?view=code");
    // A plain chat row is unchanged.
    expect(parseChatAsk({ id: "chat:t", kind: "chat", thread_id: "t" })).toEqual({ id: "chat:t", threadId: "t" });
  });

  it("pressed while Build is open, the row announces the session (Next keeps the page)", async () => {
    window.history.replaceState({}, "", "/terminals");
    const heard: string[] = [];
    const on = (e: Event) => heard.push(String((e as CustomEvent<{ id: string }>).detail.id));
    window.addEventListener(BUILD_CODE_EVENT, on);
    try {
      H.pending = [APP_ROW];
      render(<NotificationBell />);
      await screen.findByRole("button", { name: "1 notifications" });
      fireEvent.click(screen.getByRole("button", { name: /notification/i }));
      const row = await screen.findByTestId("bell-chat-waiting");
      fireEvent.click(within(row).getByRole("link"));
      expect(heard).toEqual(["a7"]);
    } finally {
      window.removeEventListener(BUILD_CODE_EVENT, on);
    }
  });
});

describe("review: a terminal's row pressed while Build shows its Code view", () => {
  it("announces the terminal, so Build switches to Terminal on it", async () => {
    window.history.replaceState({}, "", "/terminals");
    const heard: string[] = [];
    const on = (e: Event) => heard.push(String((e as CustomEvent<{ id: string }>).detail.id));
    window.addEventListener(BUILD_TERMINAL_EVENT, on);
    try {
      H.panes = [{ id: "p7", name: null, agent_cli: "claude", state: "blocked", state_line: "", alive: true }];
      render(<NotificationBell />);
      await screen.findByRole("button", { name: "1 notifications" });
      fireEvent.click(screen.getByRole("button", { name: /notification/i }));
      const row = await screen.findByTestId("bell-pane-waiting");
      fireEvent.click(within(row).getByRole("link"));
      expect(heard).toEqual(["p7"]);
    } finally {
      window.removeEventListener(BUILD_TERMINAL_EVENT, on);
    }
  });
});
