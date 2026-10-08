/**
 * v1.313.0 — UX/aesthetic wave 1, track T1 (the shell): the chrome every view
 * wears must be calm, legible and TRUTHFUL, without losing a single control.
 *
 * Pinned here, one user-visible behaviour per test:
 *  1. Phone title bar (390 px): no overlap. jsdom has no layout, so the pins
 *     are the CLASSES that decide it — the search box is ONE button visible at
 *     every width whose word + Ctrl K keycap hide below `sm`; no bare 200 px
 *     floor; the "/ Page" crumb and the "Iron Jarvis" wordmark hide below
 *     `sm` (still in the DOM); the brand link keeps an accessible name when
 *     its words are hidden; every bar control shares ONE height.
 *  2. The model chip never names a model while replies are a scripted demo
 *     (U1-1 `noModelChosen`): amber dot + "Demo replies", the dropdown still
 *     lists every model, a pick flips the chip at once.
 *  3. SimulatedBanner also shows when a provider IS connected but the default
 *     is still the demo — and then it must not claim nothing is connected.
 *  4. Theme dots are legible on the light Marks (computed contrast against
 *     the bar colour read from globals.css) and reachable on a phone (inside
 *     the nav drawer); two switchers never disagree about the active theme.
 *  5. The MoodOrb no longer reads as a settings gear when idle (no spokes).
 *  6. Desktop nav is discoverable: the hamburger carries a visible "Menu".
 *  7. The crumb names the page the way the page names itself (labelForPath
 *     vs each page's PageHeader title), and every route folder has a crumb.
 *
 * Anti-vacuity controls ride beside each red pin: a real chosen model IS
 * still named, Auto still says Auto, the no-provider banner still offers
 * "Connect a model", every theme still renders and applies, the drawer still
 * lists the nav, the orb still reports alert/thinking, every control is still
 * in the bar.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { join } from "node:path";
import React from "react";

/* ------------------------------------------------------------------ mocks */

const S = vi.hoisted(() => ({
  pathname: "/settings",
  events: [] as Array<Record<string, unknown>>,
}));

vi.mock("next/navigation", () => ({
  usePathname: () => S.pathname,
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {}, prefetch: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={typeof href === "string" ? href : String(href)} {...rest}>
      {children}
    </a>
  ),
}));

const { getMock, putMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  putMock: vi.fn(async () => ({})),
  postMock: vi.fn(async () => ({})),
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
  get: getMock,
  put: putMock,
  post: postMock,
  patch: vi.fn(async () => ({})),
  del: vi.fn(async () => ({})),
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => "",
  sseUrl: (p: string) => p,
  wsUrl: (p: string) => p,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
}));

// The orb reads the shared events hub; feed it a fixed list instead.
vi.mock("@/lib/useEvents", async (importOriginal) => {
  const orig = (await importOriginal()) as Record<string, unknown>;
  return {
    ...orig,
    useEvents: () => ({ events: S.events, connected: true }),
  };
});

// Mock contract: framer-motion mocks export `m` (and motion/AnimatePresence).
vi.mock("framer-motion", () => {
  const cache = new Map<string, unknown>();
  const MOTION_PROPS = [
    "initial", "animate", "exit", "transition", "layoutId", "layout",
    "whileHover", "whileTap", "whileFocus", "whileInView", "variants", "drag",
  ];
  const plain = (tag: string) => {
    if (!cache.has(tag)) {
      const C = React.forwardRef(function Plain(
        { children, ...rest }: { children?: React.ReactNode } & Record<string, unknown>,
        ref: React.Ref<unknown>,
      ) {
        const dom: Record<string, unknown> = { ...rest };
        for (const k of MOTION_PROPS) delete dom[k];
        return React.createElement(tag, { ...dom, ref }, children);
      });
      cache.set(tag, C);
    }
    return cache.get(tag);
  };
  const proxy = new Proxy({}, { get: (_t, tag) => plain(String(tag)) });
  return {
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
    LazyMotion: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
    MotionConfig: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
    domAnimation: {},
    domMax: {},
    m: proxy,
    motion: proxy,
    useReducedMotion: () => true,
  };
});

import { TitleBar } from "@/components/TitleBar";
import { ModelSwitcher } from "@/components/ModelSwitcher";
import { SimulatedBanner } from "@/components/SimulatedBanner";
import { MoodOrb } from "@/components/MoodOrb";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import { NavDrawer } from "@/components/Sidebar";
import { DaemonProvider, useDaemon } from "@/lib/daemon";
import { labelForPath } from "@/lib/nav";
import * as onboarding from "@/lib/onboarding";

