/**
 * v1.314.0 — UX wave 2 ("words & empty states"), track T4: system & memory.
 *
 * Every view should read calm and obvious to a busy non-technical user while
 * flow, capability and usage stay the same or better. Each test pins ONE
 * user-visible behaviour, and most carry an anti-vacuity half: the raw value
 * is still reachable (a title / a <details>), every control and consent step
 * is still there, and the value POSTED to the daemon is still the raw token.
 *
 * Interface decisions the implementer follows (see each describe):
 *  - Settings: provider + policy options show words (providerDisplay); the
 *    option VALUE stays the id. Default model is a <select aria-label="Default
 *    model"> scoped to the chosen provider, with an "Other…" option that
 *    reveals a text input aria-label="Default model id".
 *  - Settings: an Appearance row wrapped in data-testid="settings-appearance"
 *    whose buttons (aria-pressed, name contains the theme name) drive the SAME
 *    store as ThemeSwitcher (<html data-theme> + localStorage "ij_theme").
 *  - Settings deep link: /settings?focus=advanced opens the Advanced <details>
 *    (the house ?focus= convention, read from window.location — see
 *    lib/useFocusRef.ts for why not useSearchParams).
 *  - Command palette: one "Theme: <name>" row per ThemeSwitcher theme.
 *  - Connections: the status pill carries data-testid="conn-status-pill".
 *  - Activity: consecutive foldable rows (kind "lifecycle", or a 0+0 token
 *    row) of ONE session, 2 or more, collapse into one button "N steps"
 *    (aria-expanded) that expands in place; undoable / denied / reversed rows
 *    are never folded.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

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
    push: (() => {}) as (href: string) => void,
    pushed: [] as string[],
    // v1.314.0 review: live events the feed's refresh keys off (default none).
    events: [] as { id: string; type: string; session_id?: string }[],
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
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: S.events, connected: true }) }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: (href: string) => S.pushed.push(href),
    replace: () => {},
    prefetch: () => {},
    back: () => {},
  }),
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

import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import { CommandPalette } from "@/components/CommandPalette";
import SettingsPage from "@/app/settings/page";
import SelfDevPage from "@/app/self-dev/page";
import UpdatesPage from "@/app/updates/page";
import FleetPage from "@/app/fleet/page";
import ActivityPage from "@/app/activity/page";
import SecretsPage from "@/app/secrets/page";
import ConnectionsPage from "@/app/connections/page";
import { Lessons } from "@/components/memory/Lessons";
import { RestHookups } from "@/components/connections/RestHookups";
import {
  TimeTravelFeed,
  foldEntries,
  humanSummary,
  isFoldable,
  type FeedStats,
} from "@/components/TimeTravelFeed";
import { codeRouteText } from "@/lib/fleet";
import { labelForPath } from "@/lib/nav";

/* ---------------------------------------------------------------- helpers */

const ROOT = process.cwd();
/** Source text, CRLF-normalised (this checkout is autocrlf). */
const src = (rel: string) => readFileSync(join(ROOT, rel), "utf8").replace(/\r\n/g, "\n");

/** What a user SEES: closed <details> keep only their <summary>; [hidden],
 *  <script>/<style> drop out; whitespace collapsed. Titles never count. */
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

/** Every title attribute under `root` (where a raw value may live). */
const titles = (root: Element) =>
  Array.from(root.querySelectorAll("[title]")).map((e) => e.getAttribute("title") || "");

const optionPairs = (sel: HTMLSelectElement) =>
  Array.from(sel.options).map((o) => [o.value, (o.textContent || "").trim()] as const);

/** The theme names ThemeSwitcher itself renders — the ONE list. */
function themeNamesFromSwitcher(): string[] {
  const { container, unmount } = render(<ThemeSwitcher />);
  const names = Array.from(container.querySelectorAll("button[aria-pressed]")).map(
    (b) => (b.getAttribute("aria-label") || "").split(" — ").pop() as string,
  );
  unmount();
  return names;
}

const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

beforeEach(() => {
  S.responses = {};
  S.errors = {};
  S.puts = [];
  S.posts = [];
  S.pushed = [];
  S.events = [];
  S.health = null;
  delete document.documentElement.dataset.theme;
  try {
    localStorage.clear();
  } catch {
    /* ignore */
  }
  window.history.replaceState(null, "", "/");
});
afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

/* ======================================================================== */
/*  Settings — labels, not raw internal values                               */
/* ======================================================================== */

const HEALTH = {
  default_provider: "claude-cli",
  providers: [
    { provider: "claude-cli", available: true },
    { provider: "mock", available: true },
    { provider: "openai", available: false },
  ],
};
const MODELS = {
  models: [
    { provider: "claude-cli", model: "claude-opus-4-8", available: true },
    { provider: "claude-cli", model: "claude-sonnet-4-6", available: true },
    { provider: "mock", model: "mock-1", available: true },
    { provider: "codex-cli", model: "gpt-5", available: false },
  ],
};

function seedSettings(values: Record<string, unknown>) {
  S.health = HEALTH;
  S.responses["/settings"] = { settings: values };
  S.responses["/models"] = MODELS;
  S.responses["/chat/personas"] = { personas: [{ name: "assistant" }] };
}

