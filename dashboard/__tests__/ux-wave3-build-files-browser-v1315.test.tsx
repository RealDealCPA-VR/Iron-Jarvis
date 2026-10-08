/**
 * v1.315.0 — UX & aesthetic wave 3, track T4 (build & files): the Browser page.
 *
 * WHAT THE USER SAW (fresh__computeruse / phone__computeruse / light__computeruse):
 *  - TWO PRODUCTS, ONE TANGLE. "Agent browser off" sat in the page header
 *    (on a phone directly above the "Your browser" card, which has its OWN
 *    "Off"), and the amber "Read this before you turn it on… isolated,
 *    disposable VM" panel had no heading, so it read as a warning about the
 *    user's own browser. One thing had three names: "Agent browser" (pill),
 *    "Computer use" (warning, "Enable computer use", "Computer use is
 *    disabled", run history) and "Browser" (title).
 *  - YOUR BROWSER, OFF: two identical solid accent buttons ("Set up my
 *    browser", "Turn on"), and "Turn on" says "Pick Read only or Interactive
 *    below" — but the sidebar note with its amber toolbar troubleshooting sat
 *    between it and that selector.
 *
 * Interface the implementer follows:
 *  - a HEADING containing "Agent browser" (and NOT the word "off" — the pill
 *    keeps the only /Agent browser off/ text, browser-card-v1235 findByText)
 *    sits after the your-browser-card and before the explainer; the
 *    "Agent browser on/off" pill moves out of the PageHeader into that section
 *    (so it follows the card in document order), text verbatim;
 *  - one name in the lower half: no visible "computer use"; the switch reads
 *    "Turn on the agent browser" (off) and still POSTs /computeruse/enable;
 *  - off state: exactly ONE .btn-accent on the card (Set up my browser);
 *    Turn on becomes btn-ghost and still only focuses Read only;
 *  - the SidebarNote renders AFTER the access selector, the pin paragraph
 *    stays visible, the "older copy… extensions page" paragraph moves into a
 *    <details> whose <summary> mentions a popup. Every data-testid stays.
 *
 * Anti-vacuity: the explainer keeps its substance (off by default, isolated
 * disposable VM, approval for sensitive actions) and appears once; the pill
 * appears once; the setup wizard still opens; Turn on still writes nothing;
 * the access buttons still PUT the chosen level.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
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
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    puts: [] as { path: string; body: unknown }[],
    status: null as Record<string, unknown> | null,
    computeruse: { enabled: false, domain_allowlist: [], action_allowlist: [] } as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  sseUrl: (p: string) => p,
  wsUrl: (p: string) => p,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (path: string) => {
    api.gets.push(path);
    if (path === "/browser/status") {
      if (api.status === null) throw new api.FakeApiError("Not Found", 404);
      return api.status;
    }
    if (path === "/computeruse") return api.computeruse;
    if (path === "/computeruse/approvals") return { approvals: [] };
    if (path.startsWith("/computeruse/runs")) return { runs: [] };
    if (path.startsWith("/computeruse")) return { pending_approvals: 0 };
    if (path === "/health")
      return { status: "ok", version: "1.315.0", providers: [], default_provider: "mock" };
    return {};
  },
  post: async (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    return {};
  },
  put: async (path: string, body?: unknown) => {
    api.puts.push({ path, body });
    return {};
  },
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/computeruse",
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

const { YourBrowserCard } = await import("@/components/browser/YourBrowserCard");
const BrowserPage = (await import("@/components/settings/pages/BrowserPage")).default;

const base: Record<string, unknown> = {
  connected: false,
  access: "interactive",
  host_permission: false,
  extension_id: "lgihfomaieifpnemakmpadmggjnoojmm",
  active_tab: null,
  pending_pairing: null,
  paired: false,
  last_error: null,
};
const OFF = { ...base, access: "off" };
const CONNECTED = {
  ...base,
  connected: true,
  paired: true,
  host_permission: true,
  active_tab: { id: 42, title: "Example Domain", url: "https://example.com/" },
};

const follows = (a: Node, b: Node) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

beforeEach(() => {
  localStorage.clear();
  api.gets.length = 0;
  api.posts.length = 0;
  api.puts.length = 0;
  api.status = { ...base };
  api.computeruse = { enabled: false, domain_allowlist: [], action_allowlist: [] };
});
afterEach(() => cleanup());

/* ========================================================================== */

