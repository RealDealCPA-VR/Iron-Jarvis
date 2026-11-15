/**
 * v1.499.0 (vision plan leap 3, Phase 3): "Is Hartleys a client?" under ONE
 * reply.
 *
 * The daemon puts `client_question: {id, name, folders, work} | null` on the
 * POST reply and the stream's done frame (always present). The chat stores it
 * on the message like the preference suggestion, and the line asks in the
 * same quiet voice: Yes posts the client's own confirm and shows "Hartleys is
 * a client now." with a link to its page; No posts its decline and says it
 * won't ask again; a 404 / 409 shows the route's own sentence and hides the
 * question; a reopened chat shows the answer, never the question again. The
 * id in a path is only ever a client id.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const H = vi.hoisted(() => ({
  posts: [] as { path: string; body: unknown }[],
  answer: {} as unknown,
  fail: null as null | { status: number; message: string },
}));

vi.mock("@/lib/api", () => ({
  post: async (path: string, body: unknown) => {
    H.posts.push({ path, body });
    if (H.fail) throw H.fail;
    return H.answer;
  },
  // What the stream hook (lib/useChatStream) imports, for its own pin below.
  API_BASE: "",
  ijToken: () => "",
  flattenDetail: (d: unknown) => String(d ?? ""),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  },
}));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import type React from "react";
import { useState } from "react";
import { ClientQuestion } from "@/components/chat/ClientQuestion";
import { decodeClientQuestion, type ChatClientQuestion } from "@/lib/clients";

const ROOT = join(__dirname, "..");
const src = (p: string) => readFileSync(join(ROOT, p), "utf8").replace(/\r\n/g, "\n");

const HID = "client_ab12cd34ef56";
const ASK: ChatClientQuestion = { id: HID, name: "Hartleys", folders: 2, work: 9 };

beforeEach(() => {
  H.posts = [];
  H.answer = { ok: true, client: { id: HID, name: "Hartleys" }, note: "" };
  H.fail = null;
});
afterEach(() => cleanup());

describe("the question under a reply", () => {
  it("asks once, in one sentence, with two one-press answers", () => {
    render(<ClientQuestion question={ASK} />);
    const line = screen.getByTestId("client-question");
    expect(line.getAttribute("data-state")).toBe("open");
    expect(screen.getByTestId("client-question-line").textContent).toBe(
      "Iron Jarvis has seen Hartleys in 2 folders and 9 pieces of work. Is Hartleys a client?",
    );
    expect(screen.getByTestId("client-question-confirm").textContent).toBe("Yes, a client");
    expect(screen.getByTestId("client-question-decline").textContent).toBe("No");
    // Nothing is pressed by drawing it.
    expect(H.posts).toEqual([]);
  });

  it("Yes posts the client's own confirm and says so, with a link to its page", async () => {
    const settle = vi.fn();
    render(<ClientQuestion question={ASK} onSettle={settle} />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("client-question-confirm"));
    });
    expect(H.posts).toEqual([{ path: `/clients/${HID}/confirm`, body: {} }]);
    expect(screen.getByTestId("client-question-yes").textContent).toBe("Hartleys is a client now.");
    const link = screen.getByTestId("client-question-page");
    expect(link.textContent).toBe("Hartleys' page");
    expect(link.getAttribute("href")).toBe(`/clients/${HID}`);
    expect(screen.queryByTestId("client-question-confirm")).toBeNull();
    expect(settle).toHaveBeenCalledWith({ ...ASK, state: "yes" });
  });

  it("No posts the client's own decline and says it won't ask again", async () => {
    const settle = vi.fn();
    render(<ClientQuestion question={ASK} onSettle={settle} />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("client-question-decline"));
    });
    // A No expects a WAITING name, so a stale line never takes a client away.
    expect(H.posts).toEqual([{ path: `/clients/${HID}/decline`, body: { expect: "proposed" } }]);
    expect(screen.getByTestId("client-question-no").textContent).toBe("Iron Jarvis won't ask about Hartleys again.");
    expect(settle).toHaveBeenCalledWith({ ...ASK, state: "no" });
  });

  it("a No shows the daemon's own note, never a sentence of its own over it", async () => {
    H.answer = { ok: true, client: { id: HID, name: "Hartleys" }, note: "Iron Jarvis won't ask about Hartleys again." };
    render(<ClientQuestion question={ASK} />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("client-question-decline"));
    });
    expect(screen.getByTestId("client-question-no").textContent).toBe("Iron Jarvis won't ask about Hartleys again.");
    cleanup();
    H.answer = { ok: true, client: { id: HID, name: "Hartleys" }, note: "Chats in Hartleys' folders stop being told about Hartleys." };
    render(<ClientQuestion question={ASK} />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("client-question-decline"));
    });
    expect(screen.getByTestId("client-question-no").textContent).toBe(
      "Chats in Hartleys' folders stop being told about Hartleys.",
    );
  });

  it("the answer is announced, and a failure is an alert", async () => {
    render(<ClientQuestion question={ASK} />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("client-question-confirm"));
    });
    expect(screen.getByTestId("client-question").getAttribute("role")).toBe("status");
    cleanup();
    H.fail = { status: 503, message: "" };
    render(<ClientQuestion question={ASK} />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("client-question-confirm"));
    });
    await waitFor(() => expect(screen.getByTestId("client-question-error").getAttribute("role")).toBe("alert"));
  });

  it("a stale No after a Yes elsewhere is refused (409) and the line says so", async () => {
    H.fail = { status: 409, message: "Hartleys was already answered." };
    render(<ClientQuestion question={ASK} />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("client-question-decline"));
    });
    expect(H.posts).toEqual([{ path: `/clients/${HID}/decline`, body: { expect: "proposed" } }]);
    expect(screen.getByTestId("client-question-refused").textContent).toBe("Hartleys was already answered.");
  });

  it("answered somewhere else (409) shows the route's own sentence and the question goes", async () => {
    H.fail = { status: 409, message: "Hartleys was already answered." };
    const settle = vi.fn();
    render(<ClientQuestion question={ASK} onSettle={settle} />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("client-question-confirm"));
    });
    expect(screen.getByTestId("client-question-refused").textContent).toBe("Hartleys was already answered.");
    expect(screen.queryByTestId("client-question-confirm")).toBeNull();
    expect(screen.queryByTestId("client-question-decline")).toBeNull();
    expect(settle).toHaveBeenCalledWith({ ...ASK, state: "gone" });
  });

  it("the refusal stays on screen after the page stores 'gone' on the message", async () => {
    // The chat page stores the answer on the message and draws the line from
    // it (v1.499.1: it used to unmount the line on "gone", so the sentence
    // was never seen). A parent that does the same keeps the sentence.
    H.fail = { status: 409, message: "Hartleys was already answered." };
    function Row() {
      const [q, setQ] = useState<ChatClientQuestion>(ASK);
      return <ClientQuestion question={q} onSettle={setQ} />;
    }
    render(<Row />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("client-question-decline"));
    });
    expect(screen.getByTestId("client-question").getAttribute("data-state")).toBe("gone");
    expect(screen.getByTestId("client-question-refused").textContent).toBe("Hartleys was already answered.");
  });

  it("a 404 says the client is not on record", async () => {
    H.fail = { status: 404, message: "" };
    render(<ClientQuestion question={ASK} />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("client-question-decline"));
    });
    expect(screen.getByTestId("client-question-refused").textContent).toBe("That client is not on record.");
  });

  it("a press that failed for another reason says so and keeps the question", async () => {
    H.fail = { status: 503, message: "Your clients could not be read. Nothing was changed." };
    const settle = vi.fn();
    render(<ClientQuestion question={ASK} onSettle={settle} />);
    await act(async () => {
      fireEvent.click(screen.getByTestId("client-question-confirm"));
    });
    await waitFor(() =>
      expect(screen.getByTestId("client-question-error").textContent).toBe("Couldn't save your answer. Try again."),
    );
    expect(screen.getByTestId("client-question-confirm")).toBeTruthy();
    expect(settle).not.toHaveBeenCalled();
  });

  it("a reopened chat shows the answer it stored, never the question again", () => {
    // What a saved message hands back goes through the same decoder.
    const yes = decodeClientQuestion(JSON.parse(JSON.stringify({ ...ASK, state: "yes" })))!;
    render(<ClientQuestion question={yes} />);
    expect(screen.getByTestId("client-question").getAttribute("data-state")).toBe("yes");
    expect(screen.queryByTestId("client-question-confirm")).toBeNull();
    cleanup();
    render(<ClientQuestion question={{ ...ASK, state: "no" }} />);
    expect(screen.getByTestId("client-question-no").textContent).toBe("Iron Jarvis won't ask about Hartleys again.");
    cleanup();
    const { container } = render(<ClientQuestion question={{ ...ASK, state: "gone" }} />);
    expect(container).toBeEmptyDOMElement();
    expect(H.posts).toEqual([]);
  });

  it("a question whose id is not a client id never reaches the screen", () => {
    expect(decodeClientQuestion({ id: "Hartleys", name: "Hartleys", folders: 2, work: 9 })).toBeNull();
    expect(decodeClientQuestion({ id: `${HID}/../../settings`, name: "Hartleys" })).toBeNull();
    expect(decodeClientQuestion(null)).toBeNull();
  });
});

describe("where it is stored and drawn (source pins)", () => {
  const page = src("app/chat/page.tsx");

  it("both lanes of the chat page keep it on the reply", () => {
    // Stream lane: read off the stream result and spread into the receipt.
    expect(page).toContain("(streamRes as unknown as { clientQuestion?: unknown }).clientQuestion");
    const stream = page.slice(page.indexOf("const receipt = {"), page.indexOf("const receipt = {") + 4000);
    expect(stream).toContain("...(clientQuestion ? { clientQuestion } : {}),");
    // POST lane: the reply's own `client_question`.
    expect(page).toContain("const clientQuestionPost = decodeClientQuestion(res.client_question);");
    const post = page.slice(page.indexOf("const receiptPost = {"), page.indexOf("const receiptPost = {") + 4000);
    expect(post).toContain("...(clientQuestionPost ? { clientQuestion: clientQuestionPost } : {}),");
  });

  it("an answer is stored on its message and survives a later save", () => {
    expect(page).toContain("msgs = applySettledQuestions(msgs, settledClientQuestionsRef.current);");
    // v1.499.1: remembered against the very question it answered (the
    // message's own object), never the client id: after "Ask again" a newer
    // question for the same client must not take the old answer.
    expect(page).toMatch(
      /settleClientQuestion: \(index, next\) => \{[\s\S]{0,900}rememberClientAnswer\(settledClientQuestionsRef\.current, target\.clientQuestion, next\)/,
    );
    expect(page).not.toContain("settledClientQuestionsRef.current.set(next.id, next)");
    expect(page).toContain("onSettle={(next) => h.settleClientQuestion(i, next)}");
  });

  it("the page never hides a settled question itself: the line decides what a 'gone' one shows", () => {
    // Hiding it on "gone" unmounted the line the moment a 409 settled it,
    // and the route's "already answered" sentence was never seen (v1.499.1).
    const at = page.indexOf("<ClientQuestion");
    expect(at).toBeGreaterThan(0);
    const gate = page.slice(page.lastIndexOf("{", at), at);
    expect(gate).not.toContain('"gone"');
  });

  it("at most one question line rides a reply: a preference suggestion goes first", () => {
    expect(page).toContain("const clientQuestion = suggestion ? null : decodeClientQuestion(m.clientQuestion);");
    const row = src("components/harness/HarnessMessageRow.tsx");
    expect(row).toMatch(/if \(decodeSuggestion\(m\.suggestion\)\) return null;\s+const cq = decodeClientQuestion\(/);
    expect(row).toContain("<ClientQuestion key={cq.id} question={cq} />");
  });
});

describe("the stream hook carries the question (whitelist, like the suggestion)", () => {
  const WIRE = { id: "client_ab12cd34ef56", name: "Hartleys", folders: 2, work: 9, extra: "never kept" };

  it("decodes a valid one and drops null and a name for an id", async () => {
    const { decodeSSE } = await import("@/lib/useChatStream");
    const ev = decodeSSE("done", JSON.stringify({ reply: "r", client_question: WIRE })) as {
      client_question?: Record<string, unknown>;
    };
    expect(ev.client_question).toEqual({ id: "client_ab12cd34ef56", name: "Hartleys", folders: 2, work: 9 });
    const none = decodeSSE("done", JSON.stringify({ reply: "r", client_question: null })) as Record<string, unknown>;
    expect("client_question" in none).toBe(false);
    const bad = decodeSSE("done", JSON.stringify({ reply: "r", client_question: { ...WIRE, id: "Hartley's" } }));
    expect("client_question" in (bad as Record<string, unknown>)).toBe(false);
  });

  it("the real hook puts it on the resolved result as clientQuestion", async () => {
    const { useChatStream } = await import("@/lib/useChatStream");
    const { renderHook } = await import("@testing-library/react");
    const frames = `event: done
data: ${JSON.stringify({ reply: "ok", client_question: WIRE })}

`;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(frames));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, { status: 200 })));
    try {
      const { result } = renderHook(() => useChatStream());
      let settled: { clientQuestion?: { id: string; name: string } } | null = null;
      await act(async () => {
        settled = (await result.current.run({ messages: [] })) as typeof settled;
      });
      expect(settled!.clientQuestion).toEqual({ id: "client_ab12cd34ef56", name: "Hartleys", folders: 2, work: 9 });
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
