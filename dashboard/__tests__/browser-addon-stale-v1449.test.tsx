/**
 * The Your browser card compares the add-on's version with the app's
 * (v1.449.0, readiness Wave C item 31 — ease-of-use review item 9).
 *
 * The add-on reports its version on hello and `GET /browser/status` has
 * forwarded it as `extension_version` since v1.242.0, but the card only
 * printed the app's number and told the user to go read the other one in
 * the sidebar. Now an OLDER add-on is named on the card with the one press
 * that fixes it; an equal or newer one, or a missing number, says nothing.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { BROWSERS, compareVersions, staleAddonLine } from "@/components/browser/browserWords";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return { FakeApiError, status: null as Record<string, unknown> | null, appVersion: "1.448.1" };
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
      return { status: "ok", version: api.appVersion, providers: [], default_provider: "mock" };
    return {};
  },
  post: async () => ({}),
  put: async () => ({}),
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));

const { YourBrowserCard } = await import("@/components/browser/YourBrowserCard");
const { __resetApiCache } = await import("@/lib/apiCache");

const paired = (extension_version: string | undefined) => ({
  connected: true,
  access: "read_only",
  host_permission: true,
  extension_id: "lgihfomaieifpnemakmpadmggjnoojmm",
  ...(extension_version === undefined ? {} : { extension_version }),
  browser_name: "Microsoft Edge",
  browser_version: "153",
  active_tab: null,
  pending_pairing: null,
  paired: true,
  last_error: null,
});

describe("staleAddonLine (pure)", () => {
  it("orders versions numerically, not as text", () => {
    expect(compareVersions("1.9.0", "1.10.0")).toBe(-1);
    expect(compareVersions("1.448.1", "1.448.1")).toBe(0);
    expect(compareVersions("v2.0.0", "1.999.9")).toBe(1);
    expect(compareVersions("", "1.0.0")).toBeNull();
    expect(compareVersions("dev", "1.0.0")).toBeNull();
  });

  it("names an OLDER add-on, with the browser's own page and Reload", () => {
    const line = staleAddonLine("1.440.0", "1.448.1", BROWSERS.edge);
    expect(line).toContain("older copy");
    expect(line).toContain("1.440.0");
    expect(line).toContain("1.448.1");
    expect(line).toContain("edge://extensions");
    expect(line).toContain("Reload");
    expect(staleAddonLine("1.440.0", "1.448.1", BROWSERS.chrome)).toContain("chrome://extensions");
  });

  it("says nothing for an equal, a newer or an unknown number", () => {
    expect(staleAddonLine("1.448.1", "1.448.1", BROWSERS.chrome)).toBeNull();
    expect(staleAddonLine("1.450.0", "1.448.1", BROWSERS.chrome)).toBeNull();
    expect(staleAddonLine("", "1.448.1", BROWSERS.chrome)).toBeNull();
    expect(staleAddonLine("1.440.0", "", BROWSERS.chrome)).toBeNull();
  });
});

describe("the card says when the browser runs an older copy", () => {
  beforeEach(() => {
    __resetApiCache();
    api.status = null;
    api.appVersion = "1.448.1";
  });
  afterEach(() => cleanup());

  it("an older add-on gets the line, in the paired browser's words", async () => {
    api.status = paired("1.440.0");
    render(<YourBrowserCard />);
    const line = await screen.findByTestId("browser-addon-stale");
    expect(line.textContent).toContain("Edge is running an older copy of the add-on (1.440.0; this app is 1.448.1)");
    expect(line.textContent).toContain("edge://extensions");
  });

  it("the same version draws no line", async () => {
    api.status = paired("1.448.1");
    render(<YourBrowserCard />);
    await screen.findByTestId("browser-identity");
    await waitFor(() => expect(screen.getByTestId("browser-sidebar-app-version").textContent).toBe("1.448.1"));
    expect(screen.queryByTestId("browser-addon-stale")).toBeNull();
  });

  it("a daemon that sends no add-on version draws no line (never a guess)", async () => {
    api.status = paired(undefined);
    render(<YourBrowserCard />);
    await screen.findByTestId("browser-identity");
    await waitFor(() => expect(screen.getByTestId("browser-sidebar-app-version").textContent).toBe("1.448.1"));
    expect(screen.queryByTestId("browser-addon-stale")).toBeNull();
  });
});
