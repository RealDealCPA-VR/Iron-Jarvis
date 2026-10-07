/**
 * The draft card's VALUE helpers, in a module of their own (v1.311.0).
 *
 * They used to live in `EmailComposeDialog.tsx`, and `DraftCard` calls
 * `draftHeaders` during render. Bundling is per MODULE, so that one value
 * import kept the whole compose dialog (its portal, channel picker and file
 * list) on the chat route even after the dialog itself was wrapped in
 * `next/dynamic` — the v1.258.0 "a component is not its own chunk" trap. Here
 * they have no imports at all, so the card can read its headers while the
 * dialog downloads only when someone presses Save or Send.
 *
 * `EmailComposeDialog.tsx` re-exports both, so its existing importers (and
 * `email-compose-c06.test.tsx`) are unchanged.
 */

/** "a@x.com, Ann <b@y.com>; c@z.com" → one string per address. */
export function splitAddresses(raw: string): string[] {
  return raw
    .split(/[,;]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * The draft's own "To:" / "Cc:" header lines, when the model wrote them at the
 * top of the body (only the first few lines count — a "To:" further down is
 * part of the message). `body` is the text with those header lines removed.
 */
export function draftHeaders(text: string): { to: string[]; cc: string[]; body: string } {
  const lines = text.split(/\r?\n/);
  const to: string[] = [];
  const cc: string[] = [];
  const keep: string[] = [];
  let scanned = 0;
  let inHeader = true;
  for (const line of lines) {
    if (inHeader && scanned < 6) {
      const m = /^\s*(to|cc)\s*:\s*(.+?)\s*$/i.exec(line);
      if (m) {
        (m[1].toLowerCase() === "to" ? to : cc).push(...splitAddresses(m[2]));
        scanned += 1;
        continue;
      }
      if (line.trim()) {
        scanned += 1;
        inHeader = false;
      }
    }
    keep.push(line);
  }
  return { to, cc, body: keep.join("\n").replace(/^\s*\n/, "") };
}
