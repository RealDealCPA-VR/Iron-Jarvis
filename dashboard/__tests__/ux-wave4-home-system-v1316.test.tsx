/**
 * v1.316.0 — UX wave 4 ("automation, memory, connections, system"), track T1:
 * home & system (Settings, Updates, Help, Usage, Activity).
 *
 * Each test pins ONE user-visible behaviour; most carry an anti-vacuity half
 * (every control and consent step still there, the raw value still reachable
 * in a title, a real flow still works).
 *
 * Interface decisions the implementer follows:
 *  - Settings: the Save row is ALWAYS rendered, wrapped in
 *    data-testid="settings-save-bar" (Save disabled while clean). Only while
 *    the form is dirty does that wrapper carry `sticky bottom-0` plus a
 *    background, so it follows the user down the long form. No ancestor of
 *    the bar inside the page may carry an overflow-* class (sticky would not
 *    stick). A jump index <nav aria-label="Settings sections"> links to
 *    `#settings-<id>` for every section (models, local, automation,
 *    advanced) and each section element carries that id; following the
 *    Advanced link OPENS the collapsed <details>. The link text must not be
 *    exactly "Advanced" (ux-wave2-system-memory pins getByText("Advanced")).
 *  - Updates (source checkout): the apply button is `btn-accent` ONLY when an
 *    update is available; otherwise it is `btn-ghost` (still disabled). The
 *    up-to-date state shows no amber anywhere (the always-on warning icon
 *    beside the general sentence goes quiet). The <code> chips in "The exact
 *    steps" carry `whitespace-nowrap`.
 *  - Help: the Guide input takes the whole row below sm (`basis-full
 *    sm:basis-auto`, or a min-w of 14rem+) so the button wraps under it. The
 *    status line reads "Knows this app's guides …" with the counts moved to
 *    its `title`. Tile blurbs for Projects / Build / Self-development drop
 *    "context spine", "Live terminals", "throwaway worktree".
 *  - Usage chart: every bar is data-testid="usage-day-bar" with
 *    data-day="YYYY-MM-DD", oldest first, one per calendar day; the series is
 *    zero-filled across the `days` window ending today (local) and NEVER
 *    drops a raw by_day row (the daemon's cutoff is now-N days in UTC, so a
 *    row can sit one day outside either end — widen the series to include
 *    it). A zero-filled day carries data-empty="true". The bar element itself
 *    carries `style.height` as a percentage of the window's max. A segmented
 *    pair of buttons "Cost" / "Tokens" (aria-pressed) picks the series; it
 *    defaults to Tokens when the window's max day cost is 0, and the card
 *    title follows ("Cost over time" / "Tokens over time"). In that case a
 *    caption says "No billed spend in this window" (anything after it must be
 *    TRUE — a $0 can also be an unpriced model, so do not claim why unless
 *    the data says). data-testid="usage-chart-max" labels the top of the
 *    scale (formatTokens for tokens, usd for cost). Each bar's title keeps
 *    the exact date, $ and token count. hasData / the empty state stay
 *    driven by the RAW by_day.
 *  - Usage numbers: hero tokens are compact ("1.52B", Intl compact, max 2
 *    fraction digits) with the exact count in a title; the in/out sub-line is
 *    compact too. Usage reads GET /fleet/usage?days=<the selected window> and,
 *    when est_avoided_usd > 0 with local_tokens > 0, renders
 *    data-testid="usage-local-estimate" inside the Total cost tile: words
 *    "local models", "~$7,687", the baseline IN WORDS ("Claude Opus 4.8"),
 *    "list price", "estimate", a link to /fleet, and the raw
 *    provider:model in a title. It is never added to Total cost and never a
 *    bare "you saved". By model rows read words (providerDisplay; pi/… and
 *    opencode/… as "Pi · …" / "OpenCode · …" like fleet.py's labels) in a
 *    non-mono label, with the raw "provider · model" in the row's title.
 *  - Activity: "Cost in this view" reads "$0.00" at zero; a small non-zero
 *    cost keeps 4 decimals ("$0.0200", pinned by v1.300.0).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useEffect } from "react";

/* ------------------------------------------------------------------ mocks */

const S = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    responses: {} as Record<string, unknown>,
    errors: {} as Record<string, number>,
    puts: [] as { path: string; body: unknown }[],
    posts: [] as { path: string; body: unknown }[],
    health: null as unknown,
    feedStats: null as unknown,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: S.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => null,
  get: (path: string) =>
    path in S.responses ? Promise.resolve(S.responses[path]) : Promise.resolve({}),
  post: (path: string, body?: unknown) => {
    S.posts.push({ path, body });
    return Promise.resolve({});
  },
  put: (path: string, body?: unknown) => {
    S.puts.push({ path, body });
    return Promise.resolve({});
  },
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

