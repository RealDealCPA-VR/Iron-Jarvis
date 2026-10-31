/**
 * v1.417.0 (fix plan C1.7, the dashboard half) — the approval card says WHY
 * it asks and WHO asks, Enter is predictable, and the buttons do what they say.
 *
 *  - an `approval` frame's `why` (the daemon's one sentence: the once-only
 *    reason, "not in this chat's grants", an automatic rule's words) and
 *    `asker` ("model" | "iron_jarvis") survive the stream decoder's whitelist
 *    AND the hook (`PendingApproval.why` / `.asker`);
 *  - with `why` the card is the compact one: who wants to do what (the raw
 *    tool id only in a `title`), the preview, the cost, ONE why line, the
 *    buttons; Iron Jarvis's own check says "Iron Jarvis wants to …";
 *  - WITHOUT the new keys (an older daemon, a listing row) the card is
 *    today's, byte for byte (`fixtures/approval-card-old-dom-v1417.json`,
 *    captured from the card BEFORE this change; regenerate only on purpose
 *    with IJ_REGEN_APPROVAL_OLD_DOM=1);
 *  - Enter on a once-only SAFETY card still never allows it, but now SAYS so
 *    (a brief warn-toned status line in the dock);
 *  - a card the daemon answers ONCE whatever the button (priced, local only,
 *    a plan) offers Allow once and Deny only (`answersOnlyOnce`).
 */
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => ({ posts: [] as { path: string; body: unknown }[] }));

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return {
    ...real,
    post: async (p: string, body: unknown) => {
      H.posts.push({ path: p, body });
      return { ok: true };
    },
  };
});

import { ApprovalCard, type ApprovalAsk } from "@/components/chat/ApprovalCard";
import { DockAsk } from "@/components/chat/DockAsk";
import { answersOnlyOnce, askerSubject } from "@/lib/approvalWords";
import { ENTER_BLOCKED_MS, ENTER_BLOCKED_NOTE, type DockAskItem } from "@/lib/dockAsk";
import { decodeSSE, useChatStream, type PendingApproval } from "@/lib/useChatStream";

const FIXTURE = path.join(__dirname, "fixtures", "approval-card-old-dom-v1417.json");

beforeEach(() => {
  H.posts = [];
});
afterEach(() => cleanup());

/** Cards with NONE of the new keys, as the card drew them before v1.417.0. */
const OLD_CARDS: Record<string, { approval: ApprovalAsk; props?: Record<string, unknown> }> = {
  write_chat: {
    approval: {
      id: "a1",
      callId: "c1",
      tool: "write_file",
      args: { path: "notes/plan.md", content: "# Plan\n\nStep one." },
      timeoutS: 0,
      reversible: true,
    },
    props: { scope: "chat" },
  },
  shell_run: {
    approval: {
      id: "a2",
      callId: "c2",
      tool: "shell",
      args: { command: "git status" },
      timeoutS: 300,
      reversible: false,
    },
    props: { scope: "run" },
  },
  batch: {
    approval: {
      id: "a3",
      callId: "c3",
      tool: "rename_real_file",
      args: { source: "1.pdf" },
      count: 8,
      examples: [{ source: "1.pdf" }, { source: "2.pdf" }, { source: "3.pdf" }],
      timeoutS: 0,
    },
  },
  browser_always_docked: {
    approval: {
      id: "a4",
      callId: "c4",
      tool: "browser_click",
      args: { target: { role: "button", name: "Continue" } },
      canAlways: true,
      argsHash: "h1",
      reversible: false,
    },
    props: { docked: true },
  },
  once_git: {
    approval: {
      id: "a5",
      callId: "c5",
      tool: "shell",
      args: { command: "git push" },
      onceOnly: true,
      onceReason: "git",
    },
    props: { scope: "session", docked: true },
  },
  once_local: {
    approval: {
      id: "a6",
      callId: "c6",
      tool: "web_fetch",
      args: { url: "https://example.com" },
      onceOnly: true,
    },
    props: { scope: "chat" },
  },
  plan: {
    approval: { id: "a7", callId: "c7", tool: "approve_plan", args: { steps: ["Read", "Write"] } },
    props: { scope: "run" },
  },
  build_plan: {
    approval: { id: "a8", callId: "c8", tool: "exit_plan_mode", args: {} },
    props: { scope: "session", docked: true, planText: "1. Read the file\n2. Fix it" },
  },
  unknown_tool: {
    approval: { id: "a9", callId: "c9", tool: "custom:thing", args: { a: 1 } },
    props: { scope: "chat" },
  },
};

