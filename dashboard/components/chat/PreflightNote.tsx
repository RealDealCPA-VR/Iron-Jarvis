"use client";

/**
 * Preflight warning for the chat composer: the ACTIVE provider is known to be
 * unreachable, so the turn the user is about to type WILL fail. Shown before
 * sending — the whole point is to move the bad news from postmortem to
 * preflight (a user once typed a full request against a dead fleet-custom
 * endpoint and only learned after the turn failed).
 *
 * Renders null unless `available === false` (unknown availability is NOT a
 * warning — never cry wolf while /health is still loading). When the health
 * data itself is stale (the last check couldn't reach the daemon), the message
 * softens: we no longer KNOW the provider is down, we only failed to check.
 *
 * Deliberately: no buttons, one fixed-height single line (h-5 + truncate) so a
 * message SWAP (hard warning ↔ stale softening, or a long provider name) never
 * changes the row's height. Honesty note: the component returns null when
 * healthy, so the row APPEARING does insert one 20px line — reserving a
 * permanently empty strip above the composer was judged worse. If a mount site
 * wants literal zero-shift, it must render its own h-5 placeholder.
 */
export function PreflightNote({
  provider,
  available,
  stale,
}: {
  provider: string;
  available: boolean | undefined;
  stale?: boolean;
}) {
  if (available !== false) return null;
  return (
    <div
      role="status"
      data-testid="ij-preflight-note"
      className="flex h-5 min-h-5 items-center gap-1.5 overflow-hidden px-1 text-[11px] leading-none text-amber-300"
    >
      <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />
      <span className="truncate">
        {stale
          ? `${provider} may be offline — the last check couldn't reach the daemon. Pick another model or check the endpoint.`
          : `${provider} isn't reachable right now — this turn will fail. Pick another model or bring the endpoint back.`}
      </span>
    </div>
  );
}
