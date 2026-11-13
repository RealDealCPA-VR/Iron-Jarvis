/**
 * v1.418.0 (C4.6, C4.8): Build › Code's history that stays, and keys and a
 * queue like the chat's.
 *
 *   - a view holding fewer steps than the record says "N earlier steps", and
 *     "Load earlier" puts the page before them on top (the first message
 *     comes back after one press);
 *   - Enter while it works QUEUES the message (POST …/queue — never a second
 *     turn, never a note); the list under the box has a × per message; a
 *     queue the daemon HELD (Stop, a failure, a limit card) comes back to the
 *     box; Shift+Enter still sends a note;
 *   - Esc stops a running reply (the Stop press) — unless a menu is open;
 *   - Shift+Tab steps the mode ask → edits → plan → auto → ask and never
 *     into Full access;
 *   - the `/` menu opens on `/` at the start of ANY line, up to 20 rows, with
 *     each command's argument hint;
 *   - the model chip stays open while Claude Code works (it switches live);
 *     the effort chip waits (a new start), with a title saying so; Pi's
 *     effort stays open.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
  patches: [] as { path: string; body: unknown }[],
  postReply: {} as Record<string, unknown>,
  postFail: {} as Record<string, { status: number; message: string; body?: unknown }>,
  streams: [] as { path: string; body: unknown }[],
  scripts: [] as { event: string; data: Record<string, unknown>; wait?: number }[][],
  lastWatch: null as null | { onFrame?: (event: string, data: Record<string, unknown>) => void },
  hold: null as Promise<void> | null,
  release: null as (() => void) | null,
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    body?: unknown;
    constructor(message: string, status = 0, body?: unknown) {
      super(message);
      this.status = status;
      this.body = body;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: FakeApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    get: (path: string) => {
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(typeof r === "function" ? (r as () => unknown)() : r);
    },
    post: (path: string, body?: unknown) => {
      H.posts.push({ path, body });
      const fail = H.postFail[path];
      if (fail) return Promise.reject(new FakeApiError(fail.message, fail.status, fail.body));
      const r = H.postReply[path];
      return typeof r === "function" ? (r as () => Promise<unknown>)() : Promise.resolve(r ?? {});
    },
    patch: (path: string, body?: unknown) => {
      H.patches.push({ path, body });
      return Promise.resolve(H.postReply[`PATCH ${path}`] ?? {});
    },
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* (
    path: string,
    body: unknown,
    signal?: AbortSignal,
    watch?: { onFrame?: (event: string, data: Record<string, unknown>) => void },
  ) {
    H.streams.push({ path, body });
    H.lastWatch = watch ?? null;
    const frames = H.scripts.shift() ?? [];
    for (const f of frames) {
      if (f.wait) await new Promise((r) => setTimeout(r, f.wait));
      if (signal?.aborted) return;
      watch?.onFrame?.(f.event, f.data);
      await Promise.resolve();
    }
    if (H.hold) await H.hold;
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import {
  SLASH_ROWS,
  commandHints,
  decodeEventsPage,
  decodeQueue,
  earlierStepsWords,
  effortWhileRunning,
  modelWhileRunning,
  nextMode,
  olderOf,
  pickCommand,
  slashAt,
  wordsBack,
} from "@/lib/buildAppHistory";
import { decodeCodingTools } from "@/lib/buildApp";
import { SESSION, chooseEffort, effortButtons, modelRowsShown } from "./helpers/modelPicker";

const MODES = [
  { id: "ask", label: "Ask first", dangerous: false },
  { id: "edits", label: "Auto edits", dangerous: false },
  { id: "plan", label: "Plan", dangerous: false },
  { id: "auto", label: "Auto", dangerous: false },
  { id: "full", label: "Full access", dangerous: true },
];

const CLAUDE = {
  id: "claude-code",
  label: "Claude Code",
  installed: true,
  signed_in: true,
  version: "2.1.311",
  modes: MODES,
  hidden_modes: [],
  models: [
    { id: "", label: "Claude Code's default" },
    { id: "claude-opus-5-5", label: "Opus 5.5" },
  ],
  efforts: ["low", "medium", "high"],
  accounts: [],
  commands: [],
};
const PI = { ...CLAUDE, id: "pi", label: "Pi", modes: [{ id: "own", label: "Its own rules", dangerous: false }], own_sign_in: true };

const sess = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  harness: "claude-code",
  folder: "C:\\work\\shop",
  title: `Session ${id}`,
  account: "",
  model: "claude-opus-5-5",
  effort: "high",
  mode: "ask",
  harness_session_id: "",
  created_at: "2026-10-30T09:00:00Z",
  updated_at: "2026-10-30T09:00:00Z",
  archived_at: null,
  last_error: "",
  ...over,
});

function seed(sessions: Record<string, unknown>[] = []) {
  H.gets = {
    "/build/app/harnesses": { harnesses: [CLAUDE, PI] },
    "/build/app/sessions": { sessions },
    "/editor/options": { editors: [], preferred: "" },
  };
}

function settled(id = "a1", over: Record<string, unknown> = {}, view: Record<string, unknown> = {}) {
  seed([sess(id, over)]);
  H.gets[`/build/app/sessions/${id}`] = { session: sess(id, over), events: [], running: false, pending: [], ...view };
}

function working(id = "a1", over: Record<string, unknown> = {}, view: Record<string, unknown> = {}) {
  const events = [{ type: "user", at: "x", text: "Count to thirty", turn: "turn_1" }];
  seed([sess(id, over)]);
  H.gets[`/build/app/sessions/${id}`] = { session: sess(id, over), events, running: "turn_1", pending: [], ...view };
  H.scripts = [events.map((e) => ({ event: String(e.type), data: e }))];
  H.hold = new Promise<void>((res) => {
    H.release = res;
  });
}

beforeEach(() => {
  H.posts = [];
  H.patches = [];
  H.postReply = {};
  H.postFail = {};
  H.streams = [];
  H.scripts = [];
  H.hold = null;
  H.release = null;
  H.lastWatch = null;
  seed();
});

afterEach(() => {
  H.release?.();
  cleanup();
});

/* ===================================================== the pure rules */

