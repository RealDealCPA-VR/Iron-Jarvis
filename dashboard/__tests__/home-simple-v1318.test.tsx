/**
 * v1.318.0 — the calm first experience, part 1: Simple mode (the default)
 * gets a seven-place menu by job, tabs between a place's pages, and a home
 * that leads with one ask box instead of ~30 controls. Advanced is today's
 * layout, untouched (its Overview is pinned by the files that now set
 * Advanced: overview-wave2-v1310, ux-wave1-firstrun-v1313, …).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const W = vi.hoisted(() => ({
  responses: {} as Record<string, unknown>,
  path: "/",
  push: vi.fn(),
}));

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    const r = W.responses[path];
    return r === undefined ? {} : r;
  },
  post: async () => ({}),
  put: async () => ({}),
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: W.push, replace: vi.fn(), refresh: () => {}, prefetch: () => {}, back: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => W.path,
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, prefetch: _p, ...rest }: Record<string, unknown>) =>
      createElement("a", { href, ...rest }, children as never),
  };
});
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const DROP = new Set(["initial", "animate", "exit", "transition", "variants", "layout", "layoutId", "whileHover", "whileTap"]);
  const cache = new Map<string, (p: Record<string, unknown>) => unknown>();
  const tagFor = (tag: string) => {
    let c = cache.get(tag);
    if (!c) {
      c = (props: Record<string, unknown>) => {
        const rest: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(props)) if (!DROP.has(k)) rest[k] = v;
        return createElement(tag, rest);
      };
      cache.set(tag, c);
    }
    return c;
  };
  return {
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: unknown }) => createElement(Fragment, null, children as never),
    LazyMotion: ({ children }: { children?: unknown }) => createElement(Fragment, null, children as never),
    domAnimation: {},
    domMax: {},
    useReducedMotion: () => true,
    motion: new Proxy({} as Record<string, unknown>, { get: (_t, tag) => tagFor(String(tag)) }),
  };
});

import { __resetApiCache } from "@/lib/apiCache";
import OverviewPage from "@/app/page";
import { NavDrawer } from "@/components/Sidebar";
import { PageHeader } from "@/components/PageHeader";
import { HUBS, hubFor, tabLabel, visibleTabs } from "@/lib/hubs";
import { NAV_ENTRIES, NON_RAIL_ENTRIES } from "@/lib/nav";
import { writeAdvanced } from "@/lib/uiMode";

function responses(extra: Record<string, unknown> = {}) {
  W.responses = {
    "/onboarding": { version: "1.318.0", first_run: false, doctor: { ok: true, checks: [] }, checklist: [], next_step: null },
    "/health": { status: "ok", version: "1.318.0", providers: [], default_provider: "anthropic", default_model: "claude-opus-4-8" },
    "/metrics": { sessions_evaluated: 0, avg_completion: 0, avg_tool_success_rate: 0, avg_latency_s: 0, total_tool_invocations: 0, event_count: 0 },
    "/sessions?limit=50": { sessions: [] },
    "/chat/threads": {
      threads: [
        { id: "t1", title: "Quarterly numbers", updated_at: new Date().toISOString() },
        { id: "t2", title: "Email to the landlord", updated_at: new Date().toISOString() },
        { id: "t3", title: "", updated_at: new Date().toISOString() },
        { id: "t4", title: "Older one", updated_at: new Date().toISOString() },
      ],
    },
    ...extra,
  };
}

beforeEach(() => {
  __resetApiCache();
  W.path = "/";
  W.push.mockReset();
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
  responses();
});
afterEach(() => cleanup());

/** Every page a person can open: the menu, the search-only pages, and the
 *  Directory (reached from Connections; its own title, no menu row). */
const ALL_PAGES = [...[...NAV_ENTRIES, ...NON_RAIL_ENTRIES].map((e) => e.href), "/marketplace"];

