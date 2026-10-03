/**
 * v1.302.0 — Build panes on Iron-Proxy accounts.
 *
 * A vendor CLI's account is its home folder, read once when the program
 * starts, so an account belongs to a PANE: it is chosen when the pane starts
 * and "switching" means a pane started on another account. These tests guard:
 *
 *  - the Launch menu's offer: Iron-Proxy off / not running / 404 / no CLI
 *    account for that provider / a CLI with no account provider -> NO offer,
 *    and the menu is exactly what it was (the rows are gated on the offer,
 *    pinned in TerminalPane's source); on -> "as <account>" rows with their
 *    state (parked / needs sign-in DISABLED with the reason as tooltip) and
 *    "as this PC's login";
 *  - the pane's OWN account types into this pane (no request); another account
 *    POSTs /terminals/launch {cli, account, near} and hands the new pane over;
 *    the page adds it and FOCUSES it (driven through the real page);
 *  - the header chip `#pane-account-<id>` (label, amber parked "until <local
 *    time>", needs sign-in, missing, the tooltip and the daemon's note) and the
 *    rail's same short label;
 *  - the Iron-Proxy card's "Open in Build" (POST …/open -> /terminals?focus=)
 *    and the Handbook line.
 *
 * TerminalPane drags xterm into jsdom, so the menu half follows the house idiom
 * (v1.163.0, v1.238.0): mount the components it renders, unit-test the seams,
 * and SOURCE-PIN the call site — read with line endings normalised (CRLF on CI).
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
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    responses: {} as Record<string, unknown>,
    gets: [] as string[],
    calls: [] as Array<{ method: string; path: string; body: unknown }>,
    results: {} as Record<string, unknown>,
    fail: {} as Record<string, { status: number; message: string }>,
    push: vi.fn(),
  };
});

vi.mock("@/lib/api", () => {
  const send = (method: string, path: string, body?: unknown) => {
    hooks.calls.push({ method, path, body });
    const key = `${method} ${path}`;
    const f = hooks.fail[key];
    if (f) return Promise.reject(new hooks.FakeApiError(f.message, f.status));
    return Promise.resolve(key in hooks.results ? hooks.results[key] : {});
  };
  return {
    ApiError: hooks.FakeApiError,
    API_BASE: "http://127.0.0.1:8787",
    ijToken: () => null,
    get: (path: string) => {
      hooks.gets.push(path);
      const r = hooks.responses[path];
      if (r === undefined) {
        return Promise.reject(new hooks.FakeApiError(`unmocked GET ${path}`, 404));
      }
      return Promise.resolve(r);
    },
    post: (path: string, body?: unknown) => send("POST", path, body),
    patch: (path: string, body?: unknown) => send("PATCH", path, body),
    put: (path: string, body?: unknown) => send("PUT", path, body),
    del: (path: string) => send("DELETE", path),
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: hooks.push, replace: () => {}, refresh: () => {} }),
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
  const MOTION_ONLY = new Set([
    "initial",
    "animate",
    "exit",
    "transition",
    "variants",
    "layout",
    "whileHover",
    "whileTap",
    "whileInView",
    "viewport",
  ]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  return {
    // v1.250.0 mock contract: every framer-motion mock exports `m`.
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

/* ---- the Build page: xterm never enters jsdom ------------------------------ */

