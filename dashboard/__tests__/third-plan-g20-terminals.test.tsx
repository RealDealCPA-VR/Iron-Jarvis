/**
 * Third north-star fix plan, group 20 — what the screen says is what happens.
 *
 *  - TM4 (decision G15): the Continue strip SENDS the account it names; when
 *    the daemon refuses it and names another, the button offers that one and
 *    the next press sends it; the new terminal's line says what the daemon
 *    ANSWERED, never what the strip guessed.
 *  - TM5 (decision G16): a "With Iron Jarvis's tools" Launch row shows the
 *    boxes first (none ticked: Open is off and says why); Open hands the
 *    ticked boxes to the page, which makes the new terminal WITH them
 *    (`POST /terminals/launch-with-tools`); a row whose tool cannot be set up
 *    is off and shows the reason; a plain row never says "ready".
 *
 * TerminalPane cannot mount in jsdom (xterm): its Launch-menu call sites are
 * SOURCE-PINNED (CRLF normalised); the dialog and the strip mount for real.
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
    calls: [] as Array<{ method: string; path: string; body: unknown }>,
    results: {} as Record<string, unknown>,
    /** One failure per call, in order (the next call answers normally). */
    fail: {} as Record<string, Array<{ status: number; message: string; body?: unknown }>>,
  };
});

