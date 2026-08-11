/**
 * PREFLIGHT provider health (useProviderHealth + PreflightNote + ModelSwitcher
 * availability marks).
 *
 * The incident behind all of this: a user typed a full request while their
 * default provider (fleet-custom) was unreachable; /health knew, but the app
 * only said so AFTER the turn failed. These tests guard the three preflight
 * pieces:
 *  - the hook: maps /health, ONE interval, no overlapping requests, and on a
 *    fetch error keeps the LAST-KNOWN map (stale beats empty — a daemon blip
 *    must not flash every provider "down") while flagging `stale`;
 *  - PreflightNote: silent unless availability is KNOWN false, amber when it
 *    is, softened when the knowledge itself is stale;
 *  - ModelSwitcher: offline providers are MARKED but never trapped (still
 *    selectable), and the trigger's amber dot appears only when the
 *    currently-SELECTED provider is offline.
 */

import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";

const { getMock, putMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  putMock: vi.fn(async () => ({})),
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
  get: getMock,
  put: putMock,
  post: vi.fn(async () => ({})),
  patch: vi.fn(async () => ({})),
  del: vi.fn(async () => ({})),
  API_BASE: "",
  ijToken: () => "",
}));

import { useProviderHealth } from "@/lib/useProviderHealth";
import { PreflightNote } from "@/components/chat/PreflightNote";
import { ModelSwitcher } from "@/components/ModelSwitcher";

// ---- fixtures ---------------------------------------------------------------

const HEALTH = {
  status: "ok",
  version: "1.164.0",
  default_provider: "fleet-custom",
  default_model: "fleet-llama-70b",
  providers: [
    { provider: "anthropic", available: true, class: "cloud" },
    { provider: "fleet-custom", available: false, class: "custom" },
  ],
};

const MODELS = {
  models: [
    { provider: "anthropic", model: "claude-sonnet-4-6", name: "Anthropic" },
    { provider: "fleet-custom", model: "fleet-llama-70b", name: "Fleet box" },
  ],
};

const ROUTING = {
  enabled: false,
  routing_model: "",
  connected: [],
  suggested: null,
  tiers: {},
};

/** Route the mocked `get` like the daemon (ModelSwitcher hits all three). */
function mockDaemon(health: typeof HEALTH = HEALTH) {
  getMock.mockImplementation(async (path: unknown) => {
    if (path === "/health") return health;
    if (path === "/models") return MODELS;
    if (path === "/routing") return ROUTING;
    return {};
  });
}

beforeEach(() => {
  getMock.mockReset();
  putMock.mockClear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers(); // in case a fake-timer test bailed early
});

// ---- useProviderHealth ------------------------------------------------------