vi.mock("next/dynamic", async () => {
  // The REAL chip, fed the prop the page hands TerminalPane — so "the chip
  // updates while the page stays open" is driven through the page's own poll.
  const { PaneAccountChip } = await import("@/components/terminal/PaneAccountChip");
  function TerminalPaneStub(props: {
    info: { id: string; shell: string };
    onOpenedPane?: (p: { id: string; [k: string]: unknown }) => void;
    liveAccounts?: import("@/lib/paneAccounts").PaneAccounts | null;
  }) {
    return (
      <div data-testid={`terminal-pane-${props.info.id}`}>
        {props.info.shell}
        <PaneAccountChip paneId={props.info.id} accounts={props.liveAccounts ?? null} />
        <button
          type="button"
          data-testid={`stub-open-${props.info.id}`}
          onClick={() =>
            props.onOpenedPane?.({
              id: "t-new",
              cwd: "C:\\proj\\alpha",
              shell: "pwsh",
              argv: [],
              cols: 120,
              rows: 30,
              alive: true,
              exit_code: null,
              created_at: "2026-10-03T00:00:00Z",
              accounts: { anthropic: { id: "b", title: "Home Max", source: "iron-proxy", state: "ready" } },
            })
          }
        >
          open
        </button>
      </div>
    );
  }
  return {
    default: () => {
      function DynamicStub(props: Record<string, unknown>) {
        if (typeof props.paneId === "string") {
          return <div data-testid={`pane-chat-${props.paneId}`} />;
        }
        return <TerminalPaneStub {...(props as unknown as Parameters<typeof TerminalPaneStub>[0])} />;
      }
      return DynamicStub;
    },
  };
});

vi.mock("react-rnd", () => ({
  Rnd: ({ children }: { children?: React.ReactNode }) => <div data-testid="rnd">{children}</div>,
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: React.ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {actions}
    </div>
  ),
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <div data-testid="directory-tree" />,
}));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: () => <div data-testid="files-panel" />,
}));
vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: true }),
}));

import TerminalsPage from "@/app/terminals/page";
import { IronProxyCard } from "@/components/connections/IronProxyCard";
import { LaunchAccountRows, PaneAccountChip } from "@/components/terminal/PaneAccountChip";
import { PaneRail, type RailPane } from "@/components/terminal/PaneRail";
import { localTime, type IronProxyAccount } from "@/lib/ironProxy";
import {
  chipProvider,
  isPaneOwnAccount,
  launchBody,
  launchOffer,
  offerRowState,
  paneAccountBadge,
  paneAccountsOf,
  type PaneAccounts,
} from "@/lib/paneAccounts";

/* ---- fixtures -------------------------------------------------------------- */

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

function snap(status: Record<string, unknown> = {}, accounts: IronProxyAccount[] = []) {
  return {
    status: { enabled: true, running: true, owned: true, error: null, ...status },
    accounts,
    discovered: [],
    providers_used_by_jarvis: { anthropic: "claude-cli", openai: "codex-cli", xai: "grok-cli" },
  };
}

/** Today at 15:05 LOCAL time, as the UTC ISO string the daemon sends. */
function todayAt1505Iso(): string {
  const d = new Date();
  d.setHours(15, 5, 0, 0);
  return d.toISOString();
}
const PARKED_ISO = todayAt1505Iso();

const ACCOUNTS = [
  acct({ id: "b", title: "Home Max", order: 1 }),
  acct({ id: "a", title: "Work Max", order: 0, state: { status: "active" } }),
  acct({
    id: "c",
    title: "Spare",
    order: 2,
    state: {
      status: "parked",
      parkedUntil: PARKED_ISO,
      parkedReason: { kind: "quota-exhausted", message: "5-hour limit reached" },
    },
  }),
  acct({ id: "d", title: "Old Login", order: 3, state: { status: "unauthenticated" } }),
  acct({ id: "e", title: "Switched Off", order: 4, enabled: false, state: { status: "disabled" } }),
  acct({ id: "k", title: "Claude API key", order: 5, lane: "api" }),
  acct({ id: "x", title: "Grok main", provider: "xai", order: 0 }),
];

const ON = snap({}, ACCOUNTS);

const callsTo = (method: string, path: string) =>
  hooks.calls.filter((c) => c.method === method && c.path === path);

const src = (...parts: string[]) =>
  readFileSync(join(process.cwd(), ...parts), "utf8").replace(/\r\n/g, "\n");

beforeEach(() => {
  localStorage.clear();
  hooks.responses = {};
  hooks.gets = [];
  hooks.calls = [];
  hooks.results = {};
  hooks.fail = {};
  hooks.push.mockReset();
});

