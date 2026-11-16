/**
 * v1.504.0 (north-star plan, group 4 "Nothing is lost") — the words on screen.
 *
 * 4.2 (D7): a Clear press MOVES runs to the app's trash and says where; its
 *   hover says the files and the record go there (the record kept 30 days).
 * 4.3: a database set aside at start is shown with ONE press that restores
 *   the backup from before it (`POST /maintenance/set-aside/restore`).
 * 4.9 (D8): an aged run says its details were removed after N days.
 *
 * Real components; only the network is mocked.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    FakeApiError,
    responses: {} as Record<string, unknown>,
    posts: [] as { path: string; body?: unknown }[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  post: (path: string, body?: unknown) => {
    H.posts.push({ path, body });
    if (path === "/maintenance/set-aside/restore") {
      return Promise.resolve({ restored_from: "ironjarvis-backup-20261110-000000.tar.gz", undo: "Your previous state was saved." });
    }
    return Promise.resolve({ answered: true });
  },
}));
vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (H.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

import { clearTitle, clearedWords } from "@/lib/clearWords";
import { detailsRemovedWords } from "@/components/sessions/runPageWords";
import { DatabaseSetAsideNote } from "@/components/overview/DatabaseSetAsideNote";

beforeEach(() => {
  H.responses = {};
  H.posts = [];
});
afterEach(cleanup);

describe("4.2: a Clear press says it moves to the trash", () => {
  it("the hover names the trash, the record's 30 days and the user's own folders", () => {
    const t = clearTitle("completed");
    expect(t).toMatch(/^Moves every completed run to the app's trash/);
    expect(t).toMatch(/kept there 30 days/);
    expect(t).toMatch(/Your own folders are never moved\./);
    expect(t).not.toMatch(/Remove every|Deletes every/);
  });
  it("the notice says where the trash is, from the daemon's own answer", () => {
    expect(clearedWords(2, "C:\\home\\trash\\20261115-000000", "failed")).toBe(
      "Moved 2 failed runs to the app's trash (C:\\home\\trash\\20261115-000000). " +
        "Their files stay there until you delete them; the records are kept for 30 days.",
    );
    expect(clearedWords(0, "", "completed")).toBe("There were no completed runs to move.");
  });
});

describe("4.3: a set-aside database is shown with one press to restore", () => {
  it("renders nothing when nothing was set aside", () => {
    H.responses["/maintenance/set-aside"] = { set_aside: null, latest_good: null };
    const { container } = render(<DatabaseSetAsideNote />);
    expect(container.innerHTML).toBe("");
  });

  it("says what happened and restores the backup from before it on one press", async () => {
    H.responses["/maintenance/set-aside"] = {
      set_aside: { file: "C:\\home\\ironjarvis.db.corrupt-20261115-000000", at: "2026-11-15T08:00:00+00:00", reason: "x" },
      latest_good: { name: "ironjarvis-backup-20261110-000000.tar.gz", bytes: 10, modified_at: "2026-11-10T00:00:00+00:00" },
    };
    render(<DatabaseSetAsideNote />);
    const note = screen.getByTestId("db-set-aside");
    expect(note.textContent).toMatch(/Your database was found damaged on .* and was set aside/);
    expect(note.textContent).toMatch(/ironjarvis\.db\.corrupt-20261115-000000/);
    expect(note.textContent).toMatch(/Backups made before then are kept until you choose\./);
    expect(note.textContent).not.toMatch(/quarantin/i);
    fireEvent.click(screen.getByTestId("db-set-aside-restore"));
    await waitFor(() => expect(screen.getByTestId("db-set-aside-done").textContent).toMatch(/^Restored the backup ironjarvis-backup-20261110/));
    expect(H.posts.map((p) => p.path)).toEqual(["/maintenance/set-aside/restore"]);
  });

  it("with no backup from before then, says so and still offers to keep the fresh start", async () => {
    H.responses["/maintenance/set-aside"] = {
      set_aside: { file: "f", at: "2026-11-15T08:00:00+00:00", reason: "x" },
      latest_good: null,
    };
    render(<DatabaseSetAsideNote />);
    expect(screen.queryByTestId("db-set-aside-restore")).toBeNull();
    expect(screen.getByText("There is no backup from before then to restore.")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("db-set-aside-keep"));
    await waitFor(() => expect(H.posts.map((p) => p.path)).toEqual(["/maintenance/set-aside/keep"]));
  });
});

describe("4.9: an aged run says its details were removed", () => {
  it("names the days and that the final answer is kept", () => {
    expect(detailsRemovedWords(90)).toBe(
      "This run's steps and details were removed after 90 days, as the \"Keep activity history\" setting says. Its final answer is kept.",
    );
    expect(detailsRemovedWords(null)).toBeNull();
    expect(detailsRemovedWords(undefined)).toBeNull();
  });
});

describe("4.3: the Home line says a database was set aside, first", () => {
  it("puts it above an interrupted task and a failing one", async () => {
    const { homeLineItems } = await import("@/components/chat/HomeLine");
    const items = homeLineItems({
      interrupted: 2, failingLoops: 1, failingPacks: 0, running: 1, waiting: 0, databaseSetAside: 1,
    });
    expect(items.map((i) => i.kind)).toEqual(["database", "interrupted", "failing", "working"]);
    expect(items[0].text).toBe(
      "Your database was damaged and set aside, so Iron Jarvis started empty. Open to restore a backup.",
    );
    expect(
      homeLineItems({ interrupted: 0, failingLoops: 0, failingPacks: 0, running: 0, waiting: 0, databaseSetAside: 0 }),
    ).toEqual([]);
  });
});
