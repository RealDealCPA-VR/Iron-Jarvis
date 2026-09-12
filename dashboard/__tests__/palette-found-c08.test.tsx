/**
 * C-08 — "In your files & memory", the palette's second asynchronous lane.
 *
 * The lane above it finds what was SAID. This one finds what the app MADE and
 * what it REMEMBERS, and before it existed a file the chat wrote could not be
 * found from the search box at all.
 *
 * What these pin, and each is invisible on a happy path:
 *  - the lane appears BELOW "In your conversations" and above the ask row;
 *  - a file row opens the same preview the Files rail uses — it has no page to
 *    navigate to, so Enter must not push a route;
 *  - a memory row navigates to where that note actually lives;
 *  - the debounce (one request per pause, not one per keystroke);
 *  - the degrade: a daemon without /search/all 404s, and the lane then stops
 *    asking for the rest of the session instead of costing a request a key.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const routerMock = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }));

const api = vi.hoisted(() => ({
  calls: [] as string[],
  found: {
    files: [
      {
        title: "Smith_2024 summary.xlsx",
        path: "C:\\Users\\VR\\Documents\\Iron Jarvis\\Smith\\Smith_2024 summary.xlsx",
        folder: "C:\\Users\\VR\\Documents\\Iron Jarvis\\Smith",
        at: new Date().toISOString(),
      },
    ] as unknown[],
    memory: [
      { kind: "note", title: "S-corp election", snippet: "filed 2024-03-01", href: "/memory" },
    ] as unknown[],
    partial: [] as string[],
  },
  foundStatus: null as number | null,
  history: [] as unknown[],
}));

vi.mock("@/lib/api", () => ({
  get: (path: string) => {
    api.calls.push(path);
    if (path.startsWith("/search/all")) {
      if (api.foundStatus !== null) {
        return Promise.reject(Object.assign(new Error("nope"), { status: api.foundStatus }));
      }
      return Promise.resolve(api.found);
    }
    if (path.startsWith("/search/history")) {
      return Promise.resolve({ hits: api.history, mode: "fts5" });
    }
    return Promise.resolve({});
  },
  post: () => Promise.resolve({}),
  // DocPreview (the panel a file row opens) reads these from the same
  // module, so a partial mock would throw inside the preview.
  ijToken: () => "",
  API_BASE: "",
  ApiError: class ApiError extends Error {},
}));

import { CommandPalette } from "@/components/CommandPalette";

function openAndType(text: string) {
  fireEvent.keyDown(window, { key: "k", ctrlKey: true });
  const box = screen.getByRole("combobox");
  fireEvent.change(box, { target: { value: text } });
  return box;
}

beforeEach(() => {
  api.calls = [];
  api.foundStatus = null;
  api.history = [];
  routerMock.push.mockReset();
});
afterEach(cleanup);

describe("the files & memory lane", () => {
  it("shows the file the app made, with the folder it is in", async () => {
    render(<CommandPalette />);
    openAndType("smith summary");

    await screen.findByText("In your files & memory");
    expect(screen.getByText("Smith_2024 summary.xlsx")).toBeTruthy();
    expect(screen.getByText(/Documents\\Iron Jarvis\\Smith$/)).toBeTruthy();
    expect(screen.getAllByText("File").length).toBe(1);
  });

  it("asks once per pause, not once per keystroke", async () => {
    render(<CommandPalette />);
    const box = openAndType("smi");
    fireEvent.change(box, { target: { value: "smith" } });
    fireEvent.change(box, { target: { value: "smith s" } });
    fireEvent.change(box, { target: { value: "smith sum" } });

    await screen.findByText("In your files & memory");
    expect(api.calls.filter((c) => c.startsWith("/search/all")).length).toBe(1);
    expect(api.calls.at(-1)).toContain("q=smith%20sum");
  });

  it("a file opens the preview instead of navigating", async () => {
    render(<CommandPalette />);
    openAndType("smith summary");
    const row = await screen.findByText("Smith_2024 summary.xlsx");

    fireEvent.click(row);

    await screen.findByTestId("palette-preview");
    expect(routerMock.push).not.toHaveBeenCalled();
  });

  it("a remembered note opens where it lives", async () => {
    render(<CommandPalette />);
    openAndType("s-corp election");
    const row = await screen.findByText("S-corp election");

    fireEvent.click(row);

    await waitFor(() => expect(routerMock.push).toHaveBeenCalledWith("/memory"));
    expect(screen.queryByTestId("palette-preview")).toBeNull();
  });

  it("sits below the conversations lane", async () => {
    api.history = [
      { kind: "chat", ref: "chat_1", title: "Smith call", snippet: "we agreed", at: null },
    ];
    render(<CommandPalette />);
    openAndType("smith");

    await screen.findByText("In your files & memory");
    const headers = screen
      .getAllByText(/^In your /)
      .map((el) => el.textContent);
    expect(headers).toEqual(["In your conversations", "In your files & memory"]);
  });

  it("an older daemon costs one request, then the lane is gone", async () => {
    api.foundStatus = 404;
    render(<CommandPalette />);
    const box = openAndType("smith summary");

    await waitFor(() =>
      expect(api.calls.filter((c) => c.startsWith("/search/all")).length).toBe(1),
    );
    fireEvent.change(box, { target: { value: "another search" } });
    await waitFor(() =>
      expect(api.calls.filter((c) => c.startsWith("/search/history")).length).toBeGreaterThan(1),
    );
    // Settle PAST this lane's own debounce (220 ms), which is longer than the
    // conversation lane's: without it a latch that never latched still looks
    // quiet, because its second request had not been made yet.
    await new Promise((r) => setTimeout(r, 600));
    expect(api.calls.filter((c) => c.startsWith("/search/all")).length).toBe(1);
    expect(screen.queryByText("In your files & memory")).toBeNull();
  });
});