afterEach(cleanup);

/* ---- 1. which accounts the Launch menu offers ------------------------------ */

describe("launchOffer — off is exactly today's menu", () => {
  it("offers NOTHING when Iron-Proxy is off, not running, 404/absent, or has no CLI account", () => {
    expect(launchOffer("claude", null)).toBeNull(); // 404 / not loaded: useApi data stays null
    expect(launchOffer("claude", undefined)).toBeNull();
    expect(launchOffer("claude", {})).toBeNull(); // older / mocked daemon
    expect(launchOffer("claude", snap({ enabled: false, running: false }, ACCOUNTS))).toBeNull();
    expect(launchOffer("claude", snap({ enabled: true, running: false }, ACCOUNTS))).toBeNull();
    expect(launchOffer("codex", ON)).toBeNull(); // no openai account
    expect(launchOffer("opencode", ON)).toBeNull(); // a CLI with no account provider
    expect(
      launchOffer("claude", snap({}, [acct({ id: "k", lane: "api" }), acct({ id: "e", enabled: false })])),
    ).toBeNull();
  });

  it("offers the provider's enabled CLI-lane accounts in Iron-Proxy's order", () => {
    const o = launchOffer("claude", ON)!;
    expect(o.provider).toBe("anthropic");
    expect(o.accounts.map((a) => a.id)).toEqual(["a", "b", "c", "d"]);
    expect(launchOffer("grok", ON)!.accounts.map((a) => a.id)).toEqual(["x"]);
  });

  it("states a row: parked and needs-sign-in are disabled with the reason; ready/active are not", () => {
    const parked = offerRowState(ACCOUNTS[2]);
    expect(parked.disabled).toBe(true);
    expect(parked.word).toBe(`Parked until ${localTime(PARKED_ISO)}`);
    expect(parked.why).toMatch(/plan limit reached/);
    expect(parked.why).not.toContain(PARKED_ISO);
    const signin = offerRowState(ACCOUNTS[3]);
    expect(signin).toMatchObject({ word: "Needs sign-in", disabled: true });
    expect(signin.why).toMatch(/Connections page/);
    expect(offerRowState(ACCOUNTS[1])).toEqual({ word: "Active", disabled: false, why: null });
    expect(offerRowState(ACCOUNTS[0])).toEqual({ word: "Ready", disabled: false, why: null });
  });

  it("own account = this pane; no record = this PC's login", () => {
    expect(isPaneOwnAccount(undefined, null)).toBe(true);
    expect(isPaneOwnAccount(undefined, "a")).toBe(false);
    expect(isPaneOwnAccount({ id: "a", title: "Work Max", source: "iron-proxy" }, "a")).toBe(true);
    expect(isPaneOwnAccount({ id: "a", title: "Work Max", source: "iron-proxy" }, null)).toBe(false);
    expect(isPaneOwnAccount({ id: null, title: "this PC's login", source: "default" }, null)).toBe(true);
    expect(launchBody("claude", null, "t1")).toEqual({ cli: "claude", account: "default", near: "t1" });
  });
});

/* ---- 2. the Launch menu's account rows ------------------------------------- */

function renderRows(paneAccount: PaneAccounts[string] | undefined) {
  const onTypeHere = vi.fn();
  const onOpened = vi.fn();
  render(
    <LaunchAccountRows
      cliId="claude"
      cliLabel="Claude Code"
      paneId="t1"
      paneAccount={paneAccount}
      accounts={launchOffer("claude", ON)!.accounts}
      onTypeHere={onTypeHere}
      onOpened={onOpened}
    />,
  );
  return { onTypeHere, onOpened };
}

const asRow = (key: string) => screen.getByTestId(`launch-as-claude-${key}`) as HTMLButtonElement;

