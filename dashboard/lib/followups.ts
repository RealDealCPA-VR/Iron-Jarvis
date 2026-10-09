// Suggested follow-ups for the newest reply (v1.323.0) — `POST
// /chat/followups`, behind the `chat_followups` setting (off by default).
//
// Its OWN module, never lib/api.ts: ~71 test files mock api.ts wholesale, and
// a helper living there would vanish from every one of them (CLAUDE.md,
// v1.250.0). This file only USES `post`, so a test that mocks api.ts mocks
// the transport underneath it — which is exactly what it should.
//
// It is a nicety: it NEVER throws. Any failure — offline, a 404 from an
// older daemon, junk in the body — is "no suggestions".

import { post } from "@/lib/api";

/** One message as the endpoint takes it. */
export interface FollowupMessage {
  role: string;
  content: string;
}

/** Only the tail of the conversation goes — follow-ups are about the end. */
export const FOLLOWUP_MAX_MESSAGES = 6;
/** Each message is cut to this many characters before it leaves. */
export const FOLLOWUP_MAX_CHARS = 4000;
/** At most this many suggestions come back to the page. */
export const FOLLOWUP_MAX_SUGGESTIONS = 3;
/** A suggestion longer than this is not a chip; it is cut. */
const FOLLOWUP_MAX_SUGGESTION_CHARS = 300;

/** The request body: the last six usable messages, each cut to 4000 chars.
 *  Pure — exported for the tests. */
export function followupBody(
  messages: readonly FollowupMessage[],
  provider?: string,
  model?: string,
): { messages: FollowupMessage[]; provider?: string; model?: string } {
  const usable = (Array.isArray(messages) ? messages : []).filter(
    (m): m is FollowupMessage =>
      !!m &&
      typeof m === "object" &&
      typeof m.role === "string" &&
      typeof m.content === "string" &&
      m.content.trim().length > 0,
  );
  const body: { messages: FollowupMessage[]; provider?: string; model?: string } = {
    messages: usable
      .slice(-FOLLOWUP_MAX_MESSAGES)
      .map((m) => ({ role: m.role, content: m.content.slice(0, FOLLOWUP_MAX_CHARS) })),
  };
  if (provider) body.provider = provider;
  if (model) body.model = model;
  return body;
}

/** The suggestions in a response body: strings only, trimmed, non-empty,
 *  de-duplicated, at most three. Anything else is []. Pure. */
export function decodeFollowups(raw: unknown): string[] {
  const list = (raw as { suggestions?: unknown } | null | undefined)?.suggestions;
  if (!Array.isArray(list)) return [];
  const out: string[] = [];
  for (const s of list) {
    if (typeof s !== "string") continue;
    const t = s.trim().slice(0, FOLLOWUP_MAX_SUGGESTION_CHARS);
    if (!t || out.includes(t)) continue;
    out.push(t);
    if (out.length >= FOLLOWUP_MAX_SUGGESTIONS) break;
  }
  return out;
}

/**
 * Ask the daemon for up to three follow-ups to the conversation so far.
 * Resolves [] on ANY failure and never rejects. `signal` lets the page drop a
 * stale ask when the next turn starts.
 */
export async function fetchFollowups(
  messages: readonly FollowupMessage[],
  provider?: string,
  model?: string,
  opts: { signal?: AbortSignal } = {},
): Promise<string[]> {
  try {
    const body = followupBody(messages, provider, model);
    if (!body.messages.length) return [];
    const res = await post<unknown>("/chat/followups", body, {
      timeoutMs: 30_000,
      ...(opts.signal ? { signal: opts.signal } : {}),
    });
    return decodeFollowups(res);
  } catch {
    return [];
  }
}
