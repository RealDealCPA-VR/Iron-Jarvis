/**
 * v1.329.0 — calm chat wave 8, J3: Settings > Connections and the Fleet page
 * in the calm look, the protocol a node chats with, and a saved endpoint
 * listed at once.
 *
 * The closing audit found three things:
 *  1. The saved-endpoint rows mixed two looks: a quiet hairline "Anthropic"
 *     tag beside filled emerald "tools ✓" / "vision ✓" chips and an amber
 *     "no tools" chip, literal hues Daylight did not re-ink; elsewhere on the
 *     page amber and emerald status pills, a half-pixel text-[10.5px], and the
 *     key note "Paste your API key — it's stored encrypted…". Now every row
 *     chip shares ONE quiet shell (only a small mark inside carries a tone
 *     token), the whole page uses tone tokens and whole pixels, and a source
 *     guard keeps it that way (the Fleet page is guarded too).
 *  2. An Anthropic endpoint's detected kind can be "openai-compat" (its server
 *     also answers /v1/models), and the Fleet page labelled nodes by kind, so
 *     it called an Anthropic-speaking endpoint "OpenAI-compat" while Settings
 *     tagged it "Anthropic". The saved protocol now wins; the detected kind
 *     stays in the badge's title.
 *  3. A newly added endpoint was missing from GET /fleet until the sampler had
 *     sampled it. The daemon now lists it with `checked: false` (status
 *     "unknown", no metrics); the Fleet page says "not checked yet" (never
 *     online, never "exposes no serving metrics") and Settings lists the row.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

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
  return { FakeApiError, responses: {} as Record<string, unknown> };
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
    health: { default_provider: "mock", providers: [] },
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
vi.mock("@/components/connections/IronProxyCard", () => ({ IronProxyCard: () => null }));
vi.mock("@/components/connections/EnvelopeCard", () => ({
  EnvelopeRowControls: () => null,
  MeasuredEndpoints: () => null,
}));
vi.mock("@/components/BrandGlyph", () => ({ ProviderMark: () => null, BrandGlyph: () => null }));

import ConnectionsPage from "@/components/settings/pages/ConnectionsPage";
import FleetPage from "@/app/fleet/page";
import { chatHint, chatLabel, chatTone, isUnchecked, kindLabel, snapStatusLabel } from "@/lib/fleet";

/* -------------------------------------------------------------- fixtures */

function customCard() {
  return {
    provider: "custom",
    display_name: "Custom endpoint",
    method: "api_key",
    supports_oauth: false,
    supports_api_key: true,
    oauth_help: "",
    connected: false,
    status: "disconnected",
    account: "",
    source: "",
    scopes: [],
  };
}

