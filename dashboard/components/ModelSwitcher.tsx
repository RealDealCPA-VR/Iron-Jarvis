"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Cpu, Check, PlugZap, ChevronDown } from "lucide-react";
import { usePolledApi, useApi } from "@/lib/useApi";
import { put, ApiError } from "@/lib/api";
import type { Health, ModelOption } from "@/lib/types";

/** Quality tiers, plainly labelled so users pick outcome over model IDs. */
type Tier = "fast" | "balanced" | "best";
const TIER_ORDER: Tier[] = ["fast", "balanced", "best"];
const TIER_LABELS: Record<Tier, string> = {
  fast: "Fast",
  balanced: "Balanced",
  best: "Best",
};

/**
 * Per-provider quality → model id. The dial sets the default model for the
 * CURRENT default provider to the chosen tier's model. Providers NOT listed
 * here (ollama / custom / unknown) expose a single configured model, so the
 * dial is hidden for them and only the full model list applies.
 */
const TIERS: Record<string, Record<Tier, string>> = {
  anthropic: {
    fast: "claude-haiku-4-5",
    balanced: "claude-sonnet-4-6",
    best: "claude-opus-4-8",
  },
  openai: {
    fast: "gpt-4o-mini",
    balanced: "gpt-4o",
    // ChatGPT-account backend id (gpt-5-codex was retired there); the adapter
    // self-heals to whatever the backend serves if this is retired too.
    best: "gpt-5.5",
  },
  google: {
    fast: "gemini-1.5-flash",
    balanced: "gemini-2.0-flash",
    best: "gemini-1.5-pro",
  },
  xai: {
    fast: "grok-code-fast-1",
    balanced: "grok-4-1-fast",
    best: "grok-4",
  },
  // OpenRouter routes automatically — every tier is the same auto endpoint.
  openrouter: {
    fast: "openrouter/auto",
    balanced: "openrouter/auto",
    best: "openrouter/auto",
  },
};

/**
 * Topbar provider/model switcher — set the ACTIVE default model in one click,
 * across every connected account (beyond the per-session dropdown). Reuses
 * /health (current default + availability) + /models (catalog) and persists the
 * choice via PUT /settings. Opens on the global `ij:open-switcher` event (the
 * ⌘K palette dispatches it).
 */
