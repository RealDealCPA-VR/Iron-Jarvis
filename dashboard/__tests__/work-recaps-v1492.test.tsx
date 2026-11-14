/**
 * v1.492.0 (vision plan leap 3, Phase 2): recaps of finished work and
 * "Forget this folder" on the Memory page's "Your work, by folder".
 *
 * A piece of work shows its recap (written by a model afterwards, checked
 * against the work's own record) and says when the recap is from before the
 * latest messages. An opened folder can be forgotten with TWO presses; the
 * daemon's own refusal is shown in words; forgotten folders are listed with
 * "Remember again".
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  folders: null as unknown,
  entries: {} as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
  postFail: null as null | { status: number; message: string },
  afterPost: null as null | (() => void),
}));

vi.mock("@/lib/api", () => ({
  get: async (path: string) => {
    if (path === "/work-record/folders") return H.folders;
    const folder = decodeURIComponent(path.split("folder=")[1].split("&")[0]);
    return H.entries[folder];
  },
  post: async (path: string, body: unknown) => {
    H.posts.push({ path, body });
    if (H.postFail) throw H.postFail;
    H.afterPost?.();
    return path.endsWith("/forget") ? { ok: true, removed: 2, folder: "x" } : { ok: true };
  },
}));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { WorkByFolder } from "@/components/memory/WorkByFolder";
import {
  FORGET_CONFIRM,
  FORGET_LABEL,
  FORGOTTEN_HEADING,
  RECAP_ABOUT,
  RECAP_LINES_SHOWN,
  RECAP_STALE,
  REMEMBER_LABEL,
  decodeEntries,
  decodeFolders,
  decodeRecap,
  forgotWords,
} from "@/lib/workRecord";
import { LOOP_HREF, LOOP_LABELS } from "@/lib/loopWords";

const HARTLEYS = "C:\\Clients\\Hartleys 2026";
const folders = (forgotten: unknown[] = []) => ({
  folders: [{ folder: HARTLEYS, name: "Hartleys 2026", count: 2, last_at: null, kinds: { chat: 2 } }],
  total: 2,
  building: false,
  forgotten,
});
const item = (id: string, title: string, recap: unknown) => ({
  id, kind: "chat", ref: id, title, tool: "", outcome: "", local_only: false, files: [], files_total: 0,
  note: "", updated_at: null, link: `/chat?thread=${id}`, recap,
});
const WORK = {
  folder: HARTLEYS,
  name: "Hartleys 2026",
  total: 3,
  entries: [
    item("c1", "March reconciliation", {
      lines: ["Done: Reconciled March and saved recon_march.xlsx", "Open: The 1,250.00 deposit is waiting on you"],
      written_at: new Date().toISOString(),
      current: true,
    }),
    item("c2", "April questions", { lines: ["Decided: Use cash basis"], written_at: null, current: false }),
    item("c3", "No recap yet", null),
  ],
};

beforeEach(() => {
  H.folders = folders();
  H.entries = { [HARTLEYS]: WORK };
  H.posts = [];
  H.postFail = null;
  H.afterPost = null;
});
afterEach(() => cleanup());

async function openFolder() {
  render(<WorkByFolder />);
  fireEvent.click(await screen.findByRole("button", { name: /Hartleys 2026/ }));
  await screen.findByText("March reconciliation");
}

describe("decoding", () => {
  it("keeps a recap's lines, when it was written and whether it is current", () => {
    const got = decodeRecap({ lines: ["Done: a", "", 7, "Open: b"], written_at: "2026-11-14T10:00:00Z", current: true });
    expect(got).toEqual({ lines: ["Done: a", "Open: b"], writtenAt: "2026-11-14T10:00:00Z", current: true });
  });

  it("reads anything that is not a recap as none", () => {
    for (const bad of [null, undefined, "x", {}, { lines: [] }, { lines: "Done: a" }, { lines: [" "] }]) {
      expect(decodeRecap(bad)).toBeNull();
    }
    expect(decodeEntries({ entries: [{ id: "c1" }] }).entries[0].recap).toBeNull();
  });

  it("only a real true is current, and at most eight lines are kept", () => {
    expect(decodeRecap({ lines: ["Done: a"], current: "yes" })?.current).toBe(false);
    const many = decodeRecap({ lines: Array.from({ length: 20 }, (_, i) => `Done: ${i}`) });
    expect(many?.lines).toHaveLength(RECAP_LINES_SHOWN);
  });

  it("reads the forgotten folders, and an older daemon's answer as none", () => {
    expect(decodeFolders(folders()).forgotten).toEqual([]);
    expect(decodeFolders({ folders: [] }).forgotten).toEqual([]);
    const got = decodeFolders(folders([{ folder: "C:\\Old", name: "Old", at: null }, { name: "no folder" }, 3]));
    expect(got.forgotten).toEqual([{ folder: "C:\\Old", name: "Old" }]);
  });

  it("says what a forget did", () => {
    expect(forgotWords("Hartleys 2026", 2)).toBe("Forgot Hartleys 2026: 2 pieces of work left the record.");
    expect(forgotWords("Hartleys 2026", 0)).toBe("Forgot Hartleys 2026. Nothing was on record there.");
  });
});

describe("recaps on the Memory page", () => {
  it("says what recaps are, with the place to turn them off", async () => {
    render(<WorkByFolder />);
    expect(await screen.findByTestId("work-recap-about")).toHaveTextContent(RECAP_ABOUT);
    expect(RECAP_ABOUT).toContain("Settings › Memory & you");
  });

  it("shows a recap under its piece of work, and none where there is none", async () => {
    await openFolder();
    const rows = screen.getAllByTestId("work-item");
    expect(within(rows[0]).getByTestId("work-recap")).toHaveTextContent("Done: Reconciled March and saved recon_march.xlsx");
    expect(within(rows[0]).getByTestId("work-recap")).toHaveTextContent("Open: The 1,250.00 deposit is waiting on you");
    expect(within(rows[2]).queryByTestId("work-recap")).toBeNull();
  });

  it("says when a recap is from before the latest messages, and only then", async () => {
    await openFolder();
    const rows = screen.getAllByTestId("work-item");
    expect(within(rows[0]).queryByTestId("work-recap-stale")).toBeNull();
    expect(within(rows[1]).getByTestId("work-recap-stale")).toHaveTextContent(RECAP_STALE);
  });
});

describe("forgetting a folder", () => {
  it("takes two presses, then says what left the record and reads the list again", async () => {
    await openFolder();
    const press = screen.getByTestId("work-forget");
    expect(press).toHaveTextContent(FORGET_LABEL);
    fireEvent.click(press);
    expect(H.posts).toEqual([]);
    expect(screen.getByTestId("work-forget")).toHaveTextContent(FORGET_CONFIRM);
    H.afterPost = () => {
      H.folders = { folders: [], total: 0, building: false, forgotten: [{ folder: HARTLEYS, name: "Hartleys 2026", at: null }] };
    };
    await act(async () => {
      fireEvent.click(screen.getByTestId("work-forget"));
    });
    expect(H.posts).toEqual([{ path: "/work-record/forget", body: { folder: HARTLEYS } }]);
    await waitFor(() =>
      expect(screen.getByTestId("work-forget-note")).toHaveTextContent(forgotWords("Hartleys 2026", 2)),
    );
    const list = await screen.findByTestId("work-forgotten");
    expect(list).toHaveTextContent(`${FORGOTTEN_HEADING} (1)`);
    expect(list).toHaveTextContent(HARTLEYS);
  });

  it("shows the daemon's own words when a folder cannot be forgotten", async () => {
    await openFolder();
    H.postFail = { status: 400, message: "That is the top of a drive. Pick a folder inside it to forget." };
    fireEvent.click(screen.getByTestId("work-forget"));
    await act(async () => {
      fireEvent.click(screen.getByTestId("work-forget"));
    });
    await waitFor(() =>
      expect(screen.getByTestId("work-forget-note")).toHaveTextContent("That is the top of a drive."),
    );
    // Still listed: nothing was forgotten.
    expect(screen.getByRole("button", { name: /Hartleys 2026/ })).toBeInTheDocument();
  });

  it("the note's place is on the page before any press, empty", async () => {
    render(<WorkByFolder />);
    await screen.findByRole("button", { name: /Hartleys 2026/ });
    const note = screen.getByTestId("work-forget-note");
    expect(note).toHaveAttribute("role", "status");
    expect(note).toBeEmptyDOMElement();
  });

  it("lists forgotten folders with Remember again, which posts and reads again", async () => {
    H.folders = folders([{ folder: "C:\\Clients\\Old", name: "Old", at: null }]);
    render(<WorkByFolder />);
    const list = await screen.findByTestId("work-forgotten");
    expect(list).toHaveTextContent("C:\\Clients\\Old");
    H.afterPost = () => {
      H.folders = folders();
    };
    await act(async () => {
      fireEvent.click(within(list).getByRole("button", { name: REMEMBER_LABEL }));
    });
    expect(H.posts).toEqual([{ path: "/work-record/remember", body: { folder: "C:\\Clients\\Old" } }]);
    await waitFor(() => expect(screen.queryByTestId("work-forgotten")).toBeNull());
    expect(screen.getByTestId("work-forget-note")).toHaveTextContent("Remembering Old again.");
  });

  it("shows no forgotten list when there is none", async () => {
    render(<WorkByFolder />);
    await screen.findByRole("button", { name: /Hartleys 2026/ });
    expect(screen.queryByTestId("work-forgotten")).toBeNull();
  });
});

describe("the background loop has words", () => {
  it("names the recap loop for the bell and the Status page", () => {
    expect(LOOP_LABELS.work_recaps).toBe("Work recaps");
    expect(LOOP_HREF.work_recaps).toBe("/memory");
  });
});