function apiState(path: string | null) {
  const status = path ? S.errors[path] : undefined;
  return {
    data: path && status === undefined ? (S.responses[path] ?? null) : null,
    error: status !== undefined ? new S.FakeApiError(`GET ${path} failed`, status) : null,
    loading: false,
    reload: () => {},
  };
}
vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => apiState(path),
  usePolledApi: (path: string | null) => apiState(path),
}));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    health: S.health,
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, prefetch: () => {}, back: () => {} }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(window.location.search),
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
    "layoutId",
    "whileHover",
    "whileTap",
    "whileFocus",
    "whileInView",
    "viewport",
    "drag",
  ]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  return {
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
    LazyMotion: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
    domAnimation: {},
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

// Sibling cards that are not under test on their pages.
vi.mock("@/components/settings/MaintenanceTools", () => ({
  MaintenanceTools: () => null,
  mirrorLine: () => ({ tone: "muted", text: "" }),
}));
vi.mock("@/components/settings/DaemonTokenCard", () => ({ DaemonTokenCard: () => null }));
vi.mock("@/components/SafetyChecks", () => ({ SafetyChecksCard: () => null }));
vi.mock("@/components/AgentHistory", () => ({ AgentHistoryCard: () => null }));
vi.mock("@/components/BrandGlyph", async (orig) => ({
  ...((await orig()) as object),
  ProviderMark: () => null,
}));
// The Activity page's stats come from the feed; hand them over directly.
vi.mock("@/components/TimeTravelFeed", () => ({
  TimeTravelFeed: ({ onStats }: { onStats?: (s: unknown) => void }) => {
    useEffect(() => {
      if (S.feedStats) onStats?.(S.feedStats);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return null;
  },
}));

import SettingsPage from "@/app/settings/page";
import UpdatesPage from "@/app/updates/page";
import HelpPage from "@/app/help/page";
import UsagePage from "@/app/usage/page";
import ActivityPage from "@/app/activity/page";

/* ---------------------------------------------------------------- helpers */

/** What a user SEES: closed <details> keep only their <summary>; titles never count. */
function visibleText(root: Element): string {
  const c = root.cloneNode(true) as Element;
  c.querySelectorAll("details").forEach((d) => {
    if (d.hasAttribute("open")) return;
    Array.from(d.childNodes).forEach((n) => {
      if (!(n instanceof Element && n.tagName === "SUMMARY")) n.remove();
    });
  });
  c.querySelectorAll("[hidden], script, style").forEach((n) => n.remove());
  return (c.textContent ?? "").replace(/\s+/g, " ").trim();
}

const cls = (el: Element | null | undefined) => (el?.getAttribute("class") ?? "").split(/\s+/);

/** Local calendar day k days ago, as YYYY-MM-DD (negative k = the future). */
function daysAgo(k: number): string {
  const d = new Date();
  d.setHours(12, 0, 0, 0);
  d.setDate(d.getDate() - k);
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${dd}`;
}

/** Whole days between two YYYY-MM-DD strings (DST-proof: UTC midnight). */
function dayDiff(a: string, b: string): number {
  const [ay, am, ad] = a.split("-").map(Number);
  const [by, bm, bd] = b.split("-").map(Number);
  return Math.round((Date.UTC(by, bm - 1, bd) - Date.UTC(ay, am - 1, ad)) / 86_400_000);
}

beforeEach(() => {
  S.responses = {};
  S.errors = {};
  S.puts = [];
  S.posts = [];
  S.health = null;
  S.feedStats = null;
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
  window.history.replaceState(null, "", "/");
});
afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

/* ======================================================================== */
/*  Settings — Save is never out of reach                                    */
/* ======================================================================== */

function seedSettings(values: Record<string, unknown>) {
  S.health = {
    default_provider: "claude-cli",
    providers: [
      { provider: "claude-cli", available: true },
      { provider: "mock", available: true },
    ],
  };
  S.responses["/settings"] = { settings: values };
  S.responses["/models"] = {
    models: [{ provider: "claude-cli", model: "claude-opus-4-8", available: true }],
  };
  S.responses["/chat/personas"] = { personas: [{ name: "assistant" }] };
}

async function renderSettings() {
  seedSettings({ default_provider: "claude-cli", default_model: "claude-opus-4-8" });
  const utils = render(<SettingsPage />);
  await screen.findByLabelText("Default provider");
  return utils;
}

const saveBar = () => {
  const btn = screen.getByRole("button", { name: /save changes/i });
  const bar = btn.closest('[data-testid="settings-save-bar"]');
  expect(bar, "the Save row is wrapped in data-testid=settings-save-bar").not.toBeNull();
  return { btn: btn as HTMLButtonElement, bar: bar as HTMLElement };
};

describe("Settings — the Save row follows the user while there are unsaved changes", () => {
  it("clean: Save is shown (disabled) and the row is NOT sticky", async () => {
    await renderSettings();
    const { btn, bar } = saveBar();
    expect(btn.disabled).toBe(true);
    expect(cls(bar)).not.toContain("sticky");
  });

  it("dirty: the row becomes a sticky bottom bar with a background, the count and Reset", async () => {
    await renderSettings();
    fireEvent.change(screen.getByLabelText("Default provider"), { target: { value: "mock" } });
    const { btn, bar } = saveBar();
    expect(btn.disabled).toBe(false);
    expect(cls(bar)).toContain("sticky");
    expect(cls(bar)).toContain("bottom-0");
    // Fields must not show through the bar as they scroll under it.
    expect(bar.getAttribute("class") ?? "").toMatch(/\bbg-|surface/);
    expect(within(bar).getByText(/1 unsaved change\b/)).toBeInTheDocument();
    expect(within(bar).getByRole("button", { name: /reset/i })).toBeInTheDocument();
  });

  it("nothing between the bar and <main> clips it (an overflow-* ancestor stops sticky)", async () => {
    const { container } = await renderSettings();
    fireEvent.change(screen.getByLabelText("Default provider"), { target: { value: "mock" } });
    const { bar } = saveBar();
    let el: Element | null = bar.parentElement;
    while (el && el !== container) {
      expect(el.getAttribute("class") ?? "", `ancestor <${el.tagName.toLowerCase()}>`).not.toMatch(
        /(^|\s)overflow-(hidden|auto|scroll|clip|x-\w+|y-\w+)(\s|$)/,
      );
      el = el.parentElement;
    }
  });

  it("after Save or Reset the bar settles back into place", async () => {
    await renderSettings();
    const sel = screen.getByLabelText("Default provider") as HTMLSelectElement;
    fireEvent.change(sel, { target: { value: "mock" } });
    fireEvent.click(screen.getByRole("button", { name: /reset/i }));
    expect(cls(saveBar().bar)).not.toContain("sticky");
    fireEvent.change(sel, { target: { value: "mock" } });
    expect(cls(saveBar().bar)).toContain("sticky");
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await screen.findByText("Saved 1 setting.");
    expect(cls(saveBar().bar)).not.toContain("sticky");
  });

  it("anti-vacuity: Save still PUTs only the change and says so", async () => {
    await renderSettings();
    fireEvent.change(screen.getByLabelText("Default provider"), { target: { value: "mock" } });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(S.puts).toHaveLength(1));
    expect(S.puts[0].path).toBe("/settings");
    expect(S.puts[0].body).toEqual({ values: { default_provider: "mock" } });
    expect(await screen.findByText("Saved 1 setting.")).toBeInTheDocument();
    expect((screen.getByRole("button", { name: /save changes/i }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("anti-vacuity: Reset puts the form back without saving", async () => {
    await renderSettings();
    const sel = screen.getByLabelText("Default provider") as HTMLSelectElement;
    fireEvent.change(sel, { target: { value: "mock" } });
    expect(screen.getByText(/1 unsaved change\b/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /reset/i }));
    expect(sel.value).toBe("claude-cli");
    expect(screen.queryByText(/unsaved change/)).toBeNull();
    expect(S.puts).toHaveLength(0);
  });
});

describe("Settings — a jump index reaches every section", () => {
  // v1.319.0: the whole Settings form is the ADVANCED view (or "Show all
  // settings"); Simple opens on the everyday settings (settings-simple-v1319).
  beforeEach(() => localStorage.setItem("ij_nav_advanced", "1"));
  const IDS = ["models", "local", "automation", "advanced"];

  it("links to an anchor for each section, and each anchor exists", async () => {
    const { container } = await renderSettings();
    const nav = screen.getByRole("navigation", { name: "Settings sections" });
    for (const id of IDS) {
      const link = nav.querySelector(`a[href="#settings-${id}"]`);
      expect(link, `jump link for ${id}`).not.toBeNull();
      expect(container.querySelector(`#settings-${id}`), `anchor for ${id}`).not.toBeNull();
    }
  });

  it("the Advanced jump opens the collapsed Advanced section", async () => {
    await renderSettings();
    const nav = screen.getByRole("navigation", { name: "Settings sections" });
    const adv = screen.getByLabelText("Self-development").closest("details") as HTMLDetailsElement;
    expect(adv).toBeTruthy();
    expect(adv.open).toBe(false);
    fireEvent.click(nav.querySelector('a[href="#settings-advanced"]') as HTMLAnchorElement);
    await waitFor(() => expect(adv.open).toBe(true));
  });

  it("anti-vacuity: every section's settings are still on the page (safety switches included)", async () => {
    await renderSettings();
    for (const label of [
      "Default provider",
      "Keep activity history",
      "Ollama server URL",
      "Prefer my own hardware",
      "Autonomy (the pulse)",
      "Emergency stop",
      "Dry-run mode",
      "Max tokens / day",
      "Git-native workspaces",
      "Self-development",
    ]) {
      expect(screen.getAllByLabelText(label).length, label).toBeGreaterThan(0);
    }
  });
});

