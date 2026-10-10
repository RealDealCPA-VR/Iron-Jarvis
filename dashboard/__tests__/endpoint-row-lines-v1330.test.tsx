/**
 * v1.330.0 (calm chat wave 12, N4): a saved-endpoint row puts its actions on
 * their own line.
 *
 * The wave-11 audit (v11m3__endpoints__desk.png): the row's tags (the calm
 * Badge shell, 11px) and its actions (the calm action, 12px) are both
 * borderless and shared ONE flex-wrap line, so "Verify tools" read like a tag
 * beside "fable-1", and in the narrow card "Measure" landed among the tags
 * while "Delete" wrapped alone.
 *
 * Now a row reads as three lines: the name and address, then the tags, then
 * the actions. The measured chip and Measure come from ONE component
 * (EnvelopeRowControls), so the row stays one flex box and CSS `order` moves
 * every action after a full-width break. jsdom has no layout, so these tests
 * read the VISUAL sequence the way a browser does: the row's children sorted
 * (stably) by their `order` class. The browser check (n4_endpoints.cjs,
 * desk + 390px, dark + Daylight) measured the same thing in pixels.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    probeFails: false,
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
  post: vi.fn((p: string) =>
    hooks.probeFails && p.endsWith("/probe")
      ? Promise.reject(new hooks.FakeApiError("The server refused the measurement.", 500))
      : Promise.resolve({ ok: true }),
  ),
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
vi.mock("@/components/BrandGlyph", () => ({ ProviderMark: () => null, BrandGlyph: () => null }));
// NOTE: EnvelopeCard is NOT mocked here. The measured chip and Measure are
// the real EnvelopeRowControls, because they are the two halves this layout
// has to pull apart.

import ConnectionsPage from "@/components/settings/pages/ConnectionsPage";
import { envelopeUrl } from "@/components/connections/EnvelopeCard";

beforeEach(() => {
  hooks.responses = {};
  hooks.probeFails = false;
});
afterEach(() => cleanup());

/* ---------------------------------------------------------------- helpers */

const tokens = (el: Element) => (el.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);

/** The flex `order` a class list gives an item (Tailwind's order-N,
 *  order-first, order-last; 0 when none). */
function flexOrder(el: Element): number {
  let order = 0;
  for (const t of tokens(el)) {
    const m = /^order-(\d+)$/.exec(t);
    if (m) order = Number(m[1]);
    else if (t === "order-first") order = -9999;
    else if (t === "order-last") order = 9999;
    else if (t === "order-none") order = 0;
  }
  return order;
}

/** A flex container's children in the order a browser lays them out: by
 *  `order`, ties in DOM order (Array.prototype.sort is stable). */
function visualChildren(row: Element): Element[] {
  return [...row.children]
    .map((el, i) => ({ el, i, o: flexOrder(el) }))
    .sort((a, b) => a.o - b.o || a.i - b.i)
    .map((x) => x.el);
}

type Kind = "name" | "address" | "tags-break" | "tag" | "actions-break" | "action" | "note" | "report" | "other";

function kindOf(el: Element, row: Element): Kind {
  const id = el.getAttribute("data-testid") ?? "";
  if (id === "endpoint-row-tags-break") return "tags-break";
  if (id === "endpoint-row-actions-break") return "actions-break";
  if (el.tagName === "BUTTON" && el === row.querySelector("button")) return "name";
  if (el.tagName === "BUTTON") return "action";
  if (id.startsWith("measure-error-")) return "note";
  if (id.startsWith("envelope-chip-") || /^endpoint-row-(protocol|model|vision|tools)$/.test(id)) return "tag";
  if (el.tagName === "SPAN" && el.getAttribute("title")?.startsWith("http")) return "address";
  if (el.tagName === "DIV") return "report";
  return "other";
}

/** The row's visual sequence as kinds, with the words of each tag/action. */
function sequence(row: Element): { kind: Kind; words: string }[] {
  return visualChildren(row).map((el) => ({ kind: kindOf(el, row), words: (el.textContent ?? "").trim() }));
}

