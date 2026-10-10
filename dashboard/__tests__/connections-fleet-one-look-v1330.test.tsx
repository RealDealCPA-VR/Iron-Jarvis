/**
 * v1.330.0 — calm chat wave 11, M3: Connections and Fleet, one look per row.
 *
 * The wave-10 audit found two looks side by side:
 *  1. The Iron-Proxy card's Sign in, Open in Build, Unpark, Disable and the
 *     up / down arrows were bordered btn-ghost boxes beside the borderless
 *     calm Remove; the endpoint rows' hairline "Measure" chip-button sat
 *     beside the borderless Delete; Test / Make default / Disable elsewhere on
 *     Connections were bordered beside the calm Delete / Disconnect. Every
 *     secondary action is now ONE calm action (`CALM_ACTION`): no border, no
 *     fill until hover, a focus ring, 28px tall at least, the same geometry as
 *     the calm ConfirmButton. Each card keeps its one clear primary (Connect,
 *     Log in, Save; a signed-out account's Sign in stays the accent outline).
 *     The endpoint tags are the calm Badge's shell exactly (no hairline).
 *  2. The Fleet page still drew default (bordered) Badges and a default
 *     ConfirmButton Remove. They are calm now and the page is in the calm
 *     guard (connections-fleet-calm-v1329).
 *  3. Dash asides: the Iron-Proxy parked chip ("Parked until 7:24 AM — plan
 *     limit reached") and the Notifications blurb in Settings.
 */

import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import ts from "typescript";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";

/* ------------------------------------------------------------------ mocks */

const hooks = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    responses: {} as Record<string, unknown>,
    defaultProvider: "mock",
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: hooks.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  sseUrl: (p: string) => p,
  wsUrl: (p: string) => p,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  get: vi.fn((p: string) => Promise.resolve(hooks.responses[p] ?? {})),
  post: vi.fn(() => Promise.resolve({ ok: true })),
  put: vi.fn(() => Promise.resolve({})),
  patch: vi.fn(() => Promise.resolve({})),
  del: vi.fn(() => Promise.resolve({})),
}));
vi.mock("@/lib/useApi", () => {
  const read = (p: string | null) => ({
    data: p ? (hooks.responses[p] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  });
  return { useApi: read, usePolledApi: read };
});
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    health: { default_provider: hooks.defaultProvider, providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: () => {},
  }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {}, prefetch: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial", "animate", "exit", "transition", "variants", "layout",
    "whileHover", "whileTap", "whileInView", "viewport",
  ]);
  const cache = new Map<string, unknown>();
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const motion = new Proxy({} as Record<string, unknown>, {
    get: (_t, tag) => {
      const key = String(tag);
      if (!cache.has(key)) cache.set(key, tagFor(key));
      return cache.get(key);
    },
  });
  return {
    m: motion,
    motion,
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
  };
});
vi.mock("@/components/connections/RestHookups", () => ({ RestHookups: () => null }));
vi.mock("@/components/connections/EnvelopeCard", () => ({
  EnvelopeRowControls: () => null,
  MeasuredEndpoints: () => null,
}));
vi.mock("@/components/BrandGlyph", () => ({ ProviderMark: () => null, BrandGlyph: () => null }));

import { CALM_BADGE, CALM_CONFIRM } from "@/components/ui";
import {
  CALM_ACTION,
  CALM_ICON_ACTION,
  ENDPOINT_CHIP,
  ENDPOINT_CHIP_BUTTON,
} from "@/components/connections/endpointChip";
import { IronProxyCard } from "@/components/connections/IronProxyCard";
import ConnectionsPage from "@/components/settings/pages/ConnectionsPage";
import FleetPage from "@/app/fleet/page";
import { stateChip, type IronProxyAccount } from "@/lib/ironProxy";
import { asides, copyPieces } from "./helpers/dashGuard";

beforeEach(() => {
  hooks.responses = {};
  hooks.defaultProvider = "mock";
});
afterEach(() => cleanup());

/* ---------------------------------------------------------------- helpers */

const tokens = (el: Element | string) =>
  (typeof el === "string" ? el : el.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);
