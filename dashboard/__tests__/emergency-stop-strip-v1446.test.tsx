/**
 * v1.446.0 — ease-of-use review item 23: Emergency stop is seen everywhere,
 * and it has ONE name.
 *
 * Before: once Emergency stop was on, schedules, goals, rules and phone tasks
 * were all skipped, but nothing outside the Autonomy page said so, and that
 * page called it "Kill switch" while every refusal said "Emergency stop".
 *
 * Pinned here:
 *  - the app-wide strip shows ONLY while the daemon says `kill_switch: true`,
 *    in the words "Emergency stop is on — nothing starts on its own", with a
 *    Turn off press that is the Autonomy page's own action
 *    (POST /autonomy/kill {enabled: false}) and hides it;
 *  - it is hidden on the Autonomy page (that page has its own notice);
 *  - it reads `/autonomy` once, and again only when a `config.changed` event
 *    names `autonomy_kill_switch` or the daemon comes back — never on a clock;
 *  - HEIGHT: while it shows, `--ij-strip-h` (what the full-height modules
 *    subtract) is the demo strip's height PLUS its own, even after the demo
 *    strip rewrites the variable with its own height; when it hides the
 *    variable goes back to the demo strip's height;
 *  - the Autonomy page's button and words say "Emergency stop", never "Kill
 *    switch", its press feeds the strip's store, and its nav entry finds
 *    "emergency stop".
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    responses: {} as Record<string, unknown>,
    postResponses: {} as Record<string, unknown>,
    postError: null as Error | null,
    events: [] as { id: string; type: string; session_id: null; ts: string; payload: Record<string, unknown> }[],
    online: true,
    path: "/chat",
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  wsUrl: (p: string) => `ws://api.test${p}`,
  api: () => Promise.resolve({}),
  get: (path: string) => {
    H.gets.push(path);
    const r = H.responses[path];
    if (r === undefined) return Promise.reject(new H.FakeApiError(`unmocked GET ${path}`, 0));
    return Promise.resolve(typeof r === "function" ? (r as () => unknown)() : r);
  },
  post: (path: string, body?: unknown) => {
    H.posts.push({ path, body });
    if (H.postError) return Promise.reject(H.postError);
    const r = H.postResponses[path];
    return Promise.resolve(r === undefined ? {} : r);
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: H.events, connected: true }),
}));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: H.online, health: null, checking: false, refresh: () => {}, provided: true }),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => H.path,
  useRouter: () => ({ push: () => {}, replace: () => {} }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import {
  EmergencyStopStrip,
  emergencyStopState,
  resetEmergencyStopForTests,
} from "@/components/EmergencyStopStrip";
import AutonomyPage from "@/app/autonomy/page";
import { NAV } from "@/lib/nav";

const VAR = "--ij-strip-h";
const stripVar = () => document.documentElement.style.getPropertyValue(VAR);

/** Sizes by test id — jsdom lays nothing out, so each box says its height. */
let heights: Record<string, number> = {};
let rectSpy: ReturnType<typeof vi.spyOn> | null = null;

beforeEach(() => {
  heights = { "emergency-stop-strip": 36 };
  rectSpy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const h = heights[this.getAttribute("data-testid") ?? ""] ?? 0;
    return { x: 0, y: 0, top: 0, left: 0, right: 100, bottom: h, width: 100, height: h, toJSON: () => ({}) } as DOMRect;
  });
  window.scrollTo = vi.fn();
  window.HTMLElement.prototype.scrollTo = vi.fn();
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  rectSpy?.mockRestore();
  resetEmergencyStopForTests();
  document.documentElement.style.removeProperty(VAR);
  document.querySelectorAll("[data-fake-demo]").forEach((n) => n.remove());
  H.gets = [];
  H.posts = [];
  H.responses = {};
  H.postResponses = {};
  H.postError = null;
  H.events = [];
  H.online = true;
  H.path = "/chat";
});

function stop(on: boolean) {
  H.responses["/autonomy"] = { kill_switch: on, enabled: false, pending_proposals: 0 };
}