function domOf(key: string): string {
  const { approval, props } = OLD_CARDS[key];
  const { container, unmount } = render(<ApprovalCard approval={approval} {...(props ?? {})} />);
  const html = container.innerHTML;
  unmount();
  return html;
}

/** React's useId counters depend on render order, not on the card. */
const norm = (html: string) => html.replace(/_r_[0-9a-z]+_/g, "_r_");

describe("a card with none of the new keys is today's, byte for byte", () => {
  it("matches the DOM captured before the change", () => {
    const now: Record<string, string> = {};
    for (const key of Object.keys(OLD_CARDS)) now[key] = domOf(key);
    // Review: never re-made from the CURRENT card when it is missing (a
    // deleted fixture would then pass against whatever the card draws now).
    if (process.env.IJ_REGEN_APPROVAL_OLD_DOM === "1") {
      writeFileSync(FIXTURE, JSON.stringify(now, null, 2) + "\n", "utf8");
    }
    expect(existsSync(FIXTURE), `${FIXTURE} is missing: it holds the card from BEFORE v1.417.0`).toBe(true);
    const then = JSON.parse(readFileSync(FIXTURE, "utf8").replace(/\r\n/g, "\n")) as Record<string, string>;
    expect(Object.keys(then).sort()).toEqual(Object.keys(OLD_CARDS).sort());
    for (const key of Object.keys(OLD_CARDS))
      expect({ key, html: norm(now[key]) }).toEqual({ key, html: norm(then[key]) });
  });

  it("the asker alone (no why) changes only who asks", () => {
    const { container } = render(
      <ApprovalCard approval={{ ...OLD_CARDS.write_chat.approval, asker: "iron_jarvis" }} scope="chat" />,
    );
    const then = JSON.parse(readFileSync(FIXTURE, "utf8").replace(/\r\n/g, "\n")) as Record<string, string>;
    expect(norm(container.innerHTML)).toBe(
      norm(then.write_chat).replace("The assistant wants to", "Iron Jarvis wants to"),
    );
  });
});

// ---------------------------------------------------------------------------

const WHY = "Accept edits asks before this: it changes a file outside the project folder.";

