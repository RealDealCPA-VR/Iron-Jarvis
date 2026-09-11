"use client";

// SSE consumer for token-by-token CHAT streaming (FX-01). The daemon's
// streaming chat endpoint emits Server-Sent Events on the wire schema:
//
//   token     {"text":"…"}
//   tool_call {"id","name","status":"started"|"finished","ok"?,"args"?,"output"?}
//   approval  {"id","call_id","tool","args"?,"timeout_s"?}   (v1.187.0)
//   approval_resolved {"id","call_id","tool","decision"}     (v1.187.0)
//   meta      {"provider","model"}
//   round     {"round":n}
//   done      {"reply","provider","model","tools_used","denied_tools","usage",
//              "adapted": {model,changes}|null, ...}
//   error     {"detail","status"?}
//
// with a ": keepalive" comment every ~15s of idle. This library turns that raw
// byte stream into typed events (`streamSSE`) and drives one chat turn from a
// React component (`useChatStream`). It is purely additive — the non-streaming
// POST /chat path is untouched.

import { useCallback, useRef, useState } from "react";
import { API_BASE, ApiError, flattenDetail, ijToken } from "./api";
import type { WorkflowDraft } from "@/lib/types";

// ------------------------------------------------------------------ wire types

/** A single decoded SSE frame. Discriminated on `type` (the event name). */
export type SSEEvent =
  | { type: "token"; text: string }
  | {
      type: "tool_call";
      id: string;
      name: string;
      status: "started" | "finished";
      ok?: boolean;
      args?: Record<string, unknown>;
      output?: string;
    }
  | {
      /** The turn is PAUSED on a tool that needs the user's approval
       *  (v1.187.0). The daemon holds the call until POST
       *  /chat/approvals/{id} answers or the wait times out. */
      type: "approval";
      id: string;
      call_id: string;
      tool: string;
      args?: Record<string, unknown>;
      timeout_s?: number;
    }
  | {
      /** The pause above ended — by a click or by the timeout. */
      type: "approval_resolved";
      id: string;
      call_id: string;
      tool: string;
      decision: "once" | "conversation" | "deny" | "timeout";
    }
  | { type: "meta"; provider: string; model: string }
  | { type: "round"; round: number }
  | {
      type: "done";
      reply: string;
      provider?: string;
      model?: string;
      /** SERVER-side route disclosure (v1.165.0): who was asked, who actually
       *  answered, and why. The client used to infer this by comparing against
       *  the user's EXPLICIT pick, so a downgraded default-route turn (the
       *  mock's "Done. Wrote RESULT.md" incident) surfaced nothing. */
      route?: {
        requested?: string;
        provider: string;
        model?: string;
        reason?: string;
        /** v1.228.0: on a failover, the provider that failed + why. */
        from?: string;
        why?: string;
      };
      tools_used?: string[];
      denied_tools?: string[];
      /** SERVER-derived doors into the surfaces this turn touched (v1.199.0)
       *  — executed-ok tools only, files excluded (the ArtifactsRail owns
       *  files). The client renders, never derives. */
      doors?: { href: string; label: string }[];
      /** ABSOLUTE paths of documents this turn created/edited (preview). */
      documents?: string[];
      /** The turn decided it needs the full agent (v1.108.0 — one surface). */
      escalate?: boolean;
      escalate_reason?: string;
      /** Validated roster target for the hand-off (v1.139.0): "researcher",
       *  "custom:<slug>", "remote:<name>" — null/absent keeps the default. */
      escalate_agent?: string | null;
      /** The turn proposed a reusable workflow instead of prose (v1.120.0). */
      workflow_draft?: WorkflowDraft | null;
      /** The turn RAN a saved workflow via the workflow_run tool (v1.170.0) —
       *  render the live run chip under the reply. */
      workflow_run?: { run_id: string; name: string } | null;
      /** The capability ENVELOPE bent this turn to fit a measured-weak model
       *  (v1.202.0): {model, changes:["tool_cap:<n>", ...]}. The daemon sends
       *  the key on EVERY turn (null when nothing bent — the common case);
       *  the decoder keeps only the bent shape, so absent ≡ null here. */
      adapted?: { model?: string; changes: string[] } | null;
      usage?: { input_tokens?: number; output_tokens?: number };
      /** Context-window accounting for this turn (v1.146.0). */
      context?: ContextUsage | null;
    }
  | { type: "error"; detail: string; status?: number; offline?: boolean };