/* ---------------------------------------------------------------- helpers */

const ROOT = process.cwd(); // vitest runs from dashboard/
/** Source pins normalise CRLF at the READER (the CI runner checks out CRLF). */
const readSrc = (...p: string[]) => readFileSync(join(ROOT, ...p), "utf8").replace(/\r\n/g, "\n");

const tokens = (el: Element | null | undefined): string[] =>
  ((el as HTMLElement | null)?.getAttribute?.("class") ?? "").split(/\s+/).filter(Boolean);

const HIDDEN_RE = /^((max-)?(sm|md|lg|xl|2xl):)?hidden$/;
const SM_SHOW_RE = /^sm:(inline|inline-flex|inline-block|flex|block|grid|inline-grid|contents)$/;
const ANY_SHOW_RE = /^(sm|md|lg):(inline|inline-flex|inline-block|flex|block|grid|inline-grid|contents)$/;

/** Every element from `el` up to (and including) `stop`. */
function chain(el: Element, stop: Element | null): Element[] {
  const out: Element[] = [];
  let cur: Element | null = el;
  while (cur) {
    out.push(cur);
    if (cur === stop) break;
    cur = cur.parentElement;
  }
  return out;
}

/** Hidden below `sm` and shown from `sm` up: some element in the chain is
 *  `hidden` AND carries an `sm:` display, and nothing in the chain hides it at
 *  every width or from `sm` up. */
function hiddenBelowSmOnly(el: Element, stop: Element | null): boolean {
  const els = chain(el, stop);
  let gated = false;
  for (const e of els) {
    const t = tokens(e);
    if (t.includes("hidden")) {
      if (!t.some((x) => SM_SHOW_RE.test(x))) return false; // hidden at sm+ too
      gated = true;
    }
    if (t.some((x) => /^(sm|md|lg):hidden$/.test(x))) return false;
  }
  return gated;
}

/** Visible at every width: nothing in the chain carries any `hidden` gate. */
function visibleAtEveryWidth(el: Element, stop: Element | null): boolean {
  return chain(el, stop).every((e) => !tokens(e).some((x) => HIDDEN_RE.test(x)));
}

/** The height classes of one control ("h-8", "sm:h-8", "h-[34px]"…), sorted. */
function heightTokens(cls: string): string {
  return cls
    .split(/\s+/)
    .filter((t) => /^((sm|md|lg):)?h-(\d+(\.\d+)?|\[[^\]]+\])$/.test(t))
    .sort()
    .join(" ");
}

/* WCAG contrast. */
function lin(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}
function lum([r, g, b]: number[]): number {
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
function contrast(a: number[], b: number[]): number {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}
function parseColor(c: string): number[] | null {
  const s = c.trim();
  const hex = s.match(/^#([0-9a-f]{6})$/i);
  if (hex) return [0, 2, 4].map((i) => parseInt(hex[1].slice(i, i + 2), 16));
  const rgb = s.match(/^rgba?\(\s*(\d+)[ ,]+(\d+)[ ,]+(\d+)/i);
  if (rgb) return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])];
  return null;
}
/** The `--ink-950` triplet (the bar's backdrop) for a theme, read from
 *  globals.css so this test follows T2's palette rather than a copy of it. */
