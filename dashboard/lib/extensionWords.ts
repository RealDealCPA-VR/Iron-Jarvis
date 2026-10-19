/**
 * v1.356.0 (product-readiness wave 6, lane 6A): the words of the Tools page,
 * the Directory cards and the Enable dialog, kept pure so the pages and their
 * tests read the same sentences without a DOM.
 *
 *  - G78 "turn a pack off without losing it": when a pack row is OFF (the
 *    daemon's `enabled: false` / `state: "off"`), the one line an off row
 *    says, and the note after a switch press;
 *  - G77: the failing row's consequence, "Chats and agents can't use its
 *    tools until it works.";
 *  - G74 "no protocol words": a field the Enable dialog asks for is labelled
 *    in words ("Folder", "GitHub personal access token") with the raw key in
 *    a title, and the one sentence beside every box that takes a key or a
 *    token.
 *
 * Dependency-free on purpose (lib/api.ts is mocked wholesale by ~71 test
 * files, so helpers live in their own file).
 */

/* ----------------------------------------------------------------- G78 */

/** The facts a row carries about its switch (GET /mcp/servers rows and
 *  GET /connectors rows). An older daemon sends neither: a row is ON. */
export interface SwitchFacts {
  enabled?: boolean | null;
  state?: string | null;
}

/** True only when the daemon said the pack is switched off. Absent (an
 *  older daemon, a row saved before v1.356.0) is ON — never a claim. */
export function isPackOff(row: SwitchFacts | null | undefined): boolean {
  if (!row) return false;
  return row.enabled === false || row.state === "off";
}

/** The state word an off row wears. */
export const PACK_OFF_WORD = "Off";

/** The one line an off row says: what off means and that nothing is lost. */
export const PACK_OFF_LINE =
  "Switched off. Chats and agents can't use its tools until you turn it on; its settings and keys are kept.";

/** v1.357.0 integrate (wave-6 follow-up): a chat whose saved connections
 *  hold an app the user has since switched off keeps it in + › Connections
 *  as an Off row (never silently dropped), and this is what that row says:
 *  the one off line, then where to turn it on and that this chat picks it up
 *  again then. The daemon arms nothing for it meanwhile
 *  (`chat_turn._resolve_connectors` skips an id with no loaded tools). */
export const PACK_OFF_IN_CHAT_LINE =
  `${PACK_OFF_LINE} Turn it on in Settings › Tools or on its card in the Directory, ` +
  "and this chat uses it again.";

/** The switch's accessible name: the same words whichever way it points. */
export function packSwitchLabel(name: string): string {
  return `Use ${name}`;
}

/** The switch's tooltip, by its position. */
export function packSwitchTitle(on: boolean): string {
  return on
    ? "On: chats and agents can use its tools. Switch off to pause it without losing its settings or keys."
    : "Off: its tools are paused. Switch on to use it again; nothing needs typing again.";
}

/** What the daemon answers a switch (PATCH /mcp/servers/{name} {enabled}):
 *  the row plus `reloaded` / `note` / `changed`. */
export interface SwitchAnswer {
  enabled?: boolean | null;
  reloaded?: boolean | null;
  tools_loaded?: number | null;
  note?: string | null;
}

/** The note after a switch press: off = paused, nothing lost; on = how many
 *  tools came back, or the daemon's own reason it did not start. `warn` is
 *  true only when it was switched on and did not start. */
