/**
 * Fourth north-star fix plan, group 30 (v1.539.0) — the Build page's side.
 *
 *  - TM3 (decision D9 B): a terminal opened "with Iron Jarvis's tools" shows,
 *    ONCE, under its header, what its row's `tools` says: that nothing was
 *    written in its folder, each file an older Iron Jarvis left there, and the
 *    tool's own limitation (Pi's step by hand). One quiet line; × hides it.
 *  - C-TM-2: the "with tools" dialog says "Opens on {account}" — the account
 *    of the terminal it was opened from — and the launch body carries that
 *    account (`account`, as `/terminals/launch` takes it).
 *  - C-TM-1: a "could not be reopened" row whose program is gone (the daemon
 *    names `usual_shell` on it) offers "Open with {usual_shell}", which sends
 *    the retry with `{"shell": "default"}`; other rows do not offer it.
 *
 * TerminalPane cannot mount in jsdom (xterm): its call sites are SOURCE-PINNED
 * (CRLF normalised); the dialog, the line and the list mount for real.
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/* ---- api ------------------------------------------------------------------- */

const hooks = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    cancelled = false;
    body?: unknown;
    constructor(message: string, status = 0, body?: unknown) {
      super(message);
      this.status = status;
      this.name = "ApiError";
      if (body !== undefined) this.body = body;
    }
  }
  return {
    FakeApiError,
    responses: {} as Record<string, unknown>,
    calls: [] as Array<{ method: string; path: string; body: unknown; args: number }>,
    results: {} as Record<string, unknown>,
    fail: {} as Record<string, Array<{ status: number; message: string; body?: unknown }>>,
  };
});

vi.mock("@/lib/api", () => {
  const send = (method: string, path: string, rest: unknown[]) => {
    hooks.calls.push({ method, path, body: rest[0], args: rest.length });
    const key = `${method} ${path}`;
    const f = hooks.fail[key]?.shift();
    if (f) return Promise.reject(new hooks.FakeApiError(f.message, f.status, f.body));
    return Promise.resolve(key in hooks.results ? hooks.results[key] : {});
  };
  return {
    ApiError: hooks.FakeApiError,
    API_BASE: "http://127.0.0.1:8787",
    ijToken: () => null,
    get: (path: string) => {
      const r = hooks.responses[path];
      if (r === undefined) return Promise.reject(new hooks.FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(r);
    },
    post: (path: string, ...rest: unknown[]) => send("POST", path, rest),
    patch: (path: string, ...rest: unknown[]) => send("PATCH", path, rest),
    put: (path: string, ...rest: unknown[]) => send("PUT", path, rest),
    del: (path: string) => send("DELETE", path, []),
    wsUrl: (p: string) => `ws://test${p}`,
    sseUrl: (p: string) => `http://test${p}`,
    onUnauthorizedChange: () => () => {},
    onRequestErrorChange: () => () => {},
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/terminals",
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "layout"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  return {
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

/* The Build page's TerminalPane, stubbed: a button that does what the pane's
 * LaunchToolsDialog does on Open (hand the boxes AND the account it names to
 * the page), and the row's `tools` as the pane received it. */
const paneSays = vi.hoisted(() => ({
  answer: undefined as string | null | undefined,
  account: undefined as string | undefined,
}));
vi.mock("next/dynamic", () => {
  function TerminalPaneStub(props: {
    info: { id: string; tools?: unknown };
    onLaunchWithCapabilities?: (
      cli: string,
      tools: { memory: boolean; browser: boolean },
      account?: string,
    ) => Promise<string | null>;
  }) {
    return (
      <div data-testid={`terminal-pane-${props.info.id}`}>
        <span data-testid={`stub-tools-${props.info.id}`}>{JSON.stringify(props.info.tools ?? null)}</span>
        <button
          type="button"
          data-testid={`stub-open-with-tools-${props.info.id}`}
          onClick={() => {
            void props
              .onLaunchWithCapabilities?.("claude", { memory: true, browser: false }, paneSays.account)
              .then((a) => {
                paneSays.answer = a;
              });
          }}
        >
          open
        </button>
      </div>
    );
  }
  return {
    default: () => {
      function DynamicStub(props: Record<string, unknown>) {
        if (typeof props.paneId === "string") return <div data-testid={`pane-chat-${props.paneId}`} />;
        return <TerminalPaneStub {...(props as unknown as Parameters<typeof TerminalPaneStub>[0])} />;
      }
      return DynamicStub;
    },
  };
});

vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, actions, center }: { title: string; actions?: React.ReactNode; center?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      <div>{center}</div>
      <div>{actions}</div>
    </div>
  ),
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div /> }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div /> }));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));

