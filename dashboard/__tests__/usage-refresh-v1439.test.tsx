/**
 * v1.439.0 — "Check usage now" on an account.
 *
 * WHAT THESE TESTS GUARD:
 *  - ONE shared control (components/accounts/UsageRefresh): a 14 px refresh
 *    icon titled "Check usage now" whose accessible name names the account;
 *    a press POSTs /build/accounts/{id}/usage/refresh, the icon spins while it
 *    reads, then the note says "as of just now" — or the daemon's own words
 *    when the read failed (a 200 with ok: false) or was refused (a 409);
 *  - the words sit in a live region mounted WITH the button (announced);
 *  - Chat › Code's account card shows it only for an account the daemon says
 *    can be read, and draws the FRESH number the read answered;
 *  - the Connections card says "N% of the 7-day window used" and offers the
 *    button only when `can_refresh`;
 *  - Build › Code's Usage tab offers it for the session's account and reads
 *    the tab again after a read.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const hooks = vi.hoisted(() => ({
  posts: [] as Array<{ path: string; body: unknown }>,
  gets: [] as string[],
  answer: null as unknown,
  refuse: null as { status: number; message: string } | null,
  hold: null as null | Promise<void>,
  getAnswers: {} as Record<string, unknown>,
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    cancelled = false;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError,
    API_BASE: "http://127.0.0.1:8787",
    ijToken: () => null,
    get: async (path: string) => {
      hooks.gets.push(path);
      return hooks.getAnswers[path] ?? {};
    },
    post: async (path: string, body?: unknown) => {
      hooks.posts.push({ path, body });
      if (hooks.hold) await hooks.hold;
      if (hooks.refuse) throw new ApiError(hooks.refuse.message, hooks.refuse.status);
      return hooks.answer;
    },
    patch: async () => ({}),
    put: async () => ({}),
    del: async () => ({}),
  };
});

vi.mock("@/components/BrandGlyph", () => ({ ProviderMark: () => null }));
vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* () {},
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => null }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/connections",
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));

import { UsageRefresh } from "@/components/accounts/UsageRefresh";
import { asOfWords, decodeUsageRefresh } from "@/lib/accountUsage";
import { AccountCard } from "@/components/harness/HarnessAccounts";
import { decodeAccount } from "@/lib/buildAccounts";
import { windowUsedLine } from "@/lib/ironProxy";
import { CodePanes } from "@/components/buildCode/CodePanes";
import { decodeCodeSession, decodeCodingTools } from "@/lib/buildApp";

const NOW_ISO = () => new Date().toISOString().replace(/\.\d+Z$/, "Z");

function okAnswer(account: unknown = null) {
  return { account, read: { at: NOW_ISO(), ok: true, words: "Usage checked.", throttled: false } };
}

beforeEach(() => {
  hooks.posts = [];
  hooks.gets = [];
  hooks.answer = okAnswer();
  hooks.refuse = null;
  hooks.hold = null;
  hooks.getAnswers = {};
});

afterEach(() => cleanup());

describe("the shared control", () => {
  it("posts the account's refresh, spins while it reads, then says when", async () => {
    let release!: () => void;
    hooks.hold = new Promise<void>((r) => (release = r));
    const onRead = vi.fn();
    render(<UsageRefresh accountId="c-work" accountName="Work Max" onRead={onRead} />);
    const btn = screen.getByTestId("usage-refresh-c-work");
    expect(btn.getAttribute("title")).toBe("Check usage now");
    expect(btn.getAttribute("aria-label")).toBe("Check usage now for Work Max");
    // The live region is there BEFORE any words (so the answer is announced).
    const note = screen.getByTestId("usage-refresh-note-c-work");
    expect(note.getAttribute("role")).toBe("status");
    expect(note.textContent).toBe("");
    fireEvent.click(btn);
    await waitFor(() => expect(hooks.posts).toHaveLength(1));
    expect(hooks.posts[0].path).toBe("/build/accounts/c-work/usage/refresh");
    expect(btn.querySelector("svg")?.getAttribute("class") ?? "").toContain("animate-spin");
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    await act(async () => release());
    await waitFor(() => expect(note.textContent).toBe("as of just now"));
    expect(btn.querySelector("svg")?.getAttribute("class") ?? "").not.toContain("animate-spin");
    expect(onRead).toHaveBeenCalledTimes(1);
  });

  it("says the daemon's own words for a read that failed", async () => {
    hooks.answer = { account: null, read: { at: NOW_ISO(), ok: false, words: "Codex did not answer in 15 seconds.", throttled: false } };
    render(<UsageRefresh accountId="x" accountName="Codex" />);
    fireEvent.click(screen.getByTestId("usage-refresh-x"));
    const note = screen.getByTestId("usage-refresh-note-x");
    await waitFor(() => expect(note.textContent).toBe("Codex did not answer in 15 seconds."));
    expect(note.className).toContain("text-tone-warn");
  });

  it("says a refusal's sentence", async () => {
    hooks.refuse = { status: 409, message: "This account needs to sign in again before its usage can be read." };
    render(<UsageRefresh accountId="x" accountName="Old" />);
    fireEvent.click(screen.getByTestId("usage-refresh-x"));
    await waitFor(() =>
      expect(screen.getByTestId("usage-refresh-note-x").textContent).toBe(
        "This account needs to sign in again before its usage can be read.",
      ),
    );
  });

  it("shows the last read's time it was given", () => {
    const at = new Date(Date.now() - 4 * 60_000).toISOString();
    render(<UsageRefresh accountId="x" accountName="X" readAt={at} />);
    expect(screen.getByTestId("usage-refresh-note-x").textContent).toBe("as of 4 min ago");
  });

  it("words and decoders", () => {
    const now = Date.parse("2026-11-06T12:00:00Z");
    expect(asOfWords("2026-11-06T11:59:30Z", now)).toBe("as of just now");
    expect(asOfWords("2026-11-06T10:00:00Z", now)).toBe("as of 2 h ago");
    expect(asOfWords("", now)).toBe("");
    expect(asOfWords("nonsense", now)).toBe("");
    expect(decodeUsageRefresh({ read: { ok: "yes" } })).toBeNull();
    expect(decodeUsageRefresh({ read: { ok: true, at: "t", words: "w" } })?.read.throttled).toBe(false);
    expect(windowUsedLine({ percent: 38.4, window: "7 days" })).toBe("38% of the 7-day window used");
    expect(windowUsedLine({ percent: 12, window: "" })).toBe("12% used");
    expect(windowUsedLine(null)).toBeNull();
  });
});

describe("Chat › Code's account card", () => {
  const raw = (over: Record<string, unknown> = {}) => ({
    id: "c-work",
    name: "Work Max",
    vendor: "claude",
    vendor_label: "Claude Code",
    plan_label: "Max",
    status: "ready",
    ...over,
  });

  it("offers the button only when the daemon says the account can be read", () => {
    const a = decodeAccount(raw())!;
    const { rerender } = render(<AccountCard account={a} current={false} />);
    expect(screen.queryByTestId("usage-refresh-c-work")).toBeNull();
    rerender(<AccountCard account={decodeAccount(raw({ can_refresh: true }))!} current={false} />);
    expect(screen.getByTestId("usage-refresh-c-work")).toBeTruthy();
  });

  it("draws the fresh number the read answered", async () => {
    hooks.answer = okAnswer(
      raw({ status: "usage", can_refresh: true, usage: { percent: 64, window: "7 days", resets_at: null } }),
    );
    render(<AccountCard account={decodeAccount(raw({ can_refresh: true }))!} current={false} />);
    expect(screen.getByTestId("account-status").textContent).toBe("Ready");
    fireEvent.click(screen.getByTestId("usage-refresh-c-work"));
    await waitFor(() => expect(screen.getByTestId("account-status").textContent).toContain("64% · 7 days"));
  });
});

describe("the Connections card", () => {
  it("says the window used and offers the button only when the account can be read", async () => {
    vi.resetModules();
    const { IronProxyCard } = await import("@/components/connections/IronProxyCard");
    const snap = {
      status: { enabled: true, running: true, owned: true, url: "u", version: "1", error: null, bundled: true },
      accounts: [
        {
          id: "c-work", title: "Work Claude", provider: "anthropic", lane: "cli", order: 0, enabled: true,
          state: { status: "ready", served: 0, window: { percent: 38, window: "7 days", window_mins: 10080, resets_at: null } },
          can_refresh: true, usage_read: null,
        },
        {
          id: "x-out", title: "Old Codex", provider: "openai", lane: "cli", order: 0, enabled: true,
          state: { status: "unauthenticated", served: 0, window: { percent: 20, window: "5 hours" } },
          can_refresh: false,
        },
      ],
      discovered: [],
      providers_used_by_jarvis: { anthropic: "claude-cli", openai: "codex-cli", xai: "grok-cli" },
    };
    hooks.getAnswers["/iron-proxy"] = snap;
    hooks.getAnswers["/iron-proxy?discover=0"] = snap;
    render(<IronProxyCard />);
    expect((await screen.findByTestId("iron-proxy-window-c-work", {}, { timeout: 10_000 })).textContent).toBe(
      "38% of the 7-day window used",
    );
    expect(screen.getByTestId("usage-refresh-c-work").getAttribute("aria-label")).toBe(
      "Check usage now for Work Claude",
    );
    // The signed-out account's row is drawn — with no button.
    expect(screen.getByTestId("iron-proxy-window-x-out").textContent).toBe("20% of the 5-hour window used");
    expect(screen.queryByTestId("usage-refresh-x-out")).toBeNull();
    const before = hooks.gets.length;
    fireEvent.click(screen.getByTestId("usage-refresh-c-work"));
    await waitFor(() => expect(hooks.posts.map((p) => p.path)).toEqual(["/build/accounts/c-work/usage/refresh"]));
    // The card reads Iron-Proxy again after the read.
    await waitFor(() => expect(hooks.gets.length).toBeGreaterThan(before));
  });
});

describe("Build › Code's Usage tab", () => {
  const SESSION = decodeCodeSession({
    id: "app_1", harness: "claude-code", folder: "C:\work", title: "T", account: "p1", model: "", effort: "",
    mode: "ask", harness_session_id: "", created_at: "2026-10-30T09:00:00Z", updated_at: "2026-10-30T09:00:00Z",
    archived_at: null, last_error: "",
    capabilities: { files: false, shell: false, browser: false, extensions: false, memory: false },
  })!;
  const TOOLS = decodeCodingTools({ harnesses: [{ id: "claude-code", label: "Claude Code", installed: true,
    signed_in: true, own_sign_in: false, modes: [{ id: "ask", label: "Ask first", dangerous: false }],
    hidden_modes: [], models: [{ id: "", label: "Default" }], efforts: [], accounts: [], commands: [] }] });
  const usage = (over: Record<string, unknown>) => ({
    harness: "claude-code",
    account: { id: "p1", name: "Work", plan_label: "Max", status: "ready", windows: [], ...over },
    seen: null,
    totals: { replies: 0, input: 0, output: 0, cache_read: 0, cost_usd: null, list_price: false, cost_reported_by: "" },
    context: null,
  });

  it("offers the button for the session's account and reads the tab again after", async () => {
    hooks.getAnswers["/build/app/sessions/app_1/usage"] = usage({ can_refresh: true, usage_read_at: null });
    render(<CodePanes session={SESSION} tools={TOOLS} events={[]} running={false} />);
    fireEvent.click(screen.getByRole("tab", { name: "Usage" }));
    const btn = await screen.findByTestId("usage-refresh-p1", {}, { timeout: 10_000 });
    const reads = () => hooks.gets.filter((g) => g === "/build/app/sessions/app_1/usage").length;
    const before = reads();
    fireEvent.click(btn);
    await waitFor(() => expect(hooks.posts.map((p) => p.path)).toEqual(["/build/accounts/p1/usage/refresh"]));
    await waitFor(() => expect(reads()).toBeGreaterThan(before));
  });

  it("has no button when the account cannot be read", async () => {
    hooks.getAnswers["/build/app/sessions/app_1/usage"] = usage({ can_refresh: false });
    render(<CodePanes session={SESSION} tools={TOOLS} events={[]} running={false} />);
    fireEvent.click(screen.getByRole("tab", { name: "Usage" }));
    await screen.findByTestId("code-usage-who", {}, { timeout: 10_000 });
    expect(screen.queryByTestId("usage-refresh-p1")).toBeNull();
  });
});
