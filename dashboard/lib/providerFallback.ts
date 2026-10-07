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

/** What the failed turn's own provider is going through (v1.312.0). */
export interface ProviderTrouble {
  /** The effective provider: the explicit pick, else the default ("" = none known). */
  provider: string;
  /** KNOWN down — `/health` said so. Unknown is never down. */
  down: boolean;
  /** Seconds left on a cooldown; 0 when there is none. */
  cooldownS: number;
}

/**
 * The failed-turn row's facts, and nothing more (v1.312.0). Retry used to
 * stay pressable while the provider was cooling down — refused at once with
 * the same words — and a turn whose DEFAULT was down offered only that Retry.
 * The page now says "Retry in Ns" through a cooldown and offers the model
 * menu when the provider is known down. This only REPORTS: it names no other
 * provider, because which model to use instead is the user's call (v1.162.0).
 */
export function providerTrouble(
  choice: string,
  health: FallbackHealth & { cooldownByProvider?: Record<string, number> },
): ProviderTrouble {
  const provider = providerOf(choice) || health.defaultProvider || "";
  if (!provider) return { provider: "", down: false, cooldownS: 0 };
  // Optional-chained like the page's other reads (v1.232.0): the cooldown map
  // is newer than the hook's other fields.
  const raw = health.cooldownByProvider?.[provider];
  const cooldownS = typeof raw === "number" && Number.isFinite(raw) && raw > 0 ? Math.ceil(raw) : 0;
  return { provider, down: health.byProvider[provider] === false, cooldownS };
}