describe("useProviderHealth", () => {
  it("maps /health providers into byProvider and exposes defaultProvider", async () => {
    getMock.mockResolvedValue(HEALTH);
    const { result, unmount } = renderHook(() => useProviderHealth());
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.byProvider).toEqual({
      anthropic: true,
      "fleet-custom": false,
    });
    expect(result.current.defaultProvider).toBe("fleet-custom");
    expect(result.current.stale).toBe(false);
    expect(getMock).toHaveBeenCalledWith("/health", expect.anything());
    unmount();
  });

  it("keeps the LAST-KNOWN map on fetch error and flags stale; recovery clears it", async () => {
    getMock.mockResolvedValueOnce(HEALTH);
    const { result, unmount } = renderHook(() => useProviderHealth());
    await waitFor(() => expect(result.current.loading).toBe(false));

    // Daemon blip: the poll fails. Map must SURVIVE, stale must flip on.
    getMock.mockRejectedValueOnce(new Error("daemon offline"));
    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.stale).toBe(true));
    expect(result.current.byProvider).toEqual({
      anthropic: true,
      "fleet-custom": false,
    });
    expect(result.current.defaultProvider).toBe("fleet-custom");

    // Next successful poll replaces the map and clears stale.
    getMock.mockResolvedValueOnce({
      ...HEALTH,
      providers: [{ provider: "fleet-custom", available: true, class: "custom" }],
    });
    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.stale).toBe(false));
    expect(result.current.byProvider).toEqual({ "fleet-custom": true });
    unmount();
  });

  it("polls on ONE interval and stops polling after unmount", async () => {
    vi.useFakeTimers();
    getMock.mockResolvedValue(HEALTH);
    const { unmount } = renderHook(() => useProviderHealth(1000));
    await act(async () => {}); // flush the immediate first fetch
    expect(getMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(getMock).toHaveBeenCalledTimes(2);

    unmount();
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });
    expect(getMock).toHaveBeenCalledTimes(2); // interval was cleaned up
    vi.useRealTimers();
  });

  it("skips overlapping requests while one is in flight (no polling storm)", async () => {
    vi.useFakeTimers();
    let resolveHealth!: (v: unknown) => void;
    getMock.mockImplementation(
      () =>
        new Promise((res) => {
          resolveHealth = res;
        }),
    );
    const { result, unmount } = renderHook(() => useProviderHealth(1000));
    await act(async () => {});
    expect(getMock).toHaveBeenCalledTimes(1);

    // Three interval ticks + a manual refresh, all while the first request
    // hangs: the guard must swallow every one of them.
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    act(() => result.current.refresh());
    expect(getMock).toHaveBeenCalledTimes(1);

    // Once the hung request settles, polling resumes.
    await act(async () => {
      resolveHealth(HEALTH);
    });
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(getMock).toHaveBeenCalledTimes(2);
    unmount();
    vi.useRealTimers();
  });

  it("passes a real, POSITIVE timeoutMs to /health — lib/api arms the abort only when truthy", async () => {
    // lib/api.ts: `const controller = timeoutMs ? new AbortController() : null` —
    // a timeoutMs of 0/undefined silently disables the timeout entirely, and a
    // hung fetch would then pin the overlap guard forever (no future polls):
    // the exact wedge this hook's HEALTH_TIMEOUT_MS exists to prevent. Pin the
    // VALUE, not just "an options object was passed".
    getMock.mockResolvedValue(HEALTH);
    const { result, unmount } = renderHook(() => useProviderHealth());
    await waitFor(() => expect(result.current.loading).toBe(false));
    const opts = getMock.mock.calls[0][1] as { timeoutMs?: number } | undefined;
    expect(opts?.timeoutMs).toBeGreaterThan(0);
    unmount();
  });

  it("StrictMode double-mount: ONE request, ONE interval, and state still lands", async () => {
    // React 18 dev StrictMode runs effect → cleanup → effect on mount. The
    // cleanup must clear the first interval (else two pollers run forever) and
    // the re-run must re-arm `mounted` (else every state update is skipped and
    // loading spins forever). The remount's immediate refresh is absorbed by
    // the overlap guard (request #1 is still in flight when effects re-run).
    vi.useFakeTimers();
    getMock.mockResolvedValue(HEALTH);
    const { result, unmount } = renderHook(() => useProviderHealth(1000), {
      wrapper: StrictMode,
    });
    await act(async () => {}); // settle the in-flight request
    expect(getMock).toHaveBeenCalledTimes(1);
    expect(result.current.loading).toBe(false); // mounted was re-armed
    expect(result.current.byProvider).toEqual({
      anthropic: true,
      "fleet-custom": false,
    });
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    expect(getMock).toHaveBeenCalledTimes(2); // one interval, not two
    unmount();
    vi.useRealTimers();
  });

  it("FIRST poll fails: empty map + stale + settled — and PreflightNote stays silent", async () => {
    // There is no last-known map to keep, so byProvider is {} and every
    // provider reads as UNKNOWN (`available` undefined). That silence is the
    // honest behaviour: nothing was ever observed, so no provider can be
    // accused — a daemon that is down is the offline banner's job.
    getMock.mockRejectedValue(new Error("daemon offline"));
    const { result, unmount } = renderHook(() => useProviderHealth());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.stale).toBe(true);
    expect(result.current.byProvider).toEqual({});
    render(
      <PreflightNote
        provider="fleet-custom"
        available={result.current.byProvider["fleet-custom"]}
        stale={result.current.stale}
      />,
    );
    expect(screen.queryByTestId("ij-preflight-note")).toBeNull();
    unmount();
  });
});

// ---- PreflightNote ----------------------------------------------------------

