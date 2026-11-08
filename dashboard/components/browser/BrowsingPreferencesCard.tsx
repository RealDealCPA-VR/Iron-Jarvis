"use client";

/**
 * v1.454.0: "How Jarvis works on web pages" — the owner's "a preference of
 * interaction on a page in the settings. Things like look for primary sources
 * or speak in a concise only need to know manner."
 *
 * The control for the `browsing_preferences` setting (settings/schema.py). It
 * saves through the one settings writer (PUT /settings/values), so a change made
 * here sits in the ledger beside one made in chat, with its Undo. The daemon adds
 * the text to every turn that has a browser tool — the sidebar's and the chat
 * page's — before the history planner, scanned and capped
 * (chat_turn.browsing_preferences_block). Empty adds nothing.
 */
import { useState } from "react";
import { Globe } from "lucide-react";
import { ApiError, put } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { Button, Card } from "@/components/ui";
import { LoadFailedLine } from "@/components/LoadFailedLine";

/** The setting's key; pinned equal to settings/schema.py. */
export const BROWSING_PREFERENCES_KEY = "browsing_preferences";

/** The most characters it may hold; pinned equal to core/config.py. */
export const BROWSING_PREFERENCES_MAX = 2000;

/** One-press examples: each adds its line to the box (never saves by itself). */
export const BROWSING_EXAMPLES: readonly string[] = [
  "Look for primary sources",
  "Answer concisely, only what I need to know",
  "Say where each fact came from",
];

interface Values {
  values?: Record<string, unknown>;
}

/** The box's text with `line` added on a line of its own, unless it is already there. */
export function withLine(text: string, line: string): string {
  const lines = text.split("\n").map((l) => l.trim());
  if (lines.includes(line)) return text;
  const base = text.replace(/\s+$/, "");
  return base ? `${base}\n${line}` : line;
}

export function BrowsingPreferencesCard() {
  const { data, error, loading, reload } = useApi<Values>("/settings/values");
  const saved = typeof data?.values?.[BROWSING_PREFERENCES_KEY] === "string"
    ? (data.values[BROWSING_PREFERENCES_KEY] as string)
    : "";
  const [draft, setDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState("");

  // An untouched box shows what is saved (`draft` null); words being typed are
  // kept until Save.
  const text = draft ?? saved;
  const changed = draft !== null && draft !== saved;
  const tooLong = text.length > BROWSING_PREFERENCES_MAX;

  async function save() {
    if (!changed || tooLong || busy) return;
    setBusy(true);
    setNote("");
    setProblem("");
    try {
      await put("/settings/values", { values: { [BROWSING_PREFERENCES_KEY]: text.trim() } });
      setDraft(null);
      setNote(text.trim() ? "Saved. Jarvis follows these on web pages from your next message." : "Cleared. Nothing extra is added now.");
      reload();
    } catch (err) {
      setProblem(
        err instanceof ApiError && err.message
          ? err.message
          : "Couldn't save your browsing preferences. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="How Jarvis works on web pages" icon={<Globe className="h-4 w-4" />}>
      <div className="space-y-3" data-testid="browsing-preferences">
        <p className="text-[13px] leading-relaxed text-zinc-400">
          Your own standing instructions for when Jarvis reads or acts on a web page, in the
          browser sidebar or from chat. One per line. Leave it empty to add nothing.
        </p>
        {error && !data ? (
          <LoadFailedLine thing="Your browsing preferences" error={error} onRetry={reload} />
        ) : (
          <>
            <label htmlFor="browsing-preferences-box" className="sr-only">
              How Jarvis works on web pages
            </label>
            <textarea
              id="browsing-preferences-box"
              data-testid="browsing-preferences-box"
              value={text}
              rows={4}
              disabled={loading && !data}
              placeholder={"Look for primary sources\nAnswer concisely, only what I need to know"}
              onChange={(e) => {
                setDraft(e.target.value);
                setNote("");
                setProblem("");
              }}
              className="field w-full text-[13px]"
            />
            <div className="flex flex-wrap items-center gap-2">
              {BROWSING_EXAMPLES.map((line) => (
                <button
                  key={line}
                  type="button"
                  className="rounded-full border border-white/10 px-2.5 py-1 text-[12px] text-zinc-300 hover:border-accent/50"
                  onClick={() => setDraft(withLine(text, line))}
                  title="Adds this line to the box. Save keeps it."
                >
                  + {line}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Button
                variant="primary"
                size="sm"
                data-testid="browsing-preferences-save"
                onClick={() => void save()}
                disabled={!changed || tooLong || busy}
              >
                {busy ? "Saving…" : "Save"}
              </Button>
              <span
                className={`text-[12px] ${tooLong ? "text-tone-danger" : "text-zinc-500"}`}
                data-testid="browsing-preferences-count"
              >
                {text.length} / {BROWSING_PREFERENCES_MAX}
                {tooLong ? " — too long to save" : ""}
              </span>
            </div>
            <p role="status" className="text-[12px] text-zinc-400" data-testid="browsing-preferences-note">
              {note}
            </p>
            {problem ? (
              <p role="alert" className="text-[12px] text-tone-danger" data-testid="browsing-preferences-problem">
                {problem}
              </p>
            ) : null}
          </>
        )}
      </div>
    </Card>
  );
}