describe("A. the seven places (lib/hubs.ts)", () => {
  it("every page in the app belongs to exactly one place", () => {
    const owners = new Map<string, string[]>();
    for (const hub of HUBS) for (const t of hub.tabs) owners.set(t.href, [...(owners.get(t.href) ?? []), hub.key]);
    const orphans = ALL_PAGES.filter((h) => !owners.has(h));
    const doubled = [...owners].filter(([, o]) => o.length > 1);
    expect(orphans, "add these pages to a place in lib/hubs.ts").toEqual([]);
    expect(doubled).toEqual([]);
    // …and a place never lists a page that does not exist.
    expect([...owners.keys()].filter((h) => !ALL_PAGES.includes(h))).toEqual([]);
  });

  it("the Simple menu has at most seven entries, each with a one-line purpose", () => {
    expect(HUBS.length).toBeLessThanOrEqual(7);
    for (const h of HUBS) expect(h.blurb.length, h.key).toBeGreaterThan(20);
  });

  it("a page finds its place; a page under it too; the longest match wins", () => {
    expect(hubFor("/schedules")?.hub.key).toBe("automations");
    expect(hubFor("/sessions/abc123")?.tab.href).toBe("/sessions");
    expect(hubFor("/")?.hub.key).toBe("home");
    expect(hubFor("/nope")).toBeNull();
    expect(tabLabel("/schedules")).toBe("Schedules");
    expect(tabLabel("/marketplace")).toBe("Directory");
  });

  it("an advanced page shows as a tab only while you are on it", () => {
    const auto = HUBS.find((h) => h.key === "automations")!;
    expect(visibleTabs(auto, "/workflows").map((t) => t.href)).toEqual(["/workflows", "/schedules"]);
    expect(visibleTabs(auto, "/reflex").map((t) => t.href)).toContain("/reflex");
  });
});

describe("B. the side menu", () => {
  async function openDrawer() {
    render(<NavDrawer />);
    await act(async () => {
      window.dispatchEvent(new CustomEvent("ij:toggle-nav"));
    });
    return screen.findByRole("dialog", { name: "Navigation" });
  }

  it("Simple (the default): the seven places, in words; no engineering pages", async () => {
    W.path = "/";
    const drawer = await openDrawer();
    for (const name of ["Home", "Work", "Files", "Automations", "About me", "Apps & settings", "Help"]) {
      expect(within(drawer).getByRole("link", { name: new RegExp(`^${name}$`) })).toBeInTheDocument();
    }
    for (const name of ["Webhooks", "Secrets", "Local fleet", "Self-development", "Reflexes"]) {
      expect(within(drawer).queryByRole("link", { name: new RegExp(name) })).toBeNull();
    }
    expect(within(drawer).getByText("Running")).toBeInTheDocument();
    expect(within(drawer).queryByText(/127\.0\.0\.1|daemon/)).toBeNull();
  });

  it("the place you are in opens to show its pages", async () => {
    W.path = "/schedules";
    const drawer = await openDrawer();
    const here = within(drawer).getByRole("link", { name: "Schedules" });
    expect(here).toHaveAttribute("aria-current", "page");
    expect(within(drawer).getByRole("link", { name: "Workflows" })).toBeInTheDocument();
    // Another place stays closed.
    expect(within(drawer).queryByRole("link", { name: "Documents" })).toBeNull();
  });

  it("Advanced shows today's full menu — and the switch is live, no reload", async () => {
    const drawer = await openDrawer();
    expect(within(drawer).queryByRole("link", { name: /^Webhooks$/ })).toBeNull();
    fireEvent.click(within(drawer).getByRole("button", { name: /Advanced/i }));
    expect(within(drawer).getByRole("link", { name: /^Webhooks$/ })).toBeInTheDocument();
    expect(within(drawer).getByText("daemon connected")).toBeInTheDocument();
    expect(localStorage.getItem("ij_nav_advanced")).toBe("1");
  });
});

describe("C. a page shows its place's tabs (Simple only)", () => {
  // The row reads the real address (window.location), not the router mock.
  const goTo = (path: string) => window.history.pushState({}, "", path);
  afterEach(() => goTo("/"));
  it("on Schedules: Automations · Workflows · Schedules, the current one marked", async () => {
    goTo("/schedules");
    render(<PageHeader title="Schedules" />);
    const tabs = await screen.findByRole("navigation", { name: "Automations pages" });
    expect(within(tabs).getByRole("link", { name: "Schedules" })).toHaveAttribute("aria-current", "page");
    expect(within(tabs).getByRole("link", { name: "Workflows" })).toHaveAttribute("href", "/workflows");
  });

  it("Advanced: no tab row (today's header exactly)", async () => {
    localStorage.setItem("ij_nav_advanced", "1");
    goTo("/schedules");
    render(<PageHeader title="Schedules" />);
    await act(async () => {});
    expect(screen.queryByTestId("hub-tabs")).toBeNull();
    expect(screen.getByRole("heading", { name: "Schedules" })).toBeInTheDocument();
  });

  it("a page in no place, or a place with one page, shows no row", async () => {
    goTo("/help");
    render(<PageHeader title="Help" />);
    await act(async () => {});
    expect(screen.queryByTestId("hub-tabs")).toBeNull();
  });
});

