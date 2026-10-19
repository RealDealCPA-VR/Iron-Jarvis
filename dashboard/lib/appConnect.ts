/**
 * v1.351.0 (G15): "connect my GitHub" WORKS IN THE CHAT THAT ASKED, and the
 * task carries on.
 *
 * Before: the secure card said "<label> saved securely" and stopped. Nothing
 * said whether the app started, the next message still had no GitHub tools
 * (the chat's "Tools › Connections" switch for it was off), and the question the
 * user had asked was left behind.
 *
 * Now the daemon's answer to the card's save (POST /config/secret) carries,
 * for an `app.*` credential, `{app, app_name, tools_loaded[, load_reason]
 * [, waiting_for]}`. This module turns that into the card's words
 * ("GitHub connected · 26 tools"), keeps it on the saved card (`connected` —
 * names, a count and plain words, never a value), finds the question to
 * carry on with, and reads which apps a turn's `app_connect` calls named so
 * the page turns them on for the chat.
 *
 * Kept outside lib/api.ts (mocked wholesale by ~70 test files) and outside
 * lib/configCards.ts, whose whitelist decoder does not know `connected`:
 * `withAppConnected` puts it back after that decoder ran.
 */

import type { ConfigCard, SecretRequestCard } from "./configCards";

/** What a saved app credential did, as the card shows it. */
export interface AppConnected {
  /** The connector id — the chat's connector toggle ("github"). */
  app: string;
  /** Its name ("GitHub"). */
  name: string;
  /** Tools the app loaded right after the save (0 = it did not load). */
  tools: number;
  /** Why it did not load, in the daemon's plain words (tools 0 only). */
  reason?: string;
  /** Labels of the other keys the app still needs (it was not started). */
  waiting?: string[];
  /** Continue was pressed: the task was sent on. */
  continued?: boolean;
}

/** A credential card that may carry what its save did. */
export type AppSecretCard = SecretRequestCard & { connected?: AppConnected };

const str = (v: unknown): string => (typeof v === "string" ? v : "");

/** `app.github` / `app.box__BOX_CLIENT_ID` → "github" / "box"; "" otherwise. */
export function appIdOf(cardName: string): string {
  const name = str(cardName);
  if (!name.startsWith("app.")) return "";
  const id = name.slice(4).split("__", 1)[0] ?? "";
  return /^[A-Za-z0-9_-]{1,64}$/.test(id) ? id : "";
}

/** Whitelist an `AppConnected` (a saved thread hands back whatever the disk
 *  held). `undefined` for anything that is not one. */
export function decodeAppConnected(raw: unknown): AppConnected | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const r = raw as Record<string, unknown>;
  const app = str(r.app);
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(app)) return undefined;
  const tools = typeof r.tools === "number" && Number.isFinite(r.tools) ? Math.max(0, Math.floor(r.tools)) : 0;
  const waiting = Array.isArray(r.waiting)
    ? r.waiting.filter((w): w is string => typeof w === "string" && w.trim() !== "").map((w) => w.slice(0, 80)).slice(0, 4)
    : [];
  const reason = str(r.reason).slice(0, 300);
  return {
    app,
    name: (str(r.name) || app).slice(0, 60),
    tools,
    ...(reason ? { reason } : {}),
    ...(waiting.length ? { waiting } : {}),
    ...(r.continued === true ? { continued: true } : {}),
  };
}

/** The `connected` record a card's save answered, or `undefined` when the
 *  card is not an app's or the daemon said nothing about the load (an older
 *  daemon: the card keeps its old words). */
export function connectedFromSave(cardName: string, res: unknown): AppConnected | undefined {
  const app = appIdOf(cardName);
  if (!app || !res || typeof res !== "object") return undefined;
  const r = res as Record<string, unknown>;
  if (typeof r.tools_loaded !== "number" && !Array.isArray(r.waiting_for)) return undefined;
  return decodeAppConnected({
    app: str(r.app) || app,
    name: str(r.app_name) || app,
    tools: typeof r.tools_loaded === "number" ? r.tools_loaded : 0,
    reason: str(r.load_reason),
    waiting: r.waiting_for,
  });
}

/** Put each card's `connected` back after `decodeConfigCards` (which keeps
 *  only the fields it knows) — matched by the card's request id. */