describe("the stream keeps why and asker", () => {
  const frame = { id: "apr_w1", call_id: "c1", tool: "write_file" };

  it("the decoder keeps a real sentence and the two known askers, nothing else", () => {
    const yes = decodeSSE("approval", JSON.stringify({ ...frame, why: `  ${WHY}  `, asker: "iron_jarvis" })) as Record<
      string,
      unknown
    >;
    expect(yes.why).toBe(WHY);
    expect(yes.asker).toBe("iron_jarvis");
    expect(
      (decodeSSE("approval", JSON.stringify({ ...frame, asker: "model" })) as Record<string, unknown>).asker,
    ).toBe("model");
    for (const junk of [1, true, null, "", "   ", { why: WHY }]) {
      const no = decodeSSE("approval", JSON.stringify({ ...frame, why: junk, asker: junk })) as Record<string, unknown>;
      expect("why" in no, JSON.stringify(junk)).toBe(false);
      expect("asker" in no, JSON.stringify(junk)).toBe(false);
    }
    const other = decodeSSE("approval", JSON.stringify({ ...frame, asker: "someone" })) as Record<string, unknown>;
    expect("asker" in other).toBe(false);
    // A runaway sentence is cut, never dropped.
    const long = decodeSSE("approval", JSON.stringify({ ...frame, why: "x".repeat(2000) })) as Record<string, unknown>;
    expect((long.why as string).length).toBe(400);
    // The fields beside them still decode.
    const both = decodeSSE(
      "approval",
      JSON.stringify({ ...frame, why: WHY, once_only: true, cost_note: "12 credits", can_always: true }),
    ) as Record<string, unknown>;
    expect(both.once_only).toBe(true);
    expect(both.cost_note).toBe("12 credits");
    expect(both.can_always).toBe(true);
  });

  async function approvalAfter(f: Record<string, unknown>) {
    let close: () => void = () => {};
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(`event: approval\ndata: ${JSON.stringify(f)}\n\n`));
        close = () => controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, { status: 200 })));
    const { result } = renderHook(() => useChatStream());
    let run: Promise<unknown> = Promise.resolve();
    act(() => {
      run = result.current.run({ messages: [] }).catch(() => null);
    });
    await vi.waitFor(() => expect(result.current.approval?.id).toBe("apr_w1"));
    const seen = result.current.approval;
    await act(async () => {
      close();
      await run;
    });
    vi.unstubAllGlobals();
    return seen!;
  }

  it("the real hook puts why and asker on the pending approval, and only when sent", async () => {
    const got = await approvalAfter({ ...frame, why: WHY, asker: "iron_jarvis" });
    expect(got.why).toBe(WHY);
    expect(got.asker).toBe("iron_jarvis");
    const plain = await approvalAfter(frame);
    expect("why" in plain).toBe(false);
    expect("asker" in plain).toBe(false);
  });
});

// ---------------------------------------------------------------------------

const WRITE: PendingApproval = {
  id: "a_w",
  callId: "c_w",
  tool: "write_file",
  args: { path: "notes/plan.md", content: "# Plan" },
  timeoutS: 0,
};

