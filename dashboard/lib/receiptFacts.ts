/**
 * THE RECEIPT FACTS A REPLY KEEPS (harness gaps, fix 3).
 *
 * The chat page hands its receipt (components/chat/TurnReceipt) six facts a
 * Build coding session and a Build terminal's chat used to drop — so a Build
 * reply never said which cards the user allowed (a real Build reply carried
 * `approved: [{shell, conversation}, {process_start, conversation}]` and the
 * screen showed none) or that older messages were left out to fit:
 *
 *   approved     the approval cards the user answered "allow"
 *   refit        older messages left out to retry a too-long request
 *   adapted      the capability envelope bent this turn
 *   remembered   a preference kept ("Remembered: …")
 *   folderRules  the project folder's instruction files read
 *   threadRefs   earlier chats read with "@"
 *
 * Read off a SAVED reply (a JSON boundary: anything can be in it) and decoded
 * the chat page's own way (lib/turnRecovery, lib/turnReads), so one bad value
 * never reaches the receipt. Spread onto <TurnReceipt …/> by both Build
 * surfaces; a field with nothing to say is left out.
 *
 * Pure; type imports only.
 */

import type { TurnAdapted } from "@/components/chat/TurnReceipt";
import { decodeApproved, decodeRefit, type ApprovedCall, type TurnRefit } from "@/lib/turnRecovery";
import { decodeFolderRules, decodeThreadRefs, type ThreadRefReceipt } from "@/lib/turnReads";

export interface ReceiptFacts {
  approved?: ApprovedCall[];
  refit?: TurnRefit;
  adapted?: TurnAdapted;
  remembered?: string[];
  folderRules?: string[];
  threadRefs?: ThreadRefReceipt[];
}

/** The envelope's adaptation as the chat page keeps it (its `adaptedFrom`):
 *  only a list of changes worth saying. */
export function decodeAdapted(raw: unknown): TurnAdapted | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const a = raw as { model?: unknown; changes?: unknown };
  if (!Array.isArray(a.changes)) return null;
  const changes = a.changes.filter((c): c is string => typeof c === "string" && c.trim().length > 0);
  if (changes.length === 0) return null;
  return { ...(typeof a.model === "string" && a.model.trim() ? { model: a.model } : {}), changes };
}

function strings(raw: unknown, cap = 20): string[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((x): x is string => typeof x === "string" && x.trim().length > 0)
    .map((x) => x.trim().slice(0, 500))
    .slice(0, cap);
}

/** The six facts of a saved reply, decoded; absent when there is nothing. */
export function receiptFacts(m: {
  approved?: unknown;
  refit?: unknown;
  adapted?: unknown;
  remembered?: unknown;
  folderRules?: unknown;
  threadRefs?: unknown;
}): ReceiptFacts {
  const approved = decodeApproved(m.approved);
  const refit = decodeRefit(m.refit);
  const adapted = decodeAdapted(m.adapted);
  const remembered = strings(m.remembered);
  const folderRules = decodeFolderRules(m.folderRules);
  const threadRefs = decodeThreadRefs(m.threadRefs);
  return {
    ...(approved.length ? { approved } : {}),
    ...(refit ? { refit } : {}),
    ...(adapted ? { adapted } : {}),
    ...(remembered.length ? { remembered } : {}),
    ...(folderRules.length ? { folderRules } : {}),
    ...(threadRefs.length ? { threadRefs } : {}),
  };
}
