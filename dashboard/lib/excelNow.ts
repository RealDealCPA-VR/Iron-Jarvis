/**
 * EXCEL, RIGHT NOW (v1.468.0): the chat knows what the user has in front of
 * them in Excel, the way a panel inside Excel would.
 *
 * - /health says whether Excel is running at all (`excel_open`), so nothing
 *   is asked while it is not.
 * - GET /excel/live/now gives the workbook, the sheet and the selected range:
 *   NAMES ONLY, never a cell's content. It is read when the window gains
 *   focus (the user came back from Excel to type) and on a press, never on a
 *   timer: each read starts one short-lived helper on this PC.
 * - Nothing reaches a model until the user switches "Use it in this chat" on
 *   (kept for this window in sessionStorage, and on from the start in the
 *   window opened by "Open beside Excel"). Then each message carries the
 *   three names as `excel_now`, and the daemon lets that turn look at the
 *   open workbook.
 *
 * The page never subscribes to this store: it reads `excelNowForSend()` at
 * the moment a message goes out (the composer-store rule).
 */

export type ExcelNow = {
  open: boolean;
  workbook: string;
  sheet: string;
  selection: string;
  unsaved: boolean;
  others: number;
  busy: string;
};

export const EXCEL_USE_KEY = "ij.excel.use";
export const EXCEL_NOW_MIN_GAP_MS = 2500;

export const EXCEL_OFF_LINE = "Excel is open";
export const EXCEL_USE_LABEL = "Use it in this chat";
export const EXCEL_ON_LEAD = "Working with Excel";
export const EXCEL_STOP_LABEL = "Stop";
export const EXCEL_REFRESH_LABEL = "Read the selection again";
export const EXCEL_BESIDE_LABEL = "Open beside Excel";
export const EXCEL_USE_TITLE =
  "Jarvis may look at the open workbook to answer in this chat, and asks before any change. What it reads goes to the model that answers.";
export const EXCEL_BESIDE_TITLE = "A narrow chat window that stays on top, at the edge of the screen";

const NOT_OPEN: ExcelNow = { open: false, workbook: "", sheet: "", selection: "", unsaved: false, others: 0, busy: "" };

function text(v: unknown, cap: number): string {
  return typeof v === "string" ? v.slice(0, cap) : "";
}

/** The daemon's answer, whitelisted. Anything unexpected reads as not open. */
export function decodeExcelNow(raw: unknown): ExcelNow {
  if (!raw || typeof raw !== "object") return NOT_OPEN;
  const r = raw as Record<string, unknown>;
  if (r.open !== true) return { ...NOT_OPEN, busy: text(r.busy, 300) };
  const workbook = text(r.workbook, 260);
  if (!workbook) return NOT_OPEN;
  return {
    open: true,
    workbook,
    sheet: text(r.sheet, 31),
    selection: text(r.selection, 60),
    unsaved: r.unsaved === true,
    others: typeof r.others === "number" && r.others > 0 ? Math.floor(r.others) : 0,
    busy: "",
  };
}

/** "Sales!B4:B9 in Budget.xlsx" (the sheet or the selection may be unknown). */
export function excelNowWords(now: ExcelNow): string {
  const where = now.sheet && now.selection ? `${now.sheet}!${now.selection}` : now.sheet || now.selection;
  return where ? `${where} in ${now.workbook}` : now.workbook;
}

// ------------------------------------------------------------------ store --
let current: ExcelNow = NOT_OPEN;
let on = false;
let loaded = false;

function loadOn(): void {
  if (loaded) return;
  loaded = true;
  try {
    on = window.sessionStorage.getItem(EXCEL_USE_KEY) === "1";
  } catch {
    on = false;
  }
}

export function excelUseOn(): boolean {
  loadOn();
  return on;
}

export function setExcelUse(next: boolean): void {
  loaded = true;
  on = next;
  try {
    if (next) window.sessionStorage.setItem(EXCEL_USE_KEY, "1");
    else window.sessionStorage.removeItem(EXCEL_USE_KEY);
  } catch {
    /* a private window: the choice lasts while the page does */
  }
}

export function noteExcelNow(now: ExcelNow): void {
  current = now;
}

/** What a message carries: the three names, only while the user switched it
 *  on AND Excel showed a workbook at the last read. */
export function excelNowForSend(): { workbook: string; sheet: string; selection: string } | null {
  loadOn();
  if (!on || !current.open) return null;
  return { workbook: current.workbook, sheet: current.sheet, selection: current.selection };
}

/** Tests only: back to a fresh window. */
export function resetExcelNowForTests(): void {
  current = NOT_OPEN;
  on = false;
  loaded = false;
}

// ----------------------------------------------------------- desktop shell --
export type ExcelShellBridge = {
  openBeside: () => Promise<{ ok: boolean } | null>;
  beside: () => boolean;
  onBeside: (cb: (beside: boolean) => void) => () => void;
};

/** The desktop app's "beside Excel" bridge, or null in a browser or on an
 *  older desktop app. */
export function excelShellBridge(): ExcelShellBridge | null {
  if (typeof window === "undefined") return null;
  try {
    const b = (window as unknown as { ironjarvis?: { excel?: Partial<ExcelShellBridge> } }).ironjarvis?.excel;
    return b && typeof b.openBeside === "function" && typeof b.beside === "function" && typeof b.onBeside === "function"
      ? (b as ExcelShellBridge)
      : null;
  } catch {
    return null;
  }
}