describe("Launch → Claude Code → as <account>", () => {
  it("lists every offered account with its state, then this PC's login", () => {
    renderRows({ id: "a", title: "Work Max", source: "iron-proxy", state: "active" });
    const rows = screen.getByTestId("launch-accounts-claude").querySelectorAll("button");
    expect([...rows].map((b) => b.textContent)).toEqual([
      "as Work Maxthis pane",
      "as Home MaxReady · new pane",
      `as SpareParked until ${localTime(PARKED_ISO)}`,
      "as Old LoginNeeds sign-in",
      "as this PC's loginnew pane",
    ]);
    expect(asRow("c").disabled).toBe(true);
    expect(asRow("c").title).toMatch(/plan limit reached/);
    expect(asRow("d").disabled).toBe(true);
    expect(asRow("d").title).toMatch(/needs signing in/);
    expect(asRow("b").disabled).toBe(false);
    expect(asRow("default").disabled).toBe(false);
  });

  it("the pane's OWN account types into this pane — no request", () => {
    const { onTypeHere, onOpened } = renderRows({ id: "a", title: "Work Max", source: "iron-proxy" });
    fireEvent.click(asRow("a"));
    expect(onTypeHere).toHaveBeenCalledTimes(1);
    expect(onOpened).not.toHaveBeenCalled();
    expect(hooks.calls).toEqual([]);
  });

  it("a pane with no account record types 'as this PC's login' here too", () => {
    const { onTypeHere } = renderRows(undefined);
    fireEvent.click(asRow("default"));
    expect(onTypeHere).toHaveBeenCalledTimes(1);
    expect(hooks.calls).toEqual([]);
  });

  it("ANOTHER account POSTs /terminals/launch {cli, account, near} and hands the new pane over", async () => {
    hooks.results["POST /terminals/launch"] = { id: "t-new", cwd: "C:\\proj", alive: true };
    const { onTypeHere, onOpened } = renderRows({ id: "a", title: "Work Max", source: "iron-proxy" });
    fireEvent.click(asRow("b"));
    await waitFor(() => expect(onOpened).toHaveBeenCalledTimes(1));
    expect(callsTo("POST", "/terminals/launch")).toEqual([
      { method: "POST", path: "/terminals/launch", body: { cli: "claude", account: "b", near: "t1" } },
    ]);
    expect(onOpened.mock.calls[0][0]).toMatchObject({ id: "t-new", cwd: "C:\\proj" });
    expect(onTypeHere).not.toHaveBeenCalled();
  });

  it("'as this PC's login' from a pane on an account asks for account: default", async () => {
    hooks.results["POST /terminals/launch"] = { id: "t-pc" };
    const { onOpened } = renderRows({ id: "a", title: "Work Max", source: "iron-proxy" });
    fireEvent.click(asRow("default"));
    await waitFor(() => expect(onOpened).toHaveBeenCalledTimes(1));
    expect(callsTo("POST", "/terminals/launch")[0].body).toEqual({
      cli: "claude",
      account: "default",
      near: "t1",
    });
  });

  it("a refusal shows the daemon's sentence and opens nothing", async () => {
    hooks.fail["POST /terminals/launch"] = {
      status: 409,
      message: "Home Max is parked until 3:05 PM — pick another account.",
    };
    const { onOpened } = renderRows({ id: "a", title: "Work Max", source: "iron-proxy" });
    fireEvent.click(asRow("b"));
    const err = await screen.findByTestId("launch-as-error-claude");
    expect(err.textContent).toBe("Home Max is parked until 3:05 PM — pick another account.");
    expect(onOpened).not.toHaveBeenCalled();
  });
});

/* ---- 3. TerminalPane's call site (source pins) ----------------------------- */