describe("the rules", () => {
  it("says how many steps are earlier, and decodes a page", () => {
    expect(earlierStepsWords(0)).toBe("");
    expect(earlierStepsWords(1)).toBe("1 earlier step");
    expect(earlierStepsWords(3152)).toBe("3,152 earlier steps");
    expect(olderOf({ older: "s12", events_total: 5152 }, 2000)).toEqual({ cursor: "s12", earlier: 3152 });
    expect(olderOf({ older: null, events_total: 4 }, 4)).toEqual({ cursor: null, earlier: 0 });
    const page = decodeEventsPage({ events: [{ type: "user", at: "x", text: "first" }, { type: "bogus" }], older: "i3:0", earlier: 3 });
    expect(page.events).toHaveLength(1);
    expect(page).toMatchObject({ older: "i3:0", earlier: 3 });
  });

  it("opens the `/` menu at the start of any line, where the caret is", () => {
    expect(slashAt("/rev")).toEqual({ start: 0, query: "rev" });
    expect(slashAt("fix this\n/rev")).toEqual({ start: 9, query: "rev" });
    expect(slashAt("see a/b")).toBeNull();
    expect(slashAt("fix /rev")).toBeNull();
    expect(slashAt("/rev\nmore words", 4)).toEqual({ start: 0, query: "rev" });
    expect(pickCommand("fix this\n/re", { start: 9, query: "re" }, "review")).toEqual({ text: "fix this\n/review ", caret: 17 });
    expect(SLASH_ROWS).toBe(20);
    expect(commandHints({ commands: [{ name: "/review", argument_hint: "<pr>" }, { name: "x" }] })).toEqual({ review: "<pr>" });
  });

  it("steps the mode through the ordinary ones and never into Full access", () => {
    const tool = decodeCodingTools({ harnesses: [CLAUDE] })[0];
    const seen: string[] = [];
    let m = "ask";
    for (let i = 0; i < 6; i++) {
      m = nextMode(tool, m)!;
      seen.push(m);
    }
    expect(seen).toEqual(["edits", "plan", "auto", "ask", "edits", "plan"]);
    expect(nextMode(tool, "full")).toBe("ask");
    expect(nextMode(decodeCodingTools({ harnesses: [PI] })[0], "own")).toBeNull();
  });

  it("holds only the chip changes that need a new start while a reply runs", () => {
    expect(modelWhileRunning("claude-code", "Claude Code", true)).toMatchObject({ held: false, defaultHeld: true });
    expect(modelWhileRunning("codex", "Codex", true)).toMatchObject({ held: false, defaultHeld: false });
    expect(effortWhileRunning("claude-code", "Claude Code", true)).toMatchObject({ held: true });
    expect(effortWhileRunning("claude-code", "Claude Code", true).title).toContain("starts again");
    expect(effortWhileRunning("pi", "Pi", true)).toMatchObject({ held: false, defaultHeld: true });
    expect(effortWhileRunning("claude-code", "Claude Code", false)).toMatchObject({ held: false, defaultHeld: false });
  });

  it("puts waiting words back after what is typed", () => {
    const q = decodeQueue({ queued: [{ id: "q1", text: "then deploy", files: [] }, { id: "", text: "x" }], queue_held: true })!;
    expect(q.queued.map((i) => i.id)).toEqual(["q1"]);
    expect(q.held).toBe(true);
    expect(wordsBack("", q.queued)).toBe("then deploy");
    expect(wordsBack("and this", q.queued)).toBe("and this\n\nthen deploy");
    expect(decodeQueue({})).toBeNull();
  });
});