function inkFor(theme: "root" | "mark1" | "mark8"): number[] {
  const css = readSrc("app", "globals.css");
  const head = theme === "root" ? /(^|\n):root\s*\{/ : new RegExp(`:root\\[data-theme="${theme}"\\]\\s*\\{`);
  const m = css.match(head);
  if (!m || m.index === undefined) throw new Error(`no block for ${theme}`);
  const body = css.slice(m.index, css.indexOf("}", m.index));
  const v = body.match(/--ink-950:\s*(\d+)\s+(\d+)\s+(\d+)/);
  if (!v) throw new Error(`no --ink-950 in ${theme}`);
  return [Number(v[1]), Number(v[2]), Number(v[3])];
}
/** A button's effective colour over `bg`, folding in a plain `opacity-NN`. */
function effectiveColor(btn: HTMLElement, bg: number[]): number[] | null {
  const c = parseColor(btn.style.color || "");
  if (!c) return null;
  const op = tokens(btn).map((t) => t.match(/^opacity-(\d+)$/)).find(Boolean);
  const a = op ? Number(op[1]) / 100 : 1;
  return c.map((v, i) => Math.round(v * a + bg[i] * (1 - a)));
}

/* ------------------------------------------------------- daemon fixtures */

const MODELS = {
  models: [
    { provider: "anthropic", model: "claude-opus-4-8", name: "Anthropic", kind: "api", inherited_from: "claude-cli" },
    { provider: "anthropic", model: "claude-sonnet-4-6", name: "Anthropic", kind: "api", inherited_from: "claude-cli" },
    { provider: "openai", model: "gpt-5.5", name: "OpenAI", kind: "api" },
  ],
};
const ROUTING = { enabled: false, routing_model: "", connected: [], suggested: null, tiers: {} };
const PROVIDERS_UP = [
  { provider: "anthropic", available: true, class: "api", inherited_from: "claude-cli" },
  { provider: "openai", available: true, class: "api" },
];

function health(default_provider: string, default_model = "claude-opus-4-8", providers = PROVIDERS_UP) {
  return { status: "ok", version: "1.313.0", default_provider, default_model, providers };
}

function mockDaemon(h: ReturnType<typeof health>) {
  getMock.mockImplementation(async (path: unknown) => {
    if (path === "/health") return h;
    if (path === "/models") return MODELS;
    if (path === "/routing") return ROUTING;
    return {};
  });
}

/** Renders after /health has landed (so "absent" really means absent). */
function Probe() {
  const { health: h } = useDaemon();
  return <span data-testid="probe">{h ? `dp=${h.default_provider}` : "loading"}</span>;
}

async function renderWithDaemon(node: React.ReactNode) {
  const r = render(
    <DaemonProvider>
      <Probe />
      {node}
    </DaemonProvider>,
  );
  await waitFor(() => expect(screen.getByTestId("probe").textContent).toMatch(/^dp=/));
  return r;
}

/** The switcher's trigger: the only button it renders before it opens. */
async function trigger(): Promise<HTMLButtonElement> {
  await waitFor(() => expect(document.querySelectorAll("button").length).toBeGreaterThan(0));
  return document.querySelectorAll("button")[0] as HTMLButtonElement;
}

/* ---------------------------------------------------------------- set-up */

beforeEach(() => {
  S.pathname = "/settings";
  S.events = [];
  getMock.mockReset();
  putMock.mockClear();
  postMock.mockClear();
  document.documentElement.removeAttribute("data-theme");
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
});
afterEach(() => {
  cleanup();
  document.documentElement.removeAttribute("data-theme");
  document.documentElement.classList.remove("theme-transition");
});

/* =================================================== 1. phone title bar */

describe("phone title bar — nothing overlaps at 390 px", () => {
  it("search is ONE button, visible at every width, with no bare 200 px floor", () => {
    S.pathname = "/settings";
    const { container } = render(<TitleBar />);
    const header = container.querySelector("header")!;
    const buttons = screen.getAllByRole("button", { name: /search iron jarvis/i });
    // One results surface, one door: an icon twin would make two buttons
    // with one name (and break titlebar.test's getByRole).
    expect(buttons).toHaveLength(1);
    const search = buttons[0];
    expect(search.getAttribute("aria-label")).toBe("Search Iron Jarvis (Ctrl K)");
    expect(visibleAtEveryWidth(search, header)).toBe(true);
    // RED today: `min-w-[200px]` unprefixed is what pushes it over the brand.
    for (const el of chain(search, header)) {
      expect(tokens(el)).not.toContain("min-w-[200px]");
    }
    // The magnifier stays on screen at every width.
    const icon = search.querySelector("svg");
    expect(icon).not.toBeNull();
    expect(visibleAtEveryWidth(icon!, search)).toBe(true);
  });

  it("below sm the search shows only its icon: the word and the Ctrl K keycap hide (still in the DOM)", () => {
    const { container } = render(<TitleBar />);
    const header = container.querySelector("header")!;
    const search = screen.getByRole("button", { name: /search iron jarvis/i });
    const word = within(search).getByText("Search");
    const kbd = search.querySelector("kbd");
    expect(kbd?.textContent).toMatch(/Ctrl\s*K/);
    // RED today: both are always shown, which is what collides with the chip.
    expect(hiddenBelowSmOnly(word, header)).toBe(true);
    expect(hiddenBelowSmOnly(kbd!, header)).toBe(true);
  });

  it("the '/ Page' crumb hides below sm (the page's own h1 names it there) and stays in the DOM", () => {
    S.pathname = "/settings";
    const { container } = render(<TitleBar />);
    const header = container.querySelector("header")!;
    const label = within(header).getByText("Settings");
    const sep = Array.from(header.querySelectorAll("span")).find((s) => s.textContent?.trim() === "/");
    expect(sep).toBeTruthy();
    // RED today: no breakpoint on either, so 'Settings' prints over 'Search'.
    expect(hiddenBelowSmOnly(label, header)).toBe(true);
    expect(hiddenBelowSmOnly(sep!, header)).toBe(true);
  });

  it("the wordmark hides below sm, the dot stays as the Overview link, and the link keeps its name", () => {
    S.pathname = "/settings";
    const { container } = render(<TitleBar />);
    const header = container.querySelector("header")!;
    const word = within(header).getByText("Iron Jarvis");
    const link = word.closest("a")!;
    expect(link.getAttribute("href")).toBe("/");
    expect(visibleAtEveryWidth(link, header)).toBe(true);
    // RED today: the wordmark has no breakpoint.
    expect(hiddenBelowSmOnly(word, header)).toBe(true);
    // CSS `hidden` drops the words from the accessible name, so the link
    // names itself (RED today: no aria-label).
    expect(link.getAttribute("aria-label") ?? "").toMatch(/Iron Jarvis.*Overview/);
    expect((link.style as unknown as Record<string, string>).WebkitAppRegion).toBe("no-drag");
  });

  it("every bar control shares ONE height (hamburger, search, model chip, mood orb, bell)", async () => {
    mockDaemon(health("anthropic"));
    render(<TitleBar />);
    const nav = screen.getByLabelText("Open navigation");
    const search = screen.getByRole("button", { name: /search iron jarvis/i });
    cleanup();

    await renderWithDaemon(<ModelSwitcher />);
    const chip = await trigger();
    const chipCls = chip.getAttribute("class") ?? "";
    cleanup();

    const orb = render(<MoodOrb />).container.firstElementChild as HTMLElement;
    const orbCls = orb.getAttribute("class") ?? "";

    // NotificationBell is pinned at the source (T1 owns only its trigger
    // styling): the className between its toggle and its icon.
    const bell = readSrc("components", "NotificationBell.tsx");
    const at = bell.indexOf("onClick={toggleOpen}");
    const end = bell.indexOf("<Bell size", at);
    expect(at).toBeGreaterThan(-1);
    const bellCls = bell.slice(at, end).replace(/\$\{[\s\S]*?\}/g, " ");

    const heights = {
      hamburger: heightTokens(nav.getAttribute("class") ?? ""),
      search: heightTokens(search.getAttribute("class") ?? ""),
      model: heightTokens(chipCls),
      orb: heightTokens(orbCls),
      bell: heightTokens(bellCls),
    };
    // Each control declares a height, and it is the same one.
    // RED today: h-7 / h-7 / (none — padding) / h-9 / h-9.
    for (const [name, h] of Object.entries(heights)) {
      expect(h, `${name} declares no height class`).not.toBe("");
    }
    expect(new Set(Object.values(heights)).size, JSON.stringify(heights)).toBe(1);
  });

  it("CONTROL: every bar control is still there and still wired", () => {
    const { container } = render(<TitleBar right={<button data-testid="injected">x</button>} />);
    expect(screen.getByLabelText("Open navigation")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /search iron jarvis/i })).toBeInTheDocument();
    expect(screen.getByTestId("injected")).toBeInTheDocument();
    expect(container.querySelector('a[href="/"]')).not.toBeNull();
    const seen = vi.fn();
    window.addEventListener("ij:open-palette", seen);
    fireEvent.click(screen.getByRole("button", { name: /search iron jarvis/i }));
    window.removeEventListener("ij:open-palette", seen);
    expect(seen).toHaveBeenCalledTimes(1);
  });
});

