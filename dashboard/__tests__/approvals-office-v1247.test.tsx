/**
 * v1.247.0 (C3) — approvals for office work, the dashboard half.
 *
 *  - ONE CARD FOR A BATCH: an ask carrying `count` renders "× N", up to three
 *    example calls, "Allow these N" / "Deny all N" — and the one click posts
 *    one decision for the whole batch;
 *  - WAITING, NOT EXPIRING: `timeoutS === 0` says "nothing runs until you
 *    answer" and shows no countdown; a positive wait is named once;
 *  - the fields ride every hop: the SSE decoder (which whitelists), the
 *    stream hook, the escalated-run cards, the session page and the bell.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const apiState = { posts: [] as { path: string; body: unknown }[] };

vi.mock("@/lib/api", () => ({
  ApiError: class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  },
  post: async (p: string, body: unknown) => {
    apiState.posts.push({ path: p, body });
    return { ok: true };
  },
}));

import { ApprovalCard, waitingLine } from "@/components/chat/ApprovalCard";
import { sseEventFrom } from "@/lib/useChatStream";

function readSrc(rel: string): string {
  return readFileSync(path.join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");
}

beforeEach(() => {
  apiState.posts = [];
});
afterEach(() => cleanup());

const BATCH = {
  id: "apr_batch",
  callId: "c1",
  tool: "rename_real_file",
  args: { source: "1.pdf" },
  count: 8,
  examples: [{ source: "1.pdf" }, { source: "2.pdf" }, { source: "3.pdf" }],
  timeoutS: 0,
};

describe("one card for a batch", () => {
  it("names the count, shows three examples and how many more", () => {
    render(<ApprovalCard approval={BATCH} />);
    expect(screen.getByTestId("approval-count").textContent).toBe("× 8");
    expect(screen.getByText(/source: 2\.pdf/)).toBeInTheDocument();
    expect(screen.getByText("…and 5 more like these")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Allow these 8" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Deny all 8" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Allow once/ })).toBeNull();
  });

  it("one click posts ONE decision for the whole batch", async () => {
    render(<ApprovalCard approval={BATCH} />);
    fireEvent.click(screen.getByRole("button", { name: "Allow these 8" }));
    await waitFor(() =>
      expect(apiState.posts).toEqual([
        { path: "/chat/approvals/apr_batch", body: { decision: "once" } },
      ]),
    );
  });

  it("a single ask is unchanged: Allow once, your call, no count", () => {
    render(
      <ApprovalCard
        approval={{ id: "apr_1", callId: "c1", tool: "shell", args: { command: "git status" } }}
      />,
    );
    expect(screen.getByRole("button", { name: "Allow once" })).toBeInTheDocument();
    expect(screen.getByText(/your call\./)).toBeInTheDocument();
    expect(screen.queryByTestId("approval-count")).toBeNull();
  });
});

describe("waiting, not expiring", () => {
  it("0 = nothing runs until you answer; a positive wait is named once", () => {
    expect(waitingLine(0)).toBe("Waiting for you — nothing runs until you answer.");
    expect(waitingLine(300)).toBe(
      "Waiting for you — if nobody answers within 5 min, it is not run.",
    );
    expect(waitingLine(undefined)).toBe("Waiting for you.");
  });

  it("the card renders the line from the ask's own wait", () => {
    render(<ApprovalCard approval={BATCH} />);
    expect(screen.getByTestId("approval-waiting").textContent).toBe(
      "Waiting for you — nothing runs until you answer.",
    );
  });
});

describe("the fields ride every hop", () => {
  it("the SSE decoder keeps count + examples (it whitelists fields)", () => {
    const ev = sseEventFrom("approval", {
      id: "a",
      call_id: "c",
      tool: "shell",
      timeout_s: 0,
      count: 3,
      examples: [{ command: "echo 1" }, "junk", { command: "echo 2" }],
    });
    expect(ev).toMatchObject({ type: "approval", count: 3, timeout_s: 0 });
    expect((ev as { examples?: unknown[] }).examples).toEqual([
      { command: "echo 1" },
      { command: "echo 2" },
    ]);
    const single = sseEventFrom("approval", { id: "a", call_id: "c", tool: "shell", count: 1 });
    expect((single as { count?: number }).count).toBeUndefined();
  });

  it("the stream hook hands count + examples to the card", () => {
    const src = readSrc("lib/useChatStream.ts");
    expect(src).toContain("count: ev.count,");
    expect(src).toContain("examples: ev.examples,");
  });

  it("an escalated run's card reads count and wait off the event and the listing", () => {
    const src = readSrc("app/chat/page.tsx");
    expect(src).toContain('...(typeof p.count === "number" && p.count > 1 ? { count: p.count } : {}),');
    expect(src).toContain('...(typeof p.timeout_s === "number" ? { timeoutS: p.timeout_s } : {}),');
    expect(src).toContain("count: ask.count,");
    expect(src).toContain("timeoutS: ask.timeoutS,");
  });

  it("the session page and the bell carry the count", () => {
    expect(readSrc("app/sessions/[id]/page.tsx")).toContain(
      "count: (session.waiting_on as { count?: number }).count,",
    );
    const bell = readSrc("components/NotificationBell.tsx");
    expect(bell).toContain('data-testid="bell-approval-count"');
    expect(bell).toContain('"the run is waiting for you."');
  });
});
