/**
 * v1.406.0 (part 6) — a START is not an outage.
 *
 * DaemonProvider used to read the very first /health that could not reach
 * Iron Jarvis as "offline", so a page that loaded while Iron Jarvis was still
 * starting (a browser tab, a window opened mid-restart) showed the warn
 * banner "Iron Jarvis isn't responding." for the whole start. Now, until
 * /health has answered this page ONCE and while the page is younger than
 * STARTING_GRACE_MS (the desktop's own start-up gate), it says the splash's
 * calm "Starting Iron Jarvis…" and nothing that waits for `!checking` speaks.
 * After a first answer, a miss is an outage exactly as before.
 *
 * Real `@/lib/api`, real `DaemonProvider`, real `DaemonBanner`; `fetch` is
 * stubbed and time is fake.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));
vi.mock("@/components/InterruptedJobs", () => ({ useInterruptedJobs: () => ({ jobs: [] }) }));
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const strip = (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) {
      if (!["initial", "animate", "exit", "transition", "variants", "layout"].includes(k)) rest[k] = v;
    }
    return rest;
  };
  return {
    // Mock contract (v1.250.0): every framer-motion mock exports `m`.
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => (props: Record<string, unknown>) => createElement(String(tag), strip(props)),
    }),
  };
});

import { DaemonProvider, STARTING_GRACE_MS, useDaemon } from "@/lib/daemon";
import { DaemonBanner } from "@/components/DaemonBanner";
import { OFFLINE_TITLE, STARTING_TITLE } from "@/lib/offlineWords";

const HEALTH = { status: "ok", version: "1.406.0", default_provider: "mock", default_model: "", providers: [] };

/** /health refuses the connection while `down` is true (fetch rejects = status 0). */
const net = { down: true, healthCalls: 0 };

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const path = url.replace(/^https?:\/\/[^/]+/, "");
      if (path === "/health") {
        net.healthCalls += 1;
        if (net.down) throw new TypeError("Failed to fetch");
      }
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        headers: { get: () => null },
        json: async () => (path === "/health" ? HEALTH : {}),
      } as unknown as Response;
    }),
  );
}

function Probe() {
  const d = useDaemon();
  return (
    <div
      data-testid="probe"
      data-checking={String(d.checking)}
      data-online={String(d.online)}
      data-starting={String(d.starting)}
    />
  );
}

function renderApp() {
  render(
    <DaemonProvider>
      <DaemonBanner />
      <Probe />
    </DaemonProvider>,
  );
}

const advance = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
const probe = () => screen.getByTestId("probe");

beforeEach(() => {
  net.down = true;
  net.healthCalls = 0;
  vi.useFakeTimers();
  stubFetch();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
});

/** The desktop shell's daemon state (main.js `serviceState`). */
function shellWith(daemon: Record<string, unknown>) {
  (window as unknown as { ironjarvis?: unknown }).ironjarvis = {
    isDesktop: true,
    shell: {
      getState: () => ({
        platform: "win32",
        services: {
          daemon: {
            capped: false,
            restarts: 0,
            lastExit: null,
            damaged: false,
            restarting: false,
            cappedCrashes: null,
            cappedWindowMin: null,
            stalled: false,
            ...daemon,
          },
        },
      }),
    },
  };
}