describe("the card says why and who asks", () => {
  it("why is the card's ONE explanatory line; the generic paragraphs go", () => {
    render(<ApprovalCard approval={{ ...WRITE, why: WHY, reversible: true }} scope="chat" />);
    const card = screen.getByTestId("chat-approval-card");
    const whyLine = within(card).getByTestId("approval-why");
    expect(whyLine.textContent).toBe(`${WHY} You can undo this.`);
    // The undo verdict keeps its test id, inside the one line.
    expect(within(whyLine).getByTestId("approval-undo").dataset.reversible).toBe("true");
    const text = card.textContent ?? "";
    for (const gone of ["Your call", "is paused until you answer", "stops asking", "Waiting for you"]) {
      expect(text, gone).not.toContain(gone);
    }
    expect(within(card).queryByTestId("approval-waiting")).toBeNull();
    // Explanatory paragraphs: the why line and nothing else (the preview's
    // own lines are the preview, not the card's words).
    const own = [...card.querySelectorAll("p")].filter((p) => !p.closest('[data-testid="approval-preview"]'));
    expect(own.map((p) => p.dataset.testid)).toEqual(["approval-header", "approval-why"]);
  });

  it("the header names the action in words; the raw tool id is only in a title", () => {
    render(<ApprovalCard approval={{ ...WRITE, why: WHY }} scope="chat" />);
    const header = screen.getByTestId("approval-header");
    expect(header.textContent).toBe("The assistant wants to save a file");
    expect(header.textContent).not.toContain("write_file");
    expect(header.getAttribute("title")).toContain("write_file");
    expect(screen.getByTestId("chat-approval-card").querySelector("code")).toBeNull();
  });

  it("Iron Jarvis's own check says so", () => {
    render(
      <ApprovalCard
        approval={{ ...WRITE, tool: "shell", args: { command: "pytest -q" }, why: WHY, asker: "iron_jarvis" }}
        scope="session"
      />,
    );
    expect(screen.getByTestId("approval-header").textContent).toBe("Iron Jarvis wants to run a command");
    expect(askerSubject("iron_jarvis")).toBe("Iron Jarvis");
    expect(askerSubject("model")).toBe("The assistant");
    expect(askerSubject(undefined)).toBe("The assistant");
  });

  it("an unknown tool keeps its id as its words (no invented description)", () => {
    render(<ApprovalCard approval={{ ...WRITE, tool: "custom:thing", args: { a: 1 }, why: WHY }} scope="chat" />);
    expect(screen.getByTestId("approval-header").textContent).toBe("The assistant wants to run custom:thing");
  });

  it("a real deadline still shows, inside the one line", () => {
    render(<ApprovalCard approval={{ ...WRITE, timeoutS: 300, why: WHY }} scope="run" />);
    expect(screen.getByTestId("approval-waiting").textContent).toBe(
      "Waiting for you. If nobody answers within 5 min, it is not run.",
    );
    expect(screen.getByTestId("approval-why").contains(screen.getByTestId("approval-waiting"))).toBe(true);
  });

  it("a batch keeps its count, its examples and its buttons", () => {
    render(
      <ApprovalCard
        approval={{ ...OLD_CARDS.batch.approval, why: WHY } as ApprovalAsk}
        scope="chat"
      />,
    );
    expect(screen.getByTestId("approval-count").textContent).toBe("× 8");
    expect(screen.getByText("…and 5 more like these")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Allow these 8" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Deny all 8" })).toBeInTheDocument();
  });

  it("the wider answers say what they cover in their title, and keep their ids", () => {
    render(
      <ApprovalCard
        approval={{
          id: "a_b",
          callId: "c_b",
          tool: "browser_click",
          args: { target: { role: "button", name: "Continue" } },
          canAlways: true,
          argsHash: "h1",
          why: WHY,
        }}
        docked
      />,
    );
    expect(screen.getByTestId("approval-tab").getAttribute("title")).toMatch(/browser tab/);
    // Review: the title names what the daemon grants (the tool), in the
    // full card's own words, never "stop asking for click a button".
    expect(screen.getByTestId("approval-scope").getAttribute("title")).toBe(
      "Run it and stop asking for browser_click in this chat",
    );
    expect(screen.getByTestId("approval-always").getAttribute("title")).toMatch(/30 days/);
    expect(screen.queryByTestId("approval-tab-note")).toBeNull();
    expect(screen.queryByTestId("approval-always-note")).toBeNull();
  });

  it("a plan door keeps its own sentence (it says what Allow does)", () => {
    render(
      <ApprovalCard
        approval={{ id: "a_p", callId: "c_p", tool: "exit_plan_mode", args: {}, onceOnly: true, why: WHY }}
        scope="session"
        docked
        planText="1. Read"
      />,
    );
    expect(screen.getByTestId("approval-why").textContent).toMatch(/Allow and run/);
    expect(screen.getByRole("button", { name: "Allow and run" })).toBeInTheDocument();
  });

  it("the cost line stays (the quote is information)", () => {
    render(<ApprovalCard approval={{ ...WRITE, tool: "generate_media", costNote: "12 credits", why: WHY }} scope="chat" />);
    expect(screen.getByTestId("approval-cost").textContent).toBe("12 credits");
  });
});

// ---------------------------------------------------------------------------

describe("buttons do what they say (answersOnlyOnce)", () => {
  it.each([
    [{ tool: "write_file" }, false],
    [{ tool: "write_file", onceOnly: false }, false],
    [{ tool: "write_file", costNote: "" }, false],
    [{ tool: "write_file", costNote: "   " }, false],
    [{ tool: "shell", onceOnly: true }, true],
    [{ tool: "generate_media", costNote: "Generate 1 image for 12 credits" }, true],
    [{ tool: "approve_plan" }, true],
    [{ tool: "exit_plan_mode" }, true],
  ])("%j -> %s", (a, want) => {
    expect(answersOnlyOnce(a)).toBe(want);
  });

  for (const withWhy of [false, true]) {
    it(`a priced card shows Allow once and Deny only (${withWhy ? "with" : "without"} why)`, () => {
      render(
        <ApprovalCard
          approval={{
            id: "a_m",
            callId: "c_m",
            tool: "generate_media",
            args: { kind: "image" },
            costNote: "Generate 1 image on Pixio for 12 credits (your price)",
            canAlways: true,
            ...(withWhy ? { why: WHY } : {}),
          }}
          scope="session"
          docked
        />,
      );
      const card = screen.getByTestId("chat-approval-card");
      expect(
        within(card)
          .getAllByRole("button")
          .map((b) => b.textContent?.trim()),
      ).toEqual(["Allow once", "Deny"]);
      // Not a local-only card: it says nothing about "local only".
      expect(within(card).queryByTestId("approval-local-only")).toBeNull();
      expect(card.textContent).not.toContain("stops asking");
    });
  }

  it("an ordinary card still offers the wider answer (control)", () => {
    render(<ApprovalCard approval={WRITE} scope="session" docked />);
    expect(screen.getByTestId("approval-scope").textContent).toBe("Allow for this session");
  });
});

