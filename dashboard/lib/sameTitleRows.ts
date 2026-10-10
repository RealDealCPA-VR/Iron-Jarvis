/**
 * Telling same-titled chats apart in the chat list (v1.329.0, calm chat W5 G3).
 *
 * Saved chats often share a title ("Check what changed in the repo" four
 * times), and the short age on the right ("31m") can match too. So when two or
 * more VISIBLE rows share a title, each of those rows (and only those) gets a
 * quiet second part, ONE short part on every row:
 *
 *   shares its day with another twin   the time of day: "8:48 PM" (on any
 *                                      day; today, yesterday or Oct 8)
 *   alone on its day                   the day: "Today", "Yesterday", "Oct 8"
 *
 * The day always rides the tooltip ("Yesterday · 8:48:05 PM"). Only in the
 * rare tie where twins on DIFFERENT days would read the same time do those
 * rows say both ("Oct 8 · 8:48 PM").
 *
 * v1.329.0 (calm chat W6 H1): the second part takes the AGE's place on those
 * rows (the list draws one or the other, never both), and it is always the
 * short time, never seconds: "6:11:46 PM 5h" clipped the title to about 12
 * characters. Two made in the same minute read alike; the exact time (with
 * seconds) is in the label's tooltip (`sameTitleTooltip`), and the list keeps
 * them in order, newest first.
 *
 * The words come from the @ menu's own helpers (lib/atMenuRows: chatDay,
 * chatClock), so both places say a chat's time the same way. Since W8 J2 the
 * @ menu's chat rows follow this rule too (lib/chatRefsRows): one rule for
 * the sidebar list, the page's rail and the menu.
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

/** The exact time for a same-titled row's tooltip: "Today · 6:11:05 PM",
 *  "Oct 8 · 6:11:05 PM" ("" when there is no usable time). */
export function sameTitleTooltip(
  iso: string | null | undefined,
  now: number = Date.now(),
): string {
  const clock = clockWithSeconds(iso);
  return clock ? join(chatDay(iso, now), clock) : "";
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
    // ONE short part per row, whatever the day: a twin that shares its day
    // with another twin says the time ("8:48 PM", never seconds), a twin
    // alone on its day says the day ("Yesterday", "Oct 8"). The day is in
    // the tooltip either way. "Yesterday · 8:48 PM" clipped the title to
    // about 10 characters on a 15rem row.
    const short = twins.map((r, i) => {
      const shareDay = days.filter((d) => d === days[i]).length > 1;
      return shareDay ? chatClock(r.updated_at) : days[i];
    });
    twins.forEach((r, i) => {
      // The rare tie: two twins on DIFFERENT days would read the same time
      // (8:48 PM yesterday and 8:48 PM on Oct 8). Only then add the day.
      // Two made in the same minute of the same day read alike on purpose;
      // the tooltip has the seconds and the list keeps them in order.
      const clash = short.some(
        (s, j) => j !== i && s === short[i] && days[j] !== days[i],
      );
      const label = clash ? join(days[i], short[i]) : short[i];
      if (label) out.set(r.id, label);
    });
  }
  return out;
}
