/**
 * v1.226.0 (F-F-2) — the Overview no longer renders a FALSE empty state on a
 * 5xx. GET /sessions -> 500 while everything else 200s used to show "No
 * sessions yet." / "Nothing yet — try a task above." with no error text
 * anywhere (the global banner was cleared by the concurrent /metrics 200).
 * Now the sessions panels render the DataError note carrying the daemon's
 * message; the OfflineHint stays reserved for status 0.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

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
    if (path.startsWith("/sessions"))
      throw new MockApiError("internal error: OperationalError: no such column: sessions.origin", 500);
    if (path === "/health")
      return { status: "ok", version: "1.226.0", providers: [], default_provider: "mock" };
    if (path === "/metrics")
      return {
        sessions_evaluated: 12,
        avg_completion: 0.9,
        avg_tool_success_rate: 0.95,
        avg_latency_s: 1.2,
        total_tool_invocations: 40,
        event_count: 300,
      };
    if (path === "/vault") return { providers: [] };
    if (path.startsWith("/onboarding"))
      return { complete: true, done: true, dismissed: true, checklist: [], checks: [], next_step: null };
    if (path.startsWith("/goals") || path.startsWith("/autonomy"))
      return { goals: [], rules: [], enabled: false };
    if (path === "/templates") return { templates: [] };
    if (path.startsWith("/reflex")) return { rules: [] };
    if (path.startsWith("/diagnostics")) return {};
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
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));

import OverviewPage from "@/app/page";

afterEach(() => cleanup());

describe("Overview on a 500 from /sessions (v1.226.0)", () => {
  it("shows the daemon's error instead of the 'No sessions yet' empty copy", async () => {
    localStorage.setItem("ij_ov_admin", "1"); // expand "Systems & admin" (collapsed by default)
    render(<OverviewPage />);
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/sessions"));
    await waitFor(() => {
      expect(screen.getAllByText(/OperationalError/).length).toBeGreaterThan(0);
    });
    const txt = document.body.textContent || "";
    expect(/No sessions yet|Nothing yet/i.test(txt)).toBe(false);
    expect(/Could not load sessions/.test(txt)).toBe(true);
    // The daemon-offline hint is NOT shown either (status 500 !== 0).
    expect(screen.queryByText(/Daemon offline/i)).toBeNull();
  });
});
