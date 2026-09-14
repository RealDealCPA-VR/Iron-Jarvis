/**
 * v1.261.0 — the guided browser setup is written for the browser this PC has.
 *
 * The user's report, on an Edge-only work PC: "when I select Set up browser I
 * only get the instructions for Chrome, not Edge." v1.259.0 had put Edge in
 * parentheses after every Chrome sentence, which on that machine is still a
 * Chrome page. Now the daemon says which browsers are INSTALLED
 * (`installed_browsers` on GET /browser/status) and which one PAIRED
 * (`browser_name`), `browserWords.pickBrowser` turns that into one choice —
 * paired first, the only installed one second, Chrome otherwise — and every
 * sentence in the guided window and on the card is written for that browser:
 * its own add-ons page (`edge://extensions`), its own way of pinning the icon
 * (the eye icon, "Show in toolbar"). A toggle in the window's header overrides
 * the pick and is remembered on this device.
 *
 * The dialog is opened THE WAY A USER DOES — the real card, its real poll, its
 * real button — and step 2 is DRIVEN TO by pressing Copy, exactly as
 * browser-setup-modal-v1240 does it. Vocabulary: the scrub strips both
 * browsers' own page addresses, which are quoted as addresses and never name
 * the add-on.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
    status: null as Record<string, unknown> | null,
    arm: { armed: true, expires_in_s: 120, addon_dir: "" } as Record<string, unknown>,
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
    if (path === "/browser/status") {
      if (api.status === null) throw new api.FakeApiError("Not Found", 404);
      return api.status;
    }
    if (path === "/health")
      return { status: "ok", version: "1.261.0", providers: [], default_provider: "mock" };
    return {};
  },
  post: async (path: string) => (path === "/browser/setup/arm" ? api.arm : {}),
  put: async () => ({}),
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/computeruse",
}));

const { YourBrowserCard } = await import("@/components/browser/YourBrowserCard");
const { pickBrowser, browserKeyFromName, BROWSER_CHOICE_KEY } = await import(
  "@/components/browser/browserWords"
);

type Status = Record<string, unknown>;

const base: Status = {
  connected: false,
  access: "read_only",
  host_permission: false,
  extension_id: "lgihfomaieifpnemakmpadmggjnoojmm",
  extension_version: "",
  browser_name: "",
  browser_version: "",
  expected_extension_id: "lgihfomaieifpnemakmpadmggjnoojmm",
  active_tab: null,
  pending_pairing: null,
  paired: false,
  addon_dir: "C:\\Users\\vr\\AppData\\Local\\Programs\\Iron Jarvis\\resources\\browser-addon",
  last_error: null,
  installed_browsers: [],
};

const EDGE_ONLY: Status = { ...base, installed_browsers: ["Microsoft Edge"] };
const CHROME_ONLY: Status = { ...base, installed_browsers: ["Google Chrome"] };
const BOTH: Status = { ...base, installed_browsers: ["Google Chrome", "Microsoft Edge"] };
const BOTH_PAIRED_EDGE: Status = {
  ...BOTH,
  connected: true,
  paired: true,
  host_permission: true,
  browser_name: "Microsoft Edge",
  browser_version: "153",
  active_tab: { id: 1, title: "Example", url: "https://example.com/" },
};

beforeEach(() => {
  api.status = { ...base };
  api.arm = { armed: true, expires_in_s: 120, addon_dir: "" };
  try {
    window.localStorage.removeItem(BROWSER_CHOICE_KEY);
  } catch {
    /* no storage in this jsdom */
  }
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: async () => {} },
  });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

/** Everything the dialog says EXCEPT the browser toggle itself, which names both
 *  browsers by design — the sentences are what must be single-browser. */
const modalText = () => {
  const all = screen.getByTestId("browser-setup-modal").textContent ?? "";
  const toggle = screen.getByTestId("browser-setup-browser").textContent ?? "";
  return all.replace(toggle, " ");
};
/** Both browsers' own page addresses are quoted as addresses, never as a name for the add-on. */
const scrub = (t: string) => t.replace(/(chrome|edge):\/\/extensions/g, " ");