// ---------------------------------------------------------------------------

describe("Enter is predictable", () => {
  const safeAsk: PendingApproval = {
    id: "ap_git",
    callId: "c1",
    tool: "shell",
    args: { command: "git push" },
    onceOnly: true,
    onceReason: "git",
    why: "A push on this session's own branch asks you every time.",
  };
  const item = (a: PendingApproval): DockAskItem => ({ kind: "approval", id: a.id, approval: a });
  const dock = (asks: DockAskItem[]) => (
    <DockAsk
      asks={asks}
      onAnswerElicitation={vi.fn(async () => ({ ok: true }) as const)}
      onDecideSampling={vi.fn(async () => true)}
      armDelayMs={0}
    />
  );

  afterEach(() => {
    vi.useRealTimers();
    (document.activeElement as HTMLElement | null)?.blur?.();
  });

  it("Enter on a once-only safety card allows nothing, and says to click, briefly", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(dock([item(safeAsk)]));
    expect(screen.queryByTestId("dock-ask-enter-blocked")).toBeNull();
    const live = screen.getByTestId("dock-ask-enter-live");
    expect(live.getAttribute("role")).toBe("status");
    expect(live.textContent).toBe("");
    (document.activeElement as HTMLElement | null)?.blur?.();
    fireEvent.keyDown(document.body, { key: "Enter" });
    const note = screen.getByTestId("dock-ask-enter-blocked");
    expect(note.textContent).toBe(ENTER_BLOCKED_NOTE);
    expect(note.textContent).toBe("Click Allow once to run it");
    expect(note.className).toContain("text-tone-warn");
    // Review: the note lands in a live region that was ALREADY mounted (a
    // status region mounted together with its words is often not announced:
    // the v1.352.0 bell lesson).
    expect(live.contains(note)).toBe(true);
    // From inside the card too.
    fireEvent.keyDown(screen.getByTestId("dock-ask"), { key: "Enter" });
    expect(H.posts).toEqual([]);
    act(() => {
      vi.advanceTimersByTime(ENTER_BLOCKED_MS + 50);
    });
    expect(screen.queryByTestId("dock-ask-enter-blocked")).toBeNull();
    expect(H.posts).toEqual([]);
  });

  it("an ordinary card: Enter allows once, and no note", async () => {
    const plain: PendingApproval = { id: "ap_plain", callId: "c2", tool: "shell", args: { command: "git status" } };
    render(dock([item(plain)]));
    (document.activeElement as HTMLElement | null)?.blur?.();
    fireEvent.keyDown(document.body, { key: "Enter" });
    await waitFor(() => expect(H.posts).toEqual([{ path: "/chat/approvals/ap_plain", body: { decision: "once" } }]));
    expect(screen.queryByTestId("dock-ask-enter-blocked")).toBeNull();
  });

  it("Esc still declines a safety card", async () => {
    render(dock([item(safeAsk)]));
    (document.activeElement as HTMLElement | null)?.blur?.();
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(H.posts).toEqual([{ path: "/chat/approvals/ap_git", body: { decision: "deny" } }]));
    expect(screen.queryByTestId("dock-ask-enter-blocked")).toBeNull();
  });
});
