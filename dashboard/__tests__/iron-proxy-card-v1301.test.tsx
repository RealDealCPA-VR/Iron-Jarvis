/**
 * v1.301.0 — Iron-Proxy accounts on the Connections page.
 *
 * WHAT THESE TESTS GUARD — the WIRE and the honest states:
 *  - the switch POSTs /iron-proxy/enable | /disable and, after enabling, the
 *    card says "Starting…" until a GET says running (never "Running" on the
 *    strength of the POST alone); a failed start shows the daemon's sentence;
 *  - accounts are grouped by provider (Claude, Codex, Grok) in the order
 *    Iron-Proxy tries them, each with its state chip — a parked account shows
 *    its reset in LOCAL time, never the raw ISO string;
 *  - every action's request (sign in -> /terminals?focus=<pane>, unpark,
 *    move up/down with the provider's FULL id list, enable/disable, remove on
 *    the SECOND press only), adopt, add-then-sign-in;
 *  - a 404 (older daemon) renders nothing; a route error shows its `detail`.
 *
 * The REAL useApi/usePolledApi run here (through a mocked `@/lib/api`), so the
 * "waits for running" test exercises the actual re-poll.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const hooks = vi.hoisted(() => ({
  snap: null as unknown,
  getError: null as { status: number; message: string } | null,
  gets: 0,
  calls: [] as Array<{ method: string; path: string; body: unknown }>,
  fail: {} as Record<string, { status: number; message: string }>,
  results: {} as Record<string, unknown>,
  push: vi.fn(),
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
    const key = `${method} ${path}`;
    const f = hooks.fail[key];
    if (f) return Promise.reject(new ApiError(f.message, f.status));
    return Promise.resolve(key in hooks.results ? hooks.results[key] : {});
  };
  return {
    ApiError,
    API_BASE: "http://127.0.0.1:8787",
    ijToken: () => null,
    get: (path: string) => {
      // v1.302.0: the card's 5 s poll is the light view (`?discover=0`) and the
      // full view is read every 30 s — this fake daemon answers both alike.
      if (path === "/iron-proxy" || path === "/iron-proxy?discover=0") {
        hooks.gets += 1;
        if (hooks.getError) {
          return Promise.reject(new ApiError(hooks.getError.message, hooks.getError.status));
        }
        return Promise.resolve(hooks.snap);
      }
      return Promise.resolve({});
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
  usePathname: () => "/connections",
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: true }),
}));

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

vi.mock("@/components/connections/RestHookups", () => ({ RestHookups: () => null }));
vi.mock("@/components/BrandGlyph", () => ({ ProviderMark: () => null }));

import { IronProxyCard } from "@/components/connections/IronProxyCard";
import ConnectionsPage from "@/components/settings/pages/ConnectionsPage";

/* ------------------------------------------------------------------ fixtures */

function acct(over: Record<string, unknown>) {
  return {
    id: "a",
    title: "Work Claude",
    provider: "anthropic",
    lane: "cli",
    order: 0,
    enabled: true,
    state: { status: "ready", served: 0 },
    home_adopted: false,
    ...over,
  };
}

function snap(status: Record<string, unknown> = {}, rest: Record<string, unknown> = {}) {
  return {
    status: {
      enabled: true,
      running: true,
      owned: true,
      url: "http://127.0.0.1:4100",
      version: "0.6.0",
      error: null,
      bundled: true,
      ...status,
    },
    accounts: [],
    discovered: [],
    providers_used_by_jarvis: { anthropic: "claude-cli", openai: "codex-cli", xai: "grok-cli" },
    ...rest,
  };
}

/** Today at 15:05 LOCAL time, as the UTC ISO string the daemon sends. */
function todayAt1505Iso(): string {
  const d = new Date();
  d.setHours(15, 5, 0, 0);
  return d.toISOString();
}

const PARKED_ISO = todayAt1505Iso();