/* ======================================================================== */
/*  Updates — one clear signal                                               */
/* ======================================================================== */

const UPD_BASE = { current: "abc1234", remote: "abc1234", branch: "master", behind: 0 };
const LOCAL_CHANGES = {
  ...UPD_BASE,
  available: false,
  clean: false,
  reason: "working tree has uncommitted changes — commit or stash before updating",
};
const UP_TO_DATE = { ...UPD_BASE, available: false, clean: true };
const BEHIND = { ...UPD_BASE, remote: "def5678", available: true, behind: 3, clean: true };

const applyButton = (container: HTMLElement) => {
  const card = within(container).getByText("Apply update").closest("section") as HTMLElement;
  const btns = within(card)
    .getAllByRole("button")
    .filter((b) => !b.closest("details"));
  expect(btns).toHaveLength(1);
  return btns[0] as HTMLButtonElement;
};

describe("Updates (source checkout) — the apply button is brand-filled only when there is something to apply", () => {
  it("local changes: the button is a quiet, disabled ghost — not a brand-filled primary", () => {
    S.responses["/update/check"] = LOCAL_CHANGES;
    const { container } = render(<UpdatesPage />);
    const btn = applyButton(container);
    expect(btn.disabled).toBe(true);
    expect(cls(btn)).not.toContain("btn-accent");
    expect(cls(btn)).toContain("btn-ghost");
  });

  it("up to date: a quiet, disabled 'Up to date' ghost", () => {
    S.responses["/update/check"] = UP_TO_DATE;
    const { container } = render(<UpdatesPage />);
    const btn = applyButton(container);
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toMatch(/Up to date/);
    expect(cls(btn)).not.toContain("btn-accent");
  });

  it("up to date: no amber anywhere (a green badge beside a warning icon is two signals)", () => {
    S.responses["/update/check"] = UP_TO_DATE;
    const { container } = render(<UpdatesPage />);
    const amber = Array.from(container.querySelectorAll("[class]")).filter((e) =>
      /amber|tone-warn/.test(e.getAttribute("class") ?? ""),
    );
    expect(amber.map((e) => e.getAttribute("class"))).toEqual([]);
  });

  it("anti-vacuity: an available update is the brand-filled primary and still POSTs the apply", async () => {
    S.responses["/update/check"] = BEHIND;
    const { container } = render(<UpdatesPage />);
    const btn = applyButton(container);
    expect(btn.disabled).toBe(false);
    expect(cls(btn)).toContain("btn-accent");
    expect(btn.textContent).toMatch(/Apply update \(3 commits\)/);
    fireEvent.click(btn);
    await waitFor(() => expect(S.posts).toHaveLength(1));
    expect(S.posts[0]).toEqual({ path: "/update/apply", body: { build_dashboard: true } });
  });

  it("anti-vacuity: local changes still say so, the reason and Re-check stay", () => {
    S.responses["/update/check"] = LOCAL_CHANGES;
    const { container } = render(<UpdatesPage />);
    expect(visibleText(container)).toMatch(/local changes/i);
    expect(container.textContent).toContain(LOCAL_CHANGES.reason);
    expect(screen.getByRole("button", { name: /Re-check/ })).toBeInTheDocument();
  });
});

