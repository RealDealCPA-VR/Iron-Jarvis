/**
 * v1.312.0 (wave 4, desktop-offline-banner-lies-when-capped) — inside the
 * desktop app the "Daemon offline" banner says what the SHELL knows.
 *
 * Before: the banner always printed DESKTOP_OFFLINE_HINT ("restarting its local
 * service… use the tray → Quit and relaunch"), even after main.js had stopped
 * restarting a crash-looping daemon; Retry only re-probed, so the user could
 * press it for ever.
 *
 * Contract (the implementer follows these names):
 *  - window.ironjarvis.shell.getState() → { …, services: { daemon: { capped,
 *    restarts, lastExit, damaged } } }, null when refused. The banner reads it
 *    on mount WHILE OFFLINE and re-reads it at least every 5 s while offline;
 *    never while online.
 *  - window.ironjarvis.shell.restartDaemon() → true | null (refused). After it
 *    resolves the banner re-reads shell state at once.
 *  - The offline banner carries data-testid="daemon-banner-offline" and
 *    data-shell-state = "restarting" | "capped" | "unknown" (unknown = browser,
 *    an older shell, or getState refused).
 *  - restarting → the restarting wording + "attempt N" when N ≥ 1.
 *  - capped → "Iron Jarvis's service stopped after N crashes …", a
 *    [Restart Iron Jarvis] button (only when restartDaemon exists) and an
 *    [Open logs] button (shell.openLogs). A refused restart shows a note with
 *    data-testid="daemon-restart-note" pointing at the tray.
 *  - DESKTOP_OFFLINE_HINT points at the tray's "Restart Iron Jarvis", not Quit.
 *  - A browser tab (no shell) is unchanged: the CLI line, no restart button.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const D = vi.hoisted(() => ({ online: false }));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: D.online,
    unauthorized: false,
    requestError: false,
    health: null,
    checking: false,
    epoch: 0,
    refresh: () => {},
  }),
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
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => (props: Record<string, unknown>) => createElement(String(tag), strip(props)),
    }),
  };
});

import { DaemonBanner } from "@/components/DaemonBanner";
import { DESKTOP_OFFLINE_HINT } from "@/lib/desktopShell";

// Review round (v1.312.0): the shell also says whether a restart is REALLY
// under way (`restarting`) and, once capped, the toast's own numbers
// (`cappedCrashes` in `cappedWindowMin`). The fixtures carry what main.js
// sends in each state; the assertions below are unchanged.
type Daemon = {
  capped: boolean;
  restarts: number;
  lastExit: number | null;
  damaged: boolean;
  restarting: boolean;
  cappedCrashes: number | null;
  cappedWindowMin: number | null;
  stalled: boolean;
};
const daemonState = (d: Partial<Daemon>) => ({
  platform: "win32",
  hotkeys: { window: "Ctrl+Shift+J", spotlight: null },
  preferred: { window: "Ctrl+Shift+J", spotlight: "Ctrl+Shift+Space" },
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
      ...d,
    },
  },
});
const RESTARTING = (n: number) => daemonState({ restarts: n, lastExit: 1, restarting: true });
const CAPPED = daemonState({ capped: true, restarts: 10, lastExit: 1, cappedCrashes: 10, cappedWindowMin: 15 });

type Shell = {
  getState?: ReturnType<typeof vi.fn>;
  openLogs?: ReturnType<typeof vi.fn>;
  restartDaemon?: ReturnType<typeof vi.fn>;
};
const win = window as unknown as { ironjarvis?: { isDesktop?: boolean; shell?: Shell } };
function setShell(shell: Shell | null) {
  if (shell === null) delete win.ironjarvis;
  else win.ironjarvis = { isDesktop: true, shell };
}

const banner = () => screen.getByTestId("daemon-banner-offline");

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  setShell(null);
  D.online = false;
});

describe("desktop offline banner tells the truth (v1.312.0)", () => {
  it("the hint points at the tray's Restart item, not Quit", () => {
    expect(DESKTOP_OFFLINE_HINT).toMatch(/restarting its local service/);
    expect(DESKTOP_OFFLINE_HINT).toMatch(/Restart Iron Jarvis/);
    expect(DESKTOP_OFFLINE_HINT).not.toMatch(/Quit/);
    // Second review round: the tray item exists only once restarts stop, so the
    // hint says it WILL appear — never "use" an item that is not there yet.
    expect(DESKTOP_OFFLINE_HINT).toMatch(/will appear in the tray menu/);
  });

  it("restarting: the restarting words with the attempt count, no restart button", async () => {
    const getState = vi.fn().mockResolvedValue(RESTARTING(2));
    setShell({ getState, openLogs: vi.fn(), restartDaemon: vi.fn() });
    render(<DaemonBanner />);
    await waitFor(() => expect(banner()).toHaveAttribute("data-shell-state", "restarting"));
    expect(banner()).toHaveTextContent(/restarting its local service/);
    expect(banner()).toHaveTextContent(/attempt 2\b/i);
    expect(screen.queryByRole("button", { name: /Restart Iron Jarvis/ })).toBeNull();
    expect(getState).toHaveBeenCalled();
  });

  it("capped: says the service stopped after N crashes and offers Restart + Open logs", async () => {
    const restartDaemon = vi.fn().mockResolvedValue(true);
    const openLogs = vi.fn().mockResolvedValue({ ok: true, path: "C:/logs" });
    setShell({ getState: vi.fn().mockResolvedValue(CAPPED), openLogs, restartDaemon });
    render(<DaemonBanner />);
    await waitFor(() => expect(banner()).toHaveAttribute("data-shell-state", "capped"));
    expect(banner()).toHaveTextContent(/stopped after 10 crashes/);
    // The lie this wave removes: a capped service is NOT restarting.
    expect(banner()).not.toHaveTextContent(/restarting its local service/);

    fireEvent.click(screen.getByRole("button", { name: /Open logs/ }));
    expect(openLogs).toHaveBeenCalledTimes(1);
    expect(restartDaemon).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Restart Iron Jarvis/ }));
    await waitFor(() => expect(restartDaemon).toHaveBeenCalledTimes(1));
  });

  it("after a restart the banner re-reads the shell at once and stops saying stopped", async () => {
    const getState = vi.fn().mockResolvedValueOnce(CAPPED).mockResolvedValue(RESTARTING(0));
    const restartDaemon = vi.fn().mockResolvedValue(true);
    setShell({ getState, openLogs: vi.fn(), restartDaemon });
    render(<DaemonBanner />);
    await waitFor(() => expect(banner()).toHaveAttribute("data-shell-state", "capped"));
    const readsBefore = getState.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: /Restart Iron Jarvis/ }));
    await waitFor(() => expect(banner()).toHaveAttribute("data-shell-state", "restarting"));
    expect(getState.mock.calls.length).toBeGreaterThan(readsBefore);
    expect(banner()).toHaveTextContent(/restarting its local service/);
    expect(banner()).not.toHaveTextContent(/stopped after/);
    expect(screen.queryByRole("button", { name: /Restart Iron Jarvis/ })).toBeNull();
  });

  it("a refused restart says so and points at the tray", async () => {
    const restartDaemon = vi.fn().mockResolvedValue(null);
    setShell({ getState: vi.fn().mockResolvedValue(CAPPED), openLogs: vi.fn(), restartDaemon });
    render(<DaemonBanner />);
    await waitFor(() => expect(banner()).toHaveAttribute("data-shell-state", "capped"));
    // Anti-vacuity: no note before the press.
    expect(screen.queryByTestId("daemon-restart-note")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Restart Iron Jarvis/ }));
    await waitFor(() => expect(screen.getByTestId("daemon-restart-note")).toHaveTextContent(/tray/i));
    expect(restartDaemon).toHaveBeenCalledTimes(1);
  });

  it("an older shell with no restartDaemon never shows a dead Restart button", async () => {
    setShell({ getState: vi.fn().mockResolvedValue(CAPPED), openLogs: vi.fn() });
    render(<DaemonBanner />);
    await waitFor(() => expect(banner()).toHaveAttribute("data-shell-state", "capped"));
    expect(banner()).toHaveTextContent(/stopped after 10 crashes/);
    expect(screen.getByRole("button", { name: /Open logs/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Restart Iron Jarvis/ })).toBeNull();
  });

  it("polls the shell while offline: a ladder that gives up shows up within 5 s", async () => {
    vi.useFakeTimers();
    const getState = vi.fn().mockResolvedValueOnce(RESTARTING(9)).mockResolvedValue(CAPPED);
    setShell({ getState, openLogs: vi.fn(), restartDaemon: vi.fn() });
    render(<DaemonBanner />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(banner()).toHaveAttribute("data-shell-state", "restarting");
    expect(banner()).toHaveTextContent(/attempt 9\b/i);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(banner()).toHaveAttribute("data-shell-state", "capped");
    expect(banner()).toHaveTextContent(/stopped after 10 crashes/);
  });

  it("never polls the shell while the daemon is online", async () => {
    vi.useFakeTimers();
    D.online = true;
    const getState = vi.fn().mockResolvedValue(CAPPED);
    setShell({ getState, openLogs: vi.fn(), restartDaemon: vi.fn() });
    render(<DaemonBanner />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    expect(screen.queryByTestId("daemon-banner-offline")).toBeNull();
    expect(getState).not.toHaveBeenCalled();
  });

  it("getState refused (null) or absent: the default restarting hint, no restart button", async () => {
    const getState = vi.fn().mockResolvedValue(null);
    setShell({ getState, openLogs: vi.fn(), restartDaemon: vi.fn() });
    render(<DaemonBanner />);
    await waitFor(() => expect(getState).toHaveBeenCalled());
    expect(banner()).toHaveAttribute("data-shell-state", "unknown");
    expect(banner()).toHaveTextContent(/restarting its local service/);
    expect(screen.queryByRole("button", { name: /Restart Iron Jarvis/ })).toBeNull();
    cleanup();

    // The v1.226.0 shape: a shell whose preload has no `shell` bridge at all.
    win.ironjarvis = { isDesktop: true };
    render(<DaemonBanner />);
    expect(banner()).toHaveAttribute("data-shell-state", "unknown");
    expect(banner()).toHaveTextContent(/restarting its local service/);
  });

  // Review round: two ways the first cut said something untrue.
  it("capped: the crash count is the toast's window count, never the ladder counter", async () => {
    // 9 fast deaths, one run over 5 minutes (the ladder counter resets), two
    // more deaths: capped with restarts=2 — but 10 crashes in 15 minutes.
    setShell({
      getState: vi
        .fn()
        .mockResolvedValue(daemonState({ capped: true, restarts: 2, lastExit: 1, cappedCrashes: 10, cappedWindowMin: 15 })),
      openLogs: vi.fn(),
      restartDaemon: vi.fn(),
    });
    render(<DaemonBanner />);
    await waitFor(() => expect(banner()).toHaveAttribute("data-shell-state", "capped"));
    expect(banner()).toHaveTextContent(/stopped after 10 crashes in 15 minutes/);
    expect(banner()).not.toHaveTextContent(/after 2 crash/);
  });

  it("capped without the toast's numbers says 'repeated crashes', never a guessed count", async () => {
    setShell({ getState: vi.fn().mockResolvedValue(daemonState({ capped: true, restarts: 2 })), openLogs: vi.fn() });
    render(<DaemonBanner />);
    await waitFor(() => expect(banner()).toHaveAttribute("data-shell-state", "capped"));
    expect(banner()).toHaveTextContent(/stopped after repeated crashes/);
    expect(banner()).not.toHaveTextContent(/after 2 crash/);
  });

  it("a daemon that is up but not answering is not on 'restart attempt N'", async () => {
    // Crashed twice this morning, ran fine for hours, now stalls: the ladder
    // counter still says 2, but nothing is restarting.
    // Second review round: this used to assert the "restarting its local
    // service" words — the very thing the comment above says is not true. The
    // watchdog has not acted yet, so the banner says it is not answering and
    // WILL be restarted ("waiting"), with no attempt number.
    const getState = vi.fn().mockResolvedValue(daemonState({ restarts: 2, lastExit: 1, restarting: false }));
    setShell({ getState, openLogs: vi.fn(), restartDaemon: vi.fn() });
    render(<DaemonBanner />);
    await waitFor(() => expect(banner()).toHaveAttribute("data-shell-state", "waiting"));
    expect(getState).toHaveBeenCalled();
    expect(banner()).toHaveTextContent(/is not answering/);
    expect(banner()).toHaveTextContent(/restarts it if it stays silent/);
    expect(banner()).not.toHaveTextContent(/restarting its local service/);
    expect(banner()).not.toHaveTextContent(/attempt/i);
    // Not stopped: the supervisor still owns it, so no Restart yet.
    expect(screen.queryByRole("button", { name: /Restart Iron Jarvis/ })).toBeNull();
  });

  it("stalled (the watchdog's breaker): says restarts are paused and offers Restart + Open logs", async () => {
    // Second review round: up, not answering, and three watchdog kills in 15
    // minutes — the breaker stopped killing it. Nothing is restarting.
    const restartDaemon = vi.fn().mockResolvedValue(true);
    const openLogs = vi.fn().mockResolvedValue({ ok: true, path: "C:/logs" });
    const getState = vi
      .fn()
      .mockResolvedValueOnce(daemonState({ restarts: 2, lastExit: 1, restarting: false, stalled: true }))
      .mockResolvedValue(RESTARTING(0));
    setShell({ getState, openLogs, restartDaemon });
    render(<DaemonBanner />);
    await waitFor(() => expect(banner()).toHaveAttribute("data-shell-state", "stalled"));
    expect(banner()).toHaveTextContent(/stopped answering/);
    expect(banner()).toHaveTextContent(/automatic restarts are paused/);
    expect(banner()).not.toHaveTextContent(/restarting its local service/);
    expect(banner()).not.toHaveTextContent(/attempt/i);
    // Restart takes Retry's place, as in the capped view.
    expect(screen.queryByRole("button", { name: /Retry connection/ })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Open logs/ }));
    expect(openLogs).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /Restart Iron Jarvis/ }));
    await waitFor(() => expect(banner()).toHaveAttribute("data-shell-state", "restarting"));
    expect(restartDaemon).toHaveBeenCalledTimes(1);
  });

  it("a browser tab is unchanged: the CLI line and no restart button", () => {
    setShell(null);
    render(<DaemonBanner />);
    expect(banner()).toHaveAttribute("data-shell-state", "unknown");
    expect(banner()).toHaveTextContent(/uv run ironjarvis serve/);
    expect(banner()).not.toHaveTextContent(/restarting its local service/);
    expect(screen.queryByRole("button", { name: /Restart Iron Jarvis/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Open logs/ })).toBeNull();
  });
});
