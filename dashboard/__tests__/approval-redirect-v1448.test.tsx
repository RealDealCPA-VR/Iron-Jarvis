/**
 * v1.448.0 (Wave A, A3) — the chat's approval card gets "No, tell it what to
 * do instead", and the dock says the hidden box comes back.
 *
 *  - The COMPACT card (one with `why`, every live card since v1.417.0) offers
 *    the redirect when a page is there to send the words (`onRedirect`); it
 *    opens a field whose Send (or Enter) DENIES the call and THEN hands the
 *    words to the page — never before the Deny has landed, and not at all
 *    when the Deny failed.
 *  - The card WITHOUT `why` (an older daemon, a listing row) offers nothing
 *    new: its DOM is pinned byte for byte by approval-card-why-v1417.
 *  - DockAsk passes the redirect through and draws ONE line: "The message
 *    box comes back when you answer." Enter's rules are untouched: a safety
 *    once-only card is still never Enter-allowed.
 *  - The page's handler sends the words as a STEER NOTE to the running turn
 *    (read right after the refusal), else as the next message.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => ({
  posts: [] as { path: string; body: unknown }[],
  fail: false,
  gate: null as null | (() => void),
}));

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return {
    ...real,
    post: async (p: string, body: unknown) => {
      if (H.gate) await new Promise<void>((r) => (H.gate = r));
      H.posts.push({ path: p, body });
      if (H.fail) throw new Error("the daemon said no");
      return { ok: true };
    },
  };
});

import { ApprovalCard, type ApprovalAsk } from "@/components/chat/ApprovalCard";
import { DockAsk } from "@/components/chat/DockAsk";
import { REDIRECT_WORDS as BUILD_REDIRECT_WORDS } from "@/components/buildCode/CodeAskCard";
import { REDIRECT_WORDS } from "@/lib/approvalWords";
import { BOX_COMES_BACK_NOTE, ENTER_BLOCKED_NOTE, enterAllows, type DockAskItem } from "@/lib/dockAsk";

const read = (rel: string) => readFileSync(path.join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");

beforeEach(() => {
  H.posts = [];
  H.fail = false;
  H.gate = null;
});
afterEach(() => cleanup());

const WRITE: ApprovalAsk = {
  id: "a1",
  callId: "c1",
  tool: "write_file",
  args: { path: "notes/plan.md", content: "# Plan" },
  timeoutS: 0,
  reversible: true,
  why: "Saving a file is not in this chat's grants.",
};

describe("the redirect answer on the chat's approval card", () => {
  it("the compact card offers it, Build › Code's own words, only with a page to send to", () => {
    const { unmount } = render(<ApprovalCard approval={WRITE} scope="chat" onRedirect={() => {}} />);
    const btn = screen.getByTestId("approval-redirect");
    expect(btn.textContent).toBe(REDIRECT_WORDS);
    expect(REDIRECT_WORDS).toBe(BUILD_REDIRECT_WORDS); // one definition
    unmount();
    // Nobody to send the words: no button (nothing could do what it says).
    render(<ApprovalCard approval={WRITE} scope="chat" />);
    expect(screen.queryByTestId("approval-redirect")).toBeNull();
  });

  it("the card WITHOUT why offers nothing new (its DOM is the v1.417.0 fixture's)", () => {
    const { why: _why, ...old } = WRITE;
    render(<ApprovalCard approval={old} scope="chat" onRedirect={() => {}} />);
    expect(screen.queryByTestId("approval-redirect")).toBeNull();
    expect(screen.queryByTestId("approval-redirect-input")).toBeNull();
  });

  it("Send denies FIRST, then hands the words to the page", async () => {
    const order: string[] = [];
    const onRedirect = vi.fn(async (words: string) => {
      order.push(`redirect:${words}:${H.posts.length}`);
    });
    render(<ApprovalCard approval={WRITE} scope="chat" onRedirect={onRedirect} />);
    fireEvent.click(screen.getByTestId("approval-redirect"));
    const field = screen.getByTestId("approval-redirect-input") as HTMLInputElement;
    expect(field.getAttribute("aria-label")).toBe("What it should do instead");
    expect((screen.getByTestId("approval-redirect-send") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(field, { target: { value: "  write it to drafts/plan.md instead  " } });
    fireEvent.click(screen.getByTestId("approval-redirect-send"));
    await waitFor(() => expect(onRedirect).toHaveBeenCalledTimes(1));
    expect(H.posts).toEqual([{ path: "/chat/approvals/a1", body: { decision: "deny" } }]);
    // The Deny had landed (one POST recorded) when the words were handed over.
    expect(order).toEqual(["redirect:write it to drafts/plan.md instead:1"]);
  });

  it("Enter in the field sends; Escape closes it; the words wait while the Deny is in flight", async () => {
    const onRedirect = vi.fn();
    render(<ApprovalCard approval={WRITE} scope="chat" onRedirect={onRedirect} />);
    fireEvent.click(screen.getByTestId("approval-redirect"));
    let field = screen.getByTestId("approval-redirect-input") as HTMLInputElement;
    fireEvent.keyDown(field, { key: "Escape" });
    expect(screen.queryByTestId("approval-redirect-input")).toBeNull();
    expect(screen.getByTestId("approval-redirect")).toBeTruthy();
    fireEvent.click(screen.getByTestId("approval-redirect"));
    field = screen.getByTestId("approval-redirect-input") as HTMLInputElement;
    fireEvent.change(field, { target: { value: "use the other folder" } });
    H.gate = () => {};
    fireEvent.keyDown(field, { key: "Enter" });
    // Nothing handed over while the Deny is still in the air.
    await act(async () => {});
    expect(onRedirect).not.toHaveBeenCalled();
    expect(H.posts).toEqual([]);
    const release = H.gate;
    H.gate = null;
    await act(async () => {
      release?.();
    });
    await waitFor(() => expect(onRedirect).toHaveBeenCalledWith("use the other folder"));
    expect(H.posts.map((p) => p.body)).toEqual([{ decision: "deny" }]);
  });

  it("a Deny that failed keeps the words and hands nothing over", async () => {
    H.fail = true;
    const onRedirect = vi.fn();
    render(<ApprovalCard approval={WRITE} scope="chat" onRedirect={onRedirect} />);
    fireEvent.click(screen.getByTestId("approval-redirect"));
    const field = screen.getByTestId("approval-redirect-input") as HTMLInputElement;
    fireEvent.change(field, { target: { value: "try the other way" } });
    fireEvent.click(screen.getByTestId("approval-redirect-send"));
    await screen.findByText("the daemon said no");
    expect(onRedirect).not.toHaveBeenCalled();
    expect((screen.getByTestId("approval-redirect-input") as HTMLInputElement).value).toBe("try the other way");
    expect((screen.getByTestId("approval-redirect-send") as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("the dock", () => {
  const item = (approval: ApprovalAsk): DockAskItem => ({ kind: "approval", id: approval.id, approval });
  const dock = (approval: ApprovalAsk, onRedirect?: (w: string) => void) =>
    render(
      <DockAsk
        asks={[item(approval)]}
        onAnswerElicitation={async () => ({ ok: true }) as never}
        onDecideSampling={async () => true}
        onRedirect={onRedirect}
        armDelayMs={0}
      />,
    );

  it("says the message box comes back, and passes the redirect to the card", () => {
    dock(WRITE, () => {});
    expect(screen.getByTestId("dock-ask-box-note").textContent).toBe(BOX_COMES_BACK_NOTE);
    expect(BOX_COMES_BACK_NOTE).toBe("The message box comes back when you answer.");
    expect(screen.getByTestId("approval-redirect")).toBeTruthy();
  });

  it("Enter still never allows a safety once-only card; Esc still declines", async () => {
    const safety: ApprovalAsk = {
      ...WRITE,
      id: "s1",
      tool: "shell",
      args: { command: "git push" },
      onceOnly: true,
      onceReason: "git",
      why: "On this session's own branch a push asks you every time.",
    };
    expect(enterAllows(item(safety))).toBe(false);
    dock(safety, () => {});
    // The dock took the caret on mount; a key from the page body (nothing
    // focused) is the shape the v1.417.0 pin drives.
    (document.activeElement as HTMLElement | null)?.blur?.();
    fireEvent.keyDown(document.body, { key: "Enter" });
    expect(screen.getByTestId("dock-ask-enter-blocked").textContent).toBe(ENTER_BLOCKED_NOTE);
    expect(H.posts).toEqual([]);
    // The redirect is offered beside the buttons, and takes nothing from Esc.
    expect(screen.getByTestId("approval-redirect")).toBeTruthy();
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(H.posts.map((p) => p.body)).toEqual([{ decision: "deny" }]));
    (document.activeElement as HTMLElement | null)?.blur?.();
  });

  it("Enter typed INTO the redirect field never reaches the dock's Allow", async () => {
    const onRedirect = vi.fn();
    dock(WRITE, onRedirect);
    fireEvent.click(screen.getByTestId("approval-redirect"));
    const field = screen.getByTestId("approval-redirect-input") as HTMLInputElement;
    fireEvent.change(field, { target: { value: "do it elsewhere" } });
    fireEvent.keyDown(field, { key: "Enter" });
    await waitFor(() => expect(onRedirect).toHaveBeenCalledWith("do it elsewhere"));
    // ONE post, the deny — never an "once" from the dock's own Enter.
    expect(H.posts.map((p) => p.body)).toEqual([{ decision: "deny" }]);
  });
});

describe("the chat page sends the words", () => {
  it("hands DockAsk a handler that steers the running turn, else sends a message", () => {
    const page = read("app/chat/page.tsx");
    expect(page).toContain("onRedirect={redirectAfterDeny}");
    const start = page.indexOf("async function redirectAfterDeny(");
    expect(start).toBeGreaterThan(0);
    const body = page.slice(start, page.indexOf("\n  }\n", start));
    // The running turn gets a steer note (read right after the refusal)…
    expect(body).toContain("/chat/turns/${encodeURIComponent(id)}/steer");
    expect(body).toContain("setPendingSteers(");
    // …a turn that ended first gets the words as the next message…
    expect(body).toMatch(/status !== 404[\s\S]*send\(note\)/);
    // …and words that could not go stay in the box, never lost.
    expect(body).toContain("composer.setText(note)");
  });
});
