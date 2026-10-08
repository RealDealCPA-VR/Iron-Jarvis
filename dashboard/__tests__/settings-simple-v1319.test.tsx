/**
 * v1.319.0 — the calm first experience, part 2: Settings in Simple mode
 * opens on the everyday settings (which AI answers, its model, the default
 * persona), with the whole form one press away ("Show all settings") and
 * always shown in Advanced. A link INTO the form (?focus=…, #settings-…)
 * opens the whole form. The full form itself is pinned by the files that
 * now set Advanced (ux-wave4-home-system-v1316, ux-wave2-system-memory-v1314,
 * trust-posture-v1298).
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

beforeEach(() => {
  S.responses = {};
  S.errors = {};
  S.puts = [];
  S.posts = [];
  S.health = null;
  localStorage.clear();
  window.history.replaceState(null, "", "/settings");
});
afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

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

const POWER = ["Strict model pin", "Ollama server URL", "Autonomy (the pulse)", "Sandbox runtime", "Self-development"];

describe("Settings — Simple mode opens on the everyday settings", () => {
  it("shows which AI answers, its model and the default persona — nothing else", async () => {
    await renderSettings();
    expect(screen.getByLabelText("Default provider")).toBeInTheDocument();
    expect(screen.getByLabelText("Default persona")).toBeInTheDocument();
    expect(screen.getByText("Which AI answers")).toBeInTheDocument();
    for (const label of POWER) expect(screen.queryAllByLabelText(label), label).toHaveLength(0);
    expect(screen.queryByRole("navigation", { name: "Settings sections" })).toBeNull();
    expect(document.body.textContent).not.toMatch(/config\.toml|daemon restarts/);
  });

  it("'Show all settings' brings back the whole form, jump index included", async () => {
    await renderSettings();
    fireEvent.click(within(screen.getByTestId("settings-show-all")).getByRole("button", { name: "Show all settings" }));
    for (const label of POWER) expect(screen.getAllByLabelText(label).length, label).toBeGreaterThan(0);
    expect(screen.getByRole("navigation", { name: "Settings sections" })).toBeInTheDocument();
    expect(screen.queryByTestId("settings-show-all")).toBeNull();
  });

  it("a link into the form (?focus=advanced) opens the whole form", async () => {
    window.history.replaceState(null, "", "/settings?focus=advanced");
    await renderSettings();
    await waitFor(() => expect(screen.getAllByLabelText("Self-development").length).toBeGreaterThan(0));
  });

  it("Advanced shows the whole form at once, with no 'Show all' box", async () => {
    localStorage.setItem("ij_nav_advanced", "1");
    await renderSettings();
    await waitFor(() => expect(screen.getAllByLabelText("Strict model pin").length).toBeGreaterThan(0));
    expect(screen.queryByTestId("settings-show-all")).toBeNull();
  });

  it("the short view still saves through the one Save, with only what changed", async () => {
    await renderSettings();
    fireEvent.change(screen.getByLabelText("Default model"), { target: { value: "claude-opus-4-8" } });
    const persona = screen.getByLabelText("Default persona") as HTMLSelectElement;
    fireEvent.change(persona, { target: { value: "assistant" } });
    // A change the short view can make is saved like any other.
    const provider = screen.getByLabelText("Default provider") as HTMLSelectElement;
    fireEvent.change(provider, { target: { value: "mock" } });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await waitFor(() => expect(S.puts.length).toBe(1));
    const sent = (S.puts[0].body as { values: Record<string, unknown> }).values;
    expect(sent.default_provider).toBe("mock");
    expect(Object.keys(sent)).not.toContain("strict_model_pin");
  });
});

describe("the title bar's theme dots are Advanced-only (Simple keeps them in the drawer)", () => {
  it("AdvancedOnly renders nothing in Simple and its children in Advanced", async () => {
    const { AdvancedOnly } = await import("@/components/AdvancedOnly");
    render(<AdvancedOnly><span>dots</span></AdvancedOnly>);
    expect(screen.queryByText("dots")).toBeNull();
    cleanup();
    localStorage.setItem("ij_nav_advanced", "1");
    render(<AdvancedOnly><span>dots</span></AdvancedOnly>);
    expect(await screen.findByText("dots")).toBeInTheDocument();
  });

  it("the layout wraps the BAR's ThemeSwitcher (and only it) in AdvancedOnly", async () => {
    const { readFileSync } = await import("node:fs");
    const src = readFileSync("app/layout.tsx", "utf8").replace(/\r\n/g, "\n");
    expect(src).toMatch(/<AdvancedOnly>\s*<div className="hidden sm:block">\s*<ThemeSwitcher \/>/);
    expect(src).toMatch(/<ModelSwitcher \/>/);
    expect(src).not.toMatch(/<AdvancedOnly>\s*<ModelSwitcher/);
  });
});
