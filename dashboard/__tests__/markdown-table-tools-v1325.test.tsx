/**
 * v1.325.0 (wave D, FE-D1): every markdown table gets quiet tools — Copy as
 * CSV, Download CSV — and a header press sorts its column (asc → desc →
 * original), numeric-aware, blanks last, KEEPING cell formatting (the row
 * elements are reordered, never flattened).
 */
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({ API_BASE: "http://127.0.0.1:8787", ijToken: () => "" }));

import { csvField, nextSort, rowsToCsv, sortKey, sortOrder } from "@/lib/tableData";
import { Markdown } from "@/components/Markdown";
import { cleanHtml } from "@/components/chat/DraftCard";

describe("sortKey — what a cell sorts as", () => {
  it.each([
    ["$1,234.50", 1234.5],
    ["12%", 12],
    ["(300)", -300],
    ["($1,000)", -1000],
    ["-$5", -5],
    ["−7", -7],
    ["1e3", 1000],
    [" 42 ", 42],
    ["0.5", 0.5],
    [".5", 0.5],
    ["€20", 20],
  ])("%s is the number %d", (cell, n) => {
    expect(sortKey(cell)).toEqual({ kind: "num", n });
  });

  it.each(["", "   ", "-", "—", "–"])("%j is blank", (cell) => {
    expect(sortKey(cell)).toEqual({ kind: "blank" });
  });

  it.each(["Apples", "1,23", "12 apples", "v2.0.1", "1.234,5"])("%s is text", (cell) => {
    expect(sortKey(cell).kind).toBe("text");
  });
});

describe("sortOrder", () => {
  const cells = ["$1,200", "", "(300)", "12%", "abc", "1,000", "Banana"];
  it("ascending: numbers by value, then text, blanks last", () => {
    expect(sortOrder(cells, "asc").map((i) => cells[i])).toEqual([
      "(300)",
      "12%",
      "1,000",
      "$1,200",
      "abc",
      "Banana",
      "",
    ]);
  });
  it("descending keeps blanks LAST", () => {
    expect(sortOrder(cells, "desc").map((i) => cells[i])).toEqual([
      "Banana",
      "abc",
      "$1,200",
      "1,000",
      "12%",
      "(300)",
      "",
    ]);
  });
  it("is stable for equal keys", () => {
    expect(sortOrder(["b", "a", "B", "a"], "asc")).toEqual([1, 3, 0, 2]);
  });
  it("orders numbers numerically, not as strings", () => {
    expect(sortOrder(["10", "9", "100"], "asc")).toEqual([1, 0, 2]);
  });
});

describe("nextSort", () => {
  it("cycles asc → desc → original, and a new column starts at asc", () => {
    const a = nextSort(null, 1);
    expect(a).toEqual({ col: 1, dir: "asc" });
    const d = nextSort(a, 1);
    expect(d).toEqual({ col: 1, dir: "desc" });
    expect(nextSort(d, 1)).toBeNull();
    expect(nextSort(d, 2)).toEqual({ col: 2, dir: "asc" });
  });
});

describe("rows → CSV", () => {
  it("quotes per RFC 4180 and joins with CRLF", () => {
    expect(rowsToCsv([["a", "b,c"], ['q"t', "line\nbreak"]])).toBe('a,"b,c"\r\n"q""t","line\nbreak"');
  });
  it("guards formulas but leaves plain numbers alone", () => {
    expect(csvField("=SUM(A1)")).toBe("'=SUM(A1)");
    expect(csvField("+1+1")).toBe("'+1+1");
    expect(csvField("-2+3")).toBe("'-2+3");
    expect(csvField("@me")).toBe("'@me");
    expect(csvField("\tx")).toBe("'\tx");
    expect(csvField("-300")).toBe("-300");
    expect(csvField("+12.5")).toBe("+12.5");
    expect(csvField(-7)).toBe("-7");
    expect(csvField("=a,b")).toBe("\"'=a,b\"");
  });
});

const TABLE = [
  "| Client | Fee | Note |",
  "| --- | --- | --- |",
  "| **Acme** | $1,200 | [site](https://acme.example) |",
  "| Bolt | (300) | |",
  "| Crane |  | plain |",
  "| Delta | 12% | x |",
  "",
].join("\n");

function bodyFirstCells(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll("tbody tr")).map(
    (tr) => (tr.querySelector("td") as HTMLElement).textContent || "",
  );
}

