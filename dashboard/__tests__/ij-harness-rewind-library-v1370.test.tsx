/**
 * v1.370.0 (IJ harness Phase 7) — the two pieces on the harness screen:
 *
 *  - REWIND: "Rewind to here" on a message. The FIRST press only reads what
 *    the rewind would do (`GET …/rewind?to=`) and says it in words — the
 *    files that go back, the commands that are NOT undone; the SECOND press
 *    rewinds (`POST …/rewind`). The daemon wrote the cut: the screen reads
 *    the session again and puts the message back in the box. A refusal —
 *    in the preview or from the POST — is LISTED in the daemon's words, file
 *    by file, and nothing is sent. Never while a reply runs.
 *  - THE LIBRARY: a suggested lesson or harness-file edit is a quiet card
 *    with Accept / Decline; Accept posts accept and says what it did; a
 *    refusal is said in the daemon's words; the library is read again a few
 *    times after a reply (the suggestion is made in the background).
 *  - THE BELL maps `library.proposal` to a quiet row that opens the session
 *    in Build, naming the folder, never its path.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    body?: unknown;
    constructor(message: string, status = 0, body?: unknown) {
      super(message);
      this.status = status;
      if (body !== undefined) this.body = body;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown | ((path: string) => unknown)>,
    calls: [] as { method: string; path: string; body?: unknown }[],
    postAnswers: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : api.postAnswers[`${method} ${path}`];
    const value = typeof hit === "function" ? (hit as (p: string) => unknown)(path) : hit;
    if (value instanceof Error) return Promise.reject(value);
    if (value === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(value ?? {});
  };
  return {
    ApiError: api.ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    flattenDetail: (d: unknown) => String(d),
    get: (p: string) => answer("GET", p),
    post: (p: string, b?: unknown) => answer("POST", p, b),
    put: (p: string, b?: unknown) => answer("PUT", p, b),
    patch: (p: string, b?: unknown) => answer("PATCH", p, b),
    del: (p: string) => answer("DELETE", p),
  };
});

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    checking: false,
    health: { default_provider: "mock", default_model: "mock-1", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import { RewindControl, REWIND_LABEL } from "@/components/harness/RewindControl";
import { LibraryCards } from "@/components/harness/LibraryCards";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { toActivity } from "@/components/NotificationBell";
import { LIBRARY_DUE_GRACE_MS, LIBRARY_REREAD_MS, LIBRARY_RUNNING_MS, nextLibraryRead } from "@/lib/buildLibrary";
import { rewindFileWords } from "@/lib/buildRewind";

const PREVIEW_PATH = "/build/sessions/s1/rewind?to=t1";
const REWIND_PATH = "POST /build/sessions/s1/rewind";

const preview = (over: Record<string, unknown> = {}) => ({
  to: "t1",
  question: "Add a cart",
  turns: ["t1", "t2"],
  files: [
    { path: "C:\\w\\cart.py", rel: "cart.py", change: "removed" },
    { path: "C:\\w\\app.py", rel: "app.py", change: "put back" },
  ],
  problems: [],
  commands: [{ tool: "shell", what: "python -m pytest" }],
  already_undone: 0,
  can_rewind: true,
  note: "Commands are not undone: `python -m pytest` ran in the rewound turns, and whatever they changed stays as it is.",
  detail: "",
  ...over,
});

beforeEach(() => {
  api.gets = {};
  api.calls = [];
  api.postAnswers = {};
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const posts = (path: string) => api.calls.filter((c) => c.method === "POST" && c.path === path);

/* ------------------------------------------------------------- rewind */

