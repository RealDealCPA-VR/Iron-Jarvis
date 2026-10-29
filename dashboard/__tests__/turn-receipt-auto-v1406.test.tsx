/**
 * v1.406.0 part 2 — the calls Accept edits / Auto let through with NO card
 * are said on the reply's receipt (the daemon's `route.auto_approved: {count,
 * calls: [{tool, rule, why}]}`, both chat lanes, LAST on the route, only when
 * it happened): "N actions approved automatically" on the collapsed line, and
 * each call with WHY in the expanded receipt. Through the REAL TurnReceipt
 * and the REAL stream decode.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { TurnReceipt } from "@/components/chat/TurnReceipt";
import { decodeSSE } from "@/lib/useChatStream";
import { autoApprovedLine, autoApprovedRow, decodeAutoApproved } from "@/lib/turnRecovery";

afterEach(() => cleanup());

const ROUTE = { requested: "", provider: "anthropic", model: "claude-sonnet-4-5", reason: "default" };
const AUTO = {
  count: 2,
  calls: [
    { tool: "shell", rule: "test_command", why: "it runs the project's tests" },
    { tool: "shell", rule: "model", why: "a quick check said it is routine for what you asked" },
  ],
};

describe("the receipt says what was approved automatically", () => {
  it("says the count on the collapsed line, quiet", () => {
    render(<TurnReceipt inline route={{ ...ROUTE, auto_approved: AUTO }} />);
    const part = screen.getByTestId("turn-auto-approved");
    expect(part.textContent).toBe("2 actions approved automatically");
    expect(part.className).toContain("text-zinc-500");
  });

  it("lists each call and why when opened", () => {
    render(<TurnReceipt inline route={{ ...ROUTE, auto_approved: AUTO }} />);
    fireEvent.click(screen.getByTestId("turn-receipt"));
    const rows = screen.getAllByTestId("turn-auto-approved-row");
    expect(rows.map((r) => r.getAttribute("data-rule"))).toEqual(["test_command", "model"]);
    expect(rows[0].textContent).toMatch(/^Approved automatically: .+ \(it runs the project's tests\)$/);
    expect(rows[1].textContent).toContain("a quick check said it is routine");
    expect(rows[0].getAttribute("title")).toBe("shell");
  });

  it("one action reads singular; nothing is said when nothing passed or the shape is wrong", () => {
    expect(autoApprovedLine({ count: 1, calls: [] })).toBe("1 action approved automatically");
    for (const raw of [undefined, null, {}, { count: 0 }, { count: "2" }, "yes"]) {
      cleanup();
      render(<TurnReceipt inline route={{ ...ROUTE, auto_approved: raw as never }} />);
      expect(screen.queryByTestId("turn-auto-approved")).toBeNull();
    }
    expect(decodeAutoApproved({ count: 2.6, calls: [{ tool: "" }, { tool: "x", rule: 3 }] })).toEqual({
      count: 2,
      calls: [{ tool: "x", rule: "", why: "" }],
    });
    expect(autoApprovedRow({ tool: "x", rule: "", why: "" })).not.toMatch(/\(\)/);
  });

  it("the daemon's done frame carries it through the stream decode", () => {
    const ev = decodeSSE("done", JSON.stringify({ reply: "ok", route: { ...ROUTE, auto_approved: AUTO } })) as {
      type: string;
      route?: { auto_approved?: unknown };
    } | null;
    expect(ev?.type).toBe("done");
    expect(decodeAutoApproved(ev?.route?.auto_approved)?.count).toBe(2);
  });
});