describe("Markdown table tools", () => {
  let writeText: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  });
  afterEach(() => vi.restoreAllMocks());

  it("every table gets Copy as CSV and Download CSV", () => {
    render(<Markdown content={TABLE} />);
    const tools = screen.getByTestId("table-tools");
    expect(within(tools).getByRole("button", { name: /copy as csv/i })).toBeInTheDocument();
    expect(within(tools).getByRole("button", { name: /download csv/i })).toBeInTheDocument();
    // Quiet: hidden until hover or focus (and always shown on touch screens).
    expect(tools.className).toContain("opacity-0");
    expect(tools.className).toContain("group-hover/table:opacity-100");
    expect(tools.className).toContain("focus-within:opacity-100");
  });

  it("a header press sorts asc → desc → original, numeric-aware, blanks last", () => {
    const { container } = render(<Markdown content={TABLE} />);
    const fee = screen.getByRole("columnheader", { name: /fee/i });
    expect(fee).toHaveAttribute("aria-sort", "none");
    expect(bodyFirstCells(container)).toEqual(["Acme", "Bolt", "Crane", "Delta"]);

    fireEvent.click(fee);
    expect(fee).toHaveAttribute("aria-sort", "ascending");
    expect(bodyFirstCells(container)).toEqual(["Bolt", "Delta", "Acme", "Crane"]);

    fireEvent.click(fee);
    expect(fee).toHaveAttribute("aria-sort", "descending");
    expect(bodyFirstCells(container)).toEqual(["Acme", "Delta", "Bolt", "Crane"]);

    fireEvent.click(fee);
    expect(fee).toHaveAttribute("aria-sort", "none");
    expect(bodyFirstCells(container)).toEqual(["Acme", "Bolt", "Crane", "Delta"]);
  });

  it("the sort arrow is a keyboard-reachable button that says what it will do", () => {
    const { container } = render(<Markdown content={TABLE} />);
    const btn = screen.getByRole("button", { name: "Sort by Client: ascending" });
    fireEvent.click(btn);
    expect(screen.getByRole("button", { name: "Sort by Client: descending" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sort by Client: descending" }));
    expect(bodyFirstCells(container)).toEqual(["Delta", "Crane", "Bolt", "Acme"]);
  });

  it("keeps bold and links in their cells after sorting (rows moved, not flattened)", () => {
    const { container } = render(<Markdown content={TABLE} />);
    fireEvent.click(screen.getByRole("columnheader", { name: /fee/i }));
    const acmeRow = Array.from(container.querySelectorAll("tbody tr"))[2] as HTMLElement;
    expect(acmeRow.querySelector("strong")?.textContent).toBe("Acme");
    const link = acmeRow.querySelector("a") as HTMLAnchorElement;
    expect(link.textContent).toBe("site");
    expect(link.getAttribute("href")).toBe("https://acme.example");
  });

  it("moves the SAME row nodes (React reorders, it does not rebuild)", () => {
    const { container } = render(<Markdown content={TABLE} />);
    const before = Array.from(container.querySelectorAll("tbody tr"));
    fireEvent.click(screen.getByRole("columnheader", { name: /fee/i }));
    const after = Array.from(container.querySelectorAll("tbody tr"));
    expect(after[0]).toBe(before[1]); // Bolt
    expect(after[2]).toBe(before[0]); // Acme
  });

  it("Copy as CSV copies the table as shown (sorted), header first", async () => {
    render(<Markdown content={TABLE} />);
    fireEvent.click(screen.getByRole("columnheader", { name: /fee/i }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /copy as csv/i }));
    });
    expect(writeText).toHaveBeenCalledWith(
      "Client,Fee,Note\r\nBolt,(300),\r\nDelta,12%,x\r\nAcme,\"$1,200\",site\r\nCrane,,plain",
    );
    expect(await screen.findByRole("button", { name: /copied/i })).toBeInTheDocument();
  });

  it("Download CSV saves table.csv through a Blob link", async () => {
    const created: Blob[] = [];
    const createObjectURL = vi.fn((b: Blob) => {
      created.push(b);
      return "blob:fake";
    });
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectURL, configurable: true });
    const clicks: HTMLAnchorElement[] = [];
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        clicks.push(this);
      });

    render(<Markdown content={TABLE} />);
    fireEvent.click(screen.getByRole("button", { name: /download csv/i }));
    expect(click).toHaveBeenCalledTimes(1);
    expect(clicks[0].download).toBe("table.csv");
    expect(clicks[0].getAttribute("href")).toBe("blob:fake");
    expect(created[0].type).toBe("text/csv;charset=utf-8");
    const text = await created[0].text();
    expect(text.replace(/^﻿/, "")).toBe(
      "Client,Fee,Note\r\nAcme,\"$1,200\",site\r\nBolt,(300),\r\nCrane,,plain\r\nDelta,12%,x",
    );
    // The link is not left in the page.
    expect(document.querySelectorAll("a[download]")).toHaveLength(0);
  });

  it("a table with one body row has tools but no sorting", () => {
    render(<Markdown content={"| A | B |\n| - | - |\n| 1 | 2 |\n"} />);
    expect(screen.getByTestId("table-tools")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "A" })).not.toHaveAttribute("aria-sort");
    expect(screen.queryByRole("button", { name: /sort by/i })).toBeNull();
  });

  it("a draft's rich-text copy carries the table but none of the tools", () => {
    const { container } = render(<Markdown content={TABLE} />);
    const html = cleanHtml(container as HTMLElement);
    expect(html).toContain("<table");
    expect(html).toContain("Client");
    expect(html).not.toMatch(/<button/i);
    expect(html).not.toMatch(/Copy as CSV|Download CSV|Sort by/);
  });
});