const MIXED = [
  acct({ id: "x1", title: "Grok main", provider: "xai", order: 0 }),
  acct({
    id: "c",
    title: "Second Claude",
    order: 1,
    state: {
      status: "parked",
      parkedUntil: PARKED_ISO,
      parkedReason: { kind: "quota-exhausted", message: "5-hour limit reached" },
      served: 4,
    },
  }),
  acct({
    id: "a",
    title: "Work Claude",
    order: 0,
    state: { status: "active", served: 12 },
    usage: { requests_5h: 12, requests_7d: 80, parks_7d: 2, minutes_left: 45 },
  }),
  acct({ id: "d", title: "Home Codex", provider: "openai", state: { status: "unauthenticated", served: 0 } }),
  acct({ id: "e", title: "Spare Claude", order: 2, enabled: false, state: { status: "disabled", served: 0 } }),
];

const card = () => document.getElementById("iron-proxy-card");
const row = (id: string) => {
  const el = document.getElementById(`iron-proxy-account-${id}`);
  if (!el) throw new Error(`no row for ${id}`);
  return el;
};
const callsTo = (method: string, path: string) =>
  hooks.calls.filter((c) => c.method === method && c.path === path);

async function mountWith(s: unknown) {
  hooks.snap = s;
  const utils = render(<IronProxyCard />);
  await waitFor(() => expect(card()).not.toBeNull());
  return utils;
}

beforeEach(() => {
  hooks.snap = null;
  hooks.getError = null;
  hooks.gets = 0;
  hooks.calls = [];
  hooks.fail = {};
  hooks.results = {};
  hooks.push.mockReset();
});

afterEach(() => {
  cleanup();
});

/* ------------------------------------------------------------------ on / off */

describe("the switch and the status line", () => {
  it("renders OFF: switch unchecked, plain sentence, no account list", async () => {
    await mountWith(snap({ enabled: false, running: false, owned: false, version: null }));
    const toggle = document.getElementById("iron-proxy-toggle")!;
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByTestId("iron-proxy-status").textContent).toMatch(
      /Off\. Iron Jarvis uses each CLI's own sign-in/,
    );
    expect(screen.queryByRole("button", { name: /Add account/ })).toBeNull();
  });

  it("renders ON: started by Iron Jarvis + version, or already running on this PC", async () => {
    await mountWith(snap());
    expect(document.getElementById("iron-proxy-toggle")!.getAttribute("aria-checked")).toBe("true");
    // v1.325.5: a remount first paints the CACHED answer (lib/apiCache) and
    // only then the fresh GET, so wait for the line itself, never the card.
    await waitFor(() =>
      expect(screen.getByTestId("iron-proxy-status").textContent).toBe(
        "Running· started by Iron Jarvis · v0.6.0",
      ),
    );
    cleanup();
    await mountWith(snap({ owned: false, version: "0.7.1" }));
    await waitFor(() =>
      expect(screen.getByTestId("iron-proxy-status").textContent).toMatch(
        /Running· already running on this PC · v0\.7\.1/,
      ),
    );
  });

  it("switching on POSTs enable and says Starting… until a GET says running", async () => {
    await mountWith(snap({ enabled: false, running: false, owned: false }));
    // The daemon persisted the flag; the child is still starting.
    hooks.snap = snap({ enabled: true, running: false, owned: false });
    const before = hooks.gets;
    fireEvent.click(document.getElementById("iron-proxy-toggle")!);
    await waitFor(() => expect(callsTo("POST", "/iron-proxy/enable")).toHaveLength(1));
    // A GET taken AFTER the POST still says not running -> still Starting….
    await waitFor(() => expect(hooks.gets).toBeGreaterThan(before));
    await waitFor(() =>
      expect(screen.getByTestId("iron-proxy-status").textContent).toMatch(/Starting…/),
    );
    expect(screen.getByTestId("iron-proxy-status").textContent).not.toMatch(/Running/);
    expect(document.getElementById("iron-proxy-toggle")!.getAttribute("aria-checked")).toBe("true");
    // Now the daemon reports it running: the next poll flips the line.
    hooks.snap = snap({ enabled: true, running: true, owned: true });
    await waitFor(
      () =>
        expect(screen.getByTestId("iron-proxy-status").textContent).toMatch(
          /Running· started by Iron Jarvis/,
        ),
      { timeout: 6000 },
    );
    expect(callsTo("POST", "/iron-proxy/disable")).toHaveLength(0);
  });

  it("switching off POSTs disable", async () => {
    await mountWith(snap());
    hooks.snap = snap({ enabled: false, running: false });
    fireEvent.click(document.getElementById("iron-proxy-toggle")!);
    await waitFor(() => expect(callsTo("POST", "/iron-proxy/disable")).toHaveLength(1));
    expect(callsTo("POST", "/iron-proxy/enable")).toHaveLength(0);
    await waitFor(() =>
      expect(screen.getByTestId("iron-proxy-status").textContent).toMatch(/^Off\. /),
    );
  });

  it("a failed enable shows the daemon's sentence and the switch goes back off", async () => {
    await mountWith(snap({ enabled: false, running: false, owned: false }));
    hooks.fail["POST /iron-proxy/enable"] = {
      status: 409,
      message: "Iron-Proxy could not start: Node.js was not found on this PC.",
    };
    fireEvent.click(document.getElementById("iron-proxy-toggle")!);
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toBe(
        "Iron-Proxy could not start: Node.js was not found on this PC.",
      ),
    );
    expect(document.getElementById("iron-proxy-toggle")!.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByTestId("iron-proxy-status").textContent).not.toMatch(/Starting/);
  });

  it("an enabled Iron-Proxy that is not running shows status.error", async () => {
    await mountWith(
      snap({ enabled: true, running: false, owned: false, error: "Iron-Proxy stopped: its port is taken." }),
    );
    expect(screen.getByRole("alert").textContent).toBe("Iron-Proxy stopped: its port is taken.");
    expect(screen.getByTestId("iron-proxy-status").textContent).not.toMatch(/Starting/);
  });
});

