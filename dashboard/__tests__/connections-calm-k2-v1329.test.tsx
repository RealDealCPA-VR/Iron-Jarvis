/**
 * v1.329.0 — calm chat wave 9, K2: the Fleet page fits a phone again, the
 * endpoint measurement chip and the rest of Connections are calm and plain.
 *
 * The verification audit after wave 8 found:
 *  1. A REGRESSION from J3: on the Fleet page at 390px a node labelled
 *     "OpenAI-compatible" pushed the header's Remove button to right=403px
 *     (clipped to "Remov"). The Card header was one row that could not wrap.
 *     The node card's header now wraps: the controls drop to their own line
 *     and a long name or host truncates with its title.
 *  2. EnvelopeCard's provenance chip ("floor defaults", "measure failed —
 *     keeping floor defaults") was an amber bordered pill in literal hues on
 *     every saved-endpoint row, beside J3's one quiet chip. It now wears that
 *     same shell (`ENDPOINT_CHIP`, shared from components/connections/
 *     endpointChip.ts), only a small dot carries a tone token, and the words
 *     are plain ("Measuring failed"). "Nothing measured yet" draws no chip at
 *     all: Measure already says it.
 *  3. Dash asides left in Connections copy ("0 tools — restart", "Detected —
 *     ready to use", "Installed — not signed in", "Uses your X sign-in — no
 *     key stored here.", the /fleet menu blurb, and the rest of the
 *     connections folder). A parser-based guard keeps them out.
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

import FleetPage from "@/app/fleet/page";
import { EnvelopeRowControls, sourceBadge } from "@/components/connections/EnvelopeCard";
import { ENDPOINT_CHIP, ENDPOINT_CHIP_BUTTON } from "@/components/connections/endpointChip";

beforeEach(() => {
  hooks.responses = {};
});
afterEach(() => cleanup());

const ROOT = path.join(__dirname, "..");
const readSrc = (rel: string) => readFileSync(path.join(ROOT, rel), "utf8").replace(/\r\n/g, "\n");

/* ========================================== 1. the Fleet node header wraps */

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
const row = (n: ReturnType<typeof node>) => ({
  node: n,
  status: "online",
  evidence: "direct",
  latency_ms: 18,
  error: "",
  metrics_supported: false,
  metrics_reason: "This server exposes no serving metrics.",
  metrics: null,
  rates: null,
  models: [],
  children: [],
  sampled_at: 812.4,
  checked: true,
});

describe("Fleet: a node card's header wraps, so Remove stays inside the card on a phone", () => {
  it("the header can wrap, the controls keep to the right, the name and host truncate with a title", () => {
    const longName = "A very long endpoint name someone typed for the lab box in the back room";
    hooks.responses["/fleet"] = {
      nodes: [row(node("spare", longName))],
      sampling: { active: true, interval: 2 },
      code_route: {},
      error: "",
    };
    render(<FleetPage />);

    const badge = screen.getByTestId("fleet-node-chat");
    expect(badge.textContent).toBe("OpenAI-compatible");
    const section = badge.closest("section") as HTMLElement;
    const header = section.querySelector(":scope > header") as HTMLElement;
    expect(header).not.toBeNull();

    // The Card's header row is one flex line by default; the node card makes
    // it WRAP and lets its title shrink. (jsdom cannot lay out; the 390px
    // screenshot measures the real right edge.)
    const cls = section.className.split(/\s+/);
    expect(cls).toContain("[&>header]:flex-wrap");
    expect(cls).toContain("[&>header>h2]:min-w-0");

    // The controls: Remove is in them, they wrap among themselves and sit at
    // the right of whatever line they land on.
    const controls = within(header).getByTestId("fleet-node-controls");
    expect(within(controls).getByRole("button", { name: /Remove/ })).toBeTruthy();
    expect(controls.className).toMatch(/(?:^|\s)flex-wrap(?:\s|$)/);
    expect(controls.className).toMatch(/(?:^|\s)ml-auto(?:\s|$)/);
    expect(controls.className).not.toMatch(/(?:^|\s)shrink-0(?:\s|$)/);

    // A long name or host never forces the row wider: each truncates and
    // keeps its full text in the title.
    const title = within(header).getByTestId("fleet-node-title");
    const name = within(title).getByText(longName);
    expect(name.className).toMatch(/\btruncate\b/);
    expect(name.className).toMatch(/\bmin-w-0\b/);
    expect(name.getAttribute("title")).toBe(longName);
    const host = within(title).getByText("spare.test:8000");
    expect(host.className).toMatch(/\btruncate\b/);
    expect(host.getAttribute("title")).toBe("http://spare.test:8000");
  });
});

/* ======================== 2. the measurement chip: one quiet shell, plain words */