/** Open the dialog from the real card and wait for the arm to land. */
async function openFromCard(status: Status) {
  api.status = status;
  const view = render(<YourBrowserCard />);
  fireEvent.click(await screen.findByTestId("browser-setup-open"));
  await screen.findByTestId("browser-setup-countdown");
  return view;
}

/** Step 2 is reached by the press a user makes: Copy on step 1. */
async function driveToStep2() {
  fireEvent.click(await screen.findByTestId("browser-setup-folder-copy"));
  await screen.findByTestId("browser-setup-extensions-page");
}

/* -------------------------------------------------------------------------- */
/*  The pick                                                                   */
/* -------------------------------------------------------------------------- */

describe("pickBrowser: the paired browser, else the only installed one, else Chrome", () => {
  it("names Edge on an Edge-only PC and Chrome on a Chrome-only one", () => {
    expect(pickBrowser(EDGE_ONLY as never)).toBe("edge");
    expect(pickBrowser(CHROME_ONLY as never)).toBe("chrome");
  });
  it("prefers Chrome when both are installed and nothing has paired", () => {
    expect(pickBrowser(BOTH as never)).toBe("chrome");
  });
  it("lets the paired browser win over what is merely installed", () => {
    expect(pickBrowser(BOTH_PAIRED_EDGE as never)).toBe("edge");
    // Paired earlier, not running now — still the browser in use.
    expect(pickBrowser({ ...BOTH, paired: true, browser_name: "Microsoft Edge" } as never)).toBe("edge");
  });
  it("falls back to Chrome when the daemon is older and says nothing", () => {
    expect(pickBrowser(null)).toBe("chrome");
    expect(pickBrowser({ ...base, installed_browsers: undefined } as never)).toBe("chrome");
  });
  it("reads the names the add-on and the doctor actually send", () => {
    expect(browserKeyFromName("Microsoft Edge")).toBe("edge");
    expect(browserKeyFromName("Google Chrome")).toBe("chrome");
    expect(browserKeyFromName("Brave")).toBeNull();
    expect(browserKeyFromName("")).toBeNull();
  });
});

/* -------------------------------------------------------------------------- */
/*  The guided window                                                          */
/* -------------------------------------------------------------------------- */