/** A tool invocation as shown live in the UI — one card per tool call id,
 *  upgraded in place from `running` (started frame) to `done` (finished frame). */
export interface ToolCard {
  id: string;
  name: string;
  status: "running" | "done";
  ok?: boolean;
  args?: Record<string, unknown>;
  output?: string;
}

/** What one chat turn resolves to. `reply` is authoritative (from the `done`
 *  frame) and falls back to the accumulated token text if the stream dropped. */
export interface ChatStreamResult {
  reply: string;
  tools_used?: string[];
  /** Armed tools the engine refused this turn. Decoded from the frame since
   *  v1.148.0 but DROPPED here until v1.165.0 — the page could never show a
   *  denial, which is how a silently-blocked tool stays invisible. */
  deniedTools?: string[];
  /** Server-side route disclosure (v1.165.0) — see the done-frame field. */
  route?: {
        requested?: string;
        provider: string;
        model?: string;
        reason?: string;
        /** v1.228.0: on a failover, the provider that failed + why. */
        from?: string;
        why?: string;
      };
  /** Server-derived doors into the surfaces this turn touched (v1.199.0). */
  doors?: { href: string; label: string }[];
  /** The envelope's adaptation disclosure (v1.202.0) — see the done-frame
   *  field. Absent/null = nothing bent. */
  adapted?: { model?: string; changes: string[] } | null;
  /** Token usage for the turn (was decoded and dropped, like denied_tools). */
  usage?: { input_tokens?: number; output_tokens?: number };
  provider?: string;
  model?: string;
  /** ABSOLUTE paths of documents this turn created/edited (preview panel). */
  documents?: string[];
  /** The turn asked to be re-run as a full agent session, and why. The caller
   *  does that automatically — the user is never asked to pick a mode. */
  escalate?: boolean;
  escalateReason?: string;
  /** Validated roster target the turn chose for the hand-off (v1.139.0) —
   *  null/absent keeps the caller's default agent. */
  escalateAgent?: string | null;
  /** The turn proposed a reusable workflow — render it as a draft card. */
  workflowDraft?: WorkflowDraft | null;
  /** The turn RAN a saved workflow (v1.170.0) — render the live run chip. */
  workflowRun?: { run_id: string; name: string } | null;
  /** How much of the model's context window this turn used (v1.146.0). */
  context?: ContextUsage | null;
}

/** What one turn cost against the answering model's window (v1.146.0). The
 *  daemon computes it — the client only renders it, so the number the user
 *  sees is the number the planner actually budgeted against. */
export interface ContextUsage {
  window: number;
  used: number;
  headroom: number;
  dropped: number;
  tools_trimmed: number;
  clipped: boolean;
  recap: boolean;
  suggest_larger: boolean;
  /* --- compaction (v1.153.0) ------------------------------------------- */
  /** How full the window is against RAW demand — what the conversation would
   *  need with nothing dropped. Can exceed 100; `used` cannot, because a
   *  planned transcript always fits by construction. */
  percent?: number;
  /** "ok" | "suggest" (offer the user the choice) | "auto" (already acted). */
  level?: "ok" | "suggest" | "auto";
  /** The thresholds in force, as percentages, so the UI never hardcodes them. */
  suggest_at?: number;
  auto_at?: number;
  /** A verified summary is standing in for the older messages. */
  compacted?: boolean;
  /** How many leading messages that summary replaces. */
  covers?: number;
  /** Lines the ledger/transcript check removed from the model's draft. */
  stripped?: number;
  /** "manual" (the user chose) | "auto" (the ceiling). */
  trigger?: string;
  /** Compaction is switched off. The fill level above is still real — the
   *  setting governs the remedy, not the reporting. */
  disabled?: boolean;
}

// -------------------------------------------------------------- frame decoding

