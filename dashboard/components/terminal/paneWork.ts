/**
 * The Build pane chat's adapter onto the chat page's work and changes lines
 * (calm chat wave 5, v1.329.0).
 *
 * The pane draws the SAME two lines /chat draws, through the same components
 * (never a fork):
 *   - WorkLine: "Worked for 3.2 s · read 2 files, ran 1 tool", folded, from
 *     the reply's stored steps, timing, tools and thinking;
 *   - ReplyChanges: "2 files changed +14 −3", only for a reply whose
 *     `documents` lists paths, asked once when the reply is on screen, by the
 *     turn's stored window (lib/turnChanges.turnWindow).
 *
 * The pane's messages (paneChatCore's PaneMsg) did not keep the fields those
 * lines read. This file adds them as an OPTIONAL tail on the pane's own
 * message (`PaneMessage`), maps a finished stream result onto that tail, and
 * maps a stored pane message onto each line's props. A pane message saved
 * before v1.329.0 has none of the tail: its work line is built from
 * `toolsUsed` alone (as /chat does for an older reply) and it gets no changes
 * line (no window to ask by).
 *
 * Types only from '@/lib/useChatStream': tests mock that module with a fixed
 * export list, and a type import is erased.
 */

import type { TurnStep, TurnTiming } from "@/lib/useChatStream";
import type { WorkInput } from "@/components/chat/WorkLine";
import type { TurnChangesArgs } from "@/components/chat/ChangedFiles";
import { turnWindow } from "@/lib/turnChanges";
import type { PaneMsg } from "@/components/terminal/paneChatCore";

/** What the pane now keeps on a message, beside PaneMsg's own fields. All
 *  optional: a thread saved by an older pane (or by /chat) may hold any of
 *  them, none of them, or junk; every reader checks the shape. */
export interface PaneWorkFields {
  /** When the message was sent (user) or settled (reply), ISO. */
  at?: string;
  /** The turn's tool steps, in order, with durations and a safe target. */
  steps?: TurnStep[];
  /** Client-clock timing of the turn. */
  timing?: TurnTiming;
  /** The model's reasoning text. */
  thinking?: string;
  /** How long it thought, whole seconds. */
  thinkingSeconds?: number;
}

export type PaneMessage = PaneMsg & PaneWorkFields;

/** The slice of a finished stream result the pane keeps. */
export interface PaneStreamWork {
  steps?: TurnStep[] | null;
  timing?: TurnTiming | null;
  thinking?: string | null;
  thinkingMs?: number | null;
}

/** The fields a finished turn adds to its reply, in a fixed order, only
 *  when present (the chat page's rule: a field with nothing in it is not
 *  written). `at` is the settle time. Spread LAST on the reply object. */
export function paneReplyWork(res: PaneStreamWork | null | undefined, at: string): PaneWorkFields {
  const steps: TurnStep[] = res && Array.isArray(res.steps) ? res.steps : [];
  const thinking = typeof res?.thinking === "string" ? res.thinking.trim() : "";
  const ms = res?.thinkingMs;
  return {
    at,
    ...(thinking ? { thinking } : {}),
    ...(thinking && typeof ms === "number" && Number.isFinite(ms) && ms > 0
      ? { thinkingSeconds: Math.max(1, Math.round(ms / 1000)) }
      : {}),
    ...(steps.length ? { steps } : {}),
    ...(res?.timing ? { timing: res.timing } : {}),
  };
}

/** A stored pane reply as WorkLine's props. WorkLine checks every field
 *  again (a saved thread crosses a JSON boundary). */
export function paneWorkInput(m: PaneMessage): WorkInput {
  return {
    steps: m.steps ?? null,
    toolsUsed: m.toolsUsed ?? null,
    timing: m.timing ?? null,
    thinking: m.thinking ?? null,
    thinkingSeconds: m.thinkingSeconds ?? null,
  };
}

/** The window ReplyChanges asks about for the reply at `index`, or null
 *  (nothing is fetched): no documents, or no start time to ask from. The
 *  question's send time is the nearest earlier user message's `at`, used
 *  when the reply has no `timing` (the chat page's `prevUserAt`). */
export function paneTurnWindow(
  messages: readonly PaneMessage[],
  index: number,
): TurnChangesArgs | null {
  const m = messages[index];
  if (!m || m.role !== "assistant") return null;
  let prevUserAt: string | undefined;
  for (let j = index - 1; j >= 0; j -= 1) {
    if (messages[j]?.role === "user") {
      const at = messages[j].at;
      prevUserAt = typeof at === "string" ? at : undefined;
      break;
    }
  }
  const timing =
    m.timing &&
    typeof m.timing === "object" &&
    typeof m.timing.startedAt === "number" &&
    typeof m.timing.endedAt === "number"
      ? { startedAt: m.timing.startedAt, endedAt: m.timing.endedAt }
      : null;
  return turnWindow(
    {
      documents: Array.isArray(m.documents) ? m.documents : [],
      timing,
      ...(typeof m.at === "string" ? { at: m.at } : {}),
    },
    prevUserAt,
  );
}
