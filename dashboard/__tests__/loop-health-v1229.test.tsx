/**
 * v1.229.0 (audit Wave 3, OBS2) — loop health that is TRUE, and VISIBLE.
 *
 * The daemon now reports a background loop's REAL cycles (fleet, slack
 * socket) and every loop writes the same failure key (`last_error` + `at`;
 * `error` is a one-release alias). The dashboard used to read `error` only,
 * render "N failed" in an Advanced-only tile inside a collapsed card, and the
 * hero said "All systems nominal" over a dead loop. Now:
 *
 *  - Overview, Simple mode: the hero line says "N background task(s) failing"
 *    and a one-line amber note names each failing loop with its last error.
 *  - Overview, Advanced: the health tile lists the failing loop NAMES and a
 *    list under the grid carries each loop's last_error.
 *  - NotificationBell: one item per loop whose ok:false is older than 5 min —
 *    "Background task <name> has been failing for <m> min — <last_error>" —
 *    counted in the badge; a fresh failure (< 5 min) and an entry with no
 *    `at` are NOT items.
 *
 * v1.355.0 (readiness G32, UPDATED ON PURPOSE): the loop is named in the
 * user's words (lib/loopWords: `fleet` → "Your model servers can't be
 * checked"), the row/note opens the page its fix lives on (/fleet, not the
 * chat home), and the engine name + last error ride the hover title only.
 * The full new contract is pinned in background-names-v13550.test.tsx.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const { getMock, notifyMock, diagRef, mapRef } = vi.hoisted(() => ({
  getMock: vi.fn(),
  notifyMock: vi.fn(),
  diagRef: { current: {} as Record<string, unknown> },
  // v1.334.0: the Status view reads the failing loops off /ui/system-map
  // (the daemon names them there; the `error` alias is pinned in
  // tests/test_system_map_v1334.py). The bell still reads /diagnostics.
  mapRef: { current: {} as Record<string, unknown> },
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
  getMock.mockImplementation(async (path: string) => {
    if (path === "/ui/system-map") return mapRef.current;
    if (path.startsWith("/diagnostics")) return diagRef.current;
    if (path.startsWith("/sessions")) return { sessions: [] };
    if (path === "/health")
      return { status: "ok", version: "1.229.0", providers: [], default_provider: "mock" };
    if (path === "/metrics")
      return {
        sessions_evaluated: 1,
        avg_completion: 1,
        avg_tool_success_rate: 1,
        avg_latency_s: 1,
        total_tool_invocations: 1,
        event_count: 1,
      };
    if (path === "/vault") return { providers: [] };
    if (path.startsWith("/onboarding"))
      return { complete: true, done: true, dismissed: true, checklist: [], checks: [], next_step: null };
    if (path.startsWith("/goals") || path.startsWith("/autonomy"))
      return { goals: [], rules: [], enabled: false };
    if (path === "/templates") return { templates: [] };
    if (path.startsWith("/reflex")) return { rules: [] };
    if (path.startsWith("/workflows/runs")) return { runs: [] };
    if (path.startsWith("/chat/approvals")) return { approvals: [] };
    if (path.startsWith("/computeruse")) return { pending_approvals: 0 };
    return {};
  });
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
    wsUrl: (p: string) => p,
    onUnauthorizedChange: () => () => {},
    onRequestErrorChange: () => () => {},
  };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: notifyMock,
  }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));

import OverviewPage from "@/components/overview/StatusOverview";
import { NotificationBell } from "@/components/NotificationBell";

const FLEET_ERR = "RuntimeError: fleet cycle exploded";
const SLACK_ERR = "ConnectionError: apps.connections.open refused";
const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

beforeEach(() => {
  localStorage.clear();
  notifyMock.mockClear();
});
afterEach(() => cleanup());

describe("Overview names a failing background loop (v1.229.0; read off the system map since v1.334.0)", () => {
  it("the status line does not say all good and an amber note names the loop + error", async () => {
    mapRef.current = { areas: [], status: { failing_loops: [{ name: "fleet", error: FLEET_ERR }] } };
    render(<OverviewPage />);
    const note = await screen.findByTestId("loop-failing-note");
    // v1.355.0 (G32, on purpose): plain words on screen, the engine name and
    // the error on the hover, and a door to where the fix lives.
    expect(note.textContent).toContain("Your model servers can't be checked.");
    expect(note.textContent).not.toContain("fleet");
    expect(note.textContent).not.toContain(FLEET_ERR);
    const row = screen.getByTestId("loop-failing-row");
    expect(row.querySelector("[title]")?.getAttribute("title")).toBe(`fleet: ${FLEET_ERR}`);
    expect(screen.getByRole("link", { name: "Open: Model servers" })).toHaveAttribute("href", "/fleet");
    expect(screen.getByText("1 background task failing")).toBeTruthy();
    expect(screen.queryByText(/All good/)).toBeNull();
  });

  // This file renders without a DaemonProvider, so /health is unknown to the
  // status line and it shows the plain healthy line (the demo-default line is
  // pinned in ux-wave1-firstrun-v1313).
  it("no failing loop: no note, the plain healthy line", async () => {
    mapRef.current = { areas: [], status: { failing_loops: [] } };
    render(<OverviewPage />);
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/ui/system-map"));
    await screen.findByText("All good — ready when you are");
    expect(screen.queryByText(/nominal/)).toBeNull();
    expect(screen.queryByTestId("loop-failing-note")).toBeNull();
  });

  it("two failing loops: both named, each with its own reason, and the line counts them", async () => {
    mapRef.current = {
      areas: [],
      status: {
        failing_loops: [
          { name: "fleet", error: FLEET_ERR },
          { name: "rehydrate_goals", error: "RuntimeError: goals exploded" },
        ],
      },
    };
    render(<OverviewPage />);
    const note = await screen.findByTestId("loop-failing-note");
    // v1.355.0 (G32, on purpose): one line per task, each in plain words
    // with its own reason on the hover and its own door.
    const rows = screen.getAllByTestId("loop-failing-row");
    expect(rows.map((r) => r.getAttribute("data-loop"))).toEqual(["fleet", "rehydrate_goals"]);
    expect(note.textContent).toContain("Your model servers can't be checked.");
    expect(note.textContent).toContain("Your goals weren't loaded. Chat keeps working.");
    expect(rows[1].querySelector("[title]")?.getAttribute("title")).toBe(
      "rehydrate_goals: RuntimeError: goals exploded",
    );
    expect(screen.getByRole("link", { name: "Open: Goals" })).toHaveAttribute("href", "/autonomy");
    expect(screen.getByText("2 background tasks failing")).toBeTruthy();
  });
});

describe("NotificationBell: a loop failing for 5+ min is an item (v1.229.0)", () => {
  it("one deduped item per stale failing loop, with minutes and last_error; fresh or undated failures are not items", async () => {
    diagRef.current = {
      pending_reviews: 0,
      background_loops: {
        fleet: { ok: false, last_error: FLEET_ERR, at: minutesAgo(10) },
        slack_socket: { ok: false, last_error: SLACK_ERR, at: minutesAgo(1) }, // too fresh
        rehydrate_goals: { ok: false, error: "x" }, // no `at` → age unknown → no item
        scheduler: { ok: true },
      },
    };
    render(<NotificationBell />);
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/diagnostics"));
    const btn = await screen.findByLabelText("1 notifications");
    fireEvent.click(btn);
    const rows = await screen.findAllByTestId("bell-loop-failing");
    expect(rows).toHaveLength(1);
    // v1.355.0 (G32, on purpose): the task in the user's words, how long,
    // and the row opens Fleet (it opened the chat home); the engine name and
    // the last error stay on the hover title.
    expect(rows[0].textContent).toContain("Your model servers can't be checked");
    expect(rows[0].textContent).toContain("For 10 min");
    const link = rows[0].querySelector("a")!;
    expect(link.getAttribute("href")).toBe("/fleet");
    expect(link.getAttribute("title")).toBe(`fleet: ${FLEET_ERR}`);
    expect(rows[0].textContent).not.toContain(FLEET_ERR);
    expect(document.body.textContent).not.toContain("slack_socket");
    expect(screen.queryByText(/all caught up/i)).toBeNull();
    // the desktop ping names it (one task: in the row's words)
    expect(notifyMock).toHaveBeenCalled();
    expect(String(notifyMock.mock.calls[0][1])).toContain("Your model servers can't be checked");
  });

  it("no stale failing loop: no item, badge absent", async () => {
    diagRef.current = {
      pending_reviews: 0,
      background_loops: {
        slack_socket: { ok: false, last_error: SLACK_ERR, at: minutesAgo(2) },
      },
    };
    render(<NotificationBell />);
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/diagnostics"));
    fireEvent.click(screen.getByLabelText("Notifications"));
    expect(screen.queryByTestId("bell-loop-failing")).toBeNull();
    expect(await screen.findByText(/all caught up/i)).toBeTruthy();
  });
});
