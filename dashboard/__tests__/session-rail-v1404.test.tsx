/**
 * v1.404.0 — the SESSION tab of a coding session's right panes
 * (components/harness/HarnessSessionRail, lib/sessionRail). The user, beside a
 * screenshot of Claude Code's own session panel: "add a sessions rail to the
 * coding sessions, next to the other icons shown, and have it display when
 * selected information like the last attachment".
 *
 *  - a seventh icon, "Session", after Files; chosen, it is remembered;
 *  - the LAST ATTACHMENT: the newest message that carried a picture, a file,
 *    an "@" file or a "/" skill — the question being sent included — with its
 *    words and when; earlier uploads in a fold, each once; a note sent while
 *    it worked and the app's own "carry on" never count;
 *  - where it works (folder + Open, branch, Local only, the instruction files
 *    the newest reply read), what answers (model, account, approvals, plan
 *    mode, team), how full (the daemon's reading, never a guess), changes (a
 *    count, and a press to the Changes tab);
 *  - background: the helpers working now and the programs the session
 *    started (GET …/programs), each with its state in words and a Stop for a
 *    running one (POST …/stop); read again every few seconds ONLY while one
 *    runs or the session works; an older Iron Jarvis (404) says nothing runs.
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
    gets: {} as Record<string, unknown>,
    posts: [] as [string, unknown][],
    calls: [] as string[],
    stopAnswer: null as unknown,
    stopFail: "",
  };
});
vi.mock("@/lib/api", () => ({
  ApiError: api.ApiError,
  API_BASE: "http://d",
  ijToken: () => "",
  get: (p: string) => {
    api.calls.push(p);
    if (!(p in api.gets)) return Promise.reject(new api.ApiError(`unmocked ${p}`, 404));
    const v = api.gets[p];
    return v instanceof api.ApiError ? Promise.reject(v) : Promise.resolve(v);
  },
  post: (p: string, b?: unknown) => {
    api.posts.push([p, b]);
    if (p === "/chat/changes") return Promise.resolve(api.gets["POST /chat/changes"] ?? { changes: [] });
    if (p.endsWith("/stop")) return api.stopFail ? Promise.reject(new api.ApiError(api.stopFail, 500)) : Promise.resolve(api.stopAnswer);
    return Promise.resolve({});
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: () => <div data-testid="files-panel" />,
}));

import { HarnessPanes, PANE_TABS, type SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { decodeSessionView, type BuildMessage, type BuildSessionView } from "@/lib/buildSessions";
import {
  decodeProgram,
  decodePrograms,
  durationWords,
  earlierAttachments,
  lastAttachment,
  programWords,
  branchWords,
} from "@/lib/sessionRail";
import type { BuildAccount } from "@/lib/buildAccounts";

const FOLDER = "C:\\work\\shop";
const PROGRAMS = "/build/sessions/s1/programs";

function view(over: Record<string, unknown> = {}): BuildSessionView {
  const v = decodeSessionView({
    id: "s1",
    title: "Cart",
    folder: FOLDER,
    folder_name: "shop",
    session_id: "build:s1",
    messages: [{ role: "user", content: "Add a cart", at: "2026-10-22T10:00:00" }],
    ...over,
  });
  if (!v) throw new Error("bad fixture");
  return v;
}

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => null,
  close: () => {},
};

function renderRail(v: BuildSessionView, extra: Partial<React.ComponentProps<typeof HarnessPanes>> = {}) {
  const r = render(
    <HarnessPanes
      view={v}
      livePlan={null}
      terminal={terminal}
      refreshKey={0}
      undoFor={() => null}
      onUndo={async () => {}}
      modelWords="Opus 5.5"
      {...extra}
    />,
  );
  fireEvent.click(screen.getByTestId("harness-pane-tab-session"));
  return r;
}

const ACCOUNT: BuildAccount = {
  id: "a1",
  name: "Work",
  isDefault: true,
  vendor: "claude",
  vendorLabel: "Claude Code",
  planLabel: "Max",
  status: "usage",
  resetsAt: null,
  limitKind: "",
  usage: { percent: 63, window: "5-hour window", resetsAt: null },
};

beforeEach(() => {
  api.gets = { [PROGRAMS]: { programs: [] } };
  api.posts = [];
  api.calls = [];
  api.stopAnswer = null;
  api.stopFail = "";
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("the Session tab", () => {
  it("is a seventh icon after Files, named Session, and chosen it is remembered", () => {
    expect(PANE_TABS.map((t) => t.value)).toEqual(["plan", "timeline", "terminal", "changes", "browser", "files", "session"]);
    renderRail(view());
    const tab = screen.getByRole("tab", { name: "Session" });
    expect(tab.getAttribute("aria-selected")).toBe("true");
    expect(tab.querySelector("svg")).toBeTruthy();
    expect(screen.getByTestId("pane-session")).toBeTruthy();
    expect(localStorage.getItem("ij.build.pane")).toBe("session");
  });

  it("is not mounted — and reads nothing — until it is chosen", () => {
    render(
      <HarnessPanes view={view()} livePlan={null} terminal={terminal} refreshKey={0} undoFor={() => null} onUndo={async () => {}} />,
    );
    expect(screen.queryByTestId("pane-session")).toBeNull();
    expect(api.calls).not.toContain(PROGRAMS);
  });
});

describe("the last attachment", () => {
  const MESSAGES = [
    { role: "user", content: "Use this spec", at: "2026-10-22T09:00:00", attachmentPaths: ["C:\\up\\spec.pdf", "C:\\up\\old.png"] },
    { role: "assistant", content: "Read it." },
    { role: "user", content: "Again with the spec", at: "2026-10-22T09:30:00", attachmentPaths: ["C:\\up\\spec.pdf"] },
    { role: "assistant", content: "Done." },
    {
      role: "user",
      content: "Match this mock-up\nand keep the header",
      at: "2026-10-22T10:00:00",
      attachmentPaths: ["C:\\up\\cart.png", "C:\\up\\notes.txt"],
      filePaths: ["src/app.py"],
      skill: "review",
    },
    { role: "assistant", content: "On it." },
    // Neither of these is the user's own message with things on it.
    { role: "user", content: "also the footer", steer: true, attachmentPaths: ["C:\\up\\steer.png"] },
    { role: "user", content: "carry on", continuation: true, attachmentPaths: ["C:\\up\\carry.png"] },
  ];

  it("shows the newest message's pictures, files, @ files and skill, with its words; earlier uploads fold", () => {
    renderRail(view({ messages: MESSAGES }));
    const sec = screen.getByTestId("session-rail-attachment");
    const thumb = within(sec).getByTestId("session-rail-attachment-thumb") as HTMLImageElement;
    expect(thumb.alt).toBe("cart.png");
    expect(within(sec).getByTestId("session-rail-attachment-file").textContent).toContain("notes.txt");
    expect(within(sec).getByTestId("session-rail-attachment-pick").textContent).toContain("app.py");
    expect(within(sec).getByTestId("session-rail-attachment-skill").textContent).toContain("review");
    expect(within(sec).getByTestId("session-rail-attachment-when").textContent).toContain("“Match this mock-up”");
    expect(sec.textContent).not.toContain("steer.png");
    expect(sec.textContent).not.toContain("carry.png");
    const earlier = within(sec).getByTestId("session-rail-earlier");
    // spec.pdf was sent twice: listed once.
    expect(earlier.querySelector("summary")?.textContent).toBe("Earlier uploads (2)");
    expect(earlier.textContent).toContain("spec.pdf");
    expect(earlier.textContent).toContain("old.png");
  });

  it("counts the question being sent: it is the last attachment at once", () => {
    const sending = { role: "user", content: "", at: new Date().toISOString(), attachmentPaths: ["C:\\up\\just-now.png"] };
    renderRail(view({ messages: MESSAGES }), {
      messages: [...(view({ messages: MESSAGES }).messages as BuildMessage[]), sending as BuildMessage],
    });
    const sec = screen.getByTestId("session-rail-attachment");
    expect((within(sec).getByTestId("session-rail-attachment-thumb") as HTMLImageElement).alt).toBe("just-now.png");
    expect(within(sec).getByTestId("session-rail-attachment-when").textContent).toBe("Sent just now with no words");
  });

  it("says so when nothing was attached", () => {
    renderRail(view());
    expect(screen.getByTestId("session-rail-attachment-none").textContent).toContain("Nothing attached yet");
  });

  it("opens a file, and a picture, in its own app (never an .svg)", async () => {
    renderRail(
      view({
        messages: [
          { role: "user", content: "x", attachmentPaths: ["C:\\up\\cart.png", "C:\\up\\notes.txt", "C:\\up\\logo.svg"] },
        ],
      }),
    );
    const sec = screen.getByTestId("session-rail-attachment");
    const [notes, svgFile] = within(sec).getAllByTestId("session-rail-attachment-file");
    expect(svgFile.textContent).toContain("logo.svg");
    expect(within(svgFile).queryByRole("button", { name: "Open" })).toBeNull();
    fireEvent.click(within(notes).getByRole("button", { name: "Open" }));
    const thumbs = within(sec).getAllByTestId("session-rail-attachment-thumb");
    fireEvent.click(thumbs[0].closest("button") as HTMLButtonElement);
    await waitFor(() =>
      expect(api.posts.filter(([p]) => p === "/documents/open").map(([, b]) => (b as { path: string }).path)).toEqual([
        "C:\\up\\notes.txt",
        "C:\\up\\cart.png",
      ]),
    );
  });
});

describe("where it works and what answers", () => {
  it("names the folder (Open), the branch, Local only and the instruction files the newest reply read", async () => {
    renderRail(
      view({
        setup: { local_only: true },
        git: { branch: "ij/cart-3a1c", base: "main" },
        messages: [
          { role: "user", content: "Add a cart" },
          { role: "assistant", content: "Done.", folderRules: ["CLAUDE.md", "AGENTS.md"] },
        ],
      }),
    );
    expect(screen.getByTestId("session-rail-folder").textContent).toBe("shop");
    expect(screen.getByTestId("session-rail-branch").textContent).toBe("ij/cart-3a1c from main");
    expect(screen.getByTestId("session-rail-local-only")).toBeTruthy();
    expect(screen.getByTestId("session-rail-rules").textContent).toBe("Follows CLAUDE.md and AGENTS.md");
    fireEvent.click(screen.getByTestId("session-rail-open-folder"));
    await waitFor(() => expect(api.posts).toContainEqual(["/documents/open", { path: FOLDER }]));
  });

  it("a session with no branch says it works in the folder; no Local only line when it is off", () => {
    renderRail(view());
    expect(screen.getByTestId("session-rail-branch").textContent).toContain("Works in the folder directly");
    expect(screen.queryByTestId("session-rail-local-only")).toBeNull();
    expect(screen.queryByTestId("session-rail-rules")).toBeNull();
  });

  it("names the model, the account and its usage, the approval level, plan mode and the team", () => {
    renderRail(
      view({
        mode: "plan",
        setup: { approval_mode: "always_ask", reasoning: "high" },
        team: {
          lead: { provider: "claude-cli", model: "opus", label: "Opus 5.5" },
          workers: [{ name: "glm", provider: "custom", model: "glm-5.3-flash", label: "glm-5.3-flash", state: "ready" }],
          reviewer: { name: "codex", provider: "codex-cli", model: "gpt-6", label: "Codex", state: "ready" },
        },
      }),
      { account: ACCOUNT },
    );
    const sec = screen.getByTestId("session-rail-model");
    expect(sec.textContent).toContain("Opus 5.5");
    expect(sec.textContent).toContain("reasoning high");
    expect(screen.getByTestId("session-rail-account").textContent).toBe("Account Work · Max · 63% · 5-hour window");
    expect(screen.getByTestId("session-rail-approvals").textContent).toBe("Ask first · plans first, for you to approve");
    const team = screen.getByTestId("session-rail-team").textContent ?? "";
    expect(team).toContain("Team lead: Opus 5.5");
    expect(team).toContain("Workers: glm-5.3-flash");
    expect(team).toContain("Reviewer: Codex");
  });
});

describe("how full the conversation is", () => {
  it("reads the newest reply's reading, the daemon's own numbers", () => {
    renderRail(
      view({
        messages: [
          { role: "user", content: "a" },
          { role: "assistant", content: "b", context: { window: 1_000_000, used: 300_000, percent: 38, level: "ok" } },
        ],
      }),
    );
    expect(screen.getByTestId("session-rail-context-percent").textContent).toBe("38% used");
    expect(screen.getByTestId("session-rail-context-window").textContent).toBe("of 1M tokens");
    expect((screen.getByTestId("session-rail-context-bar") as HTMLElement).style.width).toBe("38%");
  });

  it("past the offer mark the reading takes the warning tone", () => {
    renderRail(
      view({
        messages: [
          { role: "user", content: "a" },
          { role: "assistant", content: "b", context: { window: 200_000, percent: 81, level: "suggest" } },
        ],
      }),
    );
    expect(screen.getByTestId("session-rail-context-percent").className).toContain("text-tone-warn");
    expect(screen.getByTestId("session-rail-context-bar").className).toContain("bg-tone-warn");
  });

  it("before any reply it says when it is measured, never a guess", () => {
    renderRail(view());
    expect(screen.getByTestId("session-rail-context-none").textContent).toBe("Measured after the first reply.");
    expect(screen.queryByTestId("session-rail-context-percent")).toBeNull();
  });
});

describe("changes", () => {
  it("counts what the session changed and opens the Changes tab", async () => {
    api.gets["POST /chat/changes"] = {
      changes: [
        { path: "a.py", status: "modified" },
        { path: "b.py", status: "created" },
      ],
    };
    renderRail(view());
    await waitFor(() =>
      expect(screen.getByTestId("session-rail-changes-count").textContent).toBe("2 files changed in this session"),
    );
    expect(api.posts.find(([p]) => p === "/chat/changes")?.[1]).toMatchObject({ session_id: "build:s1" });
    fireEvent.click(screen.getByTestId("session-rail-open-changes"));
    expect(screen.getByTestId("harness-pane-tab-changes").getAttribute("aria-selected")).toBe("true");
  });
});

describe("background", () => {
  const RUNNING = { id: "p2", command: "npm run dev", running: true, stopped: false, exit_code: null, ran_s: 185, started_s: 185 };
  const ENDED = { id: "p1", command: "pytest -q", running: false, stopped: false, exit_code: 1, ran_s: 12, started_s: 400 };

  it("lists the session's programs with their state in words, and Stop ends a running one", async () => {
    api.gets[PROGRAMS] = { programs: [RUNNING, ENDED] };
    api.stopAnswer = { program: { ...RUNNING, running: false, stopped: true, ran_s: 186 } };
    renderRail(view());
    const rows = await screen.findAllByTestId("session-rail-program");
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("npm run devRunning · 3 min"),
      expect.stringContaining("pytest -qEnded (exit 1) after 12 s"),
    ]);
    // Only a running program has a Stop.
    expect(within(rows[1]).queryByTestId("session-rail-program-stop")).toBeNull();
    fireEvent.click(within(rows[0]).getByTestId("session-rail-program-stop"));
    await waitFor(() => expect(screen.getAllByTestId("session-rail-program")[0].textContent).toContain("Stopped after 3 min"));
    expect(api.posts.map(([p]) => p)).toContain("/build/sessions/s1/programs/p2/stop");
    expect(screen.queryAllByTestId("session-rail-program-stop")).toHaveLength(0);
  });

  it("names the helpers working now", async () => {
    const helpers = [
      { id: "c1", session: "", agent: "researcher", task: "", state: "running", steps: 0, since: null, ended: null, result: "", note: "", phase: "", phaseDetail: "", ask: null, tools: [] },
      { id: "c2", session: "", agent: "builder", task: "", state: "done", steps: 0, since: null, ended: null, result: "", note: "", phase: "", phaseDetail: "", ask: null, tools: [] },
    ];
    renderRail(view(), { helpers: helpers as never, busy: true });
    const list = await screen.findByTestId("session-rail-helpers");
    expect(list.textContent).toContain("working");
    expect(list.querySelectorAll("li")).toHaveLength(1);
  });

  it("says nothing runs when nothing does — and an older Iron Jarvis (404) is not an error", async () => {
    delete api.gets[PROGRAMS];
    renderRail(view());
    expect(await screen.findByTestId("session-rail-background-none")).toBeTruthy();
    expect(screen.getByTestId("session-rail-background").querySelector('[role="alert"]')).toBeNull();
  });

  it("reads again every few seconds only while a program runs or the session works", async () => {
    vi.useFakeTimers();
    api.gets[PROGRAMS] = { programs: [ENDED] };
    renderRail(view());
    await act(async () => {
      await Promise.resolve();
    });
    const reads = () => api.calls.filter((c) => c === PROGRAMS).length;
    const first = reads();
    expect(first).toBe(1);
    await act(async () => {
      vi.advanceTimersByTime(20_000);
    });
    expect(reads()).toBe(first);
    cleanup();

    api.calls = [];
    api.gets[PROGRAMS] = { programs: [RUNNING] };
    renderRail(view());
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      vi.advanceTimersByTime(5_000);
    });
    await act(async () => {
      vi.advanceTimersByTime(5_000);
    });
    expect(reads()).toBe(3);
  });
});

describe("lib/sessionRail", () => {
  it("lastAttachment skips replies, notes sent while it worked and the app's own carry-on", () => {
    expect(lastAttachment([{ role: "assistant", content: "x", attachmentPaths: ["a.png"] } as never])).toBeNull();
    expect(lastAttachment([{ role: "user", content: "x", steer: true, attachmentPaths: ["a.png"] }])).toBeNull();
    expect(lastAttachment([{ role: "user", content: "x", continuation: true, attachmentPaths: ["a.png"] }])).toBeNull();
    expect(lastAttachment([{ role: "user", content: "x", attachmentPaths: [] }])).toBeNull();
    // Review: a "/" skill alone is not an attachment; beside a file it rides along.
    expect(lastAttachment([{ role: "user", content: "y", skill: "review" }])).toBeNull();
    expect(lastAttachment([{ role: "user", content: "y", skill: "review", filePaths: ["a.py"] }])).toMatchObject({
      skill: "review",
      picks: ["a.py"],
    });
    const long = "w".repeat(300);
    expect(lastAttachment([{ role: "user", content: long, filePaths: ["a.py"] }])?.words).toHaveLength(120);
  });

  it("earlierAttachments lists each upload once, newest first, never the last message's", () => {
    const msgs = [
      { role: "user", content: "1", attachmentPaths: ["a", "b"] },
      { role: "user", content: "2", attachmentPaths: ["c", "a"] },
      { role: "user", content: "3", attachmentPaths: ["b"] },
    ];
    expect(earlierAttachments(msgs)).toEqual({ paths: ["c", "a"], total: 2 });
    expect(earlierAttachments(msgs, 1)).toEqual({ paths: ["c"], total: 2 });
    expect(earlierAttachments([])).toEqual({ paths: [], total: 0 });
  });

  it("decodes programs strictly and says their state in words", () => {
    expect(decodePrograms({ programs: [null, { command: "x" }, { id: "p1", running: true, ran_s: 61 }], others: 2 })).toMatchObject({
      programs: [{ id: "p1" }],
      others: 2,
    });
    expect(decodePrograms("junk")).toEqual({ programs: [], others: 0 });
    const left = decodeProgram({ id: "p1", running: true, left_running: true, exit_code: 0, ran_s: 125 });
    expect(left && programWords(left)).toBe("Its command ended; what it started still runs · 2 min");
    // A program that is not running cannot be "left running".
    expect(decodeProgram({ id: "p1", running: false, left_running: true })?.leftRunning).toBe(false);
    const stopped = decodeProgram({ id: "p1", running: false, stopped: true, exit_code: 0, ran_s: 4 });
    expect(stopped && programWords(stopped)).toBe("Stopped after 4 s");
    expect(stopped?.exitCode).toBeNull();
    // Running wins: a row that says both is running, never "stopped".
    expect(decodeProgram({ id: "p1", running: true, stopped: true, ran_s: 4 })).toMatchObject({ running: true, stopped: false });
    const ended = decodeProgram({ id: "p1", running: false, stopped: false, ran_s: 3700 });
    expect(ended && programWords(ended)).toBe("Ended after 1 h 1 min");
    expect(durationWords(7200)).toBe("2 h");
  });

  it("branchWords says what became of a branch", () => {
    const g = { branch: "ij/x", base: "main", merged: false, mergedInto: "", removed: false };
    expect(branchWords({ ...g, merged: true, mergedInto: "main" })).toBe("ij/x from main (merged into main)");
    expect(branchWords({ ...g, removed: true })).toContain("its folder was removed");
  });
});

describe("the session screen hands the tab what it shows", () => {
  it("passes the SHOWN conversation (the question being sent included), the chip's model words, the helpers and busy", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const src = readFileSync(join(process.cwd(), "components/harness/HarnessSession.tsx"), "utf8").replace(/\r\n/g, "\n");
    const at = src.indexOf("<HarnessPanes");
    expect(at).toBeGreaterThan(0);
    const props = src.slice(at, src.indexOf("/>", at));
    expect(props).toContain("messages={shown}");
    expect(props).toContain("modelWords={modelPart.text}");
    expect(props).toContain("helpers={helpers}");
    expect(props).toContain("busy={busy}");
  });
});


describe("the review's fixes (v1.404.0)", () => {
  it("a '/' skill sent after an upload does not bury the upload", () => {
    renderRail(
      view({
        messages: [
          { role: "user", content: "Read the spec", attachmentPaths: ["C:\\up\\spec.pdf"] },
          { role: "assistant", content: "Read." },
          { role: "user", content: "fix it", skill: "review" },
        ],
      }),
    );
    const sec = screen.getByTestId("session-rail-attachment");
    expect(within(sec).getByTestId("session-rail-attachment-file").textContent).toContain("spec.pdf");
    expect(within(sec).queryByTestId("session-rail-attachment-skill")).toBeNull();
  });

  it("a read that fails (not a 404) says so, never 'nothing is running'", async () => {
    api.gets[PROGRAMS] = new api.ApiError("Iron Jarvis isn't responding", 0);
    renderRail(view());
    expect((await screen.findByTestId("session-rail-background-error")).textContent).toContain("could not be read");
    expect(screen.queryByTestId("session-rail-background-none")).toBeNull();
  });

  it("a Stop that fails keeps its reason, even after the read that follows", async () => {
    api.gets[PROGRAMS] = {
      programs: [{ id: "p2", command: "npm run dev", running: true, stopped: false, exit_code: null, ran_s: 5 }],
    };
    api.stopFail = "the program could not be stopped";
    renderRail(view());
    fireEvent.click(await screen.findByTestId("session-rail-program-stop"));
    expect((await screen.findByTestId("session-rail-stop-error")).textContent).toContain("the program could not be stopped");
    // The read after the failed Stop has landed (two reads), and the reason stays.
    await waitFor(() => expect(api.calls.filter((c) => c === PROGRAMS).length).toBe(2));
    expect(screen.getByTestId("session-rail-stop-error")).toBeTruthy();
  });

  it("a command that ended but left its program running shows it running, with Stop", async () => {
    api.gets[PROGRAMS] = {
      programs: [{ id: "p3", command: 'start /b "" node server.js', running: true, left_running: true, stopped: false, exit_code: 0, ran_s: 61 }],
      others: 1,
    };
    renderRail(view());
    const row = await screen.findByTestId("session-rail-program");
    expect(row.textContent).toContain("Its command ended; what it started still runs · 1 min");
    expect(within(row).getByTestId("session-rail-program-stop")).toBeTruthy();
    expect(screen.getByTestId("session-rail-others").textContent).toBe(
      "1 command left a program running. They are stopped when the session is archived or deleted, or when you quit Iron Jarvis.",
    );
    expect(screen.queryByTestId("session-rail-background-none")).toBeNull();
  });

  it("reads again every few seconds while the session works, even with nothing listed", async () => {
    vi.useFakeTimers();
    renderRail(view(), { busy: true });
    await act(async () => {
      await Promise.resolve();
    });
    const reads = () => api.calls.filter((c) => c === PROGRAMS).length;
    expect(reads()).toBe(1);
    await act(async () => {
      vi.advanceTimersByTime(5_000);
    });
    expect(reads()).toBe(2);
  });

  it("the changed-files count follows a reply that works", async () => {
    vi.useFakeTimers();
    renderRail(view(), { busy: true });
    await act(async () => {
      await Promise.resolve();
    });
    const reads = () => api.posts.filter(([p]) => p === "/chat/changes").length;
    expect(reads()).toBe(1);
    await act(async () => {
      vi.advanceTimersByTime(15_000);
    });
    expect(reads()).toBe(2);
  });

  it("an idle session's changes are not read again on a clock", async () => {
    vi.useFakeTimers();
    renderRail(view());
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(api.posts.filter(([p]) => p === "/chat/changes").length).toBe(1);
  });

  it("a summary made after the newest reading hides that reading", () => {
    renderRail(
      view({
        compaction: { summary: "s", through_turn: "t1", covers: 4, at: "2026-10-22T12:00:00" },
        messages: [
          { role: "user", content: "a" },
          { role: "assistant", content: "b", at: "2026-10-22T11:00:00", context: { window: 200_000, percent: 92, level: "suggest" } },
        ],
      }),
    );
    expect(screen.getByTestId("session-rail-summarized")).toBeTruthy();
    expect(screen.queryByTestId("session-rail-context-percent")).toBeNull();
  });

  it("the warning tone follows the meter: none while compaction is turned off", () => {
    renderRail(
      view({
        messages: [
          { role: "user", content: "a" },
          { role: "assistant", content: "b", context: { window: 200_000, percent: 85, level: "suggest", disabled: true } },
        ],
      }),
    );
    expect(screen.getByTestId("session-rail-context-percent").className).not.toContain("text-tone-warn");
  });

  it("says Local only in the app's own promise: nothing leaves without asking", () => {
    renderRail(view({ setup: { local_only: true } }));
    expect(screen.getByTestId("session-rail-local-only").textContent).toBe(
      "Local only: nothing in this session leaves this PC without asking you.",
    );
  });

  it("'See changes' opens what this session changed, even on a live branch; the tab itself opens the branch", () => {
    renderRail(view({ git: { branch: "ij/cart-3a1c", base: "main" } }));
    fireEvent.click(screen.getByTestId("session-rail-open-changes"));
    expect(screen.getByTestId("changes-scope-session").getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByTestId("harness-pane-tab-session"));
    fireEvent.click(screen.getByTestId("harness-pane-tab-changes"));
    expect(screen.getByTestId("changes-scope-branch").getAttribute("aria-pressed")).toBe("true");
  });

  it("'Sent just now' turns into minutes while the session sits idle", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-28T12:00:00Z"));
    renderRail(
      view({ messages: [{ role: "user", content: "x", at: "2026-10-28T12:00:00Z", attachmentPaths: ["C:\\up\\a.png"] }] }),
    );
    expect(screen.getByTestId("session-rail-attachment-when").textContent).toBe("Sent just now with “x”");
    await act(async () => {
      vi.advanceTimersByTime(2 * 60_000 + 1_000);
    });
    expect(screen.getByTestId("session-rail-attachment-when").textContent).toBe("Sent 2m ago with “x”");
  });
});

describe("programs a command left behind", () => {
  it("keep the list read again while they run, though nothing is listed", async () => {
    vi.useFakeTimers();
    api.gets[PROGRAMS] = { programs: [], others: 1 };
    renderRail(view());
    await act(async () => {
      await Promise.resolve();
    });
    const reads = () => api.calls.filter((c) => c === PROGRAMS).length;
    expect(reads()).toBe(1);
    await act(async () => {
      vi.advanceTimersByTime(5_000);
    });
    expect(reads()).toBe(2);
  });
});


describe("a new session's first reply", () => {
  it("counts the changes from the question being sent (the view has no lines yet)", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-28T12:00:00Z"));
    const sending = { role: "user", content: "Add a cart", at: "2026-10-28T12:00:00Z" } as BuildMessage;
    renderRail(view({ messages: [] }), { messages: [sending], busy: true });
    await act(async () => {
      await Promise.resolve();
    });
    const asks = () => api.posts.filter(([p]) => p === "/chat/changes");
    expect(asks()).toHaveLength(1);
    expect(asks()[0][1]).toMatchObject({ since: "2026-10-28T12:00:00Z", session_id: "build:s1" });
  });
});
