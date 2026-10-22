/**
 * The IJ harness view's pure halves (v1.369.0, IJ harness plan Phase 6):
 * lib/buildSessions (the session decoders, the plan, the model's questions,
 * the preview's address rule, the steer notes kept by this window, Build's
 * saved view) and lib/buildAccounts (Phase 5's real shapes: the account
 * list, the session's account, the limit card's words).
 *
 * The words here are the screen's: a test that pins a sentence pins what the
 * user reads.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, get: vi.fn(), post: vi.fn(), patch: vi.fn(), del: vi.fn() };
});

import * as api from "@/lib/api";
import {
  addressesIn,
  decodeAskResolved,
  decodeAskUser,
  decodeFolders,
  decodeSessionView,
  isPreviewAddress,
  planFromArgs,
  planFromMessages,
  readSentSteers,
  shownMessages,
  unreadSteersFor,
  viewFromSaved,
  withSteersRestored,
  writeSentSteers,
  type BuildMessage,
} from "@/lib/buildSessions";
import {
  accountStatusLine,
  decodeAccountLimit,
  decodeAccounts,
  fetchAccounts,
  limitCardLine,
  limitCardWords,
  sessionAccount,
  vendorPlanLine,
  windowWords,
} from "@/lib/buildAccounts";

afterEach(() => {
  vi.mocked(api.get).mockReset();
  localStorage.clear();
});

/* ------------------------------------------------------------------ sessions */

describe("the session list and one session", () => {
  it("groups by folder in the daemon's order, with a display name and each session's state", () => {
    const folders = decodeFolders({
      folders: [
        {
          path: "C:\\work\\site",
          name: "Site",
          is_project: true,
          project_id: "p1",
          sessions: [
            { id: "a", title: "Fix the nav", updated_at: "2026-10-20T10:00:00Z", running: true },
            { id: "", title: "no id" },
            { id: "b", title: "", waiting: true },
          ],
        },
        { path: "", name: "no path" },
        { path: "C:\\tmp", sessions: [] },
      ],
    });
    expect(folders.map((f) => f.path)).toEqual(["C:\\work\\site", "C:\\tmp"]);
    expect(folders[0]).toMatchObject({ name: "Site", isProject: true, projectId: "p1" });
    expect(folders[0].sessions.map((s) => [s.id, s.title, s.running, s.waiting])).toEqual([
      ["a", "Fix the nav", true, false],
      ["b", "New session", false, true],
    ]);
    // A folder with no name of its own is named by its path.
    expect(folders[1].name).toBe("C:\\tmp");
    expect(decodeFolders(null)).toEqual([]);
  });

  it("decodes a view, keeping only the line keys the daemon writes, and its own session id", () => {
    const v = decodeSessionView({
      id: "s1",
      title: "T",
      folder: "C:\\w",
      mode: "plan",
      setup: { provider: "claude-cli", model: "opus", local_only: true, granted_tools: ["shell", 3], account: "work", account_name: "Work" },
      messages: [
        { role: "user", content: "hi", turnId: "t1", secret: "x" },
        { role: "system", content: "never shown" },
        { role: "assistant", content: "ok", turnId: "t1", accountLimit: { account: "work" } },
      ],
      queued: [{ text: "next" }, { text: "  " }],
      turn_id: "t9",
    })!;
    expect(v.mode).toBe("plan");
    expect(v.folderName).toBe("C:\\w");
    expect(v.sessionId).toBe("build:s1");
    expect(v.setup).toMatchObject({ provider: "claude-cli", localOnly: true, grantedTools: ["shell"], account: "work", accountName: "Work" });
    expect(v.messages).toHaveLength(2);
    expect(v.messages[0]).not.toHaveProperty("secret");
    expect(v.messages[1].accountLimit).toEqual({ account: "work" });
    expect(v.queued).toEqual([{ text: "next" }]);
    expect(v.turnId).toBe("t9");
    // An older daemon (no account fields at all) is told apart from "no account".
    expect(v.knowsAccounts).toBe(false);
    expect(decodeSessionView({ id: "s1", account: null })!.knowsAccounts).toBe(true);
  });

  it("the daemon's own continuation question is never shown", () => {
    const msgs = [
      { role: "user", content: "Build it" },
      { role: "user", content: "Carry on.", continuation: true },
      { role: "assistant", content: "Done." },
    ] as BuildMessage[];
    expect(shownMessages(msgs).map((m) => m.content)).toEqual(["Build it", "Done."]);
  });
});

