/**
 * v1.413.0 — OpenCode in Build's Code view: the user's REAL OpenCode
 * (`opencode acp`), its own tools, agents and config on; Iron Jarvis turns
 * every permission question OpenCode asks into a card. Driven through the
 * real view and lib/buildApp; only the network (lib/api), the stream reader
 * and the folder tree are stand-ins.
 *
 * The events are THE CONTRACT's, written by the REAL daemon parser from
 * recorded opencode 1.18.18 output (tests/test_build_app_opencode_v1413.py,
 * IJ_REGEN_BUILD_APP_OPENCODE=1): no new event kind. OpenCode's own words:
 * four modes (Auto said with its reason), its own sign-in (no account to
 * choose, and the chip's title says OpenCode's way, never Pi's), and its own
 * "Always allow" offered as Allow for this session.
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
  postReply: {} as Record<string, unknown>,
  streams: [] as { path: string; body: unknown }[],
  scripts: [] as { event: string; data: Record<string, unknown> }[][],
  hold: null as Promise<void> | null,
  release: null as (() => void) | null,
  patches: [] as { path: string; body: unknown }[],
  patchReply: {} as Record<string, unknown>,
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
    patch: (path: string, body?: unknown) => {
      H.patches.push({ path, body });
      return Promise.resolve(H.patchReply[path] ?? {});
    },
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
  KEEPS_LAST_TITLE,
  OPENCODE_SIGN_IN_TITLE,
  OWN_SIGN_IN_TITLE,
  accountWords,
  decodeCodeEvent,
  decodeCodingTools,
  foldConversation,
  keepsLastTitle,
  ownSignInTitle,
  signsInItself,
  toolLabel,
  usageWords,
  type CodeEvent,
} from "@/lib/buildApp";
import { codeViewOfferFor } from "@/lib/buildCodeOffer";

const REAL = join(process.cwd(), "__tests__", "fixtures", "build-app-events-v1413.json");
const SC = (JSON.parse(readFileSync(REAL, "utf8")) as { scenarios: Record<string, Record<string, unknown>[]> }).scenarios;
const decodeAll = (evs: Record<string, unknown>[]) => evs.map((e) => decodeCodeEvent(e)).filter(Boolean) as CodeEvent[];

const OC_HINT =
  "OpenCode signs in by itself, per provider: to change it, run opencode auth login in a Build terminal. Iron Jarvis's accounts do not apply.";
const OC_TOOL = {
  id: "opencode",
  label: "OpenCode",
  installed: true,
  signed_in: null,
  version: null,
  own_sign_in: true,
  sign_in_hint: OC_HINT,
  modes: [
    { id: "ask", label: "Ask first", dangerous: false, about: "Asks before it changes a file, runs a command or uses the web; reads without asking." },
    { id: "edits", label: "Auto edits", dangerous: false, about: "Changes files without asking; asks before it runs a command or uses the web." },
    { id: "plan", label: "Plan", dangerous: false, about: "OpenCode's plan agent: it looks and plans, and changes no file." },
    { id: "full", label: "Full access", dangerous: true, about: "Runs everything without asking." },
  ],
  hidden_modes: [
    { id: "auto", label: "Auto", reason: "OpenCode has no mode that decides each step for you — choose Ask first or Auto edits." },
  ],
  models: [
    { id: "", label: "OpenCode's default", efforts: ["low", "high", "max"] },
    { id: "opencode/glm-5.3-flash-free", label: "OpenCode/GLM-5.3-Flash Free", efforts: ["low", "high", "max"] },
    { id: "lab/fleet", label: "Lab (local)/fleet (local, free)", efforts: [] },
  ],
  efforts: ["minimal", "low", "medium", "high", "xhigh", "max"],
  accounts: [],
  commands: [{ name: "init", description: "create/update AGENTS.md" }],
};
const TOOLS = { harnesses: [OC_TOOL] };

const sess = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  harness: "opencode",
  folder: "C:\\work\\shop",
  title: `Session ${id}`,
  account: "",
  model: "opencode/glm-5.3-flash-free",
  effort: "",
  mode: "ask",
  harness_session_id: "ses_e70eb48c6fferpq4ML7qasfQk1",
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

/** An OpenCode session parked on the scenario's first card; the rejoin
 *  replays the turn from its question and stays open. */
