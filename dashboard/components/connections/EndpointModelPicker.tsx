"use client";

/**
 * v1.328.0 (calm chat B6): the custom-endpoint form's Model field with a
 * "Fetch available models" link.
 *
 * The link asks the daemon (`POST /connections/endpoints/models`) to ask the
 * server the user typed for its own model list, then opens a searchable list
 * under the field: type to filter, arrow keys move, Enter picks, Escape
 * closes. Picking fills the field; typing in the field still works and is
 * never overwritten by a fetch. Nothing is probed until the link is pressed
 * (the key goes to that server only when the user asks).
 *
 * Saving is not this component's job: it only edits the `value` the form
 * already owns.
 */

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { post, ApiError } from "@/lib/api";

/** What the daemon answers (routes/connections.py `endpoint_models_probe`). */
interface ProbeAnswer {
  models?: string[];
  error?: string | null;
  reason?: string | null;
}

/** Every word of `query` appears in `id` (case folded). Order kept. */
export function filterModels(models: string[], query: string): string[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return models;
  return models.filter((m) => {
    const id = m.toLowerCase();
    return words.every((w) => id.includes(w));
  });
}

/** The sentence a failed request shows (the daemon's own words win). */
function requestWords(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 0) return "Iron Jarvis is not answering right now. Try again in a moment.";
    if (err.status === 404) return "This version of Iron Jarvis cannot fetch models yet. Type the model id yourself.";
    return err.message;
  }
  return "Could not ask that server for its models. Type the model id yourself.";
}