describe("Updates — the command chips never break letter by letter on a phone", () => {
  it("every <code> chip in 'The exact steps' is whitespace-nowrap", () => {
    S.responses["/update/check"] = LOCAL_CHANGES;
    const { container } = render(<UpdatesPage />);
    const steps = within(container).getByText("The exact steps").closest("details") as HTMLElement;
    const chips = Array.from(steps.querySelectorAll("code"));
    expect(chips.map((c) => c.textContent)).toEqual([
      "git pull --ff-only",
      "uv sync",
      "pnpm build",
    ]);
    for (const c of chips) expect(cls(c), c.textContent ?? "").toContain("whitespace-nowrap");
  });
});

/* ======================================================================== */
/*  Help — the Guide row on a phone, in plain words                         */
/* ======================================================================== */

const GUIDE_STATUS = {
  docs: [
    { slug: "handbook", title: "The Handbook", sections: 40 },
    { slug: "vocabulary", title: "Vocabulary", sections: 6 },
  ],
  missing: [],
  doc_sections: 46,
  live_sections: 12,
};

function renderHelp(status: unknown = GUIDE_STATUS) {
  S.responses["/guide/status"] = status;
  S.responses["/helpdocs"] = { docs: [] };
  return render(<HelpPage />);
}

describe("Help — the Ask the Guide box takes the whole row on a phone", () => {
  it("the input wraps the button beneath it below sm instead of shrinking to ~165px", () => {
    renderHelp();
    const input = screen.getByLabelText("Ask the Guide");
    const c = input.getAttribute("class") ?? "";
    expect(
      /(^|\s)basis-full(\s|$)/.test(c) || /(^|\s)min-w-\[(1[4-9]|[2-9]\d)rem\](\s|$)/.test(c),
      `input classes: ${c}`,
    ).toBe(true);
  });

  it("from sm up the box and the button share ONE row again (basis 0, never auto — .field is w-full)", () => {
    renderHelp();
    const c = screen.getByLabelText("Ask the Guide").getAttribute("class") ?? "";
    // `.field` sets width:100%, so `sm:basis-auto` would keep a 100% basis
    // and wrap the button under the box on a desktop (reviewer, v1.316.0).
    expect(/(^|\s)sm:basis-0(\s|$)/.test(c), `input classes: ${c}`).toBe(true);
    expect(c).not.toMatch(/(^|\s)sm:basis-auto(\s|$)/);
  });

  it("anti-vacuity: the box and the button still navigate to chat with the question prefilled", () => {
    renderHelp();
    fireEvent.change(screen.getByLabelText("Ask the Guide"), {
      target: { value: "How do updates install?" },
    });
    expect(screen.getByTestId("ask-guide-link")).toHaveAttribute(
      "href",
      "/chat?ask=%40guide%20How%20do%20updates%20install%3F",
    );
  });
});