describe("Settings — the provider list speaks in names; the saved value stays the id", () => {
  it("Default provider options read 'Claude Code' / 'Demo model (scripted)' with raw values", async () => {
    seedSettings({ default_provider: "mock", default_model: "mock-1" });
    render(<SettingsPage />);
    const sel = (await screen.findByLabelText("Default provider")) as HTMLSelectElement;
    const pairs = optionPairs(sel);
    expect(pairs).toContainEqual(["claude-cli", "Claude Code"]);
    expect(pairs).toContainEqual(["mock", "Demo model (scripted)"]);
    for (const [, text] of pairs) expect(text).not.toMatch(/\bmock\b/i);
    expect(sel.value).toBe("mock");
  });

  it("a saved value that is not offered right now ('auto') is kept, in words", async () => {
    seedSettings({ default_provider: "auto", default_model: "" });
    render(<SettingsPage />);
    const sel = (await screen.findByLabelText("Default provider")) as HTMLSelectElement;
    expect(sel.value).toBe("auto");
    expect(optionPairs(sel)).toContainEqual(["auto", "Auto"]);
  });

  it("picking a provider and saving PUTs the raw id (labels are display-only)", async () => {
    seedSettings({ default_provider: "mock", default_model: "mock-1" });
    render(<SettingsPage />);
    const sel = (await screen.findByLabelText("Default provider")) as HTMLSelectElement;
    fireEvent.change(sel, { target: { value: "claude-cli" } });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(S.puts).toHaveLength(1));
    const body = S.puts[0].body as { values: Record<string, unknown> };
    expect(S.puts[0].path).toBe("/settings");
    expect(body.values.default_provider).toBe("claude-cli");
  });

  it("'If my local model answers with an error' offers sentences, not refuse/failover tokens", async () => {
    seedSettings({ default_provider: "claude-cli", local_primary_policy: "refuse" });
    const { container } = render(<SettingsPage />);
    const sel = (await screen.findByLabelText(
      "If my local model answers with an error",
    )) as HTMLSelectElement;
    const pairs = optionPairs(sel);
    expect(pairs.map(([v]) => v)).toEqual(["refuse", "failover"]);
    for (const [value, text] of pairs) {
      expect(text).not.toBe(value);
      expect(text).not.toMatch(/\b(refuse|failover)\b/);
    }
    // The hint no longer quotes the raw tokens at the user...
    // (the row = the nearest ancestor that also holds the field's label)
    let row: HTMLElement = sel;
    while (row.parentElement && !(row.textContent || "").includes("If my local model answers with an error")) {
      row = row.parentElement;
    }
    expect(row).not.toBe(container);
    expect(visibleText(row)).not.toMatch(/'(refuse|failover)'/);
    // ...and its honesty facts stay one click away (the receipt names who
    // answered; Auto is the one route that may substitute).
    expect(row.textContent).toMatch(/receipt/i);
    expect(row.textContent).toMatch(/\bAuto\b/);
  });

  it("no visible 'mock' anywhere on Settings (the strict-pin hint included)", async () => {
    seedSettings({ default_provider: "claude-cli", default_model: "claude-opus-4-8" });
    const { container } = render(<SettingsPage />);
    await screen.findByLabelText("Default provider");
    expect(visibleText(container)).not.toMatch(/\bmock\b/i);
    // Anti-vacuity: the honest strict-pin control is still there.
    expect(screen.getByText("Strict model pin")).toBeInTheDocument();
  });
});

describe("Settings — Default model is a picker for the chosen provider, free text kept", () => {
  it("lists the chosen provider's models, keeps the saved one selected, and offers Other…", async () => {
    seedSettings({ default_provider: "claude-cli", default_model: "claude-opus-4-8" });
    render(<SettingsPage />);
    const ctrl = (await screen.findByLabelText("Default model")) as HTMLSelectElement;
    expect(ctrl.tagName).toBe("SELECT");
    const values = Array.from(ctrl.options).map((o) => o.value);
    expect(values).toContain("claude-opus-4-8");
    expect(values).toContain("claude-sonnet-4-6");
    expect(values).not.toContain("mock-1"); // another provider's model
    expect(values).not.toContain("gpt-5");
    expect(ctrl.value).toBe("claude-opus-4-8");
    expect(Array.from(ctrl.options).some((o) => /^Other/.test((o.textContent || "").trim()))).toBe(true);
  });

  it("changing the provider re-scopes the model list", async () => {
    seedSettings({ default_provider: "claude-cli", default_model: "claude-opus-4-8" });
    render(<SettingsPage />);
    const prov = (await screen.findByLabelText("Default provider")) as HTMLSelectElement;
    fireEvent.change(prov, { target: { value: "mock" } });
    const ctrl = screen.getByLabelText("Default model") as HTMLSelectElement;
    const values = Array.from(ctrl.options).map((o) => o.value);
    expect(values).toContain("mock-1");
    expect(values).not.toContain("claude-sonnet-4-6");
  });

  it("Other… reveals the free-text box, and what is typed is what gets saved", async () => {
    seedSettings({ default_provider: "claude-cli", default_model: "claude-opus-4-8" });
    render(<SettingsPage />);
    const ctrl = (await screen.findByLabelText("Default model")) as HTMLSelectElement;
    const other = Array.from(ctrl.options).find((o) => /^Other/.test((o.textContent || "").trim()));
    expect(other).toBeTruthy();
    fireEvent.change(ctrl, { target: { value: other!.value } });
    const box = screen.getByLabelText("Default model id") as HTMLInputElement;
    fireEvent.change(box, { target: { value: "my-model-x" } });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(S.puts).toHaveLength(1));
    const body = S.puts[0].body as { values: Record<string, unknown> };
    expect(body.values.default_model).toBe("my-model-x");
  });

  it("a saved model the catalogue does not list is never lost", async () => {
    seedSettings({ default_provider: "claude-cli", default_model: "legacy-model-7" });
    render(<SettingsPage />);
    await screen.findByLabelText("Default model");
    expect(screen.getByDisplayValue("legacy-model-7")).toBeInTheDocument();
  });
});

describe("Settings — Appearance row and the title bar share ONE theme store", () => {
  it("offers every ThemeSwitcher theme, applies it instantly, and the bar's dots follow", async () => {
    const names = themeNamesFromSwitcher();
    expect(names.length).toBeGreaterThanOrEqual(4); // anti-vacuity: the list is real
    seedSettings({ default_provider: "claude-cli" });
    const bar = render(<ThemeSwitcher />);
    render(<SettingsPage />);
    const appearance = await screen.findByTestId("settings-appearance");
    expect(appearance.textContent).toMatch(/Appearance/);
    for (const n of names) {
      expect(within(appearance).getByRole("button", { name: new RegExp(esc(n)) })).toBeInTheDocument();
    }

    fireEvent.click(within(appearance).getByRole("button", { name: /Daylight/ }));
    expect(document.documentElement.dataset.theme).toBe("mark1");
    expect(localStorage.getItem("ij_theme")).toBe("mark1");
    await waitFor(() =>
      expect(
        within(bar.container).getByRole("button", { name: /Daylight/ }).getAttribute("aria-pressed"),
      ).toBe("true"),
    );

    // ...and the other way round: the bar changes it, the row follows.
    fireEvent.click(within(bar.container).getByRole("button", { name: /Gold & Red/ }));
    await waitFor(() =>
      expect(
        within(appearance).getByRole("button", { name: /Gold & Red/ }).getAttribute("aria-pressed"),
      ).toBe("true"),
    );
    // A per-device choice: it never rides the daemon's Save.
    expect(S.puts).toEqual([]);
  });
});

