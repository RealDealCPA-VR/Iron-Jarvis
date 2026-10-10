/**
 * Words for the "@" menu's rows (calm chat wave 4, v1.329.0).
 *
 * Chats: the words for a chat's day and time (`chatDay`, `chatClock`), which
 * the chat lists' same-title rule (lib/sameTitleRows) reads. Since v1.329.0
 * (W8 J2) the menu's chat rows follow that same rule (lib/chatRefsRows): the
 * age, or for a shared title one short time or day in its place.
 *
 * Agents: a row reads as a name ("Builder", "File manager"; a custom agent as
 * the user named it), in the normal font, with the "@name" you type as a muted
 * hint. Built on `lib/agentWorlds.agentLabel`, the app's one text rule for an
 * agent's name.
 *
 * Pure, so the rules are tested on their own.
 */

import { agentLabel } from "@/lib/agentWorlds";
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

/** An "@" agent row's name as words: a built-in's id read as a word
 *  ("builder" → "Builder"), any other agent as it was named. */
export function atAgentName(a: { mention: string; kind?: string }): string {
  return agentLabel(a.mention, { builtin: a.kind === "builtin" });
}
