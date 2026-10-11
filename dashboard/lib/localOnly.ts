/**
 * "Local only" chats (v1.332.0): the dashboard's friendly front door.
 *
 * A chat switched to Local only sends nothing to a model that runs off the
 * user's own network unless the user says yes for that one message. The
 * DAEMON enforces it (every model call of the turn runs under its scope and
 * a cloud call is refused with `code: "local_only"`); this file only lets the
 * page ask BEFORE a send instead of after a refusal, and read the refusal
 * when one comes back.
 *
 * Pure helpers, no React. Kept out of lib/api.ts (most page tests mock that
 * module wholesale) and out of lib/useModels.ts (another lane owns it).
 *
 * WHICH MODEL A MESSAGE WILL USE: the one-turn choice ("Try again with…"),
 * else the chat's pick, else the project's default, else the app's default
 * (/health). A model asks first when its catalog row is not `kind: "local"`.
 * A model the catalog does not list asks too (fail closed: the daemon treats a
 * model it cannot place as cloud). Two cases never ask here, and the daemon
 * still guards both: no model known yet (nothing to name; the daemon picks
 * and refuses by name) and Auto (the daemon keeps Auto on this network while
 * the chat is local only).
 */

import { answeredModelName, type CatalogRow } from "@/lib/answeredModel";
import { providerDisplay } from "@/lib/onboarding";

/** The user's last choice, which a NEW chat starts with. */
export const LOCAL_ONLY_KEY = "ij.chat.localOnly";

/** The composer chip's tooltip, on and off. */
export const LOCAL_ONLY_TITLE_ON = "Nothing in this chat leaves this PC without asking you.";
export const LOCAL_ONLY_TITLE_OFF = "Keep this chat on this PC.";

/** The ⋯ menu's checkable row. */
export const LOCAL_ONLY_MENU_ROW = "Keep this chat on this PC";

/** The one quiet line at the top of the model menu while the chat is local only. */
export const LOCAL_ONLY_MODEL_MENU_LINE = "Local only · cloud models ask first";

/** The ask-first card. */
export const CLOUD_ASK_TITLE = "This chat is local only";
export const CLOUD_ASK_SEND = "Send once";
export const CLOUD_ASK_KEEP = "Keep it local";
export function cloudAskBody(name: string): string {
  return `Send this message to ${name}? It will leave this PC.`;
}

/** The receipt's words for a reply the user let leave this PC once. */
export const SENT_WITH_OK = "sent with your OK";

/** The last value the user chose (false when storage is unreadable). */
export function readLocalOnlyDefault(): boolean {
  try {
    return window.localStorage.getItem(LOCAL_ONLY_KEY) === "1";
  } catch {
    return false;
  }
}

/** Remember the user's choice for the next new chat. Best effort. */
export function writeLocalOnlyDefault(on: boolean): void {
  try {
    window.localStorage.setItem(LOCAL_ONLY_KEY, on ? "1" : "0");
  } catch {
    /* storage unavailable: this chat still has it */
  }
}

/** A catalog row as far as this file reads it (lib/types ModelOption). */
export interface LocalOnlyRow extends CatalogRow {
  kind?: string;
  available?: boolean;
}

/** "provider::model" → its two halves ("" each when it is not that shape). */
function halves(choice: string | null | undefined): { provider: string; model: string } {
  const c = choice ?? "";
  const i = c.indexOf("::");
  if (i === -1) return { provider: "", model: "" };
  const provider = c.slice(0, i);
  const model = c.slice(i + 2);
  return provider && model ? { provider, model } : { provider: "", model: "" };
}

/** The provider and model this message will use, by the order above. */
export function turnModel(opts: {
  turnChoice?: string | null;
  choice?: string | null;
  projectProvider?: string | null;
  projectModel?: string | null;
  defaultProvider?: string | null;
  defaultModel?: string | null;
}): { provider: string; model: string } {
  for (const c of [opts.turnChoice, opts.choice]) {
    const h = halves(c);
    if (h.provider) return h;
  }
  // The daemon fills a missing pick from the project the turn is grounded in
  // (provider, and its model when it names one) before the app's default.
  const projectProvider = (opts.projectProvider ?? "").trim();
  if (projectProvider) {
    return { provider: projectProvider, model: (opts.projectModel ?? "").trim() };
  }
  return { provider: (opts.defaultProvider ?? "").trim(), model: (opts.defaultModel ?? "").trim() };
}

/** The name the page already uses for a model (the catalog label, else the
 *  name the id spells), else the provider's plain name. */
export function cloudModelName(
  provider: string,
  model: string,
  models: readonly CatalogRow[],
): string {
  const named = model ? answeredModelName({ provider, model }, models) : undefined;
  return named || providerDisplay(provider) || provider || "this model";
}

/** What the ask-first card needs: the provider to allow and the words. */
export interface CloudAsk {
  provider: string;
  model: string;
  name: string;
}

/** Null when the message may go as it is; otherwise the model to ask about.
 *  Only call it while the chat is local only. */
export function cloudAskFor(
  provider: string,
  model: string,
  models: readonly LocalOnlyRow[],
): CloudAsk | null {
  const p = (provider || "").trim();
  if (!p || p === "auto") return null;
  const row =
    models.find((m) => m.provider === p && m.model === model) ?? models.find((m) => m.provider === p);
  if (row?.kind === "local") return null;
  return { provider: p, model, name: cloudModelName(p, model, models) };
}

/** A model for a refused provider when the refusal named none: the first
 *  connected row the catalog lists for it ("" when there is none). */
export function firstModelFor(provider: string, models: readonly LocalOnlyRow[]): string {
  const row = models.find((m) => m.provider === provider && m.available !== false);
  return row?.model ?? "";
}

/** The daemon's refusal: its own sentence plus the provider and model it did
 *  not send to. */
export interface LocalOnlyRefusal {
  detail: string;
  provider: string;
  model: string;
}

function textOf(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/** Read a refusal off a failed turn: a stream `error` frame (StreamError
 *  carries `code`/`provider`/`model`) or an HTTP 409 (ApiError carries the
 *  JSON `body`). Anything else, or a refusal that names no provider, is null:
 *  the page then shows the error as it does every other one. */
export function localOnlyRefusalFrom(e: unknown): LocalOnlyRefusal | null {
  if (!e || typeof e !== "object") return null;
  const err = e as { code?: unknown; provider?: unknown; model?: unknown; message?: unknown; body?: unknown };
  const body = err.body && typeof err.body === "object" ? (err.body as Record<string, unknown>) : null;
  const src: Record<string, unknown> | null =
    textOf(err.code) === "local_only"
      ? (err as Record<string, unknown>)
      : body && textOf(body.code) === "local_only"
        ? body
        : null;
  if (!src) return null;
  const provider = textOf(src.provider).trim();
  if (!provider) return null;
  return {
    detail: textOf(body?.detail) || textOf(err.message),
    provider,
    model: textOf(src.model).trim(),
  };
}