/** A border of any kind: `border`, `border-x`, `border-white/10`,
 *  `hover:border-…`. (`rounded-*` is not a border.) */
const BORDER = /^(?:[a-z-]+:)*border(?:-|$)/;
const borders = (el: Element | string) => tokens(el).filter((c) => BORDER.test(c));
const LITERAL_HUE =
  /(?:emerald|amber|rose|red|green|yellow|cyan|sky|blue|violet|purple|pink|orange|lime|teal|indigo|fuchsia|slate|gray|stone|neutral)-\d{2,3}/;

/** The calm action, read off a rendered button. */
function expectCalmAction(btn: HTMLElement, what: string) {
  expect(borders(btn), what).toEqual([]);
  expect(tokens(btn), what).not.toContain("btn-ghost");
  expect(tokens(btn), what).toContain("hover:bg-white/[0.06]");
  expect(tokens(btn), what).toContain("focus-visible:ring-1");
  expect(tokens(btn), what).toContain("min-h-7");
}

/* ================================================ 1. the shared calm look */

describe("one calm look: the shared strings", () => {
  it("the endpoint tag IS the calm Badge's shell (no hairline, no fill)", () => {
    expect(ENDPOINT_CHIP).toBe(CALM_BADGE);
    expect(borders(ENDPOINT_CHIP)).toEqual([]);
    expect(tokens(ENDPOINT_CHIP).filter((c) => /^bg-/.test(c))).toEqual([]);
  });

  it("a row button is the calm action, which shares the calm ConfirmButton's geometry", () => {
    expect(ENDPOINT_CHIP_BUTTON).toBe(CALM_ACTION);
    for (const tok of ["inline-flex", "min-h-7", "items-center", "gap-1.5", "rounded-lg", "px-2.5", "py-1", "text-xs", "font-medium"]) {
      expect(tokens(CALM_ACTION), tok).toContain(tok);
      expect(tokens(CALM_CONFIRM.base), tok).toContain(tok);
    }
  });

  it.each([
    ["CALM_ACTION", CALM_ACTION],
    ["CALM_ICON_ACTION", CALM_ICON_ACTION],
  ])("%s: no border, fills on hover, a focus ring, theme tokens, whole pixels", (_name, cls) => {
    expect(borders(cls)).toEqual([]);
    expect(tokens(cls)).toContain("hover:bg-white/[0.06]");
    expect(tokens(cls)).toContain("focus-visible:ring-1");
    expect(tokens(cls)).toContain("focus-visible:ring-accent/50");
    // No fill at rest: only hover / disabled variants touch the background.
    expect(tokens(cls).filter((c) => /^bg-/.test(c))).toEqual([]);
    expect(cls).not.toMatch(LITERAL_HUE);
    expect(cls).not.toMatch(/text-\[\d+\.5px\]/);
  });

  it("the icon action is a 28px square a finger can hit", () => {
    expect(tokens(CALM_ICON_ACTION)).toEqual(expect.arrayContaining(["h-7", "w-7"]));
  });
});

/* ===================================================== 2. source guard */

const DASH_ROOT = path.join(__dirname, "..");
const readRel = (rel: string) => readFileSync(path.join(DASH_ROOT, rel), "utf8").replace(/\r\n/g, "\n");
const CONN_DIR = path.join(DASH_ROOT, "components", "connections");
const CALM_FILES = [
  "components/settings/pages/ConnectionsPage.tsx",
  ...readdirSync(CONN_DIR)
    .filter((f) => f.endsWith(".tsx"))
    .map((f) => `components/connections/${f}`),
  "app/fleet/page.tsx",
];

/** Every <button> in a file whose class has a border or btn-ghost and is not
 *  a primary (btn-soft / btn-accent) or a switch. The class is read from every
 *  string in the className expression, so a ternary's two branches both count. */
