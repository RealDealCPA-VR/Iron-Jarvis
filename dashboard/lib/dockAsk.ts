// Calm chat W1-6 (v1.326.0): a question for you takes the composer's place.
//
// The pure half of the dock ask: which questions are open right now, in what
// order, and what the keys under the card say. The card itself lives in
// components/chat/DockAsk.tsx.
//
// THE QUESTIONS, in one list:
//   - the approval a chat turn is paused on (one slot, from the stream),
//   - an app's open questions from that turn (an MCP elicitation, or a
//     request to use the turn's model); an answered one has an `outcome`
//     and is not a question any more,
//   - the approvals an escalated run is paused on (a batch can be several).
//   - v1.332.0: the page's own question before a send in a "local only"
//     chat whose model would leave this PC (`cloud`, client-side: nothing is
//     running while it is up, so it is the only one).
// The screen shows the FIRST, with "1 of N" when there are more.
//
// THE FIRST STAYS FIRST: a question that arrives while the user is halfway
// through another (typing into an app's form, reading a command) never
// pushes it aside. `withHeadFirst` keeps the one on screen at the front for
// as long as it is still open.

import type { PendingApproval } from "@/lib/useChatStream";
import type { McpAsk, McpElicitationAsk, McpSamplingAsk } from "@/lib/mcpInteract";
import type { CloudAsk } from "@/lib/localOnly";
import type { AskUserAsk } from "@/lib/buildSessions";
import { onceReasonOf } from "@/lib/onceReason";

export type DockAskItem =
  | { kind: "approval"; id: string; approval: PendingApproval }
  | { kind: "elicitation"; id: string; ask: McpElicitationAsk }
  | { kind: "sampling"; id: string; ask: McpSamplingAsk }
  | { kind: "cloud"; id: string; ask: CloudAsk }
  /** v1.369.0: a question the model itself asked (`ask_user`, a Build
   *  session's turn), answered in the dock like an app's. */
  | { kind: "question"; id: string; ask: AskUserAsk };

/** Every open question, in the order they are answered. */
export function collectDockAsks({
  approval,
  mcpAsks,
  sessionApprovals,
  cloud,
  questions,
}: {
  approval?: PendingApproval | null;
  mcpAsks?: readonly McpAsk[] | null;
  sessionApprovals?: readonly PendingApproval[] | null;
  /** v1.332.0: the send waiting on "Send once" / "Keep it local". */
  cloud?: (CloudAsk & { id: string }) | null;
  /** v1.369.0: the model's own open questions (a Build session's turn). */
  questions?: readonly AskUserAsk[] | null;
}): DockAskItem[] {
  const out: DockAskItem[] = [];
  const seen = new Set<string>();
  const push = (item: DockAskItem) => {
    if (seen.has(item.id)) return;
    seen.add(item.id);
    out.push(item);
  };
  if (cloud) push({ kind: "cloud", id: cloud.id, ask: cloud });
  if (approval) push({ kind: "approval", id: approval.id, approval });
  for (const a of mcpAsks ?? []) {
    if (a.outcome) continue; // answered or ended: a record, not a question
    if (a.kind === "elicitation") push({ kind: "elicitation", id: a.id, ask: a });
    else push({ kind: "sampling", id: a.id, ask: a });
  }
  for (const s of sessionApprovals ?? []) push({ kind: "approval", id: s.id, approval: s });
  for (const q of questions ?? []) {
    if (q.outcome) continue; // answered, skipped or ended: not a question now
    push({ kind: "question", id: q.id, ask: q });
  }
  return out;
}

/** The list with the question already on screen moved to the front (when it
 *  is still open); otherwise the list as it is. */
export function withHeadFirst(list: readonly DockAskItem[], headId: string | null): DockAskItem[] {
  if (!headId) return [...list];
  const i = list.findIndex((x) => x.id === headId);
  if (i <= 0) return [...list];
  return [list[i], ...list.slice(0, i), ...list.slice(i + 1)];
}

/** v1.381.0 review: whether Enter may press this question's main answer.
 *
 *  NOT on an approval that asks ONCE, every time, for a SAFETY reason — a
 *  shell step a project's or a plugin's slash command asked for, a commit or
 *  push on a Build session's own branch, a click in the agent's browser that
 *  reads as deleting, sending or paying (`once_reason`, lib/onceReason). In
 *  the live check the user typed `/status`, pressed Enter twice, and the
 *  second Enter — meant for the composer, before the card was read — ran
 *  the project's command. The first-moment guard (DOCK_KEY_ARM_MS) is not
 *  enough: a model can put the card up later than that. Such a card is
 *  answered by a click (or Tab to "Allow once", then Enter or Space on the
 *  button itself — a deliberate press); Esc still declines. Every other
 *  question keeps Enter exactly as before, a local-only card and the plan
 *  door included. */
export function enterAllows(item: DockAskItem | null | undefined): boolean {
  if (!item || item.kind !== "approval") return true;
  const a = item.approval;
  return !(a.onceOnly === true && onceReasonOf(a.onceReason) !== undefined);
}

/** The keys line for a card whose Enter does not allow it (enterAllows). */
export const CLICK_TO_ALLOW_HINT = "Click Allow once to run it · Esc to decline";

/** The keys line under the card while a question is in the composer's place.
 *  `item` (v1.381.0 review): the question itself, so a card Enter does not
 *  allow says so instead of promising "Enter to allow". */
export function dockAskKeyHint(kind: DockAskItem["kind"], item?: DockAskItem | null): string {
  if (item && !enterAllows(item)) return CLICK_TO_ALLOW_HINT;
  if (kind === "cloud") return "Enter to send once · Esc to keep it local";
  if (kind === "question") return "Enter to send · Esc to skip";
  return kind === "elicitation" ? "Enter to send · Esc to decline" : "Enter to allow · Esc to decline";
}

/** Elements whose own Enter means something (a button presses, a field
 *  submits or types, a <summary> folds): the dock never takes Enter from them. */
const OWNS_ENTER =
  'button, a[href], input, select, textarea, summary, [contenteditable=""], [contenteditable="true"], [role="button"], [role="checkbox"], [role="combobox"], [role="listbox"], [role="textbox"], [role="menuitem"]';

/** Elements that take typing (Esc may mean "close my dropdown" there; a
 *  letter typed there is a letter, not a lettered pick — lib/letteredChoices
 *  reads this same selector). */
export const TAKES_TYPING =
  'input:not([type="button"]):not([type="submit"]):not([type="reset"]):not([type="checkbox"]):not([type="radio"]), select, textarea, [contenteditable=""], [contenteditable="true"], [role="combobox"], [role="textbox"]';

/** What a key pressed with `target` focused does to the dock question:
 *  "primary" (Allow / Send), "decline", or nothing. `inside` = the target is
 *  inside the dock card itself (else the page body has focus). */
export function dockKeyAction(
  e: Pick<KeyboardEvent, "key" | "shiftKey" | "ctrlKey" | "altKey" | "metaKey" | "repeat" | "isComposing">,
  target: Element | null,
  inside: boolean,
): "primary" | "decline" | null {
  if (e.isComposing || e.repeat) return null;
  if (e.shiftKey || e.ctrlKey || e.altKey || e.metaKey) return null;
  if (e.key !== "Enter" && e.key !== "Escape") return null;
  if (inside && target) {
    if (e.key === "Enter" && target.closest(OWNS_ENTER)) return null;
    if (e.key === "Escape" && target.closest(TAKES_TYPING)) return null;
  }
  return e.key === "Enter" ? "primary" : "decline";
}