function waitingOn(scenario: string, id = "o1") {
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
  H.patches = [];
  H.patchReply = {};
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

/* ======================================================= the contract */

describe("OpenCode's events from the real parser", () => {
  it("every scenario's every event decodes, and each turn ends once with no card left open", () => {
    for (const [name, evs] of Object.entries(SC)) {
      const got = decodeAll(evs);
      expect(got.length, name).toBe(evs.length);
      expect(got.filter((e) => e.type === "done").length, name).toBe(1);
      const conv = foldConversation(got);
      expect(conv.settled, name).toBe(true);
      expect(conv.asks, name).toEqual([]);
    }
  });

  it("an edit card carries its diff and OpenCode's own Always allow as Allow for this session", () => {
    const ev = decodeAll(SC.opencode_asks_before_an_edit).find((e) => e.type === "approval");
    expect(ev?.type).toBe("approval");
    if (ev?.type !== "approval") return;
    expect(ev.kind).toBe("edit");
    expect(ev.title).toBe("Write note.txt");
    expect(ev.diff).toContain("+ZEBRA");
    expect(ev.canSession).toBe(true);
    const cmd = decodeAll(SC.opencode_command_denied).find((e) => e.type === "approval");
    expect(cmd?.type === "approval" && cmd.kind).toBe("command");
    expect(cmd?.type === "approval" && cmd.command).toBe("echo hi");
  });

  it("a note is sent then read, never handed back; Stop reads Stopped; a model's error has no press", () => {
    const steer = decodeAll(SC.opencode_note_while_it_works).filter((e) => e.type === "steer");
    expect(steer.map((e) => (e.type === "steer" ? e.state : ""))).toEqual(["sent", "read"]);
    const stop = decodeAll(SC.opencode_stop).at(-1);
    expect(stop?.type === "done" && stop.stop).toBe("interrupted");
    const err = decodeAll(SC.opencode_model_answers_an_error).find((e) => e.type === "error");
    expect(err?.type === "error" && err.title).toBe("OpenCode's model answered with an error");
    expect(err?.type === "error" && err.fixKind).toBe("");
  });

  it("OpenCode's cost is never called list price", () => {
    const usage = decodeAll(SC.opencode_read_a_file).find((e) => e.type === "usage");
    expect(usage?.type === "usage" && usage.listPrice).toBe(false);
    expect(usageWords({ input: 10, output: 5, costUsd: 0.25, listPrice: false, costReportedBy: "OpenCode" })).toBe(
      "10 in · 5 out · $0.25, the cost OpenCode reported",
    );
  });
});

/* ======================================================= words */

describe("OpenCode's words", () => {
  const tools = decodeCodingTools(TOOLS);
  const oc = tools.find((t) => t.id === "opencode")!;

  it("its own sign-in in its own words — never Pi's", () => {
    expect(oc.ownSignIn).toBe(true);
    expect(signsInItself("opencode", null)).toBe(true);
    expect(accountWords({ account: "", accountName: "", tool: "opencode" }, tools)).toBe("OpenCode's own sign-in");
    expect(ownSignInTitle("opencode", tools)).toBe(OC_HINT);
    // The daemon's words win over the page's own copy when it sends them.
    const said = decodeCodingTools({ harnesses: [{ ...OC_TOOL, sign_in_hint: "The daemon's own words." }] });
    expect(ownSignInTitle("opencode", said)).toBe("The daemon's own words.");
    expect(ownSignInTitle("opencode", null)).toBe(OPENCODE_SIGN_IN_TITLE);
    expect(ownSignInTitle("opencode", null)).not.toContain("/login");
    expect(ownSignInTitle("pi", null)).toBe(OWN_SIGN_IN_TITLE);
    expect(keepsLastTitle("opencode")).toContain("OpenCode");
    expect(keepsLastTitle("opencode")).not.toContain("Pi");
    expect(keepsLastTitle("pi")).toBe(KEEPS_LAST_TITLE);
    expect(toolLabel("opencode")).toBe("OpenCode");
  });

  it("a terminal where OpenCode has finished offers Open in Code view", () => {
    const t = { id: "t1", alive: true };
    expect(
      codeViewOfferFor(t, { agent_cli: "opencode", state: "idle", can_open_in_code: true } as never)?.toolName,
    ).toBe("OpenCode");
  });
});

/* ======================================================= the view */

describe("the view", () => {
  it("the account chip says OpenCode's own way to sign in", async () => {
    seed([sess("o1")]);
    H.gets["/build/app/sessions/o1"] = { session: sess("o1"), events: SC.opencode_read_a_file, running: false, pending: [] };
    render(<BuildCodeView initialSession="o1" onOpenedTerminal={() => {}} />);
    expect((await screen.findByTestId("code-row-line-o1")).textContent).toBe("OpenCode · OpenCode's own sign-in");
    const chip = await screen.findByTestId("code-account-chip");
    expect(chip.getAttribute("title")).toBe(OC_HINT);
  });

  it("the mode menu offers OpenCode's four presets and says why Auto is not one", async () => {
    seed([sess("o1")]);
    H.gets["/build/app/sessions/o1"] = { session: sess("o1"), events: [], running: null, pending: [], commands: [] };
    render(<BuildCodeView initialSession="o1" onOpenedTerminal={() => {}} />);
    await screen.findByTestId("code-session");
    fireEvent.click(screen.getByTestId("code-mode-chip-button"));
    const menu = screen.getByTestId("code-mode-chip-menu");
    expect(within(menu).getAllByRole("menuitemradio").map((b) => b.dataset.testid)).toEqual([
      "code-mode-chip-option-ask",
      "code-mode-chip-option-edits",
      "code-mode-chip-option-plan",
      "code-mode-chip-option-full",
    ]);
    expect(within(menu).getByTestId("code-mode-chip-hidden-auto").textContent).toContain("no mode that decides");
  });

  it("New session for OpenCode: no account to choose, and Send sends exactly that", async () => {
    H.postReply["/build/app/sessions"] = sess("n1");
    H.gets["/build/app/sessions/n1"] = { session: sess("n1"), events: [], running: false, pending: [] };
    render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
    fireEvent.click(await screen.findByTestId("code-start-tool"));
    fireEvent.click(await screen.findByTestId("code-start-tool-opencode"));
    await waitFor(() => expect(screen.getByTestId("code-start-account-own").textContent).toBe("OpenCode's own sign-in"));
    expect(screen.getByTestId("code-start-account-own").getAttribute("title")).toBe(OC_HINT);
    expect(screen.queryByTestId("code-start-account")).toBeNull();
    fireEvent.click(screen.getByTestId("code-start-folder-choose"));
    fireEvent.click(await screen.findByTestId("stub-pick-folder"));
    fireEvent.click(screen.getByTestId("code-start-folder-tree-use"));
    fireEvent.change(screen.getByTestId("code-start-box"), { target: { value: "hello" } });
    fireEvent.click(screen.getByTestId("code-start-send"));
    await screen.findByTestId("code-session");
    await waitFor(() =>
      expect(H.posts.find((p) => p.path === "/build/app/sessions")?.body).toEqual({
        harness: "opencode",
        folder: "C:\\work\\shop",
        mode: "ask",
      }),
    );
  });

  it("an edit card offers Allow for this session, and posts it", async () => {
    waitingOn("opencode_asks_before_an_edit");
    render(<BuildCodeView initialSession="o1" onOpenedTerminal={() => {}} />);
    const card = await screen.findByTestId("code-ask");
    expect(within(card).getByTestId("code-ask-diff")).toBeInTheDocument();
    fireEvent.click(within(card).getByTestId("code-ask-allow-session"));
    await waitFor(() =>
      expect(H.posts.find((p) => p.path === "/build/app/sessions/o1/answer")?.body).toEqual({
        request_id: "ask-1",
        decision: "allow_session",
      }),
    );
  });
});