export function EndpointModelPicker({
  baseUrl,
  apiKey,
  value,
  onChange,
}: {
  baseUrl: string;
  apiKey: string;
  value: string;
  onChange: (model: string) => void;
}) {
  const uid = useId();
  const inputId = `${uid}-model`;
  const listId = `${uid}-list`;
  const optId = (i: number) => `${uid}-opt-${i}`;

  const [models, setModels] = useState<string[] | null>(null);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const modelRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  // A fetch that lands after the address or key changed is for a different
  // server: the generation drops it.
  const gen = useRef(0);

  useEffect(() => {
    gen.current += 1;
    setModels(null);
    setOpen(false);
    setError(null);
    setLoading(false);
  }, [baseUrl, apiKey]);

  const shown = useMemo(() => (models ? filterModels(models, query) : []), [models, query]);

  async function fetchModels() {
    const url = baseUrl.trim();
    if (!/^https?:\/\/\S+/i.test(url)) {
      setError("Enter the endpoint address first, starting with http:// or https://.");
      return;
    }
    const mine = ++gen.current;
    setLoading(true);
    setError(null);
    try {
      const res = await post<ProbeAnswer>("/connections/endpoints/models", {
        base_url: url,
        api_key: apiKey.trim(),
        protocol: "openai",
      });
      if (mine !== gen.current) return;
      const list = Array.isArray(res.models) ? res.models.filter((m) => typeof m === "string") : [];
      if (res.error || list.length === 0) {
        setModels(null);
        setOpen(false);
        setError(res.error || "The server answered but lists no models. Type the model id yourself.");
        return;
      }
      setModels(list);
      setQuery("");
      const at = list.indexOf(value.trim());
      setActive(at >= 0 ? at : 0);
      setOpen(true);
    } catch (err) {
      if (mine !== gen.current) return;
      setModels(null);
      setOpen(false);
      setError(requestWords(err));
    } finally {
      if (mine === gen.current) setLoading(false);
    }
  }

  function pick(model: string) {
    onChange(model);
    setOpen(false);
    modelRef.current?.focus();
  }

  function close() {
    setOpen(false);
    modelRef.current?.focus();
  }

  function move(to: number) {
    if (shown.length === 0) return;
    const next = Math.max(0, Math.min(shown.length - 1, to));
    setActive(next);
    const el = document.getElementById(optId(next));
    el?.scrollIntoView?.({ block: "nearest" });
  }

  function onFilterKey(e: React.KeyboardEvent<HTMLInputElement>) {
    // Every key handled here stays here: Enter must not save the form and
    // Escape must not close whatever holds it.
    if (e.key === "ArrowDown") {
      e.preventDefault();
      move(active + 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      move(active - 1);
    } else if (e.key === "Home") {
      e.preventDefault();
      move(0);
    } else if (e.key === "End") {
      e.preventDefault();
      move(shown.length - 1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      e.stopPropagation();
      const hit = shown[active];
      if (hit) pick(hit);
      else if (query.trim()) pick(query.trim());
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close();
    }
  }

  const count = models?.length ?? 0;

  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-0.5">
        <label htmlFor={inputId} className="text-[11px] font-medium text-zinc-400">
          Model
        </label>
        <button
          type="button"
          id="endpoint-fetch-models"
          data-testid="endpoint-fetch-models"
          onClick={() => void fetchModels()}
          disabled={loading}
          className="text-[11px] text-accent-soft underline-offset-2 transition-colors hover:text-accent hover:underline disabled:cursor-wait disabled:text-zinc-500 disabled:no-underline"
        >
          {loading ? "Fetching models…" : "Fetch available models"}
        </button>
      </div>
      <input
        ref={modelRef}
        id={inputId}
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="e.g. glm-4.7-flash / llama3"
        autoComplete="off"
        spellCheck={false}
        className="field font-mono text-xs"
      />
      {error && (
        <p data-testid="endpoint-models-error" role="status" className="text-[11px] leading-relaxed text-tone-warn">
          {error}
        </p>
      )}
      {open && models && (
        <div
          ref={panelRef}
          data-testid="endpoint-model-picker"
          onBlur={(e) => {
            // Leaving the list for anywhere outside it closes it; moving
            // inside it (the filter, an option) does not.
            const next = e.relatedTarget as Node | null;
            if (!next || !panelRef.current?.contains(next)) setOpen(false);
          }}
          className="space-y-1 rounded-xl border border-white/[0.08] bg-ink-900/60 p-1.5"
        >
          <input
            type="text"
            role="combobox"
            aria-label="Search the models"
            aria-expanded="true"
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={shown[active] ? optId(active) : undefined}
            data-testid="endpoint-model-filter"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            onKeyDown={onFilterKey}
            placeholder={`Search ${count} model${count === 1 ? "" : "s"}`}
            autoComplete="off"
            spellCheck={false}
            autoFocus
            className="w-full rounded-lg border border-white/[0.06] bg-transparent px-2 py-1 font-mono text-xs text-zinc-200 outline-none placeholder:text-zinc-500 focus:border-accent/40"
          />
          <ul
            id={listId}
            role="listbox"
            aria-label="Models on this server"
            className="max-h-56 overflow-y-auto overscroll-contain"
          >
            {shown.map((m, i) => (
              <li
                key={m}
                id={optId(i)}
                role="option"
                aria-selected={i === active}
                data-active={i === active ? "true" : undefined}
                onMouseDown={(e) => e.preventDefault()}
                onMouseEnter={() => setActive(i)}
                onClick={() => pick(m)}
                className={`flex cursor-pointer items-center justify-between gap-2 rounded-md px-2 py-1 font-mono text-xs break-all transition-colors ${
                  i === active ? "bg-white/[0.06] text-zinc-100" : "text-zinc-400"
                }`}
              >
                <span className="min-w-0">{m}</span>
                {m === value.trim() && <span className="shrink-0 font-sans text-[11px] text-zinc-500">current</span>}
              </li>
            ))}
          </ul>
          {shown.length === 0 && (
            <p className="px-2 py-1 text-[11px] text-zinc-500">
              No model matches. Press Enter to use &ldquo;{query.trim()}&rdquo; as typed.
            </p>
          )}
          <p className="px-2 text-[11px] text-zinc-500">
            {shown.length === count
              ? `${count} model${count === 1 ? "" : "s"} on this server`
              : `${shown.length} of ${count} models`}
          </p>
        </div>
      )}
    </div>
  );
}
