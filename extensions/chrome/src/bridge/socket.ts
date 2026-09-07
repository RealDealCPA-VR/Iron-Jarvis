// The one WebSocket to the Iron Jarvis daemon, and the pairing state machine
// that runs over it.
//
// There is exactly one socket per browser (D08). It carries the pairing token as
// a QUERY PARAMETER because the daemon reads `?token=` and never a header — a
// browser cannot set headers on a WebSocket handshake at all, which is why the
// daemon's verifier was written that way. An unpaired add-on connects with
// `?pairing=1` instead and is held in a RESTRICTED state where the only frame it
// may send is `browser.pairing_ack`; anything else closes the socket with 1008.
//
// Four silent failures this file is shaped around:
//
//   * A token that outlives its grant. `POST /browser/forget` deletes the daemon's
//     hash, so a stored token then earns 1008 forever while the add-on believes it
//     is paired and the card sits on "Waiting to pair". So a 1008 on a
//     token-bearing connect DROPS the stored token and the next attempt pairs
//     afresh. Without that the only cure is reinstalling the add-on.
//   * A reconnect storm. A daemon that is not running refuses instantly, so a
//     fixed-interval retry is a busy loop inside a service worker. Backoff is
//     exponential with jitter and is reset only by a frame that proves the daemon
//     accepted us, never by the socket merely opening.
//   * A replaced connection that fights back. After `browser.connection_replaced`
//     another browser is authoritative; reconnecting immediately would take the
//     session straight back and the two would alternate forever. So replacement
//     parks the backoff at its ceiling.
//   * A frame too large to parse. The daemon refuses anything over
//     MAX_FRAME_BYTES rather than JSON-decoding it on its event loop, so this side
//     refuses to SEND one and says which method produced it. A response silently
//     dropped by the daemon reads to the model as a timeout on a call that worked.
//
// MV3 lifetime, stated plainly because it is a real limitation and not a bug to
// hunt later: a service worker is torn down when idle. WebSocket traffic resets
// that idle timer, so an active bridge stays alive, but a bridge that has been
// silent long enough is unloaded and its socket closes. It reconnects on the next
// event that wakes the worker (browser startup, the popup opening, a tab
// activating). A timer-based keepalive would need the `alarms` permission, which
// D26 deliberately keeps out of the manifest.

import {
  FRAME_COMMAND,
  FRAME_CONNECTION_REPLACED,
  FRAME_DIRECTIVE,
  FRAME_EVENT,
  FRAME_HELLO,
  FRAME_PAIRED,
  FRAME_PAIRING_ACK,
  FRAME_PAIRING_REQUIRED,
  FRAME_READY,
  FRAME_RESPONSE,
  MAX_FRAME_BYTES,
  type CommandFrame,
  type DirectiveFrame,
  type ResponseFrame,
} from "../protocol";
import { browserError } from "./errors";
import type { Dispatcher } from "./dispatch";

/** The daemon's loopback address. Never configurable: the bridge is local-only. */
export const DAEMON_WS_URL = "ws://127.0.0.1:8787/browser/ws";

/** `chrome.storage.local` keys. Namespaced so a future key cannot collide. */
export const STORAGE_TOKEN_KEY = "ij.browser.pairing_token";
export const STORAGE_SUSPENDED_KEY = "ij.browser.suspended";

/** Reconnect backoff, in milliseconds. Bounded at both ends. */
export const BACKOFF_MIN_MS = 1_000;
export const BACKOFF_MAX_MS = 30_000;
export const BACKOFF_FACTOR = 2;

/** The close code the daemon uses for every refusal (bad token, bad origin). */
export const CLOSE_POLICY_VIOLATION = 1008;

/** What the popup renders. Every field is observed, none is inferred. */
export type BridgeState = "offline" | "pairing" | "connected" | "replaced" | "suspended";

/** Every state a bridge can be in, so a new one cannot be left unhandled. */
export const BRIDGE_STATES = ["offline", "pairing", "connected", "replaced", "suspended"] as const;

/**
 * What the popup's one button will DO in a given state.
 *
 * The label and the action are read off THIS function by both sides — the popup
 * for the word, the service worker for the effect — because a per-branch label
 * written beside a per-branch handler is how a button ends up saying "Disconnect"
 * over a socket that is already down (and then PERSISTING a suspend flag the user
 * never asked for, which nothing else in the product explains). One mapping, so
 * the word and the effect cannot disagree.
 *
 * `"none"` is a real answer, not a gap: while the bridge is merely offline it is
 * already retrying, there is nothing for a button to do, and D28's disconnected
 * state shows no second button at all.
 */
export type ToggleAction = "connect" | "disconnect" | "none";

