/**
 * v1.376.0 review — FOLDING IS ROUTING (the dashboard's half). The Python half
 * is tests/test_harness_model_fold_v1376.py; both read ONE fixture
 * (fixtures/harness-model-fold-v1376.json):
 *
 *  - the daemon's REAL answers (GET /models with a keyless Anthropic and
 *    OpenAI served by the signed-in CLIs, GET /build/accounts from the fake
 *    Iron-Proxy, the session views), written by the Python half;
 *  - `picks` / `picks_off`: the exact PATCH body EVERY pickable row of the
 *    IJ harness's model menu sends, recorded HERE by pressing each row of the
 *    real session screen (Iron-Proxy on, the session on the Work account; and
 *    Iron-Proxy off, a session with no account). The Python half replays each
 *    body through the real route and a real build turn and asserts the turn
 *    ran on the provider, model and account the row said.
 *
 * IJ_REGEN_HARNESS_FOLD=1 writes `picks` again after an INTENDED change.
 */
import React from "react";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    gets: {} as Record<string, unknown | ((path: string) => unknown)>,
    calls: [] as { method: string; path: string; body?: unknown }[],
    answers: {} as Record<string, unknown | ((path: string, body: unknown) => unknown)>,
    models: [] as Record<string, unknown>[],
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : api.answers[`${method} ${path}`];
    const value = typeof hit === "function" ? (hit as (p: string, b: unknown) => unknown)(path, body) : hit;
    if (value instanceof Error) return Promise.reject(value);
    if (value === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return value instanceof Promise ? value : Promise.resolve(value ?? {});
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
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: api.models, usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { companyMenu } from "@/lib/harnessModelMenu";
import { decodeAccounts } from "@/lib/buildAccounts";
import type { ModelOption } from "@/lib/types";

const FIXTURE = join(__dirname, "fixtures", "harness-model-fold-v1376.json");
type Pick = { way: string; account: string | null; row: string; model: string; body: Record<string, unknown> };
type Fixture = {
  models: Record<string, unknown>[];
  accounts: unknown;
  accounts_off: unknown;
  session_on_work: Record<string, unknown>;
  session_plain: Record<string, unknown>;
  picks: Pick[];
  picks_off: Pick[];
};
// Read with LF (the CRLF rule: a CI checkout may carry \r\n).
const read = (): Fixture => JSON.parse(readFileSync(FIXTURE, "utf8").replace(/\r\n/g, "\n")) as Fixture;

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

beforeEach(() => {
  api.calls = [];
  api.answers = {};
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const patches = () => api.calls.filter((c) => c.method === "PATCH");

async function openMenu(): Promise<HTMLElement> {
  const chip = await screen.findByTitle("Switch model");
  fireEvent.click(chip);
  const menu = await screen.findByTestId("model-menu");
  await within(menu).findAllByTestId("model-company");
  return menu;
}

const NOT_ROWS = new Set(["model-account-state", "model-account-name", "model-account-current", "model-account-warn", "model-account-meter"]);

/** Every enabled opener under the two company groups: an account row, or a
 *  way that has no accounts (its own row). */
function openers(menu: HTMLElement): { key: string; way: string; account: string | null }[] {
  const out: { key: string; way: string; account: string | null }[] = [];
  for (const way of Array.from(menu.querySelectorAll<HTMLElement>('[data-testid^="model-way-"]'))) {
    const provider = (way.getAttribute("data-testid") ?? "").replace("model-way-", "");
    if (way.tagName === "BUTTON") {
      if (!(way as HTMLButtonElement).disabled) out.push({ key: way.getAttribute("data-testid")!, way: provider, account: null });
      continue;
    }
    for (const a of Array.from(way.querySelectorAll<HTMLButtonElement>('button[data-testid^="model-account-"]'))) {
      const id = a.getAttribute("data-testid")!;
      if (NOT_ROWS.has(id)) continue;
      if (!a.disabled) out.push({ key: id, way: provider, account: id.replace("model-account-", "") });
    }
  }
  return out;
}

/** Press every model row under every opener; the PATCH body each sends. */
async function pressEverything(): Promise<Pick[]> {
  const first = await openMenu();
  const list = openers(first);
  fireEvent.click(screen.getByTitle("Switch model")); // close
  const out: Pick[] = [];
  for (const o of list) {
    let menu = await openMenu();
    fireEvent.click(within(menu).getByTestId(o.key));
    const count = within(await screen.findByTestId("model-sub")).getAllByRole("button").length;
    fireEvent.click(screen.getByTitle("Switch model"));
    for (let i = 0; i < count; i += 1) {
      menu = await openMenu();
      fireEvent.click(within(menu).getByTestId(o.key));
      const row = within(await screen.findByTestId("model-sub")).getAllByRole("button")[i];
      const before = patches().length;
      const words = row.textContent ?? "";
      fireEvent.click(row);
      await waitFor(() => expect(patches().length).toBe(before + 1));
      const body = patches()[before].body as Record<string, unknown>;
      out.push({ way: o.way, account: o.account, row: words, model: String(body.model ?? ""), body });
      await waitFor(() => expect(screen.queryByTestId("model-menu")).toBeNull());
    }
  }
  return out;
}

function stage(fx: Fixture, which: "on" | "off") {
  const session = which === "on" ? fx.session_on_work : fx.session_plain;
  api.models = fx.models;
  api.gets = {
    "/build/sessions/s1": session,
    "/build/accounts": which === "on" ? fx.accounts : fx.accounts_off,
  };
  // The session stays where it is: every press is measured from the same place.
  api.answers["PATCH /build/sessions/s1"] = () => session;
}

describe("every pickable row of the harness menu, as the daemon receives it", () => {
  for (const which of ["on", "off"] as const) {
    it(
      which === "on" ? "Iron-Proxy on, the session on Work" : "Iron-Proxy off, no account",
      async () => {
        const fx = read();
        stage(fx, which);
        render(
          <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
        );
        if (which === "on") await screen.findByTestId("account-chip");
        const got = await pressEverything();
        const key = which === "on" ? "picks" : "picks_off";
        if (process.env.IJ_REGEN_HARNESS_FOLD === "1") {
          writeFileSync(FIXTURE, `${JSON.stringify({ ...read(), [key]: got }, null, 2)}\n`, "utf8");
        }
        expect(got).toEqual(read()[key]);
        // No keyless API is a way of its own; its ids are reached through the CLI.
        expect(got.some((p) => p.way === "anthropic" || p.way === "openai")).toBe(false);
        for (const id of ["claude-opus-4-8", "claude-sonnet-4-6", "claude-fable-5", "gpt-4o"]) {
          expect(got.some((p) => p.model === id), id).toBe(true);
        }
      },
      60_000,
    );
  }

  it("a CLI's own rows come first — its plan, then its live picker — and the folded ones after them", () => {
    // The daemon lists the keyless API's block BEFORE the CLI's, so folding
    // in catalog order put Opus 4.8 / Sonnet 4.6 / claude-fable-5 above "Your
    // Claude plan" and the account's own picker (and gpt-4o above "Your
    // ChatGPT plan"). A way to reach a company reads as the CLI reads in the
    // chat: its own rows in the daemon's order, then what the fold added.
    const fx = read();
    const menu = companyMenu(fx.models as unknown as ModelOption[], decodeAccounts(fx.accounts), null, "");
    for (const cli of ["claude-cli", "codex-cli"]) {
      // v1.401.2 (moved on purpose): Claude Code's own-default row is folded
      // into the model it runs (the catalog marks Opus 5.5 `cli_default`);
      // Codex names no default, so its plan row stays first.
      const own = fx.models
        .filter((m) => m.provider === cli)
        .map((m) => m.model as string)
        .filter((m) => !(cli === "claude-cli" && m === "subscription"));
      const got = menu.models.filter((m) => m.provider === cli).map((m) => m.model);
      expect(got.slice(0, own.length), cli).toEqual(own);
      expect(got.length, cli).toBeGreaterThan(own.length);
    }
    // …and the menu draws that order: Claude Code's default model first
    // (v1.401.2: its plan row folded into it), Codex's plan row first.
    const claude = fx.picks.filter((p) => p.account === "c-work");
    expect(claude[0].model).toBe("claude-opus-5-5");
    expect(claude.some((p) => p.model === "subscription")).toBe(false);
    expect(fx.picks.filter((p) => p.account === "x-plus")[0].model).toBe("subscription");
    // A session saved on the plan row is marked on the model it runs.
    expect(companyMenu(fx.models as unknown as ModelOption[], decodeAccounts(fx.accounts), null, "claude-cli::subscription").choice).toBe(
      "claude-cli::claude-opus-5-5",
    );
  });

  it("the pairs are the folded list's, row for row; a pair stored before the change is marked as its fold", () => {
    const fx = read();
    const view = decodeAccounts(fx.accounts);
    const menu = companyMenu(fx.models as unknown as ModelOption[], view, null, "");
    const listed = new Set(menu.models.map((m) => `${m.provider}::${m.model}`));
    for (const p of fx.picks) {
      const provider =
        (p.body.provider as string | undefined) ?? (p.account?.startsWith("x-") ? "codex-cli" : "claude-cli");
      expect(listed.has(`${provider}::${p.model}`), `${provider}::${p.model}`).toBe(true);
    }
    expect(companyMenu(fx.models as unknown as ModelOption[], view, null, "anthropic::claude-opus-4-8").choice).toBe(
      "claude-cli::claude-opus-4-8",
    );
    expect(companyMenu(fx.models as unknown as ModelOption[], view, null, "openai::gpt-4o").choice).toBe(
      "codex-cli::gpt-4o",
    );
  });
});
