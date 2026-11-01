/**
 * v1.420.0 (C4.12) — Build › Code's Changes pane: "This message" beside
 * "Whole session", and "Revert this file" (two presses) through the real
 * CodePanes. The daemon's answers are the shapes
 * `tests/test_build_code_revert_v1420.py` pins on the Python side.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  calls: [] as string[],
  posts: [] as Array<{ path: string; body: unknown }>,
  postAnswer: null as unknown,
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError: FakeApiError,
    get: (path: string) => {
      H.calls.push(path);
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(r);
    },
    post: (path: string, body: unknown) => {
      H.posts.push({ path, body });
      if (H.postAnswer instanceof Error) return Promise.reject(H.postAnswer);
      return Promise.resolve(H.postAnswer);
    },
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    ijToken: () => "",
    API_BASE: "http://127.0.0.1:8787",
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* () {},
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => null }));

import { CodePanes } from "@/components/buildCode/CodePanes";
import { decodeCodeSession, decodeCodingTools } from "@/lib/buildApp";
import { REVERT_LABEL, decodeCodeChanges, revertBody } from "@/lib/codePanes";

const SESSION = decodeCodeSession({
  id: "app_1",
  harness: "claude-code",
  folder: "C:\\work\\shop",
  title: "Fix the cart",
  account: "",
  model: "",
  effort: "low",
  mode: "ask",
  harness_session_id: "",
  created_at: "2026-10-30T09:00:00Z",
  updated_at: "2026-10-30T09:00:00Z",
  archived_at: null,
  last_error: "",
  capabilities: { files: false, shell: false, browser: false, extensions: false, memory: true },
})!;
const TOOLS = decodeCodingTools({
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      own_sign_in: false,
      modes: [{ id: "ask", label: "Ask first", dangerous: false, about: "" }],
      hidden_modes: [],
      models: [{ id: "", label: "Claude Code's default" }],
      efforts: ["low"],
      accounts: [],
      commands: [],
    },
  ],
});

function changes(scope: "session" | "turn", rows: Array<Record<string, unknown>>) {
  return {
    source: "git",
    words: scope === "turn" ? "What changed in this folder while this message ran" : "Every file in this folder…",
    since: "2026-10-30T09:00:00Z",
    scope,
    turn: scope === "turn" ? "t2" : undefined,
    turn_how: scope === "turn" ? "start" : undefined,
    can_revert: true,
    changes: rows,
    files: rows.length,
    added: 1,
    removed: 1,
    truncated_files: false,
    by_tool: 0,
    no_undo:
      "No Undo here: Claude Code made these changes itself. Revert puts ONE file back as it was when the session started here; the branch, its commits and every other file stay as they are.",
  };
}
const CART = {
  path: "C:\\work\\shop\\cart.py",
  rel: "cart.py",
  name: "cart.py",
  status: "modified",
  added: 1,
  removed: 1,
  diff: "--- a/cart.py\n+++ b/cart.py\n@@ -1 +1 @@\n-y = 2\n+y = 3",
  by_tool: true,
  blob: "a".repeat(40),
};
const NOTES = { ...CART, path: "C:\\work\\shop\\notes.md", rel: "notes.md", name: "notes.md", blob: "b".repeat(40) };

beforeEach(() => {
  window.localStorage.clear();
  H.calls = [];
  H.posts = [];
  H.postAnswer = { ok: true, words: "cart.py is back as it was when this message started." };
  H.gets = {
    "/build/app/sessions/app_1/changes": changes("session", [CART, NOTES]),
    "/build/app/sessions/app_1/changes?scope=turn": changes("turn", [CART]),
    "/build/app/sessions/app_1/usage": { totals: {} },
    "/build/app/sessions/app_1/overview": { git: {}, rules: [], programs: [] },
    "/editor/options": { editors: [], preferred: "" },
  };
});
afterEach(() => cleanup());

function panes(running = false) {
  return render(<CodePanes session={SESSION} tools={TOOLS} events={[]} running={running} />);
}

describe("the decoder", () => {
  it("keeps the blob, the old name, the scope and the turn; an older daemon has no Revert", () => {
    const d = decodeCodeChanges({ ...changes("turn", [{ ...CART, old_rel: "basket.py" }]) })!;
    expect(d.scope).toBe("turn");
    expect(d.turn).toBe("t2");
    expect(d.canRevert).toBe(true);
    expect(d.changes[0].blob).toBe("a".repeat(40));
    expect(revertBody(d.changes[0], d)).toEqual({
      path: "cart.py",
      blob: "a".repeat(40),
      old_rel: "basket.py",
      scope: "turn",
      turn: "t2",
    });
    const old = decodeCodeChanges({ source: "git", changes: [CART] })!;
    expect(old.canRevert).toBe(false);
    expect(old.scope).toBe("session");
    // A tool record never offers Revert, whatever the flag says.
    expect(decodeCodeChanges({ source: "tool", can_revert: true, changes: [CART] })!.canRevert).toBe(false);
  });
});

describe("This message beside Whole session", () => {
  it("reads the whole session first, and this message's own list on the press", async () => {
    panes();
    expect(await screen.findAllByText("notes.md")).not.toHaveLength(0);
    expect(screen.getByTestId("code-changes-scope-session").getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByTestId("code-changes-scope-turn"));
    await waitFor(() => expect(H.calls).toContain("/build/app/sessions/app_1/changes?scope=turn"));
    await waitFor(() =>
      expect(screen.getByTestId("code-changes-source").textContent).toContain("while this message ran"),
    );
    expect(screen.queryAllByText("notes.md")).toHaveLength(0);
    expect(screen.getByTestId("code-changes-scope-turn").getAttribute("aria-pressed")).toBe("true");
  });
});

describe("Revert this file", () => {
  it("takes two presses, posts the row's blob and scope, and reads the list again", async () => {
    panes();
    await screen.findByTestId("code-changes-revert");
    const before = H.calls.filter((c) => c.endsWith("/changes")).length;
    const press = () => screen.getByTestId("code-revert-0").querySelector("button")!;
    expect(press().textContent).toBe(REVERT_LABEL);
    fireEvent.click(press());
    expect(H.posts).toHaveLength(0); // the first press only arms
    expect(press().textContent).toContain("Put cart.py back?");
    fireEvent.click(press());
    await waitFor(() => expect(H.posts).toHaveLength(1));
    expect(H.posts[0].path).toBe("/build/app/sessions/app_1/changes/revert");
    expect(H.posts[0].body).toMatchObject({ path: "cart.py", blob: "a".repeat(40), scope: "session" });
    expect((await screen.findByTestId("code-changes-revert-note")).textContent).toContain("is back as it was");
    await waitFor(() => expect(H.calls.filter((c) => c.endsWith("/changes")).length).toBeGreaterThan(before));
  });

  it("says the daemon's refusal in its own words", async () => {
    H.postAnswer = new Error("cart.py changed after this list was read, so it was left as it is.");
    panes();
    await screen.findByTestId("code-changes-revert");
    const press = () => screen.getByTestId("code-revert-0").querySelector("button")!;
    fireEvent.click(press());
    fireEvent.click(press());
    const note = await screen.findByTestId("code-changes-revert-note");
    expect(note.getAttribute("role")).toBe("alert");
    expect(note.textContent).toContain("changed after this list was read");
  });

  it("is not offered while a reply runs, and says when it is", async () => {
    panes(true);
    expect(await screen.findByTestId("code-changes-revert-waits")).toBeTruthy();
    expect(screen.queryByTestId("code-changes-revert")).toBeNull();
  });
});