const ENV = "/envelope/fleet-abc/gpt-oss-120b";
const profile = (source: string) => ({
  model_id: "gpt-oss-120b",
  provider: "fleet-abc",
  source,
  probed_at: null,
  measured_fields: [],
});

describe("EnvelopeRowControls: the provenance chip wears the endpoint rows' one chip shell", () => {
  it("a probe that measured nothing: 'Measuring failed', the quiet shell, a warn dot, plain title", async () => {
    hooks.responses[ENV] = { profile: profile("probe_failed"), trusted: false };
    render(<EnvelopeRowControls provider="fleet-abc" model="gpt-oss-120b" />);
    const chip = await screen.findByTestId("envelope-chip-fleet-abc-gpt-oss-120b");
    expect(chip.textContent).toBe("Measuring failed");
    expect(chip.className).toBe(ENDPOINT_CHIP);
    expect(chip.querySelector(".bg-tone-warn")).not.toBeNull();
    expect(chip.getAttribute("title")).toBe(
      "Measuring failed. Using the floor defaults: safe, conservative settings, not measured ones.",
    );
    // The Measure button is the same shell as a ghost that fills on hover.
    const btn = screen.getByTestId("measure-fleet-abc-gpt-oss-120b");
    expect(btn.className).toBe(ENDPOINT_CHIP_BUTTON);
    expect(btn.className).toMatch(/hover:bg-white\/\[0\.06\]/);
  });

  it("a measured profile: 'measured' with a success dot; seeded: no mark", async () => {
    hooks.responses[ENV] = { profile: profile("probed"), trusted: false };
    render(<EnvelopeRowControls provider="fleet-abc" model="gpt-oss-120b" />);
    const chip = await screen.findByTestId("envelope-chip-fleet-abc-gpt-oss-120b");
    expect(chip.textContent).toBe("measured");
    expect(chip.className).toBe(ENDPOINT_CHIP);
    expect(chip.querySelector(".bg-tone-success")).not.toBeNull();
    cleanup();

    hooks.responses[ENV] = { profile: profile("seeded"), trusted: false };
    render(<EnvelopeRowControls provider="fleet-abc" model="gpt-oss-120b" />);
    const seeded = await screen.findByTestId("envelope-chip-fleet-abc-gpt-oss-120b");
    expect(seeded.textContent).toBe("seeded");
    expect(seeded.querySelector("[class*='bg-tone-']")).toBeNull();
  });

  it("nothing measured yet: no chip on the row at all, Measure says it in its title", async () => {
    hooks.responses[ENV] = { profile: profile("default"), trusted: false };
    render(<EnvelopeRowControls provider="fleet-abc" model="gpt-oss-120b" />);
    const btn = await screen.findByTestId("measure-fleet-abc-gpt-oss-120b");
    // Wait for the GET to land (the title reads the answer), then assert.
    await waitFor(() => expect(btn.getAttribute("title")).toMatch(/^Not measured yet\. Measure checks/));
    expect(screen.queryByTestId("envelope-chip-fleet-abc-gpt-oss-120b")).toBeNull();
    expect(document.body.textContent).not.toMatch(/floor defaults/);
  });

  it("every provenance word is plain: no dash aside in a label or a title", () => {
    for (const source of ["probed", "tuned", "partial", "seeded", "probe_failed", "default"]) {
      const b = sourceBadge(source);
      expect(b.label, source).not.toMatch(/\s[—–]\s/);
      expect(b.title, source).not.toMatch(/\s[—–]\s/);
    }
    expect(sourceBadge("probe_failed").label).toBe("Measuring failed");
  });

  it("the rows' chip shell has ONE definition, shared by ConnectionsPage and EnvelopeCard", () => {
    const page = readSrc("components/settings/pages/ConnectionsPage.tsx");
    const env = readSrc("components/connections/EnvelopeCard.tsx");
    expect(page).toMatch(/import \{ ENDPOINT_CHIP, ENDPOINT_CHIP_BUTTON \} from "@\/components\/connections\/endpointChip"/);
    expect(env).toMatch(/import \{ ENDPOINT_CHIP, ENDPOINT_CHIP_BUTTON \} from "@\/components\/connections\/endpointChip"/);
    expect(page).not.toMatch(/const ENDPOINT_CHIP\s*=/);
    expect(env).not.toMatch(/const ENDPOINT_CHIP\s*=/);
    expect(env).not.toMatch(/TONE_(?:OK|WARN|NEUTRAL)/);
    // Whole pixels, no literal hue in the shell itself.
    expect(ENDPOINT_CHIP).toMatch(/text-\[11px\]/);
    expect(ENDPOINT_CHIP).not.toMatch(/emerald|amber|rose/);
  });
});