/* ======================================== 6. desktop nav discoverable */

describe("desktop nav is discoverable", () => {
  it("the hamburger carries a visible 'Menu' word from md up, and keeps its name and event", () => {
    const { container } = render(<TitleBar />);
    const header = container.querySelector("header")!;
    const nav = screen.getByLabelText("Open navigation");
    // RED today: an unlabelled 28 px icon.
    const word = within(nav).getByText("Menu");
    const t = chain(word, header).flatMap((e) => tokens(e));
    // Either always shown, or hidden on a phone and shown from sm/md/lg up.
    if (t.includes("hidden")) expect(t.some((x) => ANY_SHOW_RE.test(x))).toBe(true);
    expect(nav.getAttribute("aria-label")).toBe("Open navigation");
    const seen = vi.fn();
    window.addEventListener("ij:toggle-nav", seen);
    fireEvent.click(nav);
    window.removeEventListener("ij:toggle-nav", seen);
    expect(seen).toHaveBeenCalledTimes(1);
  });

  it("CONTROL: the drawer still opens on ij:toggle-nav and lists the nav", async () => {
    render(<NavDrawer />);
    await act(async () => {
      window.dispatchEvent(new CustomEvent("ij:toggle-nav"));
    });
    const drawer = await screen.findByRole("dialog", { name: "Navigation" });
    expect(within(drawer).getByRole("link", { name: /^Chat$/ })).toBeInTheDocument();
    expect(within(drawer).getByRole("link", { name: /^Settings$/ })).toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: /Advanced/i })).toBeInTheDocument();
  });
});

