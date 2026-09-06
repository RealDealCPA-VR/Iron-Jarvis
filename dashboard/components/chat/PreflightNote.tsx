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
 * v1.232.0 (audit R4): a provider that is reachable but IN COOLDOWN — the
 * router's circuit breaker tripped on repeated failures — is the second
 * preflight case (`cooldownS > 0`). The daemon refuses a turn sent to it with
 * "in cooldown, retry in N s"; this row says the same words first, so the
 * user does not type a paragraph into a model that will not be asked.
 * Unreachable wins over cooldown when both are true.
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
  cooldownS,
  signedOut,
}: {
  provider: string;
  available: boolean | undefined;
  stale?: boolean;
  /** Seconds left in the router's cooldown for `provider` (0/undefined = closed). */
  cooldownS?: number;
  /** v1.234.0: `provider` is a subscription CLI that is installed but NOT
   *  signed in. The daemon refuses the turn with the CLI's own remedy; this
   *  row says it first. Beats the generic "isn't reachable" wording, which
   *  sent a user to debug an endpoint that was never the problem. */
  signedOut?: boolean;
}) {
  const cooldown = available !== false && (cooldownS ?? 0) > 0;
  if (available !== false && !cooldown) return null;
  const signedOutCase = available === false && Boolean(signedOut);
  const signInWords = /codex/i.test(provider)
    ? "run `codex login` in a terminal"
    : "run `claude` in a terminal, then /login";
  return (
    <div
      role="status"
      data-testid="ij-preflight-note"
      data-kind={cooldown ? "cooldown" : signedOutCase ? "signed-out" : "unreachable"}
      className="flex h-5 min-h-5 items-center gap-1.5 overflow-hidden px-1 text-[11px] leading-none text-amber-300"
    >
      <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />
      <span className="truncate">
        {cooldown
          ? `${provider} is in cooldown, retry in ${cooldownS} s — it failed repeatedly, so a turn sent to it now is refused. Pick another model or wait.`
          : signedOutCase
            ? `${provider} is installed but not signed in — this turn will fail. ${signInWords[0].toUpperCase()}${signInWords.slice(1)}, then Test on Connections.`
          : stale
            ? `${provider} may be offline — the last check couldn't reach the daemon. Pick another model or check the endpoint.`
            : `${provider} isn't reachable right now — this turn will fail. Pick another model or bring the endpoint back.`}
      </span>
    </div>
  );
}
