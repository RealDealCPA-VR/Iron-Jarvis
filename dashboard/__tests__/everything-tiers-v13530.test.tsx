/**
 * v1.353.0, lane 3E (G66) — Everything is tiered and its entries say what
 * they are for.
 *
 * WHAT A NEW USER SAW: Everything listed 30 surfaces as equals, developer
 * pages (Self-development, Webhooks, Local fleet, Secrets, Artifacts, Session
 * board) beside Documents; Build had no description in the sidebar; Tools
 * said "MCP"; Webhooks read "Inbound and outbound webhook registrations";
 * Sessions and Agents overlapped; the accounts feature was named by its brand.
 *
 * NOW: lib/surfaces marks six pages `advanced`; each Everything column lists
 * its everyday entries first and the developer pages under its "More" — a
 * quiet line that still LINKS them (T2: every surface two clicks from home,
 * e2e/calm-ui.spec clicks `everything-link-<route>` right after opening
 * Everything) and opens to their full rows. The filter finds them as full
 * rows. Hovering a sidebar row shows its one line. The blurbs say what each
 * place is for, and the Iron-Proxy card says "Several accounts" first.
 *
 * Real components throughout; only the daemon is faked.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const fake = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    cancelled = false;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return { FakeApiError, ironProxy: null as unknown };
});

vi.mock("@/lib/api", () => ({
  ApiError: fake.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => "",
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (path: string) => {
    if (path === "/iron-proxy" || path === "/iron-proxy?discover=0") return fake.ironProxy;
    if (path === "/chat/threads") return { threads: [] };
    if (path === "/projects") return { projects: [] };
    if (path.startsWith("/sessions")) return { sessions: [] };
    return {};
  },
  post: async () => ({}),
  put: async () => ({}),
  patch: async () => ({}),
  del: async () => ({}),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/everything",
  useSearchParams: () => new URLSearchParams(),
}));

import EverythingPage from "@/app/everything/page";
import { AppSidebar } from "@/components/AppSidebar";
import { IronProxyCard } from "@/components/connections/IronProxyCard";
import { ADVANCED_HREFS, EVERYTHING_GROUPS, SURFACES, readPins, surfaceFor } from "@/lib/surfaces";
import { NAV_ENTRIES } from "@/lib/nav";
import { __resetApiCache } from "@/lib/apiCache";

const DEV_PAGES = ["/self-dev", "/webhooks", "/fleet", "/secrets", "/artifacts", "/kanban"];

function setWide(wide: boolean) {
  window.matchMedia = ((q: string) => ({
    matches: q.includes("min-width") ? wide : false,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

beforeEach(() => {
  __resetApiCache();
  window.localStorage.clear();
  setWide(true);
  fake.ironProxy = null;
});
afterEach(() => cleanup());

const link = (href: string) => screen.getByTestId(`everything-link-${href.slice(1)}`);

/* ------------------------------------------------------- the manifest */

describe("the manifest marks the developer pages, and only them", () => {
  it("exactly the six developer pages are advanced", () => {
    expect([...ADVANCED_HREFS].sort()).toEqual([...DEV_PAGES].sort());
    const advanced = SURFACES.filter((s) => s.advanced).map((s) => s.href);
    expect(advanced.sort()).toEqual([...DEV_PAGES].sort());
    // Everyday pages a new user needs are NOT folded.
    for (const h of ["/documents", "/schedules", "/connections", "/channels", "/memory", "/help", "/tools"]) {
      expect(surfaceFor(h)?.advanced, h).toBeFalsy();
    }
  });
});

/* ------------------------------------------------------- Everything */

