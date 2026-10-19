/**
 * IRON JARVIS, NOT "THE DAEMON" (v1.354.0, product-readiness G30/G31).
 *
 * About 65 strings a user could read named the engine's PROCESS: the banner
 * on every page said "Daemon offline.", a chat bubble said "Nothing arrived
 * from the daemon for 90 s…", the Status headline said "Daemon offline" and
 * the splash said "Booting the daemon and dashboard". A new user does not
 * know there is a daemon; they know Iron Jarvis. These are the words every
 * shell surface says instead, in ONE place so they cannot drift.
 *
 * Dependency-free on purpose: `lib/api.ts` (mocked wholesale by ~71 test
 * files), `lib/useChatStream.ts` and `components/ui.tsx` all read it, and a
 * lib must never import a component module.
 */

/** The outage, in the app's own name. Also the message of the status-0
 *  `ApiError` (lib/api.ts, lib/archiveChat.ts) and the chat stream's offline
 *  frame, so any surface that prints `err.message` says this. */
export const OFFLINE_WORDS = "Iron Jarvis isn't responding";

/** The banner's and the in-page hint's headline. */
export const OFFLINE_TITLE = `${OFFLINE_WORDS}.`;

/** What was NOT affected (the audit's "what was not affected" row): nothing
 *  the user made is lost, and a cut-off job comes back as an offer (the
 *  boot reconcile + GET /sessions/interrupted, v1.249.0). */
export const OFFLINE_SAVED_SENTENCE =
  "Your chats and files are saved; anything that was running will be offered back.";

/** The token banner's headline (was "Daemon rejected your token."). */
export const SIGNED_OUT_TITLE = "This window isn't signed in to Iron Jarvis.";

/** A data request that failed with a real (non-0) answer (was "A request to
 *  the daemon failed."). The page's own polls re-read it. */
export const PART_FAILED_TITLE = "Part of this page didn't load. It will try again in a moment.";

/** G31: the line the banner shows for a few seconds when the service answers
 *  again after an outage — the banner used to just vanish. */
export const BACK_TITLE = "Iron Jarvis is back.";

/** G31: said beside BACK_TITLE only when the restart cut work off (the bell
 *  lists it with Continue / Dismiss — components/InterruptedJobs). */
export const BACK_STOPPED_LINE = "Anything it stopped is in the bell.";

/** G31: how long the "is back" line stays. Long enough to read, short enough
 *  that it never becomes chrome. */
export const BACK_LINGER_MS = 5000;
