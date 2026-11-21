/**
 * Your other computers (v1.497.0, vision plan leap 7, Phase 1): pair two PCs
 * running Iron Jarvis with a six-digit code. The words and the shapes, in one
 * place; the card is components/settings/OtherComputers.tsx.
 *
 * The daemon side is daemon/routes/peers.py:
 *   GET    /peers                 the list, this computer's name, the code
 *                                 being shown, whether others can reach it
 *   POST   /peers/code            show a pairing code
 *   POST   /peers/code/cancel
 *   POST   /peers/join            {address, code, name, this_name}
 *   POST   /peers/check           ask every paired computer
 *   POST   /peers/{name}/check
 *   PATCH  /peers/{name}/allows   next leaps 41-a: one switch (or a folder
 *                                 list, or seen_note) for what it may do here
 *   DELETE /peers/{name}
 * (/peers/pair and /peers/unpair are the doors the OTHER computer calls.)
 *
 * Kept OUT of lib/api.ts on purpose (many test files mock that module
 * wholesale): pure helpers only, no fetches here.
 */

import { LAN_SETTING_KEY } from "./anotherJarvis";

export interface Peer {
  /** What this computer calls the other one (also its "@name" in chat). */
  name: string;
  /** What the other computer calls itself. */
  theirName: string;
  address: string;
  pairedAt: string | null;
  lastOkAt: string | null;
  lastHeardAt: string | null;
  lastError: string;
  lastErrorAt: string | null;
  /** v1.509.0: the address it last reached this computer from, when that is
   *  not the one it was paired at ("" otherwise). */
  heardFrom: string;
  /** Next leaps 41-a: what it may do HERE, switch by switch (the keys of
   *  `PeersView.switches`); every one off for a new pairing. */
  allows: Record<string, boolean>;
  /** The one line under it ("desk may: send tasks."), the daemon's words. */
  mayLine: string;
  /** What IT last said it lets THIS computer do (only shown). */
  theyAllow: { known: boolean; allows: string[]; older: boolean };
  theyLine: string;
  /** D-41.1: said once for a pairing from before the switches ("" = none). */
  keptNote: string;
}

/** One switch, as the daemon's one table says it (peers/allows.SWITCHES). */
export interface PeerSwitch {
  key: string;
  label: string;
  /** Can this version turn it on? A switch whose thing comes later is drawn
   *  in one quiet line, never as a switch. */
  ready: boolean;
}

export interface PeersView {
  thisName: string;
  peers: Peer[];
  /** The code this computer is showing, or null. */
  code: { code: string; shown: string; expiresAt: string } | null;
  network: {
    /** "Let other computers on my network reach this Iron Jarvis" is saved on. */
    saved: boolean;
    /** It is in effect: other computers can reach this one now. */
    applied: boolean;
    /** What another computer types to reach this one ("192.168.1.20:8787"). */
    addresses: string[];
  };
  /** Next leaps 41-a: the switches, in order (empty from an older Iron Jarvis). */
  switches: PeerSwitch[];
}

/** The setting the card turns on and off (settings/schema.py `lan_access`). */
export const NETWORK_SETTING_KEY = LAN_SETTING_KEY;
/** Settings opens this section for the key (SettingsHome CONTROLLED_IN_SECTION). */
export const OTHER_COMPUTERS_SECTION = "connections-computers";
/** A link from another page: Settings opens the section on mount. */
export const OTHER_COMPUTERS_HREF = `/settings?focus=${NETWORK_SETTING_KEY}`;

/** Events after which the list is read again. */
export const PEER_EVENT_TYPES = ["peer.paired", "peer.removed", "peer.code_ended", "peer.allows_changed"];

export const SECTION_LABEL = "Your other computers";
export const SECTION_BLURB =
  "Pair this computer with another one running Iron Jarvis, so each can hand the other tasks.";

export const TASKS_LINE =
  "Once two computers are paired, each can hand the other tasks: in chat, write “@” and the other " +
  "computer's name, then the task. Each computer decides what the other may do on it: press the line " +
  "under a paired computer and turn on “Send me tasks” to take its tasks. A task from the other " +
  "computer runs here on its own, at low trust: anything that would need your OK here is not done, " +
  "and the answer goes back to the chat it came from.";

/** Next leaps 41-a: the switches' own words (the daemon sends the labels;
 *  these are the card's). */
export const SWITCHES_HINT =
  "Every switch starts off. Turning “Send me tasks” off stops the tasks this computer is still doing for it.";
export const LATER_SWITCHES = "Coming in later versions:";
export const KEPT_NOTE_OK = "OK";
/** The switch's accessible name: "Send me tasks, for desk". */
export function switchLabel(name: string, label: string): string {
  return `${label}, for ${name}`;
}
/** Said while the line is folded, so a screen reader knows it opens. */
export function opensSwitches(name: string): string {
  return `Show what ${name} may do on this computer`;
}