function boxedButtons(rel: string, src: string): string[] {
  const file = ts.createSourceFile(rel, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: string[] = [];
  const visit = (n: ts.Node) => {
    if ((ts.isJsxOpeningElement(n) || ts.isJsxSelfClosingElement(n)) && n.tagName.getText(file) === "button") {
      const attrs = n.attributes.properties.filter(ts.isJsxAttribute);
      const role = attrs.find((a) => a.name.getText(file) === "role")?.initializer;
      const isSwitch = !!role && ts.isStringLiteral(role) && role.text === "switch";
      const cls = attrs.find((a) => a.name.getText(file) === "className")?.initializer;
      const pieces: string[] = [];
      const collect = (x: ts.Node) => {
        if (ts.isStringLiteral(x) || ts.isNoSubstitutionTemplateLiteral(x)) pieces.push(x.text);
        else if (ts.isTemplateHead(x) || ts.isTemplateMiddle(x) || ts.isTemplateTail(x)) pieces.push(x.text);
        ts.forEachChild(x, collect);
      };
      if (cls) collect(cls);
      for (const piece of pieces) {
        const t = tokens(piece);
        const primary = t.includes("btn-soft") || t.includes("btn-accent");
        if (t.includes("btn-ghost") || (!primary && !isSwitch && t.some((c) => BORDER.test(c)))) {
          const line = file.getLineAndCharacterOfPosition(n.getStart(file)).line + 1;
          out.push(`${rel}:${line} ${piece.trim().slice(0, 60)}`);
        }
      }
    }
    ts.forEachChild(n, visit);
  };
  visit(file);
  return out;
}

describe("source guard: no bordered secondary button on Connections or Fleet", () => {
  it.each(CALM_FILES)("%s has no btn-ghost box and no hand-rolled bordered button", (rel) => {
    expect(boxedButtons(rel, readRel(rel))).toEqual([]);
  });

  it("the guard reads every file, finds the buttons, and catches a boxed one (anti-vacuity)", () => {
    for (const f of ["IronProxyCard.tsx", "RestHookups.tsx", "EnvelopeCard.tsx"]) {
      expect(CALM_FILES).toContain(`components/connections/${f}`);
    }
    // The calm action really is what those files use now.
    for (const rel of ["components/connections/IronProxyCard.tsx", "components/connections/RestHookups.tsx", "app/fleet/page.tsx", "components/settings/pages/ConnectionsPage.tsx"]) {
      expect(readRel(rel), rel).toMatch(/className=\{CALM_ACTION\}|\$\{CALM_ACTION\}/);
    }
    const probe = [
      'const a = <button className="btn-ghost px-2.5 py-1 text-xs">x</button>;',
      'const b = <button className={ok ? "btn-soft" : "btn-ghost"}>x</button>;',
      'const c = <button className="rounded-md border border-white/10 px-2.5">x</button>;',
      "const d = <button className={`${x} hover:border-white/20`}>x</button>;",
      "const e = <button className={CALM_ACTION}>x</button>;",
      'const f = <button className="btn-soft min-h-7 px-2.5 py-1 text-xs">x</button>;',
      'const g = <button role="switch" className={`border ${on ? "border-accent/40" : "border-white/10"}`} />;',
      'const h = <span className="border border-white/10">not a button</span>;',
    ].join("\n");
    expect(boxedButtons("probe.tsx", probe).map((s) => s.split(" ")[0])).toEqual([
      "probe.tsx:1",
      "probe.tsx:2",
      "probe.tsx:3",
      "probe.tsx:4",
    ]);
  });
});

/* =============================================== 3. the Iron-Proxy card */

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

const ACCOUNTS = [
  acct({ id: "a", title: "Work Claude", order: 0, state: { status: "active", served: 3 } }),
  acct({
    id: "c",
    title: "Second Claude",
    order: 1,
    state: {
      status: "parked",
      parkedUntil: "2026-10-10T11:24:00.000Z",
      parkedReason: { kind: "quota-exhausted", message: "5-hour limit reached" },
      served: 4,
    },
  }),
  acct({ id: "d", title: "Home Codex", provider: "openai", state: { status: "unauthenticated", served: 0 } }),
];

function ironSnap() {
  return {
    status: {
      enabled: true,
      running: true,
      owned: true,
      url: "http://127.0.0.1:4100",
      version: "0.6.0",
      error: null,
      bundled: true,
    },
    accounts: ACCOUNTS,
    discovered: [{ provider: "xai", home: "C:/Users/someone/.grok", signed_in: true, title: "" }],
    providers_used_by_jarvis: { anthropic: "claude-cli", openai: "codex-cli", xai: "grok-cli" },
  };
}

async function mountIron() {
  hooks.responses["/iron-proxy"] = ironSnap();
  hooks.responses["/iron-proxy?discover=0"] = ironSnap();
  render(<IronProxyCard />);
  await waitFor(() => expect(document.getElementById("iron-proxy-account-c")).not.toBeNull());
}
const accountRow = (id: string) => document.getElementById(`iron-proxy-account-${id}`) as HTMLElement;

describe("Iron-Proxy card: one look per account row", () => {
  it("Sign in, Open in Build, Unpark, the arrows and Disable are calm, beside the calm Remove", async () => {
    await mountIron();
    const parked = accountRow("c");
    const buttons = within(parked).getAllByRole("button");
    // Sign in, Open in Build, Unpark, up, down, Disable, Remove.
    expect(buttons.map((b) => b.getAttribute("aria-label") ?? b.textContent?.trim())).toEqual([
      "Sign in",
      "Open in Build",
      "Unpark",
      "Move Second Claude up",
      "Move Second Claude down",
      "Disable",
      "Remove",
    ]);
    for (const b of buttons) expect(borders(b), b.textContent ?? "").toEqual([]);
    for (const name of [/^Sign in$/, /Open in Build/, /Unpark/, /Disable/]) {
      expectCalmAction(within(parked).getByRole("button", { name }), String(name));
    }
    // The arrows: a 28px square each, still labelled for a screen reader.
    for (const name of ["Move Second Claude up", "Move Second Claude down"]) {
      const arrow = within(parked).getByRole("button", { name });
      expect(tokens(arrow)).toEqual(expect.arrayContaining(["h-7", "w-7", "hover:bg-white/[0.06]", "focus-visible:ring-1"]));
      expect(tokens(arrow)).not.toContain("btn-ghost");
    }
    expect(within(parked).getByRole("button", { name: "Remove" }).getAttribute("data-confirm-variant")).toBe("calm");
    // Test ids and focus are kept: Open in Build is still the same button
    // (disabled on a parked account; focusable on an active one).
    expect((within(parked).getByTestId("iron-proxy-open-c") as HTMLButtonElement).disabled).toBe(true);
    const open = within(accountRow("a")).getByTestId("iron-proxy-open-a");
    expect((open as HTMLButtonElement).disabled).toBe(false);
    open.focus();
    expect(document.activeElement).toBe(open);
  });

  it("a signed-out account keeps ONE primary: Sign in is the accent outline, Check again is calm", async () => {
    await mountIron();
    const out = accountRow("d");
    const signIn = within(out).getByRole("button", { name: /^Sign in$/ });
    expect(tokens(signIn)).toContain("btn-soft");
    expect(tokens(signIn)).toContain("min-h-7");
    expectCalmAction(within(out).getByTestId("iron-proxy-check-d"), "Check again");
    // Only that one primary in the row.
    expect(out.querySelectorAll(".btn-soft, .btn-accent")).toHaveLength(1);
    // A signed-in account has no primary at all: its Sign in is calm.
    expectCalmAction(within(accountRow("a")).getByRole("button", { name: /^Sign in$/ }), "Sign in (signed in)");
    expect(accountRow("a").querySelectorAll(".btn-soft, .btn-accent")).toHaveLength(0);
  });

  it("Use this PC's login and Add account are calm too", async () => {
    await mountIron();
    const adopt = within(screen.getByTestId("iron-proxy-discovered-xai")).getByRole("button", { name: /Use this PC's login/ });
    expectCalmAction(adopt, "adopt");
    expectCalmAction(screen.getByRole("button", { name: /Add account/ }), "Add account");
  });

  it("the parked chip reads its reason after a middle dot, never a dash aside", async () => {
    await mountIron();
    const chip = within(accountRow("c")).getByTestId("iron-proxy-chip").textContent ?? "";
    expect(chip).toMatch(/^Parked until \d{1,2}:\d{2}\s[AP]M · plan limit reached$/u);
    expect(chip).not.toMatch(/[—–]/);
  });
});

/* ================================================== 4. Settings > Connections */

const card = (provider: string, over: Record<string, unknown> = {}) => ({
  provider,
  display_name: `Card ${provider}`,
  method: "api_key",
  supports_oauth: false,
  supports_api_key: true,
  oauth_help: "",
  connected: false,
  status: "disconnected",
  account: "",
  source: "",
  scopes: [],
  ...over,
});

const node = (id: string, label: string, extra: Record<string, unknown> = {}) => ({
  id,
  label,
  base_url: `http://${id}.test:8000`,
  kind: "openai-compat",
  source: "user",
  enabled: true,
  routable: true,
  default_model: "fable-1",
  api_key_name: "",
  tool_use: null,
  vision: null,
  protocol: "openai",
  ...extra,
});
const fleetRow = (n: ReturnType<typeof node>, extra: Record<string, unknown> = {}) => ({
  node: n,
  status: "online",
  evidence: "direct",
  latency_ms: 14,
  error: "",
  metrics_supported: true,
  metrics_reason: "",
  metrics: { requests_running: 0, requests_waiting: 0 },
  rates: null,
  models: [],
  children: [],
  sampled_at: 512.5,
  checked: true,
  ...extra,
});

function seed(connections: unknown[], nodes: unknown[]) {
  hooks.responses["/connections"] = { connections };
  hooks.responses["/routing/quality"] = { bar: 0.75, min_samples: 3, rows: [] };
  hooks.responses["/fleet"] = { nodes, sampling: { active: true, interval: 2 }, code_route: {}, error: "" };
  hooks.responses["/fleet/usage"] = {};
}

describe("Settings > Connections: secondary actions are calm, each card keeps its one primary", () => {
  it("a connected card: Make default and Test are calm actions beside the calm Disconnect", async () => {
    seed([card("custom", { display_name: "Custom endpoint" }), card("openai", { connected: true, status: "connected", source: "vault" })], []);
    render(<ConnectionsPage />);
    const el = await waitFor(() => {
      const c = document.getElementById("conn-card-openai");
      expect(c).not.toBeNull();
      return c as HTMLElement;
    });
    const actions = within(el).getByTestId("conn-card-actions");
    const makeDefault = within(actions).getByRole("button", { name: /Make default/ });
    const test = within(actions).getByRole("button", { name: /Test/ });
    expectCalmAction(makeDefault, "Make default");
    expectCalmAction(test, "Test");
    // Labels stay whole (the v1.329.0 wrap rule).
    for (const b of [makeDefault, test]) expect(tokens(b)).toContain("whitespace-nowrap");
    const disconnect = within(actions).getByRole("button", { name: /Disconnect/ });
    expect(disconnect.getAttribute("data-confirm-variant")).toBe("calm");
    // No button in the row is a box.
    for (const b of within(actions).getAllByRole("button")) expect(borders(b)).toEqual([]);
  });

  it("the default card's Default mark is quiet: a tone-success word, no box", async () => {
    hooks.defaultProvider = "openai";
    seed([card("openai", { connected: true, status: "connected", source: "vault" })], []);
    render(<ConnectionsPage />);
    const mark = await screen.findByTestId("conn-default-mark");
    await waitFor(() => expect(mark.textContent?.trim()).toBe("Default"));
    expect(borders(mark)).toEqual([]);
    expect(tokens(mark).filter((c) => /^bg-/.test(c))).toEqual([]);
    expect(tokens(mark)).toContain("text-tone-success");
  });

  it("a saved endpoint row: tags are the calm Badge's shell, Verify tools is the calm action like Delete", async () => {
    seed(
      [card("custom", { display_name: "Custom endpoint" })],
      [fleetRow(node("relay", "Relay anthropic", { protocol: "anthropic", tool_use: true })), fleetRow(node("spare", "Spare box"))],
    );
    render(<ConnectionsPage />);
    const custom = (await screen.findByText("Custom endpoint")).closest("#conn-card-custom") as HTMLElement;
    await within(custom).findByText("Spare box");
    const tags = [
      ...within(custom).getAllByTestId("endpoint-row-protocol"),
      ...within(custom).getAllByTestId("endpoint-row-model"),
      ...within(custom).getAllByTestId("endpoint-row-tools"),
    ];
    expect(tags).toHaveLength(4);
    for (const t of tags) {
      expect(borders(t)).toEqual([]);
      expect(tokens(t).filter((c) => c !== "font-mono").sort()).toEqual(tokens(CALM_BADGE).sort());
    }
    const verify = within(custom).getByTestId("endpoint-row-verify");
    expect(verify.className).toBe(CALM_ACTION);
    expectCalmAction(verify, "Verify tools");
    const del = within(custom).getAllByRole("button", { name: "Delete" })[0];
    expect(del.getAttribute("data-confirm-variant")).toBe("calm");
  });

  it("the unconnected cards keep their one primary (Add an endpoint is the accent outline)", async () => {
    seed([card("custom", { display_name: "Custom endpoint" })], []);
    render(<ConnectionsPage />);
    const custom = (await screen.findByText("Custom endpoint")).closest("#conn-card-custom") as HTMLElement;
    const add = within(custom).getByRole("button", { name: /Add an endpoint/ });
    expect(tokens(add)).toContain("btn-soft");
  });
});

/* =========================================================== 5. Fleet */

describe("Fleet page: calm Badges and a calm Remove", () => {
  it("status, protocol and Remove are the calm variants; Refresh and rename are calm actions", () => {
    seed([], [fleetRow(node("lab", "Lab box", { kind: "vllm" }))]);
    render(<FleetPage />);
    const chat = screen.getByTestId("fleet-node-chat");
    const protocol = chat.querySelector("[data-badge-variant]") as HTMLElement;
    expect(protocol.getAttribute("data-badge-variant")).toBe("calm");
    expect(borders(protocol)).toEqual([]);
    const online = screen.getByText("online");
    expect(online.getAttribute("data-badge-variant")).toBe("calm");
    expect(borders(online)).toEqual([]);

    const controls = screen.getByTestId("fleet-node-controls");
    const remove = within(controls).getByRole("button", { name: "Remove" });
    expect(remove.getAttribute("data-confirm-variant")).toBe("calm");
    expect(borders(remove)).toEqual([]);
    const rename = within(controls).getByRole("button", { name: "Rename this endpoint" });
    expect(tokens(rename)).toEqual(expect.arrayContaining(["h-7", "w-7", "hover:bg-white/[0.06]"]));

    const refresh = screen.getByRole("button", { name: /Refresh/ });
    expectCalmAction(refresh, "Refresh");
  });
});

/* ======================================================= 6. plain copy */

describe("the last dash asides are plain words", () => {
  const parked = (why: { kind: string; message?: string } | null) =>
    stateChip(
      {
        id: "c",
        title: "Second Claude",
        provider: "anthropic",
        lane: "cli",
        order: 0,
        enabled: true,
        state: { status: "parked", parkedUntil: "2026-10-10T11:24:00.000Z", parkedReason: why, served: 0 },
      } as unknown as IronProxyAccount,
      new Date("2026-10-10T09:00:00.000Z"),
    ).label;

  it("the parked chip: a middle dot before the reason, nothing when there is no reason", () => {
    expect(parked({ kind: "quota-exhausted", message: "5-hour limit reached" })).toMatch(/^Parked until .+ · plan limit reached$/);
    expect(parked(null)).toMatch(/^Parked until [^·—–]+$/);
    expect(asides("lib/ironProxy.ts")).toEqual([]);
  });

  it("the Notifications blurb in Settings reads as a plain sentence", () => {
    const pieces = copyPieces("components/settings/SettingsHome.tsx").map((p) => p.text);
    expect(pieces).toContain("Where Iron Jarvis messages you: Telegram, Slack, Discord and email.");
    expect(pieces.filter((t) => /messages you/.test(t) && /[—–]/.test(t))).toEqual([]);
    expect(asides("components/settings/SettingsHome.tsx").filter((s) => /messages you/.test(s))).toEqual([]);
  });
});