export function switchedNote(name: string, on: boolean, res: SwitchAnswer | null | undefined): {
  text: string;
  warn: boolean;
} {
  if (!on) {
    return {
      text: `"${name}" is off. Its tools are paused for chats and agents; its settings and keys are kept.`,
      warn: false,
    };
  }
  const n = typeof res?.tools_loaded === "number" ? res.tools_loaded : 0;
  if (res?.reloaded === false || n === 0) {
    const note = typeof res?.note === "string" ? res.note.trim() : "";
    const reason = note
      .replace(/^switched on,?\s*but\s+it\s+did\s*n[o']t\s+start\s*:\s*/i, "")
      .replace(/\s*[—–;,-]\s*see\s+its\s+row(\s+for\s+the\s+fix)?\s*\.?\s*$/i, "")
      .replace(/[\s.]+$/, "")
      .trim();
    return {
      text: `"${name}" is on, but it didn't start${reason ? `: ${reason}` : ""}. Open its row to retry.`,
      warn: true,
    };
  }
  return { text: `"${name}" is on. ${n} tool${n === 1 ? "" : "s"} ready to use.`, warn: false };
}

/** An edit saved on a pack that is switched off (the daemon does not load
 *  it behind the switch). */
export function savedWhileOff(name: string): string {
  return `"${name}" saved. It's off, so it starts when you turn it on.`;
}

/* ----------------------------------------------------------------- G77 */

/** A pack that did not start, or whose calls fail: what that costs. */
export const PACK_FAILING_CONSEQUENCE = "Chats and agents can't use its tools until it works.";

/* ----------------------------------------------------------------- G74 */

/** Said beside every box that takes a key or a token. */
export const KEYS_KEPT_HINT = "Keys and tokens are kept encrypted on this PC.";

/** How an extension reached by a web address connects, in the words the
 *  editor's "How it connects" choice uses (EditPack, the add-by-address
 *  door). An older server (the "sse" transport) says so; anything else is a
 *  web address. Never the protocol name: that rides `transportTitle`. */
export function webAddressWords(transport: string | null | undefined): string {
  return transport === "sse" ? "A web address (older servers)" : "A web address";
}

/** The protocol name a server's own docs use, for a title (tooltip) only. */
export function transportTitle(transport: string | null | undefined): string {
  return transport === "sse" ? "SSE" : "Streamable HTTP";
}

/** What a Tools row's opened "Show address" fold reads: the address, and
 *  "(older servers)" after it for an older server; the protocol name rides
 *  the title (v1.356.0 fix round: the fold read "SSE https://…"). */
export function addressLine(
  transport: string | null | undefined,
  url: string | null | undefined,
): { text: string; title: string } {
  const address = (url ?? "").trim();
  return {
    text: transport === "sse" ? `${address} (older servers)` : address,
    title: `${transportTitle(transport)} ${address}`.trim(),
  };
}

/** Words a raw key is written with that read better another way. */
const WORD_CASE: Readonly<Record<string, string>> = {
  api: "API",
  id: "ID",
  url: "URL",
  uri: "URI",
  ssh: "SSH",
  oauth: "OAuth",
  github: "GitHub",
  gitlab: "GitLab",
  openai: "OpenAI",
  aws: "AWS",
  sql: "SQL",
  dsn: "DSN",
  pat: "token",
  repo: "repository",
  dir: "folder",
};

/**
 * A field label in words, from the raw key a pack's config names:
 *
 *   "<folder-path>"                 -> "Folder"
 *   "<repo-path>"                   -> "Repository folder"
 *   "GITHUB_PERSONAL_ACCESS_TOKEN"  -> "GitHub personal access token"
 *   "BOX_CLIENT_ID"                 -> "Box client ID"
 *
 * The raw key stays in the field's title (the docs name it that way). A key
 * that yields no words is returned as it came.
 */
export function humanFieldLabel(raw: string): string {
  const text = String(raw ?? "").trim();
  const isPlaceholder = /^<.+>$/.test(text);
  const bare = isPlaceholder ? text.slice(1, -1) : text;
  let words = bare
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean)
    .map((w) => w.toLowerCase());
  if (words.length === 0) return text;
  // A "<…-path>" placeholder is a folder: say the folder, not the path.
  if (isPlaceholder && words.length > 1 && (words[words.length - 1] === "path" || words[words.length - 1] === "dir")) {
    words = words.slice(0, -1);
    if (!words.includes("folder") && !words.includes("dir")) words.push("folder");
  } else if (isPlaceholder && words.length === 1 && (words[0] === "path" || words[0] === "dir")) {
    words = ["folder"];
  }
  const out = words
    .map((w) => WORD_CASE[w] ?? w)
    .filter((w, i, all) => !(w === "folder" && all.indexOf("folder") !== i));
  // "Folder folder" never; "<folder-path>" is just "Folder".
  const joined = out.join(" ");
  return joined.charAt(0).toUpperCase() + joined.slice(1);
}