function str(v: unknown): string {
  return v === undefined || v === null ? "" : String(v);
}

/**
 * Map an SSE event name + its already-parsed JSON payload to a typed SSEEvent
 * (or null for an unknown event name). Shared by the fetch-reader path
 * (`streamSSE`) and the EventSource path (`useRunStream`) so both normalise
 * frames identically.
 */
export function sseEventFrom(
  event: string,
  data: Record<string, unknown>,
): SSEEvent | null {
  switch (event) {
    case "token":
      return { type: "token", text: str(data.text) };
    case "tool_call": {
      const ev: Extract<SSEEvent, { type: "tool_call" }> = {
        type: "tool_call",
        id: str(data.id),
        name: str(data.name),
        status: data.status === "finished" ? "finished" : "started",
      };
      if (data.ok !== undefined) ev.ok = Boolean(data.ok);
      if (data.args !== undefined && data.args !== null)
        ev.args = data.args as Record<string, unknown>;
      if (data.output !== undefined) ev.output = str(data.output);
      return ev;
    }
    case "approval": {
      const ev: Extract<SSEEvent, { type: "approval" }> = {
        type: "approval",
        id: str(data.id),
        call_id: str(data.call_id),
        tool: str(data.tool),
      };
      if (data.args !== undefined && data.args !== null)
        ev.args = data.args as Record<string, unknown>;
      if (typeof data.timeout_s === "number") ev.timeout_s = data.timeout_s;
      return ev;
    }
    case "approval_resolved": {
      const d = str(data.decision);
      return {
        type: "approval_resolved",
        id: str(data.id),
        call_id: str(data.call_id),
        tool: str(data.tool),
        decision:
          d === "once" || d === "conversation" || d === "deny" ? d : "timeout",
      };
    }
    case "meta":
      return { type: "meta", provider: str(data.provider), model: str(data.model) };
    case "round":
      return { type: "round", round: Number(data.round) || 0 };
    case "done": {
      const ev: Extract<SSEEvent, { type: "done" }> = {
        type: "done",
        reply: str(data.reply),
      };
      if (typeof data.provider === "string") ev.provider = data.provider;
      if (typeof data.model === "string") ev.model = data.model;
      if (
        data.route &&
        typeof data.route === "object" &&
        typeof (data.route as { provider?: unknown }).provider === "string"
      )
        ev.route = data.route as Extract<SSEEvent, { type: "done" }>["route"];
      if (Array.isArray(data.tools_used)) ev.tools_used = data.tools_used as string[];
      if (Array.isArray(data.denied_tools))
        ev.denied_tools = data.denied_tools as string[];
      // Doors (v1.199.0): pass through verbatim — this decoder WHITELISTS
      // fields, so an un-listed field silently vanishes from exactly the lane
      // users watch (the denied_tools lesson, learned twice already).
      if (Array.isArray(data.doors))
        ev.doors = data.doors as { href: string; label: string }[];
      // Adapted (v1.202.0): the envelope's disclosure — same whitelist
      // hazard as doors. The daemon sends null on the common (unbent) path;
      // only the bent shape (an object with a changes array) survives the
      // decode, so downstream treats absent and null identically.
      if (
        data.adapted &&
        typeof data.adapted === "object" &&
        Array.isArray((data.adapted as { changes?: unknown }).changes)
      )
        ev.adapted = data.adapted as { model?: string; changes: string[] };
      if (Array.isArray(data.documents)) ev.documents = data.documents as string[];
      if (typeof data.escalate === "boolean") ev.escalate = data.escalate;
      if (typeof data.escalate_reason === "string")
        ev.escalate_reason = data.escalate_reason;
      if (typeof data.escalate_agent === "string")
        ev.escalate_agent = data.escalate_agent; // null ≡ absent ≡ default

      if (data.workflow_draft && typeof data.workflow_draft === "object")
        ev.workflow_draft = data.workflow_draft as WorkflowDraft;
      if (
        data.workflow_run &&
        typeof data.workflow_run === "object" &&
        typeof (data.workflow_run as { run_id?: unknown }).run_id === "string"
      )
        ev.workflow_run = data.workflow_run as { run_id: string; name: string };
      if (data.usage && typeof data.usage === "object")
        ev.usage = data.usage as { input_tokens?: number; output_tokens?: number };
      if (data.context && typeof data.context === "object")
        ev.context = data.context as ContextUsage;
      return ev;
    }
    case "error": {
      const ev: Extract<SSEEvent, { type: "error" }> = {
        type: "error",
        detail: str(data.detail) || "stream error",
      };
      if (typeof data.status === "number") ev.status = data.status;
      return ev;
    }
    default:
      return null;
  }
}

