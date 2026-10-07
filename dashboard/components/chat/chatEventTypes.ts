/**
 * The /events types the chat page actually READS (v1.311.0, wave 3,
 * events-unfiltered-page-subscribers).
 *
 * ChatPage subscribes to the event feed at its root, and every frame used to
 * re-run the whole 9,000-line page: the router's provider.routed on every LLM
 * call, llm.completed, and — with the Chrome add-on paired — every tab switch
 * and page load in the user's real browser. Most of those (about half the
 * stored events in a real install) are types nothing on the page looks at, and
 * each one competed with the streaming reply and the composer. `useEvents`
 * drops a frame not on this list BEFORE any state update, so it costs the
 * page no render at all.
 *
 * A TYPE MISSING HERE IS A SILENT REGRESSION — a thread that never refreshes,
 * an approval that never appears, a progress line that stays blank — so the
 * list is pinned against SOURCE (chat-wave3-v1311.test.tsx derives every
 * `.type === "x.y"` the page, ArtifactsRail and BatchSuggestCard compare to,
 * and every `case` in stepLabel.ts). Add a consumer, add its type here.
 *
 * Its own module because a Next page may not export anything but its
 * component and route config. Module-level and never rebuilt, so the hook
 * sees one stable list.
 */
export const CHAT_EVENT_TYPES: readonly string[] = Object.freeze([
  // The sidebar refreshes when a thread changes elsewhere (another window,
  // the comm bridge, a remote reply).
  "chat.thread_updated",
  "agent_thread.updated",
  "remote.message",
  // The agent turn: finalize on completion, ask/clear approval cards.
  "agent.completed",
  "approval.requested",
  "approval.resolved",
  // The undo fold (revertedActionIds) and the folder-batch card's progress.
  "action.reverted",
  "batch.file_done",
  // stepLabel — the human-readable progress lines of an agent turn.
  "agent.started",
  "agent.state_changed",
  "tool.executed",
  "tool.denied",
  "provider.failed",
  "provider.downgraded",
  "plan.created",
  "plan.step_started",
  "plan.step_completed",
  "envelope.adapted",
]);