/** The rule this item is about: name + address, then every tag, then every
 *  action, then any note. Returns what breaks it (empty = the row reads
 *  right). */
function lineProblems(row: Element): string[] {
  const seq = sequence(row);
  const problems: string[] = [];
  const at = (k: Kind) => seq.findIndex((s) => s.kind === k);
  const tagsBreak = at("tags-break");
  const actionsBreak = at("actions-break");
  if (tagsBreak < 0) problems.push("no break before the tags");
  if (actionsBreak < 0) problems.push("no break before the actions");
  seq.forEach((s, i) => {
    if (s.kind === "other") problems.push(`unknown item "${s.words}"`);
    if ((s.kind === "name" || s.kind === "address") && i > tagsBreak) problems.push(`${s.kind} after the tags break`);
    if (s.kind === "tag" && (i < tagsBreak || i > actionsBreak)) problems.push(`tag "${s.words}" is not on the tags line`);
    if (s.kind === "action" && i < actionsBreak) problems.push(`action "${s.words}" sits before the actions break`);
    if ((s.kind === "note" || s.kind === "report") && seq.slice(i + 1).some((x) => x.kind === "action"))
      problems.push(`${s.kind} "${s.words.slice(0, 20)}" comes before an action`);
  });
  return problems;
}

/* ---------------------------------------------------------------- fixtures */

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
const fleetRow = (n: ReturnType<typeof node>) => ({
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
});

/** Three rows like the audit's shot: an Anthropic relay (checked tools, a
 *  measured profile), a vLLM box (no tools, vision), and a box whose tools
 *  are not checked yet (Verify tools). */
function seedRows() {
  hooks.responses["/connections"] = { connections: [card("custom", { display_name: "Custom endpoint" })] };
  hooks.responses["/routing/quality"] = { bar: 0.75, min_samples: 3, rows: [] };
  hooks.responses["/fleet/usage"] = {};
  hooks.responses["/fleet"] = {
    nodes: [
      fleetRow(node("relay", "Relay anthropic", { protocol: "anthropic", tool_use: true, vision: false, default_model: "fable-claude" })),
      fleetRow(node("lab", "Lab box", { kind: "vllm", tool_use: false, vision: true, default_model: "qwen3-coder" })),
      fleetRow(node("spare", "Spare box")),
    ],
    sampling: { active: true, interval: 2 },
    code_route: {},
    error: "",
  };
  // The relay has a measured profile, so its row draws the measured chip.
  hooks.responses[envelopeUrl("fleet-relay", "fable-claude")] = {
    profile: { source: "probed", probed_at: "2026-10-09T10:00:00Z" },
    trusted: false,
  };
}

async function mountRows() {
  seedRows();
  render(<ConnectionsPage />);
  const custom = (await screen.findByText("Custom endpoint")).closest("#conn-card-custom") as HTMLElement;
  // Wait for the thing asserted: the relay's measured chip (an async GET).
  await within(custom).findByTestId("envelope-chip-fleet-relay-fable-claude");
  await within(custom).findByText("Spare box");
  const rows = within(custom).getAllByTestId("endpoint-row");
  expect(rows).toHaveLength(3);
  const byName = (name: string) => rows.find((r) => r.querySelector("button")?.textContent?.includes(name)) as HTMLElement;
  return { custom, rows, byName };
}

/* ================================================================== tests */