import TerminalsPage from "@/app/terminals/page";
import { NotReopenedList, decodeNotReopened } from "@/components/terminal/NotReopenedList";
import {
  LaunchToolsDialog,
  PaneToolsNote,
  opensOnWords,
  paneToolsLines,
  resetPaneToolsNote,
  toolsAccountOf,
  type LaunchCli,
} from "@/components/terminal/TerminalPane";
import { paneAccountsOf, type PaneAccounts } from "@/lib/paneAccounts";

/* ---- fixtures -------------------------------------------------------------- */

const NOTE = "Nothing was written in your folder: Claude Code has Iron Jarvis's tools for this terminal only.";
const LEFTOVER = (p: string) =>
  `An older Iron Jarvis left ${p} in this folder. Nothing uses it now; delete it whenever you like.`;
const OLD_CODEX = LEFTOVER("C:\\proj\\t1\\.jarvis-codex");
const OLD_MCP = LEFTOVER("C:\\proj\\t1\\.mcp.json");
const PI_STEP = "Pi does not load Iron Jarvis's tools on its own: after it opens, run node C:\\Jarvis\\pi-adapter.mjs.";

const term = (id: string, extra: Record<string, unknown> = {}) => ({
  id,
  cwd: `C:\\proj\\${id}`,
  shell: "pwsh",
  argv: [],
  cols: 120,
  rows: 30,
  alive: true,
  exit_code: null,
  created_at: "2026-11-18T00:00:00Z",
  ...extra,
});

const ON_WORK: PaneAccounts = paneAccountsOf({
  accounts: { anthropic: { id: "a", title: "Work Max", source: "iron-proxy", state: "ready" } },
})!;
const ON_PC_LOGIN: PaneAccounts = paneAccountsOf({
  accounts: { anthropic: { id: null, title: "this PC's login", source: "default" } },
})!;

const cli = (over: Partial<LaunchCli> = {}): LaunchCli => ({
  id: "claude",
  label: "Claude Code",
  command: "claude",
  provider: "Anthropic",
  url: "",
  installed: true,
  recipe: { ok: true, method: "mcp_http", ready: true, limitations: [] },
  ...over,
});

const callsTo = (key: string) => hooks.calls.filter((c) => `${c.method} ${c.path}` === key);
const src = (...parts: string[]) => readFileSync(join(process.cwd(), ...parts), "utf8").replace(/\r\n/g, "\n");

function pageResponses(listing: Record<string, unknown> = {}) {
  hooks.responses = {
    "/terminals": { terminals: [term("t1")], ...listing },
    "/terminals/shells": { shells: [] },
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
    "/terminals/activity": { panes: [{ id: "t1" }] },
    "/browser/status": { access: "interactive" },
  };
}

beforeEach(() => {
  localStorage.clear();
  resetPaneToolsNote();
  hooks.responses = {};
  hooks.calls = [];
  hooks.results = {};
  hooks.fail = {};
  paneSays.answer = undefined;
  paneSays.account = undefined;
});

afterEach(cleanup);

/* ---- TM3: the line under a terminal opened with the tools ------------------- */

describe("TM3 — the note and the leftovers show once under the terminal", () => {
  it("reads the row's tools: the note, each leftover, then the limitation, each once", () => {
    const row = term("t-tools", {
      tools: { note: NOTE, leftovers: [OLD_CODEX, OLD_MCP, OLD_CODEX], limitations: [PI_STEP] },
    });
    expect(paneToolsLines(row)).toEqual([NOTE, OLD_CODEX, OLD_MCP, PI_STEP]);
    // A plain terminal's row has no `tools`: nothing is said.
    expect(paneToolsLines(term("t1"))).toEqual([]);
    // "" is "not said".
    expect(paneToolsLines(term("t2", { tools: { note: "", leftovers: [], limitations: [] } }))).toEqual([]);
  });

  it("renders those sentences from a row, and × hides them", () => {
    const row = term("t-tools", { tools: { note: NOTE, leftovers: [OLD_CODEX], limitations: [] } });
    render(<PaneToolsNote paneId="t-tools" lines={paneToolsLines(row)} />);
    const line = screen.getByTestId("pane-tools-note-t-tools");
    expect(line.textContent).toBe(NOTE + OLD_CODEX);
    expect(line.textContent!.split(OLD_CODEX).length).toBe(2); // once
    fireEvent.click(screen.getByTestId("pane-tools-note-close-t-tools"));
    expect(screen.queryByTestId("pane-tools-note-t-tools")).toBeNull();
  });

  it("the terminal mounts that line from its own row (one place for these sentences)", () => {
    const pane = src("components", "terminal", "TerminalPane.tsx");
    expect(pane).toContain("<PaneToolsNote paneId={info.id} lines={paneToolsLines(info)} />");
    expect(pane.split("<PaneToolsNote ").length).toBe(2);
  });

  it("the new terminal's row carries the answer's tools (also when the row itself omits them)", async () => {
    pageResponses();
    hooks.results["POST /terminals/launch-with-tools"] = {
      pane: term("t-tools", { cwd: "C:\\proj\\t1", name: "Claude Code" }),
      tools: { set_up: true, note: NOTE, leftovers: [OLD_CODEX], limitations: [] },
    };
    render(<TerminalsPage />);
    fireEvent.click(await screen.findByTestId("stub-open-with-tools-t1"));
    const tools = await screen.findByTestId("stub-tools-t-tools");
    expect(paneToolsLines({ tools: JSON.parse(tools.textContent!) })).toEqual([NOTE, OLD_CODEX]);
  });

  it("a row from the list keeps its own tools", async () => {
    pageResponses({
      terminals: [term("t1"), term("t9", { tools: { note: NOTE, leftovers: [], limitations: [PI_STEP] } })],
    });
    render(<TerminalsPage />);
    const tools = await screen.findByTestId("stub-tools-t9");
    expect(paneToolsLines({ tools: JSON.parse(tools.textContent!) })).toEqual([NOTE, PI_STEP]);
  });
});