/** JSON-parse an SSE `data` payload and map it to a typed event. Returns null on
 *  an unknown event name or malformed JSON. */
export function decodeSSE(event: string, rawData: string): SSEEvent | null {
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(rawData) as Record<string, unknown>;
  } catch {
    return null;
  }
  return sseEventFrom(event, data);
}

/** Parse ONE raw SSE frame (the text between blank-line separators): its
 *  `event:` name + joined `data:` lines. `:`-comments (keepalives) yield null. */
function parseFrame(raw: string): SSEEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const lineRaw of raw.split("\n")) {
    const line = lineRaw.endsWith("\r") ? lineRaw.slice(0, -1) : lineRaw;
    if (!line || line.startsWith(":")) continue; // blank or keepalive comment
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (dataLines.length === 0) return null;
  return decodeSSE(event, dataLines.join("\n"));
}

function isAbort(e: unknown): boolean {
  return e instanceof Error && e.name === "AbortError";
}

// ------------------------------------------------------------------- streamSSE

/** A stream that delivers NO bytes — not even the daemon's keepalive comment,
 *  sent after 10 s of silence since v1.246.0 — for this long is dead. */
export const STREAM_STALL_MS = 60_000;

/** The daemon prepares a turn (reads the attachments, arms the tools) BEFORE
 *  it answers the request, so no bytes flow then. Past this, the preparation
 *  is stuck rather than slow (v1.246.0). */
export const STREAM_PREP_MS = 10 * 60_000;

export function stallDetail(ms: number): string {
  return (
    `Nothing arrived from the daemon for ${Math.round(ms / 1000)} s, so this ` +
    "turn was stopped. Anything it already finished is kept — press Retry to " +
    "run it again."
  );
}

export function prepDetail(ms: number): string {
  return (
    `The daemon spent over ${Math.max(1, Math.round(ms / 60_000))} minutes ` +
    "preparing this turn without starting it, so it was stopped. Press Retry, " +
    "or attach fewer or smaller files."
  );
}

/** The watchdog's limits and the progress hook for one stream (v1.246.0). */
export interface StreamWatch {
  stallMs?: number;
  prepMs?: number;
  /** The daemon finished preparing: the response has begun. */
  onOpen?: () => void;
}

/**
 * POST `body` to an SSE endpoint and yield each decoded frame. The token rides
 * in an Authorization header (fetch, unlike EventSource, can set one). Aborts
 * are swallowed (the generator simply ends); a non-2xx response or a network
 * failure yields a single `error` event with the parsed detail.
 *
 * NEVER HANGS SILENTLY (v1.246.0). The user's report: chat "gets hung up from
 * time to time". Nothing here had a limit — a turn whose daemon stopped
 * answering spun forever. Two watchdogs end it with words instead: no bytes
 * at all for `stallMs` once the response has begun (the daemon's heartbeat
 * makes silence meaningful), and no response at all within `prepMs`. Both
 * abort OUR controller, which is linked to the caller's but is not it.
 */
