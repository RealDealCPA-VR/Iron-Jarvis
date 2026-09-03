/**
 * v1.226.0 (F-F-1, contract C7) — a non-polled page heals itself when the
 * daemon comes back.
 *
 * Before: `useApi` fetched once; when that one GET died in a daemon restart
 * gap (ApiError status 0) the page showed "Daemon offline" forever, even
 * after DaemonBanner cleared. Now DaemonProvider exposes `epoch` (+1 on each
 * offline->online edge) and useApi re-fetches on it ONLY while its last
 * error was status 0. A consumer whose fetch succeeded is NOT refetched —
 * a transition must not turn into a refetch storm across every page.
 *
 * Uses the REAL DaemonProvider + useApi with `get` mocked, so both halves of
 * the contract are exercised together.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

const H = vi.hoisted(() => ({
  healthOk: false,
  flakyFailsLeft: 1,
  netListeners: new Set<() => void>(),
  getMock: vi.fn(),
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
  H.getMock.mockImplementation(async (path: string) => {
    if (path === "/health") {
      if (!H.healthOk) throw new MockApiError("daemon offline", 0);
      return { status: "ok", version: "1.226.0", providers: [], default_provider: "mock" };
    }
    if (path === "/dead") throw new MockApiError("daemon offline", 0);
    if (path === "/broken") throw new MockApiError("internal error", 500);
    if (path === "/flaky" && H.flakyFailsLeft > 0) {
      // Mirrors the real api.ts catch: signal "could not reach the daemon"
      // BEFORE minting ApiError(…, 0). The mock is what the provider sees.
      H.flakyFailsLeft -= 1;
      H.netListeners.forEach((fn) => fn());
      throw new MockApiError("daemon offline", 0);
    }
    return { ok: true, path };
  });
  return {
    ApiError: MockApiError,
    get: H.getMock,
    onUnauthorizedChange: () => () => {},
    onRequestErrorChange: () => () => {},
    onNetworkError: (fn: () => void) => {
      H.netListeners.add(fn);
      return () => H.netListeners.delete(fn);
    },
  };
});

import { DaemonProvider, useDaemon } from "@/lib/daemon";
import { useApi } from "@/lib/useApi";

function Consumer({ path }: { path: string }) {
  const { data, error } = useApi<{ ok: boolean }>(path);
  return (
    <div data-testid={path}>
      {error ? `err:${error.status}` : data ? "ok" : "loading"}
    </div>
  );
}
function Epoch() {
  const { epoch, online } = useDaemon();
  return <div data-testid="epoch">{`${epoch}:${online ? "on" : "off"}`}</div>;
}

const calls = (path: string) => H.getMock.mock.calls.filter((c) => c[0] === path).length;

beforeEach(() => {
  vi.useFakeTimers();
  H.healthOk = false;
  H.flakyFailsLeft = 1;
  H.netListeners.clear();
  H.getMock.mockClear();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("useApi re-fetches on the daemon's offline->online epoch (v1.226.0)", () => {
  it("refetches the consumer whose GET died offline, leaves the healthy one alone", async () => {
    render(
      <DaemonProvider>
        <Epoch />
        <Consumer path="/dead" />
        <Consumer path="/fine" />
        <Consumer path="/broken" />
      </DaemonProvider>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(screen.getByTestId("/dead").textContent).toBe("err:0");
    expect(screen.getByTestId("/fine").textContent).toBe("ok");
    expect(screen.getByTestId("/broken").textContent).toBe("err:500");
    expect(screen.getByTestId("epoch").textContent).toBe("0:off");
    expect(calls("/dead")).toBe(1);
    expect(calls("/fine")).toBe(1);

    // The daemon comes back; the next /health poll (5s) sees it.
    H.healthOk = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByTestId("epoch").textContent).toBe("1:on");
    // The offline consumer was refetched (still rejecting in this fixture —
    // the point is the SECOND call); the healthy one and the 500 were not.
    expect(calls("/dead")).toBe(2);
    expect(calls("/fine")).toBe(1);
    expect(calls("/broken")).toBe(1);

    // A steady online daemon polls on without bumping the epoch.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(screen.getByTestId("epoch").textContent).toBe("1:on");
    expect(calls("/dead")).toBe(2);
    expect(calls("/fine")).toBe(1);
  });

  it("a restart BETWEEN two polls: the GET that died flips the provider offline, the next poll walks the edge", async () => {
    // Steady online app; the 5s /health poll never sees the 3-4s restart gap.
    H.healthOk = true;
    function Shell({ late }: { late: boolean }) {
      return (
        <DaemonProvider>
          <Epoch />
          <Consumer path="/fine" />
          {late && <Consumer path="/flaky" />}
        </DaemonProvider>
      );
    }
    const { rerender } = render(<Shell late={false} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(screen.getByTestId("epoch").textContent).toBe("1:on");
    expect(calls("/fine")).toBe(1);

    // The user opens a page mid-restart: its one GET cannot reach the daemon
    // (status 0) — and the daemon is back before the next scheduled poll.
    // The network-error signal makes the provider re-poll at once; the next
    // good /health is an offline->online edge, so the status-0 hook refetches
    // (all inside the same act — the recovery is not a separate tick).
    rerender(<Shell late />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(H.flakyFailsLeft).toBe(0); // the first GET did fail with status 0
    expect(screen.getByTestId("epoch").textContent).toBe("2:on");
    expect(calls("/flaky")).toBe(2);
    expect(screen.getByTestId("/flaky").textContent).toBe("ok");
    // The healthy consumer was NOT refetched by the transition.
    expect(calls("/fine")).toBe(1);
  });

  it("the epoch ticks once per edge, not once per poll, and the fallback exposes 0", async () => {
    H.healthOk = true; // before mount: the first poll fires from the effect
    render(
      <DaemonProvider>
        <Epoch />
      </DaemonProvider>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(screen.getByTestId("epoch").textContent).toBe("1:on");
    H.healthOk = false;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByTestId("epoch").textContent).toBe("1:off");
    H.healthOk = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByTestId("epoch").textContent).toBe("2:on");
    cleanup();
    // Outside the provider (a stray component) — the safe fallback.
    render(<Epoch />);
    expect(screen.getByTestId("epoch").textContent).toBe("0:on");
  });
});