/* ---- C-TM-2: opens on the account of the terminal it was opened from ------- */

describe("C-TM-2 — 'Opens on {account}' and the launch carries it", () => {
  it("names this terminal's account for that tool's provider, and the value it sends", () => {
    expect(toolsAccountOf(ON_WORK, "claude")).toMatchObject({ choice: "a", account: { title: "Work Max" } });
    expect(toolsAccountOf(ON_PC_LOGIN, "claude")?.choice).toBe("default");
    // No account for Codex's provider on this terminal: the daemon picks, nothing named.
    expect(toolsAccountOf(ON_WORK, "codex")).toBeNull();
    expect(toolsAccountOf(null, "claude")).toBeNull();
    expect(opensOnWords(ON_WORK.anthropic)).toBe("Opens on Work Max.");
    expect(opensOnWords(ON_PC_LOGIN.anthropic)).toBe("Opens on this PC's login.");
  });

  it("the dialog says 'Opens on Work Max.' and Open sends that account", async () => {
    hooks.responses["/browser/status"] = { access: "interactive" };
    const onOpen = vi.fn(async () => null);
    render(
      <LaunchToolsDialog cli={cli()} account={toolsAccountOf(ON_WORK, "claude")} onOpen={onOpen} onCancel={() => {}} />,
    );
    expect(screen.getByTestId("launch-tools-account-claude").textContent).toBe("Opens on Work Max.");
    fireEvent.click(screen.getByTestId("launch-tools-claude-memory"));
    fireEvent.click(screen.getByTestId("launch-tools-open-claude"));
    await waitFor(() => expect(onOpen).toHaveBeenCalledTimes(1));
    expect(onOpen).toHaveBeenCalledWith({ memory: true, browser: false }, "a");
  });

  it("without an account on this terminal the dialog names none and sends none", async () => {
    hooks.responses["/browser/status"] = { access: "interactive" };
    const onOpen = vi.fn(async () => null);
    render(<LaunchToolsDialog cli={cli()} account={null} onOpen={onOpen} onCancel={() => {}} />);
    expect(screen.queryByTestId("launch-tools-account-claude")).toBeNull();
    fireEvent.click(screen.getByTestId("launch-tools-claude-memory"));
    fireEvent.click(screen.getByTestId("launch-tools-open-claude"));
    await waitFor(() => expect(onOpen).toHaveBeenCalledTimes(1));
    expect(onOpen.mock.calls[0]).toEqual([{ memory: true, browser: false }]);
  });

  it("the terminal hands the dialog its own account and passes the press on", () => {
    const pane = src("components", "terminal", "TerminalPane.tsx");
    expect(pane).toContain("account={toolsAccountOf(paneAccounts, toolsFor.id)}");
    expect(pane).toContain("const why = await onLaunchWithCapabilities(toolsFor.id, tools, account);");
  });

  it("the page's launch body carries that account", async () => {
    pageResponses();
    paneSays.account = "a";
    hooks.results["POST /terminals/launch-with-tools"] = {
      pane: term("t-tools", { cwd: "C:\\proj\\t1" }),
      tools: { set_up: true, note: NOTE, leftovers: [], limitations: [] },
    };
    render(<TerminalsPage />);
    fireEvent.click(await screen.findByTestId("stub-open-with-tools-t1"));
    await waitFor(() => expect(paneSays.answer).toBeNull());
    expect(callsTo("POST /terminals/launch-with-tools").map((c) => c.body)).toEqual([
      { cli: "claude", near: "t1", capabilities: { memory: true, browser: false }, account: "a" },
    ]);
  });

  it("a refused account is the daemon's sentence, as is, and nothing opens", async () => {
    pageResponses();
    paneSays.account = "a";
    const sentence = "“Work Max” is at its usage limit right now, so no terminal was opened.";
    hooks.fail["POST /terminals/launch-with-tools"] = [{ status: 409, message: sentence }];
    render(<TerminalsPage />);
    fireEvent.click(await screen.findByTestId("stub-open-with-tools-t1"));
    await waitFor(() => expect(paneSays.answer).toBe(sentence));
    expect(screen.queryByTestId("terminal-pane-t-tools")).toBeNull();
  });
});