export async function* streamSSE(
  path: string,
  body: unknown,
  signal?: AbortSignal,
  watch: StreamWatch = {},
): AsyncGenerator<SSEEvent> {
  const token = ijToken();
  const stallMs = watch.stallMs ?? STREAM_STALL_MS;
  const prepMs = watch.prepMs ?? STREAM_PREP_MS;
  const inner = new AbortController();
  const relay = () => inner.abort();
  if (signal?.aborted) inner.abort();
  else signal?.addEventListener("abort", relay, { once: true });
  let prepExpired = false;
  const prepTimer = setTimeout(() => {
    prepExpired = true;
    inner.abort();
  }, prepMs);
  let stalled = false;
  let stallTimer: ReturnType<typeof setTimeout> | undefined;
  try {
    let res: Response;
    try {
      res = await fetch(`${API_BASE}${path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(body),
        cache: "no-store",
        signal: inner.signal,
      });
    } catch (e) {
      if (prepExpired) {
        yield { type: "error", detail: prepDetail(prepMs), status: 0 };
        return;
      }
      if (isAbort(e)) return;
      // A genuine transport failure (daemon unreachable) — flagged `offline` so the
      // caller can distinguish it from an in-band provider `error` frame (which also
      // carries status 0 but is NOT an offline condition).
      yield { type: "error", detail: "daemon offline", status: 0, offline: true };
      return;
    } finally {
      clearTimeout(prepTimer);
    }

    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try {
        const parsed = (await res.json()) as { detail?: unknown };
        // v1.226.0: a list-shaped pydantic 422 flattens to "field: msg" (C4).
        if (parsed?.detail) detail = flattenDetail(parsed.detail);
      } catch {
        /* body wasn't JSON — keep the status line */
      }
      yield { type: "error", detail, status: res.status };
      return;
    }

    const reader = res.body?.getReader();
    if (!reader) {
      yield { type: "error", detail: "no response body", status: res.status };
      return;
    }
    watch.onOpen?.();
    const armStall = () => {
      if (stallTimer !== undefined) clearTimeout(stallTimer);
      stallTimer = setTimeout(() => {
        stalled = true;
        inner.abort();
        reader.cancel().catch(() => {
          /* already closed */
        });
      }, stallMs);
    };
    armStall();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      for (;;) {
        let chunk: ReadableStreamReadResult<Uint8Array>;
        try {
          chunk = await reader.read();
        } catch (e) {
          if (stalled) {
            yield { type: "error", detail: stallDetail(stallMs), status: 0 };
            return;
          }
          // v1.226.0: the daemon dying MID-TURN surfaces HERE, as a rejected
          // read() ("Failed to fetch" / "network error"), not in the pre-fetch
          // catch above. Flag it offline the same way so the page shows the
          // OfflineHint instead of raw transport text. Scoped to the transport
          // call only: a parser fault on a bad frame (below) is NOT offline.
          if (!isAbort(e)) {
            yield { type: "error", detail: "daemon offline", status: 0, offline: true };
          }
          return;
        }
        const { value, done } = chunk;
        if (done) {
          if (stalled) {
            yield { type: "error", detail: stallDetail(stallMs), status: 0 };
            return;
          }
          break;
        }
        // ANY bytes — a keepalive comment included — prove the daemon is alive.
        armStall();
        buffer += decoder.decode(value, { stream: true });
        let sep: number;
        // Frames are separated by a blank line.
        while ((sep = buffer.indexOf("\n\n")) !== -1) {
          const raw = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          const ev = parseFrame(raw);
          if (ev) yield ev;
        }
      }
    } catch (e) {
      if (!isAbort(e)) {
        yield { type: "error", detail: e instanceof Error ? e.message : String(e) };
      }
    } finally {
      try {
        await reader.cancel();
      } catch {
        /* already closed */
      }
    }
  } finally {
    clearTimeout(prepTimer);
    if (stallTimer !== undefined) clearTimeout(stallTimer);
    signal?.removeEventListener("abort", relay);
  }
}

// ------------------------------------------------------------ tool-card upsert

/** Merge a `tool_call` frame into the live card list (keyed by id). A `started`
 *  frame adds a `running` card; a `finished` frame flips it to `done` while
 *  preserving the args the started frame carried if the finished one omitted
 *  them. */
export function upsertTool(
  prev: ToolCard[],
  ev: Extract<SSEEvent, { type: "tool_call" }>,
): ToolCard[] {
  const patch: ToolCard = {
    id: ev.id,
    name: ev.name,
    status: ev.status === "finished" ? "done" : "running",
  };
  if (ev.ok !== undefined) patch.ok = ev.ok;
  if (ev.args !== undefined) patch.args = ev.args;
  if (ev.output !== undefined) patch.output = ev.output;
  const idx = prev.findIndex((t) => t.id === patch.id);
  if (idx === -1) return [...prev, patch];
  const next = prev.slice();
  next[idx] = { ...next[idx], ...patch };
  return next;
}

// ---------------------------------------------------------------- useChatStream

/**
 * The error `run()` throws on a failed streaming turn. Extends {@link ApiError}
 * with two facts the caller needs to decide whether a non-streaming retry is
 * SAFE:
 *   - `committed` — the stream already produced a token or ran a tool, i.e. the
 *     server did real work for this turn. Re-POSTing would re-execute it (double
 *     tool side effects), so the caller MUST NOT silently fall back.
 *   - `offline` — a genuine transport failure (daemon unreachable), as opposed
 *     to an honest in-band provider `error` frame (both surface status 0).
 */
export class StreamError extends ApiError {
  readonly committed: boolean;
  readonly offline: boolean;
  /** Text streamed before the failure (empty if none) — so the caller can keep
   *  what the user already watched appear rather than dropping it on error. */
  readonly partial: string;
  constructor(
    message: string,
    status: number,
    committed: boolean,
    offline: boolean,
    partial = "",
  ) {
    super(message, status);
    this.name = "StreamError";
    this.committed = committed;
    this.offline = offline;
    this.partial = partial;
  }
}

/** A mid-turn approval request the turn is currently PAUSED on (v1.187.0). */
export interface PendingApproval {
  id: string;
  callId: string;
  tool: string;
  args?: Record<string, unknown>;
  timeoutS?: number;
}

export interface UseChatStream {
  /** True while a turn is in flight. */
  streaming: boolean;
  /** Accumulated assistant text so far this turn. */
  text: string;
  /** Live tool cards for this turn, keyed by call id. */
  tools: ToolCard[];
  /** The approval the turn is paused on, or null. The page renders the card;
   *  answering goes through POST /chat/approvals/{id} — this hook only holds
   *  the state, so the decision has exactly one write path. */
  approval: PendingApproval | null;
  /** Where the running turn is (v1.246.0): `preparing` until the daemon has
   *  read the attachments and begun the response, then `working`; null when
   *  no turn is running. */
  phase: TurnPhase | null;
  /** The running turn's request carried attachments — so `preparing` can
   *  honestly say it is reading them. */
  withFiles: boolean;
  /** When the running turn started (ms epoch), or null. */
  startedAt: number | null;
  /** When the running turn last produced a real frame (ms epoch), or null.
   *  Keepalives do not count: they prove the daemon is alive, not that the
   *  turn moved. */
  lastEventAt: number | null;
  /** Drive one chat turn. Accumulates tokens into `text`, upserts tool frames
   *  into `tools`, and resolves with the authoritative reply. Throws an
   *  ApiError on an `error` frame (matching the non-streaming POST /chat path). */
  run: (
    body: unknown,
    onToken?: (delta: string, full: string) => void,
  ) => Promise<ChatStreamResult>;
  /** Abort the in-flight turn (resolves `run` with whatever streamed so far). */
  abort: () => void;
}

/**
 * Drive a single streaming chat turn against `POST /chat/stream`. One turn at a
 * time: a new `run` (or `abort`) tears down any prior AbortController.
 */
/** See {@link UseChatStream.phase}. */
export type TurnPhase = "preparing" | "working";

function carriesFiles(body: unknown): boolean {
  const atts = (body as { attachments?: unknown } | null)?.attachments;
  return Array.isArray(atts) && atts.length > 0;
}

export function useChatStream(): UseChatStream {
  const [streaming, setStreaming] = useState(false);
  const [text, setText] = useState("");
  const [tools, setTools] = useState<ToolCard[]>([]);
  const [approval, setApproval] = useState<PendingApproval | null>(null);
  const [phase, setPhase] = useState<TurnPhase | null>(null);
  const [withFiles, setWithFiles] = useState(false);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const abort = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
    // A card left up after its stream died would collect a click the daemon
    // can only 404 — the pending entry is popped when the wait ends.
    setApproval(null);
  }, []);

  const run = useCallback(
    async (
      body: unknown,
      onToken?: (delta: string, full: string) => void,
    ): Promise<ChatStreamResult> => {
      abortRef.current?.abort(); // tear down any prior turn
      const controller = new AbortController();
      abortRef.current = controller;
      setStreaming(true);
      setText("");
      setTools([]);
      setApproval(null);
      const t0 = Date.now();
      setPhase("preparing");
      setWithFiles(carriesFiles(body));
      setStartedAt(t0);
      setLastEventAt(t0);
      const watch: StreamWatch = {
        onOpen: () => {
          if (abortRef.current !== controller) return;
          setPhase("working");
          setLastEventAt(Date.now());
        },
      };

      let acc = "";
      let done: ChatStreamResult | null = null;
      let provider: string | undefined;
      let model: string | undefined;
      // Did the server do real work for this turn (streamed a token or ran a
      // tool)? If so, a non-streaming re-POST on failure would re-execute it.
      let committed = false;

      try {
        for await (const ev of streamSSE(
          "/chat/stream",
          body,
          controller.signal,
          watch,
        )) {
          // A real frame moved the turn (keepalives never reach here).
          if (ev.type !== "done" && ev.type !== "error") setLastEventAt(Date.now());
          switch (ev.type) {
            case "token":
              committed = true;
              acc += ev.text;
              setText(acc);
              onToken?.(ev.text, acc);
              break;
            case "tool_call":
              committed = true;
              setTools((prev) => upsertTool(prev, ev));
              break;
            case "approval":
              // The turn is PAUSED server-side; render the card. Counts as
              // committed work — the model has already chosen this call, so a
              // silent re-POST would replay the turn.
              committed = true;
              setApproval({
                id: ev.id,
                callId: ev.call_id,
                tool: ev.tool,
                args: ev.args,
                timeoutS: ev.timeout_s,
              });
              break;
            case "approval_resolved":
              // Clear only the card this frame answers — a stale resolution
              // must not eat a NEWER question.
              setApproval((prev) => (prev && prev.id === ev.id ? null : prev));
              break;
            case "meta":
              provider = ev.provider;
              model = ev.model;
              break;
            case "done":
              done = {
                reply: ev.reply || acc,
                tools_used: ev.tools_used,
                deniedTools: ev.denied_tools,
                doors: ev.doors,
                adapted: ev.adapted,
                route: ev.route,
                usage: ev.usage,
                provider: ev.provider ?? provider,
                model: ev.model ?? model,
                documents: ev.documents,
                escalate: ev.escalate,
                escalateReason: ev.escalate_reason,
                escalateAgent: ev.escalate_agent,
                workflowDraft: ev.workflow_draft,
                workflowRun: ev.workflow_run,
                context: ev.context,
              };
              break;
            case "error":
              // Honest failure — surface it exactly like a failed POST /chat,
              // carrying whether the turn already committed server-side work (so
              // the caller never silently re-runs it) and whether it was offline.
              throw new StreamError(
                ev.detail,
                ev.status ?? 0,
                committed,
                ev.offline ?? false,
                acc,
              );
            default:
              break; // round / unknown — nothing to accumulate
          }
        }
      } finally {
        // A NEWER turn owns the status line — this one must not blank it.
        const superseded =
          abortRef.current !== null && abortRef.current !== controller;
        if (abortRef.current === controller) abortRef.current = null;
        setStreaming(false);
        // A turn that ends however it ends leaves no live question behind.
        setApproval(null);
        if (!superseded) {
          setPhase(null);
          setStartedAt(null);
          setLastEventAt(null);
        }
      }

      // done.reply is authoritative; fall back to the accumulated text if the
      // stream dropped (or was aborted) before a `done` frame arrived.
      return done ?? { reply: acc, provider, model };
    },
    [],
  );

  return {
    streaming,
    text,
    tools,
    approval,
    phase,
    withFiles,
    startedAt,
    lastEventAt,
    run,
    abort,
  };
}