describe("D. the Simple home", () => {
  it("leads with a status line and one ask box; the hero, the grid and the admin card wait in Advanced", async () => {
    render(<OverviewPage />);
    const home = await screen.findByTestId("home-start");
    expect(screen.getByRole("heading", { name: "Home" })).toBeInTheDocument();
    expect(within(home).getByRole("status").textContent).toMatch(/ready when you are/);
    expect(screen.getAllByLabelText("Ask Jarvis anything")).toHaveLength(1);
    expect(screen.queryByText("Systems & admin")).toBeNull();
    expect(screen.queryByTestId("group-doors")).toBeNull();
    expect(screen.queryByText(/Disk/)).toBeNull();
  });

  it("asking goes to Chat with the words waiting — nothing runs", async () => {
    render(<OverviewPage />);
    const box = await screen.findByLabelText("Ask Jarvis anything");
    fireEvent.change(box, { target: { value: "plan my week" } });
    fireEvent.submit(box.closest("form")!);
    expect(W.push).toHaveBeenCalledWith("/chat?ask=plan%20my%20week");
  });

  it("the starters are everyday tasks, in plain words", async () => {
    render(<OverviewPage />);
    const ask = await screen.findByTestId("ask-and-start");
    expect(ask.textContent).not.toMatch(/session|client|markdown/i);
  });

  it("picks up the last three chats, one press each", async () => {
    render(<OverviewPage />);
    const recent = await screen.findByTestId("home-recent");
    const links = within(recent).getAllByRole("link");
    expect(links).toHaveLength(3);
    expect(links[0]).toHaveAttribute("href", "/chat?thread=t1");
    expect(within(recent).getByText("Untitled chat")).toBeInTheDocument();
  });

  it("'Where to go' offers the five places with what each is for", async () => {
    render(<OverviewPage />);
    const places = await screen.findByTestId("home-places");
    const hrefs = within(places)
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["/chat", "/documents", "/workflows", "/you", "/connections"]);
    expect(within(places).getByTestId("show-all-modules").textContent).toMatch(/All modules/);
    expect(places.textContent).toMatch(/Work that runs by itself/);
  });

  it("'Show all modules' still reaches every module", async () => {
    render(<OverviewPage />);
    fireEvent.click(await screen.findByTestId("show-all-modules"));
    expect(await screen.findByTestId("group-doors")).toBeInTheDocument();
  });

  it("while setup is unfinished the strip leads and the page still offers ONE ask box", async () => {
    responses({
      "/onboarding": {
        version: "1.318.0",
        first_run: false,
        doctor: { ok: true, checks: [] },
        checklist: [{ key: "connect_ai", title: "Connect your AI", detail: "d", done: false, action: "" }],
        next_step: { key: "connect_ai", title: "Connect your AI", detail: "d", done: false, action: "" },
      },
    });
    render(<OverviewPage />);
    await screen.findByTestId("first-run-strip", undefined, { timeout: 4000 });
    const home = screen.getByTestId("home-start");
    // The strip says what the status line would; the home does not repeat it.
    expect(within(home).queryByRole("status")).toBeNull();
    expect(screen.getAllByLabelText("Ask Jarvis anything")).toHaveLength(1);
  });

  it("turning Advanced on brings the full Overview back at once", async () => {
    render(<OverviewPage />);
    await screen.findByTestId("home-start");
    await act(async () => {
      writeAdvanced(true);
    });
    expect(screen.getByRole("heading", { name: "Overview" })).toBeInTheDocument();
    expect(screen.queryByTestId("home-start")).toBeNull();
    expect(await screen.findByTestId("group-doors")).toBeInTheDocument();
  });
});
