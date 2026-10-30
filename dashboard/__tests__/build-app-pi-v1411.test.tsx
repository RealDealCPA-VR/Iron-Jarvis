/**
 * v1.411.0 — Pi in Build's Code view: the user's REAL Pi (`pi --mode rpc`),
 * its own tools and extensions on; Iron Jarvis turns every question Pi or an
 * extension asks into a card. Driven through the real view and lib/buildApp;
 * only the network (lib/api), the stream reader and the folder tree are
 * stand-ins.
 *
 * The events are THE CONTRACT's, written by the REAL daemon parser from
 * recorded Pi 1.3.0 output (tests/test_build_app_pi_v1411.py,
 * IJ_REGEN_BUILD_APP_PI=1): no new event kind, so the view draws Pi with the
 * pieces it already has. Pi's own words: one mode ("Pi's own rules", the other
 * four said with their reason), its own sign-in (no account to choose), and an
 * extension's confirm asked every time (Enter never allows it).
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
  postReply: {} as Record<string, unknown>,
  streams: [] as { path: string; body: unknown }[],
  scripts: [] as { event: string; data: Record<string, unknown> }[][],
  hold: null as Promise<void> | null,
  release: null as (() => void) | null,
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
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(typeof r === "function" ? (r as () => unknown)() : r);
    },
    post: (path: string, body?: unknown) => {
      H.posts.push({ path, body });
      return Promise.resolve(H.postReply[path] ?? {});
    },
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  // Every frame in its OWN macrotask (v1.409.3: same-tick delivery let React
  // fold a doubled question away and hid it).
  streamSSE: async function* (
    path: string,
    body: unknown,
    signal?: AbortSignal,
    watch?: { onFrame?: (event: string, data: Record<string, unknown>) => void },
  ) {
    H.streams.push({ path, body });
    const frames = H.scripts.shift() ?? [];
    for (const f of frames) {
      if (signal?.aborted) return;
      watch?.onFrame?.(f.event, f.data);
      await new Promise((r) => setTimeout(r, 5));
    }
    if (H.hold) await H.hold;
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: ({ onSelect }: { onSelect: (p: string) => void }) => (
    <button type="button" data-testid="stub-pick-folder" onClick={() => onSelect("C:\\work\\shop")}>
      pick
    </button>
  ),
}));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import {
  OWN_SIGN_IN_TITLE,
  accountWords,
  decodeCodeEvent,
  decodeCodingTools,
  enterAllowsCodeAsk,
  foldConversation,
  isFullAccess,
  modeLabel,
  sessionRowLine,
  toolLabel,
  type CodeEvent,
} from "@/lib/buildApp";
import { codeViewOfferFor } from "@/lib/buildCodeOffer";

/* ---- the contract fixture (the REAL daemon's words for Pi) -------------- */

const REAL = join(process.cwd(), "__tests__", "fixtures", "build-app-events-v1411.json");
const SC = (JSON.parse(readFileSync(REAL, "utf8")) as { scenarios: Record<string, Record<string, unknown>[]> }).scenarios;

const decodeAll = (evs: Record<string, unknown>[]) => evs.map((e) => decodeCodeEvent(e)).filter(Boolean) as CodeEvent[];

/* ---- the daemon ---------------------------------------------------------- */

