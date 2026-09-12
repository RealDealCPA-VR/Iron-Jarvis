/**
 * v1.256.0 (R-01) — Settings → Maintenance tells you what the app is keeping,
 * and clears the part that is safe to clear.
 *
 * WHY IT EXISTS. Measured on the live install: 814 MB of state, of which
 * `artifacts/` was 783 MB — 186 files, 57 of them videos totalling 676 MB, none
 * newer than August. Nothing pruned it and no screen reported it, so the only
 * way to find out was a file manager.
 *
 * WHAT THESE PIN, in order of what would hurt most if it broke:
 *  - TWO PRESSES. "Clear" MOVES files into the app's own trash and says they are
 *    still recoverable; "Delete permanently" is a SEPARATE press. One click must
 *    never destroy 700 MB, and the copy must not claim space is freed while the
 *    files are still on disk.
 *  - THE CONFIRM SHOWS WHAT THE DAEMON COUNTED. The card is built from the dry
 *    run, so the number the user agrees to is the number that moves.
 *  - BACKUPS AND UNDO ARE NOT OFFERED. Only the clearable categories can go.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    getResponses: {} as Record<string, unknown>,
    postResponses: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  get: async (path: string) => {
    H.gets.push(path);
    const r = H.getResponses[path];
    if (r instanceof Error) throw r;
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body: unknown) => {
    H.posts.push({ path, body });
    const r = H.postResponses[path];
    if (r instanceof Error) throw r;
    if (typeof r === "function") return (r as (b: unknown) => unknown)(body);
    return r ?? {};
  },
  put: async () => ({}),
}));

import { MaintenanceTools } from "@/components/settings/MaintenanceTools";

const STORAGE = "/maintenance/storage";
const REPAIR = "/diagnostics/repair";

/** The live install's shape, scaled down: media dominates, backups do not. */
function report(over: Record<string, unknown> = {}) {
  return {
    home: "C:\\Users\\VR\\AppData\\Roaming\\Iron Jarvis\\.ironjarvis",
    total_bytes: 820 * 1024 * 1024,
    older_than_days: 30,
    categories: [
      { label: "Generated media", dir: "artifacts", path: "…/artifacts", files: 186, bytes: 783 * 1024 * 1024, newest: "2026-08-26T10:00:00Z", clearable: true },
      { label: "Backups", dir: "backups", path: "…/backups", files: 7, bytes: 15 * 1024 * 1024, newest: "2026-09-11T10:00:00Z", clearable: false },
      { label: "Undo history", dir: "undo", path: "…/undo", files: 42, bytes: 2 * 1024 * 1024, newest: null, clearable: false },
      { label: "Cleared, awaiting deletion", dir: "trash", path: "…/trash", files: 0, bytes: 0, newest: null, clearable: false },
    ],
    candidates: {
      files: 57,
      bytes: 676 * 1024 * 1024,
      largest: [
        { rel: "artifacts/renders/demo.mp4", bytes: 71 * 1024 * 1024 },
        { rel: "artifacts/renders/promo.mp4", bytes: 62 * 1024 * 1024 },
      ],
    },
    ...over,
  };
}

beforeEach(() => {
  H.gets.length = 0;
  H.posts.length = 0;
  for (const k of Object.keys(H.getResponses)) delete H.getResponses[k];
  for (const k of Object.keys(H.postResponses)) delete H.postResponses[k];
  H.getResponses[STORAGE] = report();
  // The panel's other cards load too; keep them quiet rather than unmocked.
  H.getResponses["/settings"] = { settings: {} };
  H.getResponses["/maintenance/backups"] = { dir: "", backups: [], mirror: { configured: false } };
});
afterEach(cleanup);

function panel() {
  return render(<MaintenanceTools onRestartRequested={vi.fn()} />);
}