describe("The Browser page labels its two products", () => {
  async function renderPage() {
    render(<BrowserPage />);
    const card = await screen.findByTestId("your-browser-card");
    const pill = await screen.findByText(/Agent browser off/);
    const explainer = screen.getByText("Read this before you turn it on.");
    return { card, pill, explainer };
  }

  it("an 'Agent browser' heading opens the lower half: after Your browser, before the VM warning", async () => {
    const { card, explainer } = await renderPage();
    const heads = screen
      .getAllByRole("heading")
      .filter((h) => /Agent browser/i.test(h.textContent ?? ""));
    expect(heads.length).toBeGreaterThanOrEqual(1);
    const head = heads[0];
    expect(follows(card, head)).toBe(true);
    expect(follows(head, explainer)).toBe(true);
    // Says what it is; never "off" (the pill keeps the one /Agent browser off/).
    expect(head.textContent ?? "").not.toMatch(/\boff\b/i);
    expect(card.contains(head)).toBe(false);
  });

  it("the on/off pill lives in that section, not in the page header above Your browser", async () => {
    const { card, pill, explainer } = await renderPage();
    expect(follows(card, pill)).toBe(true);
    expect(follows(pill, explainer)).toBe(true);
    expect(card.contains(pill)).toBe(false);
    // Anti-vacuity: the pill's words are verbatim and appear exactly once.
    expect(screen.getAllByText(/Agent browser off/)).toHaveLength(1);
  });

  it("one name: nothing on the page says 'computer use' any more", async () => {
    await renderPage();
    await screen.findByText(/No runs recorded yet/); // the history section has rendered
    expect(document.body.textContent ?? "").not.toMatch(/computer[ -]use/i);
  });

  it("the switch reads 'Turn on the agent browser' and still POSTs /computeruse/enable", async () => {
    await renderPage();
    const btn = await screen.findByRole("button", { name: /Turn on the agent browser/i });
    fireEvent.click(btn);
    await waitFor(() => expect(api.posts.some((p) => p.path === "/computeruse/enable")).toBe(true));
    const body = api.posts.find((p) => p.path === "/computeruse/enable")?.body as Record<string, unknown>;
    expect(body.enabled).toBe(true);
  });

  it("anti-vacuity: the VM warning keeps its substance, once, below Your browser", async () => {
    const { card, explainer } = await renderPage();
    expect(screen.getAllByText("Read this before you turn it on.")).toHaveLength(1);
    expect(follows(card, explainer)).toBe(true);
    const panel = explainer.parentElement as HTMLElement;
    const words = panel.textContent ?? "";
    expect(words).toMatch(/off by default/);
    expect(words).toMatch(/isolated, disposable VM/);
    expect(words).toMatch(/explicit\s+approval/);
  });
});

/* ========================================================================== */

describe("Your browser, off: one primary step, the selector right under it", () => {
  it("exactly one solid accent button on the off card, and it is 'Set up my browser'", async () => {
    api.status = OFF;
    render(<YourBrowserCard />);
    const card = await screen.findByTestId("your-browser-card");
    await waitFor(() => expect(screen.getByTestId("browser-badge").dataset.state).toBe("off"));
    const turnOn = screen.getByTestId("browser-turn-on");
    const accents = Array.from(card.querySelectorAll(".btn-accent"));
    expect(accents).toHaveLength(1);
    expect(accents[0]).toBe(screen.getByTestId("browser-setup-open"));
    expect(turnOn.className).not.toMatch(/\bbtn-accent\b/);
  });

  it("the access selector comes BEFORE the sidebar note (off and connected)", async () => {
    for (const status of [OFF, CONNECTED]) {
      api.status = status;
      render(<YourBrowserCard />);
      const access = await screen.findByTestId("browser-access");
      const note = screen.getByTestId("browser-sidebar-note");
      expect(follows(access, note)).toBe(true);
      cleanup();
    }
  });

  it("the 'popup instead of a sidebar' fix is one click away in a <details>; the pin step stays visible", async () => {
    api.status = OFF;
    render(<YourBrowserCard />);
    const stale = await screen.findByTestId("browser-sidebar-stale");
    const details = stale.closest("details");
    expect(details).not.toBeNull();
    expect(details?.querySelector("summary")?.textContent ?? "").toMatch(/popup/i);
    // Anti-vacuity: every pinned id and word is still in the DOM.
    const pin = screen.getByTestId("browser-sidebar-pin");
    expect(pin.closest("details")).toBeNull();
    const note = screen.getByTestId("browser-sidebar-note");
    expect(note.contains(stale)).toBe(true);
    const text = note.textContent ?? "";
    expect(text).toMatch(/toolbar/);
    expect(text).toMatch(/sidebar/);
    expect(text).toMatch(/(chrome|edge):\/\/extensions/);
  });

  it("anti-vacuity: Turn on still only focuses Read only and writes nothing", async () => {
    api.status = OFF;
    render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-turn-on"));
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByTestId("browser-access-read_only")),
    );
    expect(api.puts).toHaveLength(0);
    expect(api.posts).toHaveLength(0);
  });

  it("anti-vacuity: the access buttons still PUT the chosen level; the wizard still opens", async () => {
    api.status = OFF;
    render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-access-read_only"));
    await waitFor(() => expect(api.puts).toHaveLength(1));
    expect(api.puts[0]).toEqual({ path: "/settings", body: { values: { browser_access: "read_only" } } });
    fireEvent.click(screen.getByTestId("browser-setup-open"));
    const dlg = await screen.findByRole("dialog");
    expect(within(dlg).getAllByRole("button").length).toBeGreaterThan(0);
  });
});
