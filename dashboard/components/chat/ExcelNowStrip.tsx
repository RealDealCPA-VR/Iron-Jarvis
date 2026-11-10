"use client";

/**
 * The "Excel is open" / "Working with Excel" line above the message box
 * (v1.468.0, lib/excelNow.ts). Quiet, one line, and nothing at all while
 * Excel is not running or shows no workbook.
 *
 * It owns its reads and its switch, and writes both into the lib/excelNow
 * store; the chat page only reads that store when a message goes out, so
 * this line never re-renders the page.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { PanelRightOpen, RefreshCw, Sheet } from "lucide-react";
import { get } from "@/lib/api";
import { useDaemon } from "@/lib/daemon";
import {
  decodeExcelNow,
  EXCEL_BESIDE_LABEL,
  EXCEL_BESIDE_TITLE,
  EXCEL_NOW_MIN_GAP_MS,
  EXCEL_OFF_LINE,
  EXCEL_ON_LEAD,
  EXCEL_REFRESH_LABEL,
  EXCEL_STOP_LABEL,
  EXCEL_USE_LABEL,
  EXCEL_USE_TITLE,
  excelNowWords,
  excelShellBridge,
  excelUseOn,
  noteExcelNow,
  setExcelUse,
  type ExcelNow,
} from "@/lib/excelNow";

export function ExcelNowStrip() {
  const excelRunning = (useDaemon().health as { excel_open?: boolean } | null | undefined)?.excel_open === true;
  const [now, setNow] = useState<ExcelNow | null>(null);
  const [on, setOn] = useState(false);
  const [beside, setBeside] = useState(false);
  const [canBeside, setCanBeside] = useState(false);
  const inFlight = useRef(false);
  const lastAt = useRef(0);

  const read = useCallback(async (force: boolean) => {
    if (inFlight.current) return;
    if (!force && Date.now() - lastAt.current < EXCEL_NOW_MIN_GAP_MS) return;
    inFlight.current = true;
    lastAt.current = Date.now();
    try {
      const got = decodeExcelNow(await get("/excel/live/now"));
      noteExcelNow(got);
      setNow(got);
    } catch {
      const none = decodeExcelNow(null);
      noteExcelNow(none);
      setNow(none);
    } finally {
      inFlight.current = false;
    }
  }, []);

  // The switch and the shell's "this is the window beside Excel" fact.
  useEffect(() => {
    setOn(excelUseOn());
    const shell = excelShellBridge();
    setCanBeside(shell !== null);
    if (!shell) return;
    const take = (b: boolean) => {
      setBeside(b);
      if (b) {
        // The window opened to sit beside Excel is for Excel: on from the start.
        setExcelUse(true);
        setOn(true);
      }
    };
    take(shell.beside() === true);
    return shell.onBeside(take);
  }, []);

  // Excel started or stopped (/health): read once, or forget what was read.
  useEffect(() => {
    if (excelRunning) {
      void read(true);
    } else {
      const none = decodeExcelNow(null);
      noteExcelNow(none);
      setNow(none);
    }
  }, [excelRunning, read]);

  // The user came back from Excel to this window: the selection has moved.
  useEffect(() => {
    if (!excelRunning) return;
    const again = () => {
      if (document.visibilityState === "visible") void read(false);
    };
    window.addEventListener("focus", again);
    document.addEventListener("visibilitychange", again);
    return () => {
      window.removeEventListener("focus", again);
      document.removeEventListener("visibilitychange", again);
    };
  }, [excelRunning, read]);

  if (!excelRunning || !now || !now.open) return null;

  const turn = (next: boolean) => {
    setExcelUse(next);
    setOn(next);
    if (next) void read(true);
  };

  return (
    <div
      data-testid="excel-now-strip"
      data-on={on ? "true" : "false"}
      className="flex w-full min-w-0 flex-wrap items-center gap-x-2 gap-y-1 px-4 pt-2 text-[12px] text-zinc-400"
    >
      <Sheet size={12} className={on ? "shrink-0 text-tone-success" : "shrink-0 text-zinc-500"} aria-hidden />
      {on ? (
        <span className="min-w-0 truncate">
          {EXCEL_ON_LEAD}: <span className="font-medium text-zinc-200">{excelNowWords(now)}</span>
        </span>
      ) : (
        <span className="min-w-0 truncate">
          {EXCEL_OFF_LINE}: <span className="text-zinc-300">{now.workbook}</span>
        </span>
      )}
      {on ? (
        <>
          <button
            type="button"
            data-testid="excel-now-refresh"
            aria-label={EXCEL_REFRESH_LABEL}
            title={EXCEL_REFRESH_LABEL}
            onClick={() => void read(true)}
            className="rounded-full p-1 text-zinc-500 hover:bg-white/[0.05] hover:text-zinc-200"
          >
            <RefreshCw size={11} aria-hidden />
          </button>
          <button
            type="button"
            data-testid="excel-now-stop"
            onClick={() => turn(false)}
            className="rounded-full px-2 py-0.5 text-[12px] text-zinc-400 hover:bg-white/[0.05] hover:text-zinc-200"
          >
            {EXCEL_STOP_LABEL}
          </button>
        </>
      ) : (
        <button
          type="button"
          data-testid="excel-now-use"
          title={EXCEL_USE_TITLE}
          onClick={() => turn(true)}
          className="rounded-full px-2 py-0.5 text-[12px] font-medium text-tone-success hover:bg-white/[0.05]"
        >
          {EXCEL_USE_LABEL}
        </button>
      )}
      {canBeside && !beside && (
        <button
          type="button"
          data-testid="excel-now-beside"
          title={EXCEL_BESIDE_TITLE}
          onClick={() => void excelShellBridge()?.openBeside()}
          className="ml-auto inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[12px] text-zinc-400 hover:bg-white/[0.05] hover:text-zinc-200"
        >
          <PanelRightOpen size={11} aria-hidden />
          {EXCEL_BESIDE_LABEL}
        </button>
      )}
    </div>
  );
}
