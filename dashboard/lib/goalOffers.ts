/**
 * "Not now" on an offer to let a goal use a tool without asking (v1.209.0)
 * is remembered in this browser, per goal and tool. One place for the key,
 * because two screens read it: Autonomy (where the offer is answered) and
 * the Improvements page (v1.495.0, which only lists offers still open).
 */

export const OFFER_DISMISS_PREFIX = "ij_goal_offer_dismissed:";

/** The part after the prefix: `<goal>:<tool>` or `<goal>:<tool>:<hash>`. */
export function offerKey(goalId: string, tool: string, argsHash = ""): string {
  return argsHash ? `${goalId}:${tool}:${argsHash}` : `${goalId}:${tool}`;
}

/** True when this browser said "Not now" to that offer. Never throws. */
export function offerDismissed(goalId: string, tool: string, argsHash = ""): boolean {
  try {
    return window.localStorage.getItem(`${OFFER_DISMISS_PREFIX}${offerKey(goalId, tool, argsHash)}`) === "1";
  } catch {
    return false;
  }
}