describe("Rewind to here: two presses, the refusal in words", () => {
  it("the first press only reads and says what would go back; the second rewinds", async () => {
    api.gets[PREVIEW_PATH] = preview();
    api.postAnswers[REWIND_PATH] = { rewound: true, question: "Add a cart", files: [], note: "" };
    const onRewound = vi.fn();
    render(<RewindControl threadId="s1" turnId="t1" onRewound={onRewound} />);
    fireEvent.click(screen.getByTestId("rewind-start"));
    const confirm = await screen.findByTestId("rewind-confirm");
    expect(posts("/build/sessions/s1/rewind")).toHaveLength(0);
    expect(screen.getByTestId("rewind-files")).toHaveTextContent("cart.py is removed (this session made it)");
    expect(screen.getByTestId("rewind-files")).toHaveTextContent("app.py goes back to how it was");
    expect(screen.getByTestId("rewind-commands")).toHaveTextContent("Commands are not undone");
    expect(confirm).toHaveTextContent("the message goes back in your box");
    fireEvent.click(screen.getByTestId("rewind-confirm-button"));
    await waitFor(() => expect(onRewound).toHaveBeenCalledWith("Add a cart"));
    expect(posts("/build/sessions/s1/rewind")).toEqual([
      { method: "POST", path: "/build/sessions/s1/rewind", body: { to: "t1" } },
    ]);
  });

  it("Cancel after the first press sends nothing", async () => {
    api.gets[PREVIEW_PATH] = preview();
    render(<RewindControl threadId="s1" turnId="t1" onRewound={vi.fn()} />);
    fireEvent.click(screen.getByTestId("rewind-start"));
    fireEvent.click(await screen.findByTestId("rewind-cancel"));
    expect(screen.queryByTestId("rewind-confirm")).toBeNull();
    expect(posts("/build/sessions/s1/rewind")).toHaveLength(0);
  });

  it("a preview that cannot rewind lists the refusal file by file and offers no rewind", async () => {
    api.gets[PREVIEW_PATH] = preview({
      can_rewind: false,
      problems: [{ path: "C:\\w\\app.py", reason: "app.py changed after this session changed it, so putting it back would overwrite the newer version" }],
      detail: "Nothing was rewound: app.py changed after this session changed it, so putting it back would overwrite the newer version.",
    });
    render(<RewindControl threadId="s1" turnId="t1" onRewound={vi.fn()} />);
    fireEvent.click(screen.getByTestId("rewind-start"));
    const refused = await screen.findByTestId("rewind-refused");
    expect(refused).toHaveTextContent("Nothing was rewound: app.py changed after this session changed it");
    expect(screen.getByTestId("rewind-problems")).toHaveTextContent("app.py changed after this session changed it");
    expect(screen.queryByTestId("rewind-confirm-button")).toBeNull();
    expect(posts("/build/sessions/s1/rewind")).toHaveLength(0);
  });

  it("a refusal from the rewind itself is listed in the daemon's words and the session is read again", async () => {
    api.gets[PREVIEW_PATH] = preview();
    api.postAnswers[REWIND_PATH] = new api.ApiError("Nothing was rewound: notes.md: the saved copy of notes.md is gone.", 409, {
      detail: "Nothing was rewound: the saved copy of notes.md is gone.",
      problems: [{ path: "C:\\w\\notes.md", reason: "the saved copy of notes.md is gone" }],
      rewound: false,
    });
    const onRewound = vi.fn();
    const onTouched = vi.fn();
    render(<RewindControl threadId="s1" turnId="t1" onRewound={onRewound} onTouched={onTouched} />);
    fireEvent.click(screen.getByTestId("rewind-start"));
    fireEvent.click(await screen.findByTestId("rewind-confirm-button"));
    const refused = await screen.findByTestId("rewind-refused");
    expect(refused).toHaveTextContent("Nothing was rewound: the saved copy of notes.md is gone.");
    expect(screen.getByTestId("rewind-problems")).toHaveTextContent("the saved copy of notes.md is gone");
    expect(onRewound).not.toHaveBeenCalled();
    expect(onTouched).toHaveBeenCalled();
  });

  it("a file the session made that is already gone is said so (v1.370.0 review)", () => {
    expect(rewindFileWords({ path: "C:/w/notes.md", rel: "notes.md", change: "already gone" }))
      .toBe("notes.md is already gone (this session made it)");
  });

  it("waits while a reply runs", () => {
    render(<RewindControl threadId="s1" turnId="t1" onRewound={vi.fn()} disabled />);
    expect(screen.getByTestId("rewind-start")).toBeDisabled();
  });
});

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function sessionView(messages: unknown[]) {
  return {
    id: "s1", title: "Add a cart", updated_at: "2026-10-22T10:00:00", running: false, waiting: false,
    archived: false, folder: "C:\\w", folder_name: "w", is_project: false, project_id: null, mode: "act",
    plan: null, setup: {}, messages, session_id: "build:s1", queued: [], turn_id: "",
  };
}

describe("on the session screen", () => {
  it("a message the daemon wrote can be rewound to; the session is read again and the message goes back in the box", async () => {
    const lines = [
      { role: "user", content: "Add a cart", turnId: "t1", at: "2026-10-22T10:00:00" },
      { role: "assistant", content: "Added it.", turnId: "t1", route: { provider: "mock", model: "m", reason: "mock" } },
    ];
    let reads = 0;
    api.gets["/build/sessions/s1"] = () => (reads++ === 0 ? sessionView(lines) : sessionView([]));
    api.gets["/build/accounts"] = new api.ApiError("no such route", 404);
    api.gets[PREVIEW_PATH] = preview();
    api.postAnswers[REWIND_PATH] = { rewound: true, question: "Add a cart", files: [], note: "" };
    render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    expect(await screen.findByText("Added it.")).toBeInTheDocument();
    const start = screen.getByTestId("rewind-start");
    expect(start).toHaveTextContent(REWIND_LABEL);
    fireEvent.click(start);
    fireEvent.click(await screen.findByTestId("rewind-confirm-button"));
    await waitFor(() => expect((screen.getByTestId("harness-input") as HTMLTextAreaElement).value).toBe("Add a cart"));
    await waitFor(() => expect(screen.queryByText("Added it.")).toBeNull());
    // The screen never writes the transcript itself.
    expect(api.calls.some((c) => c.method === "PUT" && c.path.startsWith("/chat/threads/s1") && !c.path.endsWith("/queue"))).toBe(false);
  });
});

