/**
 * v1.355.0 lane 5A — the chat's receipts, round two (G55, G62, G64), through
 * the REAL pieces: the real TurnReceipt, the real stream decode (`decodeSSE`
 * and `run()` over a mocked SSE fetch), the real panel-receipt helpers and
 * component. The page wiring is pinned in chat-failure-press-v13550.
 *
 *  - G55: a reply whose turn left older messages out to fit says so on its
 *    receipt, visible WITHOUT expanding: "Left out 12 older messages to fit
 *    this model".
 *  - G64: the expanded receipt lists every approval the user gave ("You
 *    approved: save a file (once)").
 *  - G62: an agent's line says how long it took, a remote still being
 *    waited on says for how long, and a remote with message-back off never
 *    shows a wait. The daemon's sentence is pinned EQUAL to the page's mark.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";

import { TurnReceipt, receiptWantsAttention } from "@/components/chat/TurnReceipt";
import { PanelReplyLine, PanelWaiting } from "@/components/chat/PanelReceipt";
import { decodeSSE, useChatStream } from "@/lib/useChatStream";
import {
  APPROVED_MAX,
  approvedLine,
  decodeApproved,
  decodeRefit,
  refitLine,
} from "@/lib/turnRecovery";
import {
  NO_REPLY_BACK_MARK,
  isNoReplyBack,
  panelAskedAt,
  panelReplyMs,
  panelWaiting,
  waitingWords,
} from "@/lib/panelReceipt";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const ROUTE = { requested: "", provider: "claude-cli", model: "claude-opus-5-5", reason: "default" };

// --------------------------------------------------------------------------- //
// G55 — leaving out older messages to fit is said on the reply.
// --------------------------------------------------------------------------- //
describe("G55: the receipt says what was left out to fit", () => {
  it("says it on the collapsed line, without expanding, in the quiet class", () => {
    render(<TurnReceipt inline route={ROUTE} refit={{ dropped: 12 }} />);
    const part = screen.getByTestId("turn-refit");
    expect(part.textContent).toBe("Left out 12 older messages to fit this model");
    // Quiet, like an adapted turn: plain grey, never the warning chip.
    expect(part.className).toContain("text-zinc-500");
    expect(part.className).not.toMatch(/tone-warn|border|rounded/);
    // Visible WITHOUT expanding: the detail panel is not open.
    expect(screen.queryByTestId("turn-receipt-detail")).toBeNull();
    expect(screen.getByTestId("turn-receipt").textContent).toContain(
      "Left out 12 older messages to fit this model",
    );
  });

  it("one message reads singular; a retry that left no message out says what it did", () => {
    expect(refitLine({ dropped: 1 })).toBe("Left out 1 older message to fit this model");
    expect(refitLine({ dropped: 0 })).toBe("Shortened earlier tool results to fit this model");
    expect(refitLine(null)).toBeNull();
    expect(refitLine(undefined)).toBeNull();
  });

  it("a phone (no hover) reads why in the expanded receipt", () => {
    render(<TurnReceipt inline route={ROUTE} refit={{ dropped: 3 }} />);
    fireEvent.click(screen.getByTestId("turn-receipt"));
    expect(screen.getByTestId("turn-refit-detail").textContent).toMatch(/left the oldest messages out/);
  });

  it("keeps the reply's row on screen (it is never behind a hover)", () => {
    expect(receiptWantsAttention({ route: ROUTE, refit: { dropped: 4 } })).toBe(true);
    expect(receiptWantsAttention({ route: ROUTE })).toBe(false);
  });

  it("is a fact on its own: a reply with nothing else to say still renders it", () => {
    render(<TurnReceipt refit={{ dropped: 2 }} />);
    expect(screen.getByTestId("turn-refit").textContent).toBe("Left out 2 older messages to fit this model");
  });

  it("decodes only a real shape (a saved thread is read back from disk)", () => {
    expect(decodeRefit({ dropped: 12 })).toEqual({ dropped: 12 });
    expect(decodeRefit({ dropped: 2.7 })).toEqual({ dropped: 2 });
    for (const junk of [null, undefined, "12", { dropped: "12" }, { dropped: -1 }, { dropped: NaN }, []]) {
      expect(decodeRefit(junk)).toBeNull();
    }
  });
});

// --------------------------------------------------------------------------- //
// G64 — the reply records the approvals you gave.
// --------------------------------------------------------------------------- //
describe("G64: the expanded receipt lists the approvals given", () => {
  const TWO = [
    { tool: "write_file", decision: "once" },
    { tool: "shell", decision: "conversation" },
  ];

  it("after allowing two actions, the expanded receipt lists both", () => {
    render(<TurnReceipt inline route={ROUTE} toolsUsed={["write_file", "shell"]} approved={TWO} />);
    // Folded until the user opens the receipt (the calm line stays short).
    expect(screen.queryAllByTestId("turn-approved")).toHaveLength(0);
    fireEvent.click(screen.getByTestId("turn-receipt"));
    const rows = screen.getAllByTestId("turn-approved");
    expect(rows.map((r) => r.textContent)).toEqual([
      "You approved: save a file (once)",
      "You approved: run a command (for this chat)",
    ]);
    // The exact tool id stays on hover, never in the words.
    expect(rows[0].getAttribute("title")).toBe("write_file");
    expect(rows[1].className).toContain("text-zinc-400");
  });

  it("says each answer in the card's own reach, and a batch's size", () => {
    expect(approvedLine({ tool: "browser_click", decision: "tab" })).toBe(
      "You approved: act in a browser tab (for this tab)",
    );
    expect(approvedLine({ tool: "write_file", decision: "always" })).toBe(
      "You approved: save a file (always, exactly this)",
    );
    expect(approvedLine({ tool: "write_file", decision: "once", count: 3 })).toBe(
      "You approved: save a file (once, 3 calls)",
    );
    // A tool nobody described keeps the card's own fallback words.
    expect(approvedLine({ tool: "custom:thing", decision: "once" })).toBe(
      "You approved: run custom:thing (once)",
    );
  });

  it("a refusal is never an approval, and junk rows die in the decode", () => {
    expect(
      decodeApproved([
        { tool: "shell", decision: "deny" },
        { tool: "shell", decision: "timeout" },
        { tool: "", decision: "once" },
        { decision: "once" },
        "shell",
        { tool: "write_file", decision: "ONCE", count: 1 },
      ]),
    ).toEqual([{ tool: "write_file", decision: "once" }]);
    expect(decodeApproved(null)).toEqual([]);
    const many = Array.from({ length: 40 }, () => ({ tool: "shell", decision: "once" }));
    expect(decodeApproved(many)).toHaveLength(APPROVED_MAX);
  });
});

// --------------------------------------------------------------------------- //
// The wire: the decode keeps both, and run() hands them to the page.
// --------------------------------------------------------------------------- //
describe("the stream decode carries refit, approved and the failure's press", () => {
  const done = (data: Record<string, unknown>) =>
    decodeSSE("done", JSON.stringify({ reply: "ok", ...data }));

  it("decodes refit and approved, and leaves null / [] absent", () => {
    const ev = done({ refit: { dropped: 12 }, approved: [{ tool: "shell", decision: "once" }] });
    expect(ev && ev.type === "done" && ev.refit).toEqual({ dropped: 12 });
    expect(ev && ev.type === "done" && ev.approved).toEqual([{ tool: "shell", decision: "once" }]);
    const quiet = done({ refit: null, approved: [] });
    expect(quiet && quiet.type === "done" && quiet.refit).toBeUndefined();
    expect(quiet && quiet.type === "done" && quiet.approved).toBeUndefined();
  });

  it("decodes the error frame's press beside its words", () => {
    const ev = decodeSSE(
      "error",
      JSON.stringify({
        detail: "x",
        title: "Claude Code isn't signed in",
        fix: "Press Sign in…",
        technical: "Not logged in",
        fix_kind: "sign_in",
        fix_provider: "claude-cli",
      }),
    );
    expect(ev && ev.type === "error" && ev.fix_kind).toBe("sign_in");
    expect(ev && ev.type === "error" && ev.fix_provider).toBe("claude-cli");
  });

  function sse(frames: string) {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(frames));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 200 })));
  }

  it("run() resolves with refit and approved (the merge line where fields die)", async () => {
    sse(
      `event: done\ndata: ${JSON.stringify({
        reply: "made it",
        refit: { dropped: 12 },
        approved: [
          { tool: "write_file", decision: "once" },
          { tool: "shell", decision: "conversation" },
        ],
      })}\n\n`,
    );
    const { result } = renderHook(() => useChatStream());
    let settled: Awaited<ReturnType<typeof result.current.run>> | null = null;
    await act(async () => {
      settled = await result.current.run({ messages: [] });
    });
    expect(settled!.refit).toEqual({ dropped: 12 });
    expect(settled!.approved).toHaveLength(2);
  });

  it("run() throws a StreamError that carries the press", async () => {
    sse(
      `event: error\ndata: ${JSON.stringify({
        detail: "boom",
        title: "OpenRouter refused your key",
        fix: "Update it in Settings › Connections.",
        technical: "openrouter API error 401",
        fix_kind: "key",
        fix_provider: "openrouter",
      })}\n\n`,
    );
    const { result } = renderHook(() => useChatStream());
    let caught: unknown = null;
    await act(async () => {
      try {
        await result.current.run({ messages: [] });
      } catch (e) {
        caught = e;
      }
    });
    const se = caught as { fixKind?: string; fixProvider?: string; title?: string };
    expect(se.title).toBe("OpenRouter refused your key");
    expect(se.fixKind).toBe("key");
    expect(se.fixProvider).toBe("openrouter");
  });
});

// --------------------------------------------------------------------------- //
// G62 — an agent's line has a receipt, a remote's wait has an age.
// --------------------------------------------------------------------------- //
describe("G62: agent replies get a receipt line and a pending state", () => {
  const T0 = "2026-10-19T10:00:00.000Z";
  const plus = (ms: number) => new Date(Date.parse(T0) + ms).toISOString();

  it("a reply says who and how long since the question", () => {
    render(<PanelReplyLine name="hermes" askedAt={T0} replyAt={plus(372_000)} />);
    expect(screen.getByTestId("panel-receipt").textContent).toBe("from hermes · 6 min 12 s");
  });

  it("with a stamp missing it still says who, never a made-up time", () => {
    render(<PanelReplyLine name="Builder" replyAt={plus(1000)} />);
    expect(screen.getByTestId("panel-receipt").textContent).toBe("from Builder");
    expect(panelReplyMs(plus(1000), undefined)).toBeNull();
    expect(panelReplyMs(T0, plus(5000))).toBeNull(); // a reply before its question
  });

  it("a wait reads in whole minutes, in the warn tone, and ticks by itself", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(Date.parse(T0) + 3 * 60_000 + 5_000));
    render(<PanelWaiting since={T0} />);
    const w = screen.getByTestId("panel-waiting");
    expect(w.textContent).toBe("· waiting 3 min");
    expect(w.className).toContain("text-tone-warn");
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.getByTestId("panel-waiting").textContent).toBe("· waiting 4 min");
  });

  it("the words: under a minute, minutes, hours", () => {
    expect(waitingWords(20_000)).toBe("waiting less than a minute");
    expect(waitingWords(180_000)).toBe("waiting 3 min");
    expect(waitingWords(65 * 60_000)).toBe("waiting 1 h 5 min");
    expect(waitingWords(120 * 60_000)).toBe("waiting 2 h");
  });

  it("a remote is waited on until a later line from it arrives", () => {
    const pending = {
      role: "assistant",
      panelWho: "remote:hermes",
      panelKind: "progress",
      content: "hermes has the task and will report back here when it has something.",
      at: plus(2000),
    };
    const q = { role: "user", content: "@hermes research", at: T0 };
    expect(panelWaiting([q, pending])).toEqual([false, true]);
    // Another agent's line does not end hermes's wait…
    const other = { role: "assistant", panelWho: "remote:atlas", content: "hi", at: plus(3000) };
    expect(panelWaiting([q, pending, other])).toEqual([false, true, false]);
    // …hermes's own answer does.
    const answer = { role: "assistant", panelWho: "remote:hermes", panelKind: "done", content: "Here it is", at: plus(372_000) };
    expect(panelWaiting([q, pending, answer])).toEqual([false, false, false]);
    // The answer's receipt counts from the question it answers.
    expect(panelAskedAt([q, pending, answer])[2]).toBe(T0);
  });

  it("message-back off is never shown as waiting", () => {
    const off = {
      role: "assistant",
      panelWho: "remote:hermes",
      panelKind: "progress",
      content: `hermes took the task. ${NO_REPLY_BACK_MARK}; turn on Let it message back on its card to get it in this chat.`,
    };
    expect(isNoReplyBack(off)).toBe(true);
    expect(panelWaiting([{ role: "user", content: "@hermes x" }, off])).toEqual([false, false]);
    // A local agent's line never waits either (it answers in the round).
    const local = { role: "assistant", panelWho: "builtin:builder", panelKind: "progress", content: "…" };
    expect(panelWaiting([local])).toEqual([false]);
  });

  it("review: a line saved before v1.355.0 with the old message-back-off words never waits", () => {
    // threads.py v1.285.0 to v1.354.0 said this for a remote with
    // message-back OFF; a chat saved then must not show "waiting 52 h"
    // forever after the update.
    const old = {
      role: "assistant",
      panelWho: "remote:hermes",
      panelKind: "progress",
      content: "hermes has the task and will report back on its own side when it has something.",
      at: plus(2000),
    };
    expect(isNoReplyBack(old)).toBe(true);
    expect(panelWaiting([{ role: "user", content: "@hermes x", at: T0 }, old])).toEqual([false, false]);
    // Message-back ON keeps waiting.
    const on = { ...old, content: "hermes has the task and will report back here when it has something." };
    expect(isNoReplyBack(on)).toBe(false);
    expect(panelWaiting([{ role: "user", content: "@hermes x", at: T0 }, on])).toEqual([false, true]);
  });

  it("review: a remote's late answer is timed from ITS question, not a later message", () => {
    const q = { role: "user", content: "@hermes research the lease", at: T0 };
    const pending = {
      role: "assistant",
      panelWho: "remote:hermes",
      panelKind: "progress",
      content: "hermes has the task and will report back here when it has something.",
      at: plus(2000),
    };
    // The user carries on with Jarvis while hermes works.
    const q2 = { role: "user", content: "what is the weather", at: plus(300_000) };
    const jarvis = { role: "assistant", content: "Sunny." };
    // A progress update from hermes does not move the start either.
    const update = { ...pending, content: "halfway there", at: plus(320_000) };
    const answer = { role: "assistant", panelWho: "remote:hermes", panelKind: "done", content: "Here it is", at: plus(372_000) };
    const asked = panelAskedAt([q, pending, q2, jarvis, update, answer]);
    expect(asked[5]).toBe(T0);
    // The answer closes it: hermes's NEXT answer counts from the newest question.
    const q3 = { role: "user", content: "@hermes and the deposit?", at: plus(400_000) };
    const answer2 = { ...answer, content: "Two months", at: plus(410_000) };
    expect(panelAskedAt([q, pending, q2, jarvis, answer, q3, answer2])[6]).toBe(plus(400_000));
    // A line that said its answer will not come back opens nothing.
    const off = { ...pending, content: `hermes took the task. ${NO_REPLY_BACK_MARK}; turn on Let it message back on its card to get it in this chat.` };
    expect(panelAskedAt([q, off, q2, answer])[3]).toBe(plus(300_000));
  });

  it("the page's mark is the daemon's own sentence (pinned EQUAL)", () => {
    const py = readFileSync(
      join(process.cwd(), "..", "src", "iron_jarvis", "agents", "threads.py"),
      "utf8",
    ).replace(/\r\n/g, "\n");
    expect(py).toContain(`NO_REPLY_BACK_MARK = "${NO_REPLY_BACK_MARK}"`);
  });
});