export function withAppConnected(decoded: ConfigCard[], raw: unknown): ConfigCard[] {
  if (!Array.isArray(raw) || !decoded.some((c) => c.kind === "secret")) return decoded;
  const byId = new Map<string, AppConnected>();
  for (const c of raw) {
    if (!c || typeof c !== "object") continue;
    const r = c as Record<string, unknown>;
    if (r.kind !== "secret" || !str(r.request_id)) continue;
    const conn = decodeAppConnected(r.connected);
    if (conn) byId.set(str(r.request_id), conn);
  }
  if (byId.size === 0) return decoded;
  return decoded.map((c) =>
    c.kind === "secret" && byId.has(c.request_id) ? ({ ...c, connected: byId.get(c.request_id) } as ConfigCard) : c,
  );
}

/** The card's `connected`, read safely. */
export function connectedOf(card: ConfigCard | undefined): AppConnected | undefined {
  if (!card || card.kind !== "secret") return undefined;
  return decodeAppConnected((card as AppSecretCard).connected);
}

/** "GitHub connected · 26 tools" (only when tools loaded). */
export function connectedWords(c: AppConnected): string {
  return `${c.name} connected · ${c.tools} ${c.tools === 1 ? "tool" : "tools"}`;
}

/** "Box still needs its Client secret" — an app waiting for another key. */
export function waitingWords(c: AppConnected): string {
  // "Box client secret" under "Box still needs its …" reads "client secret".
  const own = `${c.name.toLowerCase()} `;
  const list = (c.waiting ?? []).map((w) => (w.toLowerCase().startsWith(own) ? w.slice(own.length) : w));
  const named = list.length <= 1 ? list.join("") : `${list.slice(0, -1).join(", ")} and ${list.at(-1)}`;
  return `${c.name} still needs its ${named}`;
}

/** The words a Continue press sends: the app is connected, carry on with the
 *  task the user asked for (or just carry on, when there was none). */
export function continueText(appName: string, task: string): string {
  const t = task.trim();
  return t ? `${appName} is connected. Please carry on with: ${t}` : `${appName} is connected. Please carry on.`;
}

const CONTINUE_RE = /^.{1,80} is connected\. Please carry on with: ([\s\S]+)$/;