/* ------------------------------------------------------------- library */

const lessonItem = {
  id: "lib_1", kind: "lesson", status: "proposed", text: "Tests run with `python -m pytest`.", turn_id: "t1",
};
const editItem = {
  id: "lib_2", kind: "layer_edit", status: "proposed", text: "Keep the test command.", file: "memory.md",
  path: ".ironjarvis/harness/memory.md", before: "", after: "Tests: `python -m pytest`.\n", new_file: true, turn_id: "t1",
};
const LIB = "/build/sessions/s1/library";

describe("the library's suggestions: Accept / Decline", () => {
  it("shows a lesson and an edit as quiet cards; Accept keeps the lesson and says so", async () => {
    let n = 0;
    api.gets[LIB] = () => ({
      folder_name: "w", lessons: [], edits: [], proposals: n++ === 0 ? [lessonItem, editItem] : [editItem],
    });
    render(<LibraryCards threadId="s1" refreshKey={0} />);
    const card = await screen.findByTestId("library-card-lib_1");
    expect(card).toHaveTextContent("A lesson for later sessions in w");
    expect(card).toHaveTextContent("python -m pytest");
    const edit = screen.getByTestId("library-card-lib_2");
    expect(edit).toHaveTextContent("A change to .ironjarvis/harness/memory.md (a new file)");
    expect(screen.getByTestId("library-edit-diff")).toHaveTextContent("+ Tests: `python -m pytest`.");
    expect(edit).toHaveTextContent("Nothing is written into the folder until you accept it.");
    fireEvent.click(card.querySelector("[data-testid=library-accept]") as HTMLElement);
    expect(await screen.findByTestId("library-note")).toHaveTextContent("Kept. Later sessions in w will read it.");
    expect(posts("/build/library/lib_1/accept")).toHaveLength(1);
    await waitFor(() => expect(screen.queryByTestId("library-card-lib_1")).toBeNull());
  });

  it("Decline posts decline; a refusal is said in the daemon's words", async () => {
    api.gets[LIB] = { folder_name: "w", lessons: [], edits: [], proposals: [editItem] };
    api.postAnswers["POST /build/library/lib_2/accept"] = new api.ApiError(
      "The file changed since this was suggested, so it was not written. Ask again after your next turn.", 409,
    );
    render(<LibraryCards threadId="s1" refreshKey={0} />);
    const edit = await screen.findByTestId("library-card-lib_2");
    fireEvent.click(edit.querySelector("[data-testid=library-accept]") as HTMLElement);
    expect(await screen.findByRole("alert")).toHaveTextContent("The file changed since this was suggested");
    fireEvent.click(edit.querySelector("[data-testid=library-decline]") as HTMLElement);
    await waitFor(() => expect(posts("/build/library/lib_2/decline")).toHaveLength(1));
  });

  it("a kept lesson is listed and can be forgotten with two presses", async () => {
    let n = 0;
    api.gets[LIB] = () => ({
      folder_name: "w", edits: [], proposals: [],
      lessons: n++ === 0 ? [{ ...lessonItem, status: "accepted" }] : [],
    });
    render(<LibraryCards threadId="s1" refreshKey={0} />);
    const row = await screen.findByTestId("library-kept-lib_1");
    expect(screen.getByTestId("library-kept")).toHaveTextContent("Lessons kept for this folder (1)");
    const forget = row.querySelector("button") as HTMLElement;
    fireEvent.click(forget);
    expect(api.calls.some((c) => c.method === "DELETE")).toBe(false);
    fireEvent.click(forget);
    await waitFor(() =>
      expect(api.calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual(["/build/library/lib_1"]),
    );
    await waitFor(() => expect(screen.queryByTestId("library-kept")).toBeNull());
  });

  it("an older Iron Jarvis (no library route) shows nothing", async () => {
    render(<LibraryCards threadId="s1" refreshKey={0} />);
    await waitFor(() => expect(api.calls.some((c) => c.path === LIB)).toBe(true));
    expect(screen.queryByTestId("library-cards")).toBeNull();
  });

  it("after a reply the library is read again at the set delays, and no more", async () => {
    vi.useFakeTimers();
    api.gets[LIB] = { folder_name: "w", lessons: [], edits: [], proposals: [] };
    const { rerender } = render(<LibraryCards threadId="s1" refreshKey={0} />);
    await act(async () => {});
    const reads = () => api.calls.filter((c) => c.path === LIB).length;
    expect(reads()).toBe(1);
    rerender(<LibraryCards threadId="s1" refreshKey={1} />);
    await act(async () => {});
    expect(reads()).toBe(2);
    for (const ms of LIBRARY_REREAD_MS) {
      await act(async () => {
        vi.advanceTimersByTime(ms - (LIBRARY_REREAD_MS[LIBRARY_REREAD_MS.indexOf(ms) - 1] ?? 0));
      });
    }
    expect(reads()).toBe(2 + LIBRARY_REREAD_MS.length);
    await act(async () => {
      vi.advanceTimersByTime(600_000);
    });
    expect(reads()).toBe(2 + LIBRARY_REREAD_MS.length);
  });

  // v1.370.0 review: the daemon reflects ONCE a session has been idle (one
  // request per idle period, never one per reply), and says when it is due.
  it("reads again only when the daemon says a suggestion is due, then while it is made", async () => {
    vi.useFakeTimers();
    const t0 = Date.now();
    let phase: "due" | "running" | "done" = "due";
    api.gets[LIB] = () => ({
      folder_name: "w", lessons: [], edits: [],
      proposals: phase === "done" ? [lessonItem] : [],
      reflection: phase === "due"
        ? { due_at: new Date(t0 + 600_000).toISOString(), running: false, turns: 1 }
        : { due_at: null, running: phase === "running", turns: 0 },
    });
    render(<LibraryCards threadId="s1" refreshKey={0} />);
    await act(async () => {});
    const reads = () => api.calls.filter((c) => c.path === LIB).length;
    expect(reads()).toBe(1);
    // Nothing until the idle wait ends (plus its grace).
    await act(async () => {
      vi.advanceTimersByTime(600_000);
    });
    expect(reads()).toBe(1);
    phase = "running";
    await act(async () => {
      vi.advanceTimersByTime(LIBRARY_DUE_GRACE_MS + 1);
    });
    expect(reads()).toBe(2);
    // While it is being made: one read per LIBRARY_RUNNING_MS.
    phase = "done";
    await act(async () => {
      vi.advanceTimersByTime(LIBRARY_RUNNING_MS);
    });
    expect(reads()).toBe(3);
    expect(screen.getByTestId("library-card-lib_1")).toBeInTheDocument();
    // Nothing due: no more reads, ever.
    await act(async () => {
      vi.advanceTimersByTime(3_600_000);
    });
    expect(reads()).toBe(3);
  });

  it("nextLibraryRead: running, due, nothing", () => {
    const base = { folderName: "w", lessons: [], proposals: [] };
    expect(nextLibraryRead(null)).toBeNull();
    expect(nextLibraryRead({ ...base, reflection: { dueAt: null, running: false } })).toBeNull();
    expect(nextLibraryRead({ ...base, reflection: { dueAt: null, running: true } })).toBe(LIBRARY_RUNNING_MS);
    const now = Date.parse("2026-10-22T10:00:00Z");
    expect(nextLibraryRead({ ...base, reflection: { dueAt: "2026-10-22T10:10:00Z", running: false } }, now))
      .toBe(600_000 + LIBRARY_DUE_GRACE_MS);
    // A due time already past reads soon, never in the past.
    expect(nextLibraryRead({ ...base, reflection: { dueAt: "2026-10-22T09:00:00Z", running: false } }, now))
      .toBe(1000);
  });
});

/* ------------------------------------------------------------- the bell */

describe("the bell", () => {
  it("maps library.proposal to a quiet row that opens the session in Build", () => {
    const row = toActivity({
      id: "e1", ts: "2026-10-22T10:00:00Z", type: "library.proposal", session_id: "build:s1",
      payload: { id: "lib_1", kind: "lesson", folder_name: "shop", thread_id: "s1", summary: "Tests run with pytest." },
    } as never);
    expect(row).not.toBeNull();
    expect(row!.href).toBe("/terminals?session=s1");
    expect(row!.title).toBe("A lesson is suggested for shop");
    expect(row!.body).toBe("Tests run with pytest.");
    expect((row as { attention?: boolean }).attention).toBeUndefined();
    const edit = toActivity({
      id: "e2", ts: "2026-10-22T10:00:00Z", type: "library.proposal", session_id: "build:s1",
      payload: { id: "lib_2", kind: "layer_edit", folder_name: "shop", thread_id: "s1", summary: "memory.md" },
    } as never);
    expect(edit!.title).toBe("A change to a harness file is suggested for shop");
  });
});
