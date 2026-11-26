"use client";

/**
 * Next leaps 28-a: THE RECORD FOR THIS FOLDER, in the model menu of a coding
 * session (Chat › Code's ModelMenu, Build › Code's ModelPicker). Under each
 * tool and model, one muted line from the daemon's own record
 * (`GET /build/scoreboard?folder=`, `harness/scoreboard`): "In this folder:
 * kept 7 of 9, passed its checks 8 of 9". The daemon writes the words (every
 * figure with its count, "(too few to judge)" under five decided pieces, work
 * not decided yet said apart); this file only decodes them and finds the
 * row's line. Nothing on record: no line at all. The record chooses nothing
 * (D-28.1).
 *
 * READ ONCE PER MENU OPENING (`useCodingRecord(folder, open)`): never while
 * the menu is closed, never on a timer (G7). A read that fails shows nothing.
 */

import { useEffect, useState } from "react";
import { get } from "@/lib/api";

/** Chat › Code's own coding tool on the record. */
export const JARVIS_TOOL = "jarvis";

export interface CodingRecordRow {
  surface: string;
  tool: string;
  provider: string;
  model: string;
  kept: number;
  thrown: number;
  undecided: number;
  passed: number;
  failed: number;
  samples: number;
  words: string;
}

export interface CodingRecord {
  folder: string;
  rows: CodingRecordRow[];
}

function count(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.floor(v) : 0;
}

function str(v: unknown, cap = 400): string {
  return typeof v === "string" ? v.slice(0, cap) : "";
}

/** The daemon's answer, defensively: anything malformed reads as no rows. */
export function decodeCodingRecord(raw: unknown): CodingRecord {
  const o = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const rows = Array.isArray(o.rows) ? o.rows : [];
  return {
    folder: str(o.folder, 4096),
    rows: rows
      .filter((r): r is Record<string, unknown> => !!r && typeof r === "object")
      .map((r) => ({
        surface: str(r.surface, 20),
        tool: str(r.tool, 60),
        provider: str(r.provider, 120),
        model: str(r.model, 200),
        kept: count(r.kept),
        thrown: count(r.thrown),
        undecided: count(r.undecided),
        passed: count(r.passed),
        failed: count(r.failed),
        samples: count(r.samples),
        words: str(r.words),
      }))
      .filter((r) => r.words.trim() !== ""),
  };
}

/** The line for one menu row: Chat › Code's `provider::model` value, or a
 *  Build › Code tool and model id (""= the tool's own default). "" when the
 *  record holds nothing for it. */
export function recordLine(
  record: CodingRecord | null,
  tool: string,
  model: string,
  provider = "",
  alias?: (model: string) => string | null,
): string {
  if (!record || !tool) return "";
  const hit = record.rows.find((r) => {
    if (r.tool !== tool) return false;
    if (tool === JARVIS_TOOL && r.provider !== provider) return false;
    return r.model === model || (!!alias && !!r.model && alias(r.model) === model);
  });
  return hit?.words ?? "";
}

/** Chat › Code's menu value ("provider::model") as the record keys it. */
export function jarvisLine(record: CodingRecord | null, value: string): string {
  const at = value.indexOf("::");
  if (at <= 0) return "";
  return recordLine(record, JARVIS_TOOL, value.slice(at + 2), value.slice(0, at));
}

/** The folder's record, read each time `open` turns true (and the folder is
 *  known); null until it arrives, or when the read fails. */
export function useCodingRecord(folder: string | null | undefined, open: boolean): CodingRecord | null {
  // Kept with the folder it is for: another folder's record is never shown.
  const [got, setGot] = useState<{ folder: string; record: CodingRecord | null } | null>(null);
  const where = (folder || "").trim();
  useEffect(() => {
    if (!open || !where) return;
    let live = true;
    // (A read that cannot even start is a read that failed: nothing shown.)
    Promise.resolve()
      .then(() => get<unknown>(`/build/scoreboard?folder=${encodeURIComponent(where)}`))
      .then((raw) => {
        if (live) setGot({ folder: where, record: decodeCodingRecord(raw) });
      })
      .catch(() => {
        if (live) setGot({ folder: where, record: null });
      });
    return () => {
      live = false;
    };
  }, [open, where]);
  return where && got?.folder === where ? got.record : null;
}
