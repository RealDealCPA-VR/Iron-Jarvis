"use client";

// An installed app asks the user something mid-turn (v1.324.0, MCP
// elicitation). The daemon holds the app's tool call until this card answers
// through POST /chat/mcp/elicitations/{id} (the page passes `onAnswer`, which
// wraps lib/mcpInteract's `answerElicitation`), the user stops the turn, or
// the stream ends.
//
// RULES THIS CARD KEEPS:
//   - The app's message is PLAIN TEXT. It is the app's words, not ours, and
//     it never renders as markdown or HTML.
//   - It runs the SAME checks the daemon runs before it sends, so a mistake is
//     shown under its field at once; the daemon's own answer (a 400 with
//     per-field errors) lands under the same fields.
//   - Keys typed inside never reach the composer or the page's shortcuts —
//     keydown stops here.
//   - It asks for nothing secret: the quiet line says never to type a password.
//   - After an answer it says what happened in words and disables itself.

import { useMemo, useState, type FormEvent, type KeyboardEvent } from "react";
import { MessageCircleQuestion } from "lucide-react";
import { Button, LoaderInline } from "@/components/ui";
import {
  checkElicitationAnswer,
  outcomeWords,
  type AnswerResult,
  type McpElicitationAsk,
  type McpField,
  type McpOutcome,
} from "@/lib/mcpInteract";

type Action = "accept" | "decline" | "cancel";
type Value = string | boolean;

/** The <input type> a string field's format asks for. */
function inputType(f: McpField): string {
  switch (f.format) {
    case "email":
      return "email";
    case "uri":
      return "url";
    case "date":
      return "date";
    case "date-time":
      return "datetime-local";
    default:
      return "text";
  }
}

function initialValue(f: McpField): Value {
  if (f.type === "boolean") return f.default === true;
  if (f.enum) {
    const i = f.default === undefined ? -1 : f.enum.findIndex((x) => x === f.default);
    return i === -1 ? "" : String(i);
  }
  return f.default === undefined || typeof f.default === "boolean" ? "" : String(f.default);
}

/** A datetime-local value ("2026-10-08T14:30") as a full ISO time with its
 *  zone, which every date-time reader accepts. Anything else passes as typed. */
function isoDateTime(v: string): string {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(v)) return v;
  const t = new Date(v);
  return Number.isNaN(t.getTime()) ? v : t.toISOString();
}

/** The answer the form holds, as the app's types — empty optional fields are
 *  left out. Coercion happens HERE (an <input> only ever holds text). */
export function contentFrom(fields: McpField[], values: Record<string, Value>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    const v = values[f.name];
    if (f.type === "boolean") {
      out[f.name] = v === true;
      continue;
    }
    if (typeof v !== "string" || v === "") continue;
    if (f.enum) {
      const i = Number(v);
      if (Number.isInteger(i) && i >= 0 && i < f.enum.length) out[f.name] = f.enum[i];
      continue;
    }
    if (f.type === "number" || f.type === "integer") {
      const n = Number(v);
      out[f.name] = Number.isFinite(n) ? n : v;
      continue;
    }
    out[f.name] = f.format === "date-time" ? isoDateTime(v) : v;
  }
  return out;
}

function stop(e: KeyboardEvent) {
  e.stopPropagation();
}

