/**
 * v1.393.0 (the long-jobs plan's Phase 4, decision 4) — ACCOUNT GROUPS on the
 * Iron-Proxy card.
 *
 *  - each Claude Code / Codex subscription account row has a Group choice
 *    (No group, each group, New group…); a Grok account and an API-key row
 *    have none (a coding session never runs on them);
 *  - a pick saves the WHOLE setting once through the one settings writer
 *    (`PUT /settings/values {account_groups}`) — never through Iron-Proxy —
 *    with one group per account and an emptied group dropped;
 *  - the card says what a group is, in the schema's own words;
 *  - an older Iron Jarvis (no `account_groups` in its answer) shows no
 *    choice at all.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";

const hooks = vi.hoisted(() => ({
  snap: null as unknown,
  calls: [] as Array<{ method: string; path: string; body: unknown }>,
  fail: {} as Record<string, { status: number; message: string }>,
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    cancelled = false;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  const send = (method: string, path: string, body?: unknown) => {
    hooks.calls.push({ method, path, body });
    const f = hooks.fail[`${method} ${path}`];
    if (f) return Promise.reject(new ApiError(f.message, f.status));
    return Promise.resolve({});
  };
  return {
    ApiError,
    API_BASE: "http://127.0.0.1:8787",
    ijToken: () => null,
    get: (p: string) =>
      p === "/iron-proxy" || p === "/iron-proxy?discover=0" ? Promise.resolve(hooks.snap) : Promise.resolve({}),
    post: (p: string, b?: unknown) => send("POST", p, b),
    patch: (p: string, b?: unknown) => send("PATCH", p, b),
    put: (p: string, b?: unknown) => send("PUT", p, b),
    del: (p: string) => send("DELETE", p),
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/connections",
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/components/BrandGlyph", () => ({ ProviderMark: () => null }));

import { IronProxyCard } from "@/components/connections/IronProxyCard";
import { GROUP_HELP, decodeGroups, groupOf, withAccountInGroup } from "@/lib/ironProxy";

function acct(over: Record<string, unknown>) {
  return {
    id: "a",
    title: "Work Claude",
    provider: "anthropic",
    lane: "cli",
    order: 0,
    enabled: true,
    state: { status: "ready", served: 0 },
    ...over,
  };
}

const ACCOUNTS = [
  acct({ id: "tsi", title: "TSI", order: 0 }),
  acct({ id: "home", title: "Home Claude", order: 1 }),
  acct({ id: "cx", title: "Work Codex", provider: "openai" }),
  acct({ id: "grok", title: "Grok main", provider: "xai" }),
  acct({ id: "key", title: "Claude key", lane: "api", order: 2 }),
];

function snap(groups: Record<string, string[]> | undefined) {
  return {
    status: { enabled: true, running: true, owned: true, url: "", version: "0.6.0", error: null, bundled: true },
    accounts: ACCOUNTS,
    discovered: [],
    providers_used_by_jarvis: { anthropic: "claude-cli", openai: "codex-cli", xai: "grok-cli" },
    ...(groups === undefined ? {} : { account_groups: groups }),
  };
}

const row = (id: string) => document.getElementById(`iron-proxy-account-${id}`)!;
const puts = () => hooks.calls.filter((c) => c.method === "PUT" && c.path === "/settings/values");

async function mount(groups: Record<string, string[]> | undefined) {
  hooks.snap = snap(groups);
  render(<IronProxyCard />);
  await waitFor(() => expect(document.getElementById("iron-proxy-account-tsi")).not.toBeNull());
}

beforeEach(() => {
  hooks.snap = null;
  hooks.calls = [];
  hooks.fail = {};
});
afterEach(() => cleanup());

describe("the Group choice", () => {
  it("is on every Claude Code and Codex subscription row, and on no other", async () => {
    await mount({ Work: ["tsi", "cx"] });
    for (const id of ["tsi", "home", "cx"]) {
      expect(within(row(id)).getByTestId(`iron-proxy-group-${id}`)).toBeInTheDocument();
    }
    for (const id of ["grok", "key"]) {
      expect(within(row(id)).queryByTestId(`iron-proxy-group-${id}`)).toBeNull();
    }
    expect((screen.getByTestId("iron-proxy-group-tsi") as HTMLSelectElement).value).toBe("Work");
    expect((screen.getByTestId("iron-proxy-group-home") as HTMLSelectElement).value).toBe("");
    const options = within(screen.getByTestId("iron-proxy-group-home")).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["No group", "Work", "New group…"]);
    expect(screen.getByTestId("iron-proxy-group-help").textContent).toBe(GROUP_HELP);
  });

  it("a pick saves the whole setting once through the one writer — never Iron-Proxy", async () => {
    await mount({ Work: ["tsi"] });
    fireEvent.change(screen.getByTestId("iron-proxy-group-home"), { target: { value: "Work" } });
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].body).toEqual({ values: { account_groups: { Work: ["tsi", "home"] } } });
    expect(hooks.calls.some((c) => c.path.startsWith("/iron-proxy/accounts"))).toBe(false);
    expect(await screen.findByText("Home Claude is in the “Work” group.")).toBeInTheDocument();
  });

  it("No group takes the account out, and an emptied group goes", async () => {
    await mount({ Work: ["tsi"], Home: ["home"] });
    fireEvent.change(screen.getByTestId("iron-proxy-group-tsi"), { target: { value: "" } });
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].body).toEqual({ values: { account_groups: { Home: ["home"] } } });
  });

  it("New group… asks for its name, then saves it", async () => {
    await mount({});
    fireEvent.change(screen.getByTestId("iron-proxy-group-tsi"), { target: { value: "__new__" } });
    const input = await screen.findByTestId("iron-proxy-group-name-tsi");
    fireEvent.change(input, { target: { value: "  Work  " } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0].body).toEqual({ values: { account_groups: { Work: ["tsi"] } } });
  });

  it("the daemon's refusal is said in its own words", async () => {
    hooks.fail["PUT /settings/values"] = {
      status: 400,
      message: "Account groups: there is no Claude Code or Codex account “home” in Iron-Proxy.",
    };
    await mount({});
    fireEvent.change(screen.getByTestId("iron-proxy-group-home"), { target: { value: "__new__" } });
    const input = await screen.findByTestId("iron-proxy-group-name-home");
    fireEvent.change(input, { target: { value: "Work" } });
    fireEvent.submit(input.closest("form")!);
    expect(await screen.findByText(/no Claude Code or Codex account “home”/)).toBeInTheDocument();
  });

  it("an older Iron Jarvis without groups shows no choice", async () => {
    await mount(undefined);
    expect(screen.queryByTestId("iron-proxy-group-tsi")).toBeNull();
    expect(screen.queryByTestId("iron-proxy-group-help")).toBeNull();
  });
});

describe("the groups as data", () => {
  it("one group per account; a name typed in another case joins the existing group", () => {
    const g = { Work: ["tsi"], Home: ["home"] };
    expect(withAccountInGroup(g, "home", "work")).toEqual({ Work: ["tsi", "home"] });
    expect(withAccountInGroup(g, "tsi", "Home")).toEqual({ Home: ["home", "tsi"] });
    expect(withAccountInGroup(g, "tsi", null)).toEqual({ Home: ["home"] });
    expect(groupOf(g, "home")).toBe("Home");
    expect(groupOf(g, "nobody")).toBe("");
    expect(decodeGroups({ Work: ["a", 3, ""], "": ["b"], Bad: "c" })).toEqual({ Work: ["a"] });
  });

  it("says what a group is in the settings schema's own words", () => {
    const src = readFileSync(
      path.join(__dirname, "..", "..", "src", "iron_jarvis", "settings", "schema.py"),
      "utf8",
    ).replace(/\r\n/g, "\n");
    const [first, second] = GROUP_HELP.split("limit. ");
    expect(src).toContain(`"${first}limit. "`);
    expect(src).toContain(`"${second}"`);
  });
});
