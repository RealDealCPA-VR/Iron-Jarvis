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
  PANEL_ACTION_VOICE,
  PANEL_EVENT_APPROVAL,
  PANEL_EVENT_DELTA,
  PANEL_EVENT_DONE,
  PANEL_EVENT_ERROR,
  PANEL_EVENT_MODELS,
  PANEL_EVENT_STATE,
  PANEL_EVENT_STEERED,
  PANEL_EVENT_TOOL,
  PANEL_EVENT_TRANSCRIPT,
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

/**
 * What the access mode MEANS for this panel (v1.262.0), or "" when there is
 * nothing to say (off has the empty state; unknown is named in the header).
 *
 * Read only strips every acting tool from a panel turn — correctly, and silently.
 * The user's words for what that looks like: "acts as a chat bot next to the
 * window". So the mode is explained where they are looking, with the switch named.
 */
export function modeHint(access: string): string {
  switch (access) {
    case "read_only":
      return (
        "Read only: Jarvis can look at this page but not act on it. To let it click, " +
        "type and navigate for you, open Jarvis → Browser and choose Interactive."
      );
    case "interactive":
      return (
        "Interactive: Jarvis can work this page for you — navigate, click, type. " +
        "Each action that changes a page asks you first; Allow for this tab covers this tab " +
        "until you close it, Allow for this task covers the rest of this message."
      );
    default:
      return "";
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
  model: document.getElementById("model") as HTMLSelectElement | null,
  version: document.getElementById("version"),
  open: document.getElementById("open") as HTMLButtonElement | null,
  toggle: document.getElementById("toggle") as HTMLButtonElement | null,
  transcript: document.getElementById("transcript"),
  approval: document.getElementById("approval"),
  approvalText: document.getElementById("approval-text"),
  approve: document.getElementById("approve") as HTMLButtonElement | null,
  approveTask: document.getElementById("approve-task") as HTMLButtonElement | null,
  approveTab: document.getElementById("approve-tab") as HTMLButtonElement | null,
  tabAllowed: document.getElementById("tab-allowed"),
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
    // v1.267.0: what the mode MEANS lives in the pill's tooltip, not in a
    // paragraph under the header — "not too much in the way of instruction".
    el.access.title = modeHint(status.access);
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
      // v1.266.0: whether the tab the user is looking at holds an approval grant.
      // Absent (an older daemon) reads as not allowed — the line stays hidden.
      if (el.tabAllowed) {
        el.tabAllowed.hidden = payload["tab_allowed"] !== true;
      }
      return;
    }
    case PANEL_EVENT_MODELS: {
      paintModels(payload);
      const voice = (payload["voice"] ?? {}) as { available?: unknown; hint?: unknown };
      voiceAvailable = voice.available === true;
      voiceHint = typeof voice.hint === "string" ? voice.hint : "";
      refreshComposer();
      return;
    }
    case PANEL_EVENT_TRANSCRIPT: {
      applyTranscript(payload);
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
      const reason = typeof payload["reason"] === "string" ? (payload["reason"] as string) : "";
      if (reason.startsWith("voice")) {
        // A dictation that could not happen: the box keeps what it had, the
        // microphone goes back to idle, and the sentence says what to do.
        resetVoice();
        say("error", text || "Iron Jarvis could not take dictation.");
        return;
      }
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

// v1.264.0: ENTER SENDS. The composer used to take Enter as a newline and only
// the button sent — "I need to physically click send". Enter now presses the
// button that fits the moment: Send when idle, Steer while a turn is running
// (Send is refused mid-turn anyway). Shift+Enter keeps the newline for people
// who want one. Exported so the contract is pinned without a real keyboard.
export function keyToPress(key: string, shift: boolean, running: boolean): "send" | "steer" | null {
  if (key !== "Enter" || shift) return null;
  return running ? "steer" : "send";
}

el.ask?.addEventListener("keydown", (event: KeyboardEvent) => {
  // The ONE place the running state is written is `setTurn` (the body attribute
  // the stylesheet reads), so it is the one place this reads it from.
  const press = keyToPress(event.key, event.shiftKey, document.body.dataset["turn"] === "running");
  if (!press) return;
  event.preventDefault();
  (press === "send" ? el.send : el.steer)?.click();
});

function sendNow(): void {
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
  // v1.267.0: the pick rides every Send; nothing is stored on the daemon for it.
  const pick = currentPick();
  void post(PANEL_ACTION_SEND, { text, ...(pick ? pick : {}) }).then((sent) => {
    if (!sent) {
      setTurn(false);
      say("error", "That did not reach Iron Jarvis — this browser is not connected.");
    }
  });
  refreshComposer();
}

// v1.269.0: ONE BUTTON, TWO JOBS. With nothing typed it is the microphone; the
// moment there are words it is the arrow. Decided from the box's CONTENT at the
// press, never from a cached mode — a value set by code (a test, a paste) must
// send too. While a dictation runs, the press stops it.
el.send?.addEventListener("click", () => {
  if (voiceState === "listening") {
    stopDictation();
    return;
  }
  const text = (el.ask?.value ?? "").trim();
  if (text) {
    sendNow();
  } else {
    void startDictation();
  }
});

el.ask?.addEventListener("input", () => refreshComposer());

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

// v1.262.0: one press for the rest of this task. `scope: "task"` is answered by
// the daemon as the chat lane's "conversation" grant — the remaining rounds of
// THIS turn only; the next message starts with a clean slate and asks again.
el.approveTask?.addEventListener("click", () => {
  const id = pendingApprovalId;
  clearApproval();
  void post(PANEL_ACTION_APPROVE, { id, scope: "task" });
});

// v1.266.0: ONE APPROVAL PER TAB. `scope: "tab"` is answered by the daemon with
// the "tab" decision: this call runs, and the tab it acts on is granted until it
// closes — across messages, not just this one. The daemon's next `state` frame
// says so in the header.
el.approveTab?.addEventListener("click", () => {
  const id = pendingApprovalId;
  clearApproval();
  void post(PANEL_ACTION_APPROVE, { id, scope: "tab" });
});

el.deny?.addEventListener("click", () => {
  const id = pendingApprovalId;
  clearApproval();
  void post(PANEL_ACTION_DENY, { id });
});

// --- the composer button + dictation (v1.269.0) ---------------------------------

type VoiceState = "idle" | "listening" | "transcribing";
let voiceState: VoiceState = "idle";
let voiceAvailable = false;
let voiceHint = "";
/** What the box held when dictation began; the transcript is appended to it. */
let dictationBase = "";
let voiceStream: MediaStream | null = null;
let voiceCtx: AudioContext | null = null;
let voiceProc: ScriptProcessorNode | null = null;

/** Which face the composer button shows, from the box's content and the voice state. */
export function composerMode(text: string, state: VoiceState): "mic" | "send" | "stop" {
  if (state === "listening") return "stop";
  return text.trim() ? "send" : "mic";
}

function refreshComposer(): void {
  const button = el.send;
  if (!button) return;
  const mode = composerMode(el.ask?.value ?? "", voiceState);
  button.dataset["mode"] = mode;
  document.body.dataset["voice"] = voiceState;
  // NEVER `disabled`: a click on a disabled button dispatches nothing (the
  // repository's own v1.251.0 lesson), and a microphone with no engine behind it
  // must still answer a press with the sentence that says what to connect. The
  // greyed look is a data attribute the stylesheet reads.
  button.disabled = false;
  button.dataset["voiceOff"] = mode === "mic" && !voiceAvailable ? "true" : "false";
  if (mode === "send") {
    button.title = "Send · Enter";
  } else if (mode === "stop") {
    button.title = "Stop listening";
  } else {
    button.title = voiceAvailable
      ? "Dictate — Iron Jarvis writes what you say into the box"
      : voiceHint || "Voice is not set up in Iron Jarvis yet.";
  }
}

function setVoiceState(state: VoiceState): void {
  voiceState = state;
  refreshComposer();
}

function resetVoice(): void {
  teardownAudio();
  dictationBase = "";
  setVoiceState("idle");
}

function teardownAudio(): void {
  try {
    voiceProc?.disconnect();
  } catch {
    // already gone
  }
  voiceProc = null;
  voiceStream?.getTracks().forEach((t) => t.stop());
  voiceStream = null;
  void voiceCtx?.close().catch(() => {});
  voiceCtx = null;
}

/** The app's own downsampler: any input rate to 16 kHz mono PCM16 (little-endian). */
export function floatTo16kPCM(input: Float32Array, inRate: number): ArrayBuffer {
  let data = input;
  if (inRate !== 16000 && inRate > 0) {
    const ratio = inRate / 16000;
    const outLen = Math.floor(input.length / ratio);
    const out = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) out[i] = input[Math.floor(i * ratio)] || 0;
    data = out;
  }
  const pcm = new Int16Array(data.length);
  for (let i = 0; i < data.length; i++) {
    const s = Math.max(-1, Math.min(1, data[i] ?? 0));
    pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return pcm.buffer;
}

function base64Of(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

async function startDictation(): Promise<void> {
  if (voiceState !== "idle") return;
  if (!voiceAvailable) {
    say("error", voiceHint || "Voice is not set up in Iron Jarvis yet.");
    return;
  }
  const media = navigator.mediaDevices;
  if (!media || typeof media.getUserMedia !== "function") {
    say("error", "No microphone is available to this panel.");
    return;
  }
  let stream: MediaStream;
  try {
    stream = await media.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
  } catch (err) {
    const name = err instanceof DOMException ? err.name : "";
    say(
      "error",
      name === "NotAllowedError"
        ? "Microphone blocked for Iron Jarvis. Allow it for this add-on in your browser's site permissions, then try again."
        : "The microphone could not be opened.",
    );
    return;
  }
  type AC = typeof AudioContext;
  const Ctor: AC | undefined =
    (window as unknown as { AudioContext?: AC; webkitAudioContext?: AC }).AudioContext ??
    (window as unknown as { webkitAudioContext?: AC }).webkitAudioContext;
  if (!Ctor) {
    stream.getTracks().forEach((t) => t.stop());
    say("error", "This browser cannot capture audio here.");
    return;
  }
  let ctx: AudioContext;
  try {
    ctx = new Ctor({ sampleRate: 16000 });
  } catch {
    ctx = new Ctor();
  }
  voiceStream = stream;
  voiceCtx = ctx;
  dictationBase = el.ask?.value ?? "";
  setVoiceState("listening");
  const started = await post(PANEL_ACTION_VOICE, { op: "start" });
  if (!started) {
    resetVoice();
    say("error", "That did not reach Iron Jarvis — this browser is not connected.");
    return;
  }
  const source = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  voiceProc = proc;
  proc.onaudioprocess = (e) => {
    if (voiceState !== "listening") return;
    const pcm = floatTo16kPCM(e.inputBuffer.getChannelData(0), ctx.sampleRate);
    if (pcm.byteLength) {
      void post(PANEL_ACTION_VOICE, { op: "chunk", pcm_b64: base64Of(pcm) });
    }
  };
  source.connect(proc);
  proc.connect(ctx.destination);
}

function stopDictation(): void {
  if (voiceState !== "listening") return;
  teardownAudio();
  setVoiceState("transcribing");
  void post(PANEL_ACTION_VOICE, { op: "stop" }).then((sent) => {
    if (!sent) {
      resetVoice();
      say("error", "That did not reach Iron Jarvis — this browser is not connected.");
    }
  });
}

/** Paint the dictation so far into the box; the final frame hands the box back. */
function applyTranscript(payload: Record<string, unknown>): void {
  if (payload["listening"] === true && voiceState === "idle") {
    // The daemon confirmed a start this panel did not initiate (a stale frame); ignore.
    return;
  }
  const text = typeof payload["text"] === "string" ? (payload["text"] as string) : "";
  const partial = typeof payload["partial"] === "string" ? (payload["partial"] as string) : "";
  const heard = [text, partial].filter((s) => s.trim()).join(" ").trim();
  if (el.ask && (heard || payload["final"] === true)) {
    const base = dictationBase.replace(/\s+$/, "");
    el.ask.value = base && heard ? `${base} ${heard}` : base || heard;
  }
  if (payload["final"] === true) {
    dictationBase = "";
    setVoiceState("idle");
    el.ask?.focus();
  } else {
    refreshComposer();
  }
}

// --- the model (v1.267.0) ---------------------------------------------------

/** `chrome.storage.local` key for the model picked in THIS browser's sidebar. */
export const STORAGE_MODEL_KEY = "ij.panel.model";

interface ModelPick {
  provider: string;
  model: string;
}

interface ModelRow {
  provider: string;
  model: string;
  name?: string;
  available?: boolean;
}

/** The option label: the catalog's name when it has one, else the model id, then the provider. */
export function modelLabel(row: ModelRow): string {
  const head = row.name && row.name !== row.model ? row.name : row.model;
  return `${head} · ${row.provider}`;
}

/** The option value is the pick itself, so a later Send needs no lookup. */
export function pickValue(pick: ModelPick): string {
  return JSON.stringify({ provider: pick.provider, model: pick.model });
}

export function pickFromValue(value: string): ModelPick | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as Partial<ModelPick>;
    if (typeof parsed.provider === "string" && typeof parsed.model === "string" && parsed.model) {
      return { provider: parsed.provider, model: parsed.model };
    }
  } catch {
    // Not a pick this build wrote.
  }
  return null;
}

/** The pick the user last made here, or null for the app's default. */
let storedPick: ModelPick | null = null;

function currentPick(): ModelPick | null {
  return pickFromValue(el.model?.value ?? "") ?? storedPick;
}

async function loadPick(): Promise<void> {
  try {
    const got = await chrome.storage?.local?.get(STORAGE_MODEL_KEY);
    storedPick = pickFromValue(String(got?.[STORAGE_MODEL_KEY] ?? ""));
  } catch {
    // No storage here (the panel outside an add-on context): default it is.
    storedPick = null;
  }
}

function paintModels(payload: Record<string, unknown>): void {
  const select = el.model;
  if (!select) return;
  const rows = Array.isArray(payload["models"]) ? (payload["models"] as ModelRow[]) : [];
  const dflt = (payload["default"] ?? {}) as Partial<ModelPick>;
  const defaultRow = rows.find((r) => r.provider === dflt.provider && r.model === dflt.model);
  select.textContent = "";
  const first = document.createElement("option");
  first.value = "";
  // The Default option NAMES the model Jarvis would use, so "Default" is never a
  // word the user has to go and look up.
  first.textContent = defaultRow
    ? `Default · ${modelLabel(defaultRow)}`
    : dflt.model
      ? `Default · ${dflt.model}`
      : "Default";
  select.appendChild(first);
  for (const row of rows) {
    if (!row || typeof row.provider !== "string" || typeof row.model !== "string") continue;
    const option = document.createElement("option");
    option.value = pickValue(row);
    option.textContent = modelLabel(row);
    if (row.available === false) {
      // Offered, greyed, and explained on hover: a model that vanished from the
      // list would leave the user wondering where it went; one that is greyed
      // says what to do about it.
      option.disabled = true;
      option.title = "Not connected in Jarvis";
    }
    select.appendChild(option);
  }
  // Restore the remembered pick only if the daemon still lists it — a pick the
  // list no longer carries is Default, which is what the daemon would refuse it
  // back to anyway.
  const wanted = storedPick ? pickValue(storedPick) : "";
  const present = Array.from(select.options).some((o) => o.value === wanted && !o.disabled);
  select.value = present ? wanted : "";
  paintModelTitle();
}

/** v1.269.0: the select sits invisibly over a small icon, so its tooltip is the only place the pick shows. */
function paintModelTitle(): void {
  const select = el.model;
  if (!select) return;
  const label = select.selectedOptions[0]?.textContent ?? "Default";
  select.title = `Model: ${label}`;
}

el.model?.addEventListener("change", () => {
  const pick = pickFromValue(el.model?.value ?? "");
  storedPick = pick;
  paintModelTitle();
  try {
    if (pick) {
      void chrome.storage?.local?.set({ [STORAGE_MODEL_KEY]: pickValue(pick) });
    } else {
      void chrome.storage?.local?.remove(STORAGE_MODEL_KEY);
    }
  } catch {
    // No storage: the pick lasts for this panel's lifetime, which is still true.
  }
});

void loadPick();

// Tell the daemon the panel is gone, so a turn it is narrating to nobody can stop
// narrating. `pagehide` rather than `unload`: Chrome does not reliably fire `unload`
// for an extension page, and a close nobody reported leaves the daemon streaming.
window.addEventListener("pagehide", () => {
  void post(PANEL_ACTION_CLOSE);
});

paintVersion();
void refresh();
void post(PANEL_ACTION_OPEN);
refreshComposer();

// v1.266.0: the header's "allowed in this tab" line is about the tab the user is
// looking at, so a tab switch asks the daemon for a fresh state frame. Guarded:
// the panel also runs where `chrome.tabs` is absent (the runtime test's stub).
chrome.tabs?.onActivated?.addListener?.(() => {
  void post(PANEL_ACTION_OPEN);
});