describe("Help — the Guide status line speaks plainly; the counts stay in a title", () => {
  it("says what the Guide knows without internal counts", () => {
    const { container } = renderHelp();
    const seen = visibleText(container);
    expect(seen).toMatch(/Knows this app.s guides/);
    expect(seen).not.toMatch(/\d+ live catalogs/);
    expect(seen).not.toMatch(/\(\d+ sections\)/);
    const line = screen.getByText(/Knows this app.s guides/);
    const holder = line.closest("[title]");
    expect(holder).not.toBeNull();
    const title = holder!.getAttribute("title") ?? "";
    expect(title).toMatch(/2 reference docs \(46 sections\)/);
    expect(title).toMatch(/12 live catalogs/);
  });

  it("anti-vacuity: a missing doc is still named, and an offline daemon is still 'unknown'", () => {
    renderHelp({ ...GUIDE_STATUS, missing: [{ slug: "spec", file: "SPEC.MD" }] });
    expect(screen.getByText(/Missing from this install: SPEC\.MD/)).toBeInTheDocument();
    cleanup();
    S.errors["/guide/status"] = 0;
    S.responses["/helpdocs"] = { docs: [] };
    render(<HelpPage />);
    expect(screen.getByText(/daemon looks offline/)).toBeInTheDocument();
    expect(screen.queryByText(/Knows /)).toBeNull();
  });
});

describe("Help — tiles describe pages in plain words", () => {
  it("no 'context spine', 'Live terminals' or 'throwaway worktree' on the page", () => {
    const { container } = renderHelp();
    const seen = visibleText(container);
    expect(seen).not.toMatch(/context spine/i);
    expect(seen).not.toMatch(/Live terminals/i);
    expect(seen).not.toMatch(/throwaway worktree/i);
  });

  it("anti-vacuity: the same tiles, links and the self-development review gate are still said", () => {
    const { container } = renderHelp();
    const tile = (href: string) => container.querySelector(`a.card-surface[href="${href}"]`) as HTMLElement;
    expect(within(tile("/projects")).getByText("Projects")).toBeInTheDocument();
    expect(within(tile("/terminals")).getByText("Build")).toBeInTheDocument();
    const selfDev = tile("/self-dev");
    expect(within(selfDev).getByText("Self-development")).toBeInTheDocument();
    expect(selfDev.textContent).toMatch(/review/i);
  });
});

/* ======================================================================== */
/*  Usage — a chart that reads, numbers that explain themselves             */
/* ======================================================================== */

interface Day {
  day: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}
const day = (k: number, tokens: number, cost = 0): Day => ({
  day: daysAgo(k),
  input_tokens: tokens,
  output_tokens: 0,
  cost_usd: cost,
});

function seedUsage(byDay: Day[], over: Record<string, unknown> = {}, days = 30) {
  const totals = {
    input_tokens: byDay.reduce((a, d) => a + d.input_tokens, 0),
    output_tokens: byDay.reduce((a, d) => a + d.output_tokens, 0),
    cost_usd: byDay.reduce((a, d) => a + d.cost_usd, 0),
    runs: byDay.length,
  };
  const payload = {
    totals,
    by_day: byDay,
    by_model: [
      { provider: "claude-cli", model: "claude-opus-5-5", input_tokens: 10, output_tokens: 1, cost_usd: 0, runs: 1 },
    ],
    ...over,
  };
  S.responses[`/usage?days=${days}`] = payload;
  S.responses["/usage?days=365"] = payload;
  return payload;
}

