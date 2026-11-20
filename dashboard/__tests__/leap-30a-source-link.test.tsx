/**
 * Idea 30-a: a figure in a reply links to the page, line or cell it was read
 * from (v1.547.0).
 *
 * - "#source/<id>" draws as a press: GET /sources/<id>, then the file opens
 *   beside the chat at its place (the chat page answers OPEN_PLACE_EVENT);
 *   where nothing opens it, the place is said in words.
 * - "#source/none" draws a faint dotted line; its press says there is no
 *   record, and fetches nothing (D-30.1).
 * - Several ids: a chooser, one press per place.
 * - DocPreview `at`: a PDF page as a picture (GET /documents/page-image) with
 *   "How this was worked out" above it, a sheet's cell lit (rows from the
 *   cell's row when it is far down), a text file's line lit.
 * - Copy message keeps the words, not the links.
 *
 * Driven through the real Markdown renderer and the real DocPreview; only the
 * transport is mocked.
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
import { Markdown } from "@/components/Markdown";
import { DocPreview, markFigureInHtml, startFor } from "@/components/chat/DocPreview";
import {
  FILE_CHANGED,
  HOW_WORKED_OUT,
  NO_RECORD_WORDS,
  OPEN_PLACE_EVENT,
  SHOW_PLACE_TITLE,
  atOf,
  figureKey,
  parseSourceHref,
  placeWords,
  withoutPlaceLinks,
  type SourceAt,
} from "@/lib/sourceLink";

const MARK = {
  id: "src_aaaa1111aaaa1111",
  path: "C:\\Clients\\statement.pdf",
  name: "statement.pdf",
  kind: "pdf",
  locator: { page: 7, line: 3 },
  shown: "48,210.00",
  key: "48210",
  context: "Total wages 48,210.00 for the year",
  present: true,
  changed: false,
};

beforeEach(() => {
  H.get.mockReset();
  H.post.mockReset();
});
afterEach(cleanup);

describe("reading a figure link", () => {
  it("reads ids and none, and leaves anything else alone", () => {
    expect(parseSourceHref("#source/src_aaaa1111aaaa1111")).toEqual({ none: false, ids: ["src_aaaa1111aaaa1111"] });
    expect(parseSourceHref("#source/src_aaaa1111aaaa1111,src_bbbb2222bbbb2222")).toEqual({
      none: false,
      ids: ["src_aaaa1111aaaa1111", "src_bbbb2222bbbb2222"],
    });
    expect(parseSourceHref("#source/none")).toEqual({ none: true });
    for (const href of ["#source/", "#source/abc", "#source/src_x/../y", "https://x.test/#source/none", "#excel/a/b/A1"]) {
      expect(parseSourceHref(href)).toBeNull();
    }
  });

  it("says each place in plain words", () => {
    expect(placeWords({ page: 7, line: 3 })).toBe("page 7, line 3");
    expect(placeWords({ sheet: "Summary", cell: "C7" })).toBe("cell C7 on Summary");
    expect(placeWords({ table: 1, row: 2, col: 2 })).toBe("table 1, row 2, column 2");
    expect(placeWords({ paragraph: 4 })).toBe("paragraph 4");
    expect(placeWords({ line: 3, col: 2 })).toBe("line 3, column 2");
    // A CSV record: its row, as the preview shows the file.
    expect(placeWords({ row: 3, col: 2 })).toBe("row 3, column 2");
  });

  it("copies the words, never the links", () => {
    expect(withoutPlaceLinks("Total [48,210](#source/src_aaaa1111aaaa1111) and [73,519](#source/none).")).toBe(
      "Total 48,210 and 73,519.",
    );
    expect(withoutPlaceLinks("See [the site](https://x.test).")).toBe("See [the site](https://x.test).");
    expect(withoutPlaceLinks("A [1,200 [net]](<#source/src_aaaa1111aaaa1111>) b.")).toBe("A 1,200 [net] b.");
  });

  it("compares a figure the way the daemon does (the shared examples)", () => {
    const pairs = JSON.parse(
      readFileSync(join(process.cwd(), "__tests__", "fixtures", "figure-keys-v1547.json"), "utf-8"),
    ) as [string, string][];
    expect(pairs.length).toBeGreaterThanOrEqual(20);
    for (const [text, key] of pairs) expect([text, figureKey(text)]).toEqual([text, key]);
  });
});

describe("a figure in a reply", () => {
  it("opens its file at its place beside the chat", async () => {
    H.get.mockResolvedValue({ marks: [MARK] });
    const seen: { path: string; at: SourceAt }[] = [];
    const answer = (e: Event) => {
      seen.push((e as CustomEvent).detail);
      e.preventDefault();
    };
    window.addEventListener(OPEN_PLACE_EVENT, answer);
    try {
      render(<Markdown content={"Wages were $[48,210.00](#source/src_aaaa1111aaaa1111) last year."} />);
      const press = screen.getByTestId("source-link");
      expect(press.tagName).toBe("BUTTON");
      expect(press).toHaveTextContent("48,210.00");
      expect(press).toHaveAttribute("title", SHOW_PLACE_TITLE);
      expect(screen.queryByRole("link")).toBeNull();
      fireEvent.click(press);
      await waitFor(() => expect(seen).toHaveLength(1));
      expect(H.get).toHaveBeenCalledWith("/sources/src_aaaa1111aaaa1111");
      expect(seen[0].path).toBe(MARK.path);
      expect(seen[0].at).toMatchObject({ page: 7, line: 3, shown: "48,210.00", context: MARK.context, id: MARK.id });
      expect(screen.getByTestId("source-link-note").textContent).toBe("");
    } finally {
      window.removeEventListener(OPEN_PLACE_EVENT, answer);
    }
  });

  it("says the place in words where nothing opens the file", async () => {
    H.get.mockResolvedValue({ marks: [{ ...MARK, changed: true }] });
    render(<Markdown content={"[48,210.00](#source/src_aaaa1111aaaa1111)"} />);
    fireEvent.click(screen.getByTestId("source-link"));
    await waitFor(() =>
      expect(screen.getByTestId("source-link-note")).toHaveTextContent(
        "Read from page 7, line 3 of statement.pdf.",
      ),
    );
    expect(screen.getByTestId("source-link-note")).toHaveTextContent("The file has changed since it was read.");
  });

  it("offers a chooser when the value is in several places", async () => {
    // (Both records hold the figure's own value: the page opens no other.)
    const rent = { ...MARK, shown: "1,200", key: "1200" };
    H.get.mockResolvedValue({
      marks: [rent, { ...rent, id: "src_bbbb2222bbbb2222", path: "C:\\b.xlsx", name: "b.xlsx", kind: "sheet", locator: { sheet: "S", cell: "B2" } }],
    });
    const seen: string[] = [];
    const answer = (e: Event) => {
      seen.push((e as CustomEvent).detail.path);
      e.preventDefault();
    };
    window.addEventListener(OPEN_PLACE_EVENT, answer);
    try {
      render(<Markdown content={"[1,200](#source/src_aaaa1111aaaa1111,src_bbbb2222bbbb2222)"} />);
      fireEvent.click(screen.getByTestId("source-link"));
      const choices = await screen.findByTestId("source-link-choices");
      expect(choices).toHaveTextContent("In 2 places:");
      expect(choices).toHaveTextContent("statement.pdf, page 7, line 3");
      fireEvent.click(screen.getByRole("button", { name: "b.xlsx, cell B2 on S" }));
      expect(seen).toEqual(["C:\\b.xlsx"]);
      expect(screen.queryByTestId("source-link-choices")).toBeNull();
    } finally {
      window.removeEventListener(OPEN_PLACE_EVENT, answer);
    }
  });

  it("opens nothing when the record holds another value than the figure's words", async () => {
    // Review B2: a link a model managed to write, naming a real record of
    // another figure. The page compares the words with the record.
    H.get.mockResolvedValue({ marks: [MARK] });
    const seen: unknown[] = [];
    const answer = (e: Event) => {
      seen.push((e as CustomEvent).detail);
      e.preventDefault();
    };
    window.addEventListener(OPEN_PLACE_EVENT, answer);
    try {
      render(<Markdown content={"Bonus [99,999](#source/src_aaaa1111aaaa1111)."} />);
      fireEvent.click(screen.getByTestId("source-link"));
      await waitFor(() => expect(screen.getByTestId("source-link-note")).toHaveTextContent(NO_RECORD_WORDS));
      expect(seen).toEqual([]);
      expect(screen.getByTestId("source-link-note")).not.toHaveTextContent("statement.pdf");
    } finally {
      window.removeEventListener(OPEN_PLACE_EVENT, answer);
    }
  });

  it("carries a changed file to the panel", () => {
    expect(atOf({ ...MARK, changed: true }).changed).toBe(true);
    expect(atOf(MARK).changed).toBeUndefined();
  });

  it("with no record, a faint dotted line whose press says so", () => {
    render(<Markdown content={"About [48.2k](#source/none) in all."} />);
    const press = screen.getByTestId("source-link-none");
    expect(press.className).toMatch(/decoration-dotted/);
    expect(press.className).not.toMatch(/text-accent/);
    fireEvent.click(press);
    expect(screen.getByTestId("source-link-note")).toHaveTextContent(NO_RECORD_WORDS);
    expect(H.get).not.toHaveBeenCalled();
    fireEvent.click(press);
    expect(screen.getByTestId("source-link-note").textContent).toBe("");
  });
});

describe("the file, opened at the figure's place", () => {
  function previewReturns(data: Record<string, unknown>) {
    H.get.mockImplementation(async (url: string) => {
      if (url.startsWith("/documents/preview")) return data;
      if (url.startsWith("/documents/siblings")) return { files: [] };
      if (url.startsWith("/documents/places")) return { places: [] };
      throw new Error(`unexpected ${url}`);
    });
  }

  it("draws the PDF page with the line boxed, and says how it was worked out", async () => {
    previewReturns({ kind: "pdf", name: "statement.pdf", path: MARK.path, suffix: ".pdf" });
    const at: SourceAt = { page: 7, line: 3, shown: MARK.shown, context: MARK.context, id: MARK.id };
    render(<DocPreview path={MARK.path} at={at} onClose={() => {}} />);
    const img = await screen.findByTestId("doc-preview-page-image");
    const src = img.getAttribute("src") ?? "";
    // Asked for by the record's id: no line of the file in the address.
    expect(src).toBe("http://api.test/documents/page-image?mark=src_aaaa1111aaaa1111&token=tok");
    const place = screen.getByTestId("doc-preview-place");
    expect(place).toHaveTextContent(HOW_WORKED_OUT);
    expect(place).toHaveTextContent("Read from page 7, line 3 of statement.pdf.");
    expect(place.querySelector("mark")?.textContent).toBe("48,210.00");
    fireEvent.click(screen.getByTestId("doc-preview-whole-file"));
    const frame = screen.getByTitle("Preview of statement.pdf");
    expect(frame.getAttribute("src")).toMatch(/#page=7$/);
  });

  it("lights the cell on its sheet", async () => {
    previewReturns({
      kind: "sheet", name: "b.xlsx", path: "C:\\b.xlsx", suffix: ".xlsx", sheets: ["Summary"], sheet: "Summary",
      rows: [["Item", "Q2", "Q3"], ["Rent", "1200", "1250"], [], [], [], [], ["Travel", "", "48210.5"]],
      first_row: 1,
    });
    render(<DocPreview path={"C:\\b.xlsx"} at={{ sheet: "Summary", cell: "C7", shown: "48210.5" }} onClose={() => {}} />);
    const lit = await screen.findByTestId("doc-preview-lit-cell");
    expect(lit).toHaveTextContent("48210.5");
    expect(H.get).toHaveBeenCalledWith("/documents/preview?path=C%3A%5Cb.xlsx&sheet=Summary");
  });

  it("asks for the rows around a cell far down the sheet", async () => {
    expect(startFor({ cell: "C250" }, "C:\\b.xlsx")).toBe(240);
    expect(startFor({ cell: "C7" }, "C:\\b.xlsx")).toBe(1);
    expect(startFor({ line: 300, col: 2 }, "C:\\l.csv")).toBe(290);
    expect(startFor({ page: 300, line: 2 }, "C:\\s.pdf")).toBe(1);
    previewReturns({
      kind: "sheet", name: "b.xlsx", path: "C:\\b.xlsx", suffix: ".xlsx", sheets: ["Summary"], sheet: "Summary",
      rows: Array.from({ length: 20 }, (_, i) => [`row ${240 + i}`, "", String((240 + i) * 10)]),
      first_row: 240, truncated: true,
    });
    render(<DocPreview path={"C:\\b.xlsx"} at={{ sheet: "Summary", cell: "C250" }} onClose={() => {}} />);
    const lit = await screen.findByTestId("doc-preview-lit-cell");
    expect(lit).toHaveTextContent("2500");
    expect(H.get).toHaveBeenCalledWith("/documents/preview?path=C%3A%5Cb.xlsx&sheet=Summary&start=240");
    expect(screen.getByText("Rows 240–259")).toBeTruthy();
  });

  it("lights the line of a text file", async () => {
    previewReturns({ kind: "text", name: "n.txt", path: "C:\\n.txt", suffix: ".txt", content: "first\nsecond line has 6,400 in it\nthird" });
    render(<DocPreview path={"C:\\n.txt"} at={{ line: 2, shown: "6,400" }} onClose={() => {}} />);
    const lit = await screen.findByTestId("doc-preview-lit-line");
    expect(lit).toHaveTextContent("second line has 6,400 in it");
  });

  it("lights the figure in a Word page, never inside a tag", () => {
    expect(markFigureInHtml('<p title="7,450">Fee $7,450 due</p>', "7,450")).toBe(
      '<p title="7,450">Fee $<mark class="ij-at">7,450</mark> due</p>',
    );
    expect(markFigureInHtml("<p>x</p>", undefined)).toBe("<p>x</p>");
    // Only the whole figure: never "120" inside "1,120" or "120.50".
    expect(markFigureInHtml("<p>1,120 and 120.50 and 120.</p>", "120")).toBe(
      '<p>1,120 and 120.50 and <mark class="ij-at">120</mark>.</p>',
    );
  });

  it("lights a CSV record's cell by its row", async () => {
    previewReturns({
      kind: "sheet", name: "l.csv", path: "C:\\l.csv", suffix: ".csv", sheets: ["CSV"], sheet: "CSV",
      rows: [["Payee", "Memo", "Amount"], ["Acme", "rent\nfor March", "1200.00"], ["Bolt", "tools", "48210.50"]],
      first_row: 1,
    });
    render(<DocPreview path={"C:\\l.csv"} at={{ row: 3, col: 3, shown: "48210.50" }} onClose={() => {}} />);
    const lit = await screen.findByTestId("doc-preview-lit-cell");
    expect(lit).toHaveTextContent("48210.50");
  });

  it("says a cell past the columns shown is past the part shown", async () => {
    previewReturns({
      kind: "sheet", name: "b.xlsx", path: "C:\\b.xlsx", suffix: ".xlsx", sheets: ["S"], sheet: "S",
      rows: [Array.from({ length: 30 }, (_, i) => `h${i + 1}`)],
      first_row: 1,
    });
    render(<DocPreview path={"C:\\b.xlsx"} at={{ sheet: "S", cell: "AF1" }} onClose={() => {}} />);
    expect(await screen.findByTestId("doc-preview-place-past")).toHaveTextContent(
      "Cell AF1 is past the part shown here.",
    );
    expect(screen.queryByTestId("doc-preview-lit-cell")).toBeNull();
  });

  it("says the file changed in the panel, and lights no cell or line by position", async () => {
    // Review A9 / B5: on a phone the panel covers the chat's note.
    previewReturns({
      kind: "sheet", name: "b.xlsx", path: "C:\\b.xlsx", suffix: ".xlsx", sheets: ["Summary"], sheet: "Summary",
      rows: [["Item", "Q2", "Q3"], ["Rent", "1200", "999"]],
      first_row: 1,
    });
    render(
      <DocPreview path={"C:\\b.xlsx"} at={{ sheet: "Summary", cell: "C2", shown: "1250", changed: true }} onClose={() => {}} />,
    );
    expect(await screen.findByTestId("doc-preview-place-changed")).toHaveTextContent(FILE_CHANGED);
    await screen.findByText("999");
    expect(screen.queryByTestId("doc-preview-lit-cell")).toBeNull();
    cleanup();
    previewReturns({ kind: "text", name: "n.txt", path: "C:\\n.txt", suffix: ".txt", content: "first\nnow other words\nthird" });
    render(<DocPreview path={"C:\\n.txt"} at={{ line: 2, shown: "6,400", changed: true }} onClose={() => {}} />);
    await screen.findByText(/now other words/);
    expect(screen.getByTestId("doc-preview-place-changed")).toHaveTextContent(FILE_CHANGED);
    expect(screen.queryByTestId("doc-preview-lit-line")).toBeNull();
  });

  it("lights the line of a Markdown file in its plain lines", async () => {
    previewReturns({ kind: "markdown", name: "n.md", path: "C:\\n.md", suffix: ".md", content: "# Title\n\nTotal 6,400 here" });
    render(<DocPreview path={"C:\\n.md"} at={{ line: 3, shown: "6,400" }} onClose={() => {}} />);
    const lit = await screen.findByTestId("doc-preview-lit-line");
    expect(lit).toHaveTextContent("Total 6,400 here");
  });
});
