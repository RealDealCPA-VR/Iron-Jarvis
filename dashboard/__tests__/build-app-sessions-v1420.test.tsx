/**
 * v1.420.0 (fix plan C4.10) — Build › Code's session list manages its
 * sessions: Rename in the row (PATCH title), Delete in two presses (the
 * existing DELETE route; a running session is refused by the daemon and its
 * words are said, nothing leaves the list), and a search over the titles and
 * folders the list holds (no new read).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  getLog: [] as string[],
  patches: [] as { path: string; body: unknown }[],
  patchReply: {} as Record<string, unknown>,
  dels: [] as string[],
  delFail: {} as Record<string, { status: number; message: string }>,
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: FakeApiError,
    get: (path: string) => {
      H.getLog.push(path);
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(r);
    },
    post: () => Promise.resolve({}),
    patch: (path: string, body?: unknown) => {
      H.patches.push({ path, body });
      return Promise.resolve(H.patchReply[path] ?? {});
    },
    del: (path: string) => {
      H.dels.push(path);
      const fail = H.delFail[path];
      if (fail) return Promise.reject(new FakeApiError(fail.message, fail.status));
      return Promise.resolve({ ok: true });
    },
  };
});
vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* () {},
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div data-testid="dir-tree" /> }));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";

const TOOLS = {
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      version: "2.1.311",
      modes: [{ id: "ask", label: "Ask first", dangerous: false }],
      hidden_modes: [],
      models: [],
      efforts: [],
      accounts: [],
      commands: [],
    },
  ],
};

const sess = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  harness: "claude-code",
  folder: "C:\\work\\shop",
  title: `Session ${id}`,
  account: "",
  model: "",
  effort: "",
  mode: "ask",
  harness_session_id: "",
  created_at: "2026-10-31T09:00:00Z",
  updated_at: "2026-10-31T09:00:00Z",
  archived_at: null,
  last_error: "",
  ...over,
});

const SESSIONS = "/build/app/sessions";

beforeEach(() => {
  H.getLog = [];
  H.patches = [];
  H.patchReply = {};
  H.dels = [];
  H.delFail = {};
  H.gets = {
    "/build/app/harnesses": TOOLS,
    [SESSIONS]: {
      sessions: [
        sess("a1", { title: "Fix the cart" }),
        sess("a2", { title: "Login page", folder: "C:\\work\\api" }),
        sess("a3", { title: "Write the README" }),
      ],
    },
    "/editor/options": { editors: [], preferred: "" },
  };
});

afterEach(() => cleanup());

function renderView() {
  return render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
}

describe("Rename", () => {
  it("renames in the row: Enter saves once with PATCH title; the row shows the answer", async () => {
    H.patchReply[`${SESSIONS}/a1`] = sess("a1", { title: "Cart totals" });
    renderView();
    fireEvent.click(await screen.findByTestId("code-row-more-a1"));
    fireEvent.click(screen.getByTestId("code-row-rename-a1"));
    const input = (await screen.findByTestId("code-row-rename-input-a1")) as HTMLInputElement;
    expect(input.value).toBe("Fix the cart");
    fireEvent.change(input, { target: { value: "  Cart totals " } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(H.patches).toEqual([{ path: `${SESSIONS}/a1`, body: { title: "Cart totals" } }]));
    await waitFor(() => expect(screen.getByTestId("code-row-a1")).toHaveTextContent("Cart totals"));
    expect(screen.queryByTestId("code-row-rename-input-a1")).toBeNull();
    // The input's blur after Enter never saves a second time.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(H.patches).toHaveLength(1);
  });

  it("Escape and an unchanged or empty name save nothing", async () => {
    renderView();
    fireEvent.click(await screen.findByTestId("code-row-more-a1"));
    fireEvent.click(screen.getByTestId("code-row-rename-a1"));
    const input = await screen.findByTestId("code-row-rename-input-a1");
    fireEvent.change(input, { target: { value: "Something else" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByTestId("code-row-rename-input-a1")).toBeNull();
    fireEvent.click(screen.getByTestId("code-row-more-a1"));
    fireEvent.click(screen.getByTestId("code-row-rename-a1"));
    const again = await screen.findByTestId("code-row-rename-input-a1");
    fireEvent.change(again, { target: { value: "   " } });
    fireEvent.keyDown(again, { key: "Enter" });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(H.patches).toEqual([]);
  });
});

describe("Delete", () => {
  it("takes two presses, then the session leaves the list", async () => {
    renderView();
    fireEvent.click(await screen.findByTestId("code-row-more-a3"));
    const del = screen.getByTestId("code-row-delete-a3").querySelector("button") as HTMLButtonElement;
    fireEvent.click(del);
    expect(H.dels).toEqual([]);
    expect(del.textContent).toBe("Delete for good?");
    fireEvent.click(del);
    await waitFor(() => expect(H.dels).toEqual([`${SESSIONS}/a3`]));
    await waitFor(() => expect(screen.queryByTestId("code-row-a3")).toBeNull());
    expect(screen.getByTestId("code-row-a1")).toBeInTheDocument();
  });

  it("a running session is refused by Iron Jarvis: its words are said, the row stays", async () => {
    const REFUSAL = "This session's reply is still running. Stop it first, then delete it.";
    H.delFail[`${SESSIONS}/a1`] = { status: 409, message: REFUSAL };
    H.gets[SESSIONS] = { sessions: [sess("a1", { title: "Fix the cart", running: true })] };
    renderView();
    fireEvent.click(await screen.findByTestId("code-row-more-a1"));
    const del = screen.getByTestId("code-row-delete-a1").querySelector("button") as HTMLButtonElement;
    fireEvent.click(del);
    fireEvent.click(del);
    expect(await screen.findByTestId("code-row-note")).toHaveTextContent(REFUSAL);
    expect(screen.getByTestId("code-row-a1")).toBeInTheDocument();
  });
});

describe("Search", () => {
  it("filters by title and by folder, says when nothing matches, Escape clears; no new read", async () => {
    renderView();
    const box = await screen.findByTestId("code-session-search");
    await screen.findByTestId("code-row-a1");
    const reads = H.getLog.filter((p) => p === SESSIONS).length;
    fireEvent.change(box, { target: { value: "cart" } });
    expect(screen.getByTestId("code-row-a1")).toBeInTheDocument();
    expect(screen.queryByTestId("code-row-a2")).toBeNull();
    expect(screen.queryByTestId("code-row-a3")).toBeNull();
    // A folder's name finds its sessions.
    fireEvent.change(box, { target: { value: "API" } });
    expect(screen.getByTestId("code-row-a2")).toBeInTheDocument();
    expect(screen.queryByTestId("code-row-a1")).toBeNull();
    // Every word must be there.
    fireEvent.change(box, { target: { value: "write readme" } });
    expect(screen.getByTestId("code-row-a3")).toBeInTheDocument();
    expect(screen.queryByTestId("code-row-a1")).toBeNull();
    fireEvent.change(box, { target: { value: "nothing like it" } });
    expect(screen.getByTestId("code-search-none")).toHaveTextContent("No sessions match “nothing like it”.");
    fireEvent.keyDown(box, { key: "Escape" });
    expect((box as HTMLInputElement).value).toBe("");
    expect(screen.getByTestId("code-row-a1")).toBeInTheDocument();
    expect(screen.getByTestId("code-row-a2")).toBeInTheDocument();
    expect(H.getLog.filter((p) => p === SESSIONS).length).toBe(reads);
  });
});
