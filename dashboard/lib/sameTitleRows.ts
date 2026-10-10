/**
 * Telling same-titled chats apart in the chat list (v1.329.0, calm chat W5 G3).
 *
 * Saved chats often share a title ("Check what changed in the repo" four
 * times), and the short age on the right ("31m") can match too. So when two or
 * more VISIBLE rows share a title, each of those rows (and only those) gets a
 * quiet second part, kept as short as still tells them apart:
 *
 *   all on the same day   the time of day: "8:48 PM" (today),
 *                         "Yesterday · 8:48 PM", "Oct 8 · 8:48 PM"
 *   on different days     the day: "Yesterday", "Oct 8"; two that share a
 *                         day as well add the time ("Oct 8 · 8:48 PM")
 *
 * Two that still read the same (made in the same minute) show the seconds
 * too ("6:11:05 PM").
 *
 * The words come from the @ menu's own helpers (lib/atMenuRows: chatDay,
 * chatClock), so both places say a chat's time the same way.
 *
 * Pure: the list draws what this returns.
 */

import { chatClock, chatDay } from "@/lib/atMenuRows";
import { threadTime } from "@/lib/threadStatus";

export interface TitledRow {
  id: string;
  title?: string | null;
  updated_at?: string | null;
}

/** The title the list draws, folded for comparing ("" reads as the list's
 *  "Untitled chat"). */
function titleKey(t: TitledRow): string {
  return ((t.title || "").trim() || "Untitled chat").toLowerCase();
}

function join(a: string, b: string): string {
  return a && b ? `${a} · ${b}` : a || b;
}

/** "6:11:05 PM" (local time); "" when there is no usable time. */
function clockWithSeconds(iso: string | null | undefined): string {
  const t = threadTime(iso);
  if (t == null) return "";
  const d = new Date(t);
  const h = d.getHours();
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${h % 12 === 0 ? 12 : h % 12}:${mm}:${ss} ${h < 12 ? "AM" : "PM"}`;
}

/**
 * The second part for each row that shares its title with another visible
 * row, by id. Rows with a title of their own are not in the map.
 */
export function sameTitleLabels(
  rows: readonly TitledRow[],
  now: number = Date.now(),
): Map<string, string> {
  const byTitle = new Map<string, TitledRow[]>();
  for (const r of rows) {
    const k = titleKey(r);
    const list = byTitle.get(k);
    if (list) list.push(r);
    else byTitle.set(k, [r]);
  }
  const out = new Map<string, string>();
  for (const twins of byTitle.values()) {
    if (twins.length < 2) continue;
    const days = twins.map((r) => chatDay(r.updated_at, now));
    const oneDay = days.every((d) => d === days[0]);
    const labelFor = (i: number, clock: string): string => {
      const day = days[i];
      if (oneDay) return day === "Today" ? clock : join(day, clock);
      const shareDay = days.filter((d) => d === day).length > 1;
      return shareDay ? join(day, clock) : day;
    };
    const labels = twins.map((r, i) => labelFor(i, chatClock(r.updated_at)));
    twins.forEach((r, i) => {
      // Still the same words as another row: the seconds tell them apart.
      const tie = labels.some((l, j) => j !== i && l === labels[i]);
      const label = tie ? labelFor(i, clockWithSeconds(r.updated_at)) : labels[i];
      if (label) out.set(r.id, label);
    });
  }
  return out;
}