describe("a saved endpoint row: name and address, then the tags, then the actions", () => {
  it("every row reads in three lines; no action sits on the tags line", async () => {
    const { rows } = await mountRows();
    for (const row of rows) expect(lineProblems(row), row.textContent ?? "").toEqual([]);
  });

  it("the relay row: the measured chip is a tag, Measure and Delete are the actions", async () => {
    const { byName } = await mountRows();
    const seq = sequence(byName("Relay anthropic"));
    expect(seq.filter((s) => s.kind === "tag").map((s) => s.words)).toEqual([
      "Anthropic",
      "fable-claude",
      "no vision",
      "tools ✓",
      expect.stringMatching(/measured/i),
    ]);
    expect(seq.filter((s) => s.kind === "action").map((s) => s.words)).toEqual(["Measure", "Delete"]);
  });

  it("the spare row: Verify tools is an action on the actions line, never beside the model tag", async () => {
    const { byName } = await mountRows();
    const seq = sequence(byName("Spare box"));
    expect(seq.filter((s) => s.kind === "tag").map((s) => s.words)).toEqual(["fable-1"]);
    expect(seq.filter((s) => s.kind === "action").map((s) => s.words)).toEqual(["Verify tools", "Measure", "Delete"]);
  });

  it("the breaks are full-width, zero-height and hidden from a screen reader; the row wraps", async () => {
    const { rows } = await mountRows();
    for (const row of rows) {
      expect(tokens(row)).toEqual(expect.arrayContaining(["flex", "flex-wrap"]));
      for (const id of ["endpoint-row-tags-break", "endpoint-row-actions-break"]) {
        const br = within(row).getByTestId(id);
        expect(tokens(br)).toEqual(expect.arrayContaining(["basis-full", "h-0"]));
        expect(br.getAttribute("aria-hidden")).toBe("true");
        expect(br.textContent).toBe("");
      }
    }
  });

  it("Delete keeps to the right edge of the actions line and stays the calm two-press button", async () => {
    const { rows } = await mountRows();
    for (const row of rows) {
      const del = within(row).getByRole("button", { name: "Delete" });
      expect(tokens(del)).toContain("ml-auto");
      expect(del.getAttribute("data-confirm-variant")).toBe("calm");
      // The last action in the visual sequence.
      const actions = sequence(row).filter((s) => s.kind === "action");
      expect(actions[actions.length - 1].words).toBe("Delete");
    }
  });

  it("a failed Measure's note lands after the actions, still on the row", async () => {
    hooks.probeFails = true;
    const { byName } = await mountRows();
    const row = byName("Lab box");
    fireEvent.click(within(row).getByRole("button", { name: "Measure" }));
    await waitFor(() =>
      expect(within(row).getByTestId("measure-error-fleet-lab-qwen3-coder").textContent).toMatch(/refused/),
    );
    expect(lineProblems(row)).toEqual([]);
    expect(sequence(row).map((s) => s.kind).slice(-1)).toEqual(["note"]);
  });
});

describe("the reader itself (anti-vacuity)", () => {
  it("catches an action without its order, a missing break and a note before an action", () => {
    const row = document.createElement("div");
    row.innerHTML = [
      '<button>Relay</button>',
      '<span title="http://relay.test">http://relay.test</span>',
      '<span data-testid="endpoint-row-tags-break" class="h-0 basis-full"></span>',
      '<span data-testid="endpoint-row-model">fable-1</span>',
      '<button class="order-2">Verify tools</button>',
      '<button>Measure</button>', // no order: reads among the tags
      '<span data-testid="endpoint-row-actions-break" class="order-1 h-0 basis-full"></span>',
      '<span data-testid="measure-error-x" class="order-2">nope</span>', // before Delete
      '<button class="order-2 ml-auto">Delete</button>',
    ].join("");
    expect(lineProblems(row)).toEqual([
      'action "Measure" sits before the actions break',
      'note "nope" comes before an action',
    ]);
    const noBreak = document.createElement("div");
    noBreak.innerHTML = '<button>Relay</button><span data-testid="endpoint-row-model">fable-1</span><button>Delete</button>';
    expect(lineProblems(noBreak)).toEqual(expect.arrayContaining(["no break before the tags", "no break before the actions"]));
  });

  it("orders like a browser: by order, ties in DOM order", () => {
    const row = document.createElement("div");
    row.innerHTML = '<i class="order-2">c</i><i>a</i><i class="order-1">b</i><i>a2</i><i class="order-first">z</i>';
    expect(visualChildren(row).map((e) => e.textContent)).toEqual(["z", "a", "a2", "b", "c"]);
  });
});
