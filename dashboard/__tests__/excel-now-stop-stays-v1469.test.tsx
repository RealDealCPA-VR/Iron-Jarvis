/**
 * v1.469.0 review: in the window beside Excel, Stop STAYS stopped.
 *
 * That window starts with "Use it in this chat" on. The first cut switched it
 * on again every time the line was drawn anew (leaving the chat for Settings
 * and coming back, a reload), so a user who had pressed Stop was sending the
 * workbook's names, and letting the chat look at the workbook, again without
 * a press. The user's own Stop now holds for the window; a window that never
 * chose still starts on.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("@/lib/api", () => ({ get: H.get }));
vi.mock("@/lib/daemon", () => ({ useDaemon: () => ({ health: { excel_open: true } }) }));

import { ExcelNowStrip } from "@/components/chat/ExcelNowStrip";
import { parseExcelCellHref } from "@/lib/excelCellLink";
import { EXCEL_USE_KEY, excelNowForSend, resetExcelNowForTests } from "@/lib/excelNow";

describe("a link that only LOOKS like a cell link", () => {
  // The first cut's own cases all failed for another reason too (a wrong
  // count of parts, a bad cell), so the check of the "#excel/" start itself
  // had no pin: with it taken out every test still passed.
  it.each(["#other/Book.xlsx/Sheet1/A1", "x-files/Sheet1/A1", "/excel!/Book.xlsx/Sheet1/B2", "#EXCEL/Book.xlsx/Sheet1/A1"])(
    "leaves %s as an ordinary link",
    (href) => {
      expect(parseExcelCellHref(href)).toBeNull();
    },
  );
});

const NOW = { open: true, workbook: "Budget.xlsx", sheet: "Sales", selection: "B4:B9", unsaved: false, others: 0 };

function besideWindow() {
  (window as unknown as { ironjarvis?: unknown }).ironjarvis = {
    excel: { openBeside: vi.fn(), beside: () => true, onBeside: () => () => {} },
  };
}

beforeEach(() => {
  H.get.mockReset();
  H.get.mockResolvedValue(NOW);
  window.sessionStorage.clear();
  resetExcelNowForTests();
  besideWindow();
});
afterEach(() => {
  cleanup();
  (window as unknown as { ironjarvis?: unknown }).ironjarvis = undefined;
});

describe("the window beside Excel", () => {
  it("starts on, and a Stop holds when the line is drawn again", async () => {
    render(<ExcelNowStrip />);
    await waitFor(() => expect(screen.getByTestId("excel-now-strip")).toHaveAttribute("data-on", "true"));
    expect(excelNowForSend()).not.toBeNull();

    fireEvent.click(screen.getByTestId("excel-now-stop"));
    expect(screen.getByTestId("excel-now-strip")).toHaveAttribute("data-on", "false");
    expect(excelNowForSend()).toBeNull();

    // The user goes to another page of this window and comes back.
    cleanup();
    render(<ExcelNowStrip />);
    const strip = await screen.findByTestId("excel-now-strip");
    await act(async () => {});
    expect(strip).toHaveAttribute("data-on", "false");
    expect(excelNowForSend()).toBeNull();

    // ... and after a reload of the page (the module starts again).
    cleanup();
    resetExcelNowForTests();
    render(<ExcelNowStrip />);
    await screen.findByTestId("excel-now-strip");
    await act(async () => {});
    expect(screen.getByTestId("excel-now-strip")).toHaveAttribute("data-on", "false");
    expect(excelNowForSend()).toBeNull();
    expect(window.sessionStorage.getItem(EXCEL_USE_KEY)).toBe("0");
  });

  it("is told later by the app and still keeps a Stop", async () => {
    let tell: ((b: boolean) => void) | null = null;
    (window as unknown as { ironjarvis?: unknown }).ironjarvis = {
      excel: {
        openBeside: vi.fn(),
        beside: () => false,
        onBeside: (cb: (b: boolean) => void) => {
          tell = cb;
          return () => {};
        },
      },
    };
    render(<ExcelNowStrip />);
    await screen.findByTestId("excel-now-strip");
    await act(async () => tell?.(true));
    await waitFor(() => expect(screen.getByTestId("excel-now-strip")).toHaveAttribute("data-on", "true"));
    fireEvent.click(screen.getByTestId("excel-now-stop"));
    await act(async () => tell?.(true)); // the app says it again after every page load
    expect(screen.getByTestId("excel-now-strip")).toHaveAttribute("data-on", "false");
    expect(excelNowForSend()).toBeNull();
  });

  it("starts on again in a window that never chose", async () => {
    render(<ExcelNowStrip />);
    await waitFor(() => expect(screen.getByTestId("excel-now-strip")).toHaveAttribute("data-on", "true"));
    fireEvent.click(screen.getByTestId("excel-now-stop"));
    cleanup();
    window.sessionStorage.clear(); // a new window
    resetExcelNowForTests();
    render(<ExcelNowStrip />);
    await waitFor(() => expect(screen.getByTestId("excel-now-strip")).toHaveAttribute("data-on", "true"));
    // A press of "Use it in this chat" after a Stop turns it on as before.
    fireEvent.click(screen.getByTestId("excel-now-stop"));
    fireEvent.click(screen.getByTestId("excel-now-use"));
    await waitFor(() => expect(screen.getByTestId("excel-now-strip")).toHaveAttribute("data-on", "true"));
    expect(window.sessionStorage.getItem(EXCEL_USE_KEY)).toBe("1");
  });
});
