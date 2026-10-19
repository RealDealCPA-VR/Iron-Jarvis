"use client";

/**
 * RUNNING NOW (v1.352.0, G26) — the list the orb opens.
 *
 * One list, two places: the sidebar footer's orb opens it as a small popover
 * on the Help menu's surface (AppSidebar owns the popover, like the Help
 * menu), and Everything › Status's "Running" number opens it in place
 * (StatusOverview). The rows are `lib/runningNow.runningItems` over what the
 * daemon already serves — GET /sessions (active/queued runs) and the chat
 * list's running/waiting flags — so there is no new route and no second
 * vocabulary.
 *
 * Reads only while it is on screen: mounted = open. The run list is re-read
 * every RUNNING_POLL_MS (visible windows only) and at once whenever a run
 * starts or ends (the orb's own event types and in-flight rule,
 * `MoodOrb.inFlightIds`); the chat list comes from the one sidebar store
 * (`lib/chatList`), which already re-reads on a chat update and while a chat
 * is running. Elapsed times re-word every RUNNING_TICK_MS.
 *
 * Quiet grey rows, theme tokens only, no motion of its own.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePolledApi } from "@/lib/useApi";
import { useChatList } from "@/lib/chatList";
import { useEvents } from "@/lib/useEvents";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import { inFlightIds, MOOD_EVENT_TYPES } from "@/components/MoodOrb";
import {
  MAX_RUNNING_ROWS,
  RUNNING_POLL_MS,
  RUNNING_SESSIONS_PATH,
  RUNNING_TICK_MS,
  runningItems,
  type RunningSessionRow,
} from "@/lib/runningNow";

const EVENT_OPTS = { types: MOOD_EVENT_TYPES };

export function RunningNow({
  onNavigate,
  headingId,
}: {
  /** Called when a row (or "All runs") is followed: closes the popover and
   *  the phone drawer. */
  onNavigate?: () => void;
  /** The heading's id, for the container's aria-labelledby. */
  headingId?: string;
}) {
  const sessions = usePolledApi<{ sessions?: RunningSessionRow[] }>(RUNNING_SESSIONS_PATH, RUNNING_POLL_MS);
  const chats = useChatList();
  const [now, setNow] = useState(() => Date.now());
  useVisibleInterval(() => setNow(Date.now()), RUNNING_TICK_MS);

  // A run that starts or ends while the list is open shows at once.
  const { events } = useEvents(100, EVENT_OPTS);
  const flightKey = useMemo(() => inFlightIds(events).join("|"), [events]);
  const reload = sessions.reload;
  const seenRef = useRef(flightKey);
  useEffect(() => {
    if (flightKey === seenRef.current) return;
    seenRef.current = flightKey;
    setNow(Date.now());
    reload();
  }, [flightKey, reload]);

  const items = useMemo(
    () => runningItems(sessions.data?.sessions ?? null, chats.threads, now),
    [sessions.data, chats.threads, now],
  );
  const shown = items.slice(0, MAX_RUNNING_ROWS);
  const more = items.length - shown.length;
  // Nothing read yet: say so. A failed read (the daemon offline included)
  // never reads as "nothing is running" — the chats it does know still show.
  const first = sessions.data == null && sessions.error == null;
  const failed = sessions.data == null && sessions.error != null;

  return (
    <div data-testid="running-now">
      <div
        id={headingId}
        className="px-3 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500"
      >
        Running now
      </div>
      {first ? (
        <p role="status" className="px-3 py-2 text-[13px] text-zinc-500">
          Checking…
        </p>
      ) : (
        <>
          {items.length > 0 && (
            <ul className="space-y-0.5" aria-label="Running now">
              {shown.map((it) => (
                <li key={it.key}>
                  <Link
                    href={it.href}
                    onClick={() => onNavigate?.()}
                    data-testid="running-row"
                    data-kind={it.kind}
                    data-waiting={it.waiting ? "true" : undefined}
                    title={it.title}
                    aria-label={`${it.line}: ${it.detail}`}
                    className="block rounded-lg px-3 py-2 hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
                  >
                    <span
                      className={`block truncate text-[13px] ${it.waiting ? "text-tone-warn" : "text-zinc-200"}`}
                      data-testid="running-row-line"
                    >
                      {it.line}
                    </span>
                    <span className="block truncate text-[12px] text-zinc-500">{it.detail}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
          {failed ? (
            <p role="status" data-testid="running-now-failed" className="px-3 py-2 text-[13px] text-zinc-400">
              Couldn’t check the runs just now.
            </p>
          ) : (
            items.length === 0 && (
              <p role="status" data-testid="running-now-empty" className="px-3 py-2 text-[13px] text-zinc-400">
                Nothing is running right now.
              </p>
            )
          )}
        </>
      )}
      {more > 0 && (
        <Link
          href="/sessions"
          onClick={() => onNavigate?.()}
          className="block rounded-lg px-3 py-1.5 text-[12px] text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-200"
        >
          {more} more running · All runs
        </Link>
      )}
    </div>
  );
}

export default RunningNow;
