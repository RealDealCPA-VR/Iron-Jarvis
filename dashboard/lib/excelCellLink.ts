/**
 * A CELL THE USER CAN JUMP TO (v1.467.0).
 *
 * A reply about a workbook that is open in Excel names a cell as a markdown
 * link whose address is only a fragment: "#excel/<workbook>/<sheet>/<cell>",
 * each part percent-encoded. The one markdown renderer draws such a link as
 * a press that asks the daemon to show the cell (POST /excel/live/goto).
 * Nothing is read or changed, and no file is opened.
 *
 * The daemon's twin is documents/excel_live.py (CELL_LINK_PREFIX, CELL_RX),
 * pinned equal by tests/test_excel_trace_v1467.py.
 */

export const EXCEL_CELL_PREFIX = "#excel/";
export const EXCEL_CELL_RX = /^\$?[A-Za-z]{1,3}\$?[0-9]{1,7}(?::\$?[A-Za-z]{1,3}\$?[0-9]{1,7})?$/;
export const EXCEL_SHOW_TITLE = "Show this cell in Excel";
export const EXCEL_SHOW_FAILED = "Excel could not show that cell.";

export type ExcelCellTarget = { workbook: string; sheet: string; cell: string };

/** The cell a link points at, or null when the link is an ordinary one. */
export function parseExcelCellHref(href: string | undefined | null): ExcelCellTarget | null {
  if (!href || !href.startsWith(EXCEL_CELL_PREFIX)) return null;
  const parts = href.slice(EXCEL_CELL_PREFIX.length).split("/");
  if (parts.length !== 3) return null;
  let decoded: string[];
  try {
    decoded = parts.map((p) => decodeURIComponent(p).trim());
  } catch {
    return null;
  }
  const [workbook, sheet, cell] = decoded;
  if (workbook.length > 260 || sheet.length > 31 || !EXCEL_CELL_RX.test(cell)) return null;
  if (/[\u0000-\u001f]/.test(workbook + sheet) || /[/\\?*[\]:]/.test(sheet)) return null;
  return { workbook, sheet, cell: cell.replace(/\$/g, "").toUpperCase() };
}