/* ===================================================== history that stays */

describe("history that stays (C4.6)", () => {
  it("says how many steps are earlier and loads them on top with one press", async () => {
    settled("a1", {}, {
      events: [{ type: "text_done", at: "x", item: "m9", text: "The newest answer." }],
      events_total: 3,
      older: "s777",
    });
    H.gets["/build/app/sessions/a1/events?before=s777&limit=500"] = {
      events: [
        { type: "user", at: "x", text: "The very first message" },
        { type: "text_done", at: "x", item: "m1", text: "An early answer." },
      ],
      older: null,
      earlier: 0,
    };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    expect((await screen.findByTestId("code-earlier-count")).textContent).toBe("2 earlier steps");
    expect(screen.queryByText("The very first message")).toBeNull();
    fireEvent.click(screen.getByTestId("code-load-earlier"));
    expect(await screen.findByText("The very first message")).toBeInTheDocument();
    const transcript = screen.getByTestId("code-transcript").textContent ?? "";
    expect(transcript.indexOf("The very first message")).toBeLessThan(transcript.indexOf("The newest answer."));
    await waitFor(() => expect(screen.queryByTestId("code-earlier")).toBeNull());
  });

  it("a step cut for the screen reads its whole text from the record (GET …/events/{item})", async () => {
    settled("a1", {}, {
      events: [
        { type: "user", at: "x", text: "run it", turn: "t" },
        { type: "tool", at: "x", item: "t1", kind: "command", name: "Bash", title: "Run the tests", input: "pytest", status: "done", output: "short…", more: true, output_chars: 5000, turn: "t" },
        { type: "done", at: "x", stop: "completed", turn: "t" },
      ],
    });
    H.gets["/build/app/sessions/a1/events/t1"] = { item: "t1", input: "pytest", output: "THE WHOLE OUTPUT" };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const rows = await screen.findAllByTestId("work-row");
    fireEvent.click(rows[0]);
    fireEvent.click(await screen.findByTestId("code-tool-show-all"));
    expect(await screen.findByText(/THE WHOLE OUTPUT/)).toBeInTheDocument();
  });

  it("says nothing when every step is shown", async () => {
    settled("a1", {}, { events: [{ type: "user", at: "x", text: "only" }], events_total: 1, older: null });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByText("only");
    expect(screen.queryByTestId("code-earlier")).toBeNull();
  });
});

/* ===================================================== keys and a queue */