const PI_TOOL = {
  id: "pi",
  label: "Pi",
  installed: true,
  signed_in: null,
  version: null,
  own_sign_in: true,
  modes: [{ id: "own", label: "Pi's own rules", dangerous: false }],
  hidden_modes: [
    { id: "ask", label: "Ask first", reason: "Pi does not ask before it runs a tool. Only an extension of yours can ask, and its question comes here as a card." },
    { id: "edits", label: "Auto edits", reason: "Pi has no setting that lets edits run while commands still ask." },
    { id: "plan", label: "Plan", reason: "Pi's plan mode comes from an extension that switches itself off when you run the plan, so this menu could not stay in step with it. If you have it, type /plan instead." },
    { id: "full", label: "Full access", reason: "Pi already runs every tool without asking (Pi's own rules)." },
  ],
  models: [
    { id: "", label: "Pi's default" },
    { id: "openai-codex/gpt-6-luna", label: "GPT-6 Luna (openai-codex)" },
  ],
  efforts: ["off", "minimal", "low", "medium", "high", "xhigh", "max"],
  accounts: [],
  commands: [{ name: "plan", description: "Toggle plan mode (read-only exploration)" }],
};
const CLAUDE_TOOL = {
  id: "claude-code",
  label: "Claude Code",
  installed: true,
  signed_in: true,
  own_sign_in: false,
  modes: [{ id: "ask", label: "Ask first", dangerous: false }],
  hidden_modes: [],
  models: [],
  efforts: [],
  accounts: [{ id: "prof_work", name: "Work", status: "ready" }],
  commands: [],
};
const TOOLS = { harnesses: [CLAUDE_TOOL, PI_TOOL] };

const sess = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  harness: "pi",
  folder: "C:\\work\\shop",
  title: `Session ${id}`,
  account: "",
  model: "",
  effort: "",
  mode: "own",
  harness_session_id: "01a1b2c3-1111-7222-8333-444444444444",
  created_at: "2026-10-30T09:00:00Z",
  updated_at: "2026-10-30T09:00:00Z",
  archived_at: null,
  last_error: "",
  ...over,
});

function seed(sessions: Record<string, unknown>[] = []) {
  H.gets = {
    "/build/app/harnesses": TOOLS,
    "/build/app/sessions": { sessions },
    "/editor/options": { editors: [], preferred: "" },
  };
}

const frames = (events: Record<string, unknown>[]) => events.map((e) => ({ event: String(e.type), data: e }));

/** A Pi session parked on the scenario's first ask; the rejoin replays the
 *  turn from its question and stays open. */
function waitingOn(scenario: string, id = "p1") {
  const all = SC[scenario];
  const upTo = all.findIndex((e) => e.type === "approval" || e.type === "question");
  const events = all.slice(0, upTo + 1);
  seed([sess(id)]);
  H.gets[`/build/app/sessions/${id}`] = { session: sess(id), events, running: "turn-1", pending: [] };
  H.scripts = [frames(events)];
  H.hold = new Promise<void>((res) => {
    H.release = res;
  });
}

beforeEach(() => {
  H.posts = [];
  H.postReply = {};
  H.streams = [];
  H.scripts = [];
  H.hold = null;
  H.release = null;
  seed();
});

afterEach(() => {
  H.release?.();
  cleanup();
});

const ARM_MS = 450;

/* ======================================================= the contract */

describe("Pi's events from the real parser", () => {
  it("every scenario's every event decodes, and each turn ends once", () => {
    for (const [name, evs] of Object.entries(SC)) {
      const got = decodeAll(evs);
      expect(got.length, name).toBe(evs.length);
      expect(got.filter((e) => e.type === "done").length, name).toBe(1);
      const conv = foldConversation(got);
      expect(conv.settled, name).toBe(true);
      expect(conv.asks, name).toEqual([]);
    }
  });

  it("an extension's confirm is a card of the tool it is about, asked every time", () => {
    const ev = decodeAll(SC.pi_extension_confirm_before_an_edit).find((e) => e.type === "approval");
    expect(ev?.type).toBe("approval");
    if (ev?.type !== "approval") return;
    expect(ev.kind).toBe("edit");
    expect(ev.title).toBe("Edit hello.txt");
    expect(ev.diff).toContain("+pear");
    expect(ev.canSession).toBe(false);
    expect(ev.onceOnly).toBe(true);
    expect(enterAllowsCodeAsk(ev, false)).toBe(false);
  });

  it("a select is a question with the extension's choices; an input is a free answer", () => {
    const sel = decodeAll(SC.pi_extension_select_before_a_command).find((e) => e.type === "question");
    expect(sel?.type === "question" && sel.questions[0].options.map((o) => o.label)).toEqual(["Allow", "Block"]);
    const inp = decodeAll(SC.pi_extension_command_asks_for_input).find((e) => e.type === "question");
    expect(inp?.type === "question" && inp.questions[0].options).toEqual([]);
    expect(inp?.type === "question" && inp.questions[0].question).toBe("Enter a value");
  });

  it("Stop reads Stopped; a sign-in failure names Pi's own /login; a model's error has no press", () => {
    const stop = decodeAll(SC.pi_interrupt).at(-1);
    expect(stop?.type === "done" && stop.stop).toBe("interrupted");
    const signIn = decodeAll(SC.pi_not_signed_in_for_the_model).find((e) => e.type === "error");
    expect(signIn?.type === "error" && signIn.fixKind).toBe("sign_in");
    expect(signIn?.type === "error" && signIn.fix).toContain("/login");
    const other = decodeAll(SC.pi_provider_answers_an_error).find((e) => e.type === "error");
    expect(other?.type === "error" && other.fixKind).toBe("");
  });
});