/** A stand-in for the demo strip (SimulatedBanner): its testid and height. */
function demoStrip(height: number) {
  const el = document.createElement("div");
  el.setAttribute("data-testid", "simulated-banner");
  el.setAttribute("data-fake-demo", "1");
  document.body.prepend(el);
  heights["simulated-banner"] = height;
  // SimulatedBanner writes its OWN height on <html>.
  document.documentElement.style.setProperty(VAR, `${height}px`);
  return el;
}

describe("the app-wide Emergency stop strip", () => {
  it("shows only while Emergency stop is on, in ONE name, and Turn off is the Autonomy page's action", async () => {
    stop(true);
    H.postResponses["/autonomy/kill"] = { kill_switch: false, stopped: 0 };
    render(<EmergencyStopStrip />);
    const strip = await screen.findByTestId("emergency-stop-strip");
    expect(strip.textContent).toMatch(/Emergency stop is on — nothing starts on its own/);
    expect(strip.textContent).not.toMatch(/kill switch/i);
    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
    await waitFor(() => expect(screen.queryByTestId("emergency-stop-strip")).toBeNull());
    expect(H.posts).toEqual([{ path: "/autonomy/kill", body: { enabled: false } }]);
    expect(emergencyStopState()).toEqual({ known: true, on: false });
  });

  it("renders nothing while Emergency stop is off (control)", async () => {
    stop(false);
    render(<EmergencyStopStrip />);
    await waitFor(() => expect(emergencyStopState().known).toBe(true));
    expect(screen.queryByTestId("emergency-stop-strip")).toBeNull();
  });

  it("a refused Turn off keeps the strip and says why", async () => {
    stop(true);
    render(<EmergencyStopStrip />);
    await screen.findByTestId("emergency-stop-strip");
    H.postError = new H.FakeApiError("the daemon said no", 409);
    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("the daemon said no");
    expect(screen.getByTestId("emergency-stop-strip")).toBeInTheDocument();
  });

  it("is hidden on the Autonomy page, which has its own notice", async () => {
    stop(true);
    H.path = "/autonomy";
    render(<EmergencyStopStrip />);
    await waitFor(() => expect(emergencyStopState()).toEqual({ known: true, on: true }));
    expect(screen.queryByTestId("emergency-stop-strip")).toBeNull();
  });

  it("reads once, then again only when a settings change names Emergency stop — no clock", async () => {
    const interval = vi.spyOn(window, "setInterval");
    stop(false);
    const { rerender } = render(<EmergencyStopStrip />);
    await waitFor(() => expect(H.gets.filter((p) => p === "/autonomy")).toHaveLength(1));
    // An unrelated settings change: no read.
    H.events = [
      { id: "e1", type: "config.changed", session_id: null, ts: "", payload: { keys: ["device.theme"] } },
    ];
    rerender(<EmergencyStopStrip />);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(H.gets.filter((p) => p === "/autonomy")).toHaveLength(1);
    // Turned on from Settings / chat: the event names the setting → one read.
    stop(true);
    H.events = [
      { id: "e2", type: "config.changed", session_id: null, ts: "", payload: { keys: ["autonomy_kill_switch"] } },
      ...H.events,
    ];
    rerender(<EmergencyStopStrip />);
    await screen.findByTestId("emergency-stop-strip");
    expect(H.gets.filter((p) => p === "/autonomy")).toHaveLength(2);
    // testing-library's own waits poll every 50 ms; nothing of ours does.
    const ours = interval.mock.calls.filter(([fn]) => (fn as { name?: string }).name !== "checkRealTimersCallback");
    expect(ours).toEqual([]);
    interval.mockRestore();
  });

  it("reads again when the daemon comes back", async () => {
    H.online = false;
    stop(true);
    const { rerender } = render(<EmergencyStopStrip />);
    await waitFor(() => expect(H.gets).toHaveLength(1));
    H.online = true;
    rerender(<EmergencyStopStrip />);
    await waitFor(() => expect(H.gets).toHaveLength(2));
  });
});