/* ------------------------------------------------------------------ accounts */

describe("accounts", () => {
  it("groups by provider (Claude, Codex, Grok) in Iron-Proxy's order", async () => {
    await mountWith(snap({}, { accounts: MIXED }));
    const groups = [...document.querySelectorAll("[data-testid^='iron-proxy-group-']")].map((g) =>
      g.getAttribute("data-testid"),
    );
    expect(groups).toEqual([
      "iron-proxy-group-anthropic",
      "iron-proxy-group-openai",
      "iron-proxy-group-xai",
    ]);
    const claude = screen.getByTestId("iron-proxy-group-anthropic");
    const ids = [...claude.querySelectorAll("[id^='iron-proxy-account-']")].map((e) => e.id);
    expect(ids).toEqual(["iron-proxy-account-a", "iron-proxy-account-c", "iron-proxy-account-e"]);
    expect(claude.textContent).toMatch(/Iron Jarvis's claude-cli runs as these, top first/);
    expect(screen.getByTestId("iron-proxy-group-xai").textContent).toMatch(/grok-cli/);
  });

  it("shows each state chip; a parked reset is in LOCAL time, never the ISO string", async () => {
    await mountWith(snap({}, { accounts: MIXED }));
    const chip = (id: string) => within(row(id)).getByTestId("iron-proxy-chip").textContent ?? "";
    expect(chip("a")).toBe("Active");
    expect(chip("c")).toMatch(/^Parked until 3:05\sPM — plan limit reached$/u);
    expect(chip("c")).not.toContain(PARKED_ISO);
    expect(chip("d")).toBe("Needs sign-in");
    expect(chip("e")).toBe("Off");
    expect(chip("x1")).toBe("Ready");
  });

  it("shows usage when present", async () => {
    await mountWith(snap({}, { accounts: MIXED }));
    expect(within(row("a")).getByTestId("iron-proxy-usage").textContent).toBe(
      "12 requests in 5 h · 80 in 7 days · parked 2× this week · 45 min left",
    );
    expect(within(row("c")).queryByTestId("iron-proxy-usage")).toBeNull();
  });

  it("an API-key account says Iron Jarvis uses its own keys and has no Sign in", async () => {
    await mountWith(
      snap({}, { accounts: [acct({ id: "k", title: "Team key", lane: "api" })] }),
    );
    expect(row("k").textContent).toMatch(/Iron Jarvis uses its own keys/);
    expect(within(row("k")).queryByRole("button", { name: /Sign in/ })).toBeNull();
  });

  it("explains the same-provider rule", async () => {
    await mountWith(snap());
    expect(card()!.textContent).toMatch(/next account of the SAME\s+provider, never to another provider/);
    expect(card()!.textContent).toMatch(/API-key accounts are managed in Iron-Proxy/);
  });
});

/* ------------------------------------------------------------------ actions */

describe("account actions", () => {
  it("Sign in POSTs signin and opens the Build page focused on the returned pane", async () => {
    await mountWith(snap({}, { accounts: MIXED }));
    hooks.results["POST /iron-proxy/accounts/d/signin"] = { terminal_id: "t 9", name: "Sign in: Home Codex" };
    fireEvent.click(within(row("d")).getByRole("button", { name: /Sign in/ }));
    await waitFor(() => expect(hooks.push).toHaveBeenCalledWith("/terminals?focus=t%209"));
    expect(callsTo("POST", "/iron-proxy/accounts/d/signin")).toHaveLength(1);
  });

  it("Unpark POSTs unpark (only parked accounts offer it)", async () => {
    await mountWith(snap({}, { accounts: MIXED }));
    expect(within(row("a")).queryByRole("button", { name: /Unpark/ })).toBeNull();
    fireEvent.click(within(row("c")).getByRole("button", { name: /Unpark/ }));
    await waitFor(() => expect(callsTo("POST", "/iron-proxy/accounts/c/unpark")).toHaveLength(1));
  });

  it("Move up/down POSTs reorder with the provider's FULL id list", async () => {
    await mountWith(snap({}, { accounts: MIXED }));
    expect(within(row("a")).getByRole("button", { name: "Move Work Claude up" })).toBeDisabled();
    expect(within(row("e")).getByRole("button", { name: "Move Spare Claude down" })).toBeDisabled();
    fireEvent.click(within(row("a")).getByRole("button", { name: "Move Work Claude down" }));
    await waitFor(() =>
      expect(callsTo("POST", "/iron-proxy/accounts/reorder").map((c) => c.body)).toEqual([
        { provider: "anthropic", ids: ["c", "a", "e"] },
      ]),
    );
    // Wait for the row to be usable again before the next press.
    await waitFor(() =>
      expect(within(row("e")).getByRole("button", { name: "Move Spare Claude up" })).toBeEnabled(),
    );
    fireEvent.click(within(row("e")).getByRole("button", { name: "Move Spare Claude up" }));
    await waitFor(() =>
      expect(callsTo("POST", "/iron-proxy/accounts/reorder").map((c) => c.body)[1]).toEqual({
        provider: "anthropic",
        ids: ["a", "e", "c"],
      }),
    );
  });

  it("Disable / Enable PATCH the account", async () => {
    await mountWith(snap({}, { accounts: MIXED }));
    fireEvent.click(within(row("a")).getByRole("button", { name: /Disable/ }));
    await waitFor(() =>
      expect(callsTo("PATCH", "/iron-proxy/accounts/a").map((c) => c.body)).toEqual([{ enabled: false }]),
    );
    await waitFor(() => expect(within(row("e")).getByRole("button", { name: /Enable/ })).toBeEnabled());
    fireEvent.click(within(row("e")).getByRole("button", { name: /Enable/ }));
    await waitFor(() =>
      expect(callsTo("PATCH", "/iron-proxy/accounts/e").map((c) => c.body)).toEqual([{ enabled: true }]),
    );
  });

  it("Remove needs TWO presses; only the second DELETEs", async () => {
    await mountWith(snap({}, { accounts: MIXED }));
    fireEvent.click(within(row("a")).getByRole("button", { name: "Remove" }));
    expect(within(row("a")).getByRole("button", { name: "Press again to remove" })).toBeTruthy();
    // Give any wrongly-sent request a chance to land before asserting none did.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(callsTo("DELETE", "/iron-proxy/accounts/a")).toHaveLength(0);
    fireEvent.click(within(row("a")).getByRole("button", { name: "Press again to remove" }));
    await waitFor(() => expect(callsTo("DELETE", "/iron-proxy/accounts/a")).toHaveLength(1));
  });

  it("a route error shows the daemon's detail sentence", async () => {
    await mountWith(snap({}, { accounts: MIXED }));
    hooks.fail["POST /iron-proxy/accounts/c/unpark"] = {
      status: 502,
      message: "Iron-Proxy says that account is not parked any more.",
    };
    fireEvent.click(within(row("c")).getByRole("button", { name: /Unpark/ }));
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toBe(
        "Iron-Proxy says that account is not parked any more.",
      ),
    );
  });
});

