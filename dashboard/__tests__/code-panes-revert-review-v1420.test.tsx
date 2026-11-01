/**
 * v1.420.0 (C4.12) REVIEW — a Revert press armed under one scope never
 * carries over to the other: the same file has a DIFFERENT base there
 * ("Whole session" puts it back to the session's start, "This message" to
 * the message's), so a second press after switching would put back what the
 * user never confirmed. Through the real CodePanes.
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
import { REVERT_LABEL } from "@/lib/codePanes";

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

describe("two presses never span two scopes", () => {
  it("an armed Revert is disarmed when the list's scope changes", async () => {
    panes();
    await screen.findByTestId("code-changes-revert");
    const press = () => screen.getByTestId("code-revert-0").querySelector("button")!;
    fireEvent.click(press()); // armed under "Whole session"
    expect(press().textContent).toContain("Put cart.py back?");
    fireEvent.click(screen.getByTestId("code-changes-scope-turn"));
    await waitFor(() =>
      expect(screen.getByTestId("code-changes-source").textContent).toContain("while this message ran"),
    );
    expect(press().textContent).toBe(REVERT_LABEL);
    fireEvent.click(press());
    expect(H.posts).toHaveLength(0); // one press after the switch only arms
  });
});