describe("what Iron Jarvis is keeping", () => {
  it("lists the categories largest first, with the total and where they live", async () => {
    panel();
    await screen.findByTestId("storage-report");

    const rows = await screen.findAllByTestId(/^storage-row-/);
    // Largest first: media (783 MB) before backups (15 MB) before undo (2 MB).
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual([
      "storage-row-artifacts",
      "storage-row-backups",
      "storage-row-undo",
    ]);
    expect(rows[0].textContent).toContain("Generated media");
    expect(rows[0].textContent).toContain("783.0 MB");
    expect(rows[0].textContent).toContain("186 files");
    // An empty category is not listed as a row of zeros.
    expect(screen.queryByTestId("storage-row-trash")).toBeNull();
    expect((await screen.findByTestId("storage-total")).textContent).toContain("820.0 MB");
  });

  it("offers the clear with the daemon's own count, and asks before moving anything", async () => {
    panel();
    const clear = await screen.findByTestId("storage-clear");
    // The button states what the daemon counted — not an estimate of its own.
    expect(clear.textContent).toContain("57 old files");
    expect(clear.textContent).toContain("676.0 MB");

    fireEvent.click(clear);

    const confirm = await screen.findByTestId("storage-clear-confirm");
    expect(confirm.textContent).toContain("57 files");
    expect(confirm.textContent).toContain("676.0 MB");
    expect(confirm.textContent).toContain("older than 30 days");
    // The promise that makes one press safe.
    expect(confirm.textContent).toMatch(/still get them back/i);
    expect(confirm.textContent).toContain("artifacts/renders/demo.mp4");
    // Nothing has been asked of the daemon yet.
    expect(H.posts).toEqual([]);
  });

  it("clearing MOVES and says so; the space is not claimed freed", async () => {
    H.postResponses[REPAIR] = { action: "clear_media", ok: true, moved: 57, bytes: 676 * 1024 * 1024, failed: 0, trash: "…/trash/20260912-120000", names: [] };
    // What the daemon reports afterwards: media emptied, trash now holding it.
    let second = false;
    Object.defineProperty(H.getResponses, STORAGE, {
      configurable: true,
      get() {
        if (!second) return report();
        return report({
          total_bytes: 820 * 1024 * 1024,
          categories: [
            { label: "Generated media", dir: "artifacts", path: "…", files: 129, bytes: 107 * 1024 * 1024, newest: null, clearable: true },
            { label: "Cleared, awaiting deletion", dir: "trash", path: "…", files: 57, bytes: 676 * 1024 * 1024, newest: null, clearable: false },
          ],
          candidates: { files: 0, bytes: 0, largest: [] },
        });
      },
    });

    panel();
    fireEvent.click(await screen.findByTestId("storage-clear"));
    second = true;
    fireEvent.click(await screen.findByTestId("storage-clear-go"));

    await waitFor(() => expect(H.posts.length).toBe(1));
    expect(H.posts[0]).toEqual({ path: REPAIR, body: { action: "clear_media", older_than_days: 30 } });

    const note = await screen.findByTestId("storage-note");
    expect(note.textContent).toContain("57 files");
    expect(note.textContent).toMatch(/recoverable/i);

    // The trash is reported, and its deletion is a DIFFERENT press.
    const trash = await screen.findByTestId("storage-trash");
    expect(trash.textContent).toContain("57 cleared files");
    expect(trash.textContent).toMatch(/only freed once/i);
    expect(await screen.findByTestId("storage-purge")).toBeTruthy();
  });

  it("deleting permanently is its own confirmed press", async () => {
    H.getResponses[STORAGE] = report({
      categories: [
        { label: "Cleared, awaiting deletion", dir: "trash", path: "…", files: 57, bytes: 676 * 1024 * 1024, newest: null, clearable: false },
      ],
      candidates: { files: 0, bytes: 0, largest: [] },
    });
    H.postResponses[REPAIR] = { action: "purge_trash", ok: true, deleted: 57, bytes: 676 * 1024 * 1024 };

    panel();
    fireEvent.click(await screen.findByTestId("storage-purge"));
    // Still nothing sent — the second press needs its own confirmation.
    expect(H.posts).toEqual([]);

    fireEvent.click(await screen.findByTestId("storage-purge-go"));
    await waitFor(() => expect(H.posts.length).toBe(1));
    expect(H.posts[0].body).toEqual({ action: "purge_trash" });
    expect((await screen.findByTestId("storage-note")).textContent).toMatch(/for good/i);
  });

  it("says plainly when there is nothing old enough, instead of offering a dead button", async () => {
    H.getResponses[STORAGE] = report({ candidates: { files: 0, bytes: 0, largest: [] } });
    panel();
    expect((await screen.findByTestId("storage-nothing-to-clear")).textContent).toContain("30 days");
    expect(screen.queryByTestId("storage-clear")).toBeNull();
  });

  it("a daemon that cannot answer shows the reason, not an empty card", async () => {
    H.getResponses[STORAGE] = new H.FakeApiError("disk is not readable", 500);
    panel();
    await waitFor(() => expect(screen.getByText(/disk is not readable/)).toBeTruthy());
  });
});
