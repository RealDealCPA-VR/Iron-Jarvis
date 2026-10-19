/**
 * v1.354.0 (product-readiness G30 + G31): IRON JARVIS, NOT "THE DAEMON", and
 * coming back is visible.
 *
 * ACCEPTANCE (G30): with the service stopped, a new user sees "Iron Jarvis
 * isn't responding." with "Your chats and files are saved"; no chat bubble,
 * banner, Status headline or splash contains the word daemon.
 * ACCEPTANCE (G31): after the desktop app restarts the service, the user sees
 * "Iron Jarvis is back." for a few seconds.
 *
 * Driven through the REAL DaemonBanner, OfflineHint and Status view, with
 * only the daemon context and the transport mocked. The chat page's own half
 * of G31 (its offline hint clears on the reconnect edge) is pinned in
 * chat-back-online-v13540.test.tsx.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

const D = vi.hoisted(() => ({
  online: false,
  checking: false,
  unauthorized: false,
  requestError: false,
  interrupted: [] as Record<string, unknown>[],
}));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: D.online,
    unauthorized: D.unauthorized,
    requestError: D.requestError,
    health: null,
    checking: D.checking,
    epoch: 0,
    refresh: () => {},
    provided: true,
  }),
}));
vi.mock("@/lib/useApi", () => ({
  useApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
  usePolledApi: (p: string | null) => ({
    data: p === "/sessions/interrupted" ? { sessions: D.interrupted } : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));
vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError: FakeApiError,
    API_BASE: "http://127.0.0.1:8787",
    ijToken: () => "",
    get: async () => {
      throw new FakeApiError("Iron Jarvis isn't responding", 0);
    },
    post: async () => ({}),
    put: async () => ({}),
    del: async () => ({}),
    onUnauthorizedChange: () => () => {},
    onRequestErrorChange: () => () => {},
  };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/chat",
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
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
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => (props: Record<string, unknown>) => createElement(String(tag), strip(props)),
    }),
  };
});

import { DaemonBanner } from "@/components/DaemonBanner";
import { OfflineHint, OFFLINE_WORDS as UI_OFFLINE_WORDS } from "@/components/ui";
import StatusOverview from "@/components/overview/StatusOverview";
import { stallDetail, prepDetail } from "@/lib/useChatStream";
import {
  BACK_LINGER_MS,
  BACK_STOPPED_LINE,
  BACK_TITLE,
  OFFLINE_SAVED_SENTENCE,
  OFFLINE_TITLE,
  OFFLINE_WORDS,
  PART_FAILED_TITLE,
  SIGNED_OUT_TITLE,
} from "@/lib/offlineWords";

const ROOT = path.resolve(__dirname, "..");
const read = (rel: string) => readFileSync(path.join(ROOT, rel), "utf8").replace(/\r\n/g, "\n");
const win = window as unknown as { ironjarvis?: unknown };

function setDesktop(on: boolean) {
  if (on) win.ironjarvis = { isDesktop: true };
  else delete win.ironjarvis;
}

beforeEach(() => {
  D.online = false;
  D.checking = false;
  D.unauthorized = false;
  D.requestError = false;
  D.interrupted = [];
  setDesktop(false);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  setDesktop(false);
});

describe("G30 — the outage says Iron Jarvis, and what is safe", () => {
  it("the words, in one place", () => {
    expect(OFFLINE_WORDS).toBe("Iron Jarvis isn't responding");
    expect(OFFLINE_TITLE).toBe("Iron Jarvis isn't responding.");
    expect(OFFLINE_SAVED_SENTENCE).toMatch(/^Your chats and files are saved/);
    // components/ui re-exports the same words beside its primitives.
    expect(UI_OFFLINE_WORDS).toBe(OFFLINE_WORDS);
  });

  it.each([
    ["inside the desktop app", true],
    ["in a browser tab", false],
  ])("the banner, %s: 'Iron Jarvis isn't responding.' + 'Your chats and files are saved', never 'daemon'", (_n, desktop) => {
    setDesktop(desktop);
    render(<DaemonBanner />);
    const banner = screen.getByTestId("daemon-banner-offline");
    expect(banner.textContent).toContain(OFFLINE_TITLE);
    expect(banner.textContent).toContain("Your chats and files are saved");
    expect(banner.textContent).not.toMatch(/daemon/i);
  });

  it("the token banner and the request banner name the app, never the daemon", () => {
    D.online = true;
    D.unauthorized = true;
    setDesktop(true);
    render(<DaemonBanner />);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(SIGNED_OUT_TITLE);
    expect(alert.textContent).not.toMatch(/daemon/i);
    cleanup();

    D.unauthorized = false;
    D.requestError = true;
    render(<DaemonBanner />);
    const err = screen.getByRole("alert");
    expect(err.textContent).toContain(PART_FAILED_TITLE);
    expect(err.textContent).not.toMatch(/daemon/i);
  });

  it.each([
    ["inside the desktop app", true],
    ["in a browser tab", false],
  ])("the in-page hint, %s, says the same", (_n, desktop) => {
    setDesktop(desktop);
    render(<OfflineHint detail="Chat needs it running to reach your agent." />);
    const hint = screen.getByRole("status");
    expect(hint.textContent).toContain(OFFLINE_TITLE);
    expect(hint.textContent).toContain("Your chats and files are saved");
    expect(hint.textContent).not.toMatch(/daemon/i);
  });

  it("the Status headline says it, never 'Daemon offline'", () => {
    render(<StatusOverview />);
    const line = screen.getAllByRole("status").find((el) => el.textContent === OFFLINE_WORDS);
    expect(line).toBeTruthy();
    expect(screen.queryByText(/Daemon offline/i)).toBeNull();
  });

  it("a chat bubble's stall and preparation words say Iron Jarvis", () => {
    for (const s of [stallDetail(90_000), prepDetail(11 * 60_000)]) {
      expect(s).toContain("Iron Jarvis");
      expect(s).not.toMatch(/daemon/i);
    }
    expect(stallDetail(90_000)).toMatch(/^Nothing came back from Iron Jarvis for 90 s/);
  });

  it("the offline stream frame and a status-0 error carry the same words", () => {
    const stream = read("lib/useChatStream.ts");
    expect(stream).not.toContain('detail: "daemon offline"');
    expect(stream.match(/detail: OFFLINE_WORDS, status: 0, offline: true/g)?.length).toBe(2);
    for (const rel of ["lib/api.ts", "lib/archiveChat.ts"]) {
      const src = read(rel);
      expect(src, rel).not.toContain('"daemon offline"');
      expect(src, rel).toContain("new ApiError(OFFLINE_WORDS, 0)");
    }
  });

  it("the splash says 'Getting Iron Jarvis ready', never daemon", () => {
    const html = readFileSync(path.join(ROOT, "..", "desktop", "loading.html"), "utf8");
    expect(html).toContain("Getting Iron Jarvis ready");
    expect(html).not.toMatch(/daemon/i);
  });
});

describe("G31 — coming back is visible", () => {
  it("after an outage: 'Iron Jarvis is back.' for a few seconds, then nothing", async () => {
    vi.useFakeTimers();
    const view = render(<DaemonBanner />);
    expect(screen.getByTestId("daemon-banner-offline")).toBeInTheDocument();
    expect(screen.queryByTestId("daemon-banner-back")).toBeNull();

    D.online = true; // the desktop app restarted the service; /health answers
    view.rerender(<DaemonBanner />);
    const back = screen.getByTestId("daemon-banner-back");
    expect(back.textContent).toContain(BACK_TITLE);
    expect(back).toHaveAttribute("role", "status");
    expect(screen.queryByTestId("daemon-banner-offline")).toBeNull();
    // Nothing was cut off: no bell line.
    expect(screen.queryByTestId("daemon-banner-back-bell")).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(BACK_LINGER_MS - 100);
    });
    expect(screen.getByTestId("daemon-banner-back")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(screen.queryByTestId("daemon-banner-back")).toBeNull();
  });

  it("names the bell when the restart cut work off", () => {
    D.interrupted = [
      { id: "s1", task: "Rename the scans", agent_type: "builder", interrupted_at: "2026-10-18T09:00:00" },
    ];
    const view = render(<DaemonBanner />);
    D.online = true;
    view.rerender(<DaemonBanner />);
    const back = screen.getByTestId("daemon-banner-back");
    expect(back.textContent).toContain(BACK_TITLE);
    expect(back.textContent).toContain(BACK_STOPPED_LINE);
  });

  it("CONTROL: a page that loads online (the first /health) never says it is back", () => {
    D.checking = true;
    const view = render(<DaemonBanner />);
    D.checking = false;
    D.online = true;
    view.rerender(<DaemonBanner />);
    expect(screen.queryByTestId("daemon-banner-back")).toBeNull();
  });

  it("CONTROL: a token problem clearing is not a return from an outage", () => {
    D.online = true;
    D.unauthorized = true;
    const view = render(<DaemonBanner />);
    D.unauthorized = false;
    view.rerender(<DaemonBanner />);
    expect(screen.queryByTestId("daemon-banner-back")).toBeNull();
  });
});