/* ===================================== 2. the model chip tells the truth */

describe("U1-1 — noModelChosen is the one truth for 'replies are a scripted demo'", () => {
  it("is exported from lib/onboarding and answers true for '', 'mock', null, undefined", () => {
    const fn = (onboarding as unknown as { noModelChosen?: (p: string | null | undefined) => boolean })
      .noModelChosen;
    // RED today: only app/page.tsx has a private copy.
    expect(typeof fn).toBe("function");
    for (const p of ["", "mock", " mock ", null, undefined]) expect(fn!(p), String(p)).toBe(true);
    for (const p of ["auto", "anthropic", "claude-cli", "ollama", "fleet-rtx"]) {
      expect(fn!(p), p).toBe(false);
    }
    // One rule, not two: isDemoDefault agrees with it on every value.
    for (const p of ["", "mock", "auto", "anthropic"]) {
      expect(onboarding.isDemoDefault({ default_provider: p } as never)).toBe(fn!(p));
    }
  });
});

describe("the model chip never names a model while replies are a scripted demo", () => {
  it.each([["mock"], [""]])(
    "default_provider %j: the chip says 'Demo replies' and never 'claude-opus-4-8'",
    async (dp) => {
      mockDaemon(health(dp));
      await renderWithDaemon(<ModelSwitcher />);
      const btn = await trigger();
      // RED today: the chip prints the stored default_model in mono.
      expect(btn.outerHTML).not.toContain("claude-opus-4-8");
      expect(btn.textContent).toContain("Demo replies");
      const name = btn.getAttribute("aria-label") ?? "";
      expect(name).toMatch(/demo replies/i);
      expect(name).toMatch(/no model chosen/i);
    },
  );

  it("on a phone (chip words hidden below sm) an amber dot alone still says it", async () => {
    mockDaemon(health("mock"));
    await renderWithDaemon(<ModelSwitcher />);
    const btn = await trigger();
    // RED today: no demo dot exists.
    const dot = btn.querySelector('[data-testid="ij-model-demo-dot"]');
    expect(dot).not.toBeNull();
    expect(visibleAtEveryWidth(dot!, btn)).toBe(true);
    expect(tokens(dot).some((t) => /amber/.test(t))).toBe(true);
  });

  it("the menu still lists every model, and does not pin the stored id as 'active'", async () => {
    mockDaemon(health("mock"));
    await renderWithDaemon(<ModelSwitcher />);
    fireEvent.click(await trigger());
    const list = await screen.findByTestId("ij-model-list");
    for (const id of ["claude-opus-4-8", "claude-sonnet-4-6", "gpt-5.5"]) {
      expect(list.textContent).toContain(id);
    }
    // The real Anthropic row stays selectable in its group…
    expect(within(list).getByTestId("ij-model-group-anthropic").textContent).toContain("claude-opus-4-8");
    // …and nothing else claims it (RED today: a pinned "active" row reads
    // claude-opus-4-8 under the mock provider, so the id appears twice).
    expect(within(list).getAllByText("claude-opus-4-8")).toHaveLength(1);
    const pinned = within(list).queryByTestId("ij-active-model-row");
    if (pinned) expect(pinned.textContent).not.toContain("claude-opus-4-8");
  });

  it("a pick flips the chip at once (optimistic), with no wait for /health", async () => {
    mockDaemon(health("mock"));
    await renderWithDaemon(<ModelSwitcher />);
    const btn = await trigger();
    expect(btn.textContent).toContain("Demo replies"); // RED today
    fireEvent.click(btn);
    const list = await screen.findByTestId("ij-model-list");
    fireEvent.click(within(list).getByText("claude-sonnet-4-6").closest("button")!);
    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith("/settings", {
        values: { default_provider: "anthropic", default_model: "claude-sonnet-4-6" },
      }),
    );
    // /health still answers "mock" — the chip follows the pick anyway.
    await waitFor(() => expect(btn.textContent).toContain("claude-sonnet-4-6"));
    expect(btn.textContent).not.toContain("Demo replies");
    expect(btn.querySelector('[data-testid="ij-model-demo-dot"]')).toBeNull();
  });

  it("CONTROL: a real chosen model IS still named on the chip", async () => {
    mockDaemon(health("anthropic"));
    await renderWithDaemon(<ModelSwitcher />);
    const btn = await trigger();
    await waitFor(() => expect(btn.textContent).toContain("claude-opus-4-8"));
    expect(btn.textContent).not.toContain("Demo replies");
    expect(btn.querySelector('[data-testid="ij-model-demo-dot"]')).toBeNull();
  });

  it("CONTROL: Auto still says Auto, never 'Demo replies'", async () => {
    mockDaemon(health("auto"));
    await renderWithDaemon(<ModelSwitcher />);
    const btn = await trigger();
    expect(btn.textContent).toContain("Auto");
    expect(btn.textContent).not.toContain("Demo replies");
  });
});