/* ------------------------------------------------------------------ the plan */

describe("the plan pane's steps", () => {
  it("reads plan_update's `plan` and Claude Code's `todos`, in the checklist's states", () => {
    expect(planFromArgs({ plan: [{ step: "a", status: "completed" }, { step: "b", status: "in_progress" }, { step: "c" }] })).toEqual({
      steps: [
        { index: 0, goal: "a", state: "done" },
        { index: 1, goal: "b", state: "running" },
        { index: 2, goal: "c", state: "waiting" },
      ],
      total: 3,
    });
    expect(planFromArgs({ todos: [{ content: "x", status: "pending" }] })!.steps[0]).toEqual({ index: 0, goal: "x", state: "waiting" });
    expect(planFromArgs({ plan: "not a list" })).toBeNull();
    expect(planFromArgs({ plan: [] })).toBeNull();
  });

  it("a saved reply's plan is read from its step's WHOLE input, never guessed from a cut one", () => {
    const whole = { name: "plan_update", ok: true, detail: { input: JSON.stringify({ plan: [{ step: "ship", status: "pending" }] }) } };
    const cut = { name: "plan_update", ok: true, detail: { input: '{"plan": [{"step": "sh' } };
    const failed = { name: "plan_update", ok: false, detail: { input: JSON.stringify({ plan: [{ step: "no" }] }) } };
    const msg = (steps: unknown[]) => ({ role: "assistant", content: "", steps }) as unknown as BuildMessage;
    expect(planFromMessages([msg([whole])])!.steps[0].goal).toBe("ship");
    expect(planFromMessages([msg([cut])])).toBeNull();
    expect(planFromMessages([msg([failed])])).toBeNull();
    // The newest plan wins.
    expect(planFromMessages([msg([whole]), msg([cut])])!.steps[0].goal).toBe("ship");
  });
});

/* ------------------------------------------------------- the model's question */

describe("the model's own question (ask_user)", () => {
  it("decodes one to four questions, choices kept, an empty question dropped", () => {
    const ask = decodeAskUser({
      id: "ask_1",
      call_id: "c1",
      questions: [
        { id: "db", question: "Which database?", header: "Storage", options: [{ label: "SQLite" }, { label: "" }, { label: "Postgres", description: "a server" }] },
        { question: "  " },
        { question: "Anything else?", multi: true },
        { question: "4" },
        { question: "5" },
        { question: "6 (over the cap)" },
      ],
    })!;
    expect(ask.callId).toBe("c1");
    expect(ask.questions.map((q) => q.question)).toEqual(["Which database?", "Anything else?", "4"]);
    expect(ask.questions[0].options).toEqual([
      { label: "SQLite", description: "" },
      { label: "Postgres", description: "a server" },
    ]);
    expect(ask.questions[1]).toMatchObject({ id: "q3", multi: true });
    expect(decodeAskUser({ id: "x", questions: [] })).toBeNull();
  });

  it("its end is read only from an ask_user resolution", () => {
    expect(decodeAskResolved({ id: "a", kind: "ask_user", outcome: "answered" })).toEqual({ id: "a", outcome: "answered" });
    expect(decodeAskResolved({ id: "a", kind: "ask_user", outcome: "weird" })).toEqual({ id: "a", outcome: "stopped" });
    expect(decodeAskResolved({ id: "a", kind: "elicitation", outcome: "answered" })).toBeNull();
  });
});

/* --------------------------------------------------------------- the preview */

