// The side panel's whole behaviour: ask the service worker what is true, render it,
// and drive a small state machine off the frames the daemon sends back.
//
// WHAT THIS FILE IS NOT. It is not a second Jarvis. There is no model here, no agent
// loop, no policy, no tool roster and no approval logic — an approval card is
// RENDERED here and ANSWERED in the daemon, over the same registry every other Jarvis
// surface uses. Everything below either paints a frame that arrived or posts an
// action the user pressed. If a change to this file needs a decision to be made in
// the browser, the change belongs in the daemon instead.
//
// The panel holds no socket, for the reason the popup held none: there is exactly one
// live connection per browser (D08), and a socket opened here would replace the
// worker's, take the session, and die the moment Chrome closed the panel — leaving
// the daemon's card "connected" to a window that no longer exists. So every fact
// comes from the worker and every press is a `chrome.runtime.sendMessage`.
//
// The status readout below is the popup's, moved rather than rewritten (D32). The
// popup is retired because Chrome ignores `openPanelOnActionClick` while
// `action.default_popup` is set, and its two findings came with it: the one button's
// word is derived from `toggleAction` so it can never do the opposite of what it
// says, and an unreported access mode is named as UNKNOWN rather than dressed as a
// value.

import {
  PANEL_ACTION_APPROVE,
  PANEL_ACTION_CLOSE,
  PANEL_ACTION_DENY,
  PANEL_ACTION_OPEN,
  PANEL_ACTION_SEND,
  PANEL_ACTION_STEER,
  PANEL_ACTION_STOP,
  PANEL_EVENT_APPROVAL,
  PANEL_EVENT_DELTA,
  PANEL_EVENT_DONE,
  PANEL_EVENT_ERROR,
  PANEL_EVENT_STATE,
  PANEL_EVENT_STEERED,
  PANEL_EVENT_TOOL,
} from "../protocol";
import { TOGGLE_LABELS, toggleAction, type BridgeStatus } from "../bridge/socket";

interface Rendered {
  tone: "connected" | "waiting" | "off";
  word: string;
  note: string;
  toggle: string;
}

const ACCESS_WORDS: Record<string, string> = {
  off: "Off",
  read_only: "Read only",
  interactive: "Interactive",
};

/** The words for a state with no `access` reported yet. Absence, named as absence. */
export const ACCESS_UNKNOWN = "Unknown — set in Jarvis";

/**
 * State -> the words the user reads. One row per state, so none can go unwritten.
 *
 * The toggle label is NOT written per branch. It is derived from `toggleAction`, the
 * same function the worker uses to decide what a press does, so a word here can
 * never describe an effect the worker will not produce.
 */
export function describe(status: BridgeStatus): Rendered {
  const toggle = TOGGLE_LABELS[toggleAction(status.state)];
  switch (status.state) {
    case "connected":
      return status.hostPermission
        ? { tone: "connected", word: "Connected", note: "", toggle }
        : {
            tone: "waiting",
            word: "Connected — no site access",
            note: "Open Jarvis and press Grant site access to let it see your open tabs.",
            toggle,
          };
    case "pairing":
      return {
        tone: "waiting",
        word: "Waiting for Iron Jarvis",
        note: "Open Jarvis to connect your browser.",
        toggle,
      };
    case "replaced":
      return {
        tone: "waiting",
        word: "Another browser is connected",
        note: status.lastError || "Iron Jarvis is working with a different browser right now.",
        toggle,
      };
    case "suspended":
      return {
        tone: "off",
        word: "Disconnected",
        note: "This browser will stay disconnected until you press Connect.",
        toggle,
      };
    case "offline":
    default:
      return {
        tone: "off",
        word: "Not connected",
        note: status.lastError || "Open Jarvis to connect your browser.",
        toggle,
      };
  }
}

/** The word for a reported access mode, or the mode itself if it is a newer one. */
export function accessWord(access: string): string {
  if (!access) {
    return ACCESS_UNKNOWN;
  }
  return ACCESS_WORDS[access] ?? access;
}