/* ============================== 3. SimulatedBanner — the connected demo */

describe("SimulatedBanner — a connected model that nobody chose to answer", () => {
  it("shows while a provider is available but the default is still the demo", async () => {
    S.pathname = "/settings";
    mockDaemon(health("mock"));
    await renderWithDaemon(<SimulatedBanner />);
    // RED today: hidden as soon as ANY provider is available.
    const strip = await screen.findByRole("status");
    expect(strip.textContent).toMatch(/demo/i);
    // Honest in THIS case: something IS connected, so it must not say otherwise.
    expect(strip.textContent).not.toMatch(/no AI model is connected/i);
    // It leads somewhere that offers the press — never only to "/" (the
    // Overview's setup card can be dismissed).
    const actions = within(strip).queryAllByRole("link").concat(within(strip).queryAllByRole("button"));
    expect(actions.length).toBeGreaterThan(0);
    for (const a of within(strip).queryAllByRole("link")) expect(a.getAttribute("href")).not.toBe("/");
  });

  it("CONTROL: nothing connected keeps the 'Simulated mode' strip and its Connect a model link", async () => {
    mockDaemon(health("mock", "claude-opus-4-8", []));
    await renderWithDaemon(<SimulatedBanner />);
    const strip = await screen.findByRole("status");
    expect(strip.textContent).toContain("Simulated mode");
    expect(within(strip).getByRole("link", { name: /Connect a model/ }).getAttribute("href")).toBe(
      "/connections",
    );
  });

  it.each([["anthropic"], ["auto"]])(
    "CONTROL: a chosen default (%s) with a provider up shows no strip",
    async (dp) => {
      mockDaemon(health(dp));
      await renderWithDaemon(<SimulatedBanner />);
      await act(async () => {});
      expect(screen.queryByRole("status")).toBeNull();
    },
  );
});

/* ========================================= 4. theme dots: light + phone */

const THEME_NAMES = ["Daylight", "Arc Cyan", "Liquid Glass", "Gold & Red", "Silver & Red"];
const themeButtons = (root: HTMLElement) =>
  THEME_NAMES.map((n) => within(root).getByRole("button", { name: new RegExp(n) }));

