/**
 * v1.264.0 — what the dashboard says when it is opened in a browser tab.
 *
 * The add-on's Open Jarvis button opens the dashboard in the browser, where
 * there is no token. The user saw "Daemon rejected your token … re-enter it"
 * (re-enter WHAT, from WHERE?) and the Browser card announcing "This daemon
 * does not have the Browser surface yet — restart Iron Jarvis" over what was
 * really its own 401. Both now say the true thing and name the way out:
 *   - the token banner, outside the desktop shell, offers "Open in the Iron
 *     Jarvis app" (an ironjarvis:// link to THIS page) and names token.txt;
 *     inside the shell it keeps its old sentence and no link;
 *   - the Browser card tells a 401/403 (this page is not signed in) from a
 *     404 (an older daemon), and only the latter says "restart".
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

const D = vi.hoisted(() => ({
  unauthorized: true,
  desktop: false,
  pathname: "/computeruse",
  statusError: null as { message: string; status: number } | null,
}));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: D.unauthorized,
    requestError: false,
    health: null,
    checking: false,
    refresh: () => {},
    provided: true,
  }),
}));
vi.mock("@/lib/desktopShell", () => ({
  isDesktopShell: () => D.desktop,
  DESKTOP_OFFLINE_HINT: "desktop hint",
}));
vi.mock("next/navigation", () => ({
  usePathname: () => D.pathname,
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
}));
vi.mock("framer-motion", () => ({
  AnimatePresence: ({ children }: { children?: unknown }) => <>{children}</>,
  m: { div: ({ children, ...rest }: { children?: unknown } & Record<string, unknown>) => <div {...(rest as object)}>{children as never}</div> },
  motion: { div: ({ children }: { children?: unknown }) => <div>{children as never}</div> },
}));

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return { FakeApiError };
});
vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => "",
  sseUrl: (p: string) => p,
  wsUrl: (p: string) => p,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (path: string) => {
    if (path === "/browser/status" && D.statusError) {
      throw new api.FakeApiError(D.statusError.message, D.statusError.status);
    }
    if (path === "/health") return { status: "ok", version: "1.264.0", providers: [], default_provider: "mock" };
    return {};
  },
  post: async () => ({}),
  put: async () => ({}),
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));

const { DaemonBanner, appLink } = await import("@/components/DaemonBanner");
const { YourBrowserCard } = await import("@/components/browser/YourBrowserCard");

beforeEach(() => {
  D.unauthorized = true;
  D.desktop = false;
  D.pathname = "/computeruse";
  D.statusError = null;
});
afterEach(() => cleanup());

describe("the token banner names the way out of a tokenless browser tab (v1.264.0)", () => {
  it("outside the desktop shell: an ironjarvis:// link to THIS page, and the token file", () => {
    render(<DaemonBanner />);
    const link = screen.getByTestId("open-in-app") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("ironjarvis://computeruse");
    expect(link.textContent).toContain("Open in the Iron Jarvis app");
    expect(screen.getByRole("alert").textContent).toContain("token.txt");
    expect(screen.getByRole("alert").textContent).toContain("opened in a browser");
  });

  it("inside the desktop shell: the old sentence, no link (the app already has the token)", () => {
    D.desktop = true;
    render(<DaemonBanner />);
    expect(screen.queryByTestId("open-in-app")).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("Re-enter it to reconnect");
  });

  it("the link only ever names a dashboard path", () => {
    expect(appLink("/chat")).toBe("ironjarvis://chat");
    expect(appLink("/sessions/abc-123")).toBe("ironjarvis://sessions/abc-123");
    expect(appLink("/")).toBe("ironjarvis://");
    expect(appLink(null)).toBe("ironjarvis://");
    expect(appLink("/../x")).toBe("ironjarvis://");
    expect(appLink("//evil.example.com/")).toBe("ironjarvis://");
    expect(appLink("/evil.example.com")).toBe("ironjarvis://");
    expect(appLink("/chat?x=<script>")).toBe("ironjarvis://");
  });
});

describe("the Browser card tells 'not signed in' from 'no Browser surface'", () => {
  it("a 401 is this page not being signed in — never a reason to restart", async () => {
    D.statusError = { message: "missing or invalid token", status: 401 };
    render(<YourBrowserCard />);
    const note = await screen.findByTestId("browser-not-signed-in");
    expect(note.textContent).toContain("not signed in");
    expect(note.textContent).toContain("Iron Jarvis app");
    expect(screen.queryByText(/does not have the Browser surface yet/)).toBeNull();
    expect(document.body.textContent).not.toMatch(/restart Iron Jarvis/);
  });

  it("a 404 is still an older daemon, and still says restart", async () => {
    D.statusError = { message: "Not Found", status: 404 };
    render(<YourBrowserCard />);
    await screen.findByText(/does not have the Browser surface yet/);
    await waitFor(() => expect(screen.queryByTestId("browser-not-signed-in")).toBeNull());
  });
});