const el = {
  state: document.getElementById("state"),
  word: document.getElementById("state-word"),
  note: document.getElementById("note"),
  access: document.getElementById("access"),
  version: document.getElementById("version"),
  open: document.getElementById("open") as HTMLButtonElement | null,
  toggle: document.getElementById("toggle") as HTMLButtonElement | null,
  transcript: document.getElementById("transcript"),
  approval: document.getElementById("approval"),
  approvalText: document.getElementById("approval-text"),
  approve: document.getElementById("approve") as HTMLButtonElement | null,
  deny: document.getElementById("deny") as HTMLButtonElement | null,
  ask: document.getElementById("ask") as HTMLTextAreaElement | null,
  send: document.getElementById("send") as HTMLButtonElement | null,
  stop: document.getElementById("stop") as HTMLButtonElement | null,
  steer: document.getElementById("steer") as HTMLButtonElement | null,
};

/** The id of the ask the approval card is currently showing, or `""`. */
let pendingApprovalId = "";

/** The node holding the last streamed answer, so a `delta` appends to one turn. */
let streaming: HTMLElement | null = null;

/** Steer notes shown as PENDING, keyed by the id the panel minted for each. */
const pendingSteers = new Map<string, HTMLElement>();

/** Monotonic within one panel lifetime, which is all a steer note id has to be. */
let steerSeq = 0;

// --- painting ---------------------------------------------------------------

/**
 * Print the version of the add-on THIS PANEL IS RUNNING, once, at load.
 *
 * Chrome keeps serving the build it loaded until somebody presses Reload on
 * chrome://extensions, and an unpacked add-on's card there shows the version of
 * the LOADED copy — so after an Iron Jarvis update it still read 1.235.0 and a
 * user had no way to tell a stale add-on from a current one. The stale build is
 * the one that still declares `action.default_popup`, which means the toolbar
 * click opens the retired popup and this panel never runs at all; when it DOES
 * run, this line is the proof of which copy did.
 *
 * The comparison is not made here, and deliberately: `browser.ready` carries no
 * Iron Jarvis version, so the panel would have to guess at what "current" is.
 * The Browser page in Iron Jarvis knows both numbers and owns that sentence.
 */
function paintVersion(): void {
  if (!el.version) {
    return;
  }
  try {
    el.version.textContent = chrome.runtime.getManifest().version || "unknown";
  } catch {
    // No runtime (the panel opened outside an add-on context). An empty slot is
    // better than a number that is not this build's.
    el.version.textContent = "unknown";
  }
}

function turnState(): string {
  return document.body.dataset["turn"] ?? "idle";
}

function setTurn(running: boolean): void {
  // ONE attribute, read by the stylesheet, rather than five `hidden` writes that
  // can disagree: Stop and Steer exist exactly while a turn does, and a Stop button
  // visible over a finished turn is a button whose press does nothing.
  document.body.dataset["turn"] = running ? "running" : "idle";
}

function say(who: string, text: string): HTMLElement {
  const node = document.createElement("p");
  node.className = "turn";
  node.dataset["who"] = who;
  node.textContent = text;
  el.transcript?.appendChild(node);
  el.transcript?.scrollTo({ top: el.transcript.scrollHeight });
  return node;
}

function paintStatus(status: BridgeStatus): void {
  const view = describe(status);
  if (el.state) {
    el.state.dataset["tone"] = view.tone;
  }
  if (el.word) {
    el.word.textContent = view.word;
  }
  if (el.note) {
    el.note.textContent = view.note;
  }
  if (el.access) {
    // The real mode when the daemon reported one, and an explicit "Unknown"
    // otherwise. An older daemon sends `browser.ready` with no `access` at all, and
    // a bare placeholder sitting where a mode belongs reads AS the mode.
    el.access.textContent = accessWord(status.access);
  }
  // The empty state is driven by the SAME string the header prints from, so the
  // panel cannot offer a composer while its own header says access is off.
  document.body.dataset["access"] = status.access || "unknown";
  if (el.toggle) {
    // No label means no action, so the button is not drawn at all rather than
    // offering something that would do nothing. `hidden` and not display, so
    // re-showing it needs no style bookkeeping.
    el.toggle.textContent = view.toggle;
    el.toggle.hidden = view.toggle === "";
  }
}

