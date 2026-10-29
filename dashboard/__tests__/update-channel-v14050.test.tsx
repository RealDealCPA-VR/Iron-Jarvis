/**
 * v1.405.0 — Stable / Fresh on the Updates page (desktop app only).
 *
 *  - The choice is drawn only when the desktop bridge has `channels` (an
 *    older shell ignores the setting; a browser tab has no bridge).
 *  - Each choice is one sentence (the spec's words); each shows the version
 *    its channel offers; the PC's own version is said.
 *  - A PC on Stable that runs a NEWER version than Stable is told it stays
 *    there until Stable passes it (no downgrade).
 *  - A choice saves `update_channel` through PUT /settings/values and ONLY
 *    THEN asks the desktop for a check; a refused save checks nothing.
 *  - "Keep this version off Stable": two presses through the daemon when the
 *    GitHub CLI can do it; otherwise the exact command and the Actions page.
 *  - The words never claim the other PC ran or tested anything.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    gets: {} as Record<string, unknown>,
    putError: null as null | Error,
    postAnswer: {} as unknown,
    postError: null as null | Error,
    calls: [] as string[],
    puts: [] as { path: string; body: unknown }[],
    posts: [] as { path: string; body: unknown }[],
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: S.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => null,
  get: (path: string) => {
    S.calls.push(`get ${path}`);
    return path in S.gets ? Promise.resolve(S.gets[path]) : Promise.resolve({});
  },
  post: (path: string, body?: unknown) => {
    S.calls.push(`post ${path}`);
    S.posts.push({ path, body });
    return S.postError ? Promise.reject(S.postError) : Promise.resolve(S.postAnswer);
  },
  put: (path: string, body?: unknown) => {
    S.calls.push(`put ${path}`);
    S.puts.push({ path, body });
    return S.putError ? Promise.reject(S.putError) : Promise.resolve({ updated: ["update_channel"] });
  },
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useApi", () => ({
  useApi: () => ({
    data: { available: false, reason: "not a source checkout (installed package)" },
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const tag = (t: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) {
      if (!["initial", "animate", "exit", "transition", "variants", "layout", "whileHover", "whileTap"].includes(k)) {
        rest[k] = v;
      }
    }
    return createElement(t, rest);
  };
  const motion = new Proxy({} as Record<string, unknown>, { get: (_t, k) => tag(String(k)) });
  return {
    m: motion,
    motion,
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
    LazyMotion: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
    domAnimation: {},
  };
});

import UpdatesPage from "@/components/settings/pages/UpdatesPage";
import { CHANNEL_WORDS, aheadOfStable, compareVersions, stableHasIt } from "@/lib/updateChannel";

type Versions = { channel: string; current: string | null; fresh: string | null; stable: string | null };

function installBridge(versions: Versions | null, opts: { noChannels?: boolean } = {}) {
  const bridge: Record<string, unknown> = {
    getState: vi.fn(() => Promise.resolve({ status: "up-to-date", current: versions?.current ?? "1.405.0" })),
    check: vi.fn(() => {
      S.calls.push("check");
      return Promise.resolve({ status: "checking", current: versions?.current ?? "1.405.0" });
    }),
    apply: vi.fn(() => Promise.resolve(true)),
    onState: vi.fn(() => () => {}),
  };
  if (!opts.noChannels) bridge.channels = vi.fn(() => Promise.resolve(versions));
  (window as unknown as { ironjarvis?: unknown }).ironjarvis = { update: bridge };
  return bridge as { check: ReturnType<typeof vi.fn>; channels?: ReturnType<typeof vi.fn> };
}

const ON_FRESH: Versions = { channel: "fresh", current: "1.405.0", fresh: "1.405.2", stable: "1.404.0" };

beforeEach(() => {
  S.gets = {};
  S.putError = null;
  S.postAnswer = {};
  S.postError = null;
  S.calls = [];
  S.puts = [];
  S.posts = [];
});

afterEach(() => {
  cleanup();
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
});

describe("the choice", () => {
  it("draws Stable and Fresh, each in one sentence, with the version each offers and this PC's", async () => {
    installBridge(ON_FRESH);
    render(<UpdatesPage />);
    const fresh = await screen.findByTestId("update-channel-fresh");
    await waitFor(() => expect(fresh).toHaveAttribute("aria-checked", "true"));
    const stable = screen.getByTestId("update-channel-stable");
    expect(stable).toHaveAttribute("aria-checked", "false");
    expect(within(stable).getByText(
      "Gets a release once it has been out for a day with every test passing, unless you held it back.",
    )).toBeInTheDocument();
    expect(within(fresh).getByText("Gets every release as soon as it is published.")).toBeInTheDocument();
    expect(screen.getByTestId("update-channel-stable-version").textContent).toContain("offers v1.404.0");
    expect(screen.getByTestId("update-channel-fresh-version").textContent).toContain("offers v1.405.2");
    expect(screen.getByTestId("update-channel-pc").textContent).toBe("This PC runs v1.405.0.");
    expect(screen.getByRole("radiogroup", { name: "Update channel" })).toBeInTheDocument();
    // On Fresh there is nothing to wait for.
    expect(screen.queryByTestId("update-channel-ahead")).toBeNull();
  });

  it("says a PC on Stable that runs a newer version stays there until Stable passes it", async () => {
    installBridge({ ...ON_FRESH, channel: "stable" });
    render(<UpdatesPage />);
    const ahead = await screen.findByTestId("update-channel-ahead");
    expect(ahead.textContent).toBe(
      "This PC runs v1.405.0, newer than Stable's v1.404.0 (it was on Fresh). It stays on v1.405.0 " +
        "until Stable passes it; it never goes back to an older version.",
    );
    expect(screen.getByTestId("update-channel-stable")).toHaveAttribute("aria-checked", "true");
  });

  it("saves the setting through the one writer and only THEN asks for a check", async () => {
    const bridge = installBridge(ON_FRESH);
    render(<UpdatesPage />);
    const stable = await screen.findByTestId("update-channel-stable");
    await waitFor(() => expect(stable).not.toBeDisabled());
    fireEvent.click(stable);
    await waitFor(() => expect(screen.getByText("This PC is on Stable now.")).toBeInTheDocument());
    expect(S.puts).toEqual([{ path: "/settings/values", body: { values: { update_channel: "stable" } } }]);
    expect(bridge.check).toHaveBeenCalledTimes(1);
    expect(S.calls.indexOf("put /settings/values")).toBeLessThan(S.calls.indexOf("check"));
    expect(stable).toHaveAttribute("aria-checked", "true");
    // Choosing the channel it is already on saves nothing.
    fireEvent.click(stable);
    expect(S.puts).toHaveLength(1);
  });

  it("a refused save says so, changes nothing and checks nothing", async () => {
    const bridge = installBridge(ON_FRESH);
    S.putError = new S.FakeApiError("Update channel must be one of: fresh, stable.", 400);
    render(<UpdatesPage />);
    const stable = await screen.findByTestId("update-channel-stable");
    await waitFor(() => expect(stable).not.toBeDisabled());
    fireEvent.click(stable);
    await waitFor(() =>
      expect(
        screen.getByText("The channel was not changed: Update channel must be one of: fresh, stable."),
      ).toBeInTheDocument(),
    );
    expect(bridge.check).not.toHaveBeenCalled();
    expect(stable).toHaveAttribute("aria-checked", "false");
    expect(screen.getByTestId("update-channel-fresh")).toHaveAttribute("aria-checked", "true");
  });

  it("is not drawn on a desktop shell without `channels`, nor in a browser tab", async () => {
    installBridge(ON_FRESH, { noChannels: true });
    render(<UpdatesPage />);
    await screen.findByText("Installed version");
    expect(screen.queryByTestId("update-channel")).toBeNull();
    cleanup();
    delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
    render(<UpdatesPage />);
    await screen.findByText(/Updates install from the desktop app on your PC/);
    expect(screen.queryByTestId("update-channel")).toBeNull();
  });
});

describe("keep this version off Stable", () => {
  const HOLD = "/update/stable-hold?version=1.405.0";

  it("asks twice, then posts the hold for THIS PC's version", async () => {
    installBridge(ON_FRESH);
    S.gets[HOLD] = {
      version: "1.405.0", can_run: true, reason: "",
      command: "gh workflow run stable.yml --repo RealDealCPA-VR/Iron-Jarvis -f hold=1.405.0",
      actions_url: "https://github.com/RealDealCPA-VR/Iron-Jarvis/actions/workflows/stable.yml",
    };
    S.postAnswer = { ok: true, message: "Asked GitHub to keep v1.405.0 off Stable." };
    render(<UpdatesPage />);
    fireEvent.click(await screen.findByTestId("update-hold-start"));
    const confirm = await screen.findByTestId("update-hold-confirm");
    expect(confirm.textContent).toContain("Press again to keep v1.405.0 off Stable");
    expect(S.posts).toEqual([]);
    fireEvent.click(confirm);
    await waitFor(() => expect(screen.getByText("Asked GitHub to keep v1.405.0 off Stable.")).toBeInTheDocument());
    expect(S.posts).toEqual([{ path: "/update/stable-hold", body: { version: "1.405.0", confirm: true } }]);
  });

  it("shows the command and the Actions page when the app cannot ask, and posts nothing", async () => {
    installBridge(ON_FRESH);
    S.gets[HOLD] = {
      version: "1.405.0", can_run: false,
      reason: "The GitHub CLI (gh) is not installed on this PC, so the app cannot ask GitHub for you.",
      command: "gh workflow run stable.yml --repo RealDealCPA-VR/Iron-Jarvis -f hold=1.405.0",
      actions_url: "https://github.com/RealDealCPA-VR/Iron-Jarvis/actions/workflows/stable.yml",
    };
    render(<UpdatesPage />);
    fireEvent.click(await screen.findByTestId("update-hold-start"));
    const byHand = await screen.findByTestId("update-hold-by-hand");
    expect(byHand.textContent).toContain("gh workflow run stable.yml --repo RealDealCPA-VR/Iron-Jarvis -f hold=1.405.0");
    expect(byHand.textContent).toContain("is not installed");
    const link = within(byHand).getByRole("link", { name: /the Stable channel on GitHub/ });
    expect(link).toHaveAttribute("href", "https://github.com/RealDealCPA-VR/Iron-Jarvis/actions/workflows/stable.yml");
    expect(screen.queryByTestId("update-hold-confirm")).toBeNull();
    expect(S.posts).toEqual([]);
  });

  it("offers no hold for a version Stable already has: Stable never moves back", async () => {
    installBridge({ ...ON_FRESH, current: "1.404.0" });
    render(<UpdatesPage />);
    const already = await screen.findByTestId("update-hold-already");
    expect(already.textContent).toContain("v1.404.0 is already on Stable");
    expect(screen.queryByTestId("update-hold-start")).toBeNull();
  });

  it("never claims the other PC ran or tested anything; the wait is time", async () => {
    installBridge(ON_FRESH);
    render(<UpdatesPage />);
    const box = await screen.findByTestId("update-channel");
    const words = (box.textContent ?? "").toLowerCase();
    for (const claim of ["tested on your other pc", "ran on your other pc", "your other pc tested"]) {
      expect(words).not.toContain(claim);
    }
    expect(words).toContain("time only");
    expect(words).toContain("hold a version back");
  });
});

describe("the helpers", () => {
  it("compare versions by number, never as text", () => {
    expect(compareVersions("1.10.0", "1.9.0")).toBe(1);
    expect(compareVersions("v1.404.0", "1.404.0")).toBe(0);
    expect(compareVersions("1.404.0", "nonsense")).toBeNull();
    expect(aheadOfStable("1.405.0", "1.404.0")).toBe(true);
    expect(aheadOfStable("1.404.0", "1.404.0")).toBe(false);
    expect(aheadOfStable("1.405.0", null)).toBe(false);
    expect(stableHasIt("1.404.0", "1.404.0")).toBe(true);
    expect(stableHasIt("1.403.0", "1.404.0")).toBe(true);
    expect(stableHasIt("1.405.0", "1.404.0")).toBe(false);
    expect(stableHasIt("1.405.0", null)).toBe(false);
    expect(Object.keys(CHANNEL_WORDS).sort()).toEqual(["fresh", "stable"]);
  });
});
