"use client";

/**
 * DOORS (v1.199.0) — a compact row of links under an assistant reply into the
 * SURFACES this turn actually touched: ran a workflow → a door to /workflows,
 * wrote memory → a door to /memory. The chat stays the one surface; the doors
 * make the rest of the app reachable from where the work just happened.
 *
 * The doors are SERVER truth: the daemon derives them from tools that
 * executed OK this turn (never armed-but-refused ones), dedupes, caps at 4,
 * and persists them with the message — files are deliberately EXCLUDED
 * because the ArtifactsRail owns files. This strip renders exactly what the
 * daemon says and never derives doors of its own: a client-side guess would
 * be the kind of small lie the TurnReceipt exists to end.
 *
 * A message with no doors (every message persisted before v1.199.0, and any
 * turn that touched nothing) renders NOTHING — silence, not a fallback.
 *
 * Each click is tallied via `recordDoorOpen` (localStorage, never leaves the
 * machine) so the emergent-surface metric can tell a door-opened subsystem
 * from a nav-opened one — the Sidebar's clicks land in a DIFFERENT key.
 */

import Link from "next/link";
import { DoorOpen } from "lucide-react";
import { recordDoorOpen } from "@/lib/appTiles";

export interface Door {
  href: string;
  label: string;
}

export interface DoorsStripProps {
  /** Absent/null/empty on messages from before v1.199.0 — render nothing. */
  doors?: Door[] | null;
}

export function DoorsStrip({ doors }: DoorsStripProps) {
  if (!doors || doors.length === 0) return null;
  // Degenerate persisted shapes only (the props cross a JSON boundary): a
  // door without an href cannot be a link. This is shape-checking, never
  // derivation — no slicing, no reordering, no invented entries.
  const usable = doors.filter(
    (d) => d && typeof d.href === "string" && d.href.trim().length > 0,
  );
  if (usable.length === 0) return null;
  return (
    <div className="ml-11 mt-1.5 flex min-w-0 flex-wrap items-center gap-1.5">
      {usable.map((d) => (
        <Link
          key={d.href}
          href={d.href}
          // Tally BEFORE navigating, like the Sidebar's recordOpen — the
          // local counter for the emergent-surface metric, not telemetry.
          onClick={() => recordDoorOpen(d.href)}
          className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-white/[0.08] bg-white/[0.02] px-2.5 py-1 text-[11px] text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft"
        >
          <DoorOpen size={11} className="shrink-0 text-accent-soft/70" />
          <span className="truncate">{d.label || d.href}</span>
        </Link>
      ))}
    </div>
  );
}
