/**
 * Group 20 (third fix plan), MC2 — decision G17: a coding tool asking to act
 * in your browser through Iron Jarvis's tools door gets ITS OWN card in the
 * bell: who asks (the Build terminal or the coding session), what it would do
 * and on which page, and "Allow once" and "Deny" only.
 *
 * Before: the row read "An agent is asking permission" / "An agent run wants
 * to click on a page", offered "Allow for this run" (which the daemon counted
 * once) and linked to /sessions/mcp:… — a page that does not exist.
 *
 * The rows are the daemon's pending listing as it sends them
 * (routes/chat.py::pending_chat_approvals; the daemon side is pinned by
 * tests/test_third_plan_g20_door_card.py): `session_id` "mcp:<terminal>" or
 * "mcp:code:<session>", the card's sentence as `why`, `once_only`, and the
 * wait in seconds.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const { getMock, postMock, notifyMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  notifyMock: vi.fn(),
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

import { NotificationBell, agentAskWords, doorAskOf } from "@/components/NotificationBell";

const PENDING_PATH = "/chat/approvals/pending";

const TERMINAL_SENTENCE =
  "The Build terminal “notes site” wants to click 'Save changes' in your browser, in the tab showing example.com (“Account settings”).";
const CODE_SENTENCE =
  "The coding session “Tidy the report” (Claude Code) wants to type some text into 'Password' in your browser, in the tab showing example.com (“Sign in”).";

const TERMINAL_ASK = {
  id: "apr_door000000000001",
  tool: "browser_click",
  session_id: "mcp:term_door_card",
  requested_at: "2026-11-17T10:00:00",
  timeout_s: 45,
  once_only: true,
  why: TERMINAL_SENTENCE,
  mission_id: null,
  project_id: null,
  task: null,
  origin: null,
};

const CODE_ASK = {
  ...TERMINAL_ASK,
  id: "apr_door000000000002",
  tool: "browser_type",
  session_id: "mcp:code:app_door_card",
  why: CODE_SENTENCE,
};

function mockDaemon(approvals: unknown[]) {
  getMock.mockImplementation(async (path: unknown) => {
    if (path === "/computeruse") return { pending_approvals: 0 };
    if (path === "/diagnostics") return { pending_reviews: 0 };
    if (path === PENDING_PATH) return { approvals };
    return {};
  });
}

async function openRow(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
  return screen.findByTestId("bell-agent-approval");
}

function buttonNames(row: HTMLElement): string[] {
  return within(row)
    .getAllByRole("button")
    .map((b) => (b.textContent ?? "").trim());
}

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  notifyMock.mockReset();
});

afterEach(() => cleanup());

describe("a coding tool's ask through the tools door", () => {
  it("says who asks, what it would do and on which page, never 'An agent run'", async () => {
    mockDaemon([TERMINAL_ASK]);
    render(<NotificationBell />);
    const row = await openRow();
    expect(row.getAttribute("data-kind")).toBe("door");
    expect(row.textContent).toContain("A coding tool is asking permission");
    expect(within(row).getByTestId("bell-door-line").textContent).toBe(TERMINAL_SENTENCE);
    expect(row.textContent).toContain("The coding tool waits 45 seconds for your answer, then nothing is done.");
    expect(row.textContent).not.toContain("An agent");
    expect(row.textContent).not.toContain("The run");
  });

  it("offers Allow once and Deny only", async () => {
    mockDaemon([TERMINAL_ASK]);
    render(<NotificationBell />);
    const row = await openRow();
    expect(buttonNames(row)).toEqual(["Allow once", "Deny"]);
  });

  it("offers Allow once and Deny only even when the row does not say once_only", async () => {
    mockDaemon([{ ...TERMINAL_ASK, once_only: undefined, can_always: true }]);
    render(<NotificationBell />);
    const row = await openRow();
    expect(buttonNames(row)).toEqual(["Allow once", "Deny"]);
  });

  it("opens the terminal that asks, never a run page", async () => {
    mockDaemon([TERMINAL_ASK]);
    render(<NotificationBell />);
    const row = await openRow();
    const link = within(row).getByTestId("bell-door-open");
    expect(link.getAttribute("href")).toBe("/terminals?focus=term_door_card");
    expect(link.textContent).toContain("Open the terminal");
    expect(row.querySelector('a[href^="/sessions/"]')).toBeNull();
  });

  it("names a Build › Code session and opens it", async () => {
    mockDaemon([CODE_ASK]);
    render(<NotificationBell />);
    const row = await openRow();
    expect(within(row).getByTestId("bell-door-line").textContent).toBe(CODE_SENTENCE);
    const link = within(row).getByTestId("bell-door-open");
    expect(link.getAttribute("href")).toContain("code=app_door_card");
    expect(link.textContent).toContain("Open the coding session");
  });

  it("Allow once posts once", async () => {
    mockDaemon([TERMINAL_ASK]);
    postMock.mockResolvedValue({ ok: true, decision: "once" });
    render(<NotificationBell />);
    const row = await openRow();
    fireEvent.click(within(row).getByRole("button", { name: "Allow once" }));
    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith(`/chat/approvals/${TERMINAL_ASK.id}`, { decision: "once" }),
    );
    const answered = await screen.findByTestId("bell-agent-answered-line");
    expect(answered.textContent).toContain("Allowed once. The coding tool carries on");
  });

  it("the desktop ping's words are the card's sentence", () => {
    const door = doorAskOf(TERMINAL_ASK.session_id, TERMINAL_ASK.why);
    expect(door).toEqual({ sentence: TERMINAL_SENTENCE, terminal: "term_door_card", codeSession: "" });
    expect(agentAskWords({ tool: "browser_click", door: door! })).toBe(TERMINAL_SENTENCE);
    // An older daemon with no sentence: the tool in words, still not "An agent run".
    const bare = doorAskOf("mcp:term_x", undefined)!;
    expect(agentAskWords({ tool: "browser_click", door: bare })).toMatch(/^A coding tool wants to /);
  });

  it("an agent run's ask keeps its own card", async () => {
    mockDaemon([{ id: "apr_run", tool: "shell", session_id: "session_job42", requested_at: "2026-11-17T10:00:00" }]);
    render(<NotificationBell />);
    const row = await openRow();
    expect(row.getAttribute("data-kind")).toBeNull();
    expect(row.textContent).toContain("An agent is asking permission");
    expect(buttonNames(row)).toEqual(["Allow once", "Allow for this run", "Deny"]);
  });
});
