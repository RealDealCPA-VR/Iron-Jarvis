// THE RECEIPT'S TENSE (v1.310.0) — one rule for the coordinator's receipt
// (MissionOutput) and every teammate card's model line (AgentCards).
//
// A provider/model pair on a row is what the orchestrator STAMPED when the run
// was created, not proof that anything answered (v1.309.0 review: a mission
// refused because its default model was down read "Answered by fleet-custom").
// So the verb follows what the status says actually happened: only a finished
// run was answered; one still at work is working on it; a failed one tried
// it; a stopped (or restart-interrupted) one ran on it. Before v1.310.0 the
// teammate cards said "Ran on" whatever the teammate's status — a failed
// teammate read as if its model had done the work.
//
// Pure and import-free on purpose (no lib/api, no React): ~71 test files mock
// lib/api wholesale, and both screens import this.

/** The verb for a run in one of the daemon's states. `status` is either a
 *  session status (completed | failed | cancelled | active | queued) or a
 *  teammate's MemberStatus (done | failed | cancelled | working | waiting_you
 *  | queued); both vocabularies are answered here so the two screens can
 *  never drift. `interrupted` = a restart cut the run off (never "tried"). */
export function receiptVerbFor(status: string, interrupted = false): string {
  switch (status) {
    case "completed":
    case "done":
      return "Answered by";
    case "failed":
      return interrupted ? "Ran on" : "Tried";
    case "cancelled":
      return "Ran on";
    case "queued":
      // A teammate that has not started has not touched its model yet.
      return "Will run on";
    default:
      // active / working / waiting_you — still at it.
      return "Working on";
  }
}
