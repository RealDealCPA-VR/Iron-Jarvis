/**
 * v1.433.0 (Wave 1, A8) — Settings › Maintenance's "Restart Iron Jarvis".
 *
 * The press used to POST /shutdown with no body: the daemon's QUIT (Build ›
 * Code's coding tools stopped) and, to the desktop app's supervisor, a crash
 * (the 10-in-15-minutes cap, "has crashed N times" toasts). Inside the
 * desktop app it now asks the SHELL (`shell.restartDaemonTidy`, which marks
 * the exit as asked for and posts `{"restart": true}`); in a plain browser it
 * keeps today's stop and says honestly that a page cannot start it again.
 * A restore from a backup stops the daemon itself, so it tells the shell
 * first (`{stopping: true}`).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError,
    API_BASE: "http://test",
    ijToken: () => "tok-1",
    get: (...a: unknown[]) => api.get(...a),
    post: (...a: unknown[]) => api.post(...a),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ refresh: () => {}, online: true, health: null, provided: true }),
}));

import { MaintenanceCard } from "@/components/settings/MaintenanceCard";
import { MaintenanceTools } from "@/components/settings/MaintenanceTools";
import { RESTART_WORDS } from "@/lib/restartWords";
import { requestTidyRestart } from "@/lib/desktopShell";

const win = window as unknown as Record<string, unknown>;

const BACKUPS = [
  { name: "ironjarvis-backup-20260905-020000.tar.gz", bytes: 2_400_000, modified_at: "2026-09-05T02:00:00+00:00" },
];

function routeGet() {
  api.get.mockImplementation(async (p: string) => {
    if (p === "/maintenance/backups") return { dir: "C:/home/backups", backups: BACKUPS };
    if (p === "/maintenance/storage") return { total_bytes: 0, rows: [] };
    return {};
  });
}

/** ConfirmButton: one press arms, the second confirms. */
async function pressRestart() {
  fireEvent.click(await screen.findByRole("button", { name: RESTART_WORDS.label }));
  fireEvent.click(await screen.findByRole("button", { name: /Confirm restart/ }));
}

const shutdownPosts = () => api.post.mock.calls.filter((c) => c[0] === "/shutdown");

beforeEach(() => {
  api.get.mockReset();
  api.post.mockReset();
  api.post.mockResolvedValue({});
  routeGet();
  delete win.ironjarvis;
});
afterEach(() => {
  cleanup();
  delete win.ironjarvis;
});

describe("Settings › Maintenance › Restart Iron Jarvis", () => {
  it("inside the desktop app asks the shell for a tidy restart and posts no bare /shutdown", async () => {
    const restartDaemonTidy = vi.fn().mockResolvedValue(true);
    win.ironjarvis = { isDesktop: true, shell: { restartDaemonTidy } };
    render(<MaintenanceCard />);
    expect(await screen.findByText(RESTART_WORDS.blurb)).toBeTruthy();
    await pressRestart();
    await waitFor(() => expect(restartDaemonTidy).toHaveBeenCalledTimes(1));
    expect(restartDaemonTidy.mock.calls[0][0]).toBeUndefined(); // not "already stopping"
    expect(await screen.findByText(RESTART_WORDS.restartingNote, {}, { timeout: 6000 })).toBeTruthy();
    expect(shutdownPosts()).toEqual([]);
  });

  it("says so when the desktop app could not restart it, and stops nothing behind its back", async () => {
    const restartDaemonTidy = vi.fn().mockResolvedValue(false);
    win.ironjarvis = { isDesktop: true, shell: { restartDaemonTidy } };
    render(<MaintenanceCard />);
    await pressRestart();
    expect(await screen.findByText(RESTART_WORDS.refused)).toBeTruthy();
    expect(shutdownPosts()).toEqual([]);
  });

  it("in a browser keeps today's stop and says a page cannot start it again", async () => {
    render(<MaintenanceCard />);
    expect(await screen.findByText(RESTART_WORDS.browserBlurb)).toBeTruthy();
    await pressRestart();
    await waitFor(() => expect(shutdownPosts()).toHaveLength(1));
    expect(shutdownPosts()[0]).toEqual(["/shutdown"]);
    expect(await screen.findByText(RESTART_WORDS.stoppingNote, {}, { timeout: 6000 })).toBeTruthy();
  });

  it("an older desktop app with no tidy bridge keeps the old press and the old words", async () => {
    win.ironjarvis = { isDesktop: true, shell: { restartDaemon: vi.fn() } };
    render(<MaintenanceCard />);
    expect(await screen.findByText(RESTART_WORDS.blurb)).toBeTruthy();
    await pressRestart();
    await waitFor(() => expect(shutdownPosts()).toHaveLength(1));
    expect(await screen.findByText(RESTART_WORDS.restartingNote, {}, { timeout: 6000 })).toBeTruthy();
  });
});

describe("Restore from backup tells the desktop app first", () => {
  it("asks the shell to expect the stop the restore makes", async () => {
    const order: string[] = [];
    const restartDaemonTidy = vi.fn(async (o?: { stopping?: boolean }) => {
      order.push(`tidy:${JSON.stringify(o ?? null)}`);
      return true;
    });
    win.ironjarvis = { isDesktop: true, shell: { restartDaemonTidy } };
    api.post.mockImplementation(async (p: string) => {
      order.push(`post:${p}`);
      return { ok: true, restored_from: BACKUPS[0].name, files: 3, restart: "scheduled" };
    });
    const notes: string[] = [];
    render(<MaintenanceTools onRestartRequested={(n) => void notes.push(n)} />);
    await screen.findByLabelText("Backup to restore");
    fireEvent.click(screen.getByRole("button", { name: /Restore from backup…/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Restore and restart" }));
    await waitFor(() => expect(notes).toHaveLength(1));
    // After the restore answered (it is what stops Iron Jarvis), before the note.
    expect(order).toEqual(["post:/maintenance/restore", 'tidy:{"stopping":true}']);
  });
});

describe("requestTidyRestart", () => {
  it("answers no-bridge / restarting / refused and never throws", async () => {
    expect(await requestTidyRestart()).toBe("no-bridge");
    win.ironjarvis = { shell: { restartDaemonTidy: async () => true } };
    expect(await requestTidyRestart()).toBe("restarting");
    win.ironjarvis = { shell: { restartDaemonTidy: async () => null } };
    expect(await requestTidyRestart()).toBe("refused");
    win.ironjarvis = {
      shell: {
        restartDaemonTidy: async () => {
          throw new Error("ipc gone");
        },
      },
    };
    expect(await requestTidyRestart({ stopping: true })).toBe("refused");
  });

  it("the older full settings form takes the same bridge before its /shutdown", () => {
    const src = readFileSync(
      path.join(__dirname, "..", "components", "settings", "pages", "LegacySettingsForm.tsx"),
      "utf8",
    ).replace(/\r\n/g, "\n");
    const fn = src.slice(src.indexOf("async function restartDaemon()"));
    const body = fn.slice(0, fn.indexOf("\n  }\n"));
    expect(body).toContain("await requestTidyRestart()");
    expect(body.indexOf("requestTidyRestart()")).toBeLessThan(body.indexOf('post("/shutdown")'));
    expect(body).toContain('if (tidy === "no-bridge")');
    expect(src).toContain("RESTART_WORDS.browserBlurb");
  });
});
