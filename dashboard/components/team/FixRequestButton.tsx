"use client";

/**
 * "Copy as a fix request" (v1.423.0, Wave 3 lane B — the owner's decision of
 * 2026-11-01: "This will be the specifics I can feed back to the agent working
 * on the app … structured so that sending it to a coding agent for a fix is a
 * simple copy and paste of the issues experienced").
 *
 * ONE quiet button. A press reads `GET /team/fix-request?days=7[&session=…]`
 * (the daemon's `agents/fix_request`: the problems Iron Jarvis's OWN coding
 * sessions and model team ran into — a command the shell could not run, a
 * tool a worker kept calling wrong, a helper that could not start, a router
 * that found no one, a reply cut off, a review that never ran — each with
 * where, what was seen, what was expected and the evidence, secrets masked)
 * and copies its Markdown through the app's own copy path (`copyPlain`:
 * the desktop bridge first, then the browser's clipboard). Then it SAYS what
 * happened, in a status line that is mounted with the button (a live region
 * mounted with its words is often not announced — the v1.352.0 bell lesson):
 *
 *  - "Copied: N problems from the last 7 days" ("Copied: 15 of 60 …" when
 *    the report could list only some — the daemon's `listed`);
 *  - "Nothing to report from the last 7 days" — and copies NOTHING; when a
 *    source could not be read (`complete: false`), the daemon's own line,
 *    which names it (never a silent "nothing found");
 *  - a copy that could not reach the clipboard is never reported as one (the
 *    DraftCard rule): the text is shown in a box to select by hand;
 *  - any other failure in words.
 *
 * Nothing is read until the press (the report reads a week of records). A 404
 * whose detail is FastAPI's own "Not Found" means an older Iron Jarvis that
 * has no such report: the button hides itself. The route's own 404 (a session
 * that is gone) is said in its words instead.
 *
 * Mounted beside Settings › Models › Model team's intro (the last 7 days, every
 * coding session) and in a coding session's Session tab (that session only).
 */

import { useCallback, useState } from "react";
import { ClipboardCopy } from "lucide-react";
import { ApiError, get } from "@/lib/api";

/** The window the button reads, in days. */
export const FIX_REQUEST_DAYS = 7;

/** The route the button reads. */
export function fixRequestPath(session?: string | null, days: number = FIX_REQUEST_DAYS): string {
  const q = new URLSearchParams({ days: String(days) });
  const s = (session ?? "").trim();
  if (s) q.set("session", s);
  return `/team/fix-request?${q.toString()}`;
}

function windowWords(days: number): string {
  return days === 1 ? "the last day" : `the last ${days} days`;
}

/** What the status line says after a copy. v1.423.0 review: `listed` — how
 *  many of the `problems` the copied text HOLDS (the daemon's report lists at
 *  most 20 and stops at 30,000 characters); said as "15 of 60" when fewer, so
 *  the line never claims a copy of problems that are not in it. */
export function copiedWords(problems: number, days: number = FIX_REQUEST_DAYS, listed?: number): string {
  const of = typeof listed === "number" && listed >= 0 && listed < problems ? `${listed} of ` : "";
  return `Copied: ${of}${problems} problem${problems === 1 ? "" : "s"} from ${windowWords(days)}`;
}

/** What the status line says when there is nothing to copy. */
export function nothingWords(days: number = FIX_REQUEST_DAYS): string {
  return `Nothing to report from ${windowWords(days)}`;
}

/** The button's title: what it copies, and to check it before pasting it. */
export function fixRequestTitle(session?: string | null, days: number = FIX_REQUEST_DAYS): string {
  const where = (session ?? "").trim()
    ? `in this coding session over ${windowWords(days)}`
    : `in ${windowWords(days)}`;
  return (
    `Copies a report of the problems Iron Jarvis's coding sessions and model team ran into ${where}, ` +
    "written for the coding agent that fixes Iron Jarvis. Check it before you paste it anywhere: it " +
    "holds commands, file paths and errors from your work (secrets are masked)."
  );
}