describe("TerminalPane wires the rows without changing today's menu", () => {
  const pane = src("components", "terminal", "TerminalPane.tsx");

  it("reads Iron-Proxy ONLY while the Launch menu is open, and NEVER the full view (F3)", () => {
    expect(pane).toContain(
      'usePolledApi<unknown>(\n    launchOpen ? "/iron-proxy?discover=0" : null,\n    LAUNCH_ACCOUNTS_POLL_MS,\n  );',
    );
    // Every Iron-Proxy path the pane names is the light one — the full view
    // makes Iron-Proxy run each vendor CLI's status command.
    expect(pane.match(/["'`]\/iron-proxy[^"'`]*["'`]/g)).toEqual(['"/iron-proxy?discover=0"']);
  });

  it("the account rows render only under an offer, after the unchanged plain row", () => {
    expect(pane).toContain("const offer = launchOffer(c.id, ironProxySnap);");
    expect(pane).toMatch(
      /onClick=\{\(\) => \{\n\s+launchCli\(c\);\n\s+setLaunchOpen\(false\);\n\s+\}\}[\s\S]*?<LaunchRecipeNote cli=\{c\} \/>\n\s+<\/button>\n\s+\{offer && \(\n\s+<LaunchAccountRows/,
    );
    // The pane's own account goes through the very same launchCli.
    expect(pane).toMatch(/onTypeHere=\{\(\) => \{\n\s+launchCli\(c\);\n\s+setLaunchOpen\(false\);/);
    expect(pane).toContain("paneAccount={paneAccounts?.[offer.provider]}");
  });

  it("another account's pane is handed to the page (else /terminals?focus=)", () => {
    expect(pane).toMatch(
      /function openedPane\(pane: OpenedPane\) \{\n\s+setLaunchOpen\(false\);\n\s+if \(onOpenedPane\) onOpenedPane\(pane\);\n\s+else window\.location\.assign\(terminalsHref\(pane\.id\)\);/,
    );
    expect(pane).toContain("onOpened={openedPane}");
  });

  it("the header carries the account chip from the pane row", () => {
    expect(pane).toContain(
      "const paneAccounts = liveAccounts !== undefined ? liveAccounts : paneAccountsOf(info);",
    );
    expect(pane).toContain(
      "<PaneAccountChip paneId={info.id} accounts={paneAccounts} agentCli={agentCli || paneCli} />",
    );
  });

  it("the Build page passes onOpenedPane", () => {
    const page = src("app", "terminals", "page.tsx");
    expect(page).toContain("onOpenedPane={adoptPane}");
    expect(page).toContain("liveAccounts={livePaneAccounts(t, act)}");
  });
});

/* ---- 4. the Build page adds and FOCUSES the new pane ----------------------- */

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

function seedPage(terminals: unknown[], panes: unknown[] = []) {
  hooks.responses = {
    "/terminals": { terminals },
    "/terminals/shells": { shells: [] },
    "/models": { models: [] },
    "/terminals/ai-clis": { clis: [] },
    "/skills": { skills: [] },
    "/terminals/activity": { panes },
  };
}

describe("the Build page", () => {
  it("adds the pane another account opened and brings it into focus", async () => {
    seedPage([term("t1"), term("t2")]);
    render(<TerminalsPage />);
    await screen.findByTestId("terminal-pane-t1");
    expect(screen.getByTestId("rail-pane-t1").style.visibility).toBe("visible");
    fireEvent.click(screen.getByTestId("stub-open-t1"));
    await waitFor(() => expect(screen.getByTestId("rail-pane-t-new").style.visibility).toBe("visible"));
    expect(screen.getByTestId("rail-pane-t1").style.visibility).toBe("hidden");
    expect(screen.getByTestId("rail-row-t-new")).toBeTruthy();
    // …and its rail row names the account it started on.
    expect(screen.getByTestId("rail-account-t-new").textContent).toBe("Claude · Home Max");
  });

  it("the chip and the rail turn amber when an ACTIVITY tick reports parked — no reload", async () => {
    const acc = (extra: Record<string, unknown>) => ({
      anthropic: { id: "a", title: "Work Max", source: "iron-proxy", ...extra },
    });
    seedPage([term("t1", { accounts: acc({ state: "ready" }) })], [{ id: "t1", accounts: acc({ state: "ready" }) }]);
    render(<TerminalsPage />);
    await waitFor(() => expect(hooks.gets.filter((g) => g === "/terminals/activity").length).toBeGreaterThan(0));
    const chip = () => document.getElementById("pane-account-t1");
    await waitFor(() => expect(chip()?.getAttribute("data-tone")).toBe("plain"));
    expect(screen.getByTestId("rail-account-t1").textContent).toBe("Claude · Work Max");
    // The account gets parked while the page stays open: only the activity
    // poll says so (GET /terminals is read once, at load).
    hooks.responses["/terminals/activity"] = {
      panes: [{ id: "t1", accounts: acc({ state: "parked", until: PARKED_ISO, reason: "rate-limit" }) }],
    };
    await waitFor(() => expect(chip()?.getAttribute("data-tone")).toBe("amber"), { timeout: 8000 });
    expect(chip()!.textContent).toBe(`Claude · Work Max · until ${localTime(PARKED_ISO)}`);
    await waitFor(() =>
      expect(screen.getByTestId("rail-account-t1").getAttribute("data-tone")).toBe("amber"),
    );
    expect(hooks.gets.filter((g) => g === "/terminals")).toHaveLength(1);
  });

  it("the rail shows each pane's account label; a pane without accounts shows none", async () => {
    seedPage([
      term("t1", {
        accounts: {
          anthropic: {
            id: "a",
            title: "Work Max",
            source: "iron-proxy",
            state: "parked",
            until: PARKED_ISO,
            reason: "rate-limit",
          },
        },
      }),
      term("t2"),
    ]);
    render(<TerminalsPage />);
    const label = await screen.findByTestId("rail-account-t1");
    expect(label.textContent).toBe(`Claude · Work Max · until ${localTime(PARKED_ISO)}`);
    expect(label.getAttribute("data-tone")).toBe("amber");
    expect(screen.queryByTestId("rail-account-t2")).toBeNull();
  });
});

/* ---- 5. the header chip ---------------------------------------------------- */

describe("the account chip", () => {
  const chip = (id = "t1") => document.getElementById(`pane-account-${id}`);

  it("renders nothing for a pane without accounts", () => {
    render(<PaneAccountChip paneId="t1" accounts={paneAccountsOf({ id: "t1" })} />);
    expect(chip()).toBeNull();
  });

  it("names the account; the tooltip says it is fixed and how to switch", () => {
    render(
      <PaneAccountChip
        paneId="t1"
        accounts={paneAccountsOf({
          accounts: { anthropic: { id: "a", title: "Work Max", source: "iron-proxy", state: "active" } },
        })}
      />,
    );
    expect(chip()!.textContent).toBe("Claude · Work Max");
    expect(chip()!.getAttribute("data-tone")).toBe("plain");
    const tip = chip()!.getAttribute("title")!;
    expect(tip).toMatch(/Claude Code as the Iron-Proxy account “Work Max”/);
    expect(tip).toMatch(/fixed for this pane's life/);
    expect(tip).toMatch(/Launch \(the rocket\) → Claude Code → “as …”/);
  });

  it("this PC's login, with the daemon's note in the tooltip", () => {
    render(
      <PaneAccountChip
        paneId="t1"
        accounts={paneAccountsOf({
          accounts: {
            anthropic: {
              id: null,
              title: "this PC's login",
              source: "default",
              state: "default",
              note: "Iron-Proxy was not answering — this pane uses this PC's login",
            },
          },
        })}
      />,
    );
    expect(chip()!.textContent).toBe("Claude · this PC's login");
    expect(chip()!.getAttribute("title")).toContain(
      "Iron-Proxy was not answering — this pane uses this PC's login",
    );
  });

  it("amber with the LOCAL reset time when parked; amber needs sign-in; red missing", () => {
    const one = (a: Record<string, unknown>) => paneAccountBadge(paneAccountsOf({ accounts: { anthropic: a } }));
    const parked = one({ id: "a", title: "Work Max", source: "iron-proxy", state: "parked", until: PARKED_ISO });
    expect(parked).toMatchObject({ label: `Claude · Work Max · until ${localTime(PARKED_ISO)}`, tone: "amber" });
    expect(parked!.label).not.toContain(PARKED_ISO);
    // The object form of `state` reads the same.
    expect(
      one({
        id: "a",
        title: "Work Max",
        source: "iron-proxy",
        state: { status: "parked", until: PARKED_ISO, reason: "rate-limit" },
      }),
    ).toMatchObject({ label: `Claude · Work Max · until ${localTime(PARKED_ISO)}`, tone: "amber" });
    expect(one({ id: "a", title: "Work Max", source: "iron-proxy", state: "needs-sign-in" })).toMatchObject({
      label: "Claude · Work Max · needs sign-in",
      tone: "amber",
    });
    expect(one({ id: "a", title: "Work Max", source: "iron-proxy", state: "missing" })).toMatchObject({
      label: "Claude · Work Max · missing",
      tone: "red",
    });
    const off = one({ id: "a", title: "Work Max", source: "iron-proxy", state: "missing", reason: "disabled" });
    expect(off).toMatchObject({ label: "Claude · Work Max · switched off", tone: "red" });
    expect(off!.tooltip).toMatch(/switched off in Iron-Proxy/);
    // Iron-Proxy off / not read yet: the label alone, no alarm.
    expect(one({ id: "a", title: "Work Max", source: "iron-proxy", state: "unknown" })).toMatchObject({
      label: "Claude · Work Max",
      tone: "plain",
    });
  });

  it("anthropic first; the running CLI's own provider when the pane holds one", () => {
    const accounts = paneAccountsOf({
      accounts: {
        xai: { id: "x", title: "Grok main", source: "iron-proxy" },
        anthropic: { id: "a", title: "Work Max", source: "iron-proxy" },
      },
    });
    expect(chipProvider(accounts)).toBe("anthropic");
    expect(chipProvider(accounts, "grok")).toBe("xai");
    expect(chipProvider(accounts, "codex")).toBe("anthropic");
    render(<PaneAccountChip paneId="t9" accounts={accounts} agentCli="grok" />);
    expect(chip("t9")!.textContent).toBe("Grok · Grok main");
    expect(chip("t9")!.getAttribute("title")).toContain("Also on this pane: Claude · Work Max.");
  });

  it("the rail row shows the same short label", () => {
    const pane: RailPane = {
      id: "t1",
      label: "alpha",
      state: "idle",
      account: paneAccountBadge(
        paneAccountsOf({ accounts: { anthropic: { id: "a", title: "Work Max", source: "iron-proxy", state: "missing" } } }),
      ),
    };
    render(
      <PaneRail panes={[pane]} focusedId="t1" onFocus={vi.fn()} onClose={vi.fn()} onRename={vi.fn()} onNew={vi.fn()} />,
    );
    const el = screen.getByTestId("rail-account-t1");
    expect(el.textContent).toBe("Claude · Work Max · missing");
    expect(el.getAttribute("data-tone")).toBe("red");
  });
});

/* ---- 6. the Iron-Proxy card's "Open in Build" ------------------------------ */

describe("Iron-Proxy card: Open in Build", () => {
  async function mountCard(s: unknown) {
    hooks.responses["/iron-proxy"] = s;
    hooks.responses["/iron-proxy?discover=0"] = s;
    render(<IronProxyCard />);
    await waitFor(() => expect(document.getElementById("iron-proxy-card")).not.toBeNull());
  }

  it("POSTs …/open and goes to the Build page focused on the new pane", async () => {
    hooks.results["POST /iron-proxy/accounts/b/open"] = { terminal_id: "t 7", name: "Claude Code · Home Max" };
    await mountCard(ON);
    fireEvent.click(await screen.findByTestId("iron-proxy-open-b"));
    await waitFor(() => expect(hooks.push).toHaveBeenCalledWith("/terminals?focus=t%207"));
    expect(callsTo("POST", "/iron-proxy/accounts/b/open")).toHaveLength(1);
  });

  it("only CLI accounts of Claude/Codex/Grok get it; parked / signed-out / off say why", async () => {
    await mountCard(ON);
    await screen.findByTestId("iron-proxy-open-a");
    expect(screen.queryByTestId("iron-proxy-open-k")).toBeNull(); // API key
    expect((screen.getByTestId("iron-proxy-open-x") as HTMLButtonElement).disabled).toBe(false);
    const parked = screen.getByTestId("iron-proxy-open-c") as HTMLButtonElement;
    expect(parked.disabled).toBe(true);
    expect(parked.title).toMatch(/parked/);
    expect((screen.getByTestId("iron-proxy-open-d") as HTMLButtonElement).title).toMatch(/Sign this account in/);
    expect((screen.getByTestId("iron-proxy-open-e") as HTMLButtonElement).title).toMatch(/switched off/);
  });

  it("an answer without a pane shows a sentence and navigates nowhere", async () => {
    hooks.results["POST /iron-proxy/accounts/a/open"] = {};
    await mountCard(ON);
    fireEvent.click(await screen.findByTestId("iron-proxy-open-a"));
    await screen.findByText("The daemon did not open a Build pane.");
    expect(hooks.push).not.toHaveBeenCalled();
  });

  it("names the Handbook section", async () => {
    await mountCard(ON);
    expect(document.getElementById("iron-proxy-build-help")!.textContent).toContain(
      "“Several accounts in Build” in the Handbook",
    );
  });

  it("polls the LIGHT view; discovered logins come only from the slow full read (F3)", async () => {
    const found = { provider: "openai", home: "C:\\Users\\VR\\.codex", signed_in: true };
    const both = (status: Record<string, unknown>) => {
      hooks.responses["/iron-proxy?discover=0"] = snap(status, ACCOUNTS);
      hooks.responses["/iron-proxy"] = { ...snap(status, ACCOUNTS), discovered: [found] };
    };
    both({ enabled: false, running: false });
    render(<IronProxyCard />);
    await waitFor(() => expect(document.getElementById("iron-proxy-toggle")).not.toBeNull());
    // Switch on: while starting, the card re-reads every second — the LIGHT view only.
    both({ enabled: true, running: false });
    fireEvent.click(document.getElementById("iron-proxy-toggle")!);
    await waitFor(() => expect(callsTo("POST", "/iron-proxy/enable")).toHaveLength(1));
    const n = (p: string) => hooks.gets.filter((g) => g === p).length;
    // The full view is read twice in all: on mount, and by the one reload that
    // follows the switch. Wait for that reload to land before counting.
    await waitFor(() => expect(n("/iron-proxy")).toBe(2));
    const lightAfterReload = n("/iron-proxy?discover=0");
    await waitFor(() => expect(n("/iron-proxy?discover=0")).toBeGreaterThanOrEqual(lightAfterReload + 3), {
      timeout: 8000,
    });
    expect(n("/iron-proxy")).toBe(2); // the 1 s starting poll never reads the full view
    // Running now: accounts from the light read, the adopt row from the full one.
    both({});
    await waitFor(() => expect(document.getElementById("iron-proxy-account-a")).not.toBeNull(), {
      timeout: 4000,
    });
    expect(screen.getByTestId("iron-proxy-discovered-openai")).toBeTruthy();
  });

  it("an older daemon (404) still renders nothing", async () => {
    render(<IronProxyCard />); // /iron-proxy unmocked -> 404
    await waitFor(() => expect(hooks.gets).toContain("/iron-proxy"));
    await new Promise((r) => setTimeout(r, 20));
    expect(document.getElementById("iron-proxy-card")).toBeNull();
  });
});