describe("Settings — /settings?focus=advanced opens the Advanced section", () => {
  it("opens the collapsed Advanced <details> when the deep link asks for it", async () => {
    seedSettings({ default_provider: "claude-cli" });
    window.history.replaceState(null, "", "/settings?focus=advanced");
    render(<SettingsPage />);
    await screen.findByLabelText("Default provider");
    const adv = screen.getByText("Advanced").closest("details") as HTMLDetailsElement;
    expect(adv).toBeTruthy();
    await waitFor(() => expect(adv.open).toBe(true));
    // The switch self-dev points at lives in there.
    expect(within(adv).getByText("Self-development")).toBeInTheDocument();
  });

  it("anti-vacuity: without the deep link Advanced stays collapsed", async () => {
    seedSettings({ default_provider: "claude-cli" });
    render(<SettingsPage />);
    await screen.findByLabelText("Default provider");
    const adv = screen.getByText("Advanced").closest("details") as HTMLDetailsElement;
    expect(adv.open).toBe(false);
  });
});

describe("Settings → backup copy — the status line never reads 'Off' under a ticked box", () => {
  it("mirrorLine(unconfigured) says no folder is set, not 'Off'", async () => {
    const real = await vi.importActual<typeof import("@/components/settings/MaintenanceTools")>(
      "@/components/settings/MaintenanceTools",
    );
    const t = real.mirrorLine(null).text;
    expect(t).not.toMatch(/^Off\b/);
    expect(t).toMatch(/No backup folder set/i);
    expect(t).toMatch(/this PC/);
  });

  it("the media box stays usable before a folder is saved (one press saves both); its status line is not 'Off'", async () => {
    const real = await vi.importActual<typeof import("@/components/settings/MaintenanceTools")>(
      "@/components/settings/MaintenanceTools",
    );
    S.responses["/settings"] = { settings: { backup_mirror_dir: "", backup_mirror_media: true } };
    S.responses["/maintenance/backups"] = { backups: [], mirror: { configured: false } };
    const { container } = render(<real.MaintenanceTools onRestartRequested={() => {}} />);
    const box = container.querySelector("#backup-mirror-media") as HTMLInputElement;
    expect(box).toBeTruthy();
    expect(box.disabled).toBe(false);
    expect(box.checked).toBe(true);
    await waitFor(() =>
      expect(screen.getByTestId("backup-mirror-status").textContent).not.toMatch(/^Off\b/),
    );
  });
});

/* ======================================================================== */
/*  Command palette — Theme commands on the same store                       */
/* ======================================================================== */

async function openPalette() {
  await act(async () => {
    window.dispatchEvent(new Event("ij:open-palette"));
  });
}
async function typeInPalette(value: string) {
  await act(async () => {
    fireEvent.change(screen.getByRole("combobox"), { target: { value } });
  });
}

describe("Command palette — 'Theme: <name>' for every theme", () => {
  it("lists exactly one Theme row per ThemeSwitcher theme", async () => {
    const names = themeNamesFromSwitcher();
    render(<CommandPalette />);
    await openPalette();
    await typeInPalette("theme");
    const labels = screen.queryAllByRole("option").map((o) => o.textContent || "");
    for (const n of names) {
      expect(labels.filter((l) => l.includes(`Theme: ${n}`))).toHaveLength(1);
    }
    expect(labels.filter((l) => /Theme: /.test(l))).toHaveLength(names.length);
  });

  it("running 'Theme: Daylight' applies it through the bar's store, without navigating", async () => {
    const bar = render(<ThemeSwitcher />);
    render(<CommandPalette />);
    await openPalette();
    await typeInPalette("daylight");
    const row = screen
      .queryAllByRole("option")
      .find((o) => (o.textContent || "").includes("Theme: Daylight"));
    expect(row).toBeTruthy();
    await act(async () => {
      fireEvent.click(row!);
    });
    expect(document.documentElement.dataset.theme).toBe("mark1");
    expect(localStorage.getItem("ij_theme")).toBe("mark1");
    expect(S.pushed).toEqual([]);
    expect(screen.queryByRole("combobox")).toBeNull(); // the palette closed
    await waitFor(() =>
      expect(
        within(bar.container).getByRole("button", { name: /Daylight/ }).getAttribute("aria-pressed"),
      ).toBe("true"),
    );
  });
});

/* ======================================================================== */
/*  Self-development — one plain sentence, the raw reason kept once          */
/* ======================================================================== */

const OFF_REASON = "self-dev disabled (set self_dev_enabled = true)";

