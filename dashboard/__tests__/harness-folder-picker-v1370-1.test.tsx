/**
 * v1.370.1 — the New session folder picker opens folders; it never starts a
 * session by itself.
 *
 * The user's report: "I'm not able to select a proper subfolder that has a
 * project in it, it stops at a top level folder … it stops right here at VR
 * and does not allow me to drill down." Reproduced on a scratch daemon:
 * clicking `C:\Users` showed "This folder cannot be used: … not writable",
 * and clicking `VR` CREATED a session in `C:\Users\VR`. The cause: the
 * folder tree's row is a <button> with no `type`, and the New session panel
 * draws the tree inside its <form>, so every row click also SUBMITTED the
 * form with the folder just clicked. The tree's own tests passed (no form
 * around them) and the sidebar's tests mocked the tree away.
 *
 * Driven here (v1.412.0) through the REAL folder chip's dialog with the
 * REAL DirectoryTree: opening `Users` then `VR` chooses nothing and shows
 * VR's folders; only "Use this folder" chooses.
 */

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const LISTS: Record<string, { name: string; path: string; is_project: string | null }[]> = {
  "C:\\": [
    { name: "Users", path: "C:\\Users", is_project: null },
    { name: "Windows", path: "C:\\Windows", is_project: null },
  ],
  "C:\\Users": [
    { name: "Public", path: "C:\\Users\\Public", is_project: null },
    { name: "VR", path: "C:\\Users\\VR", is_project: null },
  ],
  "C:\\Users\\VR": [
    { name: "Projects", path: "C:\\Users\\VR\\Projects", is_project: null },
  ],
  "C:\\Users\\VR\\Projects": [
    { name: "Iron-Jarvis", path: "C:\\Users\\VR\\Projects\\Iron-Jarvis", is_project: "git" },
  ],
};

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status = 0;
  }
  return {
    ApiError,
    get: (p: string) => {
      if (p.startsWith("/fs/list")) {
        const path = decodeURIComponent(new URL(p, "http://x").searchParams.get("path") ?? "");
        return Promise.resolve({ path, entries: LISTS[path] ?? [] });
      }
      return Promise.reject(new ApiError(`unmocked ${p}`));
    },
  };
});
vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path === "/fs/drives" ? { drives: [{ path: "C:\\", label: "C:" }] } : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

import { FolderChip } from "@/components/folders/FolderChip";

afterEach(() => cleanup());

// v1.412.0 (MOVED ON PURPOSE): there is no New session form any more — the
// folder is chosen with the folder chip's "Choose…", which (in a browser, no
// desktop picker) opens the REAL folder tree in a dialog. The rule this file
// pins still holds there: opening folders picks nothing by itself; only
// "Use this folder" chooses, and no button the tree draws submits anything.
function renderChip() {
  const onPick = vi.fn();
  render(<FolderChip folder={null} onPick={onPick} testId="chip" />);
  fireEvent.click(screen.getByTestId("chip-choose"));
  return { onPick, dialog: screen.getByTestId("chip-tree") };
}

async function openRow(box: HTMLElement, path: string) {
  const row = await waitFor(() => {
    const el = within(box).getByTitle(path);
    expect(el.tagName).toBe("BUTTON");
    return el;
  });
  fireEvent.click(row);
  return row;
}

describe("the folder chip's folder tree", () => {
  it("drills down Users > VR > Projects without choosing anything, and Use this folder chooses the folder picked", async () => {
    const { onPick, dialog } = renderChip();
    await openRow(dialog, "C:\\Users");
    await openRow(dialog, "C:\\Users\\VR");
    await openRow(dialog, "C:\\Users\\VR\\Projects");
    await waitFor(() => expect(within(dialog).getByTitle("C:\\Users\\VR\\Projects\\Iron-Jarvis")).toBeTruthy());
    expect(onPick).not.toHaveBeenCalled();
    await openRow(dialog, "C:\\Users\\VR\\Projects\\Iron-Jarvis");
    expect(onPick).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("chip-tree-use"));
    await waitFor(() => expect(onPick).toHaveBeenCalledWith("C:\\Users\\VR\\Projects\\Iron-Jarvis"));
    expect(onPick).toHaveBeenCalledTimes(1);
  });

  it("every button in the dialog is a plain button, never a submit, and there is no form", async () => {
    const { dialog } = renderChip();
    await openRow(dialog, "C:\\Users");
    await waitFor(() => expect(within(dialog).getByTitle("C:\\Users\\VR")).toBeTruthy());
    expect(dialog.querySelector("form")).toBeNull();
    const submits = Array.from(dialog.querySelectorAll("button")).filter(
      (b) => (b.getAttribute("type") ?? "submit") === "submit",
    );
    expect(submits.map((b) => b.getAttribute("title") || b.textContent)).toEqual([]);
  });
});