vi.mock("@/lib/api", () => {
  const send = (method: string, path: string, body?: unknown) => {
    hooks.calls.push({ method, path, body });
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
    post: (path: string, body?: unknown) => send("POST", path, body),
    patch: (path: string, body?: unknown) => send("PATCH", path, body),
    put: (path: string, body?: unknown) => send("PUT", path, body),
    del: (path: string) => send("DELETE", path),
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
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
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
 * LaunchToolsDialog does on Open — hand the ticked boxes to the page. */
const paneSays = vi.hoisted(() => ({ answer: undefined as string | null | undefined }));
vi.mock("next/dynamic", () => {
  function TerminalPaneStub(props: {
    info: { id: string };
    onLaunchWithCapabilities?: (cli: string, tools: { memory: boolean; browser: boolean }) => Promise<string | null>;
  }) {
    return (
      <div data-testid={`terminal-pane-${props.info.id}`}>
        <button
          type="button"
          data-testid={`stub-open-with-tools-${props.info.id}`}
          onClick={() => {
            void props.onLaunchWithCapabilities?.("claude", { memory: true, browser: false }).then((a) => {
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
      <div data-testid="page-header-center">{center}</div>
      <div data-testid="page-header-actions">{actions}</div>
    </div>
  ),
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div /> }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div /> }));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));

import TerminalsPage from "@/app/terminals/page";
import { ContinuedFromLine, PaneContinue, resetContinueMemory } from "@/components/terminal/ContinueStrip";
import {
  LaunchRecipeNote,
  LaunchToolsDialog,
  RECIPE_HERE_HEADLINE,
  RECIPE_ONE_MORE_STEP_HEADLINE,
  RECIPE_READY_HEADLINE,
  RecipePlace,
  recipeCanSetUp,
  recipeNote,
  toolsNoneTickedWords,
  type LaunchCli,
} from "@/components/terminal/TerminalPane";
import type { IronProxyAccount } from "@/lib/ironProxy";
import { paneAccountsOf, type ContinueAnswer, type PaneAccounts, type PaneLimit } from "@/lib/paneAccounts";

/* ---- fixtures -------------------------------------------------------------- */

const LIGHT = "/iron-proxy?discover=0";

function acct(over: Partial<IronProxyAccount> & Record<string, unknown>): IronProxyAccount {
  return {
    id: "a",
    title: "Work Max",
    provider: "anthropic",
    lane: "cli",
    order: 0,
    enabled: true,
    state: { status: "ready", served: 0 },
    ...over,
  } as IronProxyAccount;
}

const snap = (accounts: IronProxyAccount[]) => ({
  status: { enabled: true, running: true, owned: true, error: null },
  accounts,
  discovered: [],
});

const WORK = acct({ id: "a", title: "Work Max", order: 0, state: { status: "parked", parkedReason: { kind: "quota-exhausted" } } });
const PERSONAL = acct({ id: "b", title: "Personal", order: 1 });
const SPARE = acct({ id: "c", title: "Spare", order: 2 });

const LIMIT: PaneLimit = { line: "Usage limit reached · resets 3pm", reset_words: "resets 3pm", since: "2026-10-03T14:00:00Z" };
const ON_WORK: PaneAccounts = paneAccountsOf({
  accounts: { anthropic: { id: "a", title: "Work Max", source: "iron-proxy", state: "parked" } },
})!;

const answerOn = (to: { id: string; title: string }): ContinueAnswer => ({
  pane: { id: "t-next", cwd: "C:\\proj\\t1", shell: "pwsh", alive: true },
  resumed: true,
  session_id: "s-1",
  parked: true,
  from: { id: "a", title: "Work Max" },
  to,
  note: `Carried your conversation over to “${to.title}”.`,
});

const CONTINUE = "POST /terminals/t1/continue-on-next";
const callsTo = (key: string) => hooks.calls.filter((c) => `${c.method} ${c.path}` === key);
const go = () => screen.queryByTestId("pane-continue-go-t1") as HTMLButtonElement | null;
const src = (...parts: string[]) => readFileSync(join(process.cwd(), ...parts), "utf8").replace(/\r\n/g, "\n");

beforeEach(() => {
  localStorage.clear();
  resetContinueMemory();
  hooks.responses = {};
  hooks.calls = [];
  hooks.results = {};
  hooks.fail = {};
  paneSays.answer = undefined;
});

afterEach(cleanup);

function mountStrip(onOpened = vi.fn()) {
  render(
    <>
      <PaneContinue paneId="t1" accounts={ON_WORK} limit={LIMIT} onOpened={onOpened} />
      <ContinuedFromLine paneId="t-next" />
    </>,
  );
  return onOpened;
}

/* ---- TM4: the strip sends what it names ------------------------------------ */

describe("TM4 — Continue on X goes to X", () => {
  it("the press sends the account the button names", async () => {
    hooks.responses[LIGHT] = snap([WORK, PERSONAL, SPARE]);
    hooks.results[CONTINUE] = answerOn({ id: "b", title: "Personal" });
    const onOpened = mountStrip();
    await waitFor(() => expect(go()?.textContent).toBe("Continue on “Personal”"));
    expect(screen.getByTestId("pane-continue-privacy-t1").textContent).toContain("copied to “Personal”");
    fireEvent.click(go()!);
    await waitFor(() => expect(onOpened).toHaveBeenCalledTimes(1));
    expect(callsTo(CONTINUE).map((c) => c.body)).toEqual([{ account: "b" }]);
    expect(document.getElementById("pane-continued-t-next")!.textContent).toBe(
      "Continued from “Work Max” — Carried your conversation over to “Personal”.",
    );
  });

  it("the new terminal's line says where the daemon ANSWERED it went, not the strip's guess", async () => {
    hooks.responses[LIGHT] = snap([WORK, PERSONAL, SPARE]);
    hooks.results[CONTINUE] = answerOn({ id: "c", title: "Spare" });
    const onOpened = mountStrip();
    await waitFor(() => expect(go()?.textContent).toBe("Continue on “Personal”"));
    fireEvent.click(go()!);
    await waitFor(() => expect(onOpened).toHaveBeenCalledTimes(1));
    expect(document.getElementById("pane-continued-t-next")!.textContent).toBe(
      "Continued from “Work Max” — It opened on “Spare”, not “Personal”. Carried your conversation over to “Spare”.",
    );
  });

  it("a refusal that names another account: the sentence, then the button offers THAT one and sends it", async () => {
    hooks.responses[LIGHT] = snap([WORK, PERSONAL, SPARE]);
    const sentence =
      "“Personal” cannot take over right now (it is at its usage limit), so nothing was copied. “Spare” can: press Continue on “Spare” to use it instead.";
    hooks.fail[CONTINUE] = [{ status: 409, message: sentence, body: { detail: sentence, instead: { id: "c", title: "Spare" } } }];
    hooks.results[CONTINUE] = answerOn({ id: "c", title: "Spare" });
    const onOpened = mountStrip();
    await waitFor(() => expect(go()?.textContent).toBe("Continue on “Personal”"));
    fireEvent.click(go()!);
    const err = await screen.findByTestId("pane-continue-error-t1");
    expect(err.textContent).toBe(sentence);
    expect(onOpened).not.toHaveBeenCalled();
    // waitFor the thing asserted: the button now names Spare.
    await waitFor(() => expect(go()?.textContent).toBe("Continue on “Spare”"));
    expect(screen.getByTestId("pane-continue-privacy-t1").textContent).toContain("copied to “Spare”");
    fireEvent.click(go()!);
    await waitFor(() => expect(onOpened).toHaveBeenCalledTimes(1));
    expect(callsTo(CONTINUE).map((c) => c.body)).toEqual([{ account: "b" }, { account: "c" }]);
  });

  it("with the account manager's list not in hand, the press names nothing and the daemon picks", async () => {
    hooks.results[CONTINUE] = answerOn({ id: "b", title: "Personal" });
    const onOpened = mountStrip();
    await waitFor(() => expect(go()?.textContent).toBe("Continue on the next account"));
    fireEvent.click(go()!);
    await waitFor(() => expect(onOpened).toHaveBeenCalledTimes(1));
    expect(callsTo(CONTINUE).map((c) => c.body)).toEqual([undefined]);
  });
});

/* ---- TM5: the boxes first ------------------------------------------------- */

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

describe("TM5 — the boxes are shown first", () => {
  it("none ticked: Open is off and says why; ticking one is the consent and Open sends it", async () => {
    hooks.responses["/browser/status"] = { access: "interactive" };
    const onOpen = vi.fn(async () => null);
    render(<LaunchToolsDialog cli={cli()} onOpen={onOpen} onCancel={() => {}} />);
    const open = screen.getByTestId("launch-tools-open-claude") as HTMLButtonElement;
    expect(open.disabled).toBe(true);
    expect(screen.getByTestId("launch-tools-none-claude").textContent).toBe(toolsNoneTickedWords("Claude Code"));
    const memory = screen.getByTestId("launch-tools-claude-memory") as HTMLInputElement;
    expect(memory.checked).toBe(false);
    fireEvent.click(memory);
    expect(open.disabled).toBe(false);
    expect(screen.queryByTestId("launch-tools-none-claude")).toBeNull();
    fireEvent.click(open);
    await waitFor(() => expect(onOpen).toHaveBeenCalledTimes(1));
    expect(onOpen).toHaveBeenCalledWith({ memory: true, browser: false });
  });

  it("a set-up that fails after the press is said beside the boxes", async () => {
    hooks.responses["/browser/status"] = { access: "interactive" };
    const why = "Claude Code could not be set up with Iron Jarvis's tools, so no terminal was opened.";
    render(<LaunchToolsDialog cli={cli()} onOpen={async () => why} onCancel={() => {}} />);
    fireEvent.click(screen.getByTestId("launch-tools-claude-memory"));
    fireEvent.click(screen.getByTestId("launch-tools-open-claude"));
    expect((await screen.findByTestId("launch-tools-error-claude")).textContent).toBe(why);
  });

  it("the Browser box is off while this install's browser setting is off", async () => {
    hooks.responses["/browser/status"] = { access: "off" };
    render(<LaunchToolsDialog cli={cli()} onOpen={async () => null} onCancel={() => {}} />);
    await waitFor(() =>
      expect((screen.getByTestId("launch-tools-claude-browser") as HTMLInputElement).disabled).toBe(true),
    );
    expect(screen.getByText("off for this install")).toBeTruthy();
  });

  it("the Launch row opens the boxes, never a terminal by itself; a row that cannot be set up is off", () => {
    const pane = src("components", "terminal", "TerminalPane.tsx");
    expect(pane).toMatch(/disabled=\{!recipeCanSetUp\(c\)\}\s*onClick=\{\(\) => \{\s*setToolsFor\(c\);/);
    expect(pane).toContain("<LaunchToolsDialog");
    expect(pane).toContain("const why = await onLaunchWithCapabilities(toolsFor.id, tools);");
    // The plain rows type into THIS terminal: never "ready".
    expect(pane).toMatch(/<RecipePlace\.Provider value="here">\s*\{installedClis\.map\(\(c\) => \{/);
  });

  it("inside the plain rows the note never says ready", () => {
    render(
      <RecipePlace.Provider value="here">
        <LaunchRecipeNote cli={cli()} />
      </RecipePlace.Provider>,
    );
    expect(screen.getByText(RECIPE_HERE_HEADLINE)).toBeTruthy();
    expect(screen.queryByText(RECIPE_READY_HEADLINE)).toBeNull();
  });

  it("a not-ready row shows its reason, never 'ready'", () => {
    const reason = "Iron Jarvis could not read this Claude Code build's help output.";
    const off = cli({ recipe: { ok: false, method: "none", limitations: [reason] } });
    expect(recipeCanSetUp(off)).toBe(false);
    render(<LaunchRecipeNote cli={off} />);
    expect(screen.getByTestId("launch-recipe-claude").textContent).toContain(reason);
    expect(screen.queryByText(RECIPE_READY_HEADLINE)).toBeNull();

    const step = "Pi does not load the Iron Jarvis adapter on its own: after it opens, run it.";
    const pi = cli({ id: "pi", label: "Pi", recipe: { ok: true, method: "mcp_stdio", ready: false, limitations: [step] } });
    expect(recipeCanSetUp(pi)).toBe(true);
    expect(recipeNote(pi)).toMatchObject({ ready: false, headline: RECIPE_ONE_MORE_STEP_HEADLINE, limitations: [step] });

    // A ready recipe, on a plain row (types into this terminal): not ready.
    expect(recipeNote(cli(), "here")).toMatchObject({ ready: false, headline: RECIPE_HERE_HEADLINE });
    expect(recipeNote(cli())).toMatchObject({ ready: true, headline: RECIPE_READY_HEADLINE });
  });
});

/* ---- TM5 through the Build page ------------------------------------------- */

const term = (id: string, extra: Record<string, unknown> = {}) => ({
  id,
  cwd: `C:\\proj\\${id}`,
  shell: "pwsh",
  argv: [],
  cols: 120,
  rows: 30,
  alive: true,
  exit_code: null,
  created_at: "2026-10-03T00:00:00Z",
  ...extra,
});

describe("TM5 — the page makes the new terminal WITH the ticked boxes", () => {
  function pageResponses() {
    hooks.responses = {
      "/terminals": { terminals: [term("t1")] },
      "/terminals/shells": { shells: [] },
      "/models": { models: [] },
      "/terminals/ai-clis": { clis: [] },
      "/skills": { skills: [] },
      "/terminals/activity": { panes: [{ id: "t1" }] },
    };
  }

  it("posts launch-with-tools with the boxes and the pane's folder, and adopts the new terminal", async () => {
    pageResponses();
    hooks.results["POST /terminals/launch-with-tools"] = {
      pane: term("t-tools", { cwd: "C:\\proj\\t1", name: "Claude Code" }),
      tools: { set_up: true, limitations: [] },
    };
    render(<TerminalsPage />);
    const open = await screen.findByTestId("stub-open-with-tools-t1");
    fireEvent.click(open);
    await waitFor(() => expect(screen.getByTestId("rail-pane-t-tools").style.visibility).toBe("visible"));
    expect(callsTo("POST /terminals/launch-with-tools").map((c) => c.body)).toEqual([
      { cli: "claude", near: "t1", capabilities: { memory: true, browser: false } },
    ]);
    // Never the old bare door.
    expect(callsTo("POST /terminals")).toEqual([]);
    expect(paneSays.answer).toBeNull();
  });

  it("a refusal comes back as the daemon's sentence, and nothing is added", async () => {
    pageResponses();
    const sentence = "Tick Memory or Browser first: with neither, Claude Code would start without Iron Jarvis's tools, so no terminal was opened.";
    hooks.fail["POST /terminals/launch-with-tools"] = [{ status: 409, message: sentence }];
    render(<TerminalsPage />);
    fireEvent.click(await screen.findByTestId("stub-open-with-tools-t1"));
    await waitFor(() => expect(paneSays.answer).toBe(sentence));
    expect(screen.queryByTestId("rail-pane-t-tools")).toBeNull();
  });
});