/* ===================================== 3. no dash asides in Connections copy */

/** Every string literal, template piece and JSX text node in a source file,
 *  with its line. Comments are not nodes, so they never count. (The same
 *  reader as mission-copy-v1329.) */
function copyPieces(rel: string, src = readSrc(rel)): Array<{ line: number; text: string; jsx: boolean }> {
  const file = ts.createSourceFile(rel, src, ts.ScriptTarget.Latest, true, rel.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const out: Array<{ line: number; text: string; jsx: boolean }> = [];
  const visit = (n: ts.Node) => {
    if (
      ts.isStringLiteral(n) ||
      ts.isNoSubstitutionTemplateLiteral(n) ||
      ts.isTemplateHead(n) ||
      ts.isTemplateMiddle(n) ||
      ts.isTemplateTail(n) ||
      ts.isJsxText(n)
    ) {
      const text = ts.isJsxText(n) ? n.getText(file) : (n as ts.LiteralLikeNode).text;
      out.push({ line: file.getLineAndCharacterOfPosition(n.getStart(file)).line + 1, text, jsx: ts.isJsxText(n) });
    }
    ts.forEachChild(n, visit);
  };
  visit(file);
  return out;
}

/** A dash used as an aside: an em or en dash with a space (or the edge of
 *  the text) on both sides. A lone dash placeholder ("—") is not an aside. */
const ASIDE = /(^|\s)[—–](\s|$)/;
const DASH_ONLY = /^[—–]$/;

function asides(rel: string, src?: string): string[] {
  return copyPieces(rel, src)
    .filter((p) => !(p.jsx ? DASH_ONLY.test(p.text.trim()) : DASH_ONLY.test(p.text)) && ASIDE.test(p.text))
    .map((p) => `${rel}:${p.line}: ${p.text.trim().slice(0, 80)}`);
}

const CONN_DIR = "components/connections";
const COPY_FILES = [
  "components/settings/pages/ConnectionsPage.tsx",
  ...readdirSync(path.join(ROOT, CONN_DIR))
    .filter((f) => /\.tsx?$/.test(f))
    .map((f) => `${CONN_DIR}/${f}`),
  "app/fleet/page.tsx",
  "lib/fleet.ts",
];

describe("Connections and Fleet copy is plain sentences", () => {
  it("covers the page, every file in the connections folder, the Fleet page and lib/fleet", () => {
    for (const f of ["EnvelopeCard.tsx", "IronProxyCard.tsx", "RestHookups.tsx", "EndpointModelPicker.tsx"]) {
      expect(COPY_FILES).toContain(`${CONN_DIR}/${f}`);
    }
  });

  it("no spaced em or en dash in any user-visible string or JSX text", () => {
    expect(COPY_FILES.flatMap((rel) => asides(rel))).toEqual([]);
  });

  it("the strings the audit named now read plainly (and are read as copy)", () => {
    const page = copyPieces("components/settings/pages/ConnectionsPage.tsx").map((p) => p.text).join("\n");
    expect(page).toContain("0 tools · restart");
    expect(page).toContain("Detected · ready to use");
    expect(page).toContain("Installed · not signed in");
    expect(page).toContain("sign-in. No key is stored here.");
    for (const old of ["0 tools —", "Detected —", "Installed —", "sign-in — no key"]) {
      expect(page).not.toContain(old);
    }
    const env = copyPieces("components/connections/EnvelopeCard.tsx").map((p) => p.text).join("\n");
    expect(env).toContain("Measuring failed. Using the floor defaults.");
    expect(env).not.toMatch(/keeping floor defaults/);
  });

  it("the /fleet menu blurb is a plain sentence", () => {
    const nav = readSrc("lib/nav.ts");
    const at = nav.indexOf('href: "/fleet"');
    expect(at).toBeGreaterThan(-1);
    const entry = nav.slice(at, nav.indexOf("},", nav.indexOf("blurb:", at)) + 2);
    const blurb = /blurb:\s*"([^"]*)"/.exec(entry)?.[1] ?? "";
    expect(blurb).toBe("Every model server you can reach, and what each one has loaded and is serving.");
    expect(blurb).not.toMatch(/[—–]/);
  });

  it("the guard reads copy and skips comments (anti-vacuity)", () => {
    const probe = [
      "// a comment — fine",
      "/* block — fine */",
      'const a = "one — two";',
      "const b = `x ${1} — y`;",
      "const c = <p>{/* jsx — fine */}left — right</p>;",
      'const e = "—";',
      "const j = <td>—</td>;",
    ].join("\n");
    expect(asides("probe.tsx", probe).map((s) => s.split(":")[1])).toEqual(["3", "4", "5"]);
  });
});