export const NETWORK_OFF =
  "Other computers cannot reach this one yet. To pair, turn this on here and on the other computer. " +
  "Iron Jarvis restarts to apply it, and another computer can ask this one for nothing until you pair it " +
  "or connect it yourself. The link between the two computers is not encrypted, so pair only over a " +
  "network you trust (your own, or Tailscale).";
export const TURN_ON = "Turn on and restart";
export const NETWORK_SAVED = "Turned on. It takes effect when Iron Jarvis restarts.";
export const RESTART_NOW = "Restart now";
export const NETWORK_ON = "Other computers on your network can reach this one at:";
export const TURN_OFF = "Turn off";
export const TURN_OFF_TITLE =
  "Stop other computers reaching this one. Iron Jarvis restarts. Paired computers stay on the list, but cannot reach it until you turn this on again";

/** Between offices (the owner's decision 2: Tailscale, no cloud relay). */
export const TAILSCALE_LINE =
  "In another office, put both computers on Tailscale: its address (it starts with 100.) works here " +
  "like any other.";

/** Windows asks once; a dismissed question blocks other computers silently. */
export const FIREWALL_LINE =
  "If the other computer still cannot reach this one, Windows Firewall is blocking Iron Jarvis: allow it " +
  "in Windows Security › Firewall & network protection › Allow an app through firewall.";

export const RESTART_NO_BRIDGE = "Saved. Quit Iron Jarvis and open it again to apply it.";
export const RESTART_REFUSED =
  "Saved, but Iron Jarvis could not restart itself. Press Restart Iron Jarvis in Settings › System › Maintenance.";
export const RESTARTING = "Restarting Iron Jarvis…";

export const NO_PEERS = "No other computer is paired yet.";

export const SHOW_CODE = "Show a pairing code";
export const USE_CODE = "Use a pairing code";
export const CANCEL_CODE = "Cancel";
/** Said when the code this computer showed was typed wrong too often. */
export const CODE_TRIED_OUT =
  "The code was typed wrong too many times, so it stopped working. If that was not you on the other " +
  "computer, someone else on your network tried it. Show a new code to try again.";
export const NEEDS_NETWORK =
  "Turn on the setting above first, so the other computer can reach this one.";
/** The same hint while the setting is saved on and waits for the restart. */
export const NEEDS_RESTART =
  "Restart Iron Jarvis first (above), so the other computer can reach this one.";

export function codeSteps(addresses: string[]): string {
  const where = addresses.length
    ? addresses.length === 1
      ? `this computer's address, ${addresses[0]}`
      : `one of this computer's addresses: ${addresses.join(" or ")}`
    : "this computer's address";
  return (
    `On the other computer, open Settings › Connections › ${SECTION_LABEL}, press ${USE_CODE}, ` +
    `and type ${where} and the code. The code works for 10 minutes, and only once.`
  );
}

export const ADDRESS_LABEL = "The other computer's address";
export const ADDRESS_PLACEHOLDER = "192.168.1.20";
export const CODE_LABEL = "The code it shows";
export const CODE_PLACEHOLDER = "482 917";
/** Pair's reason while the code box holds something that is not a code. */
export const CODE_NOT_SIX_DIGITS = "The code is the six digits the other computer shows.";
export const CALL_IT_LABEL = "What to call it here (optional)";
export const CALL_IT_PLACEHOLDER = "desk";
export const THIS_NAME_LABEL = "This computer's name";
export const PAIR = "Pair";
export const PAIRING = "Pairing…";

/** Said beside the join form when THIS computer cannot be reached: the
 *  pairing still works, but the other one's answers cannot come back. */
export const ANSWERS_CANNOT_COME_BACK =
  "This computer cannot be reached yet, so the other computer's answers cannot come back here. " +
  "Turn on the setting above too.";
/** The same note while the setting is saved on and waits for the restart. */
export const ANSWERS_AFTER_RESTART =
  "This computer cannot be reached until Iron Jarvis restarts, so the other computer's answers " +
  "cannot come back here yet. Restart it above.";

export const CHECK = "Check";
export const REMOVE = "Remove";
export function removeTitle(name: string): string {
  return `End the pairing with ${name} on both computers. From that moment it cannot reach this one`;
}

/** The remote-agent row (Agents › Your team) for a paired computer. */
export const PAIRED_REMOTE_NOTE = "Paired. Change or remove it in Settings › Connections › Your other computers.";

/** The pointer at the top of "Another computer running Jarvis". */
export const PAIR_INSTEAD =
  "The easy way: pair the two computers in Settings › Connections › Your other computers. One shows a " +
  "code, the other types it. The steps below set it up by hand instead.";
export const PAIR_INSTEAD_LINK = "Open Your other computers";

function text(v: unknown, max = 600): string {
  return typeof v === "string" ? v.slice(0, max) : "";
}

function time(v: unknown): string | null {
  return typeof v === "string" && v ? v.slice(0, 40) : null;
}