function showApproval(id: string, text: string): void {
  pendingApprovalId = id;
  if (el.approvalText) {
    el.approvalText.textContent = text;
  }
  if (el.approval) {
    el.approval.hidden = false;
  }
}

function clearApproval(): void {
  pendingApprovalId = "";
  if (el.approval) {
    el.approval.hidden = true;
  }
}

// --- talking to the worker --------------------------------------------------

/**
 * Post one panel action and report whether it actually left the browser.
 *
 * The worker answers `{sent: false}` when the socket is down or unpaired, and the
 * caller SAYS SO. A panel that painted the user's question into the transcript and
 * then sent nothing is a panel that looks like it is thinking forever.
 */
async function post(action: string, params: Record<string, unknown> = {}): Promise<boolean> {
  try {
    const reply = (await chrome.runtime.sendMessage({
      kind: "panel_action",
      action,
      params,
    })) as { sent?: boolean } | null;
    return Boolean(reply?.sent);
  } catch {
    return false;
  }
}

async function refresh(): Promise<void> {
  let status: BridgeStatus;
  try {
    status = (await chrome.runtime.sendMessage({ kind: "status" })) as BridgeStatus;
  } catch {
    // The worker was evicted and has not woken yet. The message above is itself what
    // wakes it, so the next refresh works; saying so beats a blank header.
    paintStatus({
      state: "offline",
      paired: false,
      hostPermission: false,
      access: "",
      lastError: "Waking up. Reopen this panel.",
      pendingCommands: 0,
    });
    return;
  }
  paintStatus(status);
}

// --- the frames the daemon sends -------------------------------------------

/**
 * Apply one `browser.panel_event`.
 *
 * Exported so the shape of the state machine is readable in one place: every event
 * name in `ALL_PANEL_EVENTS` has a branch, and an unknown one is a newer daemon,
 * which is reported rather than swallowed.
 */
export function applyPanelEvent(event: string, payload: Record<string, unknown>): void {
  const text = typeof payload["text"] === "string" ? (payload["text"] as string) : "";
  switch (event) {
    case PANEL_EVENT_STATE: {
      // The daemon is the only thing that knows whether a turn is running: the panel
      // may have been opened halfway through one, in a window that never sent it.
      setTurn(payload["running"] === true);
      if (payload["running"] !== true) {
        streaming = null;
      }
      return;
    }
    case PANEL_EVENT_DELTA: {
      setTurn(true);
      if (!streaming) {
        streaming = say("jarvis", "");
      }
      streaming.textContent = (streaming.textContent ?? "") + text;
      el.transcript?.scrollTo({ top: el.transcript.scrollHeight });
      return;
    }
    case PANEL_EVENT_TOOL: {
      setTurn(true);
      const name = typeof payload["name"] === "string" ? (payload["name"] as string) : "a tool";
      streaming = null;
      say("tool", text || name);
      return;
    }
    case PANEL_EVENT_APPROVAL: {
      const id = typeof payload["id"] === "string" ? (payload["id"] as string) : "";
      showApproval(id, text || "Jarvis wants to act on this page.");
      return;
    }
    case PANEL_EVENT_STEERED: {
      // The note LANDED. Until this frame arrives it is shown as pending, because
      // the turn takes a steer at its next step and only the daemon knows when that
      // happened — telling the user it landed early is the one lie this costs.
      const id = typeof payload["id"] === "string" ? (payload["id"] as string) : "";
      const node = pendingSteers.get(id) ?? null;
      if (node) {
        node.dataset["pending"] = "false";
        node.textContent = `Steer: ${payload["text"] ?? node.dataset["text"] ?? ""}`;
        pendingSteers.delete(id);
      }
      return;
    }
    case PANEL_EVENT_DONE: {
      setTurn(false);
      streaming = null;
      clearApproval();
      // A turn that ended without consuming a steer never will. Saying so is the
      // whole reason the note was marked pending in the first place.
      for (const [id, node] of pendingSteers) {
        node.textContent = `${node.dataset["text"] ?? ""} — not taken; the turn ended first`;
        node.dataset["pending"] = "false";
        pendingSteers.delete(id);
      }
      return;
    }
    case PANEL_EVENT_ERROR: {
      setTurn(false);
      streaming = null;
      clearApproval();
      say("error", text || "Iron Jarvis could not finish that.");
      return;
    }
    default:
      say("error", `Iron Jarvis sent something this add-on does not understand (${event}).`);
      return;
  }
}