const bars = () => screen.queryAllByTestId("usage-day-bar");
const barFor = (iso: string) =>
  bars().find((b) => b.getAttribute("data-day") === iso) as HTMLElement | undefined;

describe("Usage chart — one bar per calendar day in the window, gaps shown", () => {
  it("3 days of data in a 30-day window draw 30 bars, oldest first, ending today", () => {
    seedUsage([day(20, 1_000_000), day(10, 2_000_000), day(1, 500_000)]);
    render(<UsagePage />);
    const all = bars();
    expect(all).toHaveLength(30);
    const isos = all.map((b) => b.getAttribute("data-day") as string);
    expect(isos[0]).toBe(daysAgo(29));
    expect(isos[29]).toBe(daysAgo(0));
    for (let i = 1; i < isos.length; i++) expect(dayDiff(isos[i - 1], isos[i])).toBe(1);
    expect(barFor(daysAgo(5))?.getAttribute("data-empty")).toBe("true");
    expect(barFor(daysAgo(10))?.getAttribute("data-empty")).not.toBe("true");
  });

  it("a raw row just outside the window (UTC cutoff skew) is never dropped", () => {
    seedUsage([day(30, 1_000), day(10, 2_000), day(-1, 3_000)]);
    render(<UsagePage />);
    const isos = bars().map((b) => b.getAttribute("data-day") as string);
    expect(isos).toContain(daysAgo(30));
    expect(isos).toContain(daysAgo(-1));
    for (let i = 1; i < isos.length; i++) expect(dayDiff(isos[i - 1], isos[i])).toBe(1);
  });

  it("the 7-day window draws 7 bars", () => {
    seedUsage([day(2, 100)], {}, 30);
    seedUsage([day(2, 100)], {}, 7);
    render(<UsagePage />);
    fireEvent.click(screen.getByRole("button", { name: "7d" }));
    expect(bars()).toHaveLength(7);
  });

  it("anti-vacuity: no usage in the window is still the empty state, with no bars", () => {
    seedUsage([], { totals: { input_tokens: 0, output_tokens: 0, cost_usd: 0, runs: 0 }, by_model: [] });
    render(<UsagePage />);
    expect(screen.getByText(/No usage recorded in this window yet/)).toBeInTheDocument();
    expect(bars()).toHaveLength(0);
  });
});

