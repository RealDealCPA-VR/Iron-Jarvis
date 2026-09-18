/**
 * v1.277.0 — the last few models picked in chat, newest first.
 *
 * The composer's model menu is a two-level tree (provider → model) over a
 * catalog that is 49 rows on the daily driver. The pick a person makes most is
 * the one they made last time, so the menu opens on the last three picks and a
 * typed filter finds the rest. Stored per browser in localStorage, read only
 * when the menu opens, never on the page's render path (the page must not
 * subscribe to anything per keystroke — v1.250.0 S-05).
 *
 * A value is the composer's own `provider::model` string; "" (the default
 * model) is never remembered, because the menu already opens on it.
 */

export const RECENT_MODELS_KEY = "ij_chat_model_recent";
export const RECENT_MODELS_MAX = 3;

/** The remembered picks, newest first; empty when nothing was ever picked. */
export function readRecentModels(): string[] {
  try {
    const raw = window.localStorage.getItem(RECENT_MODELS_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((x): x is string => typeof x === "string" && x.includes("::"))
      .slice(0, RECENT_MODELS_MAX);
  } catch {
    return [];
  }
}

/** Put `choice` first (once), keep the bound, and return the new list. */
export function rememberRecentModel(choice: string): string[] {
  if (!choice || !choice.includes("::")) return readRecentModels();
  const next = [choice, ...readRecentModels().filter((x) => x !== choice)].slice(
    0,
    RECENT_MODELS_MAX,
  );
  try {
    window.localStorage.setItem(RECENT_MODELS_KEY, JSON.stringify(next));
  } catch {
    /* a browser that refuses storage still gets the list for this menu */
  }
  return next;
}

/** Rows whose provider, model id or display name contain `query` (case-insensitive). */
export function matchModels<T extends { provider: string; model: string; name?: string }>(
  rows: readonly T[],
  query: string,
  max: number,
): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const out: T[] = [];
  for (const m of rows) {
    if (`${m.name ?? ""} ${m.provider} ${m.model}`.toLowerCase().includes(q)) {
      out.push(m);
      if (out.length >= max) break;
    }
  }
  return out;
}