function record(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function decodeAllows(raw: unknown): Record<string, boolean> {
  const r = record(raw) ?? {};
  const out: Record<string, boolean> = {};
  for (const [k, v] of Object.entries(r)) {
    if (k.startsWith("may_") && typeof v === "boolean") out[k] = v;
  }
  return out;
}

function decodeTheyAllow(raw: unknown): Peer["theyAllow"] {
  const r = record(raw) ?? {};
  return {
    known: r.known === true,
    allows: (Array.isArray(r.allows) ? r.allows : [])
      .filter((a): a is string => typeof a === "string" && a.startsWith("may_"))
      .slice(0, 20),
    older: r.older === true,
  };
}

function decodeSwitch(raw: unknown): PeerSwitch | null {
  const r = record(raw);
  const key = r ? text(r.key, 40) : "";
  const label = r ? text(r.label, 80) : "";
  if (!r || !key.startsWith("may_") || !label) return null;
  return { key, label, ready: r.ready === true };
}

function decodePeer(raw: unknown): Peer | null {
  const r = record(raw);
  const name = r ? text(r.name, 80).trim() : "";
  if (!r || !name) return null;
  return {
    name,
    theirName: text(r.their_name, 80),
    address: text(r.address, 200),
    pairedAt: time(r.paired_at),
    lastOkAt: time(r.last_ok_at),
    lastHeardAt: time(r.last_heard_at),
    lastError: text(r.last_error, 400),
    lastErrorAt: time(r.last_error_at),
    heardFrom: text(r.heard_from, 80),
    allows: decodeAllows(r.allows),
    mayLine: text(r.may_line, 300),
    theyAllow: decodeTheyAllow(r.they_allow),
    theyLine: text(r.they_line, 300),
    keptNote: text(r.kept_note, 300),
  };
}

/** v1.509.0 (second review F6): said under a pairing whose computer now
 *  reaches this one from another address. Its answers are not sent to the
 *  old address; pairing again is the way to follow it. "" when it has not
 *  moved. */
export function movedWords(peer: Peer): string {
  if (!peer.heardFrom) return "";
  return `${peer.name} now reaches this computer from ${peer.heardFrom}, not the address it was paired at. Pair again if it moved.`;
}

/** A defensive read of `GET /peers` (and the answers that return the same view). */
export function decodePeers(raw: unknown): PeersView {
  const r = record(raw) ?? {};
  const code = record(r.code);
  const net = record(r.network) ?? {};
  const codeText = code ? text(code.code, 12) : "";
  return {
    thisName: text(r.this_name, 80),
    peers: (Array.isArray(r.peers) ? r.peers : [])
      .map(decodePeer)
      .filter((p): p is Peer => p !== null),
    code:
      code && /^\d{6}$/.test(codeText)
        ? {
            code: codeText,
            shown: text(code.shown, 12) || `${codeText.slice(0, 3)} ${codeText.slice(3)}`,
            expiresAt: text(code.expires_at, 40),
          }
        : null,
    network: {
      saved: net.saved === true,
      applied: net.applied === true,
      addresses: (Array.isArray(net.addresses) ? net.addresses : [])
        .filter((a): a is string => typeof a === "string" && !!a)
        .slice(0, 8)
        .map((a) => a.slice(0, 80)),
    },
    switches: (Array.isArray(r.switches) ? r.switches : [])
      .map(decodeSwitch)
      .filter((s): s is PeerSwitch => s !== null)
      .slice(0, 20),
  };
}

function newer(a: string | null, b: string | null): string | null {
  if (!a) return b;
  if (!b) return a;
  return Date.parse(a) >= Date.parse(b) ? a : b;
}

/** One line about how the other computer has been answering: the last time
 *  it was heard from, or — when the newest thing is a failed check — why it
 *  did not answer. */
export function peerStatusLine(peer: Peer, ago: (iso: string) => string): { words: string; warn: boolean } {
  const heard = newer(peer.lastOkAt, peer.lastHeardAt);
  const failedLast =
    !!peer.lastError && !!peer.lastErrorAt && (!heard || Date.parse(peer.lastErrorAt) > Date.parse(heard));
  if (failedLast) {
    return { words: `Did not answer ${ago(peer.lastErrorAt as string)}: ${peer.lastError}`, warn: true };
  }
  if (heard) return { words: `Last heard from ${ago(heard)}.`, warn: false };
  return { words: "Not heard from yet.", warn: false };
}

/** "Works until 6:12 PM" for a code. */
export function codeEndsWords(expiresAt: string): string {
  const ms = Date.parse(expiresAt);
  if (!Number.isFinite(ms)) return "";
  const at = new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return `Works until ${at}.`;
}

/** The digits a person typed (spaces and dashes dropped), or "" when it is
 *  not six digits. The daemon applies the same rule (peers/codes.normalize). */
export function typedCode(raw: string): string {
  const digits = raw.replace(/[\s.-]/g, "");
  return /^\d{6}$/.test(digits) ? digits : "";
}
