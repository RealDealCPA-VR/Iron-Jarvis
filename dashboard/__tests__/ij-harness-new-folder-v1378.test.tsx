/**
 * v1.378.0 (IJ harness part 2, Phase 7, the walkthrough) — New session opened
 * on the folder of the session in front.
 *
 * v1.412.0 (MOVED ON PURPOSE, the user's request): New session starts AT ONCE
 * with NO folder ("it should start automatically with a no folder chosen") —
 * no form, no folder box. The folder of the session in front (v1.378.0) is
 * now where the session's "Choose…" opens the folder picker
 * (`folderPickFrom`); with no session open, the Terminals view's folder.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({ gets: {} as Record<string, unknown>, posts: [] as { path: string; body: unknown }[] }));
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
    post: (p: string, body?: unknown) => {
      api.posts.push({ path: p, body });
      return Promise.resolve(p === "/build/sessions" ? { id: "new1", title: "New session", folder: "", no_folder: true } : {});
    },
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div data-testid="tree" /> }));
vi.mock("@/components/harness/HarnessSession", () => ({
  HarnessSession: ({ threadId, folderPickFrom }: { threadId: string; folderPickFrom?: string | null }) => (
    <div data-testid="session-stub" data-id={threadId} data-pick-from={folderPickFrom ?? ""} />
  ),
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
  api.posts = [];
  localStorage.clear();
  window.history.replaceState(null, "", "/terminals");
});
afterEach(() => cleanup());

async function startNew(initialSession: string | null, defaultFolder: string | null): Promise<HTMLElement> {
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
    await screen.findAllByTestId("harness-folder-group");
  } else {
    await waitFor(() => expect(screen.getByTestId("harness-new-session")).toBeInTheDocument());
  }
  fireEvent.click(screen.getByTestId("harness-new-session"));
  await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "new1"));
  return screen.getByTestId("session-stub");
}

describe("New session (v1.412.0): at once, no folder; Choose… opens where the user was", () => {
  it("starts a session with no folder at once — no form — whatever the session in front", async () => {
    await startNew("b1", "C:\\somewhere\\else");
    expect(screen.queryByTestId("harness-new-session-form")).toBeNull();
    expect(api.posts.filter((p) => p.path === "/build/sessions").map((p) => p.body)).toEqual([{ folder: "" }]);
  });

  it("the new session's picker opens on the folder of the session in front, not the Terminals view's", async () => {
    const stub = await startNew("b1", "C:\\somewhere\\else");
    expect(stub).toHaveAttribute("data-pick-from", "C:\\work\\tools");
  });

  it("follows the session the user opens", async () => {
    render(
      <IjHarnessView initialSession="b1" terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />,
    );
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "b1"));
    fireEvent.click(screen.getByTestId("harness-session-row-a1"));
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "a1"));
    expect(screen.getByTestId("session-stub")).toHaveAttribute("data-pick-from", "C:\\work\\shop");
    fireEvent.click(screen.getByTestId("harness-new-session"));
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "new1"));
    expect(screen.getByTestId("session-stub")).toHaveAttribute("data-pick-from", "C:\\work\\shop");
  });

  it("with no session open, the picker opens on the Terminals view's folder", async () => {
    api.gets = { "/build/sessions": { folders: [] } };
    const stub = await startNew(null, "C:\\last");
    expect(stub).toHaveAttribute("data-pick-from", "C:\\last");
  });
});
