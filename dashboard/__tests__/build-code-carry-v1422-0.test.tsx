/**
 * v1.422.0 (Wave 2 "Never lose work", B3 + B5) — Build › Code carrying on by
 * itself after an interruption, as the page draws it:
 *
 *   - a message Iron Jarvis sent BY ITSELF to carry the reply on (`user`
 *     with `carry`) is one quiet line, never the user's bubble;
 *   - a reply a restart cut off that was NOT carried on by itself (the
 *     session read's `cut`) says why and offers **Continue** — in place of
 *     Retry, which would send the old message again;
 *   - an account's limit that WAITS by itself (`account_wait`) says until
 *     when, with **Cancel**; Continue on another account is offered only for
 *     the tools whose conversation can move (Claude Code, Codex), never Grok;
 *   - the bell opens Build › Code for a Build › Code session's news.
 *
 * Drawn through the REAL CodeConversation and the REAL bell row mapping.
 */

import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/lib/api", () => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: MockApiError,
    get: vi.fn(async () => ({})),
    post: vi.fn(async () => ({})),
    put: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
    API_BASE: "",
    ijToken: () => "",
    sseUrl: (p: string) => p,
  };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: vi.fn(),
  }),
}));
vi.mock("@/lib/useApi", () => ({
  useApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
  usePolledApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/useReviews", () => ({ useReviews: () => ({ reviews: {}, loading: false, reload: () => {} }) }));
vi.mock("@/lib/useDocumentVisible", () => ({ useDocumentVisible: () => true }));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: null, refresh: () => {}, epoch: 0 }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/terminals",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import { CodeConversation } from "@/components/buildCode/CodeConversation";
import { toActivity } from "@/components/NotificationBell";
import { buildCodeHref, decodeCodeEvent, foldConversation, type CodeEvent } from "@/lib/buildApp";
import {
  canMoveAccounts,
  carryLineWords,
  cutWords,
  decodeCarry,
  decodeCarryMark,
  waitWords,
} from "@/lib/buildCodeCarry";
import type { IJEvent } from "@/lib/types";

afterEach(cleanup);

function events(raw: Record<string, unknown>[]): CodeEvent[] {
  return raw.map((e) => decodeCodeEvent(e)).filter((e): e is CodeEvent => !!e);
}

const CUT_REPLY = [
  { type: "user", at: "2026-11-01T10:00:00Z", text: "build the parser", turn: "t1" },
  { type: "text_done", at: "2026-11-01T10:00:05Z", item: "a1", text: "Reading the files first.", turn: "t1" },
  { type: "error", at: "2026-11-01T10:01:00Z", title: "Iron Jarvis stopped while this was working",
    fix: "Send the message again — the conversation is kept.", technical: "", turn: "t1" },
  { type: "done", at: "2026-11-01T10:01:00Z", stop: "interrupted", turn: "t1" },
];

const LIMIT_REPLY = [
  { type: "user", at: "2026-11-01T10:00:00Z", text: "carry on with the parser", turn: "t1" },
  { type: "error", at: "2026-11-01T10:00:02Z", title: "Claude Code hit this account's limit",
    fix: "Press Continue on another account, or wait until it resets.", technical: "", fix_kind: "account_limit",
    account: "p1", turn: "t1" },
  { type: "done", at: "2026-11-01T10:00:02Z", stop: "completed", turn: "t1" },
];

describe("the shapes the daemon sends", () => {
  it("decodes the session read's cut and wait, and nothing from an older daemon", () => {
    expect(decodeCarry({})).toEqual({ cut: null, wait: null, limitNote: "" });
    const got = decodeCarry({
      cut: { turn: "t1", resumable: true, why_not: "a newer message was sent", waiting: false },
      account_wait: { account: "p1", account_name: "Work", resets_at: "2026-11-01T15:30:00Z", group: "Work" },
      account_limit_note: "it now waits for you",
    });
    expect(got.cut).toEqual({ turn: "t1", resumable: true, whyNot: "a newer message was sent", waiting: false });
    expect(got.wait).toEqual({ account: "p1", accountName: "Work", resetsAt: "2026-11-01T15:30:00Z", group: "Work" });
    expect(got.limitNote).toBe("it now waits for you");
  });

  it("a carry mark is one of the three, else the user's own message", () => {
    expect(decodeCarryMark({ why: "restart", from: "t1" })).toMatchObject({ why: "restart", pressed: false });
    expect(decodeCarryMark({ why: "nope" })).toBeNull();
    expect(decodeCarryMark("restart")).toBeNull();
    const ev = decodeCodeEvent({ type: "user", at: "x", text: "hi", carry: { why: "reset", account_name: "Work" } });
    expect(ev && ev.type === "user" && ev.carry?.why).toBe("reset");
    const plain = decodeCodeEvent({ type: "user", at: "x", text: "hi" });
    expect(plain && plain.type === "user" && "carry" in plain).toBe(false);
  });

  it("the words say what happened, in plain words", () => {
    expect(carryLineWords({ why: "restart", fromAccount: "", toAccount: "", accountName: "", group: "", pressed: false }))
      .toMatch(/after Iron Jarvis restarted/);
    expect(carryLineWords({ why: "move", fromAccount: "Work", toAccount: "Work 2", accountName: "", group: "Work", pressed: false }))
      .toBe("Carried on by itself with Work 2 — Work reached its limit.");
    expect(carryLineWords({ why: "reset", fromAccount: "", toAccount: "", accountName: "Work", group: "", pressed: false }))
      .toBe("Work's limit reset, so it carried on by itself.");
    const now = new Date("2026-11-01T12:00:00Z");
    expect(waitWords({ account: "p1", accountName: "Work", resetsAt: "2026-11-01T15:30:00Z", group: "" }, now))
      .toMatch(/^Waiting for Work to reset at .+ — it carries on by itself then\.$/);
    expect(waitWords({ account: "p1", accountName: "Work", resetsAt: null, group: "Team" }, now))
      .toMatch(/another account in your group "Team"/);
    expect(cutWords({ turn: "t1", resumable: false, whyNot: "", waiting: false })).toMatch(/send your message again/);
    expect(canMoveAccounts("claude-code") && canMoveAccounts("codex")).toBe(true);
    expect(canMoveAccounts("grok") || canMoveAccounts("pi") || canMoveAccounts("opencode")).toBe(false);
  });
});

describe("the conversation", () => {
  it("a carry-on message is a quiet line, never the user's bubble", () => {
    const { items } = foldConversation(events([
      ...CUT_REPLY,
      { type: "user", at: "2026-11-01T10:05:00Z", text: "Iron Jarvis restarted while you were working…",
        carry: { why: "restart", from: "t1" }, turn: "t2" },
    ]));
    render(<CodeConversation items={items} folder="C:\\work" tool="claude-code" />);
    expect(screen.getByTestId("code-carry")).toHaveTextContent(/after Iron Jarvis restarted/);
    expect(screen.getByTestId("code-carry").getAttribute("data-why")).toBe("restart");
    expect(screen.queryByText(/Iron Jarvis restarted while you were working/)).toBeNull();
    expect(screen.getAllByTestId("code-user")).toHaveLength(1);
    // The cut reply's error is not the newest thing any more: no press.
    expect(screen.queryByTestId("code-continue-cut")).toBeNull();
    expect(screen.queryByTestId("code-retry")).toBeNull();
  });

  it("a reply a restart cut off offers Continue (not Retry) and says why it did not carry on itself", () => {
    const { items } = foldConversation(events(CUT_REPLY));
    const onContinueCut = vi.fn();
    const onRetry = vi.fn();
    render(
      <CodeConversation
        items={items}
        folder="C:\\work"
        tool="claude-code"
        onRetry={onRetry}
        cut={{ turn: "t1", resumable: true, whyNot: "a newer message was sent", waiting: false }}
        onContinueCut={onContinueCut}
      />,
    );
    expect(screen.getByTestId("code-cut-why")).toHaveTextContent("It did not carry on by itself because a newer message was sent.");
    expect(screen.queryByTestId("code-retry")).toBeNull();
    fireEvent.click(screen.getByTestId("code-continue-cut"));
    expect(onContinueCut).toHaveBeenCalledTimes(1);
    expect(onRetry).not.toHaveBeenCalled();
  });

  it("a cut the tool never saved offers Retry (there is nothing to carry on)", () => {
    const { items } = foldConversation(events(CUT_REPLY));
    render(
      <CodeConversation
        items={items}
        folder="C:\\work"
        tool="claude-code"
        onRetry={() => {}}
        cut={{ turn: "t1", resumable: false, whyNot: "", waiting: false }}
        onContinueCut={() => {}}
      />,
    );
    expect(screen.queryByTestId("code-continue-cut")).toBeNull();
    expect(screen.getByTestId("code-retry")).toBeInTheDocument();
  });

  it("with no cut the press is Retry, exactly as before", () => {
    const { items } = foldConversation(events(CUT_REPLY));
    render(<CodeConversation items={items} folder="C:\\work" tool="claude-code" onRetry={() => {}} />);
    expect(screen.getByTestId("code-retry")).toBeInTheDocument();
    expect(screen.queryByTestId("code-cut-why")).toBeNull();
  });

  it("a limit that waits by itself says until when, and Cancel ends it", () => {
    const { items } = foldConversation(events(LIMIT_REPLY));
    const onCancelWait = vi.fn();
    render(
      <CodeConversation
        items={items}
        folder="C:\\work"
        tool="claude-code"
        onContinueOn={() => {}}
        wait={{ account: "p1", accountName: "Work", resetsAt: "2026-11-01T15:30:00Z", group: "" }}
        onCancelWait={onCancelWait}
      />,
    );
    expect(screen.getByTestId("code-limit-waiting")).toHaveTextContent(/Waiting for Work to reset at .+it carries on by itself then/);
    fireEvent.click(screen.getByTestId("code-wait-cancel"));
    expect(onCancelWait).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("code-continue-on")).toBeInTheDocument();
  });

  it("no wait: the card has no waiting line; Grok never offers Continue on another account", () => {
    const { items } = foldConversation(events(LIMIT_REPLY));
    const { unmount } = render(
      <CodeConversation items={items} folder="C:\\work" tool="codex" onContinueOn={() => {}} limitNote="it now waits for you" />,
    );
    expect(screen.queryByTestId("code-limit-waiting")).toBeNull();
    expect(screen.getByTestId("code-limit-note")).toHaveTextContent("it now waits for you");
    expect(screen.getByTestId("code-continue-on")).toBeInTheDocument();
    unmount();
    render(<CodeConversation items={items} folder="C:\\work" tool="grok" onContinueOn={() => {}} />);
    expect(screen.queryByTestId("code-continue-on")).toBeNull();
  });
});

describe("the bell", () => {
  const ev = (type: string, payload: Record<string, unknown>, sid: string | null = null): IJEvent => ({
    id: "e1",
    type,
    session_id: sid,
    ts: "2026-11-01T12:00:00Z",
    payload,
  });

  it("a Build › Code session's news opens Build › Code on that session", () => {
    const resumed = toActivity(ev("build.resumed", { thread_id: "app_1", title: "Fix the parser",
      reason: "restart", surface: "build_app" }, "build_app:app_1"))!;
    expect(resumed.href).toBe(buildCodeHref("app_1"));
    expect(resumed.title).toMatch(/carried on after Iron Jarvis restarted/);
    const limit = toActivity(ev("build.account_limit", { thread_id: "app_1", title: "Fix the parser",
      account_name: "Work", kind: "usage", waiting: true, surface: "build_app" }, "build_app:app_1"))!;
    expect(limit.href).toBe(buildCodeHref("app_1"));
    // A Chat › Code session's news still opens the chat page's Code view.
    const chat = toActivity(ev("build.resumed", { thread_id: "bt_1", title: "x", reason: "restart" }, "build:bt_1"))!;
    expect(chat.href).not.toBe(buildCodeHref("bt_1"));
  });
});
