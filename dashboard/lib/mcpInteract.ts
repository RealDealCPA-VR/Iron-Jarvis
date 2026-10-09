// Apps that talk back (v1.324.0) — the client half of MCP elicitation,
// sampling, progress, prompts and resources. "Pack" is the daemon's word for
// an MCP server the user installed; the screen calls it an "app".
//
// Everything here is PURE or a thin fetcher, and lives OUTSIDE lib/api.ts and
// lib/useChatStream.ts on purpose: ~71 test files mock `@/lib/api` wholesale
// and ~50 mock `@/lib/useChatStream` with a fixed export list, so a decoder in
// either would vanish under those mocks (CLAUDE.md, v1.250.0).
//
// Every decoder WHITELISTS: a field the daemon did not promise, or one of the
// wrong type, dies here — the same rule as the stream's other frames, so a
// newer daemon's extra key can never reach the screen half-understood.

import { API_BASE, get, ijToken, post } from "@/lib/api";

// ------------------------------------------------------------------- types

export type McpFieldType = "string" | "number" | "integer" | "boolean";
export type McpFieldFormat = "email" | "uri" | "date" | "date-time";

/** One question in an app's form (the daemon's normalised requestedSchema). */
export interface McpField {
  name: string;
  type: McpFieldType;
  title: string;
  description: string;
  required: boolean;
  enum?: (string | number)[];
  /** Labels for `enum`, same length (dropped otherwise). */
  enum_names?: string[];
  format?: McpFieldFormat;
  minimum?: number;
  maximum?: number;
  min_length?: number;
  max_length?: number;
  default?: string | number | boolean;
}

/** How an ask ended. Elicitation: accept | decline | cancel; sampling:
 *  approved | denied; either: stopped (the turn ended first). */
export type McpOutcome = "accept" | "decline" | "cancel" | "approved" | "denied" | "stopped";

export interface McpElicitationAsk {
  kind: "elicitation";
  id: string;
  callId: string;
  pack: string;
  message: string;
  fields: McpField[];
  /** Set once the daemon says the ask ended (mcp_resolved, or the turn did). */
  outcome?: McpOutcome;
}

export interface McpMessage {
  role: "user" | "assistant";
  text: string;
}

export interface McpSamplingAsk {
  kind: "sampling";
  id: string;
  callId: string;
  pack: string;
  system: string;
  messages: McpMessage[];
  /** Messages the daemon left out of the card (beyond the first 20). */
  more: number;
  maxTokens: number | null;
  /** The provider that would answer (the turn's own), and its model id. */
  model: string;
  modelId: string;
  outcome?: McpOutcome;
}

export type McpAsk = McpElicitationAsk | McpSamplingAsk;

/** A running tool's progress, folded onto its tool card. */
export interface McpProgress {
  progress: number;
  total: number | null;
  message: string;
}

export interface McpProgressFrame extends McpProgress {
  callId: string;
  pack: string;
}

export interface McpResolvedFrame {
  id: string;
  kind: "elicitation" | "sampling";
  outcome: McpOutcome;
}

export interface PackPromptArgument {
  name: string;
  title: string;
  description: string;
  required: boolean;
}

export interface PackPrompt {
  pack: string;
  name: string;
  title: string;
  description: string;
  arguments: PackPromptArgument[];
}

export interface PackFailure {
  pack: string;
  error: string;
}

export interface PackPromptResult {
  text: string;
  messages: McpMessage[];
  /** Something in the prompt was blocked by the safety scan. */
  flagged: boolean;
}

export interface PackResource {
  pack: string;
  uri: string;
  name: string;
  title: string;
  description: string;
  mime_type: string;
}

/** One attached app resource as the turn read it (the done frame's
 *  `resources` receipt): `ok` false with a one-sentence `note` on failure. */
export interface ResourceReceipt {
  pack: string;
  uri: string;
  ok: boolean;
  note: string;
}

/** What answering an app's question came back with. */
export interface AnswerResult {
  ok: boolean;
  /** The daemon's per-field problems (the ask stays open). */
  errors?: Record<string, string>;
  /** One plain sentence when it failed for another reason. */
  error?: string;
}

// ----------------------------------------------------------------- limits

export const MAX_MESSAGE_CHARS = 2_000;
export const MAX_SYSTEM_CHARS = 2_000;
export const MAX_SAMPLE_TEXT_CHARS = 4_000;
export const MAX_SAMPLE_MESSAGES = 20;
export const MAX_FIELDS = 20;
/** The hook keeps at most this many asks per turn (pending ones never drop). */
export const MAX_ASKS = 20;