export function ModelSwitcher() {
  const health = usePolledApi<Health>("/health", 5000);
  const modelsData = useApi<{ models: ModelOption[] }>("/models");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const h = health.data;
  const models = useMemo(() => modelsData.data?.models ?? [], [modelsData.data]);
  const avail = useMemo(() => {
    const m = new Map<string, boolean>();
    for (const p of h?.providers ?? []) m.set(p.provider, p.available);
    return m;
  }, [h]);

  // Optimistic selection: reflect a click INSTANTLY (the /health poll is every
  // 5s, so without this the button label + highlights look inert for seconds —
  // which reads as "the switcher does nothing"). Reconciled once /health agrees.
  const [optimistic, setOptimistic] = useState<{ provider: string; model: string } | null>(
    null,
  );
  const activeProvider = optimistic?.provider ?? h?.default_provider;
  const activeModel = optimistic?.model ?? h?.default_model;
  useEffect(() => {
    if (
      optimistic &&
      h?.default_provider === optimistic.provider &&
      h?.default_model === optimistic.model
    ) {
      setOptimistic(null); // server caught up
    }
  }, [h?.default_provider, h?.default_model, optimistic]);

  // Quality dial: the tier map + which of the current provider's models the
  // catalog actually offers (so an unavailable tier is greyed like the list).
  const tiers = activeProvider ? TIERS[activeProvider] : undefined;
  const providerModels = useMemo(() => {
    const s = new Set<string>();
    for (const m of models) if (m.provider === activeProvider) s.add(m.model);
    return s;
  }, [models, activeProvider]);

  // Open via a global event so ⌘K can summon it from anywhere.
  useEffect(() => {
    const onOpen = () => setOpen(true);
    window.addEventListener("ij:open-switcher", onOpen);
    return () => window.removeEventListener("ij:open-switcher", onOpen);
  }, []);

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function choose(m: ModelOption) {
    const key = `${m.provider}|${m.model}`;
    setBusy(key);
    setErr(null);
    setOptimistic({ provider: m.provider, model: m.model }); // instant feedback
    try {
      await put("/settings", {
        values: { default_provider: m.provider, default_model: m.model },
      });
      await health.reload?.();
      setOpen(false);
    } catch (e) {
      setOptimistic(null); // revert the label on failure
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (!h) return null; // until /health loads (the offline banner covers downtime)

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-xs text-zinc-300 transition-colors hover:border-white/20"
        title="Switch the active model"
        aria-label="Switch the active model"
      >
        <Cpu size={13} className="text-accent-soft" />
        <span className="hidden max-w-[150px] truncate font-mono text-[11px] sm:inline">
          {activeModel}
        </span>
        <ChevronDown size={12} className="text-zinc-500" />
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-1.5 w-72 rounded-xl border border-white/10 bg-ink-950/95 p-1.5 shadow-card-hover backdrop-blur-xl">
          {tiers && (
            <div className="mb-1 border-b border-white/[0.06] px-1 pb-2">
              <div className="px-1 py-1.5 text-[10px] uppercase tracking-wider text-zinc-400">
                Quality
              </div>
              <div className="flex gap-0.5 rounded-lg border border-white/10 bg-white/[0.03] p-0.5">
                {TIER_ORDER.map((tier) => {
                  const model = tiers[tier];
                  const active = model === activeModel;
                  const ok = providerModels.has(model);
                  const key = `${activeProvider}|${model}`;
                  return (
                    <button
                      key={tier}
                      onClick={() =>
                        ok &&
                        activeProvider &&
                        choose({ provider: activeProvider, model })
                      }
                      disabled={!ok || busy === key}
                      title={ok ? model : `${model} · not available`}
                      className={`flex-1 rounded-md px-2 py-1 text-center text-[11px] font-medium transition-colors ${
                        active
                          ? "bg-accent/[0.18] text-accent-soft"
                          : ok
                            ? "text-zinc-300 hover:bg-white/[0.06]"
                            : "cursor-not-allowed text-zinc-600 opacity-50"
                      }`}
                    >
                      {TIER_LABELS[tier]}
                      {!ok && (
                        <span className="ml-1 text-[9px] text-zinc-600">n/a</span>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
          <div className="px-2 py-1.5 text-[10px] uppercase tracking-wider text-zinc-400">
            Active model
          </div>
          <div className="max-h-80 overflow-y-auto">
            {models.length === 0 ? (
              <div className="px-2 py-2 text-[11px] text-zinc-500">No models.</div>
            ) : (
              models.map((m) => {
                const active =
                  m.provider === activeProvider && m.model === activeModel;
                const ok = avail.get(m.provider) ?? false;
                const key = `${m.provider}|${m.model}`;
                return (
                  <button
                    key={key}
                    onClick={() => ok && choose(m)}
                    disabled={!ok || busy === key}
                    className={`flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-xs transition-colors ${
                      ok ? "hover:bg-white/[0.06]" : "cursor-not-allowed opacity-40"
                    } ${active ? "bg-accent/[0.1]" : ""}`}
                  >
                    <span className="min-w-0">
                      <span className="block truncate font-mono text-[11px] text-zinc-200">
                        {m.model}
                      </span>
                      <span className="text-[10px] text-zinc-500">
                        {m.provider}
                        {!ok && " · not connected"}
                      </span>
                    </span>
                    {active && <Check size={13} className="text-accent-soft" />}
                  </button>
                );
              })
            )}
          </div>
          {err && <div className="px-2 py-1.5 text-[11px] text-rose-300">{err}</div>}
          <Link
            href="/connections"
            onClick={() => setOpen(false)}
            className="mt-1 flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[11px] text-accent-soft transition-colors hover:bg-white/[0.04]"
          >
            <PlugZap size={12} /> Connect another account…
          </Link>
        </div>
      )}
    </div>
  );
}
