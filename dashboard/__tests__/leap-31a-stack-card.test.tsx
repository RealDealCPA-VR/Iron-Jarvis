/**
 * Idea 31-a: a scanned pile becomes named documents — the plan card.
 *
 * - The card rides the reply's `config_cards` (kind "stack") and is
 *   whitelisted: a bad plan id, a page past the scan's end or junk is dropped.
 * - The user moves a page to another document (press it, then "Move page N
 *   here", or drag it), renames, and picks a folder INSIDE the chat's folder;
 *   no page is ever lost by an edit, and Confirm refuses a plan that lost one.
 * - Confirm is the one main button: it posts the plan as the user left it to
 *   /documents/stack/<id>/apply; the card then lists the files and Undo posts
 *   /documents/stack/<id>/undo.
 * - D-31.2: pages this PC could not read are said, and the offer names its
 *   model ("Read 2 more pages with Claude?"); only the press posts read-more,
 *   for that model. With no model that reads pictures, no offer.
 * - The words are in VOCABULARY.md; at 390 px every row wraps (no fixed width).
 *
 * Driven through the real ConfigCards and StackPlanCard; only the transport
 * is mocked.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  }
  return { API_BASE: "http://api.test", get: H.get, post: H.post, ApiError, ijToken: () => "tok" };
});

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ConfigCards } from "@/components/chat/ConfigCards";
import { StackPlanCard } from "@/components/chat/StackPlanCard";
import { cardId, decodeConfigCards, type ConfigCard } from "@/lib/configCards";
import {
  applyBody,
  decodeStackCard,
  everyPageOnce,
  fileName,
  folderInside,
  movePage,
  offerWords,
  spanWords,
  undoNote,
  type StackCard,
} from "@/lib/stackPlan";

const PLAN = "stk_0123456789abcdef";

function raw(extra: Record<string, unknown> = {}) {
  return {
    kind: "stack",
    plan_id: PLAN,
    source: "C:\\Work\\scan.pdf",
    source_name: "scan.pdf",
    root: "C:\\Work",
    page_count: 10,
    documents: [
      { pages: [1, 2, 3, 4], name: "2026-03 Bank statement.pdf", folder: "Bank statements", blank: [], unsure: false },
      { pages: [5, 6, 7], name: "2026-03 Electricity bill.pdf", folder: "Bills", blank: [7], unsure: false },
      { pages: [8], name: "Not sure (page 8).pdf", folder: "", blank: [], unsure: true },
      { pages: [9, 10], name: "2026-04 Water bill.pdf", folder: "Bills", blank: [], unsure: false },
    ],
    unread: [],
    read_here: 3,
    read_by_model: 0,
    read_by: "",
    snippets: { "1": "First Harbor Bank statement", "5": "City Power and Light" },
    offer: null,
    applied: false,
    ...extra,
  };
}

function card(extra: Record<string, unknown> = {}): StackCard {
  const c = decodeStackCard(raw(extra));
  if (!c) throw new Error("fixture did not decode");
  return c;
}

beforeEach(() => {
  H.get.mockReset();
  H.post.mockReset();
});
afterEach(cleanup);

describe("the card's wire shape is whitelisted", () => {
  it("rides config_cards and keeps only a well-formed plan", () => {
    const cards = decodeConfigCards([raw(), { ...raw(), plan_id: "../etc" }, { ...raw(), plan_id: "stk_XYZ" }, { kind: "nope" }]);
    expect(cards).toHaveLength(1);
    expect(cards[0].kind).toBe("stack");
    expect(cardId(cards[0] as ConfigCard)).toBe(`stack:${PLAN}`);
  });
  it("drops a page the scan does not have and junk fields", () => {
    const c = decodeStackCard(raw({
      documents: [{ pages: [1, 2, 99, -1, "3"], name: "A.pdf", folder: "x", blank: [] }, { pages: [], name: "B.pdf" }],
      snippets: { "1": "ok", "../x": "no", "2": 5 },
    }))!;
    expect(c.documents).toEqual([{ pages: [1, 2], name: "A.pdf", folder: "x", blank: [], unsure: false }]);
    expect(c.snippets).toEqual({ "1": "ok" });
    expect(decodeStackCard({ ...raw(), page_count: 0 })).toBeNull();
  });
});

describe("the edits never lose a page", () => {
  it("moves a page, drops a document left empty, and Confirm's check holds", () => {
    const c = card();
    let docs = movePage(c.documents, 8, 0);
    expect(docs.map((d) => d.pages)).toEqual([[1, 2, 3, 4, 8], [5, 6, 7], [9, 10]]);
    expect(everyPageOnce(docs, 10)).toBe(true);
    docs = movePage(docs, 7, 0);
    expect(docs[0].pages).toEqual([1, 2, 3, 4, 7, 8]);
    expect(docs[1].blank).toEqual([]);
    expect(movePage(docs, 99, 1)).toBe(docs);
    expect(everyPageOnce([{ ...docs[0], pages: [1] }], 10)).toBe(false);
  });
  it("a folder is one inside the chat's folder, or nothing", () => {
    expect(folderInside("C:\\Work", "C:\\Work")).toBe("");
    expect(folderInside("C:\\Work", "c:\\work\\Bills\\2026")).toBe("Bills/2026");
    expect(folderInside("C:\\Work", "C:\\Workshop")).toBeNull();
    expect(folderInside("C:\\Work", "D:\\Other")).toBeNull();
    expect(fileName(' my: "bill" ')).toBe("my bill.pdf");
    expect(fileName("x.PDF")).toBe("x.pdf");
    expect(spanWords([41, 42])).toBe("pages 41-42");
    expect(spanWords([7])).toBe("page 7");
  });
});

describe("the card", () => {
  it("moves a page by press, renames, and Confirm posts the plan as the user left it", async () => {
    const settled: StackCard[] = [];
    H.post.mockResolvedValue({
      files: [{ path: "C:\\Work\\Bank statements\\Bank March.pdf", pages: 5, name: "Bank March.pdf", folder: "Bank statements" }],
      card: { ...raw(), applied: true },
    });
    render(<StackPlanCard card={card()} onSettle={(n) => settled.push(n)} />);
    expect(screen.getByText("Sort scan.pdf")).toBeTruthy();
    expect(screen.getByDisplayValue("Not sure (page 8).pdf")).toBeTruthy();
    expect(screen.getByText(/Nothing is written until you confirm/)).toBeTruthy();
    // The blank page is marked as such.
    expect(screen.getByTestId("stack-page-7").getAttribute("title")).toMatch(/Blank page/);
    // Press page 8, then move it into the first document.
    fireEvent.click(screen.getByTestId("stack-page-8"));
    fireEvent.click(screen.getByTestId("stack-move-here-0"));
    expect(screen.queryByDisplayValue("Not sure (page 8).pdf")).toBeNull();
    const name = screen.getByDisplayValue("2026-03 Bank statement.pdf");
    fireEvent.change(name, { target: { value: "Bank March" } });
    fireEvent.blur(name);
    fireEvent.click(screen.getByTestId("stack-confirm"));
    await waitFor(() => expect(settled).toHaveLength(1));
    const [path, body] = H.post.mock.calls[0];
    expect(path).toBe(`/documents/stack/${PLAN}/apply`);
    expect(body.documents[0]).toEqual({ pages: [1, 2, 3, 4, 8], name: "Bank March.pdf", folder: "Bank statements", unsure: false });
    expect(body.documents).toHaveLength(3);
    expect(settled[0].applied).toBe(true);
    expect(settled[0].written?.[0].name).toBe("Bank March.pdf");
  });

  it("drags a page onto another document", () => {
    render(<StackPlanCard card={card()} onSettle={() => {}} />);
    const data = new Map<string, string>();
    const dataTransfer = {
      setData: (k: string, v: string) => data.set(k, v),
      getData: (k: string) => data.get(k) ?? "",
      effectAllowed: "", dropEffect: "",
    };
    fireEvent.dragStart(screen.getByTestId("stack-page-9"), { dataTransfer });
    fireEvent.dragOver(screen.getByTestId("stack-doc-1"), { dataTransfer });
    fireEvent.drop(screen.getByTestId("stack-doc-1"), { dataTransfer });
    expect(screen.getByTestId("stack-doc-1").textContent).toContain("pages 5-7, 9");
  });

  it("a written plan lists its files and one Undo takes them back", async () => {
    const settled: StackCard[] = [];
    H.post.mockResolvedValue({ output: "undo: removed 1 created file(s)", card: { ...raw(), applied: false } });
    const written = card({
      applied: true,
      written: [{ path: "C:\\Work\\Bills\\a.pdf", pages: 3, name: "a.pdf", folder: "Bills" }],
    });
    render(<StackPlanCard card={written} onSettle={(n) => settled.push(n)} />);
    expect(screen.getByTestId("stack-card-written").textContent).toContain("Wrote 1 document from scan.pdf");
    expect(screen.getByText("Bills/a.pdf")).toBeTruthy();
    fireEvent.click(screen.getByText("Undo"));
    await waitFor(() => expect(settled).toHaveLength(1));
    expect(H.post.mock.calls[0][0]).toBe(`/documents/stack/${PLAN}/undo`);
    expect(settled[0].undone).toBe(true);
    cleanup();
    render(<StackPlanCard card={settled[0]} onSettle={() => {}} />);
    expect(screen.getByTestId("stack-card-undone").textContent).toContain("scan.pdf is as it was");
  });

  it("an Undo that found a file already gone says so in the card's words, never the journal's (review 2)", async () => {
    const plain = "7 documents were removed, 1 was already gone and 3 folders it had made were removed. scan.pdf is as it was.";
    expect(undoNote(plain)).toBe(plain);
    expect(undoNote("8 documents were removed and 3 folders it had made were removed. scan.pdf is as it was.")).toBe("");
    expect(undoNote("undo: removed 8 created file(s) (1 already gone); the scan was left as it was")).toBe("");
    expect(undoNote("undo: removed 2 created file(s) (2 moved to the trash, in C:\\H\\trash)")).toBe("");
    expect(undoNote(undefined)).toBe("");

    const settled: StackCard[] = [];
    H.post.mockResolvedValue({ output: plain, card: { ...raw(), applied: false } });
    const written = card({ applied: true, written: [{ path: "C:\\Work\\Bills\\a.pdf", pages: 3, name: "a.pdf", folder: "Bills" }] });
    render(<StackPlanCard card={written} onSettle={(n) => settled.push(n)} />);
    fireEvent.click(screen.getByText("Undo"));
    await waitFor(() => expect(settled).toHaveLength(1));
    cleanup();
    render(<StackPlanCard card={settled[0]} onSettle={() => {}} />);
    const said = screen.getByTestId("stack-card-undone").textContent ?? "";
    expect(said).toContain("Undone — 7 documents were removed, 1 was already gone");
    expect(said).not.toMatch(/undo:|file\(s\)/);
  });

  it("names the model before any unread page is sent, and only the press sends it (D-31.2)", async () => {
    const offered = card({ unread: [8, 9], offer: { provider: "anthropic", model: null, name: "Claude" } });
    expect(offerWords(offered)).toBe("Read 2 more pages with Claude?");
    H.post.mockResolvedValue({ read: 2, changed: false, card: { ...raw(), read_by_model: 2, read_by: "Claude" } });
    const settled: StackCard[] = [];
    render(<StackPlanCard card={offered} onSettle={(n) => settled.push(n)} />);
    expect(screen.getByTestId("stack-unread").textContent).toContain("2 pages (pages 8-9) could not be read on this PC.");
    expect(H.post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("stack-read-more"));
    await waitFor(() => expect(settled).toHaveLength(1));
    expect(H.post.mock.calls[0][0]).toBe(`/documents/stack/${PLAN}/read-more`);
    expect(H.post.mock.calls[0][1]).toEqual({ provider: "anthropic", thread_id: "", local_only: false });
    cleanup();
    render(<StackPlanCard card={card({ unread: [8] })} onSettle={() => {}} />);
    expect(screen.queryByTestId("stack-read-more")).toBeNull();
    expect(screen.getByText("No connected model can read pictures.")).toBeTruthy();
  });

  it("one press sends at most what the offer says, and the chat's Local only rides it", async () => {
    const many = Array.from({ length: 60 }, (_, k) => k + 1);
    const big = decodeStackCard(raw({
      page_count: 60,
      documents: [{ pages: many, name: "Not sure (pages 1-60).pdf", folder: "", blank: [], unsure: true }],
      unread: many,
      offer: { provider: "anthropic", model: null, name: "Claude", pages: 20 },
    }))!;
    expect(offerWords(big)).toBe("Read 20 of 60 more pages with Claude?");
    // An offer from an older daemon (no count) is the whole number.
    expect(offerWords(decodeStackCard(raw({ unread: [8, 9], offer: { provider: "a", name: "Claude" } }))!)).toBe(
      "Read 2 more pages with Claude?",
    );
    H.post.mockResolvedValue({ read: 20, changed: false, stopped: "", card: raw() });
    render(<StackPlanCard card={big} onSettle={() => {}} scope={() => ({ threadId: "thr_9", localOnly: true })} />);
    fireEvent.click(screen.getByTestId("stack-read-more"));
    await waitFor(() => expect(H.post).toHaveBeenCalledTimes(1));
    expect(H.post.mock.calls[0][1]).toEqual({ provider: "anthropic", thread_id: "thr_9", local_only: true });
  });

  it("a press that fails after some pages were read says what was kept, never 'nothing changed'", async () => {
    const offered = card({ unread: [8, 9], offer: { provider: "anthropic", model: null, name: "Claude" } });
    H.post.mockRejectedValue(new Error("The request took too long."));
    H.get.mockResolvedValue({ card: { ...raw(), unread: [9], read_by_model: 1, read_by: "Claude" } });
    const settled: StackCard[] = [];
    render(<StackPlanCard card={offered} onSettle={(n) => settled.push(n)} />);
    fireEvent.click(screen.getByTestId("stack-read-more"));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("1 page was read and kept before this stopped."));
    expect(H.get.mock.calls[0][0]).toBe(`/documents/stack/${PLAN}`);
    expect(screen.getByRole("alert").textContent).toContain("The request took too long.");
    expect(screen.queryByText(/Nothing was changed on the plan/)).toBeNull();
    expect(settled[0].unread).toEqual([9]);
  });

  it("a new plan after a read says it replaced the user's moves", async () => {
    const offered = card({ unread: [8], offer: { provider: "anthropic", model: null, name: "Claude" } });
    const replanned = {
      ...raw(),
      documents: [
        { pages: [1, 2, 3, 4, 8], name: "2026-03 Bank statement.pdf", folder: "Bank statements", blank: [], unsure: false },
        { pages: [5, 6, 7], name: "2026-03 Electricity bill.pdf", folder: "Bills", blank: [7], unsure: false },
        { pages: [9, 10], name: "2026-04 Water bill.pdf", folder: "Bills", blank: [], unsure: false },
      ],
    };
    H.post.mockResolvedValue({ read: 1, changed: false, stopped: "", card: replanned });
    render(<StackPlanCard card={offered} onSettle={() => {}} />);
    fireEvent.change(screen.getByDisplayValue("2026-04 Water bill.pdf"), { target: { value: "Water" } });
    fireEvent.click(screen.getByTestId("stack-read-more"));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("your moves and new names were replaced"));
    expect(screen.queryByDisplayValue("Not sure (page 8).pdf")).toBeNull();
  });

  it("a Confirm whose answer never came reads the card before it says what happened (review 3)", async () => {
    const lost = Object.assign(new Error("Iron Jarvis is not responding."), { status: 0 });
    // The daemon wrote the documents; only the answer was lost.
    H.post.mockRejectedValueOnce(lost);
    H.get.mockResolvedValueOnce({ card: { ...raw(), applied: true } });
    const settled: StackCard[] = [];
    render(<StackPlanCard card={card()} onSettle={(n) => settled.push(n)} />);
    fireEvent.click(screen.getByTestId("stack-confirm"));
    await waitFor(() => expect(settled).toHaveLength(1));
    expect(H.get.mock.calls[0][0]).toBe(`/documents/stack/${PLAN}`);
    expect(settled[0].applied).toBe(true);
    expect(screen.queryByText(/Nothing was changed/)).toBeNull();
    cleanup();
    render(<StackPlanCard card={settled[0]} onSettle={() => {}} />);
    expect(screen.getByTestId("stack-card-written").textContent).toContain("Wrote the documents from scan.pdf");
    cleanup();
    // Not written yet, and no answer: it may still be writing, never "nothing changed".
    H.post.mockRejectedValueOnce(lost);
    H.get.mockResolvedValueOnce({ card: raw() });
    render(<StackPlanCard card={card()} onSettle={() => {}} />);
    fireEvent.click(screen.getByTestId("stack-confirm"));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("may still be being written"));
    expect(screen.queryByText(/Nothing was changed/)).toBeNull();
    cleanup();
    // A refusal in words is said as it came.
    H.post.mockRejectedValueOnce(Object.assign(new Error("The scan changed since the plan was made."), { status: 409 }));
    H.get.mockResolvedValueOnce({ card: raw() });
    render(<StackPlanCard card={card()} onSettle={() => {}} />);
    fireEvent.click(screen.getByTestId("stack-confirm"));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("The scan changed since the plan was made."));
  });

  it("Confirm refuses a plan that lost a page and posts nothing", async () => {
    const lost = card({ documents: [{ pages: [1, 2, 3], name: "A.pdf", folder: "", blank: [], unsure: false }] });
    render(<StackPlanCard card={lost} onSettle={() => {}} />);
    fireEvent.click(screen.getByTestId("stack-confirm"));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Every page goes in one document"));
    expect(H.post).not.toHaveBeenCalled();
  });

  it("ConfigCards draws the plan card for a kind \"stack\"", () => {
    render(<ConfigCards cards={decodeConfigCards([raw()])} onSettle={() => {}} />);
    expect(screen.getByTestId("stack-card")).toBeTruthy();
    expect(applyBody(card().documents).documents).toHaveLength(4);
  });
});

describe("calm and plain", () => {
  const DASH = join(__dirname, "..");
  it("the new words are in VOCABULARY.md", () => {
    const vocab = readFileSync(join(DASH, "..", "VOCABULARY.md"), "utf-8");
    for (const w of ["Not sure (pages 41-42).pdf", "Read 4 more pages with", "Read 20 of 60 more pages with", "Sort this stack"]) {
      expect(vocab).toContain(w);
    }
  });
  it("one main button, and nothing wider than a phone", () => {
    const src = readFileSync(join(DASH, "components", "chat", "StackPlanCard.tsx"), "utf-8");
    expect(src.match(/variant="primary"/g)).toHaveLength(1);
    expect(src).not.toMatch(/\bw-\[\d{3,}px\]|\bmin-w-\[(?:[4-9]\d\d|\d{4,})px\]/);
    expect(src).toContain("flex-wrap");
  });
  it("a scanner's long file name with no spaces wraps instead of widening the card", () => {
    const long = "Client_Family_Trust_2025_Scanned_Documents_From_The_Office_Scanner.pdf";
    render(<StackPlanCard card={card({ source_name: long })} onSettle={() => {}} />);
    const heading = screen.getByText(`Sort ${long}`);
    expect(heading.className).toContain("[overflow-wrap:anywhere]");
    expect(heading.className).toContain("min-w-0");
    const footer = screen.getByText(/Nothing is written until you confirm/);
    expect(footer.className).toContain("[overflow-wrap:anywhere]");
  });
});
