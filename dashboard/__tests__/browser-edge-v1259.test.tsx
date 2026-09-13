/**
 * v1.259.0 — the browser add-on says WHICH browser it is, and the card says it.
 *
 * Plain words: Chrome and Edge load the very same add-on with the very same
 * identity, so until now Iron Jarvis could not tell an Edge user from a Chrome
 * one — and every setup step was written for Chrome. Now the add-on reports its
 * browser when it greets the daemon, and the Browser card prints it.
 *
 * Two halves pinned here:
 *   1. the add-on's `describeBrowser()` — brands first, UA fallback, and NEVER a
 *      guess when nothing can be said;
 *   2. the card renders "Paired browser: Microsoft Edge 153" from the status,
 *      and renders nothing of the kind when the status carries no browser.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { describeBrowser } from "../../extensions/chrome/src/bridge/browser-identity";

/* ------------------------------------------------------------------ the add-on */

describe("describeBrowser — the add-on names its browser truthfully", () => {
  it("prefers the real brand over the Chromium / Not A;Brand noise", () => {
    const nav = {
      userAgentData: {
        brands: [
          { brand: "Not A(Brand", version: "8" },
          { brand: "Chromium", version: "153" },
          { brand: "Microsoft Edge", version: "153" },
        ],
      },
      userAgent: "Mozilla/5.0 ... Chrome/153.0.0.0 Safari/537.36 Edg/153.0.4234.32",
    };
    expect(describeBrowser(nav)).toEqual({ name: "Microsoft Edge", version: "153" });
  });

  it("names Chrome the same way", () => {
    const nav = {
      userAgentData: { brands: [{ brand: "Google Chrome", version: "140" }, { brand: "Chromium", version: "140" }] },
    };
    expect(describeBrowser(nav)).toEqual({ name: "Google Chrome", version: "140" });
  });

  it("falls back to the UA string, where Edge is the Edg/ token", () => {
    const ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153.0.0.0 Safari/537.36 Edg/153.0.4234.32";
    expect(describeBrowser({ userAgent: ua })).toEqual({ name: "Microsoft Edge", version: "153.0.4234.32" });
    expect(describeBrowser({ userAgentData: { brands: [{ brand: "Chromium", version: "153" }] }, userAgent: ua })).toEqual({
      name: "Microsoft Edge",
      version: "153.0.4234.32",
    });
  });

  it("a Chrome UA without Edg/ is Chrome", () => {
    const ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36";
    expect(describeBrowser({ userAgent: ua })).toEqual({ name: "Google Chrome", version: "140.0.0.0" });
  });

  it("says nothing rather than guessing", () => {
    expect(describeBrowser(undefined)).toBeUndefined();
    expect(describeBrowser({})).toBeUndefined();
    expect(describeBrowser({ userAgent: "curl/8.0" })).toBeUndefined();
  });
});

/* --------------------------------------------------------------------- the card */

type Status = Record<string, unknown>;

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
    posts: [] as { path: string; body?: unknown }[],
    puts: [] as { path: string; body?: unknown }[],
    status: null as Record<string, unknown> | null,
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
    if (path === "/health")
      return { status: "ok", version: "1.259.0", providers: [], default_provider: "mock" };
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

const { YourBrowserCard } = await import("@/components/browser/YourBrowserCard");

const connected: Status = {
  connected: true,
  access: "read_only",
  host_permission: true,
  extension_id: "lgihfomaieifpnemakmpadmggjnoojmm",
  extension_version: "1.259.0",
  active_tab: null,
  pending_pairing: null,
  paired: true,
  last_error: null,
};

describe("the Browser card names the paired browser", () => {
  beforeEach(() => {
    api.gets.length = 0;
    api.posts.length = 0;
    api.puts.length = 0;
  });
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("prints the name and version the add-on reported", async () => {
    api.status = { ...connected, browser_name: "Microsoft Edge", browser_version: "153" };
    render(<YourBrowserCard />);
    const line = await screen.findByTestId("browser-identity");
    expect(line.textContent).toContain("Microsoft Edge 153");
  });

  it("prints nothing of the kind when the add-on did not say — never a guess", async () => {
    api.status = { ...connected, browser_name: "", browser_version: "" };
    render(<YourBrowserCard />);
    await screen.findByTestId("browser-badge");
    expect(screen.queryByTestId("browser-identity")).toBeNull();
  });

  it("does not claim a browser that is not connected", async () => {
    api.status = { ...connected, connected: false, browser_name: "Microsoft Edge", browser_version: "153" };
    render(<YourBrowserCard />);
    await screen.findByTestId("browser-badge");
    expect(screen.queryByTestId("browser-identity")).toBeNull();
  });

  it("the identity line never calls the add-on an extension", async () => {
    api.status = { ...connected, browser_name: "Microsoft Edge", browser_version: "153" };
    render(<YourBrowserCard />);
    const line = await screen.findByTestId("browser-identity");
    expect(line.textContent ?? "").not.toMatch(/extension/i);
  });
});