/* ======================================================= words */

describe("Pi's words", () => {
  const tools = decodeCodingTools(TOOLS);
  const pi = tools.find((t) => t.id === "pi")!;

  it("its own sign-in, its own rules, never Full access", () => {
    expect(pi.ownSignIn).toBe(true);
    expect(tools.find((t) => t.id === "claude-code")!.ownSignIn).toBe(false);
    expect(accountWords({ account: "", accountName: "", tool: "pi" }, tools)).toBe("Pi's own sign-in");
    expect(accountWords({ account: "", accountName: "", tool: "pi" }, null)).toBe("Pi's own sign-in");
    expect(accountWords({ account: "", accountName: "", tool: "claude-code" }, tools)).toBe("this PC's sign-in");
    expect(modeLabel("own", pi)).toBe("Pi's own rules");
    expect(modeLabel("own", null)).toBe("Its own rules");
    expect(isFullAccess("own", pi)).toBe(false);
    expect(toolLabel("pi")).toBe("Pi");
  });

  it("a terminal where Pi has finished offers Open in Code view; a word that starts with pi does not", () => {
    const t = { id: "t1", alive: true };
    expect(codeViewOfferFor(t, { agent_cli: "pi", state: "idle", can_open_in_code: true } as never)?.toolName).toBe("Pi");
    expect(codeViewOfferFor(t, { agent_cli: "pip", state: "idle", can_open_in_code: true } as never)).toBeNull();
  });
});

/* ======================================================= the view */

