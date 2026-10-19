/**
 * v1.354.0 (G47): the words of the model-server card on Settings, Connections
 * (the daemon's "custom" slot plus every endpoint the user saved on it).
 *
 * Kept outside the page so the page and its tests read the SAME words, and
 * dependency-free (no lib/api import: ~71 test files mock that module whole).
 */

/** The card's title. The daemon still names the slot "Custom endpoint"; that
 *  name stays on the slot's own saved row and in the title's tooltip. */
export const MODEL_SERVERS_TITLE = "Model servers on your network";

/** The status pill on that card while a keyless server on it answers. */
export const OWN_ENDPOINT_LABEL = "Connected · your endpoint · no key needed";

/** The card title for one connection row: only the custom slot is renamed. */
export function connectionTitle(conn: { provider: string; display_name: string }): string {
  return conn.provider === "custom" ? MODEL_SERVERS_TITLE : conn.display_name;
}

/**
 * True when the daemon reports this row connected THROUGH a keyless model
 * server that answers (`connections/registry.ENDPOINT_SOURCE`, the row's
 * `source`). Keyed on its leading words, the same way `inheritedVia` reads
 * "inherited from …", so a stored key (`source: "vault"`) never matches.
 */
export function isOwnEndpoint(conn: { connected?: boolean; source?: string | null }): boolean {
  return Boolean(conn.connected) && /^your endpoint\b/.test(conn.source ?? "");
}

/**
 * The note after a save whose live check could not reach the server. The
 * endpoint IS saved (each save is its own provider), so this is a warning,
 * never the green "Endpoint saved" it used to be, and it keeps the check's
 * own error so a mistyped address says what went wrong.
 */
export function unreachableNote(address: string, error: string): string {
  const where = address.trim() || "the server";
  const why = error.trim().replace(/[.\s]+$/, "");
  return (
    `Saved, but Jarvis could not reach ${where}${why ? `: ${why}` : ""}. ` +
    "Start the server or check the address, then press Verify tools."
  );
}

/** v1.355.0 integrate: the note after a save whose check REACHED the server
 *  and got an error back (a wrong model name, a refused key). It must never
 *  say "could not reach" — the server answered. (v1.355.0 re-review: moved
 *  below `unreachableNote`, whose doc comment it had been inserted under.) */
export function answeredWithErrorNote(address: string, error: string): string {
  const where = address.trim() || "The server";
  const why = error.trim().replace(/[.\s]+$/, "");
  return (
    `Saved. ${where} answered, but with an error${why ? `: ${why}` : ""}. ` +
    "Check the model name and the key, then press Verify tools."
  );
}