export function toggleAction(state: BridgeState): ToggleAction {
  switch (state) {
    case "connected":
    case "pairing":
      return "disconnect";
    case "suspended":
    case "replaced":
      // A suspended browser MUST always offer the way back: the flag is persisted
      // across worker eviction, so a state with no Connect button is a browser the
      // user cannot revive from the only add-on surface there is.
      return "connect";
    case "offline":
    default:
      return "none";
  }
}

/** The word on the button for each action. `""` means: render no button. */
export const TOGGLE_LABELS: Record<ToggleAction, string> = {
  connect: "Connect",
  disconnect: "Disconnect",
  none: "",
};

export interface BridgeStatus {
  state: BridgeState;
  paired: boolean;
  hostPermission: boolean;
  /** Browser access mode, only if the daemon has told us. `""` means unknown. */
  access: string;
  /** The last refusal or socket error, in the daemon's own words where there is one. */
  lastError: string;
  pendingCommands: number;
}

export interface BridgeSocketOptions {
  dispatcher: Dispatcher;
  /** Read live, never cached: the user may grant site access at any moment. */
  hostPermission: () => Promise<boolean>;
  /** Handle one `browser.directive`. Resolves to the result the daemon gets back. */
  onDirective: (action: string, params: Record<string, unknown>) => Promise<Record<string, unknown>>;
  extensionId: string;
  extensionVersion: string;
  /** Called whenever the status the popup shows changes. */
  onStatusChange?: (status: BridgeStatus) => void;
}

export class BridgeSocket {
  private readonly opts: BridgeSocketOptions;
  private ws: WebSocket | null = null;
  private token = "";
  private suspended = false;
  private state: BridgeState = "offline";
  private access = "";
  private lastError = "";
  private hostPermission = false;
  private backoffMs = BACKOFF_MIN_MS;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private pairingRequestId = "";

  constructor(opts: BridgeSocketOptions) {
    this.opts = opts;
  }

  status(): BridgeStatus {
    return {
      state: this.suspended ? "suspended" : this.state,
      paired: Boolean(this.token),
      hostPermission: this.hostPermission,
      access: this.access,
      lastError: this.lastError,
      pendingCommands: this.opts.dispatcher.pending,
    };
  }

  /** The pairing request id the daemon minted, or `""`. Read by the popup copy. */
  get pendingPairingId(): string {
    return this.pairingRequestId;
  }

  /**
   * Load the stored token and the suspend flag, then connect unless suspended.
   *
   * Reading storage before the first connect matters: connecting with `?pairing=1`
   * while a valid token sits in storage would ask the user to pair a browser that
   * is already paired, and the daemon would mint a second credential for it.
   */
  async start(): Promise<void> {
    const stored = await chrome.storage.local.get([STORAGE_TOKEN_KEY, STORAGE_SUSPENDED_KEY]);
    const token = stored[STORAGE_TOKEN_KEY];
    this.token = typeof token === "string" ? token : "";
    this.suspended = stored[STORAGE_SUSPENDED_KEY] === true;
    this.hostPermission = await this.opts.hostPermission();
    if (this.suspended) {
      this.setState("suspended");
      return;
    }
    this.connect();
  }

  /** Open the socket now, cancelling any pending retry. Safe to call when open. */
  connect(): void {
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    const live = this.ws;
    if (live && (live.readyState === WebSocket.OPEN || live.readyState === WebSocket.CONNECTING)) {
      return;
    }
    const url = this.token
      ? `${DAEMON_WS_URL}?token=${encodeURIComponent(this.token)}`
      : `${DAEMON_WS_URL}?pairing=1`;
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch (err) {
      // `new WebSocket` throws synchronously when the runtime refuses the URL.
      // Retrying is still right, but the reason must be recorded or the popup
      // shows a bare "Not connected" with nothing behind it.
      this.lastError = err instanceof Error ? err.message : String(err);
      this.setState("offline");
      this.scheduleRetry();
      return;
    }
    this.ws = ws;
    ws.addEventListener("open", () => {
      void this.onOpen(ws);
    });
    ws.addEventListener("message", (event: MessageEvent) => {
      void this.onMessage(event);
    });
    ws.addEventListener("close", (event: CloseEvent) => {
      this.onClose(event);
    });
    ws.addEventListener("error", () => {
      // The `error` event carries no detail by design (it would leak cross-origin
      // information), so the only honest words are that the socket failed.
      // `close` follows and drives the retry; this handler exists so the popup
      // has something true to show meanwhile.
      this.lastError = this.lastError || "could not reach Iron Jarvis on 127.0.0.1:8787";
    });
  }

