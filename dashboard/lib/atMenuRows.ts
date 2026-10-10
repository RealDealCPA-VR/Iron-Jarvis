/**
 * Words for the "@" menu's rows (calm chat wave 4, v1.329.0).
 *
 * Chats: saved chats often share a title ("Check what changed in the repo"),
 * and the age on the right ("now", "44m") is the same for two chats made in
 * the same minute. So a chat row carries a quiet second part: the PROJECT it
 * belongs to when that is known, else the day it last changed. Two rows that
 * still read the same (same title, same second part) add the time of day.
 *
 * Agents: a row reads as a name ("Builder", "File manager"; a custom agent as
 * the user named it), in the normal font, with the "@name" you type as a muted
 * hint. Built on `lib/agentWorlds.agentLabel`, the app's one text rule for an
 * agent's name.
 *
 * Pure, so the rules are tested on their own.
 */

import { agentLabel } from "@/lib/agentWorlds";
import type { ChatRefPick } from "@/lib/chatRefs";
import { threadTime } from "@/lib/threadStatus";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** "Today", "Yesterday", "Oct 8", or "Oct 8, 2025" in another year; "" when
 *  there is no usable time. Written out by hand so it reads the same on
 *  every machine. */
export function chatDay(iso: string | null | undefined, now: number = Date.now()): string {
  const t = threadTime(iso);
  if (t == null) return "";
  const d = new Date(t);
  const today = new Date(now);
  if (sameDay(d, today)) return "Today";
  const y = new Date(now);
  y.setDate(y.getDate() - 1);
  if (sameDay(d, y)) return "Yesterday";
  const base = `${MONTHS[d.getMonth()]} ${d.getDate()}`;
  return d.getFullYear() === today.getFullYear() ? base : `${base}, ${d.getFullYear()}`;
}

/** "3:41 PM" (local time); "" when there is no usable time. */
export function chatClock(iso: string | null | undefined): string {
  const t = threadTime(iso);
  if (t == null) return "";
  const d = new Date(t);
  const h = d.getHours();
  const m = String(d.getMinutes()).padStart(2, "0");
  return `${h % 12 === 0 ? 12 : h % 12}:${m} ${h < 12 ? "AM" : "PM"}`;
}

/**
 * Each row's quiet second part, by chat id. `projectName(row)` names the
 * project the chat belongs to, or returns null when that is not known (or the
 * chat has none); then the day stands in. Rows that share a title AND a
 * second part also get the time of day, so they read apart.
 */
export function chatRefSecondary(
  rows: readonly ChatRefPick[],
  projectName: (row: ChatRefPick) => string | null | undefined,
  now: number = Date.now(),
): Map<string, string> {
  const base = new Map<string, string>();
  for (const r of rows) {
    const p = (projectName(r) ?? "").trim();
    base.set(r.id, p || chatDay(r.updatedAt, now));
  }
  const key = (r: ChatRefPick) => `${r.title.trim().toLowerCase()}\u0000${base.get(r.id) ?? ""}`;
  const counts = new Map<string, number>();
  for (const r of rows) counts.set(key(r), (counts.get(key(r)) ?? 0) + 1);
  const out = new Map<string, string>();
  for (const r of rows) {
    const b = base.get(r.id) ?? "";
    const clock = (counts.get(key(r)) ?? 0) > 1 ? chatClock(r.updatedAt) : "";
    out.set(r.id, b && clock ? `${b} · ${clock}` : b || clock);
  }
  return out;
}

/** An "@" agent row's name as words: a built-in's id read as a word
 *  ("builder" → "Builder"), any other agent as it was named. */
export function atAgentName(a: { mention: string; kind?: string }): string {
  return agentLabel(a.mention, { builtin: a.kind === "builtin" });
}