describe("Everything: everyday entries first, developer pages under More", () => {
  it("folded, More names each developer page as a link that says what it is for on hover", () => {
    render(<EverythingPage />);
    for (const href of DEV_PAGES) {
      const s = surfaceFor(href)!;
      const region = screen.getByTestId(`everything-more-${s.group}`);
      expect(region.getAttribute("data-open"), href).toBe("false");
      const a = within(region).getByTestId(`everything-link-${href.slice(1)}`);
      expect(a.getAttribute("href")).toBe(href);
      expect(a.getAttribute("title")).toBe(s.blurb);
      // Folded: the name only — no line, no pin door.
      expect(a.textContent).toBe(s.label);
      expect(screen.queryByLabelText(`Pin ${s.label} to the sidebar`)).toBeNull();
    }
  });

  it("in every column the everyday rows come BEFORE its More", () => {
    render(<EverythingPage />);
    for (const g of EVERYTHING_GROUPS) {
      const col = screen.queryByTestId(`everything-group-${g.key}`);
      if (!col) continue;
      const more = within(col).queryByTestId(`everything-more-${g.key}`);
      const everyday = SURFACES.filter((s) => s.group === g.key && !s.advanced);
      for (const s of everyday) {
        const row = within(col).getByTestId(`everything-link-${s.href.slice(1)}`);
        // Everyday rows carry their one line.
        expect(row.textContent).toContain(s.blurb);
        if (more) expect(row.compareDocumentPosition(more) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      }
    }
    // Work has no developer page, so no More at all.
    expect(screen.queryByTestId("everything-more-work")).toBeNull();
  });

  it("More opens to full rows with lines and pins, and closes again (keyboard-reachable button)", () => {
    render(<EverythingPage />);
    const toggle = screen.getByRole("button", { name: "More System pages" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.getAttribute("aria-controls")).toBe("everything-more-system");
    toggle.focus();
    fireEvent.click(toggle);
    // The SAME button (focus kept for a keyboard user), now expanded.
    expect(screen.getByRole("button", { name: "More System pages" })).toBe(toggle);
    expect(document.activeElement).toBe(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const region = screen.getByTestId("everything-more-system");
    expect(region.getAttribute("data-open")).toBe("true");
    for (const href of ["/fleet", "/self-dev", "/kanban"]) {
      const s = surfaceFor(href)!;
      expect(within(region).getByTestId(`everything-link-${href.slice(1)}`).textContent).toContain(s.blurb);
    }
    fireEvent.click(screen.getByLabelText("Pin Local fleet to the sidebar"));
    expect(readPins()).toEqual(["/fleet"]);
    // Other columns stay folded (per column).
    expect(screen.getByTestId("everything-more-setup").getAttribute("data-open")).toBe("false");
    fireEvent.click(toggle);
    expect(region.getAttribute("data-open")).toBe("false");
  });

  it("the filter still finds a developer page, shown as a full row with no toggle", () => {
    render(<EverythingPage />);
    fireEvent.change(screen.getByTestId("everything-filter"), { target: { value: "webhook" } });
    const region = screen.getByTestId("everything-more-automations");
    expect(region.getAttribute("data-open")).toBe("true");
    // Open while filtering: a plain "More" label, no toggle to press.
    expect(within(region).queryByRole("button", { name: /^More / })).toBeNull();
    expect(within(region).getByText("More")).toBeTruthy();
    expect(link("/webhooks").textContent).toContain(surfaceFor("/webhooks")!.blurb);
    // An alias finds it too.
    fireEvent.change(screen.getByTestId("everything-filter"), { target: { value: "vault" } });
    expect(link("/secrets").getAttribute("href")).toBe("/secrets");
  });

  it("every surface outside the sidebar is still one click from Everything (T2)", () => {
    render(<EverythingPage />);
    const hrefs = within(screen.getByTestId("everything-grid"))
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    for (const href of DEV_PAGES) expect(hrefs, href).toContain(href);
    expect(new Set(hrefs)).toEqual(new Set(SURFACES.filter((s) => s.group !== "primary").map((s) => s.href)));
  });
});

/* ------------------------------------------------------- the sidebar */

describe("the sidebar: hovering Build says what it is", () => {
  it("an expanded row's title is its one line; collapsed it stays the name", () => {
    render(<AppSidebar />);
    const build = screen.getByTestId("sidebar-nav-terminals");
    const blurb = NAV_ENTRIES.find((e) => e.href === "/terminals")!.blurb;
    expect(build.getAttribute("title")).toBe(blurb);
    // Moved on purpose (harness gaps, fix 4): Build's line names BOTH views.
    expect(blurb).toMatch(/Terminals for your own coding tools, and coding sessions/);
    expect(screen.getByTestId("sidebar-nav-everything").getAttribute("title")).toBe(surfaceFor("/everything")!.blurb);
    fireEvent.click(screen.getByLabelText("Collapse the sidebar"));
    expect(screen.getByTestId("sidebar-nav-terminals").getAttribute("title")).toBe("Build");
  });
});

/* ------------------------------------------------------- the words */

describe("each entry says what it is for", () => {
  const blurb = (href: string) => NAV_ENTRIES.find((e) => e.href === href)!.blurb;

  it("Tools, Webhooks, Sessions and Agents in plain words", () => {
    expect(blurb("/tools")).toBe(
      "Add abilities like GitHub, a database or your own scripts, and choose which ones ask first.",
    );
    expect(blurb("/tools")).not.toMatch(/MCP/);
    expect(blurb("/webhooks")).toBe("Let another app start work here, or get told when work finishes.");
    expect(blurb("/sessions")).toBe("Every task an agent ran, step by step.");
    expect(blurb("/agents")).toMatch(/^For tasks bigger than one chat: /);
    // People who know the protocol still find Tools by it.
    expect(NAV_ENTRIES.find((e) => e.href === "/tools")!.aliases).toContain("mcp");
  });
});

/* ------------------------------------------------------- Iron-Proxy */

describe("the accounts card says what it is before its brand", () => {
  it("title 'Several accounts (Iron-Proxy)'; off, it names the tools and the one account each uses", async () => {
    fake.ironProxy = {
      status: { enabled: false, running: false, owned: false, url: null, version: null, error: null, bundled: true },
      accounts: [],
      discovered: [],
      providers_used_by_jarvis: { anthropic: "claude-cli", openai: "codex-cli", xai: "grok-cli" },
    };
    render(<IronProxyCard />);
    await waitFor(() => expect(document.getElementById("iron-proxy-card")).not.toBeNull());
    const card = document.getElementById("iron-proxy-card")!;
    expect(within(card).getByText("Several accounts (Iron-Proxy)")).toBeTruthy();
    expect(card.textContent).not.toMatch(/Iron-Proxy accounts/);
    await waitFor(() =>
      expect(within(card).getByTestId("iron-proxy-status").textContent).toBe(
        "Off. Claude Code, Codex and Grok each use the one account they're signed in with on this PC.",
      ),
    );
    // Anti-vacuity: the switch keeps its id and name.
    expect(document.getElementById("iron-proxy-toggle")!.getAttribute("aria-label")).toBe("Use Iron-Proxy accounts");
  });
});