describe("the browser preview opens only a page on this PC or the user's network", () => {
  it.each([
    ["http://localhost:5173/", true],
    ["http://127.0.0.1:3000", true],
    ["http://192.168.1.20:8080", true],
    ["http://10.0.0.5", true],
    ["http://172.20.1.1", true],
    ["http://100.100.1.1", true],
    ["http://myapp.local", true],
    ["http://devbox", true],
    ["https://box.tail1234.ts.net", true],
    ["https://example.com", false],
    ["http://8.8.8.8", false],
    ["http://172.32.0.1", false],
    ["file:///C:/x.html", false],
    ["ftp://localhost/build.zip", false],
    ["ws://127.0.0.1:3000", false],
    ["javascript:alert(1)", false],
    ["not a url", false],
  ])("%s → %s", (url, ok) => {
    expect(isPreviewAddress(url)).toBe(ok);
  });

  it("finds the addresses the session's own output printed, newest first, 0.0.0.0 as localhost", () => {
    const msgs = [
      { role: "assistant", content: "Server on http://localhost:3000/." },
      {
        role: "assistant",
        content: "",
        steps: [{ detail: { output: "  Local: http://0.0.0.0:5173/\n  see https://example.com too" } }],
      },
    ] as unknown as BuildMessage[];
    expect(addressesIn(msgs)).toEqual(["http://localhost:5173/", "http://localhost:3000/"]);
  });
});

/* ---------------------------------------------------------- steer notes kept */

describe("a steer note the turn never read", () => {
  it("is the note sent to THAT turn and not in its transcript; the done frame's unread count too", () => {
    const sent = [
      { turnId: "t1", text: "use tabs" },
      { turnId: "t1", text: "and dark mode" },
      { turnId: "t0", text: "old turn" },
    ];
    const msgs = [{ role: "user", content: "use tabs", steer: true, turnId: "t1" }] as BuildMessage[];
    expect(unreadSteersFor(sent, "t1", msgs)).toEqual(["and dark mode"]);
    expect(unreadSteersFor(sent, "t1", msgs, ["one more"])).toEqual(["and dark mode", "one more"]);
  });

  it("goes back under what the user has typed, once", () => {
    expect(withSteersRestored("", ["a"])).toBe("a");
    expect(withSteersRestored("typing", ["a", "b"])).toBe("typing\n\na\n\nb");
    expect(withSteersRestored("a", ["a"])).toBe("a");
  });

  it("is kept per session in this window's storage", () => {
    writeSentSteers("s1", [{ turnId: "t1", text: "x" }]);
    expect(readSentSteers("s1")).toEqual([{ turnId: "t1", text: "x" }]);
    writeSentSteers("s1", []);
    expect(localStorage.getItem("ij.build.steers.s1")).toBeNull();
  });
});

describe("Build's saved view", () => {
  it("the retired canvas opens the IJ harness; anything else the Terminals rail", () => {
    expect(viewFromSaved("harness")).toBe("harness");
    expect(viewFromSaved("canvas")).toBe("harness");
    expect(viewFromSaved("rail")).toBe("rail");
    expect(viewFromSaved(null)).toBe("rail");
    expect(viewFromSaved("junk")).toBe("rail");
  });
});

/* ------------------------------------------------------------------ accounts */

const LIST = {
  claude: [
    {
      id: "work", name: "Work", vendor: "claude", vendor_label: "Claude Code", provider: "claude-cli",
      default: true, plan: "max", plan_label: "Max", status: "usage",
      usage: { percent: 38.4, window: "7 days", window_mins: 10080, resets_at: "2030-01-01T16:12:00Z" },
    },
    { id: "home", name: "Home", vendor: "claude", vendor_label: "Claude Code", default: false, plan_label: "Pro", status: "limit", resets_at: "2030-01-01T16:12:00Z", limit_kind: "usage" },
    { id: "cx-in-wrong-group", name: "Codex", vendor: "codex", status: "ready" },
  ],
  codex: [{ id: "cx", name: "Codex Plus", vendor: "codex", vendor_label: "Codex", plan_label: "Plus", status: "needs_sign_in" }],
  iron_proxy: { enabled: true, running: true, needs_update: true, note: "Iron-Proxy needs an update" },
};

