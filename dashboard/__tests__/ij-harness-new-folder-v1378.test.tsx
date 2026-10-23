/**
 * v1.378.0 (IJ harness part 2, Phase 7, the walkthrough) — New session opens
 * on the folder of the session in front.
 *
 * The walkthrough's second task starts another session in the same folder as
 * the first. The New session box was filled from the Terminals view's folder
 * (the terminal in front, else the last folder picked there) — empty in a
 * window that never used a terminal, or another project's folder — so the
 * user pasted the folder again each time: one press Claude Code does not need.
 * The folder of the session in front (its group's folder: the folder the user
 * picked, for a session on its own branch too) is now the default; with no
 * session open the old default stands.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({ gets: {} as Record<string, unknown> }));
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    get: (p: string) => (p in api.gets ? Promise.resolve(api.gets[p]) : Promise.reject(new ApiError(`unmocked ${p}`, 404))),
    post: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div data-testid="tree" /> }));
vi.mock("@/components/harness/HarnessSession", () => ({
  HarnessSession: ({ threadId }: { threadId: string }) => <div data-testid="session-stub" data-id={threadId} />,
}));

import { IjHarnessView } from "@/components/harness/IjHarnessView";

const LIST = {
  folders: [
    {
      path: "C:\\work\\shop",
      name: "shop",
      is_project: false,
      project_id: null,
      sessions: [{ id: "a1", title: "Fix the cart", updated_at: "2026-10-23T10:00:00", running: false, waiting: false, archived: false }],
    },
    {
      path: "C:\\work\\tools",
      name: "tools",
      is_project: false,
      project_id: null,
      sessions: [{ id: "b1", title: "Script", updated_at: "2026-10-22T10:00:00", running: false, waiting: false, archived: false }],
    },
  ],
};
const terminal = { find: () => null, open: async () => null, render: () => null, close: () => {} };

beforeEach(() => {
  api.gets = { "/build/sessions": LIST };
  localStorage.clear();
  window.history.replaceState(null, "", "/terminals");
});
afterEach(() => cleanup());

async function openForm(initialSession: string | null, defaultFolder: string | null): Promise<HTMLInputElement> {
  render(
    <IjHarnessView
      initialSession={initialSession}
      terminals={[]}
      onOpenTerminal={() => {}}
      defaultFolder={defaultFolder}
      terminal={terminal}
    />,
  );
  if (initialSession) {
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", initialSession));
  }
  // The folder groups are drawn from the list: the default is read off them.
  await screen.findAllByTestId("harness-folder-group");
  fireEvent.click(screen.getByTestId("harness-new-session"));
  return screen.getByTestId("harness-new-folder") as HTMLInputElement;
}

describe("New session's folder", () => {
  it("is the folder of the session in front, not the Terminals view's folder", async () => {
    const box = await openForm("b1", "C:\\somewhere\\else");
    expect(box.value).toBe("C:\\work\\tools");
  });

  it("follows the session the user opens", async () => {
    render(
      <IjHarnessView initialSession="b1" terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />,
    );
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "b1"));
    fireEvent.click(screen.getByTestId("harness-session-row-a1"));
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "a1"));
    fireEvent.click(screen.getByTestId("harness-new-session"));
    expect((screen.getByTestId("harness-new-folder") as HTMLInputElement).value).toBe("C:\\work\\shop");
  });

  it("with no session open, is the old default (the Terminals view's folder)", async () => {
    api.gets = { "/build/sessions": { folders: [] } };
    render(
      <IjHarnessView initialSession={null} terminals={[]} onOpenTerminal={() => {}} defaultFolder={"C:\\last"} terminal={terminal} />,
    );
    await waitFor(() => expect(screen.getByTestId("harness-new-session")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("harness-new-session"));
    expect((screen.getByTestId("harness-new-folder") as HTMLInputElement).value).toBe("C:\\last");
  });
});