describe("a message while it works waits (C4.8)", () => {
  it("Enter queues it — never a second turn, never a note — and its × takes it off", async () => {
    working();
    H.postReply["/build/app/sessions/a1/queue"] = { ok: true, id: "q1", queued: [{ id: "q1", text: "then add a test", files: [] }], queue_held: false };
    H.postReply["/build/app/sessions/a1/queue/q1/remove"] = { queued: [], queue_held: false };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    const box = screen.getByTestId("code-box") as HTMLTextAreaElement;
    expect(box.getAttribute("placeholder")).toContain("Enter queues your message");
    fireEvent.change(box, { target: { value: "then add a test" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() =>
      expect(H.posts.find((p) => p.path === "/build/app/sessions/a1/queue")?.body).toEqual({ text: "then add a test" }),
    );
    expect(H.posts.some((p) => p.path.endsWith("/steer"))).toBe(false);
    expect(H.streams.filter((s) => s.path.endsWith("/turn"))).toHaveLength(0);
    expect(box.value).toBe("");
    const list = await screen.findByTestId("code-queued");
    expect(within(list).getByText("then add a test")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("code-queued-remove-q1"));
    await waitFor(() => expect(screen.queryByTestId("code-queued")).toBeNull());
    expect(H.posts.some((p) => p.path === "/build/app/sessions/a1/queue/q1/remove")).toBe(true);
  });

  it("Shift+Enter still sends a note to the running reply", async () => {
    working();
    H.postReply["/build/app/sessions/a1/steer"] = { ok: true, id: "n1", state: "sent" };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    const box = screen.getByTestId("code-box");
    fireEvent.change(box, { target: { value: "also this" } });
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    await waitFor(() => expect(H.posts.find((p) => p.path.endsWith("/steer"))?.body).toEqual({ text: "also this" }));
    expect(H.posts.some((p) => p.path.endsWith("/queue"))).toBe(false);
  });

  it("a queue the daemon held (the reply was stopped) comes back to the box, with words", async () => {
    settled("a1", {}, { queued: [{ id: "q1", text: "then deploy", files: [] }], queue_held: true });
    H.postReply["/build/app/sessions/a1/queue/take"] = { ok: true, taken: [{ id: "q1", text: "then deploy", files: [] }] };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = (await screen.findByTestId("code-box")) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe("then deploy"));
    expect(screen.getByTestId("code-info").textContent).toContain("back in your message box");
    expect(screen.queryByTestId("code-queued")).toBeNull();
  });

  it("when the reply ends and the daemon sends the waiting message, that new reply is followed — once", async () => {
    working();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    // The daemon ended turn_1 and started the waiting message as turn_2.
    H.gets["/build/app/sessions/a1"] = {
      session: sess("a1"),
      events: [
        { type: "user", at: "x", text: "Count to thirty", turn: "turn_1" },
        { type: "done", at: "x", stop: "completed", turn: "turn_1" },
        { type: "user", at: "y", text: "then add a test", turn: "turn_2" },
      ],
      running: "turn_2",
      pending: [],
      queued: [],
      queue_held: false,
    };
    H.scripts = [[{ event: "user", data: { type: "user", at: "y", text: "then add a test", turn: "turn_2" } }]];
    const hold = H.release;
    H.hold = new Promise<void>((res) => {
      H.release = res;
    });
    await act(async () => {
      hold?.();
      await new Promise((r) => setTimeout(r, 50));
    });
    await waitFor(() => expect(H.streams.filter((s) => s.path.endsWith("/turns/turn_2/stream"))).toHaveLength(1));
    expect(await screen.findByText("then add a test")).toBeInTheDocument();
  });

  it("a record that still names the turn that just ended is never followed in a loop", async () => {
    working();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    H.hold = null;
    await act(async () => {
      H.release?.();
      await new Promise((r) => setTimeout(r, 200));
    });
    expect(H.streams.filter((s) => s.path.endsWith("/turns/turn_1/stream")).length).toBeLessThanOrEqual(1);
  });

  it("no reply running after all (409 not_working): the words go as a message", async () => {
    working();
    H.postFail["/build/app/sessions/a1/queue"] = { status: 409, message: "not working", body: { code: "not_working" } };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    const box = screen.getByTestId("code-box");
    fireEvent.change(box, { target: { value: "next thing" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() =>
      expect(H.streams.find((s) => s.path === "/build/app/sessions/a1/turn")?.body).toMatchObject({ text: "next thing" }),
    );
  });
});

describe("keys (C4.8)", () => {
  it("Esc stops a running reply (the Stop press) — not while a menu is open", async () => {
    working("a1", {}, { commands: [{ name: "review", description: "Review" }] });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    const box = screen.getByTestId("code-box");
    fireEvent.change(box, { target: { value: "/rev" } });
    await screen.findByTestId("code-slash-menu");
    fireEvent.keyDown(box, { key: "Escape" });
    expect(H.posts.some((p) => p.path.endsWith("/interrupt"))).toBe(false);
    expect(screen.queryByTestId("code-slash-menu")).toBeNull();
    fireEvent.keyDown(box, { key: "Escape" });
    await waitFor(() => expect(H.posts.some((p) => p.path === "/build/app/sessions/a1/interrupt")).toBe(true));
  });

  it("Esc with nothing running stops nothing", async () => {
    settled();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box");
    fireEvent.keyDown(box, { key: "Escape" });
    await act(() => new Promise((r) => setTimeout(r, 20)));
    expect(H.posts.some((p) => p.path.endsWith("/interrupt"))).toBe(false);
  });

  it("Shift+Tab steps the mode ask → edits and never into Full access", async () => {
    settled("a1", { mode: "auto" });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box");
    fireEvent.keyDown(box, { key: "Tab", shiftKey: true });
    await waitFor(() => expect(H.patches.at(-1)?.body).toEqual({ mode: "ask" }));
    expect(H.patches.some((p) => (p.body as { mode?: string }).mode === "full")).toBe(false);
  });

  it("the `/` menu opens on a later line, lists up to 20 commands with their hints", async () => {
    const commands = Array.from({ length: 25 }, (_, i) => ({ name: `cmd${i}`, description: `Command ${i}`, ...(i === 0 ? { argument_hint: "<file>" } : {}) }));
    settled("a1", {}, { commands });
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = (await screen.findByTestId("code-box")) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "fix the login page\n/cmd" } });
    const menu = await screen.findByTestId("code-slash-menu");
    expect(within(menu).getAllByRole("option")).toHaveLength(20);
    expect(within(menu).getByTestId("code-command-hint").textContent).toBe("<file>");
    fireEvent.click(screen.getByTestId("code-command-cmd0"));
    expect(box.value).toBe("fix the login page\n/cmd0 ");
  });

  it("the model chip stays open while Claude Code works; the effort chip waits, and says why", async () => {
    working();
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    // v1.475.0 (moved on purpose): one chip; the model column stays open
    // except going back to the default; every effort row waits, and says why.
    expect((screen.getByTestId("code-model-chip") as HTMLButtonElement).disabled).toBe(false);
    const rows = modelRowsShown(SESSION);
    expect(rows[0].id).toBe("");
    expect(rows[0].disabled).toBe(true);
    expect(rows.slice(1).every((r) => !r.disabled)).toBe(true);
    const efforts = effortButtons(SESSION);
    expect(efforts.length).toBeGreaterThan(0);
    expect(efforts.every((b) => b.disabled)).toBe(true);
    expect(efforts[0].title).toContain("starts again");
  });

  it("v1.481.1: a model or effort change while it works is said once (the note goes when the line comes)", async () => {
    working("p1", { harness: "pi", mode: "own", model: "", effort: "high", harness_session_id: "h-1" });
    H.postReply["PATCH /build/app/sessions/p1"] = {
      session: sess("p1", { harness: "pi", mode: "own", model: "", effort: "low", harness_session_id: "h-1" }),
      applied: { effort: "next" },
    };
    render(<BuildCodeView initialSession="p1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    chooseEffort(SESSION, "low");
    // The note says it until the conversation says it.
    expect(await screen.findByTestId("code-info")).toBeInTheDocument();
    await act(async () => {
      H.lastWatch?.onFrame?.("notice", { type: "notice", at: "2026-11-12T20:00:09Z", text: "Effort changed: High → Low, from your next message.", turn: "turn_1" });
    });
    await waitFor(() => expect(screen.queryByTestId("code-info")).toBeNull());
    expect(screen.getAllByText(/Effort changed: High → Low/)).toHaveLength(1);
  });

  it("v1.481.1: a change pressed as the reply ends is still said (the note stays when no line comes)", async () => {
    // The reply ends while the change is out, so the daemon's line goes to
    // the record only; the page's note must not wait for a line that never
    // reaches the stream.
    // An earlier change's line is already drawn: only a NEW line ends the note.
    working("c1", { harness: "pi", mode: "own", model: "", harness_session_id: "h-1", effort: "high" }, {
      events: [
        { type: "notice", at: "2026-11-12T19:00:00Z", text: "Effort changed: Low → High.", turn: "turn_0" },
        { type: "user", at: "x", text: "Count to thirty", turn: "turn_1" },
      ],
    });
    expect(H.gets["/build/app/sessions/c1"]).toBeTruthy();
    let answer: (v: unknown) => void = () => {};
    H.postReply["PATCH /build/app/sessions/c1"] = new Promise((res) => {
      answer = res;
    });
    render(<BuildCodeView initialSession="c1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    chooseEffort(SESSION, "low");
    await waitFor(() => expect(H.patches).toHaveLength(1));
    H.gets["/build/app/sessions/c1"] = {
      session: sess("c1", { harness: "pi", mode: "own", model: "", harness_session_id: "h-1", effort: "low" }),
      events: [
        { type: "notice", at: "2026-11-12T19:00:00Z", text: "Effort changed: Low → High.", turn: "turn_0" },
        { type: "user", at: "2026-11-12T20:00:00Z", text: "Count to thirty", turn: "turn_1" },
      ],
      running: null,
      pending: [],
    };
    await act(async () => {
      H.release?.();
    });
    await waitFor(() => expect(screen.queryByTestId("code-working")).toBeNull());
    await act(async () => {
      answer({ session: sess("c1", { harness: "pi", mode: "own", model: "", harness_session_id: "h-1", effort: "low" }), applied: { effort: "next" } });
    });
    expect(await screen.findByTestId("code-info")).toBeInTheDocument();
    expect(screen.getByText("Effort changed: Low → High.")).toBeInTheDocument();
    await act(async () => {});
    expect(screen.getByTestId("code-info")).toBeInTheDocument();
  });

  it("v1.481.1: a change made while the reply streams reads nothing mid-stream, and nothing beside the reply's own last read", async () => {
    working("c2", { harness: "pi", mode: "own", model: "", harness_session_id: "h-1", effort: "high" });
    const view = H.gets["/build/app/sessions/c2"];
    let reads = 0;
    H.gets["/build/app/sessions/c2"] = () => {
      reads += 1;
      return view;
    };
    H.postReply["PATCH /build/app/sessions/c2"] = { session: sess("c2", { harness: "pi", mode: "own", model: "", harness_session_id: "h-1", effort: "low" }), applied: { effort: "next" } };
    render(<BuildCodeView initialSession="c2" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    const before = reads;
    chooseEffort(SESSION, "low");
    await waitFor(() => expect(H.patches).toHaveLength(1));
    await act(async () => {});
    await act(async () => {});
    expect(reads).toBe(before);
    // The reply ends: the turn's own read and the change's read follow.
    H.gets["/build/app/sessions/c2"] = () => {
      reads += 1;
      return { ...(view as Record<string, unknown>), running: null };
    };
    await act(async () => {
      H.release?.();
    });
    // The turn's own last read started after the change answered, so it
    // holds the line: one read, never a second one beside it.
    await waitFor(() => expect(reads).toBe(before + 1));
    await act(async () => {});
    expect(reads).toBe(before + 1);
  });

  const QUEUED = (id: string) => ({
    session: sess(id, { harness: "pi", mode: "own", model: "", effort: "low", harness_session_id: "h-1" }),
    events: [
      { type: "user", at: "2026-11-12T20:00:00Z", text: "Count to thirty", turn: "turn_1" },
      { type: "done", at: "2026-11-12T20:00:05Z", stop: "completed", turn: "turn_1" },
      { type: "user", at: "2026-11-12T20:00:06Z", text: "then add a test", turn: "turn_2" },
    ],
    running: "turn_2",
    pending: [],
    queued: [],
    queue_held: false,
  });

  it("v1.481.1: a change made during a reply never draws the next waiting message twice", async () => {
    working("q1", { harness: "pi", mode: "own", model: "", effort: "high", harness_session_id: "h-1" });
    H.postReply["PATCH /build/app/sessions/q1"] = { session: QUEUED("q1").session, applied: { effort: "now" } };
    render(<BuildCodeView initialSession="q1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    chooseEffort(SESSION, "low");
    await waitFor(() => expect(H.patches).toHaveLength(1));
    await act(async () => {});
    // The reply ends and the daemon starts the waiting message as turn_2.
    H.gets["/build/app/sessions/q1"] = () => new Promise((r) => setTimeout(() => r(QUEUED("q1")), 15));
    H.scripts = [[{ event: "user", data: { type: "user", at: "2026-11-12T20:00:06Z", text: "then add a test", turn: "turn_2" } }]];
    const rel = H.release;
    H.hold = new Promise<void>((res) => {
      H.release = res;
    });
    rel?.();
    await new Promise((r) => setTimeout(r, 300));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100));
    });
    expect(screen.getAllByText("then add a test")).toHaveLength(1);
  });

  it("v1.481.1: a change answered as the reply ends makes no read beside the reply's own (the next message is drawn once)", async () => {
    // The change answers AFTER the turn's last read started. A second read
    // landing during turn_2's follow used to put its saved question back
    // beside the replay's own.
    working("q2", { harness: "pi", mode: "own", model: "", effort: "high", harness_session_id: "h-1" });
    let answer: (v: unknown) => void = () => {};
    H.postReply["PATCH /build/app/sessions/q2"] = new Promise((res) => {
      answer = res;
    });
    render(<BuildCodeView initialSession="q2" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    chooseEffort(SESSION, "low");
    await waitFor(() => expect(H.patches).toHaveLength(1));
    let reads = 0;
    H.gets["/build/app/sessions/q2"] = () => {
      reads += 1;
      const wait = reads === 1 ? 40 : 80;
      return new Promise((r) => setTimeout(() => r(QUEUED("q2")), wait));
    };
    // turn_2's replay brings its question only after the second read landed.
    H.scripts = [[{ event: "user", wait: 120, data: { type: "user", at: "2026-11-12T20:00:06Z", text: "then add a test", turn: "turn_2" } }]];
    const rel = H.release;
    H.hold = new Promise<void>((res) => {
      H.release = res;
    });
    rel?.();
    await new Promise((r) => setTimeout(r, 5));
    answer({ session: QUEUED("q2").session, applied: { effort: "now" } });
    await new Promise((r) => setTimeout(r, 300));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100));
    });
    expect(reads).toBe(1);
    expect(screen.getAllByText("then add a test")).toHaveLength(1);
  });

  it("v1.481.1: a note left waiting for a line that never came does not later erase another message", async () => {
    working("c1", { harness: "pi", mode: "own", model: "", harness_session_id: "h-1", effort: "high" }, {
      events: [{ type: "user", at: "2026-11-12T20:00:00Z", text: "Count to thirty", turn: "turn_1" }],
    });
    let answer: (v: unknown) => void = () => {};
    H.postReply["PATCH /build/app/sessions/c1"] = new Promise((res) => {
      answer = res;
    });
    render(<BuildCodeView initialSession="c1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    chooseEffort(SESSION, "low");
    await waitFor(() => expect(H.patches).toHaveLength(1));
    const S1 = sess("c1", { harness: "pi", mode: "own", model: "", harness_session_id: "h-1", effort: "low" });
    H.gets["/build/app/sessions/c1"] = {
      session: S1,
      events: [
        { type: "user", at: "2026-11-12T20:00:00Z", text: "Count to thirty", turn: "turn_1" },
        { type: "done", at: "2026-11-12T20:00:05Z", stop: "completed", turn: "turn_1" },
      ],
      running: null,
      pending: [],
    };
    await act(async () => {
      H.release?.();
    });
    await waitFor(() => expect(screen.queryByTestId("code-working")).toBeNull());
    await act(async () => {
      answer({ session: S1, applied: { effort: "next" } });
    });
    expect(await screen.findByTestId("code-info")).toBeInTheDocument();
    // turn_2: the user sends a message.
    H.scripts = [[{ event: "user", data: { type: "user", at: "2026-11-12T20:00:10Z", text: "go on", turn: "turn_2" } }]];
    H.hold = new Promise<void>((res) => {
      H.release = res;
    });
    H.gets["/build/app/sessions/c1"] = { session: S1, events: [], running: "turn_2", pending: [] };
    const box = screen.getByTestId("code-box");
    fireEvent.change(box, { target: { value: "go on" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await screen.findByTestId("code-working");
    // A note that comes straight back.
    H.postReply["/build/app/sessions/c1/steer"] = { ok: true, id: "n2", state: "returned" };
    fireEvent.change(box, { target: { value: "also" } });
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    await waitFor(() => expect(screen.getByTestId("code-info").textContent).toContain("did not read this note"));
    // turn_2 ends; the saved record now holds the earlier change's line.
    H.gets["/build/app/sessions/c1"] = {
      session: S1,
      events: [
        { type: "user", at: "2026-11-12T20:00:00Z", text: "Count to thirty", turn: "turn_1" },
        { type: "notice", at: "2026-11-12T20:00:06Z", text: "Effort changed: High → Low, from your next message.", turn: "turn_1" },
        { type: "done", at: "2026-11-12T20:00:05Z", stop: "completed", turn: "turn_1" },
        { type: "user", at: "2026-11-12T20:00:10Z", text: "go on", turn: "turn_2" },
        { type: "text_done", at: "2026-11-12T20:00:11Z", text: "ok", turn: "turn_2" },
        { type: "done", at: "2026-11-12T20:00:12Z", stop: "completed", turn: "turn_2" },
      ],
      running: null,
      pending: [],
    };
    await act(async () => {
      H.release?.();
    });
    await waitFor(() => expect(screen.queryByTestId("code-working")).toBeNull());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    // On master this note stays (nothing clears it).
    expect(screen.queryByTestId("code-info")?.textContent ?? "").toContain("did not read this note");
  });

  it("v1.481.1: a change line and a returned note drawn together keep the returned-note message", async () => {
    working("p9", { harness: "pi", mode: "own", model: "", effort: "high", harness_session_id: "h-1" });
    H.postReply["/build/app/sessions/p9/steer"] = { ok: true, id: "n1", state: "sent" };
    H.postReply["PATCH /build/app/sessions/p9"] = {
      session: sess("p9", { harness: "pi", mode: "own", model: "", effort: "low", harness_session_id: "h-1" }),
      applied: { effort: "next" },
    };
    render(<BuildCodeView initialSession="p9" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    const box = screen.getByTestId("code-box");
    fireEvent.change(box, { target: { value: "also this" } });
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    await waitFor(() => expect(H.posts.some((p) => p.path.endsWith("/steer"))).toBe(true));
    await act(async () => {});
    chooseEffort(SESSION, "low");
    expect(await screen.findByTestId("code-info")).toBeInTheDocument();
    await act(async () => {
      H.lastWatch?.onFrame?.("notice", { type: "notice", at: "2026-11-12T20:00:09Z", text: "Effort changed: High → Low, from your next message.", turn: "turn_1" });
      H.lastWatch?.onFrame?.("steer", { type: "steer", id: "n1", state: "returned", text: "also this", turn: "turn_1" });
    });
    await act(async () => { await new Promise((r) => setTimeout(r, 80)); });
    expect((screen.getByTestId("code-box") as HTMLTextAreaElement).value).toContain("also this");
    expect(screen.queryByTestId("code-info")?.textContent ?? "").toMatch(/back|returned|box|read/i);
  });

  it("Pi's effort stays open while it works (it changes live)", async () => {
    working("p1", { harness: "pi", mode: "own", model: "", effort: "high" });
    render(<BuildCodeView initialSession="p1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-working");
    // The levels change live; only going back to the default waits (as the
    // old select's first option did).
    const levels = effortButtons(SESSION).filter((b) => b.dataset.testid !== "code-pick-effort-default");
    expect(levels.length).toBeGreaterThan(0);
    expect(levels.some((b) => b.disabled)).toBe(false);
  });
});