describe("the account list (GET /build/accounts)", () => {
  it("decodes the two groups and Iron-Proxy's note; a row in the wrong group is dropped", () => {
    const v = decodeAccounts(LIST)!;
    expect(v.groups.claude.map((a) => a.id)).toEqual(["work", "home"]);
    expect(v.groups.codex.map((a) => a.id)).toEqual(["cx"]);
    expect(v.ironProxy).toEqual({ enabled: true, running: true, needsUpdate: true, note: "Iron-Proxy needs an update" });
    expect(v.groups.claude[0]).toMatchObject({ isDefault: true, vendorLabel: "Claude Code", planLabel: "Max" });
    expect(decodeAccounts({ nothing: true })).toBeNull();
  });

  it("each card says ONE status line", () => {
    const v = decodeAccounts(LIST)!;
    const now = new Date("2030-01-01T09:00:00");
    expect(accountStatusLine(v.groups.claude[0], now)).toMatchObject({ kind: "usage", text: "38% · 7 days", percent: 38.4 });
    expect(accountStatusLine(v.groups.claude[1], now).text).toMatch(/^Limit reached · resets /);
    expect(accountStatusLine(v.groups.codex[0], now)).toEqual({ kind: "sign_in", text: "Needs to sign in again" });
    expect(vendorPlanLine(v.groups.claude[0])).toBe("Claude Code · Max");
    expect(vendorPlanLine({ ...v.groups.codex[0], planLabel: "" })).toBe("Codex");
    expect(windowWords("", 1440)).toBe("1 day");
    expect(windowWords("", 300)).toBe("5 hours");
  });

  it("the session's account is the list's row (its live status) when listed, else the view's own", () => {
    const v = decodeAccounts(LIST)!;
    expect(sessionAccount({ id: "home", name: "Home" }, "claude", v)!.status).toBe("limit");
    const gone = sessionAccount({ id: "old", name: "Old", status: "missing" }, "claude", v)!;
    expect(gone).toMatchObject({ id: "old", status: "missing" });
    expect(sessionAccount(null, "claude", v)).toBeNull();
  });

  it("an Iron Jarvis without the account list (404) hides the account pieces; other failures throw", async () => {
    vi.mocked(api.get).mockRejectedValueOnce(new api.ApiError("Not Found", 404));
    expect(await fetchAccounts()).toBeNull();
    vi.mocked(api.get).mockRejectedValueOnce(new api.ApiError("boom", 500));
    await expect(fetchAccounts()).rejects.toThrow("boom");
    vi.mocked(api.get).mockResolvedValueOnce(LIST);
    expect((await fetchAccounts())!.groups.claude).toHaveLength(2);
  });
});

describe("the limit card's words", () => {
  const base = { account: "work", account_name: "Work", provider: "claude-cli", vendor: "claude", resets_at: "2030-01-01T16:12:00Z", turn_id: "t" };
  const card = (over: Record<string, unknown>) => limitCardWords(decodeAccountLimit({ ...base, ...over })!);

  it("a usage limit: the mockup's sentence, a reset time, Resume when ready", () => {
    const w = card({ kind: "usage", resumable: true });
    expect(`${w.title} ${w.body}`).toBe(
      "This account reached its limit. Wait for it to reset, or choose another account. Your conversation and work are preserved.",
    );
    expect(w).toMatchObject({ showsReset: true, canResume: true });
    expect(limitCardLine(decodeAccountLimit({ ...base, kind: "usage" })!, new Date("2030-01-01T09:00:00"))).toMatch(/^Work · resets /);
  });

  it("sign in, removed and turned off are not resets: no reset time, no Resume unless resumable", () => {
    for (const [kind, title] of [
      ["signin", "This account needs to sign in again."],
      ["missing", "The account this session used was removed."],
      ["disabled", "This account is turned off."],
    ] as const) {
      const w = card({ kind });
      expect(w.title).toBe(title);
      expect(w.showsReset).toBe(false);
      expect(w.canResume).toBe(false);
      expect(w.body).toContain("Your conversation and work are preserved.");
      expect(card({ kind, resumable: true }).canResume).toBe(true);
      expect(limitCardLine(decodeAccountLimit({ ...base, kind })!)).toBe("Work");
    }
  });

  it("a reset the daemon says it cannot resume offers no Resume", () => {
    expect(card({ kind: "rate", resumable: false }).canResume).toBe(false);
    expect(card({ kind: "busy" }).title).toBe("This account is busy right now.");
  });

  it("junk is not a card", () => {
    expect(decodeAccountLimit(null)).toBeNull();
    expect(decodeAccountLimit({ kind: "usage" })).toBeNull();
    expect(decodeAccountLimit({ account: "a", kind: "nonsense" })!.kind).toBe("unknown");
  });
});