describe("Self-development — switched off reads as 'Turned off', not a config key twice", () => {
  it("shows 'Turned off', points at Settings → Advanced, says it restarts, keeps the raw reason ONCE", () => {
    S.responses["/self-dev"] = {
      enabled: false,
      repo_root: "C:/src/iron-jarvis",
      available: false,
      reason: OFF_REASON,
    };
    const { container } = render(<SelfDevPage />);
    expect(screen.getByText("Turned off")).toBeInTheDocument();
    expect(screen.queryByText("Not available yet")).toBeNull();
    const all = container.textContent || "";
    expect(all.split(OFF_REASON).length - 1).toBe(1); // reachable, not repeated
    expect(visibleText(container)).toMatch(/Settings → Advanced/);
    expect(visibleText(container)).toMatch(/restart/i);
    // The way to turn it on lands on the switch, not the top of Settings.
    const links = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(links).toContain("/settings?focus=advanced");
    expect(links).not.toContain("/settings");
  });

  it("enabled but no repo found keeps its own copy (it is NOT 'Turned off')", () => {
    S.responses["/self-dev"] = {
      enabled: true,
      repo_root: null,
      available: false,
      reason: "Iron Jarvis git repo not found (set self_dev_root)",
    };
    render(<SelfDevPage />);
    expect(screen.queryByText("Turned off")).toBeNull();
    expect(screen.getAllByRole("link").some((a) => (a.getAttribute("href") || "").startsWith("/settings"))).toBe(true);
  });

  it("daemon down: offline notice, never 'Turned off'", () => {
    S.errors["/self-dev"] = 0;
    render(<SelfDevPage />);
    expect(screen.getByText(/Daemon offline or unreachable/)).toBeInTheDocument();
    expect(screen.queryByText("Turned off")).toBeNull();
  });

  it("guard: available still offers the review-gated Maintainer form; title matches the nav", () => {
    S.responses["/self-dev"] = { enabled: true, repo_root: "C:/x", available: true, reason: "ready" };
    render(<SelfDevPage />);
    expect(screen.getByRole("button", { name: /Start Maintainer/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(labelForPath("/self-dev"));
  });
});

/* ======================================================================== */
/*  Updates — local changes in words, git mechanics one disclosure down      */
/* ======================================================================== */

describe("Updates (source checkout) — 'local changes', not 'dirty' / git verbs", () => {
  const DIRTY = {
    available: false,
    behind: 0,
    current: "abc1234def",
    remote: "abc1234def",
    branch: "master",
    clean: false,
    reason: "working tree has uncommitted changes — commit or stash before updating",
  };

  it("says updates are paused by local changes; 'dirty', 'git pull', 'stash' are not in sight", () => {
    S.responses["/update/check"] = DIRTY;
    const { container } = render(<UpdatesPage />);
    const seen = visibleText(container);
    expect(seen).toMatch(/local changes/i);
    expect(seen).not.toMatch(/\bdirty\b/i);
    expect(seen).not.toMatch(/git pull/);
    expect(seen).not.toMatch(/\bstash\b/);
  });

  it("anti-vacuity: the exact steps and the daemon's reason stay one click away; Apply/Re-check remain", () => {
    S.responses["/update/check"] = DIRTY;
    const { container } = render(<UpdatesPage />);
    const details = Array.from(container.querySelectorAll("details"));
    expect(details.length).toBeGreaterThan(0);
    const folded = details.map((d) => d.textContent || "").join(" ");
    expect(folded).toMatch(/git pull --ff-only/);
    expect(container.textContent).toContain(DIRTY.reason);
    expect(screen.getByRole("button", { name: /Re-check/ })).toBeInTheDocument();
    // v1.316.0: the apply button is still there (disabled) — now a quiet
    // ghost, because brand-fill is kept for an update you can actually apply.
    const apply = screen.getByRole("button", { name: /Paused — local changes/ }) as HTMLButtonElement;
    expect(apply.disabled).toBe(true);
  });
});

describe("Updates — a FAILED check is not 'local changes' (coordinator, v1.314.0)", () => {
  // core/updates.py returns clean:false with reason "git error: …" when the
  // probe throws. That is not the user's local changes.
  const GIT_ERROR = {
    available: false,
    behind: 0,
    current: null,
    remote: null,
    branch: null,
    clean: false,
    reason: "git error: fatal: not a git repository",
  };

  it("says it couldn't check, keeps the reason, and never claims local changes", () => {
    S.responses["/update/check"] = GIT_ERROR;
    const { container } = render(<UpdatesPage />);
    const seen = visibleText(container);
    expect(seen).toMatch(/Couldn.t check/);
    // The STATUS claims (badge, sentence, button) — the page's general help
    // line ("It won't run while this copy has local changes") is always true.
    expect(seen).not.toMatch(/Local changes(?!\.)|You have local changes|Paused — local changes/);
    expect(container.textContent).toContain(GIT_ERROR.reason);
  });
});

describe("PageGrid carry-over — /updates and /self-dev", () => {
  it.each([
    ["app/updates/page.tsx", 2],
    ["app/self-dev/page.tsx", 2],
  ])("%s lays out with <PageGrid cols={3}> and keeps its desktop spans", (rel, spans) => {
    const text = src(rel);
    expect(text).toMatch(/import\s*\{\s*PageGrid\s*\}\s*from\s*"@\/components\/PageGrid"/);
    expect((text.match(/<PageGrid\s+cols=\{3\}/g) ?? []).length).toBe(1);
    expect(text).not.toMatch(/"grid gap-6 lg:grid-cols-\d/);
    expect((text.match(/lg:col-span-\d/g) ?? []).length).toBe(spans);
  });
});

describe("Tools — no 'fleet' word in the scope line", () => {
  it("says 'available to all your agents'", () => {
    const text = src("app/tools/page.tsx");
    expect(text).not.toMatch(/available to every agent in this fleet/);
    expect(text).toMatch(/available to all your agents/);
  });
});

/* ======================================================================== */
/*  Memory — "What I've learned" tells the truth and drops the jargon        */
/* ======================================================================== */

const NOW = new Date().toISOString();
const LESSONS = {
  lessons: [
    // Newest first, as the daemon sends them: the reflection is on top.
    {
      id: "r1",
      text: "Worked well for build: Done. Wrote RESULT.md summarizing the task.",
      source: "reflection",
      weight: 1,
      scope: "user",
      created_at: NOW,
    },
    { id: "p1", text: "Keep answers short", source: "preference", weight: 5, scope: "user", created_at: NOW },
    { id: "f1", text: "Use numbered steps", source: "feedback", weight: 3, scope: "project:acme", created_at: NOW },
  ],
};
const IMPROVEMENT = {
  outcomes: { count: 5, avg_score: 1, baseline: 1 },
  agents: [
    { agent_type: "builder", sessions: 5, avg_score: 1, success_rate: 1, trend: 0, recent_scores: [] },
  ],
  lessons: [
    {
      lesson_id: "p1",
      text: "Keep answers short",
      source: "preference",
      base_weight: 5,
      weight_bonus: 0,
      effective_weight: 5,
      applied_count: 4,
      avg_score: 1,
      success_rate: 1,
    },
    {
      lesson_id: "r1",
      text: "Worked well for build: Done. Wrote RESULT.md summarizing the task.",
      source: "reflection",
      base_weight: 1,
      weight_bonus: 0,
      effective_weight: 1,
      applied_count: 4,
      avg_score: 1,
      success_rate: 1,
    },
  ],
};

function renderLessons() {
  S.responses["/lessons?limit=50"] = LESSONS;
  S.responses["/improvement"] = IMPROVEMENT;
  return render(<Lessons />);
}
/** The lesson list's <li> for one lesson (not the stats table row). */
const lessonRow = (text: string) =>
  screen.getAllByText(text).map((e) => e.closest("li")).find(Boolean) as HTMLElement;

describe("What I've learned — copy matches the daemon (reflections are not sent)", () => {
  it("a Reflection row says it is not sent to the model; a preference row does not", () => {
    renderLessons();
    expect(lessonRow(LESSONS.lessons[0].text).textContent).toMatch(/not sent to the model/);
    expect(lessonRow("Keep answers short").textContent).not.toMatch(/not sent to the model/);
  });

  it("nothing on the tab claims every lesson reaches every run", () => {
    const { container } = renderLessons();
    expect(container.textContent).not.toMatch(/every future run/i);
    expect(container.textContent).not.toMatch(/every chat and run/i);
  });

  it("reflections sit after preferences, under 'Notes about past jobs'", () => {
    renderLessons();
    expect(screen.getByText("Notes about past jobs")).toBeInTheDocument();
    const pref = lessonRow("Keep answers short");
    const refl = lessonRow(LESSONS.lessons[0].text);
    expect(pref.compareDocumentPosition(refl) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("the Memory tab blurb no longer says lessons go into every future run", () => {
    const text = src("components/memory/MemorySurface.tsx");
    const m = /id:\s*"lessons"[\s\S]*?blurb:\s*([\s\S]*?),\n\s*\}/.exec(text);
    expect(m).toBeTruthy();
    expect(m![1]).not.toMatch(/every future run/);
    expect(m![1]).toMatch(/not sent to the model/);
  });
});

describe("What I've learned — no 'weight 3 · user', the numbers one disclosure down", () => {
  it("rows show no 'weight N' and no default 'user' scope; the number stays in a title", () => {
    const { container } = renderLessons();
    expect(visibleText(container)).not.toMatch(/\bweight \d/);
    const pref = lessonRow("Keep answers short");
    expect(visibleText(pref)).not.toMatch(/\buser\b/);
    expect(titles(pref).some((t) => /\b5\b/.test(t))).toBe(true);
    // A non-default scope is still said.
    expect(lessonRow("Use numbered steps").textContent).toMatch(/acme/);
  });

  it("'By agent' and 'Lesson weights' live in a closed <details>; Reflect stays outside", () => {
    const { container } = renderLessons();
    for (const label of [/By agent/, /Lesson weights/]) {
      const el = screen.getByText(label);
      const d = el.closest("details") as HTMLDetailsElement | null;
      expect(d).toBeTruthy();
      expect(d!.open).toBe(false);
    }
    const reflect = screen.getByRole("button", { name: /Reflect on recent sessions/ });
    expect(reflect.closest("details")).toBeNull();
    // The summary line outside the disclosure is plain words.
    expect(visibleText(container)).not.toMatch(/avg score|baseline/);
  });

  it("agents read by name ('Builder'), and reflections carry no 'applied N×'", () => {
    const { container } = renderLessons();
    const d = screen.getByText(/Lesson weights/).closest("details") as HTMLElement;
    const all = Array.from(container.querySelectorAll("*"));
    expect(all.some((e) => e.textContent === "Builder")).toBe(true);
    expect(all.some((e) => e.children.length === 0 && e.textContent === "builder")).toBe(false);
    expect((d.textContent || "").match(/applied \d+×/g) ?? []).toHaveLength(1); // p1 only
  });

  it("guard: Distill now, Reflect and every Forget control are still here", () => {
    renderLessons();
    expect(screen.getByRole("button", { name: /Distill now/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Reflect on recent sessions/ })).toBeInTheDocument();
    expect(screen.getAllByTitle(/Forget this lesson/)).toHaveLength(3);
  });
});

/* ======================================================================== */
/*  Connections — the inherited card: roomy and plain                        */
/* ======================================================================== */

function conn(provider: string, over: Record<string, unknown> = {}) {
  return {
    provider,
    display_name: provider,
    method: "api_key",
    connected: false,
    status: "disconnected",
    account: "",
    source: "",
    scopes: [],
    ...over,
  };
}
function seedConnections(connections: unknown[]) {
  S.responses["/connections"] = { connections };
  S.responses["/routing/quality"] = { bar: 0.75, min_samples: 3, rows: [] };
  S.responses["/fleet"] = { nodes: [] };
  S.health = {
    default_provider: "ollama",
    providers: [
      { provider: "anthropic", available: true, inherited_from: "claude-cli" },
      { provider: "openai", available: true },
    ],
  };
}
const cardOf = (provider: string) => document.getElementById(`conn-card-${provider}`) as HTMLElement;
const pillText = (card: HTMLElement) =>
  (within(card).getByTestId("conn-status-pill").textContent || "").replace(/\s+/g, " ").trim();

describe("Connections — an inherited login reads 'Connected · via Claude Code'", () => {
  it("names the product, not the CLI id, in a pill that does not wrap", async () => {
    seedConnections([
      conn("anthropic", {
        display_name: "Anthropic",
        connected: true,
        status: "connected",
        source: "inherited from claude-cli",
      }),
      conn("openai", {
        display_name: "OpenAI",
        connected: true,
        status: "connected",
        source: "vault",
        account: "sk-…1234",
      }),
    ]);
    render(<ConnectionsPage />);
    await screen.findByText("Anthropic");
    const anthropic = cardOf("anthropic");
    expect(pillText(anthropic)).toBe("Connected · via Claude Code");
    expect(within(anthropic).getByTestId("conn-status-pill").className).toMatch(/\bwhitespace-nowrap\b/);
    expect(visibleText(anthropic)).not.toMatch(/claude-cli/);
    // Its own line says where the login lives and that no key is kept here.
    const why = within(anthropic).getByText(/no key stored here/);
    expect(why.tagName).toBe("P");
    expect(why.textContent).toMatch(/Claude Code/);
    // No key is involved, so no "API key" method chip.
    expect(within(anthropic).queryByText("API key")).toBeNull();

    // Anti-vacuity: a vault login is still told apart, keeps its chip + Disconnect.
    const openai = cardOf("openai");
    expect(pillText(openai)).toBe("Connected");
    expect(within(openai).getByText("API key")).toBeInTheDocument();
    expect(within(openai).getByRole("button", { name: /Disconnect/ })).toBeInTheDocument();
    // ...and the inherited card still has Test + Make default, no Disconnect.
    expect(within(anthropic).getByRole("button", { name: /Test/ })).toBeInTheDocument();
    expect(within(anthropic).getByRole("button", { name: /Make default/ })).toBeInTheDocument();
    expect(within(anthropic).queryByRole("button", { name: /Disconnect/ })).toBeNull();
  });

  it("an inherited Codex login reads 'via Codex'", async () => {
    seedConnections([
      conn("openai", {
        display_name: "OpenAI",
        connected: true,
        status: "connected",
        source: "inherited from codex-cli",
      }),
    ]);
    render(<ConnectionsPage />);
    await screen.findByText("OpenAI");
    expect(pillText(cardOf("openai"))).toBe("Connected · via Codex");
    expect(visibleText(cardOf("openai"))).not.toMatch(/codex-cli/);
  });
});

/* ======================================================================== */
/*  REST hookups + Secrets — labels first, raw values behind a title         */
/* ======================================================================== */

describe("REST hookups — one plain state instead of 'Disabled' + 'Unconfigured'", () => {
  const INTEGRATIONS = {
    integrations: [
      { id: "rest_api", kind: "rest", display_name: "Generic REST API", enabled: false, configured: false, required_secrets: [] },
      { id: "crm", kind: "rest", display_name: "CRM", enabled: true, configured: true, required_secrets: [] },
      { id: "billing", kind: "rest", display_name: "Billing", enabled: false, configured: true, required_secrets: [] },
    ],
  };
  const card = (name: string) => screen.getByText(name).closest("section") as HTMLElement;

  it("never-touched reads 'Not set up yet'; configured+on 'Ready'; configured+off 'Off'", () => {
    S.responses["/integrations"] = INTEGRATIONS;
    render(<RestHookups />);
    const generic = card("Generic REST API");
    expect(visibleText(generic)).toMatch(/Not set up yet/);
    expect(visibleText(generic)).not.toMatch(/unconfigured|\bdisabled\b/i);
    expect(visibleText(card("CRM"))).toMatch(/\bReady\b/);
    expect(visibleText(card("Billing"))).toMatch(/\bOff\b/);
  });

  it("the id moves to a title, and Enable / Configure / Test all remain", () => {
    S.responses["/integrations"] = INTEGRATIONS;
    render(<RestHookups />);
    const generic = card("Generic REST API");
    expect(visibleText(generic)).not.toMatch(/\brest_api\b/);
    expect(titles(generic).some((t) => t.includes("rest_api"))).toBe(true);
    expect(within(generic).getByRole("button", { name: /Enable/ })).toBeInTheDocument();
    expect(within(generic).getByRole("button", { name: /Configure/ })).toBeInTheDocument();
    expect(within(generic).getByRole("button", { name: /Test/ })).toBeInTheDocument();
  });
});

describe("Secrets — kinds in words, a placeholder that is not a fake value", () => {
  const SECRETS = {
    secrets: [{ name: "OPENAI_API_KEY", kind: "api_key", description: "", updated_at: NOW }],
  };

  it("the value box does not look pre-filled; kinds read as words with the same values", () => {
    S.responses["/secrets"] = SECRETS;
    const { container } = render(<SecretsPage />);
    const value = container.querySelector('input[type="password"]') as HTMLInputElement;
    expect(value.placeholder).not.toMatch(/^[•●*]+$/);
    expect(value.placeholder.trim()).not.toBe("");
    const sel = screen.getByLabelText("Secret kind") as HTMLSelectElement;
    expect(optionPairs(sel)).toEqual([
      ["api_key", "API key"],
      ["oauth", "OAuth token"],
      ["token", "Access token"],
      ["password", "Password"],
      ["generic", "Other"],
    ]);
  });

  it("a stored secret's kind reads 'API key', the raw kind in a title; Delete is still two presses", () => {
    S.responses["/secrets"] = SECRETS;
    render(<SecretsPage />);
    const row = screen.getByText("OPENAI_API_KEY").closest("tr") as HTMLElement;
    expect(visibleText(row)).toMatch(/API key/);
    expect(visibleText(row)).not.toMatch(/api_key/);
    expect(titles(row).some((t) => t.includes("api_key"))).toBe(true);
    fireEvent.click(within(row).getByRole("button", { name: /Delete/ }));
    expect(within(row).getByRole("button", { name: /Confirm/ })).toBeInTheDocument();
  });

  it("storing still POSTs the raw kind token", async () => {
    S.responses["/secrets"] = SECRETS;
    const { container } = render(<SecretsPage />);
    fireEvent.change(screen.getByPlaceholderText("OPENAI_API_KEY"), { target: { value: "X_TOKEN" } });
    fireEvent.change(container.querySelector('input[type="password"]') as HTMLInputElement, {
      target: { value: "s3cret" },
    });
    fireEvent.change(screen.getByLabelText("Secret kind"), { target: { value: "api_key" } });
    fireEvent.click(screen.getByRole("button", { name: /Store secret/ }));
    await waitFor(() => expect(S.posts.some((p) => p.path === "/secrets")).toBe(true));
    const body = S.posts.find((p) => p.path === "/secrets")!.body as Record<string, unknown>;
    expect(body.kind).toBe("api_key");
  });
});

/* ======================================================================== */
/*  Fleet — plain copy and an empty state with a way forward                 */
/* ======================================================================== */

describe("Fleet — words and the empty state", () => {
  it("the code-route line is a sentence, not 'code routing is off'", () => {
    const off = codeRouteText({ enabled: false, target: "fleet-spark:glm" });
    expect(off).not.toMatch(/code routing is off/);
    expect(off).toMatch(/^Coding work/);
    expect(codeRouteText({ enabled: true, target: "fleet-spark:glm" })).toMatch(/^Coding work goes to /);
    // Guard: the daemon's own plain reason still wins, and nothing is invented.
    expect(codeRouteText({ enabled: true, target: "x", effective: { why: "Coding goes to Spark." } })).toBe(
      "Coding goes to Spark.",
    );
    expect(codeRouteText(null)).toBeNull();
  });

  it("no nodes: the primary way forward is Connections' endpoint form; Settings stays as a second link", () => {
    S.responses["/fleet"] = { nodes: [] };
    render(<FleetPage />);
    const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href") || "");
    expect(hrefs.some((h) => /^\/connections(\?focus=endpoints|#conn-card-custom)$/.test(h))).toBe(true);
    expect(hrefs).toContain("/settings");
    // The page's own add-by-URL form is still right there.
    expect(screen.getByLabelText("Base URL")).toBeInTheDocument();
  });

  it("the example address is not a real-looking private tailnet IP", () => {
    S.responses["/fleet"] = { nodes: [] };
    render(<FleetPage />);
    expect((screen.getByLabelText("Base URL") as HTMLInputElement).placeholder).not.toMatch(/^https?:\/\/100\./);
  });

  it("the savings line names the baseline in words, with the raw id in a title", () => {
    S.responses["/fleet"] = { nodes: [] };
    S.responses["/fleet/usage"] = {
      days: 30,
      local_tokens: 1000,
      cloud_tokens: 500,
      est_avoided_usd: 1.23,
      comparison_provider: "anthropic",
      comparison_model: "claude-opus-4-8",
      // the daemon's own wording (routes/fleet.py) — it names the raw id
      basis: "estimate: what the local tokens would have cost on anthropic:claude-opus-4-8 at list price",
    };
    const { container } = render(<FleetPage />);
    const seen = visibleText(container);
    expect(seen).toMatch(/avoided vs/);
    expect(seen).not.toContain("anthropic:claude-opus-4-8");
    expect(seen).toMatch(/Anthropic|Claude Opus/);
    expect(titles(container).some((t) => t.includes("anthropic:claude-opus-4-8"))).toBe(true);
    // v1.314.0 review: the basis is a RECORD — one press down, never deleted.
    expect(seen).toMatch(/How this is estimated/);
    const basis = screen.getByText(/estimate: what the local tokens would have cost/);
    expect(basis.closest("details")).not.toBeNull();
  });

  it("the sampling chip says 'Live updates'; the title still matches the crumb", () => {
    S.responses["/fleet"] = { nodes: [], sampling: { active: true, interval: 2 } };
    render(<FleetPage />);
    expect(screen.getByText("Live updates")).toBeInTheDocument();
    // v1.313.0 made the crumb read "Fleet" (CRUMB_LABELS) — the title stays it.
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(labelForPath("/fleet"));
  });
});

/* ======================================================================== */
/*  Activity — a human log; undo stays one press away                        */
/* ======================================================================== */

describe("Activity — event words", () => {
  it("names delegation and desktop-notification events", () => {
    expect(humanSummary("delegation.started")).toBe("Handed work to a teammate");
    expect(humanSummary("delegation.completed")).toBe("Teammate finished");
    expect(humanSummary("comm.desktop")).toBe("Sent a desktop notification");
  });

  it("an agent state change reads as a sentence", () => {
    expect(humanSummary("agent running→completed")).toBe("Agent went from running to completed");
  });

  it("never says 'mock' (the fallback event and a demo token row)", () => {
    expect(humanSummary("provider.downgraded")).not.toMatch(/mock/i);
    expect(humanSummary("mock/mock-1 · 0+0 tok")).not.toMatch(/mock/i);
    expect(humanSummary("mock/mock-1 · 0+0 tok")).toMatch(/Demo model/);
    const real = humanSummary("claude-cli/claude-opus-4-8 · 120+40 tok");
    expect(real).toMatch(/Claude Code/);
    expect(real).toContain("claude-opus-4-8");
  });

  it("guard: the v1.232 words and the verbatim fallback are unchanged", () => {
    expect(humanSummary("provider.routed brain")).toBe("Picked a model for this turn · brain");
    expect(humanSummary("weird.new_kind x")).toBe("weird.new_kind x");
    expect(humanSummary("wrote report.md")).toBe("wrote report.md");
  });
});

const T0 = Date.now();
const at = (min: number) => new Date(T0 - min * 60_000).toISOString();
function entry(id: string, over: Record<string, unknown>) {
  return { id, ts: at(Number(id.slice(1))), kind: "lifecycle", actor: "job:mission", session_id: "session_04f556aa", ...over };
}
const AUDIT = {
  entries: [
    entry("e1", { summary: "agent running→completed" }),
    entry("e2", { summary: "agent.completed" }),
    entry("e3", { kind: "token", summary: "mock/mock-1 · 0+0 tok", input_tokens: 0, output_tokens: 0 }),
    entry("e4", { kind: "tool", tool: "write_file", summary: "write_file ok", undoable: true, reversible: true, ok: true }),
    entry("e5", { summary: "session.completed" }),
    entry("e6", { summary: "agent.started" }),
    entry("e7", { kind: "tool", tool: "read_file", summary: "denied read_file: outside", verdict: "deny", ok: false }),
    entry("e8", { summary: "agent.started", session_id: "session_99aa11bb" }),
  ],
  next_cursor: null,
  total: 8,
};

describe("Activity — runs of lifecycle steps fold; undo/denied rows never do", () => {
  it("folds consecutive same-session lifecycle (and 0+0 token) rows into 'N steps'", async () => {
    S.responses["/audit?limit=50"] = AUDIT;
    const seen: FeedStats[] = [];
    render(<TimeTravelFeed onStats={(s) => seen.push(s)} />);
    const three = await screen.findByRole("button", { name: /3 steps/ });
    expect(three.getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByRole("button", { name: /2 steps/ })).toBeInTheDocument();
    expect(screen.queryByText("Agent went from running to completed")).toBeNull();

    // Undo stays one press away, and the denied row stays in sight.
    expect(screen.getAllByRole("button", { name: /^Undo$/ })).toHaveLength(1);
    expect(screen.getByText("denied")).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(three);
    });
    expect(three.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Agent went from running to completed")).toBeInTheDocument();
    expect(screen.getByText("Agent finished")).toBeInTheDocument();

    // Nothing was dropped from the audit trail's numbers.
    expect(seen.at(-1)?.loaded).toBe(8);
    expect(seen.at(-1)?.undoable).toBe(1);
  });

  // v1.314.0 review: the fixture's undoable and denied rows are kind "tool",
  // which never folds anyway — so these pin the exclusions themselves.
  it("never folds a lifecycle/0+0 row that can be undone, was reverted, or was refused", () => {
    const fold = (over: Record<string, unknown>) =>
      isFoldable(entry("e1", over) as unknown as Parameters<typeof isFoldable>[0]);
    expect(fold({})).toBe(true); // anti-vacuity: a plain lifecycle row folds
    expect(fold({ kind: "token", input_tokens: 0, output_tokens: 0 })).toBe(true);
    expect(fold({ undoable: true })).toBe(false);
    expect(fold({ kind: "token", input_tokens: 0, output_tokens: 0, undone: true })).toBe(false);
    expect(fold({ verdict: "deny" })).toBe(false);
    expect(fold({ ok: false })).toBe(false);
  });

  it("an undoable row breaks a run: the rows either side stay single", () => {
    const rows = [
      entry("e1", { summary: "agent.completed" }),
      entry("e2", { summary: "agent.started", undoable: true }),
      entry("e3", { summary: "session.completed" }),
    ] as unknown as Parameters<typeof foldEntries>[0];
    expect(foldEntries(rows).map((it) => it.type)).toEqual(["row", "row", "row"]);
    // anti-vacuity: without the undoable flag the same three rows are ONE group
    const plain = rows.map((r) => ({ ...r, undoable: false }));
    expect(foldEntries(plain).map((it) => it.type)).toEqual(["group"]);
  });

  it("an open group stays open when a live refresh adds the run's next step on top", async () => {
    S.responses["/audit?limit=50"] = AUDIT;
    const { rerender } = render(<TimeTravelFeed />);
    const three = await screen.findByRole("button", { name: /3 steps/ });
    await act(async () => {
      fireEvent.click(three);
    });
    expect(three.getAttribute("aria-expanded")).toBe("true");

    // The mission writes one more lifecycle row in the same session; the
    // feed refetches on a live event (debounced ~600 ms).
    S.responses["/audit?limit=50"] = {
      ...AUDIT,
      entries: [entry("e0", { summary: "agent.started" }), ...AUDIT.entries],
      total: 9,
    };
    S.events = [{ id: "ev-1", type: "llm.completed", session_id: "session_04f556aa" }];
    rerender(<TimeTravelFeed />);
    const four = await screen.findByRole("button", { name: /4 steps/ }, { timeout: 3000 });
    expect(four.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Agent went from running to completed")).toBeInTheDocument();
  });

  it("guard: Undo is still two presses", async () => {
    S.responses["/audit?limit=50"] = AUDIT;
    render(<TimeTravelFeed />);
    const undo = await screen.findByRole("button", { name: /^Undo$/ });
    fireEvent.click(undo);
    expect(screen.getByRole("button", { name: /Confirm undo\?/ })).toBeInTheDocument();
    expect(S.posts).toEqual([]);
  });

  it("the actor and the session read in words; the raw values live in titles, the link is unchanged", async () => {
    S.responses["/audit?limit=50"] = AUDIT;
    render(<TimeTravelFeed />);
    const undo = await screen.findByRole("button", { name: /^Undo$/ });
    const row = undo.closest("li") as HTMLElement;
    const seen = visibleText(row);
    expect(seen).toMatch(/\bMission\b/);
    expect(seen).not.toMatch(/job:mission/);
    expect(titles(row).some((t) => t.includes("job:mission"))).toBe(true);
    const link = within(row).getByRole("link");
    expect(link.getAttribute("href")).toBe("/sessions/session_04f556aa");
    expect(visibleText(link)).not.toMatch(/session_04f5/);
    expect(titles(row).some((t) => t.includes("session_04f556aa"))).toBe(true);
  });
});

describe("Activity page — exports say what they are", () => {
  it("'Export' + the format on both links; the export URLs are unchanged", async () => {
    S.responses["/audit?limit=50"] = { entries: [], next_cursor: null, total: 0 };
    render(<ActivityPage />);
    const links = screen.getAllByRole("link");
    const mdLink = links.find((a) => (a.getAttribute("href") || "").includes("format=md"));
    const jsonLink = links.find((a) => (a.getAttribute("href") || "").includes("format=json"));
    expect(mdLink).toBeTruthy();
    expect(jsonLink).toBeTruthy();
    expect(mdLink!.textContent).toMatch(/Export/i);
    expect(mdLink!.textContent).toMatch(/\.md\b|Markdown/i);
    expect(jsonLink!.textContent).toMatch(/Export/i);
    expect(jsonLink!.textContent).toMatch(/\.json\b|JSON/);
  });
});