const CONNECT_RE =
  /^(?:(?:ok(?:ay)?|now|then|so|and|yes|sure|great)[\s,.!]+)*(?:(?:please|pls|can you|could you|would you|will you|go ahead and|i(?:'d| would) like (?:you )?to|i want (?:you )?to|let'?s|let me|help me)\s+)*(?:re)?(connect|link|hook up|set up|setup|add|sign in to|log ?in to|authori[sz]e|(?:set|hook) (?:it|that|this|them) up)\b/i;

/** Verbs that also start ordinary work ("add a task in Todoist", "set up a
 *  meeting with Bob"): with these, a message is a bare connect request only
 *  when what follows names the app itself or its key. */
const WORK_VERB_RE = /^(?:add|set ?up)$/i;

/** Words that say nothing about WHAT is connected ("my", "account", "it"). */
const CONNECT_FILLER = new Set([
  "my", "the", "a", "an", "our", "your", "me", "us", "it", "its", "this", "them",
  "to", "with", "up", "in", "into", "on", "again", "here", "now", "please", "jarvis", "chat",
]);

/** Words that say the request is about a credential ("my Notion token"). */
const CREDENTIAL_WORDS = new Set([
  "account", "accounts", "acct", "token", "tokens", "key", "keys", "api", "access", "app",
  "integration", "integrations", "connection", "connector", "credential", "credentials", "login",
]);

/** The app a connect card is for, as `taskBefore` is told it. */
export interface ConnectApp {
  /** The connector id ("github", "google-drive"). */
  id?: string;
  /** Its name ("GitHub", "Google Drive"). */
  name?: string;
}

function words(text: string): string[] {
  return text
    .toLowerCase()
    .split(/[\s/]+/)
    .map((w) => w.replace(/^[^a-z0-9]+|[^a-z0-9]+$/g, "").replace(/'s$/, ""))
    .filter(Boolean);
}

function appWords(app: ConnectApp): Set<string> {
  const out = new Set<string>();
  for (const part of [str(app.id), str(app.name)]) {
    const ws = words(part.replace(/[-_.]+/g, " "));
    for (const w of ws) out.add(w);
    if (ws.length > 1) out.add(ws.join(""));
  }
  return out;
}

/** A reply that only says go ahead ("yes", "sure, go ahead") — it answers
 *  Jarvis's offer to connect; it is not the task. */
const GO_AHEAD_RE =
  /^(?:(?:ok(?:ay)?|yes|yeah|yep|yup|sure|alright|all right|fine|please|go ahead|do it|please do|sounds good|of course|absolutely|thanks|thank you)[\s,.!]*)+$/i;

/** What may follow the app's name in a bare request ("… please", "… now"). */
const POLITE_TAIL_RE = /[\s,]*(?:please|pls|thanks|thank you|now|for me)?[\s.!?]*$/i;

/** A word or mark that starts a SECOND clause — the task itself rides the
 *  same message ("connect my GitHub and list my open PRs", "… so you can
 *  …", "… to see my PRs"; a leading "to" is just "connect to GitHub"). */
const MORE_TASK_RE = /\b(?:and|then|so|but|also|plus|after|before|because|while|which|that|once)\b|\S\s+to\b|[,;:]/i;

/** A message that only asks to connect something ("connect my GitHub",
 *  "please link Notion") — not the task to carry on with. A request that
 *  also carries the task ("connect my GitHub and list my open PRs") is NOT
 *  one: it is the task (v1.351.0 review — skipping it carried an OLDER,
 *  unrelated question on instead).
 *
 *  Given the `app` the card is for, "add" and "set up" count only when what
 *  follows names that app or a key ("set up GitHub", "add my Notion
 *  token"): "add a task in Todoist" and "set up a meeting with Bob" are the
 *  work itself (v1.351.0 fix round 2 — they read as bare requests, so
 *  Continue carried an older question on instead). */
export function isConnectRequest(text: string, app?: ConnectApp): boolean {
  const t = str(text).trim();
  if (!t || t.length > 120) return false;
  const m = CONNECT_RE.exec(t);
  if (!m) return false;
  const rest = t.slice(m[0].length).replace(POLITE_TAIL_RE, "").trim();
  // "connect it", "set that up": only a pointer back at Jarvis's offer.
  if (/^(?:it|that|this|them|that one|this one)(?:\s+up)?$/i.test(rest)) return true;
  if (MORE_TASK_RE.test(rest)) return false;
  if (rest.split(/\s+/).filter(Boolean).length > 6) return false;
  if (!app || !WORK_VERB_RE.test(m[1].replace(/\s+/g, " "))) return true;
  const said = words(rest);
  if (said.some((w) => CREDENTIAL_WORDS.has(w))) return true;
  const own = appWords(app);
  return said.every((w) => CONNECT_FILLER.has(w) || own.has(w));
}

/** "yes", "sure, go ahead": a go-ahead, never the task. */
export function isGoAhead(text: string): boolean {
  const t = str(text).trim();
  return t.length > 0 && t.length <= 40 && GO_AHEAD_RE.test(t);
}

interface MessageLike {
  role?: string;
  content?: string;
  steer?: boolean;
  continuation?: boolean;
}

/** The question to carry on with: the newest user message before `index`
 *  that is not a mid-turn note, a hidden Continue turn, a go-ahead ("yes")
 *  or a bare "connect my X" (judged for `app`, the card's app, when given) —
 *  and an earlier Continue's words are unwrapped to the task they carried.
 *  "" when there is none. */
export function taskBefore(messages: readonly MessageLike[], index: number, app?: ConnectApp): string {
  for (let j = Math.min(index, messages.length) - 1; j >= 0; j--) {
    const m = messages[j];
    if (!m || m.role !== "user" || m.steer || m.continuation) continue;
    const text = str(m.content).trim();
    if (!text) continue;
    const carried = CONTINUE_RE.exec(text);
    if (carried) return carried[1].trim();
    if (isGoAhead(text) || isConnectRequest(text, app)) continue;
    return text;
  }
  return "";
}

interface ToolLike {
  name?: string;
  ok?: boolean;
  status?: string;
  args?: Record<string, unknown>;
}

/** The apps a turn's FINISHED, successful `app_connect` calls named — turned
 *  on for the chat so the next message has their tools ("It is on for this
 *  chat now" is the tool's own promise for an app that was already
 *  connected). */
export function appConnectIds(tools: readonly ToolLike[] | null | undefined): string[] {
  const out: string[] = [];
  for (const t of tools ?? []) {
    if (!t || t.name !== "app_connect" || t.ok !== true || (t.status && t.status !== "done")) continue;
    const id = str(t.args?.app).trim().toLowerCase();
    if (/^[a-z0-9_-]{1,64}$/.test(id) && !out.includes(id)) out.push(id);
  }
  return out;
}

/** `prev` with every id in `ids` turned on, up to `max` (the cap the "+"
 *  menu enforces). The SAME array back when nothing changed. */
export function withConnectors(prev: readonly string[], ids: readonly string[], max: number): string[] {
  let next: string[] | null = null;
  for (const id of ids) {
    const list: readonly string[] = next ?? prev;
    if (!id || list.includes(id) || list.length >= max) continue;
    next = [...list, id];
  }
  return next ?? (prev as string[]);
}
