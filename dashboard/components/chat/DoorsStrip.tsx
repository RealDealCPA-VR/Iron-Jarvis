"use client";

/**
 * DOORS (v1.199.0) — a compact row of links under an assistant reply into the
 * SURFACES this turn actually touched: ran a workflow → a door to /workflows,
 * wrote memory → a door to /memory. The chat stays the one surface; the doors
 * make the rest of the app reachable from where the work just happened.
 *
 * The doors are SERVER truth: the daemon derives them from tools that
 * executed OK this turn (never armed-but-refused ones), dedupes, caps at 4,
 * and persists them with the message. Since v1.353.0 (G41) a call that FAILED
 * because something is not set up adds its fix door too ("Connect Google
 * Drive in Settings", "Pair your browser" — daemon/doors.failure_door_for);
 * a call the user declined or the rules refused still adds none. Files are deliberately EXCLUDED
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
 *
 * v1.436.0 (Wave 5, B3): a message that asks for a step only a page can do
 * gets ONE door to the exact place (daemon/page_doors). A sign-in to Claude
 * Code, Codex or Grok carries `press` — the /health provider id — and is
 * drawn through the ONE Sign in press (components/connections/
 * CliSignInPress, variant "door"): the same terminal it opens on Settings ›
 * Connections, never a second sign-in path. An unknown `press` is dropped
 * when the door is decoded, so the door is then its plain link.
 */

import Link from "next/link";
import { DoorOpen } from "lucide-react";
import { recordDoorOpen } from "@/lib/appTiles";
import { subscriptionCliFor } from "@/lib/cliSignIn";
import { CliSignInPress } from "@/components/connections/CliSignInPress";
import { DOOR_PILL } from "@/components/chat/doorStyle";
import { InstallDoorPress, isInstallPress } from "@/components/chat/InstallDoorPress";

export interface Door {
  href: string;
  label: string;
  /** v1.436.0: a sign-in press this door is drawn through ("claude-cli",
   *  "codex-cli", "grok-cli"); absent = a link. */
  press?: string;
  /** v1.455.0 (readiness Wave G, item 23): an install press this door is
   *  drawn through ("claude", "codex" — lib/cliInstall); absent = none. */
  install?: string;
}

/** The sign-in press a door names, when the dashboard has that press. */
export function doorPress(d: Pick<Door, "press"> | null | undefined): string | undefined {
  const p = d && typeof d.press === "string" ? d.press : "";
  return p && subscriptionCliFor(p) ? p : undefined;
}

/** Decode the daemon's `doors` array (v1.199.0) — server truth carried onto
 *  the message verbatim. Entries are shape-checked only (the payload crosses
 *  a JSON boundary), never re-derived, sliced, or reordered: which doors a
 *  turn earned is the DAEMON's call (executed-ok tools, deduped, capped).
 *  v1.436.0: `press` is kept only when it names a press this dashboard has. */
export function decodeDoors(raw: unknown): Door[] | null {
  if (!Array.isArray(raw)) return null;
  const out: Door[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const d = item as { href?: unknown; label?: unknown; press?: unknown; install?: unknown };
    if (typeof d.href !== "string" || !d.href.trim()) continue;
    const press = doorPress({ press: typeof d.press === "string" ? d.press : undefined });
    const install = isInstallPress(d.install) ? d.install : undefined;
    out.push({
      href: d.href,
      label: typeof d.label === "string" && d.label.trim() ? d.label : d.href,
      ...(press ? { press } : {}),
      ...(install ? { install } : {}),
    });
  }
  return out.length > 0 ? out : null;
}

export interface DoorsStripProps {
  /** Absent/null/empty on messages from before v1.199.0 — render nothing. */
  doors?: Door[] | null;
  /** v1.426.0 (Wave 6, E2): the host's own handling of a door it knows (the
   *  chat page's coding offer carries the user's request into the Code view
   *  — lib/codingStart). Answers true when it handled the press: the link's
   *  own navigation is then not taken. Absent: every door is a plain link. */
  onOpen?: (door: Door) => boolean;
}

export function DoorsStrip({ doors, onOpen }: DoorsStripProps) {
  if (!doors || doors.length === 0) return null;
  // Degenerate persisted shapes only (the props cross a JSON boundary): a
  // door without an href cannot be a link. This is shape-checking, never
  // derivation — no slicing, no reordering, no invented entries.
  const usable = doors.filter(
    (d) => d && typeof d.href === "string" && d.href.trim().length > 0,
  );
  if (usable.length === 0) return null;
  return (
    <div className="mt-1.5 flex min-w-0 flex-wrap items-center gap-1.5">
      {usable.map((d) => {
        if (isInstallPress(d.install)) {
          // v1.455.0: an install door IS the one install press (lib/cliInstall).
          return <InstallDoorPress key={d.href + d.install} cli={d.install} onLaunched={() => recordDoorOpen(d.href)} />;
        }
        const press = doorPress(d);
        if (press) {
          // v1.436.0: a sign-in door IS the one Sign in press; the open is
          // tallied once a terminal opened.
          return (
            <CliSignInPress
              key={d.href}
              provider={press}
              variant="door"
              testIdPrefix="door"
              onLaunched={() => recordDoorOpen(d.href)}
            />
          );
        }
        return (
          <Link
            key={d.href}
            href={d.href}
            // Tally BEFORE navigating, like the Sidebar's recordOpen — the
            // local counter for the emergent-surface metric, not telemetry.
            onClick={(e) => {
              recordDoorOpen(d.href);
              if (onOpen?.(d)) e.preventDefault();
            }}
            className={DOOR_PILL}
          >
            <DoorOpen size={11} className="shrink-0 text-accent-soft/70" />
            <span className="truncate">{d.label || d.href}</span>
          </Link>
        );
      })}
    </div>
  );
}
