"use client";

// Use a ready-made prompt an installed app offers (v1.324.0, MCP prompts).
// The user fills the prompt's blanks, presses Use, and the filled text is put
// in the COMPOSER through `onInsert` — never sent by itself; the user reads it
// and sends it (product rule 4). The daemon scans the text first: a `flagged`
// result had something blocked and the form says so.

import { useState, type FormEvent, type KeyboardEvent } from "react";
import { Button, LoaderInline } from "@/components/ui";
import { errorStatus, getPackPrompt, type PackPrompt } from "@/lib/mcpInteract";

/** One plain sentence for a failed fetch. */
export function promptErrorWords(e: unknown, pack: string): string {
  const who = pack || "the app";
  const status = errorStatus(e);
  if (status !== null && e instanceof Error) {
    if (status === 0) return "Jarvis is not reachable right now. Try again in a moment.";
    if (status === 404) return `That prompt is no longer offered by ${who}.`;
    const msg = (e.message || "").trim();
    if (msg && msg !== "[object Object]" && !/^\d{3}\b/.test(msg)) return msg;
  }
  return `Could not get that prompt from ${who}. Try again.`;
}

function stop(e: KeyboardEvent) {
  e.stopPropagation();
}

export function PackPromptForm({
  prompt,
  onInsert,
}: {
  prompt: PackPrompt;
  onInsert: (text: string) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [missing, setMissing] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flagged, setFlagged] = useState(false);

  async function use(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const need = new Set(
      prompt.arguments.filter((a) => a.required && !(values[a.name] ?? "").trim()).map((a) => a.name),
    );
    setMissing(need);
    if (need.size) return;
    const args: Record<string, string> = {};
    for (const a of prompt.arguments) {
      const v = values[a.name] ?? "";
      if (v.trim()) args[a.name] = v;
    }
    setBusy(true);
    setError(null);
    setFlagged(false);
    try {
      const res = await getPackPrompt(prompt.pack, prompt.name, args);
      if (!res.text.trim()) {
        setError(`${prompt.pack || "The app"} sent back an empty prompt.`);
        return;
      }
      setFlagged(res.flagged);
      onInsert(res.text);
    } catch (err) {
      setError(promptErrorWords(err, prompt.pack));
    } finally {
      setBusy(false);
    }
  }

  const title = prompt.title || prompt.name;
  return (
    <form
      data-testid="pack-prompt-form"
      onSubmit={(e) => void use(e)}
      onKeyDown={stop}
      noValidate
      aria-label={`Use the prompt ${title}`}
      className="space-y-2 rounded-xl border border-white/[0.08] bg-white/[0.02] p-3"
    >
      <div>
        <p className="text-sm font-medium text-zinc-100">{title}</p>
        <p className="text-[11px] text-zinc-500">From {prompt.pack}</p>
      </div>
      {prompt.description && (
        <p className="whitespace-pre-wrap break-words text-xs text-zinc-400">{prompt.description}</p>
      )}
      {prompt.arguments.map((a) => {
        const id = `pp-${prompt.pack}-${prompt.name}-${a.name}`;
        const isMissing = missing.has(a.name);
        return (
          <div key={a.name} className="space-y-1">
            <label htmlFor={id} className="block text-xs font-medium text-zinc-300">
              {a.title || a.name}
              {a.required && <span className="text-zinc-500"> (required)</span>}
            </label>
            <input
              id={id}
              type="text"
              autoComplete="off"
              aria-required={a.required || undefined}
              aria-invalid={isMissing || undefined}
              value={values[a.name] ?? ""}
              onChange={(e) => {
                const v = e.target.value;
                setValues((prev) => ({ ...prev, [a.name]: v }));
                if (isMissing)
                  setMissing((prev) => {
                    const next = new Set(prev);
                    next.delete(a.name);
                    return next;
                  });
              }}
              className="field text-sm"
            />
            {a.description && <p className="text-[11px] text-zinc-500">{a.description}</p>}
            {isMissing && (
              <p role="alert" className="text-[11px] text-tone-danger">
                Please fill this in.
              </p>
            )}
          </div>
        );
      })}
      {flagged && (
        <p data-testid="pack-prompt-flagged" role="status" className="text-[11px] text-tone-warn">
          Part of this prompt looked unsafe, so it was left out. Read it before you send.
        </p>
      )}
      {error && (
        <p role="alert" className="text-[11px] text-tone-danger">
          {error}
        </p>
      )}
      <div className="flex justify-end">
        <Button type="submit" variant="primary" size="sm" disabled={busy}>
          {busy ? <LoaderInline label="Getting it…" /> : "Use"}
        </Button>
      </div>
    </form>
  );
}
