/**
 * The quiet part on the right of an "@" menu chat row (v1.329.0, calm chat
 * W8 J2): the SAME rule as the chat lists (ThreadGroups / SidebarChats).
 *
 *   a title of its own        the list's age: "now", "12m", "3h", "2d"
 *   a title another row has   `lib/sameTitleRows.sameTitleLabels`: ONE short
 *                             time ("8:48 PM") or day ("Yesterday", "Oct 8"),
 *                             in place of the age, never both
 *
 * The menu used to draw the project (or the day, plus the time on a tie) AND
 * the age, so a same-titled row on a phone read "Check what changed in
 * the… Yesterday · 8:48… 3h": the double label W6 H1 took out of both lists.
 * The exact time with seconds rides the twin label's tooltip, as in the lists.
 *
 * "Same title" is judged over the rows the menu draws (every chat match is
 * drawn; the squeezed fit caps only the sections ABOVE the chats).
 *
 * Pure, so the rule is tested on its own. Its own module because
 * `lib/sameTitleRows` already imports from `lib/atMenuRows`.
 */

import type { ChatRefPick } from "@/lib/chatRefs";
import { sameTitleLabels, sameTitleTooltip } from "@/lib/sameTitleRows";
import { formatAge } from "@/lib/threadStatus";

export interface ChatRefRowPart {
  /** "twin": the same-title time or day; "age": the list's short age. */
  kind: "twin" | "age";
  text: string;
  /** The exact time for a twin ("Today · 8:48:05 PM"); "" for an age. */
  tooltip: string;
}

/**
 * Each row's one quiet part, by chat id. A row with no usable time is not in
 * the map (it draws nothing on the right).
 */
export function chatRefRowParts(
  rows: readonly ChatRefPick[],
  now: number = Date.now(),
): Map<string, ChatRefRowPart> {
  const twins = sameTitleLabels(
    rows.map((r) => ({ id: r.id, title: r.title, updated_at: r.updatedAt ?? null })),
    now,
  );
  const out = new Map<string, ChatRefRowPart>();
  for (const r of rows) {
    const twin = twins.get(r.id);
    if (twin) {
      out.set(r.id, { kind: "twin", text: twin, tooltip: sameTitleTooltip(r.updatedAt, now) });
      continue;
    }
    const age = formatAge(r.updatedAt, now);
    if (age) out.set(r.id, { kind: "age", text: age, tooltip: "" });
  }
  return out;
}