describe("--ij-strip-h covers both strips", () => {
  it("is the demo strip's height plus this strip's, and survives the demo strip rewriting it", async () => {
    demoStrip(40);
    stop(true);
    render(<EmergencyStopStrip />);
    await screen.findByTestId("emergency-stop-strip");
    await waitFor(() => expect(stripVar()).toBe("76px"));
    // The demo strip resizes and writes ITS height again.
    heights["simulated-banner"] = 52;
    document.documentElement.style.setProperty(VAR, "52px");
    await waitFor(() => expect(stripVar()).toBe("88px"));
  });

  it("alone (no demo strip) it is just this strip's height", async () => {
    stop(true);
    render(<EmergencyStopStrip />);
    await screen.findByTestId("emergency-stop-strip");
    await waitFor(() => expect(stripVar()).toBe("36px"));
  });

  it("goes back to the demo strip's own height when Emergency stop is turned off", async () => {
    demoStrip(40);
    stop(true);
    H.postResponses["/autonomy/kill"] = { kill_switch: false };
    render(<EmergencyStopStrip />);
    await screen.findByTestId("emergency-stop-strip");
    await waitFor(() => expect(stripVar()).toBe("76px"));
    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
    await waitFor(() => expect(screen.queryByTestId("emergency-stop-strip")).toBeNull());
    expect(stripVar()).toBe("40px");
  });
});

/* ---- The Autonomy page says "Emergency stop" ------------------------------ */

function primeAutonomy(kill: boolean) {
  H.responses["/autonomy"] = {
    enabled: true,
    level: "suggest",
    dry_run: false,
    kill_switch: kill,
    tick_seconds: 900,
    max_actions_per_day: 20,
    max_tokens_per_day: 200000,
    used_actions_24h: 0,
    used_tokens_24h: 0,
    active_goals: 0,
    pending_proposals: 0,
  };
  H.responses["/goals"] = { goals: [] };
  H.responses["/goals/digest?hours=24"] = { digest: { goals: [], since: "2026-10-18T09:00:00Z" } };
  H.responses["/autonomy/goals"] = { goals: [] };
  H.responses["/proposals?status=pending"] = { proposals: [] };
  H.responses["/autonomy/briefing"] = { text: "", active_goals: 0, recent_actions: 0, pending_proposals: 0, pushed: null };
  H.responses["/grants?live=1"] = { grants: [] };
}

describe("the Autonomy page calls it Emergency stop", () => {
  it("the button, the flash and the notice never say Kill switch, and the press feeds the strip", async () => {
    primeAutonomy(false);
    H.postResponses["/autonomy/kill"] = { kill_switch: true, stopped: 1 };
    H.path = "/autonomy";
    render(<AutonomyPage />);
    const btn = await screen.findByRole("button", { name: /^\s*Emergency stop\s*$/ });
    await waitFor(() => expect(btn).toBeEnabled());
    fireEvent.click(btn);
    await screen.findByText(/^Emergency stop is on — schedules, goals, rules, phone tasks and autonomy will not start\. Stopped 1 running task\.$/);
    await waitFor(() => expect(emergencyStopState()).toEqual({ known: true, on: true }));
    expect(document.body.textContent).not.toMatch(/kill switch/i);
  });

  it("engaged: Turn off Emergency stop, and its flash says it is off", async () => {
    primeAutonomy(true);
    H.postResponses["/autonomy/kill"] = { kill_switch: false };
    H.path = "/autonomy";
    render(<AutonomyPage />);
    const btn = await screen.findByRole("button", { name: /Turn off Emergency stop/ });
    expect(screen.getByText("Emergency stop is on — nothing starts on its own.")).toBeInTheDocument();
    // The page's own read keeps the strip's store true.
    await waitFor(() => expect(emergencyStopState()).toEqual({ known: true, on: true }));
    await waitFor(() => expect(btn).toBeEnabled());
    fireEvent.click(btn);
    await screen.findByText(/^Emergency stop is off/);
    expect(H.posts).toContainEqual({ path: "/autonomy/kill", body: { enabled: false } });
    expect(document.body.textContent).not.toMatch(/kill switch/i);
  });

  it("the nav finds the Autonomy page by 'emergency stop'", () => {
    const entry = NAV.flatMap((sec) => sec.items).find((i) => i.href === "/autonomy");
    expect(entry?.aliases).toContain("emergency stop");
  });
});
