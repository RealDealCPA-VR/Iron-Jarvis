/**
 * The one-click remedy for a failed turn (v1.275.0).
 *
 * The page already knows which providers are reachable (`useProviderHealth`),
 * so when the turn failed on an EXPLICIT pick whose provider is down while the
 * default provider is up, the banner can offer "Retry with the default model"
 * instead of leaving the user to open the three-level model menu after having
 * typed the whole request. Only that case: falling back to a different
 * provider is the user's choice, never a routing decision (the v1.162.0 rule),
 * so nothing here picks a provider the user did not already have as default.
 */

export interface FallbackHealth {
  byProvider: Record<string, boolean>;
  defaultProvider: string;
}

/** `provider::model` as the page encodes an explicit pick; "" is the default. */
export function providerOf(choice: string): string {
  const i = choice.indexOf("::");
  return i === -1 ? "" : choice.slice(0, i);
}

/**
 * Whether the failed turn's explicit provider is known-down while the default
 * provider is known-up — the only case a one-click "use the default" is honest.
 */
export function canRetryWithDefault(choice: string, health: FallbackHealth): boolean {
  const picked = providerOf(choice);
  if (!picked) return false; // already on the default: Retry is the remedy
  const dflt = health.defaultProvider;
  if (!dflt || dflt === picked) return false;
  if (health.byProvider[picked] !== false) return false; // not KNOWN down
  return health.byProvider[dflt] === true; // and the default KNOWN up
}
