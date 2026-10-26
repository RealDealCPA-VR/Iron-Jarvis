/**
 * v1.398.0 (night shift N1) — Coding sessions › Tonight
 * (components/harness/TonightList, lib/nightShift, the sidebar's Tonight row).
 *
 *  - the window in words, the limits, the rows' states in the ONE status
 *    table's words (Queued / Working / Done / Failed / Needs you / Stopped),
 *    what happened folded under a row;
 *  - add (folder, words, how it runs: a model, the team, try on 2–3 models);
 *    a refusal shows Iron Jarvis's own words;
 *  - move up / down, edit, cancel (never starts), remove (two presses), stop
 *    a running task (two presses), open its session;
 *  - Emergency stop said once above the list;
 *  - the keyboard: Ctrl+Enter adds from the words box.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    list: null as unknown,
    calls: [] as [string, string, unknown][],
    fail: {} as Record<string, Error>,
  };
});
vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) => {
    api.calls.push(["GET", p, undefined]);
    if (p === "/build/night") return Promise.resolve(api.list);
    if (p === "/build/sessions") return Promise.resolve(SESSIONS);
    return Promise.reject(new api.ApiError(`unmocked ${p}`, 404));
  },
  post: (p: string, b?: unknown) => {
    api.calls.push(["POST", p, b]);
    if (api.fail[p]) return Promise.reject(api.fail[p]);
    if (p === "/build/night") return Promise.resolve({ id: "new", state: "queued", words: (b as { words: string }).words });
    return Promise.resolve({});
  },
  patch: (p: string, b?: unknown) => {
    api.calls.push(["PATCH", p, b]);
    return Promise.resolve({ id: "t1", state: "queued" });
  },
  put: () => Promise.resolve({}),
  del: (p: string) => {
    api.calls.push(["DELETE", p, undefined]);
    return Promise.resolve({ ok: true });
  },
}));
vi.mock("@/lib/useModels", () => {
  const usable = [
    { provider: "claude-cli", model: "claude-opus-4-8", label: "Opus 4.8", available: true },
    { provider: "codex-cli", model: "gpt-6", label: "GPT-6", available: true },
    { provider: "custom", model: "glm-5.3", label: "GLM 5.3", available: true },
    { provider: "ollama", model: "qwen3", label: "Qwen 3", available: true },
  ];
  return { useModels: () => ({ models: usable, usable, data: { models: usable }, loading: false, error: null, reload: () => {} }) };
});

vi.mock("@/components/harness/HarnessSession", () => ({
  HarnessSession: ({ threadId }: { threadId: string }) => <div data-testid="session-stub" data-id={threadId} />,
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => null }));

const SESSIONS = {
  folders: [
    {
      path: "C:\work\shop", name: "shop", is_project: false, project_id: null,
      sessions: [{ id: "s1", title: "Add a cart", updated_at: "2026-10-26T10:00:00", running: false, waiting: false, archived: false }],
    },
  ],
};

import { TonightList } from "@/components/harness/TonightList";
import { IjHarnessView } from "@/components/harness/IjHarnessView";
import { decodeList, nightStateWords } from "@/lib/nightShift";

function task(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    folder: "C:\\work\\shop",
    words: `Task ${id}`,
    provider: "",
    model: "",
    team: false,
    try_on: [],
    position: 1,
    state: "queued",
    thread_id: "",
    turn_id: "",
    branch: "",
    notes: [],
    created_at: "2026-10-26T18:00:00Z",
    updated_at: "2026-10-26T18:00:00Z",
    started_at: null,
    ended_at: null,
    ...over,
  };
}

function listOf(tasks: unknown[], over: Record<string, unknown> = {}) {
  return {
    tasks,
    window: { start: "23:00", end: "07:00", open: false, words: "Starts at 11:00 PM tonight.", next_start: null },
    limits: { at_once: 2, task_minutes: 90, card_wait_minutes: 15 },
    emergency_stop: false,
    ...over,
  };
}

async function shown(onOpen = vi.fn()) {
  render(<TonightList folders={["C:\\work\\shop", "C:\\work\\api"]} onOpenSession={onOpen} />);
  await screen.findByTestId("tonight-window");
  return onOpen;
}

beforeEach(() => {
  api.calls = [];
  api.fail = {};
  api.list = listOf([]);
});
afterEach(() => cleanup());

describe("Tonight", () => {
  it("says the window and the limits, and the rows' states in the status table's words", async () => {
    api.list = listOf([
      task("t1", { state: "running", thread_id: "s1", branch: "ij/fix-tests-1a2b", started_at: "2026-10-26T23:00:05Z" }),
      task("t2"),
      task("t3", { state: "needs_you", thread_id: "s3", notes: [{ kind: "declined", text: "Needs your OK: run a command (nobody answered in 15 minutes, so it was skipped).", at: null }] }),
      task("t4", { state: "done", thread_id: "s4" }),
      task("t5", { state: "failed", notes: [{ kind: "error", text: "This folder cannot be used: it does not exist", at: null }] }),
      task("t6", { state: "stopped" }),
    ]);
    await shown();
    expect(screen.getByTestId("tonight-window").textContent).toBe("Starts at 11:00 PM tonight.");
    expect(screen.getByText("Up to 2 at a time, never two in one folder, 90 minutes each. Each runs on its own branch.")).toBeTruthy();
    const words = (id: string) => screen.getByTestId(`tonight-state-${id}`).textContent;
    expect([words("t1"), words("t2"), words("t3"), words("t4"), words("t5"), words("t6")]).toEqual([
      "Working", "Queued", "Needs you", "Done", "Failed", "Stopped",
    ]);
    // The words are the ONE table's (lib/statusWords).
    expect(nightStateWords("needs_you").word).toBe("Needs you");
    // What happened is folded under the row.
    const notes = screen.getByTestId("tonight-notes-t3");
    expect(notes.tagName).toBe("DETAILS");
    expect(within(notes).getByText(/nobody answered in 15 minutes/)).toBeTruthy();
    // The branch shows on a started row.
    expect(within(screen.getByTestId("tonight-task-t1")).getByText(/ij\/fix-tests-1a2b/)).toBeTruthy();
  });

  it("adds a task with the team, and shows a refusal in Iron Jarvis's own words", async () => {
    await shown();
    expect(screen.getByTestId("tonight-empty")).toBeTruthy();
    fireEvent.click(screen.getByTestId("tonight-add"));
    fireEvent.change(screen.getByTestId("tonight-folder"), { target: { value: "C:\\work\\api" } });
    fireEvent.change(screen.getByTestId("tonight-words"), { target: { value: "Fix the parser tests" } });
    fireEvent.change(screen.getByTestId("tonight-how"), { target: { value: "team" } });
    api.fail["/build/night"] = new api.ApiError(
      "A night task works on its own branch, so its folder must be in a git repository with at least one commit: x", 400,
    );
    fireEvent.click(screen.getByTestId("tonight-submit"));
    expect((await screen.findByTestId("tonight-problem")).textContent).toMatch(/^A night task works on its own branch/);
    delete api.fail["/build/night"];
    api.list = listOf([task("new", { words: "Fix the parser tests", team: true })]);
    fireEvent.click(screen.getByTestId("tonight-submit"));
    await waitFor(() => expect(screen.getByTestId("tonight-task-new")).toBeTruthy());
    const posts = api.calls.filter(([m, p]) => m === "POST" && p === "/build/night");
    expect(posts[posts.length - 1][2]).toEqual({
      folder: "C:\\work\\api", words: "Fix the parser tests", provider: "", model: "", team: true, try_on: [],
    });
    expect(within(screen.getByTestId("tonight-task-new")).getByText(/Your model team/)).toBeTruthy();
  });

  it("tries a task on at most three models", async () => {
    await shown();
    fireEvent.click(screen.getByTestId("tonight-add"));
    fireEvent.change(screen.getByTestId("tonight-words"), { target: { value: "Try it" } });
    fireEvent.change(screen.getByTestId("tonight-how"), { target: { value: "try" } });
    const box = screen.getByTestId("tonight-try-on");
    const checks = within(box).getAllByRole("checkbox") as HTMLInputElement[];
    for (const c of checks.slice(0, 3)) fireEvent.click(c);
    expect(checks[3].disabled).toBe(true);
    fireEvent.click(screen.getByTestId("tonight-submit"));
    await waitFor(() => expect(api.calls.some(([m, p]) => m === "POST" && p === "/build/night")).toBe(true));
    const body = api.calls.find(([m, p]) => m === "POST" && p === "/build/night")![2] as { try_on: unknown[] };
    expect(body.try_on).toEqual([
      { provider: "claude-cli", model: "claude-opus-4-8" },
      { provider: "codex-cli", model: "gpt-6" },
      { provider: "custom", model: "glm-5.3" },
    ]);
  });

  it("Ctrl+Enter in the words box adds the task", async () => {
    await shown();
    fireEvent.click(screen.getByTestId("tonight-add"));
    const words = screen.getByTestId("tonight-words");
    fireEvent.change(words, { target: { value: "Write the changelog" } });
    fireEvent.keyDown(words, { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(api.calls.some(([m, p]) => m === "POST" && p === "/build/night")).toBe(true));
  });

  it("moves, edits, cancels and removes a queued task", async () => {
    api.list = listOf([task("q1"), task("q2")]);
    await shown();
    expect((screen.getByTestId("tonight-up-q1") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId("tonight-down-q2") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId("tonight-up-q2"));
    await waitFor(() => expect(api.calls).toContainEqual(["POST", "/build/night/q2/move", { direction: "up" }]));
    fireEvent.click(screen.getByTestId("tonight-cancel-q1"));
    await waitFor(() => expect(api.calls).toContainEqual(["POST", "/build/night/q1/cancel", {}]));
    // Edit opens the same fields in place; Save sends a PATCH.
    fireEvent.click(screen.getByTestId("tonight-edit-q2"));
    fireEvent.change(screen.getByTestId("tonight-words"), { target: { value: "Task q2, better" } });
    fireEvent.click(screen.getByTestId("tonight-submit"));
    await waitFor(() => expect(api.calls.some(([m, p]) => m === "PATCH" && p === "/build/night/q2")).toBe(true));
    // Remove is two presses.
    const row = await screen.findByTestId("tonight-task-q1");
    const remove = within(row).getByText("Remove");
    fireEvent.click(remove);
    expect(api.calls.some(([m]) => m === "DELETE")).toBe(false);
    await act(async () => {
      fireEvent.click(within(row).getByText("Remove it?"));
    });
    await waitFor(() => expect(api.calls).toContainEqual(["DELETE", "/build/night/q1", undefined]));
  });

  it("opens a running task's session and stops it with two presses", async () => {
    api.list = listOf([task("r1", { state: "running", thread_id: "sess-r1" })]);
    const onOpen = await shown();
    fireEvent.click(screen.getByTestId("tonight-open-r1"));
    expect(onOpen).toHaveBeenCalledWith("sess-r1");
    const row = screen.getByTestId("tonight-task-r1");
    expect(within(row).queryByText("Remove")).toBeNull();
    fireEvent.click(within(row).getByText("Stop"));
    expect(api.calls.some(([, p]) => p === "/build/night/r1/cancel")).toBe(false);
    await act(async () => {
      fireEvent.click(within(row).getByText("Stop it?"));
    });
    await waitFor(() => expect(api.calls).toContainEqual(["POST", "/build/night/r1/cancel", {}]));
  });

  it("says Emergency stop once above the list", async () => {
    api.list = listOf([task("q1")], { emergency_stop: true });
    await shown();
    expect(screen.getByTestId("tonight-emergency").textContent).toMatch(/^Emergency stop is on, so nothing here starts\./);
  });

  it("decodes only what the list promises", () => {
    const got = decodeList({
      tasks: [task("a", { state: "exploded", try_on: [{ provider: "x" }, { nope: 1 }], notes: [{ text: "" }, { kind: "env", text: "t" }] }), { no: "id" }],
      window: { words: "w" },
      limits: {},
    });
    expect(got.tasks.map((t) => [t.id, t.state, t.tryOn.length, t.notes.length])).toEqual([["a", "queued", 1, 1]]);
    expect([got.atOnce, got.taskMinutes, got.cardWaitMinutes, got.emergencyStop]).toEqual([2, 90, 15, false]);
  });

  it("the Tonight row opens the list in the middle, and Open goes to the task's session", async () => {
    localStorage.clear();
    window.history.replaceState(null, "", "/terminals");
    api.list = listOf([task("r1", { state: "running", thread_id: "s1" })]);
    const terminal = { find: () => null, open: async () => null, render: () => null, close: () => {} };
    render(<IjHarnessView initialSession={null} terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />);
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "s1"));
    fireEvent.click(screen.getByTestId("harness-tonight"));
    expect(screen.getByTestId("harness-tonight")).toHaveAttribute("aria-current", "page");
    await screen.findByTestId("tonight-task-r1");
    expect(screen.queryByTestId("session-stub")).toBeNull();
    // The folder box offers the sessions' folders.
    fireEvent.click(screen.getByTestId("tonight-add"));
    expect(document.querySelector("#tonight-folders option")?.getAttribute("value")).toBe("C:\work\shop");
    fireEvent.click(screen.getByTestId("tonight-open-r1"));
    await waitFor(() => expect(screen.getByTestId("session-stub")).toHaveAttribute("data-id", "s1"));
    expect(screen.queryByTestId("tonight")).toBeNull();
  });
});
