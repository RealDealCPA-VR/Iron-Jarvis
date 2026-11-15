/**
 * v1.498.0: Build › Code sends a reply back by itself (hosted/check_again.py).
 * The hidden follow-up is a `user` event whose `carry.why` is "check": it is
 * drawn as Iron Jarvis's own quiet line with the daemon's words, never as
 * the user's bubble; the verdict is an ordinary notice.
 */
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

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
import { decodeCodeEvent, foldConversation, type CodeEvent } from "@/lib/buildApp";
import { carryLineWords, decodeCarryMark } from "@/lib/buildCodeCarry";

afterEach(cleanup);

function events(raw: Record<string, unknown>[]): CodeEvent[] {
  return raw.map((e) => decodeCodeEvent(e)).filter((e): e is CodeEvent => !!e);
}

const LINE = "Iron Jarvis sent it back: the reply said report.xlsx was saved, and it is not in the folder.";

describe("a check follow-up", () => {
  it("decodes the check mark and its words", () => {
    const m = decodeCarryMark({ why: "check", from: "t1", origin: "t1", round: 1, note: LINE });
    expect(m?.why).toBe("check");
    expect(m && carryLineWords(m)).toBe(LINE);
    const bare = decodeCarryMark({ why: "check" });
    expect(bare && carryLineWords(bare)).toMatch(/sent it back/);
    // A note on another kind of carry-on is never read.
    expect(decodeCarryMark({ why: "restart", note: "planted" })?.note).toBeUndefined();
  });

  it("is a quiet line, never the user's bubble, and the verdict is a notice", () => {
    const { items } = foldConversation(events([
      { type: "user", at: "2026-11-14T10:00:00Z", text: "make the report", turn: "t1" },
      { type: "text_done", at: "2026-11-14T10:00:05Z", item: "a1", text: "I saved report.xlsx.", turn: "t1" },
      { type: "done", at: "2026-11-14T10:00:06Z", stop: "completed", turn: "t1" },
      { type: "user", at: "2026-11-14T10:00:07Z", text: "FROM IRON JARVIS, NOT FROM THE USER. hidden words",
        carry: { why: "check", from: "t1", origin: "t1", round: 1, note: LINE }, turn: "t2" },
      { type: "text_done", at: "2026-11-14T10:00:09Z", item: "a2", text: "I saved report.xlsx now.", turn: "t2" },
      { type: "done", at: "2026-11-14T10:00:10Z", stop: "completed", turn: "t2" },
      { type: "notice", at: "2026-11-14T10:00:11Z",
        text: "Checked by Iron Jarvis: this is not finished. It was sent back once and stopped there: the last try changed nothing. Tell it to keep going and it will carry on." },
    ]));
    render(<CodeConversation items={items} folder="C:\work" tool="claude-code" />);
    expect(screen.getByTestId("code-carry")).toHaveTextContent(LINE);
    expect(screen.getByTestId("code-carry").getAttribute("data-why")).toBe("check");
    expect(screen.queryByText(/hidden words/)).toBeNull();
    expect(screen.getAllByTestId("code-user")).toHaveLength(1);
    expect(screen.getByText(/Checked by Iron Jarvis: this is not finished/)).toBeTruthy();
  });
});
