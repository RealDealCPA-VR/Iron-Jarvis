/**
 * v1.412.0 review — an abandoned New session does not pile up.
 *
 * New session (and Ctrl+N) makes a server row at once, so every press left an
 * empty "New session" row behind. Now a session THIS view made by New session
 * (or moved there by "Choose…") that the user LEAVES — another session opened,
 * New session pressed again, the view closed — while it still has no message,
 * no upload, no words in its box and no running reply is deleted with the
 * daemon's `only_if_empty` guard. Anything with content stays.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  calls: [] as { method: string; path: string; body?: unknown }[],
  n: 0,
}));
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
      api.calls.push({ method: "POST", path: p, body });
      if (p === "/build/sessions") {
        api.n += 1;
        return Promise.resolve({ id: `new${api.n}`, title: "New session", folder: "", no_folder: true });
      }
      return Promise.resolve({});
    },
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: (p: string) => {
      api.calls.push({ method: "DELETE", path: p });
      return Promise.resolve({});
    },
  };
});
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div data-testid="tree" /> }));
vi.mock("@/components/harness/HarnessSession", async () => {
  const used = await import("@/lib/emptySessions");
  return {
    HarnessSession: ({ threadId }: { threadId: string }) => (
      <div data-testid="session-stub" data-id={threadId}>
        <button type="button" data-testid="stub-type" onClick={() => used.noteSessionUsed(threadId)}>
          type
        </button>
      </div>
    ),
  };
});

import { IjHarnessView } from "@/components/harness/IjHarnessView";
import { forgetSessionUse } from "@/lib/emptySessions";

const LIST = {
  folders: [
    {
      path: "C:\\work\\shop",
      name: "shop",
      is_project: false,
      project_id: null,
      sessions: [{ id: "a1", title: "Fix the cart", updated_at: "2026-10-23T10:00:00", running: false, waiting: false, archived: false }],
    },
  ],
};
const terminal = { find: () => null, open: async () => null, render: () => null, close: () => {} };

beforeEach(() => {
  api.gets = { "/build/sessions": LIST };
  api.calls = [];
  api.n = 0;
  localStorage.clear();
  forgetSessionUse();
  window.history.replaceState(null, "", "/terminals");
});
afterEach(() => cleanup());

const deletes = () => api.calls.filter((c) => c.method === "DELETE").map((c) => c.path);

async function mount() {
  const out = render(
    <IjHarnessView initialSession="a1" terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />,
  );
  await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "a1"));
  return out;
}

async function newSession(id: string) {
  fireEvent.click(screen.getByTestId("harness-new-session"));
  await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", id));
}

describe("an abandoned New session", () => {
  it("is deleted (only if empty) when New session is pressed again", async () => {
    await mount();
    await newSession("new1");
    expect(deletes()).toEqual([]);
    await newSession("new2");
    await waitFor(() => expect(deletes()).toEqual(["/build/sessions/new1?only_if_empty=1"]));
  });

  it("is deleted when another session is opened", async () => {
    await mount();
    await newSession("new1");
    fireEvent.click(screen.getByTestId("harness-session-row-a1"));
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "a1"));
    await waitFor(() => expect(deletes()).toEqual(["/build/sessions/new1?only_if_empty=1"]));
  });

  it("is deleted when the view closes", async () => {
    const { unmount } = await mount();
    await newSession("new1");
    unmount();
    await waitFor(() => expect(deletes()).toEqual(["/build/sessions/new1?only_if_empty=1"]));
  });

  it("one the user typed in (or attached to) is kept", async () => {
    await mount();
    await newSession("new1");
    fireEvent.click(screen.getByTestId("stub-type"));
    fireEvent.click(screen.getByTestId("harness-session-row-a1"));
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "a1"));
    await new Promise((r) => setTimeout(r, 30));
    expect(deletes()).toEqual([]);
  });

  it("a session the user opened from the list is never deleted for being left", async () => {
    await mount();
    await newSession("new1");
    fireEvent.click(screen.getByTestId("stub-type"));
    await newSession("new2");
    fireEvent.click(screen.getByTestId("stub-type"));
    fireEvent.click(screen.getByTestId("harness-session-row-a1"));
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "a1"));
    await new Promise((r) => setTimeout(r, 30));
    expect(deletes()).toEqual([]);
  });
});