type NodeExtra = Record<string, unknown>;
const node = (id: string, label: string, extra: NodeExtra = {}) => ({
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

/** One GET /fleet row the way the v1.329.0 daemon sends it. */
const row = (n: ReturnType<typeof node>, extra: NodeExtra = {}) => ({
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

/** A node the daemon lists before any probe (sampler.not_checked_snapshot). */
const unchecked = (n: ReturnType<typeof node>) =>
  row(n, {
    status: "unknown",
    evidence: "none",
    latency_ms: null,
    metrics_supported: false,
    metrics_reason: "Not checked yet.",
    metrics: null,
    sampled_at: 0,
    checked: false,
  });

const ANTH = node("relay", "Relay anthropic", { protocol: "anthropic", tool_use: true, vision: false });
const OA = node("lab", "Lab box", { kind: "vllm", tool_use: false, vision: true });
const UNVERIFIED = node("spare", "Spare box");
const FRESH = node("fresh", "Just saved", { protocol: "anthropic" });

function seed(nodes: unknown[]) {
  hooks.responses["/connections"] = { connections: [customCard()] };
  hooks.responses["/routing/quality"] = { bar: 0.75, min_samples: 3, rows: [] };
  hooks.responses["/fleet"] = { nodes, sampling: { active: true, interval: 2 }, code_route: {}, error: "" };
  hooks.responses["/fleet/usage"] = {};
}

beforeEach(() => {
  hooks.responses = {};
});
afterEach(() => cleanup());

/* ================================================================ guard */

const DASH_ROOT = path.join(__dirname, "..");
const FILES: Record<string, string> = {
  "ConnectionsPage.tsx": path.join(DASH_ROOT, "components", "settings", "pages", "ConnectionsPage.tsx"),
  "app/fleet/page.tsx": path.join(DASH_ROOT, "app", "fleet", "page.tsx"),
};
const read = (name: string) => readFileSync(FILES[name], "utf8").replace(/\r\n/g, "\n");

/** Exact class tokens a file may keep, each with the reason. Empty today:
 *  every colour on both pages is a theme token. An entry that no longer
 *  matches anything fails below, so the list cannot rot. */
const ALLOW: Array<{ file: string; token: string; why: string }> = [];

const HUES =
  "slate|gray|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose";
const PROPS =
  "text|bg|border|ring|from|via|to|fill|stroke|outline|decoration|divide|placeholder|shadow|caret|accent";
const HALF_PIXEL = /text-\[\d+\.5px\]/g;
const LITERAL_HUE = new RegExp(
  `(?<![\\w-])(?:${PROPS})(?:-[trblxyse])?-(?:${HUES})-\\d{2,3}(?:\\/(?:\\d{1,3}|\\[[0-9.]+\\]))?`,
  "g",
);
// A literal colour value anywhere inside an arbitrary class, e.g. the old
// `shadow-[0_0_8px_2px_rgba(52,211,153,0.5)]` glow or `bg-[#0a0c11]`. A value
// that reads a theme variable (`rgb(var(--accent-rgb)/0.4)`) follows the theme.
const ARBITRARY_COLOUR = /(?<![\w-])[a-z-]+-\[[^\]\s"'`]*?(?:#[0-9a-fA-F]{3,8}|(?:rgb|hsl)a?\((?!\s*var\())[^\]\s"'`]*\]/g;

function offenders(name: string, src: string): string[] {
  const allowed = ALLOW.filter((a) => a.file === name).map((a) => a.token);
  const hits: string[] = [];
  for (const re of [HALF_PIXEL, LITERAL_HUE, ARBITRARY_COLOUR]) {
    for (const m of src.matchAll(re)) {
      const tok = m[0];
      if (allowed.some((a) => tok === a || tok.startsWith(`${a}/`))) continue;
      const line = src.slice(0, m.index ?? 0).split("\n").length;
      hits.push(`${name}:${line} ${tok}`);
    }
  }
  return hits;
}

describe("source guard: Connections and Fleet use whole pixels and theme tokens", () => {
  it.each(Object.keys(FILES))("%s has no half-pixel size, literal hue or literal colour", (name) => {
    const src = read(name);
    // Anti-vacuity: the file is the real page and it does use tone tokens.
    expect(src.length).toBeGreaterThan(5000);
    expect(src).toMatch(/text-tone-(?:success|warn|danger)/);
    expect(offenders(name, src)).toEqual([]);
  });

  it("the patterns catch what they are meant to catch", () => {
    const bad = [
      'className="text-[10.5px]"',
      'className="border-emerald-400/25 bg-emerald-400/[0.08] text-emerald-300/90"',
      'className="bg-amber-500/[0.05]"',
      'className="h-1.5 w-1.5 shadow-[0_0_8px_2px_rgba(52,211,153,0.5)]"',
      'className="bg-[#0a0c11]"',
    ].join("\n");
    expect(offenders("probe", bad)).toHaveLength(7);
    expect(offenders("probe", 'className="shadow-[0_0_8px_rgb(var(--accent-rgb)/0.4)] text-tone-warn/25"')).toEqual([]);
  });

  it("every allowlist entry still matches something", () => {
    for (const a of ALLOW) expect(read(a.file), `${a.file}: ${a.token}`).toContain(a.token);
  });

  it("the key notes are plain sentences", () => {
    const src = read("ConnectionsPage.tsx");
    expect(src).toContain("Paste your API key. It is stored encrypted and never shown again.");
    expect(src).not.toMatch(/Paste your API key —/);
    expect(src).not.toMatch(/Endpoint saved —/);
  });
});

/* ======================================================= Connections rows */

async function savedRows() {
  render(<ConnectionsPage />);
  const card = (await screen.findByText("Custom endpoint")).closest("#conn-card-custom") as HTMLElement;
  await within(card).findByText("Relay anthropic");
  return card;
}

describe("Settings > Connections: one quiet chip look on every saved-endpoint row", () => {
  it("the Anthropic tag, the model, tools, vision and Verify share one shell; only the mark is toned", async () => {
    seed([row(ANTH), row(OA), row(UNVERIFIED)]);
    const card = await savedRows();

    const chips = [
      ...within(card).getAllByTestId("endpoint-row-protocol"),
      ...within(card).getAllByTestId("endpoint-row-model"),
      ...within(card).getAllByTestId("endpoint-row-tools"),
      ...within(card).getAllByTestId("endpoint-row-vision"),
      ...within(card).getAllByTestId("endpoint-row-verify"),
    ];
    // relay: Anthropic, model, tools ✓, no vision · lab: model, no tools,
    // vision ✓ · spare: model, Verify tools.
    expect(chips).toHaveLength(9);
    const shell = (el: HTMLElement) =>
      el.className
        .split(/\s+/)
        .filter((c) => !/^(?:font-mono|transition-colors|hover:|disabled:)/.test(c))
        .sort()
        .join(" ");
    const shells = new Set(chips.map(shell));
    expect(shells.size, [...shells].join("\n")).toBe(1);
    for (const el of chips) expect(el.className).not.toMatch(/emerald|amber|rose|green|yellow/);

    const tools = within(card).getAllByTestId("endpoint-row-tools");
    expect(tools.map((t) => t.textContent)).toEqual(["tools ✓", "no tools"]);
    // The verified yes and the "no" keep their meaning through a token mark.
    expect(tools[0].querySelector(".text-tone-success")).not.toBeNull();
    expect(tools[1].querySelector(".bg-tone-warn")).not.toBeNull();
    // Tooltips are plain sentences.
    for (const el of chips) expect(el.getAttribute("title") ?? "").not.toMatch(/ — /);
  });

  it("a node the daemon has not checked yet is still a saved row", async () => {
    seed([row(ANTH), unchecked(FRESH)]);
    const card = await savedRows();
    expect(within(card).getByText("Just saved")).toBeInTheDocument();
    expect(within(card).getAllByTestId("endpoint-row-protocol")).toHaveLength(2);
  });
});

/* ===================================================== lib/fleet words */

describe("lib/fleet: the protocol a node chats with wins over its detected kind", () => {
  it("chatLabel / chatTone / chatHint", () => {
    const anth = { protocol: "anthropic", kind: "openai-compat" };
    expect(chatLabel(anth)).toBe("Anthropic");
    expect(chatTone(anth)).toBe("slate");
    expect(chatHint(anth)).toBe(
      "Replies use the Anthropic Messages API. The server was detected as OpenAI-compatible.",
    );
    expect(chatLabel({ protocol: "openai", kind: "vllm" })).toBe("vLLM");
    expect(chatLabel({ kind: "openai-compat" })).toBe("OpenAI-compatible");
    expect(chatHint({ kind: "unknown" })).toBe(
      "Replies use the OpenAI chat API. The server type has not been detected yet.",
    );
    expect(kindLabel("openai")).toBe("OpenAI-compatible");
  });

  it("an unchecked snapshot reads 'not checked yet'; an older daemon's row is checked", () => {
    expect(isUnchecked({ checked: false })).toBe(true);
    expect(isUnchecked({})).toBe(false);
    expect(snapStatusLabel({ checked: false, status: "unknown" })).toBe("not checked yet");
    expect(snapStatusLabel({ status: "unknown" })).toBe("not detected yet");
    expect(snapStatusLabel({ checked: true, status: "online" })).toBe("online");
  });
});

/* ========================================================== Fleet page */

describe("Fleet page: the protocol shown, a new endpoint listed at once", () => {
  it("an Anthropic endpoint detected as openai-compat is labelled Anthropic, the kind in the title", () => {
    seed([row(ANTH), row(OA)]);
    render(<FleetPage />);
    const badges = screen.getAllByTestId("fleet-node-chat");
    expect(badges.map((b) => b.textContent)).toEqual(["Anthropic", "vLLM"]);
    expect(badges[0].getAttribute("title")).toMatch(/Anthropic Messages API/);
    expect(badges[0].getAttribute("title")).toMatch(/detected as OpenAI-compatible/);
    expect(badges[1].getAttribute("title")).toMatch(/OpenAI chat API/);
    // Nothing on the page calls the Anthropic endpoint by the OpenAI word.
    expect(within(badges[0]).queryByText(/OpenAI/)).toBeNull();
  });

  it("a node not checked yet says so: no online, no metrics claim, Refresh named", () => {
    seed([unchecked(FRESH), row(OA)]);
    render(<FleetPage />);
    const box = screen.getByTestId("fleet-node-unchecked");
    expect(box.textContent).toBe("Not checked yet. Refresh checks it now.");
    expect(screen.getByText("Just saved")).toBeInTheDocument();
    // Exactly one "not checked yet" pill (the new node), exactly one online
    // (the checked node beside it); the new node is never called online.
    expect(screen.getAllByText("Not checked yet")).toHaveLength(1);
    expect(screen.getAllByText("online")).toHaveLength(1);
    expect(screen.queryByText("not detected yet")).toBeNull();
    // No claim about metrics the node was never asked for.
    expect(document.body.textContent).not.toMatch(/exposes no serving metrics/);
    expect(screen.getAllByTestId("fleet-node-unchecked")).toHaveLength(1);
  });
});