/** The sentence when the clipboard could not be reached. */
export const CLIPBOARD_UNAVAILABLE =
  "Your clipboard could not be reached, so nothing was copied. Select the report below and copy it.";

type Report = { text: string; problems: number; listed?: number; complete: boolean };

function count(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.floor(v) : undefined;
}

function decode(raw: unknown): Report | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.text !== "string") return null;
  return {
    text: r.text,
    problems: count(r.problems) ?? 0,
    listed: count(r.listed),
    // v1.423.0 review: `complete: false` — a source could not be read, or
    // calls were left out; an older Iron Jarvis says nothing (complete).
    complete: r.complete !== false,
  };
}

/** An older Iron Jarvis has no such route: FastAPI's own 404 ("Not Found"). */
function olderIronJarvis(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404 && err.message.trim() === "Not Found";
}

const PRESS =
  "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] text-zinc-400 transition-colors " +
  "hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 " +
  "focus-visible:ring-accent/60 disabled:opacity-50";

type Said = { tone: "quiet" | "warn"; text: string } | null;

export function FixRequestButton({
  session = null,
  days = FIX_REQUEST_DAYS,
  className = "",
}: {
  /** One coding session (its id) — absent: every coding session. */
  session?: string | null;
  days?: number;
  className?: string;
}) {
  const [hidden, setHidden] = useState(false);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<Said>(null);
  const [fallback, setFallback] = useState<string | null>(null);

  const press = useCallback(async () => {
    setBusy(true);
    setSaid(null);
    setFallback(null);
    try {
      const report = decode(await get<unknown>(fixRequestPath(session, days)));
      if (!report) {
        setSaid({ tone: "warn", text: "Iron Jarvis answered with something that is not a report." });
        return;
      }
      if (report.problems <= 0) {
        // Never a silent "nothing found" (v1.423.0 review): when a source
        // could not be read, the daemon's own line says which.
        setSaid(
          report.complete
            ? { tone: "quiet", text: nothingWords(days) }
            : { tone: "warn", text: report.text },
        );
        return;
      }
      // The app's own copy path (the desktop bridge first, then the browser),
      // loaded on the press so this button adds nothing to the page it sits on.
      const { copyPlain } = await import("@/components/settings/MaintenanceTools");
      const outcome = await copyPlain(report.text);
      if (outcome === "copied") {
        setSaid({ tone: "quiet", text: copiedWords(report.problems, days, report.listed) });
      } else {
        setFallback(report.text);
        setSaid({ tone: "warn", text: CLIPBOARD_UNAVAILABLE });
      }
    } catch (err) {
      if (olderIronJarvis(err)) {
        setHidden(true);
        return;
      }
      const words = err instanceof ApiError || err instanceof Error ? err.message : String(err);
      setSaid({ tone: "warn", text: `The report could not be made: ${words}` });
    } finally {
      setBusy(false);
    }
  }, [session, days]);

  if (hidden) return null;
  return (
    <div data-testid="fix-request" className={`relative space-y-1 ${className}`.trim()}>
      <button
        type="button"
        data-testid="fix-request-copy"
        className={PRESS}
        title={fixRequestTitle(session, days)}
        disabled={busy}
        onClick={() => void press()}
      >
        <ClipboardCopy size={13} aria-hidden />
        {busy ? "Making the report…" : "Copy as a fix request"}
      </button>
      <p
        role="status"
        aria-live="polite"
        data-testid="fix-request-status"
        // Never `sr-only` (absolute): inside a coding session's scroll box an
        // unparented absolute stretched the page (the v1.420.2 lesson). An
        // empty paragraph takes no room.
        className={`text-[12px] ${said?.tone === "warn" ? "text-tone-warn" : "text-zinc-500"}`}
      >
        {said?.text ?? ""}
      </p>
      {fallback !== null ? (
        <textarea
          data-testid="fix-request-text"
          readOnly
          value={fallback}
          aria-label="The fix request"
          className="field h-40 w-full font-mono text-[11px]"
          onFocus={(e) => e.currentTarget.select()}
        />
      ) : null}
    </div>
  );
}

export default FixRequestButton;
