"use client";

import { useState, type ReactNode } from "react";
import { post } from "@/lib/api";
import { EXCEL_SHOW_FAILED, EXCEL_SHOW_TITLE, type ExcelCellTarget } from "@/lib/excelCellLink";

/**
 * A cell named in a reply, drawn as a press that shows it in Excel
 * (v1.467.0, lib/excelCellLink.ts). It looks like a link because it reads as
 * one; it is a button because it acts on this PC and opens no page. A
 * refusal ("That workbook is not open in Excel") is said beside it in the
 * daemon's own words. The status line is always mounted so a screen reader
 * hears the words when they arrive.
 */
export function ExcelCellLink({ target, children }: { target: ExcelCellTarget; children?: ReactNode }) {
  const [busy, setBusy] = useState(false);
  const [words, setWords] = useState("");

  async function show() {
    if (busy) return;
    setBusy(true);
    setWords("");
    try {
      await post("/excel/live/goto", target);
    } catch (e) {
      setWords(e instanceof Error && e.message ? e.message : EXCEL_SHOW_FAILED);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={show}
        title={EXCEL_SHOW_TITLE}
        data-testid="excel-cell-link"
        aria-busy={busy}
        className="inline text-accent-soft underline decoration-accent/40 underline-offset-2 transition-colors hover:decoration-accent"
      >
        {children}
      </button>
      <span role="status" data-testid="excel-cell-link-note" className={words ? "ml-1 text-xs text-tone-warn" : "sr-only"}>
        {words}
      </span>
    </>
  );
}
