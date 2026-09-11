/**
 * The "Your browser" card, all six states (v1.235.0, Browser Ship 1).
 *
 * The card is the ONLY place a user pairs their own Chrome with Iron Jarvis,
 * and it is the only place they learn why it is not working. Six states of one
 * card is exactly the shape that rots: five of them are rare, so a regression
 * in any of them ships green and is discovered by the user. This file mounts
 * the component once per state and pins what the user reads.
 *
 * What each assertion catches, stated as the silent failure it prevents:
 *
 *  - THE WORD, NOT THE COLOUR. Every state badge prints its own word ("Off",
 *    "Not connected", "Waiting to pair", "Paired — not running", "Connected —
 *    no site access", "Connected"). Four of the six are amber; if the word ever
 *    collapses into "a yellow dot" the user cannot tell "press Pair" from
 *    "grant site access" from "the add-on is not running", and a colour-blind
 *    user cannot tell any of them from Connected. Pinning the text is what
 *    keeps colour from becoming the state.
 *
 *  - PAIR POSTS EXACTLY ONCE. Pairing mints a credential; a second POST on a
 *    consumed request_id 404s, which would render an error over a pairing that
 *    actually succeeded. The card guards with an in-flight lock AND a disabled
 *    button; this test double-clicks and counts the posts, so removing either
 *    guard goes red. The count is read AFTER the success note (the signal the
 *    handler sets LAST), never after the post itself — waiting on the first
 *    observable side effect is the v1.177.1/v1.178.0 flake.
 *
 *  - TEST IS ABSENT UNTIL CONNECTED. POST /browser/test needs a live socket. A
 *    Test button on a disconnected card is a button whose only possible outcome
 *    is BROWSER_NOT_CONNECTED — an invitation to a failure. Asserted across all
 *    five non-connected states, not just one.
 *
 *  - THE INSTALL INSTRUCTIONS EXIST AND ARE REACHABLE. A user with nothing
 *    loaded has no other way in: no add-on, no pairing request, no tabs. The
 *    steps must name the folder Chrome's "Load unpacked" wants. This is the
 *    v1.218.0 lesson in its original form — a capability whose entry point is
 *    unreachable does not exist.
 *
 *  - NO USER-FACING STRING CALLS THE ADD-ON AN "EXTENSION". VOCABULARY.md made
 *    "extension" this app's word for an MCP server in v1.216.0. Two different
 *    things under one word on one screen is the exact failure vocabulary.test.ts
 *    exists to prevent, so the rendered text of every state is scanned. Two
 *    literals are stripped before the scan rather than the scan being loosened,
 *    and neither is a name for the add-on: `chrome://extensions`, Chrome's name
 *    for its own page, and `extensions/chrome`, the folder on disk that Load
 *    unpacked is pointed at.
 *
 *  - THE ACCESS SELECTOR IS THE ONLY THING THAT TURNS IT ON. "Turn on" focuses
 *    the selector and writes nothing: read-only versus interactive is a safety
 *    decision the app must not make for the user. If "Turn on" ever starts
 *    PUTting a level by itself, the "posts nothing" assertion goes red.
 *
 *  - A DAEMON WITHOUT THE ROUTE SAYS SO. GET /browser/status 404s on an install
 *    that has not restarted into this version. Rendering an "Off" card with a
 *    switch that cannot work is the failure; the card says restart instead.
 *
 * And on the page itself: the header now reads Browser, the card is mounted
 * ABOVE the existing computer-use explainer, and the explainer is still there
 * exactly once — because "mount a new card at the top" is the change most
 * likely to be delivered as a duplicated section.
 *
 * No assertion here measures elapsed time. POST /browser/test reports
 * round_trip_ms and the card displays it; the value asserted is the one the
 * mock returned, which measures nothing.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
    /** null => the route does not exist on this daemon (404). */
    status: null as Record<string, unknown> | null,
    test: {
      ok: true,
      detail: "Read-only round trip completed",
      round_trip_ms: 41,
      active_tab: { id: 42, title: "Example Domain", url: "https://example.com/" },
    } as Record<string, unknown>,
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
    if (path === "/computeruse") return { enabled: false, domain_allowlist: [], action_allowlist: [] };
    if (path === "/computeruse/approvals") return { approvals: [] };
    if (path.startsWith("/computeruse/runs")) return { runs: [] };
    if (path.startsWith("/computeruse")) return { pending_approvals: 0 };
    if (path === "/health")
      return { status: "ok", version: "1.235.0", providers: [], default_provider: "mock" };
    return {};
  },
  post: async (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    if (path === "/browser/test") return api.test;
    if (path === "/browser/pair") return { paired: true };
    if (path === "/browser/request-host-permission") return { requested: true };
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
const BrowserPage = (await import("@/app/computeruse/page")).default;

/* -------------------------------------------------------------------------- */
/*  Status fixtures — one per state of the card                                */
/* -------------------------------------------------------------------------- */

type Status = Record<string, unknown>;

const base: Status = {
  connected: false,
  access: "interactive",
  host_permission: false,
  extension_id: "lgihfomaieifpnemakmpadmggjnoojmm",
  active_tab: null,
  pending_pairing: null,
  paired: false,
  last_error: null,
};

const STATES: { state: string; word: string; status: Status }[] = [
  { state: "off", word: "Off", status: { ...base, access: "off" } },
  { state: "not_connected", word: "Not connected", status: { ...base } },
  {
    state: "waiting",
    word: "Waiting to pair",
    status: {
      ...base,
      pending_pairing: { request_id: "pair_abc123", first_seen_at: "2026-09-06T12:00:00Z" },
    },
  },
  { state: "paired_down", word: "Paired — not running", status: { ...base, paired: true } },
  {
    state: "no_site_access",
    word: "Connected — no site access",
    status: { ...base, connected: true, paired: true, host_permission: false },
  },
  {
    state: "connected",
    word: "Connected",
    status: {
      ...base,
      connected: true,
      paired: true,
      host_permission: true,
      active_tab: { id: 42, title: "Example Domain", url: "https://example.com/" },
    },
  },
];

const byState = (name: string): Status => {
  const row = STATES.find((s) => s.state === name);
  if (!row) throw new Error(`no fixture for state ${name}`);
  return row.status;
};

beforeEach(() => {
  localStorage.clear();
  api.gets.length = 0;
  api.posts.length = 0;
  api.puts.length = 0;
  api.status = { ...base };
  api.test = {
    ok: true,
    detail: "Read-only round trip completed",
    round_trip_ms: 41,
    active_tab: { id: 42, title: "Example Domain", url: "https://example.com/" },
  };
});
afterEach(() => cleanup());

const postPaths = () => api.posts.map((p) => p.path);

/* -------------------------------------------------------------------------- */

describe("every state of the Your browser card carries a word (v1.235.0)", () => {
  for (const { state, word, status } of STATES) {
    it(`${state} renders the badge word "${word}"`, async () => {
      api.status = status;
      render(<YourBrowserCard />);
      const badge = await screen.findByTestId("browser-badge");
      await waitFor(() => expect(badge.dataset.state).toBe(state));
      expect(badge.textContent?.trim()).toBe(word);
    });
  }

  it("each state also offers its own single next step", async () => {
    // Off -> Turn on. Waiting -> Pair. No site access -> Grant site access.
    // Connected -> Test. The two remaining states offer the install steps,
    // pinned in their own test below.
    const expected: [string, string][] = [
      ["off", "browser-turn-on"],
      ["waiting", "browser-pair"],
      ["no_site_access", "browser-grant"],
      ["connected", "browser-test"],
    ];
    for (const [state, testid] of expected) {
      api.status = byState(state);
      render(<YourBrowserCard />);
      expect(await screen.findByTestId(testid)).toBeTruthy();
      cleanup();
    }
  });
});

describe("Test belongs to a live socket only (v1.235.0)", () => {
  for (const { state, status } of STATES.filter((s) => s.state !== "connected")) {
    it(`${state} renders no Test button`, async () => {
      api.status = status;
      render(<YourBrowserCard />);
      // Wait for the card's own data to have landed before asserting an
      // absence: a still-loading card renders no Test button either, which
      // would make this pass for the wrong reason.
      await screen.findByTestId("browser-badge");
      await waitFor(() => expect(screen.getByTestId("browser-access")).toBeTruthy());
      expect(screen.queryByTestId("browser-test")).toBeNull();
    });
  }

  it("connected renders Test, and the readout shows the daemon's own report", async () => {
    api.status = byState("connected");
    render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-test"));
    const readout = await screen.findByTestId("browser-test-result");
    expect(readout.textContent).toContain("Read-only round trip completed");
    // The elapsed figure is DISPLAYED, never timed: this is the number the
    // mock returned, so the assertion measures no hardware.
    expect(readout.textContent).toContain("41 ms");
    expect(readout.textContent).toContain("Example Domain");
    expect(postPaths()).toEqual(["/browser/test"]);
  });

  it("a failing Test reports the daemon's reason instead of a green tick", async () => {
    api.status = byState("connected");
    api.test = { ok: false, detail: "BROWSER_NOT_CONNECTED: your browser is not connected", round_trip_ms: 0, active_tab: null };
    render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-test"));
    const readout = await screen.findByTestId("browser-test-result");
    expect(readout.textContent).toContain("BROWSER_NOT_CONNECTED");
    expect(readout.querySelector('[role="alert"]')).not.toBeNull();
  });
});

describe("Pair posts once, with the pending request id (v1.235.0)", () => {
  it("one POST /browser/pair even on a double click", async () => {
    api.status = byState("waiting");
    render(<YourBrowserCard />);
    const btn = await screen.findByTestId("browser-pair");
    fireEvent.click(btn);
    fireEvent.click(btn);
    // The success note is set LAST in the handler — waiting on the POST itself
    // would read the count before the second click could have added to it.
    await screen.findByText(/Paired\. Your browser is now connected/);
    expect(postPaths().filter((p) => p === "/browser/pair")).toHaveLength(1);
    expect(api.posts[0].body).toEqual({ request_id: "pair_abc123" });
  });

  it("Grant site access asks the browser, and says a prompt is coming", async () => {
    api.status = byState("no_site_access");
    render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-grant"));
    await screen.findByText(/accept the prompt it opens/);
    expect(postPaths()).toEqual(["/browser/request-host-permission"]);
  });

  it("Disconnect keeps the pairing and says so", async () => {
    api.status = byState("connected");
    render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-disconnect"));
    await screen.findByText(/stays paired and reconnects on its own/);
    expect(postPaths()).toEqual(["/browser/disconnect"]);
  });

  it("Forget takes two clicks and then deletes the pairing", async () => {
    api.status = byState("connected");
    render(<YourBrowserCard />);
    const forget = await screen.findByRole("button", { name: /forget/i });
    fireEvent.click(forget);
    // First click only arms it — an accidental click must not destroy a
    // credential the user would have to re-pair to recover.
    expect(postPaths()).toEqual([]);
    fireEvent.click(await screen.findByRole("button", { name: /confirm/i }));
    await screen.findByText(/asks to pair again/);
    expect(postPaths()).toEqual(["/browser/forget"]);
  });
});

describe("the install instructions are reachable when nothing is talking (v1.235.0)", () => {
  for (const state of ["not_connected", "paired_down"]) {
    it(`${state} offers the steps, and they name the folder Load unpacked wants`, async () => {
      api.status = byState(state);
      render(<YourBrowserCard />);
      const toggle = await screen.findByTestId("browser-install-toggle");
      expect(toggle.textContent).toContain("Iron Jarvis browser add-on");
      expect(screen.queryByTestId("browser-install-steps")).toBeNull();
      fireEvent.click(toggle);
      const steps = await screen.findByTestId("browser-install-steps");
      expect(steps.textContent).toContain("Load unpacked");
      expect(steps.textContent).toContain("extensions/chrome");
      expect(steps.textContent).toContain("Developer mode");
      expect(steps.textContent).toContain("Waiting to pair");
    });
  }

  it("a connected card does not repeat them", async () => {
    api.status = byState("connected");
    render(<YourBrowserCard />);
    await screen.findByTestId("browser-active-tab");
    expect(screen.queryByTestId("browser-install-toggle")).toBeNull();
  });

  it("the connected card shows the real active tab", async () => {
    api.status = byState("connected");
    render(<YourBrowserCard />);
    const tab = await screen.findByTestId("browser-active-tab");
    expect(tab.textContent).toContain("Example Domain");
    expect(tab.textContent).toContain("https://example.com/");
  });
});

describe("the add-on is never called an extension to the user (v1.235.0)", () => {
  it("no rendered state uses the word, in any state, install steps expanded", async () => {
    for (const { state, status } of STATES) {
      api.status = status;
      render(<YourBrowserCard />);
      await screen.findByTestId("browser-badge");
      const toggle = screen.queryByTestId("browser-install-toggle");
      if (toggle) fireEvent.click(toggle);
      // Two literals are removed rather than the rule being relaxed, because
      // neither is a NAME for the add-on: `chrome://extensions` is Chrome's own
      // name for Chrome's own page, and `extensions/chrome` is the real folder
      // on disk that Chrome's Load unpacked must be pointed at. The user has to
      // type the first and select the second. Everything the copy CALLS the
      // add-on must read "add-on".
      const text = (document.body.textContent ?? "")
        .replace(/chrome:\/\/extensions/g, "")
        .replace(/extensions\/chrome/g, "");
      expect(text, `state ${state} leaked the MCP word`).not.toMatch(/extension/i);
      cleanup();
    }
  });
});

describe("access is the user's decision, written through /settings (v1.235.0)", () => {
  it("Turn on focuses the selector and writes nothing by itself", async () => {
    api.status = byState("off");
    render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-turn-on"));
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByTestId("browser-access-read_only")),
    );
    expect(api.puts).toEqual([]);
    expect(postPaths()).toEqual([]);
  });

  it("picking a level PUTs browser_access and nothing else", async () => {
    api.status = byState("off");
    render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-access-read_only"));
    await waitFor(() => expect(api.puts).toHaveLength(1));
    expect(api.puts[0]).toEqual({
      path: "/settings",
      body: { values: { browser_access: "read_only" } },
    });
  });

  it("the live level is the pressed one", async () => {
    api.status = { ...base, access: "interactive" };
    render(<YourBrowserCard />);
    const on = await screen.findByTestId("browser-access-interactive");
    await waitFor(() => expect(on.getAttribute("aria-pressed")).toBe("true"));
    expect(screen.getByTestId("browser-access-off").getAttribute("aria-pressed")).toBe("false");
  });

  it("a connected socket under access off still reads Off, never Connected", async () => {
    // The server refuses every browser tool while access is off, so a card
    // that said "Connected" there would promise an ability that does not exist.
    api.status = { ...base, access: "off", connected: true, paired: true, host_permission: true };
    render(<YourBrowserCard />);
    const badge = await screen.findByTestId("browser-badge");
    await waitFor(() => expect(badge.dataset.state).toBe("off"));
    expect(badge.textContent?.trim()).toBe("Off");
    expect(screen.queryByTestId("browser-test")).toBeNull();
  });
});

