/**
 * v1.226.0 (F-E-8) — inside the packaged desktop app the offline copy no
 * longer tells the user to run `uv run ironjarvis serve`. The Electron
 * preload exposes `window.ironjarvis.isDesktop`; there the daemon is
 * supervised (restart ladder), so both offline surfaces — the OfflineHint
 * card and the app-wide DaemonBanner — say the service is restarting and
 * point at tray -> Quit and relaunch. A browser tab keeps the CLI line.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

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
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => (props: Record<string, unknown>) => createElement(String(tag), strip(props)),
    }),
  };
});

import { OfflineHint } from "@/components/ui";
import { DaemonBanner } from "@/components/DaemonBanner";
import { isDesktopShell } from "@/lib/desktopShell";

type W = Window & { ironjarvis?: { isDesktop?: boolean } };
const setDesktop = (on: boolean) => {
  if (on) (window as W).ironjarvis = { isDesktop: true };
  else delete (window as W).ironjarvis;
};

afterEach(() => {
  cleanup();
  setDesktop(false);
});

describe("offline copy inside the desktop shell (v1.226.0)", () => {
  it("isDesktopShell reads the preload flag", () => {
    expect(isDesktopShell()).toBe(false);
    setDesktop(true);
    expect(isDesktopShell()).toBe(true);
  });

  it("OfflineHint: desktop -> restarting/tray hint; browser -> the CLI line", () => {
    setDesktop(true);
    render(<OfflineHint detail="settings" />);
    expect(screen.getByText(/restarting its local service/)).toBeInTheDocument();
    expect(screen.getByText(/tray → Quit and relaunch/)).toBeInTheDocument();
    expect(screen.queryByText(/uv run ironjarvis serve/)).toBeNull();
    // The per-page detail still rides along.
    expect(screen.getByText(/— settings/)).toBeInTheDocument();
    cleanup();

    setDesktop(false);
    render(<OfflineHint />);
    expect(screen.getByText(/uv run ironjarvis serve/)).toBeInTheDocument();
    expect(screen.queryByText(/restarting its local service/)).toBeNull();
  });

  it("DaemonBanner: desktop -> restarting/tray hint; browser -> the CLI line", () => {
    D.online = false;
    setDesktop(true);
    render(<DaemonBanner />);
    expect(screen.getByText(/Daemon offline\./)).toBeInTheDocument();
    expect(screen.getByText(/restarting its local service/)).toBeInTheDocument();
    expect(screen.queryByText(/uv run ironjarvis serve/)).toBeNull();
    cleanup();

    setDesktop(false);
    render(<DaemonBanner />);
    expect(screen.getByText(/uv run ironjarvis serve/)).toBeInTheDocument();
    expect(screen.queryByText(/restarting its local service/)).toBeNull();
  });
});
