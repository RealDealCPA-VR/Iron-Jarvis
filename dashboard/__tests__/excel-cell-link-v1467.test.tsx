/**
 * v1.467.0: a cell of a workbook that is open in Excel, named in a reply as
 * a link "#excel/<workbook>/<sheet>/<cell>", is a press that shows the cell
 * in Excel (POST /excel/live/goto). Driven through the real Markdown
 * renderer with only the transport mocked.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("@/lib/api", () => ({
  API_BASE: "http://127.0.0.1:8787",
  post: H.post,
}));

import { Markdown } from "@/components/Markdown";
import { EXCEL_SHOW_TITLE, parseExcelCellHref } from "@/lib/excelCellLink";

beforeEach(() => {
  H.post.mockReset();
  H.post.mockResolvedValue({ ok: true });
});
afterEach(cleanup);

describe("reading a cell link", () => {
  it("reads the three parts, decoded, with the cell tidied", () => {
    expect(parseExcelCellHref("#excel/Budget%202026.xlsx/Q1%20Sales/$b$13")).toEqual({
      workbook: "Budget 2026.xlsx",
      sheet: "Q1 Sales",
      cell: "B13",
    });
    expect(parseExcelCellHref("#excel//Data/A1:C9")).toEqual({ workbook: "", sheet: "Data", cell: "A1:C9" });
  });

  it.each([
    "https://example.com/#excel/a/b/A1",
    "#section",
    "#excel/Book.xlsx/Data",
    "#excel/Book.xlsx/Data/B",
    "#excel/Book.xlsx/Data/A1/extra",
    "#excel/Book.xlsx/a%3Ab/A1",
    "#excel/Book.xlsx/%E0%A4%A/A1",
    "#excel/Book.xlsx/" + "s".repeat(32) + "/A1",
  ])("leaves %s as an ordinary link", (href) => {
    expect(parseExcelCellHref(href)).toBeNull();
  });
});

describe("a cell named in a reply", () => {
  it("is a press that asks Excel to show that cell", async () => {
    render(<Markdown content={"The total is in [Sales!B13](#excel/Budget%202026.xlsx/Sales/B13)."} />);
    const press = screen.getByTestId("excel-cell-link");
    expect(press.tagName).toBe("BUTTON");
    expect(press).toHaveTextContent("Sales!B13");
    expect(press).toHaveAttribute("title", EXCEL_SHOW_TITLE);
    expect(screen.queryByRole("link")).toBeNull();
    fireEvent.click(press);
    await waitFor(() =>
      expect(H.post).toHaveBeenCalledWith("/excel/live/goto", { workbook: "Budget 2026.xlsx", sheet: "Sales", cell: "B13" }),
    );
    expect(screen.getByTestId("excel-cell-link-note")).toHaveTextContent("");
  });

  it("says the daemon's own words when Excel cannot show it", async () => {
    H.post.mockRejectedValue(new Error("No open workbook is called 'Budget.xlsx'."));
    render(<Markdown content={"See [Data!A1](#excel/Budget.xlsx/Data/A1)"} />);
    fireEvent.click(screen.getByTestId("excel-cell-link"));
    await waitFor(() =>
      expect(screen.getByTestId("excel-cell-link-note")).toHaveTextContent("No open workbook is called 'Budget.xlsx'."),
    );
    expect(screen.getByTestId("excel-cell-link-note")).toHaveAttribute("role", "status");
  });

  it("leaves every other link a link, and a malformed cell link too", () => {
    render(<Markdown content={"[docs](https://example.com/a) and [bad](#excel/Book.xlsx/Data/notacell)"} />);
    expect(screen.queryByTestId("excel-cell-link")).toBeNull();
    expect(screen.getAllByRole("link")).toHaveLength(2);
    expect(H.post).not.toHaveBeenCalled();
  });
});