/* ------------------------------------------------------------ adopt + add */

describe("adopt and add", () => {
  it("offers 'Use this PC's login' only for logins no account uses, and POSTs adopt", async () => {
    await mountWith(
      snap(
        {},
        {
          accounts: [acct({ id: "d", title: "Home Codex", provider: "openai" })],
          discovered: [
            { provider: "anthropic", home: "C:\\Users\\me\\.claude", title: "Claude (existing login)", signed_in: true },
            { provider: "openai", home: "C:\\Users\\me\\.codex", signed_in: true, adopted_profile_id: "d" },
          ],
        },
      ),
    );
    expect(screen.queryByTestId("iron-proxy-discovered-openai")).toBeNull();
    const offer = screen.getByTestId("iron-proxy-discovered-anthropic");
    fireEvent.click(within(offer).getByRole("button", { name: /Use this PC's login/ }));
    await waitFor(() =>
      expect(callsTo("POST", "/iron-proxy/accounts/adopt").map((c) => c.body)).toEqual([
        { provider: "anthropic", home: "C:\\Users\\me\\.claude", title: "Claude (existing login)" },
      ]),
    );
  });

  it("Add account POSTs provider + title, then offers Sign in for the new account", async () => {
    await mountWith(snap());
    hooks.results["POST /iron-proxy/accounts"] = {
      id: "new1",
      title: "Night Codex",
      provider: "openai",
      lane: "cli",
    };
    hooks.results["POST /iron-proxy/accounts/new1/signin"] = { terminal_id: "t-42", name: "Sign in: Night Codex" };
    fireEvent.change(screen.getByLabelText("Provider"), { target: { value: "openai" } });
    fireEvent.change(screen.getByLabelText("Account name"), { target: { value: "  Night Codex " } });
    const add = screen.getByRole("button", { name: /Add account/ });
    await waitFor(() => expect(add).toBeEnabled());
    fireEvent.click(add);
    await waitFor(() =>
      expect(callsTo("POST", "/iron-proxy/accounts").map((c) => c.body)).toEqual([
        { provider: "openai", title: "Night Codex" },
      ]),
    );
    const signNow = await screen.findByRole("button", { name: /Sign in now/ });
    await waitFor(() => expect(signNow).toBeEnabled());
    fireEvent.click(signNow);
    await waitFor(() => expect(hooks.push).toHaveBeenCalledWith("/terminals?focus=t-42"));
    expect(callsTo("POST", "/iron-proxy/accounts/new1/signin")).toHaveLength(1);
  });

  it("the provider select offers Claude, Codex and Grok", async () => {
    await mountWith(snap());
    const opts = [...(screen.getByLabelText("Provider") as HTMLSelectElement).options].map((o) => [
      o.value,
      o.textContent,
    ]);
    expect(opts).toEqual([
      ["anthropic", "Claude"],
      ["openai", "Codex"],
      ["xai", "Grok"],
    ]);
  });
});

/* ------------------------------------------------------- older daemon + page */

describe("older daemon, load errors, and the page", () => {
  async function settle() {
    await waitFor(() => expect(hooks.gets).toBeGreaterThan(0));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
  }

  it("renders NOTHING when the daemon answers 404", async () => {
    hooks.getError = { status: 404, message: "Not Found" };
    const { container } = render(<IronProxyCard />);
    await settle();
    expect(container.innerHTML).toBe("");
  });

  it("a 500 on load shows the error sentence (not an empty card)", async () => {
    hooks.getError = { status: 500, message: "Iron-Proxy status is unavailable right now." };
    render(<IronProxyCard />);
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toBe(
        "Could not load Iron-Proxy: Iron-Proxy status is unavailable right now.",
      ),
    );
  });

  it("an answer without a status (a stub daemon) renders nothing", async () => {
    hooks.snap = {};
    const { container } = render(<IronProxyCard />);
    await settle();
    expect(container.innerHTML).toBe("");
  });

  it("the Connections page mounts the card", async () => {
    hooks.snap = snap({}, { accounts: MIXED });
    render(<ConnectionsPage />);
    await waitFor(() => expect(document.getElementById("iron-proxy-account-a")).not.toBeNull());
    expect(card()).not.toBeNull();
  });
});
