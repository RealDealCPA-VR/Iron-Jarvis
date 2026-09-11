/**
 * v1.249.0 (R-05): Settings → Maintenance gains "Copy backups to another drive".
 *
 * Pinned:
 *  - the folder and the media box come from GET /settings; Save sends BOTH
 *    through PUT /settings `values` (trimmed), and a refusal (400) is shown
 *    verbatim — the daemon's sentence names why the folder can't be used;
 *  - the status line is plain words from GET /maintenance/backups → `mirror`:
 *    off, the drive is missing (named, amber), no copy yet, the last copy with
 *    its media count, a partial copy, or why the last copy failed;
 *  - `refreshKey` (bumped by the page after "Back up now") re-reads it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));

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
    put: (...a: unknown[]) => api.put(...a),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

import { ApiError } from "@/lib/api";
import { MaintenanceTools, mirrorLine, type MirrorStatus } from "@/components/settings/MaintenanceTools";

const DIR = "D:\\Iron Jarvis backups";

function mirror(over: Partial<MirrorStatus> = {}): MirrorStatus {
  return { dir: DIR, media: true, configured: true, missing: false, last: null, ...over };
}

function routeGet(settings: Record<string, unknown>, m: MirrorStatus | null) {
  api.get.mockImplementation(async (path: string) => {
    if (path === "/settings") return { settings };
    if (path === "/maintenance/backups") return { dir: "C:/home/backups", backups: [], mirror: m };
    throw new Error(`unexpected GET ${path}`);
  });
}

beforeEach(() => {
  api.get.mockReset();
  api.post.mockReset();
  api.put.mockReset();
});
afterEach(() => cleanup());

describe("mirrorLine — the status sentence", () => {
  it("says off, missing, no copy yet, the last copy, partial and failed in plain words", () => {
    expect(mirrorLine(null).text).toMatch(/Off — backups are kept on this PC only/);
    const miss = mirrorLine(mirror({ missing: true }));
    expect(miss.tone).toBe("warn");
    expect(miss.text).toContain(DIR);
    expect(miss.text).toMatch(/is the drive plugged in\?/);
    expect(mirrorLine(mirror()).text).toMatch(/No copy yet/);
    const ok = mirrorLine(
      mirror({ last: { ok: true, at: "2026-09-11T20:00:00+00:00", media: { copied: 3, truncated: true } } }),
    );
    expect(ok.tone).toBe("ok");
    expect(ok.text).toMatch(/^Last copy .+ · 3 new media files copied \(the rest follow with the next backup\)$/);
    expect(mirrorLine(mirror({ last: { ok: true, at: "x", media: { copied: 0 } } })).text).toMatch(/media up to date/);
    const partial = mirrorLine(mirror({ last: { ok: true, at: "x", error: "1 media file(s) could not be copied" } }));
    expect(partial.tone).toBe("warn");
    expect(partial.text).toContain("could not be copied");
    const failed = mirrorLine(mirror({ last: { ok: false, error: "couldn't copy the backup to D:\\x: disk full" } }));
    expect(failed.tone).toBe("warn");
    expect(failed.text).toBe("The last copy didn’t complete: couldn't copy the backup to D:\\x: disk full");
  });
});

describe("Copy backups to another drive (the card)", () => {
  it("shows the saved folder and the missing-drive line", async () => {
    routeGet({ backup_mirror_dir: DIR, backup_mirror_media: true }, mirror({ missing: true }));
    render(<MaintenanceTools onRestartRequested={() => {}} />);
    await waitFor(() => expect((screen.getByLabelText("Backup copy folder") as HTMLInputElement).value).toBe(DIR));
    await waitFor(() =>
      expect(screen.getByTestId("backup-mirror-status").textContent).toMatch(/is the drive plugged in\?/),
    );
    expect((screen.getByRole("checkbox", { name: /generated images, video and audio/ }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(/keep that drive somewhere safe/)).toBeTruthy();
  });

  it("Save sends the trimmed folder and the media box, then re-reads the status", async () => {
    routeGet({ backup_mirror_dir: "", backup_mirror_media: true }, mirror({ configured: false, dir: "" }));
    api.put.mockResolvedValue({ updated: ["backup_mirror_dir", "backup_mirror_media"] });
    render(<MaintenanceTools onRestartRequested={() => {}} />);
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/settings"));
    fireEvent.change(screen.getByLabelText("Backup copy folder"), { target: { value: `  ${DIR}  ` } });
    fireEvent.click(screen.getByRole("checkbox", { name: /generated images, video and audio/ }));
    const statusReads = api.get.mock.calls.filter((c) => c[0] === "/maintenance/backups").length;
    fireEvent.click(screen.getByRole("button", { name: /Save/ }));
    await screen.findByText(`Saved — every backup is now also copied to ${DIR}.`);
    expect(api.put).toHaveBeenCalledWith("/settings", {
      values: { backup_mirror_dir: DIR, backup_mirror_media: false },
    });
    expect(api.get.mock.calls.filter((c) => c[0] === "/maintenance/backups").length).toBeGreaterThan(statusReads);
  });

  it("shows the daemon's refusal verbatim and saves nothing", async () => {
    routeGet({ backup_mirror_dir: "", backup_mirror_media: true }, mirror({ configured: false, dir: "" }));
    api.put.mockRejectedValue(
      new ApiError("backup copy folder: folder does not exist on this machine: E:\\gone", 400),
    );
    render(<MaintenanceTools onRestartRequested={() => {}} />);
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/settings"));
    fireEvent.change(screen.getByLabelText("Backup copy folder"), { target: { value: "E:\\gone" } });
    fireEvent.click(screen.getByRole("button", { name: /Save/ }));
    await screen.findByText("backup copy folder: folder does not exist on this machine: E:\\gone");
    expect(screen.queryByText(/Saved —/)).toBeNull();
  });

  it("a settings load that lands late never wipes what you typed", async () => {
    // Measured on the live check: the GET resolved after the folder was typed,
    // blanked the box, and Save then stored an empty folder.
    let release!: (v: unknown) => void;
    const slow = new Promise((r) => (release = r));
    api.get.mockImplementation(async (path: string) => {
      if (path === "/settings") {
        await slow;
        return { settings: { backup_mirror_dir: "", backup_mirror_media: true } };
      }
      if (path === "/maintenance/backups") return { backups: [], mirror: mirror({ configured: false, dir: "" }) };
      throw new Error(`unexpected GET ${path}`);
    });
    api.put.mockResolvedValue({});
    render(<MaintenanceTools onRestartRequested={() => {}} />);
    const box = screen.getByLabelText("Backup copy folder") as HTMLInputElement;
    fireEvent.change(box, { target: { value: DIR } });
    release({});
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/settings"));
    expect(box.value).toBe(DIR);
    fireEvent.click(screen.getByRole("button", { name: /Save/ }));
    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith("/settings", {
        values: { backup_mirror_dir: DIR, backup_mirror_media: true },
      }),
    );
  });

  it("re-reads the copy status when the page bumps refreshKey after Back up now", async () => {
    routeGet({ backup_mirror_dir: DIR, backup_mirror_media: true }, mirror());
    const { rerender } = render(<MaintenanceTools onRestartRequested={() => {}} refreshKey={0} />);
    await waitFor(() => expect(screen.getByTestId("backup-mirror-status").textContent).toMatch(/No copy yet/));
    routeGet(
      { backup_mirror_dir: DIR, backup_mirror_media: true },
      mirror({ last: { ok: true, at: "2026-09-11T20:00:00+00:00", media: { copied: 2 } } }),
    );
    rerender(<MaintenanceTools onRestartRequested={() => {}} refreshKey={1} />);
    await waitFor(() =>
      expect(screen.getByTestId("backup-mirror-status").textContent).toMatch(/2 new media files copied/),
    );
  });
});