describe("the desktop's known-bad state wins over Starting… (v1.406.0 part 6 follow-up)", () => {
  const cases: Array<[string, Record<string, unknown>, string]> = [
    ["capped", { capped: true, restarts: 10, lastExit: 1, cappedCrashes: 10, cappedWindowMin: 15 }, "capped"],
    ["stalled", { stalled: true }, "stalled"],
    ["damaged", { damaged: true }, "damaged"],
    ["restarting after a crash", { restarting: true, restarts: 2, lastExit: 1 }, "restarting"],
  ];
  for (const [label, daemon, view] of cases) {
    it(`${label}: the offline banner shows at once, not the calm line`, async () => {
      shellWith(daemon);
      renderApp();
      await advance(50);
      expect(screen.queryByTestId("daemon-banner-starting")).toBeNull();
      const banner = screen.getByTestId("daemon-banner-offline");
      expect(banner.dataset.shellState).toBe(view);
    });
  }

  it("a normal first spawn the shell calls 'restarting' (no crash yet) stays calm", async () => {
    shellWith({ restarting: true, restarts: 0 });
    renderApp();
    await advance(50);
    expect(screen.getByTestId("daemon-banner-starting")).toBeTruthy();
    expect(screen.queryByTestId("daemon-banner-offline")).toBeNull();
  });

  it("a desktop shell that is merely waiting stays calm, and the line goes when Iron Jarvis answers", async () => {
    shellWith({});
    renderApp();
    await advance(50);
    expect(screen.getByTestId("daemon-banner-starting")).toBeTruthy();
    net.down = false;
    await advance(1_500);
    expect(screen.queryByTestId("daemon-banner-starting")).toBeNull();
    expect(screen.queryByTestId("daemon-banner-offline")).toBeNull();
  });

  it("a crash the shell reports DURING the start replaces the calm line within one shell read", async () => {
    let crashed = false;
    (window as unknown as { ironjarvis?: unknown }).ironjarvis = {
      isDesktop: true,
      shell: {
        getState: () => ({
          services: { daemon: { capped: crashed, restarts: crashed ? 10 : 0, restarting: false, damaged: false, stalled: false } },
        }),
      },
    };
    renderApp();
    await advance(50);
    expect(screen.getByTestId("daemon-banner-starting")).toBeTruthy();
    crashed = true;
    await advance(3_100);
    expect(screen.queryByTestId("daemon-banner-starting")).toBeNull();
    expect(screen.getByTestId("daemon-banner-offline").dataset.shellState).toBe("capped");
  });
});

describe("Iron Jarvis starting is said calmly (v1.406.0 part 6)", () => {
  it("says Starting… (never isn't responding) until the first answer, then nothing", async () => {
    renderApp();
    await advance(50);
    expect(screen.getByTestId("daemon-banner-starting").textContent).toContain(STARTING_TITLE);
    expect(screen.queryByTestId("daemon-banner-offline")).toBeNull();
    expect(probe().dataset.checking).toBe("true");
    expect(probe().dataset.starting).toBe("true");
    // Twenty seconds of a slow start: still calm, and asked about once a second.
    await advance(20_000);
    expect(screen.queryByTestId("daemon-banner-offline")).toBeNull();
    expect(screen.getByTestId("daemon-banner-starting")).toBeTruthy();
    expect(net.healthCalls).toBeGreaterThanOrEqual(15);
    // Iron Jarvis answers: the line goes, with no "is back" (it never went away).
    net.down = false;
    await advance(1_500);
    expect(screen.queryByTestId("daemon-banner-starting")).toBeNull();
    expect(screen.queryByTestId("daemon-banner-offline")).toBeNull();
    expect(screen.queryByTestId("daemon-banner-back")).toBeNull();
    expect(probe().dataset.online).toBe("true");
    expect(probe().dataset.checking).toBe("false");
    expect(document.body.textContent).not.toContain(OFFLINE_TITLE);
  });

  it("past the start-up grace it is an outage, said as before", async () => {
    renderApp();
    await advance(STARTING_GRACE_MS + 6_000);
    expect(screen.queryByTestId("daemon-banner-starting")).toBeNull();
    expect(screen.getByTestId("daemon-banner-offline").textContent).toContain(OFFLINE_TITLE);
    expect(probe().dataset.checking).toBe("false");
  });

  it("once Iron Jarvis has answered, losing it is an outage at once (no grace)", async () => {
    net.down = false;
    renderApp();
    await advance(50);
    expect(probe().dataset.online).toBe("true");
    net.down = true;
    await advance(5_200); // the next 5 s poll misses, its confirm re-poll misses
    expect(screen.queryByTestId("daemon-banner-starting")).toBeNull();
    expect(screen.getByTestId("daemon-banner-offline")).toBeTruthy();
  });
});