export function ElicitationCard({
  ask,
  onAnswer,
}: {
  ask: McpElicitationAsk;
  onAnswer: (action: Action, content?: Record<string, unknown>) => Promise<AnswerResult>;
}) {
  const [values, setValues] = useState<Record<string, Value>>(() => {
    const v: Record<string, Value> = {};
    for (const f of ask.fields) v[f.name] = initialValue(f);
    return v;
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Action | "">("");
  const [answered, setAnswered] = useState<McpOutcome | null>(null);
  const known = useMemo(() => new Set(ask.fields.map((f) => f.name)), [ask.fields]);

  // The daemon's word (mcp_resolved, or the turn ending) wins over ours.
  const outcome: McpOutcome | null = ask.outcome ?? answered;
  const closed = outcome !== null;
  const who = ask.pack || "An app";

  async function answer(action: Action) {
    if (busy || closed) return;
    setError(null);
    let content: Record<string, unknown> | undefined;
    if (action === "accept") {
      content = contentFrom(ask.fields, values);
      const problems = checkElicitationAnswer(ask.fields, content);
      if (Object.keys(problems).length) {
        setErrors(problems);
        return;
      }
    }
    setErrors({});
    setBusy(action);
    let res: AnswerResult;
    try {
      res = await onAnswer(action, content);
    } catch (e) {
      res = { ok: false, error: e instanceof Error ? e.message : String(e) };
    }
    setBusy("");
    if (res.ok) {
      setAnswered(action);
      return;
    }
    if (res.errors && Object.keys(res.errors).length) {
      const mine: Record<string, string> = {};
      const other: string[] = [];
      for (const [k, v] of Object.entries(res.errors)) {
        if (known.has(k)) mine[k] = v;
        else other.push(v);
      }
      setErrors(mine);
      if (other.length) setError(other.join(" "));
      return;
    }
    setError(res.error || "That did not go through. Try again.");
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void answer("accept");
  }

  function set(name: string, v: Value) {
    setValues((prev) => ({ ...prev, [name]: v }));
    if (errors[name]) setErrors((prev) => {
      const next = { ...prev };
      delete next[name];
      return next;
    });
  }

  return (
    <form
      role={closed ? "group" : "alertdialog"}
      aria-label={`${who} is asking`}
      data-testid="mcp-elicitation-card"
      data-outcome={outcome ?? ""}
      onSubmit={onSubmit}
      onKeyDown={stop}
      noValidate
      className="space-y-2.5 rounded-xl border border-accent/25 bg-accent/[0.06] p-3"
    >
      <div className="flex items-center gap-2">
        <MessageCircleQuestion size={15} className="shrink-0 text-accent-soft" aria-hidden="true" />
        <p className="text-sm font-medium text-zinc-100">{who} is asking</p>
      </div>
      {ask.message && (
        <p
          data-testid="mcp-elicitation-message"
          className="whitespace-pre-wrap break-words text-sm leading-relaxed text-zinc-200"
        >
          {ask.message}
        </p>
      )}

      <fieldset disabled={closed || !!busy} className="space-y-2.5">
        {ask.fields.map((f) => {
          const id = `mcp-${ask.id}-${f.name}`;
          const err = errors[f.name];
          const errId = `${id}-error`;
          const label = (
            <>
              {f.title}
              {f.required && <span className="text-zinc-500"> (required)</span>}
            </>
          );
          const common = {
            id,
            name: f.name,
            "aria-required": f.required || undefined,
            "aria-invalid": err ? true : undefined,
            "aria-describedby": err ? errId : undefined,
          };
          let control;
          if (f.type === "boolean") {
            control = (
              <label htmlFor={id} className="flex items-center gap-2 text-sm text-zinc-200">
                <input
                  {...common}
                  type="checkbox"
                  checked={values[f.name] === true}
                  onChange={(e) => set(f.name, e.target.checked)}
                  className="h-4 w-4 accent-[rgb(var(--accent-rgb))]"
                />
                <span>{label}</span>
              </label>
            );
          } else {
            const v = typeof values[f.name] === "string" ? (values[f.name] as string) : "";
            const input = f.enum ? (
              <select
                {...common}
                value={v}
                onChange={(e) => set(f.name, e.target.value)}
                className="field text-sm"
              >
                <option value="">Choose…</option>
                {f.enum.map((opt, i) => (
                  <option key={i} value={String(i)}>
                    {f.enum_names?.[i] ?? String(opt)}
                  </option>
                ))}
              </select>
            ) : f.type === "number" || f.type === "integer" ? (
              <input
                {...common}
                type="number"
                inputMode={f.type === "integer" ? "numeric" : "decimal"}
                step={f.type === "integer" ? 1 : "any"}
                min={f.minimum}
                max={f.maximum}
                value={v}
                onChange={(e) => set(f.name, e.target.value)}
                className="field text-sm"
              />
            ) : (
              <input
                {...common}
                type={inputType(f)}
                autoComplete="off"
                minLength={f.min_length}
                maxLength={f.max_length}
                value={v}
                onChange={(e) => set(f.name, e.target.value)}
                className="field text-sm"
              />
            );
            control = (
              <>
                <label htmlFor={id} className="block text-xs font-medium text-zinc-300">
                  {label}
                </label>
                {input}
              </>
            );
          }
          return (
            <div key={f.name} className="space-y-1" data-testid={`mcp-field-${f.name}`}>
              {control}
              {f.description && <p className="text-[11px] text-zinc-500">{f.description}</p>}
              {err && (
                <p id={errId} role="alert" className="text-[11px] text-tone-danger">
                  {err}
                </p>
              )}
            </div>
          );
        })}
      </fieldset>

      <p className="text-[11px] text-zinc-500">
        Only answer if you trust this app — never type a password here.
      </p>

      {error && (
        <p role="alert" className="text-[11px] text-tone-danger">
          {error}
        </p>
      )}

      {closed ? (
        <p data-testid="mcp-elicitation-outcome" className="text-xs font-medium text-zinc-300">
          {outcomeWords(outcome, ask.pack)}
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" size="sm" disabled={!!busy}>
            {busy === "accept" ? <LoaderInline label="Sending…" /> : "Send"}
          </Button>
          <Button variant="secondary" size="sm" onClick={() => void answer("decline")} disabled={!!busy}>
            {busy === "decline" ? <LoaderInline label="Declining…" /> : "Decline"}
          </Button>
          <Button variant="secondary" size="sm" onClick={() => void answer("cancel")} disabled={!!busy}>
            {busy === "cancel" ? <LoaderInline label="Skipping…" /> : "Not now"}
          </Button>
        </div>
      )}
    </form>
  );
}
