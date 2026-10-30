/**
 * v1.379.0 REVIEW — the merge of v1.378.0 ("New session opens on the folder
 * of the session in front") with v1.379.0 (a session with NO folder).
 *
 * DECISION: in front of a session with no folder, New session opens with no
 * folder too — the box empty and the folder list closed, so Start makes
 * another session with no folder — never the Terminals view's folder (which
 * the folderless group's empty path used to fall through to, with the folder
 * list opened as if nothing had been chosen). In front of a folder session,
 * v1.378.0's rule stands.
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
    post: (p: string, b?: unknown) => {
      api.posts.push({ path: p, body: b });
      return Promise.resolve({ id: "new1", folder: "", no_folder: true, title: "New session", messages: [] });
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
  work_root: "C:\\Users\\me\\Documents\\Iron Jarvis",
  folders: [
    {
      path: "",
      name: "No folder",
      no_folder: true,
      is_project: false,
      project_id: null,
      sessions: [{ id: "n1", title: "Plan a trip", updated_at: "2026-10-23T10:00:00", running: false, waiting: false, archived: false }],
    },
    {
      path: "C:\\work\\shop",
      name: "shop",
      is_project: false,
      project_id: null,
      sessions: [{ id: "a1", title: "Fix the cart", updated_at: "2026-10-22T10:00:00", running: false, waiting: false, archived: false }],
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

// v1.412.0 (moved on purpose): there is no New session form any more —
// New session starts a session with no folder AT ONCE, in front of any
// session; only where its "Choose…" opens follows the session in front.
async function startInFrontOf(id: string) {
  render(
    <IjHarnessView
      initialSession={id}
      terminals={[]}
      onOpenTerminal={() => {}}
      defaultFolder={"C:\\the\\terminals\\folder"}
      terminal={terminal}
    />,
  );
  await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", id));
  await screen.findAllByTestId("harness-folder-group");
  fireEvent.click(screen.getByTestId("harness-new-session"));
  await waitFor(() => expect(api.posts.find((p) => p.path === "/build/sessions")).toBeTruthy());
  return api.posts.find((p) => p.path === "/build/sessions")?.body;
}

describe("New session in front of a session with no folder", () => {
  it("makes another with no folder — never the Terminals view's folder", async () => {
    expect(await startInFrontOf("n1")).toEqual({ folder: "" });
    expect(screen.queryByTestId("tree")).toBeNull();
  });

  it("in front of a folder session it ALSO starts with no folder (v1.412.0: the user's choice)", async () => {
    expect(await startInFrontOf("a1")).toEqual({ folder: "" });
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "new1"));
    expect(screen.getByTestId("session-stub")).toHaveAttribute("data-pick-from", "C:\\work\\shop");
  });
});
