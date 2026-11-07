/**
 * v1.446.0 (ease-of-use review, item 7): a restore can be undone.
 *
 * Pinned:
 *  - The section and the confirm say the current state is saved as a backup
 *    first, so the restore can be undone.
 *  - After a restore the note names the backup the daemon wrote of the
 *    replaced state (`saved_as`) and says restoring it undoes this; an older
 *    daemon that sends no `saved_as` gets the old note, with no false promise.
 *  - A backup written before a restore is labelled as such in the list.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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

import { MaintenanceTools, restoredNote } from "@/components/settings/MaintenanceTools";

const SAFETY = "ironjarvis-backup-20261107-120000-before-restore.tar.gz";
const BACKUPS = [
  { name: SAFETY, bytes: 2_500_000, modified_at: "2026-11-07T12:00:00+00:00" },
  { name: "ironjarvis-backup-20261106-020000.tar.gz", bytes: 2_400_000, modified_at: "2026-11-06T02:00:00+00:00" },
];

beforeEach(() => {
  api.get.mockReset();
  api.post.mockReset();
  api.get.mockImplementation(async (path: string) => {
    if (path === "/maintenance/backups") return { dir: "C:/home/backups", backups: BACKUPS };
    if (path === "/maintenance/storage") return { categories: [], total_bytes: 0, candidates: { files: 0, bytes: 0 } };
    return {};
  });
});
afterEach(() => cleanup());

describe("a restore can be undone", () => {
  it("says the current state is saved first, in the section and the confirm", async () => {
    render(<MaintenanceTools onRestartRequested={() => {}} />);
    const section = await screen.findByTestId("restore-backup");
    expect(section.textContent).toMatch(/Your current state is saved as a backup first, so you can undo it/);
    await screen.findByLabelText("Backup to restore");
    fireEvent.click(screen.getByRole("button", { name: /Restore from backup…/ }));
    const dialog = await screen.findByRole("dialog", { name: "Confirm restore" });
    expect(dialog.textContent).toMatch(/saved as\s+a new backup first, so you can restore it to undo this/);
  });

  it("names the backup of the replaced state in the note after the restore", async () => {
    api.post.mockResolvedValue({
      ok: true,
      restored_from: BACKUPS[1].name,
      files: 9,
      restart: "scheduled",
      saved_as: "ironjarvis-backup-20261107-130000-before-restore.tar.gz",
    });
    const notes: string[] = [];
    render(<MaintenanceTools onRestartRequested={(n) => void notes.push(n)} />);
    const select = (await screen.findByLabelText("Backup to restore")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: BACKUPS[1].name } });
    fireEvent.click(screen.getByRole("button", { name: /Restore from backup…/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Restore and restart" }));
    await waitFor(() => expect(notes).toHaveLength(1));
    expect(notes[0]).toContain(
      "Your previous state was saved as ironjarvis-backup-20261107-130000-before-restore.tar.gz. Restore it to undo this.",
    );
  });

  it("promises no undo when an older daemon names no saved backup", () => {
    const note = restoredNote({ restored_from: "ironjarvis-backup-20261106-020000.tar.gz", files: 3 });
    expect(note).not.toMatch(/undo/);
    expect(note).toMatch(/Restored 3 file\(s\) from ironjarvis-backup-20261106-020000\.tar\.gz\./);
  });

  it("labels a backup written before a restore in the list", async () => {
    render(<MaintenanceTools onRestartRequested={() => {}} />);
    const select = (await screen.findByLabelText("Backup to restore")) as HTMLSelectElement;
    expect(select.options[0].textContent).toMatch(/saved before a restore/);
    expect(select.options[1].textContent).not.toMatch(/saved before a restore/);
  });
});
