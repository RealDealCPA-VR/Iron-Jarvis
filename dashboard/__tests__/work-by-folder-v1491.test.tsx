/**
 * v1.491.0 (vision plan leap 3, Phase 1): "Your work, by folder" on the
 * Memory page.
 *
 * Iron Jarvis keeps its own record of what it did in each folder. The section
 * lists every folder with work on record (one read), reads a folder's own
 * work when it is opened (once), links each piece of work back to where it
 * opens, says how a piece ended only when it did not simply finish, and never
 * shows an empty list for a read that failed.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  folders: null as unknown,
  entries: {} as Record<string, unknown>,
  fail: null as null | { status: number; message: string },
  failEntries: false,
  paths: [] as string[],
}));

vi.mock("@/lib/api", () => ({
  get: async (path: string) => {
    H.paths.push(path);
    if (path === "/work-record/folders") {
      if (H.fail) throw H.fail;
      return H.folders;
    }
    if (H.failEntries) throw { status: 500, message: "boom" };
    const folder = decodeURIComponent(path.split("folder=")[1].split("&")[0]);
    return H.entries[folder];
  },
}));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { WorkByFolder, WORK_BUILD_REREADS } from "@/components/memory/WorkByFolder";
import {
  WORK_ABOUT,
  WORK_BUILDING,
  WORK_EMPTY,
  countWords,
  decodeEntries,
  decodeFolders,
  filesWords,
  insideLink,
  kindWords,
} from "@/lib/workRecord";

const HARTLEYS = "C:\\Clients\\Hartleys 2026";
const FOLDERS = {
  folders: [
    { folder: HARTLEYS, name: "Hartleys 2026", count: 2, last_at: new Date().toISOString(), kinds: { chat: 1, run: 1 } },
    { folder: "C:\\Code\\shop", name: "shop", count: 1, last_at: null, kinds: { code: 1 } },
  ],
  total: 3,
  building: false,
};
const HARTLEYS_WORK = {
  folder: HARTLEYS,
  name: "Hartleys 2026",
  total: 2,
  entries: [
    {
      id: "chat:c1", kind: "chat", ref: "c1", title: "March reconciliation", tool: "", outcome: "",
      local_only: true, files: [{ name: "recon-march.xlsx", path: HARTLEYS + "\\recon-march.xlsx", how: "wrote" }],
      files_total: 1, note: "", updated_at: new Date().toISOString(), link: "/chat?thread=c1",
    },
    {
      id: "run:s1", kind: "run", ref: "s1", title: "Import the statements", tool: "", outcome: "failed",
      local_only: false, files: [], files_total: 0, note: "The run failed: the bank file was empty.",
      updated_at: null, link: "/sessions/s1",
    },
  ],
};

beforeEach(() => {
  H.folders = FOLDERS;
  H.entries = { [HARTLEYS]: HARTLEYS_WORK };
  H.fail = null;
  H.failEntries = false;
  H.paths.length = 0;
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("the words and the decoders", () => {
  it("names each kind of work in the app's own words", () => {
    expect(kindWords("chat", "")).toBe("Chat");
    expect(kindWords("code", "")).toBe("Coding session");
    expect(kindWords("tool_code", "Codex")).toBe("Coding session (Codex)");
    expect(kindWords("run", "")).toBe("Agent run");
    expect(countWords(1)).toBe("1 piece of work");
    expect(countWords(4)).toBe("4 pieces of work");
  });

  it("keeps only an address inside the app as a link", () => {
    expect(insideLink("/chat?thread=c1")).toBe("/chat?thread=c1");
    expect(insideLink("//evil.example/x")).toBe("");
    expect(insideLink("https://evil.example/x")).toBe("");
    expect(insideLink("javascript:alert(1)")).toBe("");
    expect(insideLink(undefined)).toBe("");
  });

  it("drops rows that are not rows and never invents a count", () => {
    expect(decodeFolders(null)).toEqual({ folders: [], foldersTotal: 0, total: 0, building: false });
    expect(decodeFolders({ folders: [null, { name: "x" }, { folder: "C:\\a", count: "7" }] }).folders).toEqual([
      { folder: "C:\\a", name: "C:\\a", count: 0, lastAt: null },
    ]);
    const got = decodeEntries({
      entries: [{ id: "" }, { id: "chat:1", link: "https://x", files: [{ name: "" }, { name: "a.txt" }] }],
    });
    expect(got.entries).toHaveLength(1);
    expect(got.entries[0].link).toBe("");
    expect(got.entries[0].files).toEqual([{ name: "a.txt", path: "" }]);
  });

  it("lists a few file names and counts the rest", () => {
    const item = decodeEntries({
      entries: [{ id: "x", files: ["a", "b", "c", "d", "e", "f"].map((n) => ({ name: n })), files_total: 9 }],
    }).entries[0];
    expect(filesWords(item)).toBe("Files: a, b, c, d, e, and 4 more");
  });
});

describe("the section", () => {
  it("lists every folder from one read and reads a folder's work only when it is opened", async () => {
    render(<WorkByFolder />);
    const rows = await screen.findAllByTestId("work-folder");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Hartleys 2026")).toBeTruthy();
    expect(rows[0].textContent).toContain("2 pieces of work");
    expect(rows[1].textContent).toContain("1 piece of work");
    expect(H.paths).toEqual(["/work-record/folders"]);

    fireEvent.click(within(rows[0]).getByRole("button"));
    const items = await screen.findAllByTestId("work-item");
    expect(items).toHaveLength(2);
    expect(H.paths.filter((p) => p.startsWith("/work-record/entries"))).toHaveLength(1);
    expect(H.paths[1]).toContain(encodeURIComponent(HARTLEYS));

    // Each piece links back to where it opens, and says its files.
    const first = within(items[0]).getByRole("link", { name: "March reconciliation" });
    expect(first.getAttribute("href")).toBe("/chat?thread=c1");
    expect(items[0].textContent).toContain("Chat");
    expect(items[0].textContent).toContain("Local only");
    expect(items[0].textContent).toContain("Files: recon-march.xlsx");
    // A piece that did not simply finish says so, with the run's own line.
    expect(items[1].textContent).toContain("Agent run");
    expect(items[1].textContent).toContain("Failed");
    expect(items[1].textContent).toContain("the bank file was empty");
    // One that finished says nothing about how it ended.
    expect(items[0].textContent).not.toContain("Done");

    // Opening it again reads again (the record grows), and what was shown
    // stays on screen meanwhile.
    fireEvent.click(within(rows[0]).getByRole("button"));
    fireEvent.click(within(rows[0]).getByRole("button"));
    expect(screen.getAllByTestId("work-item")).toHaveLength(2);
    await waitFor(() => expect(H.paths.filter((p) => p.startsWith("/work-record/entries"))).toHaveLength(2));
  });

  it("a run's own line is shown as plain words, and archived work says so", async () => {
    H.entries[HARTLEYS] = {
      ...HARTLEYS_WORK,
      entries: [
        { ...HARTLEYS_WORK.entries[1], note: "**The run failed**: see `bank.csv`", archived: true },
      ],
      total: 1,
    };
    render(<WorkByFolder />);
    const rows = await screen.findAllByTestId("work-folder");
    fireEvent.click(within(rows[0]).getByRole("button"));
    const item = await screen.findByTestId("work-item");
    expect(item.textContent).toContain("The run failed: see bank.csv");
    expect(item.textContent).not.toContain("**");
    expect(item.textContent).toContain("archived");
  });

  it("says so when only the most recently used folders are shown", async () => {
    H.folders = { ...FOLDERS, folders_total: 700 };
    render(<WorkByFolder />);
    expect((await screen.findByTestId("work-by-folder-more")).textContent).toBe(
      "Showing the 2 most recently used of 700 folders.",
    );
  });

  it("says who sees the record, and never that nothing leaves this PC", async () => {
    render(<WorkByFolder />);
    await screen.findAllByTestId("work-folder");
    expect(WORK_ABOUT).toContain("the model answering that chat sees those titles and file names");
    expect(WORK_ABOUT).not.toMatch(/nothing here leaves/i);
    expect(screen.getByText(WORK_ABOUT)).toBeTruthy();
  });

  it("says how to get started when nothing is on record", async () => {
    H.folders = { folders: [], total: 0, building: false };
    render(<WorkByFolder />);
    expect((await screen.findByTestId("work-by-folder-empty")).textContent).toBe(WORK_EMPTY);
  });

  it("a read that failed is said, never shown as an empty list, and Try again reads again", async () => {
    H.fail = { status: 500, message: "boom" };
    render(<WorkByFolder />);
    const line = await screen.findByTestId("work-by-folder-failed");
    expect(line.textContent).toContain("Your work record couldn't load");
    expect(screen.queryByTestId("work-by-folder-empty")).toBeNull();
    H.fail = null;
    fireEvent.click(within(line).getByRole("button"));
    await waitFor(() => expect(screen.getAllByTestId("work-folder")).toHaveLength(2));
  });

  it("draws nothing at all on an older Iron Jarvis", async () => {
    H.fail = { status: 404, message: "Not Found" };
    render(<WorkByFolder />);
    await waitFor(() => expect(H.paths).toHaveLength(1));
    await waitFor(() => expect(screen.queryByTestId("work-by-folder")).toBeNull());
  });

  it("a folder whose work cannot be read says so and can be read again", async () => {
    render(<WorkByFolder />);
    const rows = await screen.findAllByTestId("work-folder");
    H.failEntries = true;
    fireEvent.click(within(rows[0]).getByRole("button"));
    const line = await within(rows[0]).findByText(/This folder's work couldn't load/);
    H.failEntries = false;
    fireEvent.click(within(line.closest("p") as HTMLElement).getByRole("button"));
    await waitFor(() => expect(screen.getAllByTestId("work-item")).toHaveLength(2));
  });

  it("reads again while a long first build is going, and only a bounded number of times", async () => {
    vi.useFakeTimers();
    H.folders = { ...FOLDERS, building: true };
    render(<WorkByFolder />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId("work-by-folder-building").textContent).toBe(WORK_BUILDING);
    for (let i = 0; i < WORK_BUILD_REREADS + 5; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000);
      });
    }
    expect(H.paths.filter((p) => p === "/work-record/folders")).toHaveLength(1 + WORK_BUILD_REREADS);
  });

  it("stops reading again once the build is done", async () => {
    vi.useFakeTimers();
    H.folders = { ...FOLDERS, building: true };
    render(<WorkByFolder />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    H.folders = FOLDERS;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    const after = H.paths.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(H.paths.length).toBe(after);
    expect(screen.queryByTestId("work-by-folder-building")).toBeNull();
  });
});