const FIELD_NAME = /^[A-Za-z0-9_.-]{1,64}$/;
const FIELD_TYPES: readonly McpFieldType[] = ["string", "number", "integer", "boolean"];
const FORMATS: readonly McpFieldFormat[] = ["email", "uri", "date", "date-time"];
const OUTCOMES: readonly McpOutcome[] = [
  "accept",
  "decline",
  "cancel",
  "approved",
  "denied",
  "stopped",
];

// ---------------------------------------------------------------- helpers

function rec(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function text(v: unknown, max?: number): string {
  if (typeof v !== "string") return "";
  return max !== undefined && v.length > max ? v.slice(0, max) : v;
}

function finite(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

function count(v: unknown): number | undefined {
  return finite(v) && Number.isInteger(v) && v >= 0 ? v : undefined;
}

// --------------------------------------------------------------- decoders

/** One form field, or null when it cannot be shown honestly. */
export function decodeField(raw: unknown): McpField | null {
  const d = rec(raw);
  if (!d) return null;
  const name = d.name;
  if (typeof name !== "string" || !FIELD_NAME.test(name)) return null;
  const type = d.type;
  if (typeof type !== "string" || !(FIELD_TYPES as readonly string[]).includes(type)) return null;
  const f: McpField = {
    name,
    type: type as McpFieldType,
    title: text(d.title, 200) || name,
    description: text(d.description, 1_000),
    required: d.required === true,
  };
  if (Array.isArray(d.enum)) {
    const vals = d.enum.filter(
      (x): x is string | number => typeof x === "string" || finite(x),
    );
    if (vals.length && vals.length === d.enum.length) {
      f.enum = vals;
      if (
        Array.isArray(d.enum_names) &&
        d.enum_names.length === vals.length &&
        d.enum_names.every((x) => typeof x === "string")
      )
        f.enum_names = d.enum_names as string[];
    }
  }
  if (f.type === "string" && typeof d.format === "string" && (FORMATS as readonly string[]).includes(d.format))
    f.format = d.format as McpFieldFormat;
  if ((f.type === "number" || f.type === "integer") && finite(d.minimum)) f.minimum = d.minimum;
  if ((f.type === "number" || f.type === "integer") && finite(d.maximum)) f.maximum = d.maximum;
  if (f.type === "string") {
    const lo = count(d.min_length);
    const hi = count(d.max_length);
    if (lo !== undefined) f.min_length = lo;
    if (hi !== undefined) f.max_length = hi;
  }
  const dv = d.default;
  if (f.type === "boolean" ? typeof dv === "boolean" : f.type === "string" ? typeof dv === "string" : finite(dv))
    f.default = dv as string | number | boolean;
  return f;
}

/** `mcp_elicitation` → an ask, or null without an id. */
export function decodeElicitation(raw: unknown): McpElicitationAsk | null {
  const d = rec(raw);
  if (!d) return null;
  const id = text(d.id);
  if (!id) return null;
  const fields: McpField[] = [];
  const seen = new Set<string>();
  if (Array.isArray(d.fields)) {
    for (const r of d.fields) {
      const f = decodeField(r);
      if (!f || seen.has(f.name)) continue;
      seen.add(f.name);
      fields.push(f);
      if (fields.length >= MAX_FIELDS) break;
    }
  }
  return {
    kind: "elicitation",
    id,
    callId: text(d.call_id),
    pack: text(d.pack, 200),
    message: text(d.message, MAX_MESSAGE_CHARS),
    fields,
  };
}

/** `mcp_sampling` → an ask, or null without an id. */
export function decodeSampling(raw: unknown): McpSamplingAsk | null {
  const d = rec(raw);
  if (!d) return null;
  const id = text(d.id);
  if (!id) return null;
  const messages: McpMessage[] = [];
  let dropped = 0;
  if (Array.isArray(d.messages)) {
    for (const r of d.messages) {
      const m = rec(r);
      if (!m || (m.role !== "user" && m.role !== "assistant") || typeof m.text !== "string") continue;
      if (messages.length >= MAX_SAMPLE_MESSAGES) {
        dropped += 1;
        continue;
      }
      messages.push({ role: m.role, text: text(m.text, MAX_SAMPLE_TEXT_CHARS) });
    }
  }
  return {
    kind: "sampling",
    id,
    callId: text(d.call_id),
    pack: text(d.pack, 200),
    system: text(d.system, MAX_SYSTEM_CHARS),
    messages,
    more: (count(d.more) ?? 0) + dropped,
    maxTokens: finite(d.max_tokens) && d.max_tokens > 0 ? Math.floor(d.max_tokens) : null,
    model: text(d.model, 100),
    modelId: text(d.model_id, 200),
  };
}

/** `mcp_progress` → a frame, or null without a call id or a numeric progress. */
export function decodeProgress(raw: unknown): McpProgressFrame | null {
  const d = rec(raw);
  if (!d) return null;
  const callId = text(d.call_id);
  if (!callId || !finite(d.progress)) return null;
  return {
    callId,
    pack: text(d.pack, 200),
    progress: d.progress,
    total: finite(d.total) ? d.total : null,
    message: text(d.message, 500),
  };
}

/** `mcp_resolved` → a frame, or null without an id. An outcome this client
 *  does not know reads as "stopped" — the card closes, never stays live. */
export function decodeResolved(raw: unknown): McpResolvedFrame | null {
  const d = rec(raw);
  if (!d) return null;
  const id = text(d.id);
  if (!id) return null;
  const o = d.outcome;
  return {
    id,
    kind: d.kind === "sampling" ? "sampling" : "elicitation",
    outcome:
      typeof o === "string" && (OUTCOMES as readonly string[]).includes(o)
        ? (o as McpOutcome)
        : "stopped",
  };
}

// ---------------------------------------------------------- hook reducers

/** Add (or replace, by id) an ask in the live list. */
export function upsertAsk(prev: McpAsk[], ask: McpAsk): McpAsk[] {
  const idx = prev.findIndex((a) => a.id === ask.id);
  let next: McpAsk[];
  if (idx === -1) next = [...prev, ask];
  else {
    next = prev.slice();
    next[idx] = ask;
  }
  // Bounded: the oldest ANSWERED asks go first; an open question never drops.
  while (next.length > MAX_ASKS) {
    const old = next.findIndex((a) => a.outcome !== undefined);
    if (old === -1) break;
    next.splice(old, 1);
  }
  return next;
}

/** Record how an ask ended (the daemon's word wins over a local guess). */
export function resolveAsk(prev: McpAsk[], fr: McpResolvedFrame): McpAsk[] {
  const idx = prev.findIndex((a) => a.id === fr.id);
  if (idx === -1) return prev;
  const next = prev.slice();
  next[idx] = { ...next[idx], outcome: fr.outcome };
  return next;
}

/** The turn ended: every still-open ask among `ids` is "stopped". */
export function settleAsks(prev: McpAsk[], ids: ReadonlySet<string>): McpAsk[] {
  if (!prev.some((a) => a.outcome === undefined && ids.has(a.id))) return prev;
  return prev.map((a) =>
    a.outcome === undefined && ids.has(a.id) ? { ...a, outcome: "stopped" as const } : a,
  );
}

/** Fold a progress frame onto the tool card with the same call id. A frame
 *  for a call with no card is dropped (never invents a card). */
export function foldToolProgress<T extends { id: string; progress?: McpProgress }>(
  cards: T[],
  fr: McpProgressFrame,
): T[] {
  const idx = cards.findIndex((c) => c.id === fr.callId);
  if (idx === -1) return cards;
  const next = cards.slice();
  next[idx] = {
    ...next[idx],
    progress: { progress: fr.progress, total: fr.total, message: fr.message },
  };
  return next;
}

// ------------------------------------------------------------- the checks

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const SCHEME = /^[A-Za-z][A-Za-z0-9+.-]*:\S+$/;
const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const DATE_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

function validDate(v: string): boolean {
  const m = DATE.exec(v);
  if (!m) return false;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const da = Number(m[3]);
  const dt = new Date(Date.UTC(y, mo - 1, da));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === mo - 1 && dt.getUTCDate() === da;
}

function enumHas(values: (string | number)[], v: unknown): boolean {
  return values.some((x) => x === v);
}

/** The same checks the daemon runs before it passes an answer on: one plain
 *  sentence per field with a problem; {} = fine. Coerces nothing. */
export function checkElicitationAnswer(
  fields: McpField[],
  content: Record<string, unknown>,
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const f of fields) {
    const v = content[f.name];
    if (v === undefined || v === null || v === "") {
      if (f.required) errors[f.name] = f.enum ? "Please pick one." : "Please fill this in.";
      continue;
    }
    if (f.enum) {
      if (!enumHas(f.enum, v)) errors[f.name] = "Please pick one of the choices.";
      continue;
    }
    if (f.type === "boolean") {
      if (typeof v !== "boolean") errors[f.name] = "This must be yes or no.";
      continue;
    }
    if (f.type === "number" || f.type === "integer") {
      if (!finite(v)) {
        errors[f.name] = f.type === "integer" ? "This must be a whole number." : "This must be a number.";
      } else if (f.type === "integer" && !Number.isInteger(v)) {
        errors[f.name] = "This must be a whole number.";
      } else if (f.minimum !== undefined && v < f.minimum) {
        errors[f.name] = `This must be ${f.minimum} or more.`;
      } else if (f.maximum !== undefined && v > f.maximum) {
        errors[f.name] = `This must be ${f.maximum} or less.`;
      }
      continue;
    }
    // string
    if (typeof v !== "string") {
      errors[f.name] = "This must be text.";
      continue;
    }
    if (f.min_length !== undefined && v.length < f.min_length) {
      errors[f.name] = `Use at least ${f.min_length} characters.`;
      continue;
    }
    if (f.max_length !== undefined && v.length > f.max_length) {
      errors[f.name] = `Use ${f.max_length} characters or fewer.`;
      continue;
    }
    if (f.format === "email" && !EMAIL.test(v))
      errors[f.name] = "This must be an email address, like name@example.com.";
    else if (f.format === "uri" && !SCHEME.test(v))
      errors[f.name] = "This must be a web address, like https://example.com.";
    else if (f.format === "date" && !validDate(v)) errors[f.name] = "This must be a date.";
    else if (f.format === "date-time" && (!DATE_TIME.test(v) || Number.isNaN(Date.parse(v))))
      errors[f.name] = "This must be a date and time.";
  }
  return errors;
}

/** The words a finished ask shows, from the user's side. */
export function outcomeWords(outcome: McpOutcome, pack: string): string {
  const who = pack || "The app";
  switch (outcome) {
    case "accept":
      return `Sent. ${who} has your answer.`;
    case "decline":
      return `You said no. ${who} was told.`;
    case "cancel":
      return `Skipped for now. ${who} was told you did not answer.`;
    case "approved":
      return `You allowed it. The answer goes to ${who}.`;
    case "denied":
      return `You said no. ${who} was told.`;
    case "stopped":
    default:
      return "This question ended before it was answered.";
  }
}

// ---------------------------------------------------------- list decoders

export function decodePackPrompts(raw: unknown): { prompts: PackPrompt[]; failed: PackFailure[] } {
  const d = rec(raw) ?? {};
  const prompts: PackPrompt[] = [];
  if (Array.isArray(d.prompts)) {
    for (const r of d.prompts) {
      const p = rec(r);
      if (!p) continue;
      const pack = text(p.pack, 200);
      const name = text(p.name, 200);
      if (!pack || !name) continue;
      const args: PackPromptArgument[] = [];
      if (Array.isArray(p.arguments)) {
        for (const a of p.arguments) {
          const ar = rec(a);
          const an = ar ? text(ar.name, 100) : "";
          if (!ar || !an || args.some((x) => x.name === an)) continue;
          args.push({
            name: an,
            title: text(ar.title, 200),
            description: text(ar.description, 1_000),
            required: ar.required === true,
          });
        }
      }
      prompts.push({
        pack,
        name,
        title: text(p.title, 200),
        description: text(p.description, 1_000),
        arguments: args,
      });
    }
  }
  return { prompts, failed: decodeFailed(d.failed) };
}

function decodeFailed(raw: unknown): PackFailure[] {
  if (!Array.isArray(raw)) return [];
  const out: PackFailure[] = [];
  for (const r of raw) {
    const f = rec(r);
    const pack = f ? text(f.pack, 200) : "";
    if (f && pack) out.push({ pack, error: text(f.error, 500) });
  }
  return out;
}

export function decodePromptResult(raw: unknown): PackPromptResult {
  const d = rec(raw) ?? {};
  const messages: McpMessage[] = [];
  if (Array.isArray(d.messages)) {
    for (const r of d.messages) {
      const m = rec(r);
      if (m && (m.role === "user" || m.role === "assistant") && typeof m.text === "string")
        messages.push({ role: m.role, text: m.text });
    }
  }
  return { text: text(d.text), messages, flagged: d.flagged === true };
}

/** The done frame's `resources` receipt (≤ 8 on the wire). Rows without a
 *  pack and a uri die; `ok` is a real true only. Non-array → []. */
export function decodeResourceReceipts(raw: unknown): ResourceReceipt[] {
  if (!Array.isArray(raw)) return [];
  const out: ResourceReceipt[] = [];
  for (const r of raw) {
    const x = rec(r);
    if (!x) continue;
    const pack = text(x.pack, 200);
    const uri = text(x.uri, 2_000);
    if (!pack || !uri) continue;
    out.push({ pack, uri, ok: x.ok === true, note: text(x.note, 500) });
    if (out.length >= 8) break;
  }
  return out;
}

export const MAX_RESOURCES = 200;

export function decodePackResources(raw: unknown): { resources: PackResource[]; failed: PackFailure[] } {
  const d = rec(raw) ?? {};
  const resources: PackResource[] = [];
  if (Array.isArray(d.resources)) {
    for (const r of d.resources) {
      const x = rec(r);
      if (!x) continue;
      const pack = text(x.pack, 200);
      const uri = text(x.uri, 2_000);
      if (!pack || !uri) continue;
      resources.push({
        pack,
        uri,
        name: text(x.name, 200),
        title: text(x.title, 200),
        description: text(x.description, 1_000),
        mime_type: text(x.mime_type, 100),
      });
      if (resources.length >= MAX_RESOURCES) break;
    }
  }
  return { resources, failed: decodeFailed(d.failed) };
}

// --------------------------------------------------------------- fetchers

/** The HTTP status an `api()` failure carries (ApiError.status), or null.
 *  Duck-typed on purpose: ~71 test files mock `@/lib/api` wholesale, and an
 *  `instanceof ApiError` against a mock without that export would throw. */
export function errorStatus(e: unknown): number | null {
  const s = e && typeof e === "object" ? (e as { status?: unknown }).status : undefined;
  return typeof s === "number" ? s : null;
}

/** The prompts every installed app offers. An older daemon (404) has none. */
export async function fetchPackPrompts(): Promise<{ prompts: PackPrompt[]; failed: PackFailure[] }> {
  try {
    return decodePackPrompts(await get<unknown>("/mcp/prompts"));
  } catch (e) {
    if (errorStatus(e) === 404) return { prompts: [], failed: [] };
    throw e;
  }
}

/** One prompt's text, filled with the user's arguments. Throws ApiError. */
export async function getPackPrompt(
  pack: string,
  name: string,
  args: Record<string, string>,
): Promise<PackPromptResult> {
  return decodePromptResult(await post<unknown>("/mcp/prompts/get", { pack, name, arguments: args }));
}

/** The things installed apps can attach, filtered by `q`. Older daemon = none. */
export async function fetchPackResources(
  q?: string,
): Promise<{ resources: PackResource[]; failed: PackFailure[] }> {
  const query = q && q.trim() ? `?q=${encodeURIComponent(q.trim())}` : "";
  try {
    return decodePackResources(await get<unknown>(`/mcp/resources${query}`));
  } catch (e) {
    if (errorStatus(e) === 404) return { resources: [], failed: [] };
    throw e;
  }
}

function fieldErrors(detail: unknown): Record<string, string> | null {
  const d = rec(detail);
  const errs = d ? rec(d.errors) : null;
  if (!errs) return null;
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(errs)) if (typeof v === "string" && v) out[k] = v;
  return Object.keys(out).length ? out : null;
}