describe("Usage chart — Tokens when nothing was billed; a real scale", () => {
  it("all-zero cost: Tokens is chosen, titled, captioned, scaled to the busiest day", () => {
    seedUsage([day(20, 1_000_000), day(10, 2_000_000), day(1, 500_000)]);
    render(<UsagePage />);
    expect(screen.getByRole("button", { name: "Tokens" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Cost" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("Tokens over time")).toBeInTheDocument();
    expect(screen.queryByText("Cost over time")).toBeNull();
    expect(screen.getByText(/No billed spend in this window/)).toBeInTheDocument();
    expect(barFor(daysAgo(10))?.style.height).toBe("100%");
    expect(barFor(daysAgo(20))?.style.height).toBe("50%");
    expect(screen.getByTestId("usage-chart-max").textContent).toContain("2M");
  });

  it("the cost series is one press away, and its title follows", () => {
    seedUsage([day(20, 1_000_000), day(10, 2_000_000)]);
    render(<UsagePage />);
    fireEvent.click(screen.getByRole("button", { name: "Cost" }));
    expect(screen.getByRole("button", { name: "Cost" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Cost over time")).toBeInTheDocument();
    expect(bars().length).toBe(30);
  });

  it("with billed spend, Cost is the default, scaled to the dearest day; no 'no spend' caption", () => {
    seedUsage([day(20, 1_000, 1.5), day(10, 2_000, 4.5), day(3, 10_000, 0)]);
    render(<UsagePage />);
    expect(screen.getByRole("button", { name: "Cost" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Cost over time")).toBeInTheDocument();
    expect(screen.queryByText(/No billed spend/)).toBeNull();
    expect(barFor(daysAgo(10))?.style.height).toBe("100%");
    expect(screen.getByTestId("usage-chart-max").textContent).toContain("$4.50");
  });

  it("anti-vacuity: the day's tooltip keeps the exact dollars and tokens", () => {
    seedUsage([day(10, 2_000_000)]);
    const { container } = render(<UsagePage />);
    const chart = (screen.queryByText("Tokens over time") ?? screen.getByText("Cost over time")).closest(
      "section",
    ) as HTMLElement;
    expect(container.contains(chart)).toBe(true);
    const tips = Array.from(chart.querySelectorAll("[title]"))
      .map((e) => e.getAttribute("title") ?? "")
      .filter((t) => t.includes("2,000,000 tokens"));
    expect(tips).toHaveLength(1);
    expect(tips[0]).toContain("$0.00");
  });
});

describe("Usage numbers — compact at a glance, exact on hover", () => {
  it("1,524,819,623 tokens read '1.52B' with the exact count in a title; the sub-line is compact", () => {
    const payload = seedUsage([day(1, 10)]);
    payload.totals = { input_tokens: 1_521_665_331, output_tokens: 3_154_292, cost_usd: 0, runs: 15 };
    render(<UsagePage />);
    const v = screen.getByText("1.52B");
    expect(v.closest("[title]")?.getAttribute("title")).toBe("1,524,819,623");
    const tile = screen.getByText("Total tokens").closest(".card-surface") as HTMLElement;
    expect(tile.textContent).toMatch(/1\.52B in/);
    expect(tile.textContent).toMatch(/3\.15M out/);
    expect(tile.textContent).not.toMatch(/1,521,665,331/);
    // The exact in/out stays reachable.
    const titles = Array.from(tile.querySelectorAll("[title]")).map((e) => e.getAttribute("title"));
    expect(titles.join(" ")).toMatch(/1,521,665,331/);
  });
});

const FLEET = {
  days: 30,
  local_tokens: 1_521_665_331,
  cloud_tokens: 3_154_292,
  cloud_cost_usd: 0,
  est_avoided_usd: 7687.18,
  baseline_priced: true,
  comparison_provider: "anthropic",
  comparison_model: "claude-opus-4-8",
  basis: "estimate: what the local tokens would have cost on anthropic:claude-opus-4-8 at list price",
  by_node: [],
};

describe("Usage — local work's value is named against its baseline, never added to the bill", () => {
  const costTile = () => screen.getByText("Total cost").closest(".card-surface") as HTMLElement;

  it("Total cost stays $0.00 and a separate line names local models, the estimate and its baseline", () => {
    seedUsage([day(1, 10)]);
    S.responses["/fleet/usage?days=30"] = FLEET;
    render(<UsagePage />);
    const tile = costTile();
    expect(within(tile).getByText("$0.00")).toBeInTheDocument();
    const line = within(tile).getByTestId("usage-local-estimate");
    const t = line.textContent ?? "";
    expect(t).toMatch(/local models/i);
    expect(t).toMatch(/~\$7,687/);
    expect(t).toContain("Claude Opus 4.8");
    expect(t).toMatch(/list price/i);
    expect(t).toMatch(/estimate/i);
    expect(t).not.toMatch(/you saved/i);
    expect(line.querySelector('a[href="/fleet"]') ?? line.closest('a[href="/fleet"]')).not.toBeNull();
    const titles = [line, ...Array.from(line.querySelectorAll("[title]"))]
      .map((e) => e.getAttribute("title") ?? "")
      .join(" ");
    expect(titles).toContain("anthropic:claude-opus-4-8");
  });

  it("reads the estimate for the SELECTED window", () => {
    seedUsage([day(1, 10)], {}, 30);
    seedUsage([day(1, 10)], {}, 7);
    S.responses["/fleet/usage?days=30"] = FLEET;
    S.responses["/fleet/usage?days=7"] = { ...FLEET, days: 7, est_avoided_usd: 1234.5 };
    render(<UsagePage />);
    fireEvent.click(screen.getByRole("button", { name: "7d" }));
    expect(screen.getByTestId("usage-local-estimate").textContent).toMatch(/~\$1,23[45]/);
  });

  it("anti-vacuity: no line when the baseline is unpriced, nothing ran locally, or /fleet/usage fails", () => {
    seedUsage([day(1, 10)]);
    S.responses["/fleet/usage?days=30"] = { ...FLEET, est_avoided_usd: null, baseline_priced: false };
    render(<UsagePage />);
    expect(screen.queryByTestId("usage-local-estimate")).toBeNull();
    cleanup();
    S.responses["/fleet/usage?days=30"] = { ...FLEET, local_tokens: 0, est_avoided_usd: 0 };
    render(<UsagePage />);
    expect(screen.queryByTestId("usage-local-estimate")).toBeNull();
    cleanup();
    delete S.responses["/fleet/usage?days=30"];
    S.errors["/fleet/usage?days=30"] = 500;
    render(<UsagePage />);
    expect(screen.queryByTestId("usage-local-estimate")).toBeNull();
    expect(screen.getByText("Total cost")).toBeInTheDocument();
  });

  it("never a bare figure: no line when the baseline has no name, even with a priced estimate", () => {
    seedUsage([day(1, 10)]);
    S.responses["/fleet/usage?days=30"] = {
      ...FLEET, comparison_provider: "", comparison_model: "", est_avoided_usd: 7687,
    };
    render(<UsagePage />);
    expect(screen.queryByTestId("usage-local-estimate")).toBeNull();
    expect(screen.getByText("Total cost")).toBeInTheDocument();
  });

  it("no line when nothing ran locally, even if the daemon reports a positive estimate", () => {
    seedUsage([day(1, 10)]);
    S.responses["/fleet/usage?days=30"] = { ...FLEET, local_tokens: 0, est_avoided_usd: 7687 };
    render(<UsagePage />);
    expect(screen.queryByTestId("usage-local-estimate")).toBeNull();
    expect(screen.getByText("Total cost")).toBeInTheDocument();
  });

  it("anti-vacuity: the subscription list-price line is unchanged", () => {
    seedUsage([day(1, 10)], {
      totals: { input_tokens: 10, output_tokens: 0, cost_usd: 0, runs: 1, list_price_equivalent_usd: 12.5 },
    });
    render(<UsagePage />);
    expect(screen.getByTestId("usage-total-list-price").textContent).toBe(
      "~$12.50 list-price equivalent (subscription)",
    );
  });
});

describe("Usage — By model reads in words; the raw id stays in the row's title", () => {
  const MODELS = [
    {
      provider: "claude-cli", model: "claude-opus-5-5", input_tokens: 1000, output_tokens: 500,
      cost_usd: 1.234, runs: 2, list_price_equivalent: true,
    },
    { provider: "pi/local-models", model: "fleet", input_tokens: 5000, output_tokens: 50, cost_usd: 0, runs: 4 },
    { provider: "openai", model: "gpt-5", input_tokens: 100, output_tokens: 10, cost_usd: 0.5, runs: 1 },
  ];

  it("no raw provider ids in sight; words in a non-mono label; raw ids in titles", () => {
    seedUsage([day(1, 10)], { by_model: MODELS });
    render(<UsagePage />);
    const card = screen.getByText("By model").closest("section") as HTMLElement;
    const seen = visibleText(card);
    expect(seen).not.toMatch(/claude-cli/);
    expect(seen).not.toMatch(/pi\/local-models/);
    expect(seen).toContain("Claude Code");
    expect(seen).toContain("OpenAI");
    // textContent glues the card title to the first row ("By modelPi"), so a
    // \b boundary never matches there; pin the full fleet.py-style label.
    expect(seen).toContain("Pi · local-models");
    const titles = Array.from(card.querySelectorAll("[title]")).map((e) => e.getAttribute("title") ?? "");
    for (const raw of ["claude-cli · claude-opus-5-5", "pi/local-models · fleet", "openai · gpt-5"]) {
      expect(titles.some((t) => t.includes(raw)), raw).toBe(true);
    }
    for (const el of within(card).getAllByText(/Claude Code|OpenAI/)) {
      expect(cls(el)).not.toContain("font-mono");
    }
  });

  it("anti-vacuity: the subscription row still says list-price equivalent; the metered row its $", () => {
    seedUsage([day(1, 10)], { by_model: MODELS });
    render(<UsagePage />);
    expect(screen.getByTestId("usage-list-price").textContent).toBe(" · ~$1.23 list-price equivalent");
    const card = screen.getByText("By model").closest("section") as HTMLElement;
    const openaiRow = Array.from(card.querySelectorAll("[title]")).find((e) =>
      (e.getAttribute("title") ?? "").includes("openai · gpt-5"),
    ) as HTMLElement;
    expect(openaiRow.textContent).toContain("$0.50");
    expect(openaiRow.textContent).not.toContain("list-price");
  });
});

/* ======================================================================== */
/*  Activity — one money format                                             */
/* ======================================================================== */

describe("Activity — 'Cost in this view' reads $0.00 at zero", () => {
  const BASE = { total: 2, loaded: 2, undoable: 0, inputTokens: 10, outputTokens: 1, listPriceUsd: 0 };

  it("zero is '$0.00', not '$0.0000'", async () => {
    S.feedStats = { ...BASE, costUsd: 0 };
    render(<ActivityPage />);
    const tile = (await screen.findByText("Cost in this view")).closest(".card-surface") as HTMLElement;
    await waitFor(() => expect(within(tile).getByText("$0.00")).toBeInTheDocument());
    expect(tile.textContent).not.toContain("$0.0000");
  });

  it("anti-vacuity: a small non-zero cost keeps its precision; the list-price value stays apart", async () => {
    S.feedStats = { ...BASE, costUsd: 0.02, listPriceUsd: 0.42 };
    render(<ActivityPage />);
    expect(await screen.findByText("$0.0200")).toBeInTheDocument();
    expect(
      screen.getByText("+ ~$0.4200 list-price value (Claude subscription, not billed)"),
    ).toBeInTheDocument();
  });
});