describe("PreflightNote", () => {
  it("renders NOTHING when the provider is available", () => {
    render(<PreflightNote provider="fleet-custom" available={true} />);
    expect(screen.queryByTestId("ij-preflight-note")).toBeNull();
  });

  it("renders NOTHING while availability is unknown (undefined)", () => {
    render(<PreflightNote provider="fleet-custom" available={undefined} />);
    expect(screen.queryByTestId("ij-preflight-note")).toBeNull();
  });

  it("warns in amber, compactly, when the provider is known-unavailable", () => {
    render(<PreflightNote provider="fleet-custom" available={false} />);
    const note = screen.getByTestId("ij-preflight-note");
    expect(note.textContent).toContain(
      "fleet-custom isn't reachable right now — this turn will fail.",
    );
    expect(note.textContent).toContain("Pick another model or bring the endpoint back.");
    expect(note.className).toContain("text-amber-300");
    expect(note.className).toContain("text-[11px]");
    expect(note.className).toContain("h-5"); // fixed height — no layout jump
    expect(note.querySelector("button")).toBeNull(); // no buttons, ever
  });

  it("softens the message when the health data itself is stale", () => {
    render(<PreflightNote provider="fleet-custom" available={false} stale />);
    const note = screen.getByTestId("ij-preflight-note");
    expect(note.textContent).toContain("last check couldn't reach the daemon");
    // A stale map is not certain knowledge — never promise the turn WILL fail.
    expect(note.textContent).not.toContain("this turn will fail");
  });
});

// ---- ModelSwitcher availability marks ---------------------------------------

describe("ModelSwitcher availability", () => {
  async function openSwitcher() {
    render(<ModelSwitcher />);
    const trigger = await screen.findByRole("button", {
      name: /switch the active model/i,
    });
    fireEvent.click(trigger);
    await screen.findByText("Active model");
    return trigger;
  }

  it("marks an offline provider's option (struck label + '(offline)') but does NOT disable it", async () => {
    mockDaemon();
    await openSwitcher();

    // Exactly one row carries the offline mark: fleet-custom's.
    const marks = await screen.findAllByText(/\(offline\)/);
    expect(marks).toHaveLength(1);
    const offlineRow = marks[0].closest("button")!;
    expect(offlineRow.textContent).toContain("fleet-llama-70b");
    expect(offlineRow).not.toBeDisabled(); // never trap the user
    const label = within(offlineRow).getByText("fleet-llama-70b");
    expect(label.className).toContain("line-through");

    // The online provider's row is unmarked.
    const onlineRow = screen.getByText("claude-sonnet-4-6").closest("button")!;
    expect(onlineRow.textContent).not.toContain("(offline)");
    expect(within(onlineRow).getByText("claude-sonnet-4-6").className).not.toContain(
      "line-through",
    );

    // Selecting the offline provider still goes through to PUT /settings.
    fireEvent.click(offlineRow);
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/settings", {
        values: {
          default_provider: "fleet-custom",
          default_model: "fleet-llama-70b",
        },
      }),
    );
  });

  it("shows the amber dot on the trigger when the SELECTED provider is offline", async () => {
    mockDaemon(); // default_provider = fleet-custom, which /health says is down
    render(<ModelSwitcher />);
    const dot = await screen.findByTestId("ij-model-offline-dot");
    expect(dot.className).toContain("bg-amber-400");
  });

  it("shows NO amber dot when the selected provider is online — even with another provider down", async () => {
    mockDaemon({
      ...HEALTH,
      default_provider: "anthropic",
      default_model: "claude-sonnet-4-6",
    });
    render(<ModelSwitcher />);
    await screen.findByRole("button", { name: /switch the active model/i });
    expect(screen.queryByTestId("ij-model-offline-dot")).toBeNull();
  });

  it("shows NO amber dot when the selected provider's availability is UNKNOWN (absent from /health)", async () => {
    // Strict `=== false` in `selectedOffline`: a provider /health doesn't list
    // (fresh endpoint, the "auto" sentinel, a race with the poll) is unknown,
    // not offline — a truthiness check would raise a false alarm here.
    mockDaemon({
      ...HEALTH,
      default_provider: "ollama",
      default_model: "llama3.1",
    });
    render(<ModelSwitcher />);
    await screen.findByRole("button", { name: /switch the active model/i });
    expect(screen.queryByTestId("ij-model-offline-dot")).toBeNull();
  });
});