chrome.runtime.onMessage.addListener((message: unknown) => {
  // Read the discriminant as a plain string first: this arrives from another
  // context, so it is untrusted input rather than a value TypeScript has narrowed.
  const body = (message ?? {}) as { kind?: string; event?: string; payload?: unknown };
  if (body.kind === "panel_event") {
    const payload = (body.payload ?? {}) as Record<string, unknown>;
    applyPanelEvent(String(body.event ?? ""), payload);
    return;
  }
  if (body.kind === "panel_status") {
    void refresh();
  }
});

// --- the controls -----------------------------------------------------------

el.open?.addEventListener("click", () => {
  void chrome.runtime.sendMessage({ kind: "open_jarvis" });
});

el.toggle?.addEventListener("click", () => {
  el.toggle?.setAttribute("disabled", "true");
  void chrome.runtime
    .sendMessage({ kind: "toggle_connection" })
    .then(() => refresh())
    .finally(() => el.toggle?.removeAttribute("disabled"));
});

el.send?.addEventListener("click", () => {
  const text = (el.ask?.value ?? "").trim();
  if (!text) {
    return;
  }
  say("you", text);
  if (el.ask) {
    el.ask.value = "";
  }
  setTurn(true);
  streaming = null;
  void post(PANEL_ACTION_SEND, { text }).then((sent) => {
    if (!sent) {
      setTurn(false);
      say("error", "That did not reach Iron Jarvis — this browser is not connected.");
    }
  });
});

el.stop?.addEventListener("click", () => {
  // The button is NOT flipped to idle here. Stop is a request the daemon answers
  // with `done`, and a panel that painted the turn as finished on the press would
  // be claiming an abort it cannot perform — a tool already running still finishes.
  say("tool", "Stop requested. The step already running will finish.");
  void post(PANEL_ACTION_STOP);
});

el.steer?.addEventListener("click", () => {
  const text = (el.ask?.value ?? "").trim();
  if (!text || turnState() !== "running") {
    return;
  }
  steerSeq += 1;
  const id = `steer_${steerSeq}`;
  const node = say("steer", `Steer (pending): ${text}`);
  node.dataset["pending"] = "true";
  node.dataset["text"] = `Steer: ${text}`;
  pendingSteers.set(id, node);
  if (el.ask) {
    el.ask.value = "";
  }
  void post(PANEL_ACTION_STEER, { id, text }).then((sent) => {
    if (!sent) {
      node.dataset["pending"] = "false";
      node.textContent = `${node.dataset["text"] ?? ""} — not sent; this browser is not connected`;
      pendingSteers.delete(id);
    }
  });
});

el.approve?.addEventListener("click", () => {
  const id = pendingApprovalId;
  clearApproval();
  void post(PANEL_ACTION_APPROVE, { id });
});

el.deny?.addEventListener("click", () => {
  const id = pendingApprovalId;
  clearApproval();
  void post(PANEL_ACTION_DENY, { id });
});

// Tell the daemon the panel is gone, so a turn it is narrating to nobody can stop
// narrating. `pagehide` rather than `unload`: Chrome does not reliably fire `unload`
// for an extension page, and a close nobody reported leaves the daemon streaming.
window.addEventListener("pagehide", () => {
  void post(PANEL_ACTION_CLOSE);
});

paintVersion();
void refresh();
void post(PANEL_ACTION_OPEN);