  /**
   * Stop reconnecting and close the live socket, remembering the choice.
   *
   * The flag is PERSISTED because a service worker is torn down when idle: a
   * suspend held only in memory would silently undo itself the next time Chrome
   * unloaded the worker, and the user would find the bridge connected again with
   * no action of their own.
   */
  async suspend(): Promise<void> {
    this.suspended = true;
    await chrome.storage.local.set({ [STORAGE_SUSPENDED_KEY]: true });
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    this.closeSocket();
    this.setState("suspended");
  }

  /** Clear the suspend flag and connect. The exact inverse of `suspend`. */
  async resume(): Promise<void> {
    this.suspended = false;
    await chrome.storage.local.set({ [STORAGE_SUSPENDED_KEY]: false });
    this.backoffMs = BACKOFF_MIN_MS;
    this.lastError = "";
    this.connect();
  }

  /**
   * Re-send `browser.hello` so the daemon learns the current host-permission state.
   *
   * Called when the user grants or revokes site access. Without it the daemon's
   * card would keep showing "no site access" until something else happened to
   * send a frame, and the user who just pressed the button would conclude it
   * failed and press it again.
   */
  async announce(): Promise<void> {
    this.hostPermission = await this.opts.hostPermission();
    this.emitStatus();
    if (!this.token) {
      return;
    }
    this.sendHello();
  }

  /** Emit one `browser.event`. A no-op unless the socket is paired and open. */
  emitEvent(eventId: string, event: string, payload: Record<string, unknown>): void {
    if (!this.isOpen() || !this.token) {
      return;
    }
    this.send({ id: eventId, type: FRAME_EVENT, event, payload });
  }

  // --- socket lifecycle -------------------------------------------------

  private async onOpen(ws: WebSocket): Promise<void> {
    if (ws !== this.ws) {
      return;
    }
    this.lastError = "";
    this.hostPermission = await this.opts.hostPermission();
    if (this.token) {
      // A paired socket greets first. An UNPAIRED socket must not: it is
      // RESTRICTED, and `browser.hello` is not `browser.pairing_ack`, so greeting
      // would close the very socket that is waiting to be paired with 1008.
      this.sendHello();
      this.setState("connected");
      return;
    }
    this.setState("pairing");
  }

  private onClose(event: CloseEvent): void {
    this.ws = null;
    this.pairingRequestId = "";
    if (event.code === CLOSE_POLICY_VIOLATION && this.token) {
      // The daemon refused this credential — Forget was pressed, the pairing was
      // revoked, or the stored hash is gone. Keeping the token would earn the same
      // 1008 forever, so drop it and let the next attempt pair afresh.
      this.lastError = "Iron Jarvis refused this browser's pairing; pairing again.";
      void this.forgetToken();
    } else if (event.code === CLOSE_POLICY_VIOLATION) {
      this.lastError = event.reason || "Iron Jarvis refused the connection.";
    }
    if (this.suspended) {
      this.setState("suspended");
      return;
    }
    if (this.state !== "replaced") {
      this.setState("offline");
    }
    this.scheduleRetry();
  }