describe("the guided window is written for the browser this PC has (v1.261.0)", () => {
  it("on an Edge-only PC every step is Edge's — its page, its pin, and no Chrome anywhere", async () => {
    await openFromCard(EDGE_ONLY);
    expect(screen.getByTestId("browser-setup-browser").dataset.browser).toBe("edge");
    expect(screen.getByTestId("browser-setup-browser-edge").getAttribute("aria-pressed")).toBe("true");
    // Step 1: the folder sentence names the browser that will ask for it.
    expect(modalText()).toContain("Edge asks for it by path");
    expect(modalText()).not.toMatch(/Chrome/);
    // Step 2: Edge's own page, and only Edge's.
    await driveToStep2();
    expect(screen.getByTestId("browser-setup-extensions-page").textContent).toBe("edge://extensions");
    expect(modalText()).toContain("Load it in Edge");
    expect(modalText()).not.toContain("chrome://extensions");
    expect(modalText()).not.toMatch(/Chrome/);
  });

  it("on an Edge-only PC the last step pins the icon Edge's way", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await openFromCard(EDGE_ONLY);
    // Step 5 arrives when the poll reports connected + site access.
    api.status = { ...EDGE_ONLY, connected: true, paired: true, host_permission: true, browser_name: "Microsoft Edge" };
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    const pin = await screen.findByTestId("browser-setup-pin");
    expect(pin.textContent).toContain("Edge does not put a newly loaded add-on on the toolbar");
    expect(pin.textContent).toContain("eye icon");
    expect(pin.textContent).toContain("Show in toolbar");
    expect(pin.textContent).not.toMatch(/press the pin/);
    const stale = screen.getByTestId("browser-setup-stale-build");
    expect(stale.textContent).toContain("edge://extensions");
    expect(stale.textContent).not.toContain("chrome://extensions");
    expect(modalText()).not.toMatch(/Chrome/);
  });

  it("on a Chrome-only PC the steps are Chrome's, with no Edge in them", async () => {
    await openFromCard(CHROME_ONLY);
    expect(screen.getByTestId("browser-setup-browser").dataset.browser).toBe("chrome");
    await driveToStep2();
    expect(screen.getByTestId("browser-setup-extensions-page").textContent).toBe("chrome://extensions");
    expect(modalText()).toContain("Load it in Chrome");
    expect(modalText()).not.toMatch(/Edge/);
  });

  it("with both installed it writes for Chrome until the user says Edge — and remembers", async () => {
    await openFromCard(BOTH);
    expect(screen.getByTestId("browser-setup-browser").dataset.browser).toBe("chrome");
    fireEvent.click(screen.getByTestId("browser-setup-browser-edge"));
    expect(screen.getByTestId("browser-setup-browser").dataset.browser).toBe("edge");
    await driveToStep2();
    expect(screen.getByTestId("browser-setup-extensions-page").textContent).toBe("edge://extensions");
    expect(window.localStorage.getItem(BROWSER_CHOICE_KEY)).toBe("edge");
    // A fresh mount on this device opens on the remembered choice.
    cleanup();
    await openFromCard(BOTH);
    expect(screen.getByTestId("browser-setup-browser").dataset.browser).toBe("edge");
  });

  it("with both installed, the browser that PAIRED is the one the steps are for", async () => {
    // A paired-but-down Edge still offers the guided window (paired_down state).
    await openFromCard({ ...BOTH, paired: true, browser_name: "Microsoft Edge" });
    expect(screen.getByTestId("browser-setup-browser").dataset.browser).toBe("edge");
  });

  it("never calls the add-on an extension in either browser's words", async () => {
    await openFromCard(EDGE_ONLY);
    await driveToStep2();
    expect(scrub(modalText())).not.toMatch(/\bextensions?\b/i);
    cleanup();
    await openFromCard(CHROME_ONLY);
    await driveToStep2();
    expect(scrub(modalText())).not.toMatch(/\bextensions?\b/i);
  });
});

/* -------------------------------------------------------------------------- */
/*  The card                                                                   */
/* -------------------------------------------------------------------------- */

describe("the card's own notes follow the same pick (v1.261.0)", () => {
  it("on an Edge-only PC the install steps and the sidebar notes are Edge's", async () => {
    api.status = EDGE_ONLY;
    render(<YourBrowserCard />);
    const badge = await screen.findByTestId("browser-badge");
    await waitFor(() => expect(badge.dataset.state).toBe("not_connected"));
    fireEvent.click(screen.getByTestId("browser-install-toggle"));
    const steps = screen.getByTestId("browser-install-steps").textContent ?? "";
    expect(steps).toContain("In Edge, open edge://extensions");
    expect(steps).not.toContain("chrome://extensions");
    expect(steps).not.toMatch(/Chrome/);
    expect(screen.getByTestId("browser-addon-packaged").textContent).toContain("Edge's Load unpacked box");
    const pin = screen.getByTestId("browser-sidebar-pin").textContent ?? "";
    expect(pin).toContain("eye icon");
    expect(pin).not.toMatch(/press the pin/);
    expect(screen.getByTestId("browser-sidebar-stale").textContent).toContain("edge://extensions");
    expect(scrub(screen.getByTestId("browser-sidebar-note").textContent ?? "")).not.toMatch(/\bextensions?\b/i);
  });

  it("once Edge has paired, the connected card's notes are Edge's too", async () => {
    api.status = BOTH_PAIRED_EDGE;
    render(<YourBrowserCard />);
    const badge = await screen.findByTestId("browser-badge");
    await waitFor(() => expect(badge.dataset.state).toBe("connected"));
    expect(screen.getByTestId("browser-identity").textContent).toContain("Microsoft Edge 153");
    expect(screen.getByTestId("browser-sidebar-stale").textContent).toContain("edge://extensions");
    expect(screen.getByTestId("browser-sidebar-pin").textContent).toContain("Show in toolbar");
  });
});
