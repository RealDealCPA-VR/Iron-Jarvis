/**
 * v1.325.0 — wave D: the version picker, the conversation map and quoting a
 * selection (components/chat/{BranchPicker,ConversationMap,QuoteSelection},
 * lib/quote).
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { BranchPicker } from "@/components/chat/BranchPicker";
import {
  ConversationMap,
  MAP_LINE_CHARS,
  firstLine,
  mapRows,
  type MapMessage,
} from "@/components/chat/ConversationMap";
import { QuoteSelection } from "@/components/chat/QuoteSelection";
import { QUOTE_MAX_CHARS, insertQuote, quoteBlock } from "@/lib/quote";

afterEach(() => {
  cleanup();
  window.getSelection()?.removeAllRanges();
});

// ---------------------------------------------------------------- picker

describe("BranchPicker", () => {
  it("renders nothing for a single version", () => {
    const { container } = render(<BranchPicker pos={0} count={1} onSwitch={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows 'n / count' (1-based) and switches by 0-based index", () => {
    const onSwitch = vi.fn();
    render(<BranchPicker pos={1} count={3} onSwitch={onSwitch} />);
    const picker = screen.getByTestId("branch-picker");
    expect(picker).toHaveTextContent("2 / 3");
    expect(picker).toHaveAccessibleName("Version 2 of 3");
    fireEvent.click(screen.getByRole("button", { name: "Previous version" }));
    expect(onSwitch).toHaveBeenLastCalledWith(0);
    fireEvent.click(screen.getByRole("button", { name: "Next version" }));
    expect(onSwitch).toHaveBeenLastCalledWith(2);
  });

  it("disables the arrow at each end", () => {
    const { rerender } = render(<BranchPicker pos={0} count={2} onSwitch={() => {}} />);
    expect(screen.getByRole("button", { name: "Previous version" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next version" })).toBeEnabled();
    rerender(<BranchPicker pos={1} count={2} onSwitch={() => {}} />);
    expect(screen.getByRole("button", { name: "Previous version" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Next version" })).toBeDisabled();
  });

  it("is fully disabled while a turn runs", () => {
    const onSwitch = vi.fn();
    render(<BranchPicker pos={1} count={3} onSwitch={onSwitch} disabled />);
    const prev = screen.getByRole("button", { name: "Previous version" });
    const next = screen.getByRole("button", { name: "Next version" });
    expect(prev).toBeDisabled();
    expect(next).toBeDisabled();
    fireEvent.click(prev);
    fireEvent.click(next);
    expect(onSwitch).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------- map

const U = (content: string, extra: Partial<MapMessage> = {}): MapMessage => ({ role: "user", content, ...extra });
const A = (content: string): MapMessage => ({ role: "assistant", content });

function convo(): MapMessage[] {
  return [
    U("What is a K-1?"), // 0
    A("A short answer."), // 1
    U("Explain depreciation\nin more detail please"), // 2
    U("make it shorter", { steer: true }), // 3
    A(Array.from({ length: 120 }, () => "word").join(" ")), // 4
    U("carry on", { continuation: true }), // 5 — hidden
    A("more words here"), // 6 — merged into the same answer
    U("> quoted bit from above\nWhy is that?"), // 7
  ];
}

describe("ConversationMap — rows", () => {
  it("lists questions, indents steer notes, skips hidden continuations", () => {
    const rows = mapRows(convo());
    expect(rows.map((r) => [r.index, r.kind])).toEqual([
      [0, "question"],
      [2, "question"],
      [3, "note"],
      [7, "question"],
    ]);
    expect(rows[0].replyWords).toBe(3);
    // the continuation's answer counts toward the question it continued
    expect(rows[1].replyWords).toBe(123);
    expect(rows[3].replyWords).toBeNull();
  });

  it("first line: skips quoted lines, collapses spaces, caps the length", () => {
    expect(firstLine("> quoted\n\n  Why   is that?  ")).toBe("Why is that?");
    expect(firstLine("> only a quote")).toBe("only a quote");
    expect(firstLine("")).toBe("");
    const long = firstLine("x".repeat(200));
    expect(long).toHaveLength(MAP_LINE_CHARS);
    expect(long.endsWith("…")).toBe(true);
  });

  it("says how long each reply was, quietly", () => {
    render(<ConversationMap messages={convo()} onJump={() => {}} onClose={() => {}} />);
    const opts = screen.getAllByRole("option");
    expect(opts).toHaveLength(4);
    expect(opts[0]).toHaveTextContent("What is a K-1?");
    expect(opts[0]).toHaveTextContent("short reply");
    expect(opts[1]).toHaveTextContent("Explain depreciation");
    expect(opts[1]).not.toHaveTextContent("in more detail");
    expect(opts[1]).toHaveTextContent("reply · 123 words");
    expect(opts[2]).toHaveTextContent("Note: make it shorter");
    expect(opts[2]).toHaveAttribute("data-kind", "note");
    expect(opts[3]).toHaveTextContent("Why is that?");
    expect(opts[3]).toHaveTextContent("no reply yet");
    expect(screen.getByRole("dialog", { name: "Conversation map" })).toHaveTextContent("3 questions");
  });
});

describe("ConversationMap — use", () => {
  it("a click jumps to that message", () => {
    const onJump = vi.fn();
    render(<ConversationMap messages={convo()} onJump={onJump} onClose={() => {}} />);
    fireEvent.click(screen.getAllByRole("option")[3]);
    expect(onJump).toHaveBeenCalledWith(7);
  });

  it("arrows move, Enter jumps, Esc closes", () => {
    const onJump = vi.fn();
    const onClose = vi.fn();
    render(<ConversationMap messages={convo()} onJump={onJump} onClose={onClose} />);
    const list = screen.getByRole("listbox");
    expect(list).toHaveFocus(); // ≤ 8 questions: no search box, the list takes the keys
    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "ArrowUp" });
    expect(screen.getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(list, { key: "Enter" });
    expect(onJump).toHaveBeenCalledWith(2);
    fireEvent.keyDown(list, { key: "End" });
    fireEvent.keyDown(list, { key: "Enter" });
    expect(onJump).toHaveBeenLastCalledWith(7);
    fireEvent.keyDown(list, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("marks the question on screen and starts there", () => {
    render(<ConversationMap messages={convo()} activeIndex={4} onJump={() => {}} onClose={() => {}} />);
    const opts = screen.getAllByRole("option");
    expect(opts[1]).toHaveAttribute("aria-current", "true");
    expect(opts[1]).toHaveAttribute("aria-selected", "true");
    expect(opts[0]).not.toHaveAttribute("aria-current");
  });

  it("no search box at 8 questions; one past 8 that filters on the whole text", () => {
    const eight: MapMessage[] = [];
    for (let i = 0; i < 8; i++) eight.push(U(`Question number ${i}`), A("ok"));
    const { unmount } = render(<ConversationMap messages={eight} onJump={() => {}} onClose={() => {}} />);
    expect(screen.queryByRole("searchbox")).toBeNull();
    unmount();

    const nine = [...eight, U("Payroll taxes for a new hire\nwith overtime details"), A("sure")];
    const onJump = vi.fn();
    render(<ConversationMap messages={nine} onJump={onJump} onClose={() => {}} />);
    const box = screen.getByRole("searchbox", { name: "Find a question" });
    expect(box).toHaveFocus();
    fireEvent.change(box, { target: { value: "OVERTIME" } }); // second line, any case
    const opts = screen.getAllByRole("option");
    expect(opts).toHaveLength(1);
    expect(opts[0]).toHaveTextContent("Payroll taxes for a new hire");
    // keys work from the box too
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onJump).toHaveBeenCalledWith(16);
    fireEvent.change(box, { target: { value: "zzz" } });
    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.getByText("No questions match.")).toBeInTheDocument();
  });

  it("Esc does not leak to the page", () => {
    const pageKey = vi.fn();
    render(
      <div onKeyDown={(e) => pageKey(e.key)}>
        <ConversationMap messages={convo()} onJump={() => {}} onClose={() => {}} />
      </div>,
    );
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    expect(pageKey).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------- quote

describe("lib/quote", () => {
  it("quoteBlock prefixes every line, keeps one block, trims", () => {
    expect(quoteBlock("  first line\r\nsecond  \n\n\n\nthird  ")).toBe("> first line\n> second\n>\n> third");
    expect(quoteBlock("   ")).toBe("");
  });

  it("quoteBlock caps long text with an honest ellipsis", () => {
    const q = quoteBlock("a".repeat(QUOTE_MAX_CHARS + 500));
    expect(q.startsWith("> ")).toBe(true);
    expect(q.endsWith("…")).toBe(true);
    expect(q.length).toBe(2 + QUOTE_MAX_CHARS + 1);
  });

  it("insertQuote goes after what is typed, as its own paragraph", () => {
    expect(insertQuote("", "> hi")).toBe("> hi\n\n");
    expect(insertQuote("Look at this:  \n", "> hi")).toBe("Look at this:\n\n> hi\n\n");
    expect(insertQuote("keep me", "  ")).toBe("keep me");
  });
});

function selectText(node: Node, start: number, end: number, endNode: Node = node) {
  const range = document.createRange();
  range.setStart(node, start);
  range.setEnd(endNode, end);
  const sel = window.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(range);
  act(() => {
    document.dispatchEvent(new Event("selectionchange"));
  });
}

function Harness({ onQuote }: { onQuote: (t: string) => void }) {
  return (
    <div>
      <p data-testid="foreign" data-quote-source>
        A reply outside the wrapper
      </p>
      <QuoteSelection onQuote={onQuote}>
        <p data-testid="question">My own question text</p>
        <div data-testid="reply" data-quote-source>
          <p data-testid="reply-p1">Hello world from the reply</p>
          <p data-testid="reply-p2">Second paragraph</p>
        </div>
        <div data-testid="reply2" data-quote-source>
          <p data-testid="reply2-p">Another reply</p>
        </div>
      </QuoteSelection>
    </div>
  );
}

const textOf = (id: string) => screen.getByTestId(id).firstChild as Text;

describe("QuoteSelection", () => {
  it("a selection inside a reply offers Quote; pressing it quotes and clears", () => {
    const onQuote = vi.fn();
    render(<Harness onQuote={onQuote} />);
    expect(screen.queryByTestId("quote-selection")).toBeNull();
    selectText(textOf("reply-p1"), 0, 11);
    const btn = screen.getByRole("button", { name: "Quote this in your message" });
    fireEvent.click(btn);
    expect(onQuote).toHaveBeenCalledWith("Hello world");
    expect(window.getSelection()!.isCollapsed || window.getSelection()!.rangeCount === 0).toBe(true);
    expect(screen.queryByTestId("quote-selection")).toBeNull();
  });

  it("works across paragraphs of the SAME reply", () => {
    const onQuote = vi.fn();
    render(<Harness onQuote={onQuote} />);
    selectText(textOf("reply-p1"), 6, 6, textOf("reply-p2"));
    fireEvent.click(screen.getByTestId("quote-selection"));
    expect(onQuote).toHaveBeenCalledTimes(1);
    // Chromium puts a line break between the paragraphs; jsdom glues them.
    expect(onQuote.mock.calls[0][0]).toMatch(/^world from the reply\s*Second$/);
  });

  it("no button outside a reply, across two replies, or in a reply outside the wrapper", () => {
    render(<Harness onQuote={() => {}} />);
    selectText(textOf("question"), 0, 6);
    expect(screen.queryByTestId("quote-selection")).toBeNull();
    selectText(textOf("reply-p2"), 0, 6, textOf("reply2-p"));
    expect(screen.queryByTestId("quote-selection")).toBeNull();
    selectText(textOf("question"), 3, 5, textOf("reply-p1"));
    expect(screen.queryByTestId("quote-selection")).toBeNull();
    selectText(textOf("foreign"), 0, 7);
    expect(screen.queryByTestId("quote-selection")).toBeNull();
  });

  it("hides for a collapsed or blank selection", () => {
    render(<Harness onQuote={() => {}} />);
    selectText(textOf("reply-p1"), 0, 11);
    expect(screen.getByTestId("quote-selection")).toBeInTheDocument();
    selectText(textOf("reply-p1"), 4, 4);
    expect(screen.queryByTestId("quote-selection")).toBeNull();
    selectText(textOf("reply-p1"), 0, 11);
    selectText(textOf("reply-p1"), 5, 6); // a lone space
    expect(screen.queryByTestId("quote-selection")).toBeNull();
  });

  it("hides on scroll", () => {
    render(<Harness onQuote={() => {}} />);
    selectText(textOf("reply-p1"), 0, 11);
    expect(screen.getByTestId("quote-selection")).toBeInTheDocument();
    act(() => {
      screen.getByTestId("reply").dispatchEvent(new Event("scroll"));
    });
    expect(screen.queryByTestId("quote-selection")).toBeNull();
  });

  it("is portaled to the body, outside the wrapper", () => {
    render(<Harness onQuote={() => {}} />);
    selectText(textOf("reply-p1"), 0, 11);
    const btn = screen.getByTestId("quote-selection");
    expect(within(screen.getByTestId("reply")).queryByTestId("quote-selection")).toBeNull();
    expect(btn.parentElement).toBe(document.body);
    expect(btn.style.position).toBe("fixed");
  });
});