/* ---- C-TM-1: "Open with {usual shell}" on a row whose program is gone ------- */

const GONE_WHY = "Its program, fish, is no longer on this PC (it may have been uninstalled or moved).";
const notReopened = () => ({
  restore_failed: { count: 2, ids: ["t2", "t3"], reason: "their shell didn't start" },
  restore_failed_panes: [
    { id: "t2", name: "notes", cwd: "C:\\proj\\beta", reason: GONE_WHY, usual_shell: "PowerShell 7" },
    { id: "t3", name: null, cwd: "C:\\proj\\gamma", reason: "Its shell did not start within a minute." },
  ],
});
const RETRY_T2 = "POST /terminals/not-reopened/t2/retry";

describe("C-TM-1 — 'Open with {usual shell}' on a row whose program is gone", () => {
  it("reads usual_shell only where the daemon names it", () => {
    const rows = decodeNotReopened(notReopened().restore_failed_panes);
    expect(rows[0].usualShell).toBe("PowerShell 7");
    expect(rows[1].usualShell).toBeUndefined();
  });

  it("shows the button only on that row", async () => {
    pageResponses(notReopened());
    render(<TerminalsPage />);
    expect((await screen.findByTestId("not-reopened-usual-t2")).textContent).toBe("Open with PowerShell 7");
    expect(screen.queryByTestId("not-reopened-usual-t3")).toBeNull();
    expect(screen.getByTestId("not-reopened-retry-t3")).toBeTruthy();
  });

  it("sends the retry with {shell: 'default'}, and the reopened terminal takes the row's place", async () => {
    pageResponses(notReopened());
    hooks.results[RETRY_T2] = term("t2", { cwd: "C:\\proj\\beta", shell: "pwsh" });
    render(<TerminalsPage />);
    fireEvent.click(await screen.findByTestId("not-reopened-usual-t2"));
    await waitFor(() => expect(screen.queryByTestId("not-reopened-t2")).toBeNull());
    expect(callsTo(RETRY_T2).map((c) => c.body)).toEqual([{ shell: "default" }]);
    expect(await screen.findByTestId("terminal-pane-t2")).toBeTruthy();
    expect(screen.getByTestId("not-reopened-t3")).toBeTruthy();
  });

  it("Try again still sends no shell", async () => {
    pageResponses(notReopened());
    hooks.results[RETRY_T2] = term("t2");
    render(<TerminalsPage />);
    fireEvent.click(await screen.findByTestId("not-reopened-retry-t2"));
    await waitFor(() => expect(screen.queryByTestId("not-reopened-t2")).toBeNull());
    expect(callsTo(RETRY_T2).map((c) => c.args)).toEqual([0]);
  });

  it("a refusal is said on the row, as 'Try again' says it, and the row stays", async () => {
    pageResponses(notReopened());
    const sentence = "The terminal could not be reopened. Its program, pwsh, is no longer on this PC.";
    hooks.fail[RETRY_T2] = [{ status: 409, message: sentence, body: { detail: sentence } }];
    render(<TerminalsPage />);
    const usual = await screen.findByTestId("not-reopened-usual-t2");
    await act(async () => {
      fireEvent.click(usual);
    });
    await waitFor(() =>
      expect(screen.getByTestId("not-reopened-t2").querySelector('[role="status"]')?.textContent).toBe(sentence),
    );
  });

  it("the list hands the press on with the shell", async () => {
    const onRetry = vi.fn(async () => null);
    render(
      <NotReopenedList panes={decodeNotReopened(notReopened().restore_failed_panes)} onRetry={onRetry} onClose={async () => null} />,
    );
    fireEvent.click(screen.getByTestId("not-reopened-usual-t2"));
    await waitFor(() => expect(onRetry).toHaveBeenCalledTimes(1));
    expect(onRetry).toHaveBeenCalledWith("t2", "default");
  });
});
