/**
 * v1.468.0: "Excel is open" / "Working with Excel", the line above the chat's
 * message box (components/chat/ExcelNowStrip, lib/excelNow).
 *
 * - Nothing is asked, and nothing is drawn, while /health does not say Excel
 *   is running.
 * - The line shows names only. Nothing is sent with a message until the user
 *   presses "Use it in this chat"; Stop takes it back.
 * - The selection is read again when the window gains focus, never on a timer.
 * - In the desktop app's window beside Excel the switch starts on.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  get: vi.fn(),
  health: { excel_open: true } as { excel_open?: boolean } | null,
}));

vi.mock("@/lib/api", () => ({ get: H.get }));
vi.mock("@/lib/daemon", () => ({ useDaemon: () => ({ health: H.health }) }));

import { ExcelNowStrip } from "@/components/chat/ExcelNowStrip";
import {
  decodeExcelNow,
  EXCEL_NOW_MIN_GAP_MS,
  EXCEL_USE_KEY,
  excelNowForSend,
  excelNowWords,
  resetExcelNowForTests,
} from "@/lib/excelNow";

const NOW = { open: true, workbook: "Budget.xlsx", sheet: "Sales", selection: "B4:B9", cell: "B4", unsaved: true, others: 0 };

type Shell = { openBeside: ReturnType<typeof vi.fn>; beside: () => boolean; onBeside: (cb: (b: boolean) => void) => () => void };

function setShell(shell: Shell | null) {
  (window as unknown as { ironjarvis?: unknown }).ironjarvis = shell ? { excel: shell } : undefined;
}

beforeEach(() => {
  H.get.mockReset();
  H.get.mockResolvedValue(NOW);
  H.health = { excel_open: true };
  window.sessionStorage.clear();
  resetExcelNowForTests();
  setShell(null);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("reading the daemon's answer", () => {
  it("keeps names only and reads anything else as not open", () => {
    expect(decodeExcelNow({ ...NOW, rows: [["secret"]] })).toEqual({
      open: true, workbook: "Budget.xlsx", sheet: "Sales", selection: "B4:B9", unsaved: true, others: 0, busy: "",
    });
    expect(decodeExcelNow({ open: true, workbook: "" }).open).toBe(false);
    expect(decodeExcelNow({ open: false, busy: "Excel is busy." })).toMatchObject({ open: false, busy: "Excel is busy." });
    expect(decodeExcelNow("nope").open).toBe(false);
    expect(excelNowWords(decodeExcelNow(NOW))).toBe("Sales!B4:B9 in Budget.xlsx");
    expect(excelNowWords(decodeExcelNow({ ...NOW, selection: "" }))).toBe("Sales in Budget.xlsx");
  });
});

describe("the line above the message box", () => {
  it("asks nothing and draws nothing while Excel is not running", async () => {
    H.health = { excel_open: false };
    render(<ExcelNowStrip />);
    await act(async () => {});
    expect(H.get).not.toHaveBeenCalled();
    expect(screen.queryByTestId("excel-now-strip")).toBeNull();
    cleanup();
    H.health = {}; // an older daemon says nothing about Excel
    render(<ExcelNowStrip />);
    await act(async () => {});
    expect(H.get).not.toHaveBeenCalled();
  });

  it("draws nothing when Excel runs with no workbook", async () => {
    H.get.mockResolvedValue({ open: false });
    render(<ExcelNowStrip />);
    await waitFor(() => expect(H.get).toHaveBeenCalledWith("/excel/live/now"));
    await act(async () => {});
    expect(screen.queryByTestId("excel-now-strip")).toBeNull();
  });

  it("names the workbook, and sends nothing until the user switches it on", async () => {
    render(<ExcelNowStrip />);
    const strip = await screen.findByTestId("excel-now-strip");
    expect(strip).toHaveAttribute("data-on", "false");
    expect(strip).toHaveTextContent("Excel is open: Budget.xlsx");
    expect(strip).not.toHaveTextContent("B4:B9");
    expect(excelNowForSend()).toBeNull();

    fireEvent.click(screen.getByTestId("excel-now-use"));
    await waitFor(() => expect(screen.getByTestId("excel-now-strip")).toHaveAttribute("data-on", "true"));
    expect(screen.getByTestId("excel-now-strip")).toHaveTextContent("Working with Excel: Sales!B4:B9 in Budget.xlsx");
    expect(excelNowForSend()).toEqual({ workbook: "Budget.xlsx", sheet: "Sales", selection: "B4:B9" });
    expect(window.sessionStorage.getItem(EXCEL_USE_KEY)).toBe("1");

    fireEvent.click(screen.getByTestId("excel-now-stop"));
    expect(screen.getByTestId("excel-now-strip")).toHaveAttribute("data-on", "false");
    expect(excelNowForSend()).toBeNull();
    expect(window.sessionStorage.getItem(EXCEL_USE_KEY)).toBeNull();
  });

  it("keeps the switch for this window, and stops sending when Excel closes", async () => {
    window.sessionStorage.setItem(EXCEL_USE_KEY, "1");
    const view = render(<ExcelNowStrip />);
    await waitFor(() => expect(screen.getByTestId("excel-now-strip")).toHaveAttribute("data-on", "true"));
    expect(excelNowForSend()).not.toBeNull();
    H.health = { excel_open: false };
    view.rerender(<ExcelNowStrip />);
    await waitFor(() => expect(screen.queryByTestId("excel-now-strip")).toBeNull());
    expect(excelNowForSend()).toBeNull();
  });

  it("reads the selection again when the window gains focus, and on the press", async () => {
    const t0 = 1_800_000_000_000;
    const clock = vi.spyOn(Date, "now").mockReturnValue(t0);
    window.sessionStorage.setItem(EXCEL_USE_KEY, "1");
    render(<ExcelNowStrip />);
    await screen.findByTestId("excel-now-strip");
    expect(H.get).toHaveBeenCalledTimes(1);

    fireEvent.focus(window); // straight away: too soon to ask again
    await act(async () => {});
    expect(H.get).toHaveBeenCalledTimes(1);

    clock.mockReturnValue(t0 + EXCEL_NOW_MIN_GAP_MS + 1);
    H.get.mockResolvedValue({ ...NOW, selection: "D2" });
    fireEvent.focus(window);
    await waitFor(() => expect(screen.getByTestId("excel-now-strip")).toHaveTextContent("Sales!D2 in Budget.xlsx"));
    expect(excelNowForSend()).toEqual({ workbook: "Budget.xlsx", sheet: "Sales", selection: "D2" });

    H.get.mockResolvedValue({ ...NOW, selection: "E7" });
    fireEvent.click(screen.getByTestId("excel-now-refresh")); // the press reads at once
    await waitFor(() => expect(screen.getByTestId("excel-now-strip")).toHaveTextContent("Sales!E7"));
    clock.mockRestore();
  });

  it("reads a failed answer as not open, and sends nothing", async () => {
    window.sessionStorage.setItem(EXCEL_USE_KEY, "1");
    H.get.mockRejectedValue(new Error("boom"));
    render(<ExcelNowStrip />);
    await waitFor(() => expect(H.get).toHaveBeenCalled());
    await act(async () => {});
    expect(screen.queryByTestId("excel-now-strip")).toBeNull();
    expect(excelNowForSend()).toBeNull();
  });
});

describe("the window beside Excel", () => {
  it("offers to open it only in the desktop app, and opens it on the press", async () => {
    render(<ExcelNowStrip />);
    await screen.findByTestId("excel-now-strip");
    expect(screen.queryByTestId("excel-now-beside")).toBeNull();
    cleanup();

    const shell: Shell = { openBeside: vi.fn().mockResolvedValue({ ok: true }), beside: () => false, onBeside: () => () => {} };
    setShell(shell);
    render(<ExcelNowStrip />);
    fireEvent.click(await screen.findByTestId("excel-now-beside"));
    expect(shell.openBeside).toHaveBeenCalledTimes(1);
    expect(excelNowForSend()).toBeNull(); // opening the other window switches nothing on here
  });

  it("starts switched on in that window, also when the app says so after the page loaded", async () => {
    let tell: ((b: boolean) => void) | null = null;
    const shell: Shell = {
      openBeside: vi.fn(),
      beside: () => false,
      onBeside: (cb) => {
        tell = cb;
        return () => {};
      },
    };
    setShell(shell);
    render(<ExcelNowStrip />);
    const strip = await screen.findByTestId("excel-now-strip");
    expect(strip).toHaveAttribute("data-on", "false");
    await act(async () => tell?.(true));
    await waitFor(() => expect(screen.getByTestId("excel-now-strip")).toHaveAttribute("data-on", "true"));
    expect(screen.queryByTestId("excel-now-beside")).toBeNull();
    expect(excelNowForSend()).toEqual({ workbook: "Budget.xlsx", sheet: "Sales", selection: "B4:B9" });
  });
});