describe("the daemon's own failures are visible (v1.235.0)", () => {
  it("last_error is shown on the card, not only in a log", async () => {
    api.status = { ...byState("connected"), last_error: "ACTION_TIMEOUT: read_page after 20s" };
    render(<YourBrowserCard />);
    const line = await screen.findByTestId("browser-last-error");
    expect(line.textContent).toContain("ACTION_TIMEOUT: read_page after 20s");
  });

  it("a daemon without the route says restart, and offers no dead switch", async () => {
    api.status = null; // GET /browser/status -> 404
    render(<YourBrowserCard />);
    await screen.findByText(/does not have the Browser surface yet/);
    expect(screen.queryByTestId("browser-access")).toBeNull();
  });
});

describe("the Browser page mounts the card at the top (v1.235.0)", () => {
  it("the header reads Browser and the card sits above the computer-use explainer", async () => {
    api.status = { ...base };
    render(<BrowserPage />);
    expect((await screen.findByTestId("page-title")).textContent).toBe("Browser");
    const card = await screen.findByTestId("your-browser-card");
    const explainer = screen.getByText("Read this before you turn it on.");
    // DOCUMENT_POSITION_FOLLOWING: the explainer comes AFTER the card.
    expect(card.compareDocumentPosition(explainer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("every existing section survives, exactly once", async () => {
    api.status = { ...base };
    render(<BrowserPage />);
    await screen.findByTestId("your-browser-card");
    // The explainer, the allowlists and the history are the page's own
    // sections: mounting a card at the top must not duplicate or drop them.
    expect(screen.getAllByText("Read this before you turn it on.")).toHaveLength(1);
    expect(screen.getAllByTestId("your-browser-card")).toHaveLength(1);
    // WAIT FOR THE THING ASSERTED (the CLAUDE.md waitFor rule): this label is
    // rendered from the page's OWN computer-use status fetch, which resolves
    // independently of the card's /browser/status — a sync getBy right after
    // the card appeared went red on a contended CI runner (v1.244.0's gate).
    expect(await screen.findByText(/Agent browser off/)).toBeTruthy();
  });
});