describe("theme dots are legible on the light Marks", () => {
  it.each([["mark1"], ["mark8"]] as const)(
    "on %s every reactor reaches 3:1 against the bar (opacity folded in)",
    async (theme) => {
      document.documentElement.dataset.theme = theme;
      render(<ThemeSwitcher />);
      await act(async () => {});
      const group = screen.getByRole("group", { name: /theme/i });
      const bg = inkFor(theme);
      const report: Record<string, number> = {};
      for (const btn of themeButtons(group)) {
        const c = effectiveColor(btn, bg);
        expect(c, `${btn.getAttribute("aria-label")} has no inline colour`).not.toBeNull();
        report[btn.getAttribute("aria-label") ?? "?"] = Number(contrast(c!, bg).toFixed(2));
      }
      // RED today: Arc Cyan / Gold / Silver are pale smudges, and every
      // inactive reactor is dimmed to opacity-55 on top of that.
      for (const [name, ratio] of Object.entries(report)) {
        expect(ratio, `${name}: ${JSON.stringify(report)}`).toBeGreaterThanOrEqual(3);
      }
    },
  );

  it("switching INTO Daylight re-colours the row at once (not only on mount)", async () => {
    document.documentElement.dataset.theme = "mark2";
    render(<ThemeSwitcher />);
    await act(async () => {});
    const group = screen.getByRole("group", { name: /theme/i });
    fireEvent.click(within(group).getByRole("button", { name: /Daylight/ }));
    expect(document.documentElement.dataset.theme).toBe("mark1");
    const bg = inkFor("mark1");
    for (const btn of themeButtons(screen.getByRole("group", { name: /theme/i }))) {
      const c = effectiveColor(btn, bg);
      expect(c).not.toBeNull();
      expect(contrast(c!, bg), btn.getAttribute("aria-label") ?? "").toBeGreaterThanOrEqual(3);
    }
  });

  it("CONTROL: every theme still renders, keeps its own colour on dark, and applies", async () => {
    document.documentElement.dataset.theme = "mark2";
    render(<ThemeSwitcher />);
    await act(async () => {});
    const group = screen.getByRole("group", { name: /theme/i });
    const btns = themeButtons(group);
    const colours = btns.map((b) => b.style.color);
    expect(colours.every(Boolean)).toBe(true);
    expect(new Set(colours).size).toBe(5); // each reactor shows ITS theme
    const ids = ["mark1", "mark2", "mark8", "mark23", "mark29"];
    THEME_NAMES.forEach((n, i) => {
      fireEvent.click(within(screen.getByRole("group", { name: /theme/i })).getByRole("button", { name: new RegExp(n) }));
      expect(document.documentElement.dataset.theme).toBe(ids[i]);
      expect(window.localStorage.getItem("ij_theme")).toBe(ids[i]);
    });
  });
});

describe("theme is reachable on a phone and never disagrees with itself", () => {
  it("the nav drawer carries the theme row, and a press there applies it", async () => {
    render(<NavDrawer />);
    await act(async () => {
      window.dispatchEvent(new CustomEvent("ij:toggle-nav"));
    });
    const drawer = await screen.findByRole("dialog", { name: "Navigation" });
    // RED today: below sm the bar's row is `hidden` and nothing else offers it.
    const group = within(drawer).getByRole("group", { name: /theme/i });
    expect(themeButtons(group)).toHaveLength(5);
    fireEvent.click(within(group).getByRole("button", { name: /Daylight/ }));
    expect(document.documentElement.dataset.theme).toBe("mark1");
    expect(window.localStorage.getItem("ij_theme")).toBe("mark1");
  });

  it("two switchers (bar + drawer) agree on the active theme after either is pressed", async () => {
    document.documentElement.dataset.theme = "mark2";
    render(
      <>
        <div data-testid="a">
          <ThemeSwitcher />
        </div>
        <div data-testid="b">
          <ThemeSwitcher />
        </div>
      </>,
    );
    await act(async () => {});
    const a = screen.getByTestId("a");
    const b = screen.getByTestId("b");
    fireEvent.click(within(a).getByRole("button", { name: /Daylight/ }));
    // MutationObserver callbacks are microtasks.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    // RED today: B keeps highlighting Arc Cyan — a stale "active" mark.
    expect(within(b).getByRole("button", { name: /Daylight/ }).getAttribute("aria-pressed")).toBe("true");
    expect(within(b).getByRole("button", { name: /Arc Cyan/ }).getAttribute("aria-pressed")).toBe("false");
  });
});

/* ======================================== 5. the orb is not a settings cog */

