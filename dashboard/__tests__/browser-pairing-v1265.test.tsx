/**
 * v1.265.0 — Forget is reachable wherever a credential exists, and the card says
 * what Pair will do to the pairing this install already holds.
 *
 * THE REPORT: every Pair press answered "AUTHENTICATION_FAILED: This install is
 * already paired with a browser. Press Forget on the Browser page in Iron Jarvis
 * before pairing another." — and the Browser page had no Forget button. It was
 * rendered only under Connected, and the install was "paired" with a browser
 * that was not there (a credential minted for a socket that had already closed).
 * The daemon's own remedy pointed at a control the page did not show in the one
 * state that needed it.
 *
 * Pinned here, against the REAL card with its real poll:
 *
 *  - PAIRED, NOT RUNNING offers Forget, and it still takes two clicks (the arm
 *    step is what keeps an accidental click from destroying a credential).
 *  - WAITING with an idle pairing says Pair REPLACES it — the user is told what
 *    the press does — and still offers Forget for the user who would rather end
 *    it first.
 *  - WAITING with a CONNECTED pairing says Forget is needed first, and the Forget
 *    line beside it says which pairing it ends.
 *  - WAITING with no pairing says nothing about replacing, and offers no Forget:
 *    there is nothing to forget, and a button that posts to nothing is a lie.
 *  - CONNECTED keeps exactly ONE Forget (its own, next to Test and Disconnect):
 *    the new line must not double it.
 *  - NOT CONNECTED with no pairing offers no Forget.
 *  - None of the new words call the add-on an "extension" (VOCABULARY.md).
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
    if (path === "/computeruse") return { enabled: false, domain_allowlist: [], action_allowlist: [] };
    if (path === "/computeruse/approvals") return { approvals: [] };
    if (path.startsWith("/computeruse/runs")) return { runs: [] };
    if (path.startsWith("/computeruse")) return { pending_approvals: 0 };
    if (path === "/health")
      return { status: "ok", version: "1.265.0", providers: [], default_provider: "mock" };
    return {};
  },
  post: async (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    if (path === "/browser/pair") return { paired: true };
    if (path === "/browser/forget") return { forgotten: true };
    return {};
  },
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
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

const { YourBrowserCard } = await import("@/components/browser/YourBrowserCard");

type Status = Record<string, unknown>;

const base: Status = {
  connected: false,
  access: "interactive",
  host_permission: false,
  extension_id: "lgihfomaieifpnemakmpadmggjnoojmm",
  expected_extension_id: "lgihfomaieifpnemakmpadmggjnoojmm",
  active_tab: null,
  pending_pairing: null,
  paired: false,
  last_error: null,
};

const PENDING = {
  request_id: "pair_abc123",
  first_seen_at: "2026-09-06T12:00:00Z",
  extension_id: "lgihfomaieifpnemakmpadmggjnoojmm",
};

const STATUS = {
  not_connected: { ...base },
  paired_down: { ...base, paired: true },
  waiting_unpaired: { ...base, pending_pairing: PENDING },
  waiting_idle_pairing: { ...base, pending_pairing: PENDING, paired: true },
  waiting_connected_pairing: { ...base, pending_pairing: PENDING, paired: true, connected: true },
  connected: {
    ...base,
    connected: true,
    paired: true,
    host_permission: true,
    active_tab: { id: 42, title: "Example Domain", url: "https://example.com/" },
  },
} satisfies Record<string, Status>;

beforeEach(() => {
  localStorage.clear();
  api.gets.length = 0;
  api.posts.length = 0;
  api.status = { ...base };
});
afterEach(() => cleanup());

const postPaths = () => api.posts.map((p) => p.path);

async function mount(status: Status) {
  api.status = status;
  render(<YourBrowserCard />);
  await screen.findByTestId("browser-badge");
  // The card's own data has landed once the access selector is up — an
  // absence asserted before that would pass on a still-loading card.
  await waitFor(() => expect(screen.getByTestId("browser-access")).toBeTruthy());
}

describe("Forget wherever a credential exists (v1.265.0)", () => {
  it("Paired — not running offers Forget, and it still takes two clicks", async () => {
    await mount(STATUS.paired_down);
    const line = await screen.findByTestId("browser-forget-line");
    expect(line.textContent).toContain("Deletes the pairing this install still holds");
    fireEvent.click(screen.getByRole("button", { name: /forget/i }));
    expect(postPaths()).toEqual([]);
    fireEvent.click(await screen.findByRole("button", { name: /confirm/i }));
    await screen.findByText(/asks to pair again/);
    expect(postPaths()).toEqual(["/browser/forget"]);
  });

  it("Waiting with an idle pairing says Pair replaces it, and still offers Forget", async () => {
    await mount(STATUS.waiting_idle_pairing);
    const note = await screen.findByTestId("browser-waiting-replace");
    expect(note.textContent).toContain("replaces that pairing");
    expect(note.textContent).not.toContain("Forget first");
    expect(screen.getByTestId("browser-forget-line")).toBeTruthy();
    fireEvent.click(screen.getByTestId("browser-pair"));
    await waitFor(() => expect(postPaths()).toEqual(["/browser/pair"]));
  });

  it("Waiting with a CONNECTED pairing says Forget is needed first, and names what it ends", async () => {
    await mount(STATUS.waiting_connected_pairing);
    const note = await screen.findByTestId("browser-waiting-replace");
    expect(note.textContent).toContain("Forget first");
    expect(note.textContent).not.toContain("replaces");
    const line = screen.getByTestId("browser-forget-line");
    expect(line.textContent).toContain("Ends the connected browser's pairing");
  });

  it("Waiting with no pairing says nothing about replacing and offers no Forget", async () => {
    await mount(STATUS.waiting_unpaired);
    expect(screen.queryByTestId("browser-waiting-replace")).toBeNull();
    expect(screen.queryByTestId("browser-forget-line")).toBeNull();
    expect(screen.queryByRole("button", { name: /forget/i })).toBeNull();
  });

  it("Connected keeps exactly one Forget — its own", async () => {
    await mount(STATUS.connected);
    // The connected row (Test / Disconnect / Forget) is up once the live tab is.
    await screen.findByTestId("browser-active-tab");
    await screen.findByRole("button", { name: /forget/i });
    expect(screen.queryByTestId("browser-forget-line")).toBeNull();
    expect(screen.getAllByRole("button", { name: /forget/i })).toHaveLength(1);
  });

  it("Not connected with no pairing offers no Forget", async () => {
    await mount(STATUS.not_connected);
    expect(screen.queryByTestId("browser-forget-line")).toBeNull();
    expect(screen.queryByRole("button", { name: /forget/i })).toBeNull();
  });

  it("none of the new words call the add-on an extension", async () => {
    for (const status of [STATUS.paired_down, STATUS.waiting_idle_pairing, STATUS.waiting_connected_pairing]) {
      await mount(status);
      const line = screen.getByTestId("browser-forget-line").textContent ?? "";
      const note = screen.queryByTestId("browser-waiting-replace")?.textContent ?? "";
      expect((line + note).toLowerCase()).not.toContain("extension");
      cleanup();
    }
  });
});
