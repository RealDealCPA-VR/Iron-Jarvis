/**
 * v1.500.1 (the owner, on Chat › Code's list in the app sidebar: "there is no
 * way to archive, or delete a session") — each coding-session row has a ⋯
 * menu with Archive (Stop and archive while it works) and Delete (two
 * presses); an archived row has Delete too. The view wires them to the
 * daemon's own routes and drops the session from the columns.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown>,
    posts: [] as [string, unknown][],
    dels: [] as string[],
  };
});
vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) =>
    p in api.gets ? Promise.resolve(api.gets[p]) : Promise.reject(new api.ApiError(`unmocked ${p}`, 404)),
  post: (p: string, b?: unknown) => {
    api.posts.push([p, b]);
    return Promise.resolve({});
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: (p: string) => {
    api.dels.push(p);
    return Promise.resolve({});
  },
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => null }));
vi.mock("@/components/harness/HarnessSession", () => ({
  HarnessSession: ({ threadId }: { threadId: string }) => <div data-testid="session-stub" data-id={threadId} />,
}));

import { HarnessSidebar } from "@/components/harness/HarnessSidebar";
import { IjHarnessView } from "@/components/harness/IjHarnessView";
import { decodeFolders } from "@/lib/buildSessions";

const LIST = {
  folders: [
    {
      path: "C:\\work\\shop",
      name: "Shop site",
      is_project: false,
      project_id: null,
      sessions: [
        { id: "a1", title: "Add a cart", updated_at: "2026-10-22T10:00:00", running: true, waiting: false, archived: false },
        { id: "a2", title: "Fix the footer", updated_at: "2026-10-21T10:00:00", running: false, waiting: false, archived: false },
      ],
    },
  ],
};
const ARCHIVED = {
  folders: [
    {
      path: "C:\\work\\shop",
      name: "Shop site",
      is_project: false,
      project_id: null,
      sessions: [
        ...LIST.folders[0].sessions,
        { id: "z9", title: "Old idea", updated_at: "2026-10-01T10:00:00", running: false, waiting: false, archived: true },
      ],
    },
  ],
};

beforeEach(() => {
  api.gets = { "/build/sessions": LIST, "/build/sessions?archived=1": ARCHIVED };
  api.posts = [];
  api.dels = [];
  localStorage.clear();
  window.history.replaceState(null, "", "/chat?view=code");
});
afterEach(() => cleanup());

function sidebar(over: Partial<React.ComponentProps<typeof HarnessSidebar>> = {}) {
  const props = {
    folders: decodeFolders(LIST),
    listError: null,
    selectedId: "a1",
    onSelect: vi.fn(),
    onStart: vi.fn(async () => null as string | null),
    onRename: vi.fn(async () => null as string | null),
    onArchive: vi.fn(async () => null as string | null),
    onDelete: vi.fn(async () => null as string | null),
    terminals: [],
    onOpenTerminal: vi.fn(),
    ...over,
  };
  render(<HarnessSidebar {...props} />);
  return props;
}

describe("a session row's ⋯ menu", () => {
  it("archives a quiet session", async () => {
    const p = sidebar();
    fireEvent.click(screen.getByTestId("harness-row-menu-a2"));
    const item = screen.getByTestId("harness-row-archive-a2");
    expect(item).toHaveTextContent(/^Archive$/);
    fireEvent.click(item);
    await waitFor(() => expect(p.onArchive).toHaveBeenCalledWith("a2", false));
    expect(p.onSelect).not.toHaveBeenCalled();
  });

  it("says Stop and archive while the session works, and stops it first", async () => {
    const p = sidebar();
    fireEvent.click(screen.getByTestId("harness-row-menu-a1"));
    const item = screen.getByTestId("harness-row-archive-a1");
    expect(item).toHaveTextContent("Stop and archive");
    fireEvent.click(item);
    await waitFor(() => expect(p.onArchive).toHaveBeenCalledWith("a1", true));
  });

  it("deletes only on the second press", async () => {
    const p = sidebar();
    fireEvent.click(screen.getByTestId("harness-row-menu-a2"));
    const del = screen.getByTestId("harness-row-delete-a2");
    fireEvent.click(del);
    expect(p.onDelete).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("harness-row-delete-a2"));
    await waitFor(() => expect(p.onDelete).toHaveBeenCalledWith("a2"));
  });

  it("says a refusal in the daemon's words", async () => {
    sidebar({ onArchive: vi.fn(async () => "This session is still running something.") });
    fireEvent.click(screen.getByTestId("harness-row-menu-a2"));
    fireEvent.click(screen.getByTestId("harness-row-archive-a2"));
    expect(await screen.findByRole("alert")).toHaveTextContent("This session is still running something.");
  });

  it("closes on Escape and offers nothing when the view gives no actions", () => {
    sidebar();
    fireEvent.click(screen.getByTestId("harness-row-menu-a2"));
    fireEvent.keyDown(screen.getByTestId("harness-row-menu-list-a2"), { key: "Escape" });
    expect(screen.queryByTestId("harness-row-menu-list-a2")).toBeNull();
    cleanup();
    sidebar({ onArchive: undefined, onDelete: undefined });
    expect(screen.queryByTestId("harness-row-menu-a2")).toBeNull();
  });

  it("an archived row deletes too, on the second press", async () => {
    const p = sidebar({
      archived: decodeFolders({ folders: [{ ...ARCHIVED.folders[0], sessions: [ARCHIVED.folders[0].sessions[2]] }] }),
      onBringBack: vi.fn(async () => null as string | null),
    });
    fireEvent.click(screen.getByTestId("harness-archived-toggle"));
    fireEvent.click(screen.getByTestId("harness-archived-delete-z9"));
    expect(p.onDelete).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("harness-archived-delete-z9"));
    await waitFor(() => expect(p.onDelete).toHaveBeenCalledWith("z9"));
  });
});

describe("the view wires them to the daemon", () => {
  const terminal = { find: () => null, open: async () => null, render: () => null, close: () => {} };

  it("archives through the session's route (stopping a working one) and closes it", async () => {
    render(<IjHarnessView initialSession="a1" terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />);
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "a1"));
    fireEvent.click(screen.getByTestId("harness-row-menu-a1"));
    fireEvent.click(screen.getByTestId("harness-row-archive-a1"));
    await waitFor(() => expect(api.posts).toContainEqual(["/build/sessions/a1/archive", { stop: true }]));
    await waitFor(() => expect(screen.queryByTestId("session-stub")?.getAttribute("data-id") ?? null).not.toBe("a1"));
  });

  it("deletes through DELETE /build/sessions/{id}", async () => {
    render(<IjHarnessView initialSession="a1" terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />);
    const row = await screen.findByTestId("harness-session-row-a2");
    expect(within(row.closest("li") as HTMLElement).getByTestId("harness-row-menu-a2")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("harness-row-menu-a2"));
    fireEvent.click(screen.getByTestId("harness-row-delete-a2"));
    fireEvent.click(screen.getByTestId("harness-row-delete-a2"));
    await waitFor(() => expect(api.dels).toContain("/build/sessions/a2"));
  });
});
