/**
 * v1.330.0 — calm chat wave 12, N3: the last Fleet and Settings rough edges.
 *
 * The wave-11 closing audit:
 *  1. The Fleet header's "Live updates" chip was a hand-made bordered pill
 *     (`rounded-xl border border-white/[0.08] bg-ink-900/80`) next to the
 *     borderless calm Refresh. It is the calm Badge now: no border, no fill,
 *     the dot carries the tone (success while sampling, neutral when idle).
 *  2. The child-row comment said the daemon refuses to "remove (or rename)" a
 *     proxy's model while the daemon only refused remove. The daemon now
 *     refuses remove, rename, re-detect and verify (409), and the comment
 *     says so.
 *  3. Settings > Permissions: "Approvals you gave once and for all — revoke
 *     any." had a dash aside. Plain sentences now.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

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
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {}, prefetch: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/fleet",
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

import FleetPage from "@/app/fleet/page";
import { CALM_BADGE } from "@/components/ui";
import { asides, copyPieces, readSrc } from "./helpers/dashGuard";

/* -------------------------------------------------------------- fixtures */

const node = (id: string, label: string) => ({
  id,
  label,
  base_url: `http://${id}.test:4000`,
  kind: "vllm",
  source: "user",
  parent_id: "",
  alias: "",
  enabled: true,
  routable: false,
  default_model: "glm",
  api_key_name: "",
  tool_use: null,
  vision: null,
  protocol: "openai",
});

const row = (n: ReturnType<typeof node>) => ({
  node: n,
  status: "online",
  evidence: "direct",
  latency_ms: 14,
  error: "",
  metrics_supported: false,
  metrics_reason: "",
  metrics: null,
  rates: null,
  models: [],
  children: [],
  sampled_at: 512.5,
  checked: true,
});

function seed(active: boolean) {
  hooks.responses["/fleet"] = {
    nodes: [row(node("lab", "Lab box"))],
    sampling: active
      ? { active: true, interval: 2, lease_expires_in: 41 }
      : { active: false, interval: 30, lease_expires_in: 0 },
    code_route: {},
    error: "",
  };
  hooks.responses["/fleet/usage"] = {};
}

const tokens = (el: Element | string) =>
  (typeof el === "string" ? el : el.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);
const BORDER = /^(?:[a-z-]+:)*border(?:-|$)/;
const LITERAL_HUE =
  /(?:emerald|amber|rose|red|green|yellow|cyan|sky|blue|violet|purple|pink|orange|lime|teal|indigo|fuchsia|slate|gray|stone|neutral)-\d{2,3}/;
const HALF_PIXEL = /\[\d+\.\d+px\]/;

beforeEach(() => {
  hooks.responses = {};
});
afterEach(() => cleanup());

/* ================================================== 1. the Live updates chip */

describe("Fleet header: the sampling chip is the calm chip", () => {
  it.each([
    [true, "Live updates", "green", "bg-tone-success", /^Sampling every 2s · lease 41s left$/],
    [false, "Idle", "slate", "bg-zinc-500", /^Not sampling right now\. Idles at 30s$/],
  ])("active=%s reads %s with a %s dot, borderless, theme tokens, whole pixels", (active, text, tone, dotClass, title) => {
    seed(active);
    render(<FleetPage />);

    const chip = screen.getByTestId("fleet-sampling-chip");
    expect(chip.textContent).toBe(text);
    expect(chip.getAttribute("data-badge-variant")).toBe("calm");
    expect(chip.getAttribute("data-tone")).toBe(tone);
    expect(chip.getAttribute("title")).toMatch(title);

    // The calm Badge shell exactly: no border, no fill, no hand-made pill.
    expect(tokens(chip).sort()).toEqual(tokens(CALM_BADGE).sort());
    for (const el of [chip, ...Array.from(chip.querySelectorAll("*"))]) {
      expect(tokens(el).filter((c) => BORDER.test(c)), "a border").toEqual([]);
      expect(tokens(el).filter((c) => /^bg-ink/.test(c)), "a filled pill").toEqual([]);
      expect(tokens(el).filter((c) => LITERAL_HUE.test(c)), "a literal hue").toEqual([]);
      expect(tokens(el).filter((c) => HALF_PIXEL.test(c)), "a half-pixel size").toEqual([]);
    }

    // The dot carries the tone (a tone token, not a literal colour).
    const dot = chip.querySelector("span[aria-hidden='true']") as HTMLElement;
    expect(dot).not.toBeNull();
    expect(tokens(dot)).toContain(dotClass);
  });

  it("sits beside the calm Refresh, and the old bordered pill is gone from the page source", () => {
    seed(true);
    render(<FleetPage />);
    const chip = screen.getByTestId("fleet-sampling-chip");
    const refresh = screen.getByRole("button", { name: /Refresh/ });
    expect(chip.parentElement).toBe(refresh.parentElement);

    const src = readSrc("app/fleet/page.tsx");
    expect(src).not.toMatch(/rounded-xl border border-white\/\[0\.08\] bg-ink-900\/80/);
  });
});

/* ======================================================= 2. the child comment */

describe("Fleet child row: the comment matches what the daemon refuses", () => {
  it("names every action the daemon refuses on a proxy's model", () => {
    const src = readSrc("app/fleet/page.tsx");
    const at = src.indexOf('data-testid="fleet-child-note"');
    expect(at).toBeGreaterThan(-1);
    const before = src.slice(Math.max(0, at - 900), at);
    expect(before).toMatch(/refuses \(409\) to remove, rename, re-detect or\s+\/\/\s+verify it/);
    expect(before).not.toMatch(/remove \(or rename\)/);
  });
});

/* ===================================================== 3. the Settings blurb */

describe("Settings > Permissions: the Standing grants blurb is plain sentences", () => {
  it("reads as two sentences with no dash aside, and the file has no aside left", () => {
    const pieces = copyPieces("components/settings/SettingsHome.tsx").map((p) => p.text);
    expect(pieces).toContain("Approvals you gave that stay in place. You can revoke any of them.");
    expect(pieces.filter((t) => /once and for all/.test(t))).toEqual([]);
    expect(asides("components/settings/SettingsHome.tsx")).toEqual([]);
  });
});
