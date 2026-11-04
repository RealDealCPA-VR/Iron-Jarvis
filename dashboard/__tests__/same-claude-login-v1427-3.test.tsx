/**
 * v1.427.3 (a USER BUG) — Claude accounts signed in to ONE Claude account.
 *
 * The user's three Claude accounts in Iron-Proxy all held the same Claude
 * login, so Continue on another account moved onto the same limit twice and
 * then refused. Pinned here:
 *  - the Iron-Proxy card names the accounts that share one login, under the
 *    Claude group only, with how to fix it; an older daemon draws nothing;
 *  - after a refusal with nothing free, the limit card offers "Try <name>
 *    anyway" for the accounts the daemon offers (`choices`), and a press
 *    names that account; never for Grok, never without choices.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const hooks = vi.hoisted(() => ({ snap: null as unknown }));

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
    get: (p: string) =>
      p === "/iron-proxy" || p === "/iron-proxy?discover=0" ? Promise.resolve(hooks.snap) : Promise.resolve({}),
    post: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/connections",
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/components/BrandGlyph", () => ({ ProviderMark: () => null }));

import { IronProxyCard } from "@/components/connections/IronProxyCard";
import { CodeConversation } from "@/components/buildCode/CodeConversation";
import { SHARED_LOGIN_FIX, sharedLoginLines } from "@/lib/ironProxy";
import { decodeCodeEvent, decodeTryChoices, foldConversation, type CodeEvent } from "@/lib/buildApp";

function acct(over: Record<string, unknown>) {
  return {
    id: "a",
    title: "Work Claude",
    provider: "anthropic",
    lane: "cli",
    order: 0,
    enabled: true,
    state: { status: "ready", served: 0 },
    ...over,
  };
}

const ACCOUNTS = [
  acct({ id: "tsi", title: "TSI" }),
  acct({ id: "vr2", title: "VR2", order: 1 }),
  acct({ id: "own", title: "Claude (existing login)", order: 2 }),
  acct({ id: "cx", title: "Work Codex", provider: "openai" }),
];

function snap(shared: unknown) {
  return {
    status: { enabled: true, running: true, owned: true, url: "", version: "0.6.0", error: null, bundled: true },
    accounts: ACCOUNTS,
    discovered: [],
    providers_used_by_jarvis: { anthropic: "claude-cli", openai: "codex-cli", xai: "grok-cli" },
    account_groups: {},
    ...(shared === undefined ? {} : { shared_logins: shared }),
  };
}

async function mount(shared: unknown) {
  hooks.snap = snap(shared);
  render(<IronProxyCard />);
  await waitFor(() => expect(document.getElementById("iron-proxy-account-tsi")).not.toBeNull());
}

beforeEach(() => {
  hooks.snap = null;
});
afterEach(() => cleanup());

describe("the Iron-Proxy card says which accounts share one Claude login", () => {
  it("names them under the Claude group, with how to fix it", async () => {
    await mount([["tsi", "vr2", "own"]]);
    const box = await screen.findByTestId("iron-proxy-shared-login");
    expect(box.textContent).toContain(
      '"TSI", "VR2" and "Claude (existing login)" are signed in to the same Claude account, so they share one usage limit.',
    );
    expect(box.textContent).toContain(SHARED_LOGIN_FIX);
    expect(within(screen.getByTestId("iron-proxy-group-anthropic")).getByTestId("iron-proxy-shared-login")).toBe(box);
    expect(within(screen.getByTestId("iron-proxy-group-openai")).queryByTestId("iron-proxy-shared-login")).toBeNull();
  });

  it("an older daemon, or none shared, draws nothing", async () => {
    await mount(undefined);
    expect(screen.queryByTestId("iron-proxy-shared-login")).toBeNull();
    cleanup();
    await mount([]);
    expect(screen.queryByTestId("iron-proxy-shared-login")).toBeNull();
  });

  it("reads only real sets of two or more named accounts", () => {
    expect(sharedLoginLines(null)).toEqual([]);
    expect(sharedLoginLines({ accounts: ACCOUNTS as never, shared_logins: "x" as never })).toEqual([]);
    expect(sharedLoginLines({ accounts: ACCOUNTS as never, shared_logins: [["tsi", "gone"]] })).toEqual([]);
    expect(sharedLoginLines({ accounts: ACCOUNTS as never, shared_logins: [["tsi", "vr2"]] })).toEqual([
      '"TSI" and "VR2" are signed in to the same Claude account, so they share one usage limit.',
    ]);
  });
});

const LIMIT_REPLY = [
  { type: "user", at: "2026-11-04T00:49:32Z", text: "continue", turn: "t1" },
  { type: "error", at: "2026-11-04T00:49:33Z", title: "Claude Code hit this account's limit",
    fix: "Press Continue on another account, or wait until it resets.", technical: "", fix_kind: "account_limit",
    account: "vr2", turn: "t1" },
  { type: "done", at: "2026-11-04T00:49:33Z", stop: "failed", turn: "t1" },
];

function items() {
  const evs = LIMIT_REPLY.map((e) => decodeCodeEvent(e)).filter((e): e is CodeEvent => !!e);
  return foldConversation(evs).items;
}

describe("Try an account anyway", () => {
  it("reads the daemon's choices only from a none_free refusal", () => {
    expect(decodeTryChoices(null)).toEqual([]);
    expect(decodeTryChoices({ detail: "x" })).toEqual([]);
    expect(decodeTryChoices({ code: "same_login", choices: [{ id: "p3", title: "Other" }] })).toEqual([]);
    expect(
      decodeTryChoices({
        code: "none_free",
        choices: [{ id: "p3", title: "Other", why: "at its usage limit until 10:30 PM" }, { title: "no id" }, "junk"],
      }),
    ).toEqual([{ id: "p3", title: "Other", why: "at its usage limit until 10:30 PM" }]);
  });

  it("offers each choice on the limit card, and a press names that account", () => {
    const onTry = vi.fn();
    render(
      <CodeConversation
        items={items()}
        folder="C:\\work"
        tool="claude-code"
        onContinueOn={() => {}}
        tryChoices={[{ id: "p3", title: "Other", why: "at its usage limit until 10:30 PM" }]}
        onTryAccount={onTry}
      />,
    );
    const btn = screen.getByTestId("code-try-account-p3");
    expect(btn.textContent).toBe("Try Other anyway");
    expect(btn.getAttribute("title")).toBe("at its usage limit until 10:30 PM");
    fireEvent.click(btn);
    expect(onTry).toHaveBeenCalledWith("p3");
    expect(screen.getByTestId("code-continue-on")).toBeInTheDocument();
  });

  it("no choices, or Grok, offers nothing more", () => {
    const { unmount } = render(
      <CodeConversation items={items()} folder="C:\\work" tool="claude-code" onContinueOn={() => {}} onTryAccount={() => {}} />,
    );
    expect(screen.queryByTestId("code-try-accounts")).toBeNull();
    unmount();
    render(
      <CodeConversation
        items={items()}
        folder="C:\\work"
        tool="grok"
        onContinueOn={() => {}}
        tryChoices={[{ id: "p3", title: "Other", why: "" }]}
        onTryAccount={() => {}}
      />,
    );
    expect(screen.queryByTestId("code-try-accounts")).toBeNull();
  });
});