  private scheduleRetry(): void {
    if (this.suspended || this.retryTimer !== null) {
      return;
    }
    // Jitter, so many browsers restarting together do not arrive in lockstep.
    const base = Math.min(BACKOFF_MAX_MS, this.backoffMs);
    const delay = base + Math.floor(Math.random() * (base / 4));
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      this.connect();
    }, delay);
    this.backoffMs = Math.min(BACKOFF_MAX_MS, this.backoffMs * BACKOFF_FACTOR);
  }

  private closeSocket(): void {
    const ws = this.ws;
    this.ws = null;
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      ws.close(1000, "closed by the user");
    }
  }

  private async forgetToken(): Promise<void> {
    this.token = "";
    await chrome.storage.local.remove(STORAGE_TOKEN_KEY);
    this.emitStatus();
  }

  // --- frames ------------------------------------------------------------

  private async onMessage(event: MessageEvent): Promise<void> {
    const raw = typeof event.data === "string" ? event.data : "";
    if (!raw) {
      return;
    }
    let frame: Record<string, unknown>;
    try {
      frame = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      this.lastError = "Iron Jarvis sent a frame this add-on could not read.";
      this.emitStatus();
      return;
    }
    const kind = String(frame["type"] ?? "");
    switch (kind) {
      case FRAME_PAIRING_REQUIRED:
        this.onPairingRequired(String(frame["request_id"] ?? ""));
        return;
      case FRAME_PAIRED:
        await this.onPaired(String(frame["token"] ?? ""));
        return;
      case FRAME_READY:
        this.onReady(frame);
        return;
      case FRAME_CONNECTION_REPLACED:
        this.onConnectionReplaced(frame);
        return;
      case FRAME_COMMAND:
        // Deliberately not awaited: concurrent in-flight commands are required, so
        // a slow read must not hold the read loop and delay the next command.
        void this.answerCommand(frame as unknown as CommandFrame);
        return;
      case FRAME_DIRECTIVE:
        void this.answerDirective(frame as unknown as DirectiveFrame);
        return;
      default:
        // An unknown frame type is the daemon running ahead of this add-on.
        // Record it and carry on: closing the socket would break a bridge that
        // otherwise works.
        this.lastError = `Iron Jarvis sent an unsupported frame (${kind || "no type"}).`;
        this.emitStatus();
        return;
    }
  }

  private onPairingRequired(requestId: string): void {
    this.pairingRequestId = requestId;
    this.setState("pairing");
    // The one frame a restricted socket may send. Sent immediately, because the
    // daemon's five-minute pairing deadline is already running.
    this.send({ type: FRAME_PAIRING_ACK, request_id: requestId });
  }

  private async onPaired(token: string): Promise<void> {
    if (!token) {
      this.lastError = "Iron Jarvis sent an empty pairing token.";
      this.emitStatus();
      return;
    }
    // The plaintext arrives exactly once, ever. Storing it before anything else
    // can throw is deliberate: losing it means the user must press Forget in Iron
    // Jarvis and pair again, and nothing in the add-on can recover it.
    this.token = token;
    await chrome.storage.local.set({ [STORAGE_TOKEN_KEY]: token });
    this.backoffMs = BACKOFF_MIN_MS;
    this.lastError = "";
    this.setState("connected");
    // The socket has just left RESTRICTED state, so this is the first moment a
    // greeting is legal — and the daemon holds no version or host-permission
    // state for this connection until one arrives.
    this.sendHello();
  }

  private onReady(frame: Record<string, unknown>): void {
    this.backoffMs = BACKOFF_MIN_MS;
    this.lastError = "";
    const access = frame["access"];
    if (typeof access === "string") {
      // Only if the daemon actually said so. Inventing a mode here would put a
      // confident "Interactive" in the popup while the daemon refused every
      // acting call — the exact shape of dishonesty this product forbids.
      this.access = access;
    }
    this.setState("connected");
  }

  private onConnectionReplaced(frame: Record<string, unknown>): void {
    const error = frame["error"] as { message?: string } | undefined;
    this.lastError = error?.message || "Another browser connected and replaced this one.";
    // Park the backoff at its ceiling: the other browser is authoritative now, and
    // an immediate reconnect would take the session straight back off it.
    this.backoffMs = BACKOFF_MAX_MS;
    this.setState("replaced");
  }

  private async answerCommand(frame: CommandFrame): Promise<void> {
    const response = await this.opts.dispatcher.dispatch(frame);
    this.sendResponse(response, String(frame.method ?? ""));
  }

  private async answerDirective(frame: DirectiveFrame): Promise<void> {
    const id = String(frame.id ?? "");
    const action = String(frame.action ?? "");
    try {
      const result = await this.opts.onDirective(action, { ...(frame.params ?? {}) });
      this.sendResponse({ id, type: FRAME_RESPONSE, success: true, result }, action);
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      this.sendResponse(
        { id, type: FRAME_RESPONSE, success: false, error: browserError("EXTENSION_ERROR", { detail }) },
        action,
      );
    }
  }

  private sendResponse(response: ResponseFrame, label: string): void {
    const body = JSON.stringify(response);
    const size = byteLength(body);
    if (size > MAX_FRAME_BYTES) {
      // The daemon refuses an oversized frame BEFORE json.loads, so sending this
      // would be answered with nothing at all and the model would read a timeout
      // on a call that actually succeeded. Refusing here names the method and the
      // size, which is a fact the model can act on by asking for less.
      this.sendRaw(
        JSON.stringify({
          id: response.id,
          type: FRAME_RESPONSE,
          success: false,
          error: browserError("EXTENSION_ERROR", {
            detail: `${label || "the result"} produced ${size} bytes, over the ${MAX_FRAME_BYTES}-byte frame limit`,
          }),
        }),
      );
      return;
    }
    this.sendRaw(body);
  }

  private sendHello(): void {
    this.send({
      type: FRAME_HELLO,
      extension_id: this.opts.extensionId,
      extension_version: this.opts.extensionVersion,
      host_permission: this.hostPermission,
    });
  }

  private send(frame: Record<string, unknown>): void {
    this.sendRaw(JSON.stringify(frame));
  }

  private sendRaw(body: string): void {
    if (!this.isOpen()) {
      return;
    }
    this.ws?.send(body);
  }

  private isOpen(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  private setState(state: BridgeState): void {
    this.state = state;
    this.emitStatus();
  }

  private emitStatus(): void {
    this.opts.onStatusChange?.(this.status());
  }
}

/** UTF-8 byte length, which is what the daemon's cap counts. */
function byteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}
