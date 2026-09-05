/**
 * v1.229.0 (audit Wave 3, task 3D — OBS5 / D8 / CL7): Settings → Maintenance
 * gains Copy diagnostics, Open logs folder and Restore from backup….
 *
 * Pinned:
 *  - Copy diagnostics puts ONE JSON blob on the clipboard through the desktop
 *    bridge: /health version+instance, /diagnostics, /diagnostics/reliability,
 *    /diagnostics/errors — and an endpoint that failed is recorded as
 *    `{ error }`, never silently dropped.
 *  - With no clipboard at all the button does NOT say "Copied": it shows the
 *    JSON in a box under a sentence saying the clipboard is unavailable.
 *  - Open logs folder renders only when the bridge exposes `shell.openLogs`,
 *    calls it, and shows the path it opened (or the shell's refusal).
 *  - Restore lists GET /maintenance/backups, the confirm names the file and
 *    says a restart follows, Confirm POSTs {name} and hands the note to the
 *    page; a 409 from the daemon is shown verbatim and nothing restarts.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));

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

import { ApiError } from "@/lib/api";
import { MaintenanceTools } from "@/components/settings/MaintenanceTools";

const win = window as unknown as Record<string, unknown>;

const BACKUPS = [
  { name: "ironjarvis-backup-20260905-020000.tar.gz", bytes: 2_400_000, modified_at: "2026-09-05T02:00:00+00:00" },
  { name: "ironjarvis-backup-20260904-020000.tar.gz", bytes: 2_300_000, modified_at: "2026-09-04T02:00:00+00:00" },
];

function routeGet(overrides: Record<string, unknown | (() => Promise<unknown>)> = {}) {
  api.get.mockImplementation(async (path: string) => {
    if (path in overrides) {
      const v = overrides[path];
      return typeof v === "function" ? (v as () => Promise<unknown>)() : v;
    }
    switch (path) {
      case "/health":
        return { status: "ok", version: "1.229.0", instance: "inst-1", providers: [], secret: "no" };
      case "/diagnostics":
        return { db_liveness: "ok", db_integrity: "ok", background_loops: {} };
      case "/diagnostics/reliability":
        return { disk: { free: 1, total: 2 }, provider_failures_24h: 0 };
      case "/diagnostics/errors":
        return { capacity: 50, errors: [{ ts: "t", level: "WARNING", logger: "ironjarvis.x", message: "m" }] };
      case "/maintenance/backups":
        return { dir: "C:/home/backups", backups: BACKUPS };
      default:
        throw new Error(`unexpected GET ${path}`);
    }
  });
}

beforeEach(() => {
  api.get.mockReset();
  api.post.mockReset();
  delete win.ironjarvis;
  Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
});
afterEach(() => {
  cleanup();
  delete win.ironjarvis;
});

describe("Copy diagnostics (OBS5)", () => {
  it("copies one JSON blob of the four endpoints through the desktop bridge and says Copied", async () => {
    const written: string[] = [];
    win.ironjarvis = { isDesktop: true, clipboardWriteText: async (t: string) => void written.push(t) };
    routeGet();
    render(<MaintenanceTools onRestartRequested={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Copy diagnostics/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /^Copied$/ })).not.toBeNull());
    expect(written).toHaveLength(1);
    const blob = JSON.parse(written[0]);
    expect(blob.app).toEqual({ version: "1.229.0", instance: "inst-1" }); // version + instance ONLY
    expect(blob.diagnostics.db_liveness).toBe("ok");
    expect(blob.reliability.disk.total).toBe(2);
    expect(blob.recent_errors).toEqual([{ ts: "t", level: "WARNING", logger: "ironjarvis.x", message: "m" }]);
    expect(typeof blob.collected_at).toBe("string");
    expect(screen.queryByText(/Clipboard unavailable/)).toBeNull();
  });

  it("records an endpoint that failed as { error } instead of dropping it", async () => {
    const written: string[] = [];
    win.ironjarvis = { isDesktop: true, clipboardWriteText: async (t: string) => void written.push(t) };
    routeGet({
      "/diagnostics/reliability": () => Promise.reject(new ApiError("internal error [err_1234abcd]: boom", 500)),
    });
    render(<MaintenanceTools onRestartRequested={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Copy diagnostics/ }));
    await waitFor(() => expect(written).toHaveLength(1));
    const blob = JSON.parse(written[0]);
    expect(blob.reliability).toEqual({ error: "internal error [err_1234abcd]: boom" });
    expect(blob.diagnostics.db_liveness).toBe("ok"); // the others still made it
  });

  it("never claims a copy that did not happen: no clipboard -> the JSON is shown to select by hand", async () => {
    routeGet();
    render(<MaintenanceTools onRestartRequested={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Copy diagnostics/ }));
    await screen.findByText(/Clipboard unavailable here/);
    expect(screen.queryByRole("button", { name: /^Copied$/ })).toBeNull();
    const box = screen.getByLabelText("Diagnostics JSON") as HTMLTextAreaElement;
    expect(JSON.parse(box.value).app.version).toBe("1.229.0");
  });
});

describe("Open logs folder (D8)", () => {
  it("renders only when the bridge exposes shell.openLogs", () => {
    routeGet();
    win.ironjarvis = { isDesktop: true, shell: { getState: async () => null } }; // an older shell
    render(<MaintenanceTools onRestartRequested={() => {}} />);
    expect(screen.queryByRole("button", { name: /Open logs folder/ })).toBeNull();
  });

  it("opens the folder through the bridge and shows the path it opened", async () => {
    routeGet();
    const calls: number[] = [];
    win.ironjarvis = {
      isDesktop: true,
      shell: {
        openLogs: async () => {
          calls.push(1);
          return { ok: true, path: "C:\\Users\\me\\AppData\\Roaming\\Iron Jarvis\\logs" };
        },
      },
    };
    render(<MaintenanceTools onRestartRequested={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Open logs folder/ }));
    await screen.findByText("Opened C:\\Users\\me\\AppData\\Roaming\\Iron Jarvis\\logs");
    expect(calls).toHaveLength(1);
  });

  it("shows the shell's failure instead of pretending", async () => {
    routeGet();
    win.ironjarvis = {
      isDesktop: true,
      shell: { openLogs: async () => ({ ok: false, path: "X:\\logs", error: "No application is associated" }) },
    };
    render(<MaintenanceTools onRestartRequested={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Open logs folder/ }));
    await screen.findByText(/Couldn’t open X:\\logs: No application is associated/);
  });
});

describe("Restore from backup (CL7)", () => {
  it("lists the archives, confirms by name with the restart warning, posts the name, hands over the note", async () => {
    routeGet();
    api.post.mockResolvedValue({ ok: true, restored_from: BACKUPS[1].name, files: 12, restart: "scheduled" });
    const notes: string[] = [];
    render(<MaintenanceTools onRestartRequested={(n) => void notes.push(n)} />);
    const select = (await screen.findByLabelText("Backup to restore")) as HTMLSelectElement;
    expect(select.options).toHaveLength(2);
    expect(select.value).toBe(BACKUPS[0].name); // newest preselected
    fireEvent.change(select, { target: { value: BACKUPS[1].name } });
    fireEvent.click(screen.getByRole("button", { name: /Restore from backup…/ }));
    const dialog = await screen.findByRole("dialog", { name: "Confirm restore" });
    expect(dialog.textContent).toContain(BACKUPS[1].name);
    expect(dialog.textContent).toMatch(/Iron Jarvis restarts/);
    expect(api.post).not.toHaveBeenCalled(); // nothing until confirmed
    fireEvent.click(screen.getByRole("button", { name: "Restore and restart" }));
    await waitFor(() => expect(notes).toHaveLength(1));
    expect(api.post).toHaveBeenCalledWith("/maintenance/restore", { name: BACKUPS[1].name });
    expect(notes[0]).toMatch(/Restored 12 file\(s\) from ironjarvis-backup-20260904-020000\.tar\.gz/);
    expect(notes[0]).toMatch(/restarting/);
  });

  it("shows the daemon's 409 verbatim and does not restart", async () => {
    routeGet();
    api.post.mockRejectedValue(
      new ApiError("cannot restore while work is in flight (1 session(s) running) — wait for them to finish or cancel them, then retry", 409),
    );
    const notes: string[] = [];
    render(<MaintenanceTools onRestartRequested={(n) => void notes.push(n)} />);
    await screen.findByLabelText("Backup to restore");
    fireEvent.click(screen.getByRole("button", { name: /Restore from backup…/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Restore and restart" }));
    await screen.findByText(/cannot restore while work is in flight \(1 session\(s\) running\)/);
    expect(notes).toHaveLength(0);
  });

  it("says there are no backups yet when the list is empty", async () => {
    routeGet({ "/maintenance/backups": { dir: "x", backups: [] } });
    render(<MaintenanceTools onRestartRequested={() => {}} />);
    await screen.findByText(/No backups yet/);
    expect(screen.queryByRole("button", { name: /Restore from backup…/ })).toBeNull();
  });
});