describe("MoodOrb no longer reads as a settings gear", () => {
  it("idle draws a dot and a ring — no spokes", () => {
    S.events = [];
    const { container } = render(<MoodOrb />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    // RED today: eight radial spokes around a circle = a cog.
    expect(svg!.querySelectorAll("line")).toHaveLength(0);
    // It still says what it is, in one img role (hooks-wave3 does getByRole("img")).
    const imgs = screen.getAllByRole("img");
    expect(imgs).toHaveLength(1);
    expect(imgs[0].getAttribute("aria-label")).toMatch(/Idle/);
  });

  it("CONTROL: alert and thinking are still reported", () => {
    S.events = [{ id: "e1", type: "review.requested", session_id: "s1", payload: {}, ts: "" }];
    const a = render(<MoodOrb />);
    expect(screen.getByRole("img").getAttribute("aria-label")).toMatch(/Attention needed/);
    a.unmount();
    S.events = [{ id: "e2", type: "session.created", session_id: "s2", payload: {}, ts: "" }];
    render(<MoodOrb />);
    expect(screen.getByRole("img").getAttribute("aria-label")).toMatch(/Thinking/);
  });
});

/* ===================================== 7. the crumb names the page right */

/** Route folders under app/ that render a page of their own (a server
 *  redirect is not a page; "/" has no crumb by design). */
function routeFolders(): string[] {
  const app = join(ROOT, "app");
  return readdirSync(app)
    .filter((d) => statSync(join(app, d)).isDirectory())
    .filter((d) => !d.startsWith("(") && !d.startsWith("_") && !d.startsWith("["))
    .filter((d) => existsSync(join(app, d, "page.tsx")))
    .filter((d) => !/\bredirect\(/.test(readSrc("app", d, "page.tsx")));
}

/** A page's own `<PageHeader title="…">` literal, if it has one. */
function pageTitle(dir: string): string | null {
  const src = readSrc("app", dir, "page.tsx");
  const at = src.indexOf("<PageHeader");
  if (at < 0) return null;
  const m = src.slice(at, at + 600).match(/\btitle=(?:"([^"]+)"|\{"([^"]+)"\})/);
  return m ? (m[1] ?? m[2]) : null;
}

/** Pages whose header is a hero headline, not a module name. */
const HEADLINE_PAGES = new Set(["help"]);

describe("the crumb names the page the way the page names itself", () => {
  it("every route folder that renders a page has a crumb", () => {
    const missing = routeFolders().filter((d) => !labelForPath(`/${d}`));
    // RED today: /kanban, /lessons, /ltm, /marketplace show no '/ page' at all.
    expect(missing).toEqual([]);
    expect(labelForPath("/")).toBeNull();
    expect(labelForPath("/not-a-real-page")).toBeNull();
  });

  it("the deep-link routes resolve to the page they render", () => {
    expect(labelForPath("/kanban")).toBe("Kanban");
    expect(labelForPath("/marketplace")).toBe("Directory");
    // /ltm and /lessons render MemorySurface, whose h1 is "Memory".
    expect(readSrc("components", "memory", "MemorySurface.tsx")).toMatch(/title="Memory"/);
    expect(labelForPath("/ltm")).toBe("Memory");
    expect(labelForPath("/lessons")).toBe("Memory");
  });

  it("each crumb equals that page's PageHeader title", () => {
    const mismatched: Record<string, { crumb: string | null; h1: string }> = {};
    for (const d of routeFolders()) {
      if (HEADLINE_PAGES.has(d)) continue;
      const h1 = pageTitle(d);
      if (!h1) continue;
      const crumb = labelForPath(`/${d}`);
      if (crumb !== h1) mismatched[`/${d}`] = { crumb, h1 };
    }
    // RED today: /fleet 'Local fleet' vs 'Fleet', /self-dev 'Self-improvement'
    // vs 'Self-development', /train 'Train on me' vs 'Train Jarvis on me'.
    expect(mismatched).toEqual({});
  });

  it("CONTROL: the sidebar's catalogue is untouched — no alias row leaks into NAV", async () => {
    const { NAV_ENTRIES } = await import("@/lib/nav");
    const hrefs = NAV_ENTRIES.map((e) => e.href);
    for (const alias of ["/kanban", "/ltm", "/lessons", "/marketplace"]) {
      expect(hrefs).not.toContain(alias);
    }
    // Nested routes and the prefix-collision rule still hold.
    expect(labelForPath("/sessions/abc123")).toBe("Sessions");
    expect(labelForPath("/filesearch")).not.toBe(labelForPath("/fleet"));
  });
});
