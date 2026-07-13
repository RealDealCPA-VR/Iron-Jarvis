"use client";

// SSE consumer for token-by-token CHAT streaming (FX-01). The daemon's
// streaming chat endpoint emits Server-Sent Events on the wire schema:
//
//   token     {"text":"…"}
//   tool_call {"id","name","status":"started"|"finished","ok"?,"args"?,"output"?}
//   meta      {"provider","model"}
//   round     {"round":n}
//   done      {"reply","provider","model","tools_used","denied_tools","usage"}
//   error     {"detail","status"?}
//
// with a ": keepalive" comment every ~15s of idle. This library turns that raw
// byte stream into typed events (`streamSSE`) and drives one chat turn from a
// React component (`useChatStream`). It is purely additive — the non-streaming
// POST /chat path is untouched.

import { useCallback, useRef, useState } from "react";
import { API_BASE, ApiError, ijToken } from "./api";

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
  | { type: "meta"; provider: string; model: string }
  | { type: "round"; round: number }
  | {
      type: "done";
      reply: string;
      provider?: string;
      model?: string;
      tools_used?: string[];
      denied_tools?: string[];
      usage?: { input_tokens?: number; output_tokens?: number };
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
  provider?: string;
  model?: string;
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
      if (Array.isArray(data.tools_used)) ev.tools_used = data.tools_used as string[];
      if (Array.isArray(data.denied_tools))
        ev.denied_tools = data.denied_tools as string[];
      if (data.usage && typeof data.usage === "object")
        ev.usage = data.usage as { input_tokens?: number; output_tokens?: number };
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

/**
 * POST `body` to an SSE endpoint and yield each decoded frame. The token rides
 * in an Authorization header (fetch, unlike EventSource, can set one). Aborts
 * are swallowed (the generator simply ends); a non-2xx response or a network
 * failure yields a single `error` event with the parsed detail.
 */
export async function* streamSSE(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const token = ijToken();
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
      signal,
    });
  } catch (e) {
    if (isAbort(e)) return;
    // A genuine transport failure (daemon unreachable) — flagged `offline` so the
    // caller can distinguish it from an in-band provider `error` frame (which also
    // carries status 0 but is NOT an offline condition).
    yield { type: "error", detail: "daemon offline", status: 0, offline: true };
    return;
  }

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const parsed = (await res.json()) as { detail?: unknown };
      if (parsed?.detail) detail = String(parsed.detail);
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
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
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
  constructor(message: string, status: number, committed: boolean, offline: boolean) {
    super(message, status);
    this.name = "StreamError";
    this.committed = committed;
    this.offline = offline;
  }
}

export interface UseChatStream {
  /** True while a turn is in flight. */
  streaming: boolean;
  /** Accumulated assistant text so far this turn. */
  text: string;
  /** Live tool cards for this turn, keyed by call id. */
  tools: ToolCard[];
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
export function useChatStream(): UseChatStream {
  const [streaming, setStreaming] = useState(false);
  const [text, setText] = useState("");
  const [tools, setTools] = useState<ToolCard[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  const abort = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
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

      let acc = "";
      let done: ChatStreamResult | null = null;
      let provider: string | undefined;
      let model: string | undefined;
      // Did the server do real work for this turn (streamed a token or ran a
      // tool)? If so, a non-streaming re-POST on failure would re-execute it.
      let committed = false;

      try {
        for await (const ev of streamSSE("/chat/stream", body, controller.signal)) {
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
            case "meta":
              provider = ev.provider;
              model = ev.model;
              break;
            case "done":
              done = {
                reply: ev.reply || acc,
                tools_used: ev.tools_used,
                provider: ev.provider ?? provider,
                model: ev.model ?? model,
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
              );
            default:
              break; // round / unknown — nothing to accumulate
          }
        }
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
        setStreaming(false);
      }

      // done.reply is authoritative; fall back to the accumulated text if the
      // stream dropped (or was aborted) before a `done` frame arrived.
      return done ?? { reply: acc, provider, model };
    },
    [],
  );

  return { streaming, text, tools, run, abort };
}