describe("the view", () => {
  it("a row says Pi · Pi's own sign-in, and the account chip says why there is no account", async () => {
    seed([sess("p1")]);
    H.gets["/build/app/sessions/p1"] = { session: sess("p1"), events: SC.pi_read_a_file, running: false, pending: [] };
    render(<BuildCodeView initialSession="p1" onOpenedTerminal={() => {}} />);
    expect((await screen.findByTestId("code-row-line-p1")).textContent).toBe("Pi · Pi's own sign-in");
    const chip = await screen.findByTestId("code-account-chip");
    expect(chip.textContent).toContain("Pi's own sign-in");
    expect(chip.getAttribute("title")).toBe(OWN_SIGN_IN_TITLE);
    expect(sessionRowLine(
      { ...{ id: "p1", tool: "pi", folder: "", title: "", account: "", accountName: "", model: "", effort: "", mode: "own" } } as never,
      decodeCodingTools(TOOLS),
    )).toBe("Pi · Pi's own sign-in");
  });

  it("the mode menu offers Pi's own rules only, and says why the others are not offered", async () => {
    seed([sess("p1")]);
    H.gets["/build/app/sessions/p1"] = { session: sess("p1"), events: [], running: null, pending: [], commands: [] };
    render(<BuildCodeView initialSession="p1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session");
    expect(screen.getByTestId("code-mode-chip-button").textContent).toContain("Pi's own rules");
    fireEvent.click(screen.getByTestId("code-mode-chip-button"));
    const menu = screen.getByTestId("code-mode-chip-menu");
    expect(within(menu).getAllByRole("menuitemradio").map((b) => b.dataset.testid)).toEqual(["code-mode-chip-option-own"]);
    expect(within(menu).getByTestId("code-mode-chip-hidden-ask").textContent).toContain(
      "Pi does not ask before it runs a tool",
    );
    expect(within(menu).getByTestId("code-mode-chip-hidden-full").textContent).toContain("without asking");
    expect(within(menu).getByTestId("code-mode-chip-hidden-plan").textContent).toContain("/plan");
  });

  it("New session for Pi: no account to choose, Pi's own rules, and Start sends exactly that", async () => {
    H.postReply["/build/app/sessions"] = sess("n1");
    H.gets["/build/app/sessions/n1"] = { session: sess("n1"), events: [], running: false, pending: [] };
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    fireEvent.click(await screen.findByTestId("code-new"));
    const row = await screen.findByTestId("code-new-tool-pi");
    expect(row.dataset.available).toBe("true");
    fireEvent.click(within(row).getByRole("radio"));
    await waitFor(() => expect(screen.getByTestId("code-new-account-own").textContent).toBe("Account: Pi's own sign-in"));
    expect(screen.queryByTestId("code-new-account")).toBeNull();
    expect(screen.getByTestId("code-new-mode-button").textContent).toContain("Pi's own rules");
    fireEvent.click(screen.getByTestId("stub-pick-folder"));
    fireEvent.click(screen.getByTestId("code-new-start"));
    await screen.findByTestId("code-session");
    expect(H.posts.find((p) => p.path === "/build/app/sessions")?.body).toEqual({
      harness: "pi",
      folder: "C:\\work\\shop",
      mode: "own",
    });
  });

  it("an extension's confirm shows the edit it is about; Enter never allows it; Allow once posts the answer", async () => {
    waitingOn("pi_extension_confirm_before_an_edit");
    render(<BuildCodeView initialSession="p1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    expect(card.dataset.kind).toBe("approval");
    expect(within(card).getByTestId("code-ask-diff")).toBeInTheDocument();
    expect(within(card).queryByTestId("code-ask-allow-session")).toBeNull();
    await act(() => new Promise((r) => setTimeout(r, ARM_MS)));
    expect(within(card).getByTestId("code-ask-keys").textContent).toBe("Click Allow once to allow it · Esc to deny");
    (document.activeElement as HTMLElement | null)?.blur();
    fireEvent.keyDown(window, { key: "Enter" });
    await act(() => new Promise((r) => setTimeout(r, 30)));
    expect(H.posts.filter((p) => p.path.endsWith("/answer"))).toHaveLength(0);
    fireEvent.click(within(card).getByTestId("code-ask-allow"));
    await waitFor(() =>
      expect(H.posts.find((p) => p.path === "/build/app/sessions/p1/answer")?.body).toEqual({
        request_id: "ask-1",
        decision: "allow",
      }),
    );
    // The question was drawn once (the replay restarts from it).
    expect(screen.getAllByTestId("code-user")).toHaveLength(1);
  });

  it("an extension's select is a question: a choice and Send posts it keyed by the question's words", async () => {
    waitingOn("pi_extension_select_before_a_command");
    render(<BuildCodeView initialSession="p1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    expect(card.dataset.kind).toBe("question");
    fireEvent.click(within(card).getByTestId("code-ask-option-0-0"));
    fireEvent.click(within(card).getByTestId("code-ask-send"));
    await waitFor(() =>
      expect(H.posts.find((p) => p.path.endsWith("/answer"))?.body).toEqual({
        request_id: "ask-1",
        decision: "allow",
        answers: { "Dangerous command: rm -rf ./nothing-here": "Allow" },
      }),
    );
  });
});