/**
 * Answer an app's question. A 400 carrying `{detail: {errors}}` comes back as
 * per-field `errors` (the question stays open); anything else that fails is
 * one sentence in `error`. Never throws.
 *
 * Raw fetch, not `post`: `api()` flattens an OBJECT detail to a string
 * ("[object Object]"), so the per-field errors would be lost on the way.
 */
export async function answerElicitation(
  id: string,
  action: "accept" | "decline" | "cancel",
  content?: Record<string, unknown>,
): Promise<AnswerResult> {
  let res: Response;
  try {
    const token = ijToken();
    res = await fetch(`${API_BASE}/chat/mcp/elicitations/${encodeURIComponent(id)}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(action === "accept" ? { action, content: content ?? {} } : { action }),
    });
  } catch {
    return { ok: false, error: "Jarvis is not reachable right now. Try again in a moment." };
  }
  if (res.ok) return { ok: true };
  let detail: unknown = null;
  try {
    detail = ((await res.json()) as { detail?: unknown } | null)?.detail ?? null;
  } catch {
    /* not JSON */
  }
  if (res.status === 400) {
    const errors = fieldErrors(detail);
    if (errors) return { ok: false, errors };
  }
  if (res.status === 404) return { ok: false, error: "This question already ended." };
  return {
    ok: false,
    error: typeof detail === "string" && detail ? detail : "That did not go through. Try again.",
  };
}

/** Allow or refuse an app's request to ask the model. true = recorded. */
export async function decideSampling(id: string, decision: "approve" | "deny"): Promise<boolean> {
  try {
    await post(`/chat/mcp/sampling/${encodeURIComponent(id)}`, { decision });
    return true;
  } catch {
    return false;
  }
}
