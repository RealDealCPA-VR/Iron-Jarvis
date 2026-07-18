"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Cpu, Check, PlugZap, ChevronDown, Sparkles } from "lucide-react";
import { usePolledApi, useApi } from "@/lib/useApi";
import { put, post, ApiError } from "@/lib/api";
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

/* ---- Auto (smart routing) ------------------------------------------------- */
/** A connected/routing model, matching the daemon's `{provider, model}` shape. */
type PM = { provider: string; model: string };

/** The `/routing` view the daemon returns (and echoes from enable/disable). */
interface RoutingView {
  enabled: boolean; // default_provider === "auto"
  routing_model: string; // "provider:model" | ""
  connected: PM[];
  suggested: PM | null; // the cheapest connected model
  tiers: { light?: PM; standard?: PM; heavy?: PM };
}

/** Difficulty tiers Auto routes into, cheapest → most capable. */
const ROUTE_TIERS = ["light", "standard", "heavy"] as const;
const ROUTE_TIER_LABELS: Record<(typeof ROUTE_TIERS)[number], string> = {
  light: "Light",
  standard: "Standard",
  heavy: "Heavy",
};

/** Serialize a model as the daemon's "provider:model" routing id. */
const fmtPM = (pm: PM): string => (pm.model ? `${pm.provider}:${pm.model}` : pm.provider);

/** One selectable routing-model row in the "Turn on Auto" chooser. */
function RoutingChoice({
  pm,
  selected,
  recommended,
  onClick,
}: {
  pm: PM;
  selected: boolean;
  recommended?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center justify-between gap-2 rounded-md border px-1.5 py-1 text-left transition-colors ${
        selected
          ? "border-accent/50 bg-accent/[0.12]"
          : "border-transparent hover:bg-white/[0.06]"
      }`}
    >
      <span className="min-w-0">
        <span className="block truncate font-mono text-[11px] text-zinc-200">
          {pm.model || pm.provider}
        </span>
        <span className="text-[9px] text-zinc-500">
          {recommended ? "Recommended · " : ""}
          {pm.provider}
        </span>
      </span>
      <span className="flex shrink-0 items-center gap-1">
        {recommended && (
          <span className="rounded-full bg-emerald-500/15 px-1.5 py-0.5 text-[8px] font-semibold uppercase tracking-wide text-emerald-300">
            cheapest
          </span>
        )}
        {selected && <Check size={12} className="text-accent-soft" />}
      </span>
    </button>
  );
}

/**
 * Topbar provider/model switcher — set the ACTIVE default model in one click,
 * across every connected account (beyond the per-session dropdown). Reuses
 * /health (current default + availability) + /models (catalog) and persists the
 * choice via PUT /settings. Opens on the global `ij:open-switcher` event (the
 * ⌘K palette dispatches it).
 *
 * Also hosts **Auto — smart routing**: when `default_provider === "auto"` a
 * cheap user-chosen routing model classifies each request and forwards it to the
 * best connected model. The Auto row (top of the panel) turns it on/off and shows
 * where requests go; picking any specific model below turns Auto back off.
 */
export function ModelSwitcher() {
  const health = usePolledApi<Health>("/health", 5000);
  const modelsData = useApi<{ models: ModelOption[] }>("/models");
  const [open, setOpen] = useState(false);
  // The Auto — smart-routing detail is a collapsible disclosure (expands on
  // hover / click) so it never sits fully-expanded and static above the model
  // list — once configured, it collapses to a one-line summary so you can pick
  // from the rest of the list. Reset to collapsed each time the panel opens.
  const [autoOpen, setAutoOpen] = useState(false);
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

  // Auto is ON when the active provider is the "auto" sentinel.
  const autoOn = activeProvider === "auto";

  // Lazy /routing view: fetched while Auto is ON (so the topbar button can name
  // the routing model) or while the panel is open (for the chooser / tiers).
  const routing = useApi<RoutingView>(autoOn || open ? "/routing" : null, [autoOn, open]);
  const rv = routing.data;
  const [pick, setPick] = useState<string>(""); // chosen routing id, "provider:model"
  const [routingBusy, setRoutingBusy] = useState(false);
  const [routingErr, setRoutingErr] = useState<string | null>(null);

  // Chooser selection: the user's pick, else the suggested cheapest.
  const suggestedRM = rv?.suggested ? fmtPM(rv.suggested) : "";
  const selectedRM = pick || suggestedRM;

  // Routing model shown in the topbar (just the model part, dropping provider).
  const routingModelStr = rv?.routing_model ?? "";
  const rmShort = routingModelStr.includes(":")
    ? routingModelStr.slice(routingModelStr.indexOf(":") + 1)
    : routingModelStr;

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

  // Start with the Auto detail collapsed every time the panel (re)opens.
  useEffect(() => {
    if (!open) setAutoOpen(false);
  }, [open]);

  // Refetch the catalog each time the panel opens: the topbar survives every
  // in-app navigation, so a once-at-mount /models went stale the moment an
  // endpoint/connection was saved elsewhere — a freshly added endpoint showed
  // in the per-page pickers (they remount and refetch) but never here.
  const reloadModels = modelsData.reload;
  useEffect(() => {
    if (open) reloadModels?.();
  }, [open, reloadModels]);

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

  // Turn Auto ON (or, while on, re-point it at a different routing model). Blank
  // `routingModel` lets the daemon default to the suggested cheapest. Optimistic:
  // the topbar flips to "Auto" instantly, reconciled once /health agrees.
  async function enableAuto(routingModel: string, close: boolean) {
    setRoutingBusy(true);
    setRoutingErr(null);
    setOptimistic({ provider: "auto", model: h?.default_model ?? "" });
    try {
      await post("/routing/enable", { routing_model: routingModel });
      health.reload?.();
      routing.reload?.();
      if (close) setOpen(false);
    } catch (e) {
      setOptimistic(null); // revert the "Auto" label on failure
      setRoutingErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setRoutingBusy(false);
    }
  }

  if (!h) return null; // until /health loads (the offline banner covers downtime)

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-xs text-zinc-300 transition-colors hover:border-white/20"
        title={
          autoOn
            ? `Auto — smart routing${rv?.routing_model ? ` · via ${rv.routing_model}` : ""}`
            : "Switch the active model"
        }
        aria-label={autoOn ? "Auto — smart routing" : "Switch the active model"}
      >
        {autoOn ? (
          <Sparkles size={13} className="text-accent-soft" />
        ) : (
          <Cpu size={13} className="text-accent-soft" />
        )}
        {autoOn ? (
          <span className="hidden items-baseline gap-1 sm:inline-flex">
            <span className="text-[11px] font-medium text-accent-soft">Auto</span>
            {rmShort && (
              <span className="max-w-[110px] truncate font-mono text-[10px] text-zinc-500">
                {rmShort}
              </span>
            )}
          </span>
        ) : (
          <span className="hidden max-w-[150px] truncate font-mono text-[11px] sm:inline">
            {activeModel}
          </span>
        )}
        <ChevronDown size={12} className="text-zinc-500" />
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-1.5 w-80 rounded-xl border border-white/10 bg-ink-950/95 p-1.5 shadow-card-hover backdrop-blur-xl">
          {/* Auto — smart routing: a COLLAPSIBLE disclosure. Compact by default so
              it never sits fully-expanded over the model list; expands on hover
              (or click, for touch/keyboard) to configure, and collapses to a
              one-line summary once you move on to the list below. */}
          <div
            className="mb-1.5 rounded-lg border border-accent/30 bg-accent/[0.08]"
            onMouseEnter={() => setAutoOpen(true)}
            onMouseLeave={() => setAutoOpen(false)}
          >
            <button
              type="button"
              onClick={() => setAutoOpen((v) => !v)}
              aria-expanded={autoOpen}
              className="flex w-full items-center justify-between gap-2 rounded-lg px-2 py-2 text-left"
            >
              <span className="flex min-w-0 items-center gap-1.5">
                <Sparkles size={13} className="shrink-0 text-accent-soft" />
                <span className="text-[12px] font-medium text-zinc-100">
                  Auto — smart routing
                </span>
              </span>
              <span className="flex shrink-0 items-center gap-1.5">
                {autoOn ? (
                  <span className="rounded-full bg-emerald-500/15 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-emerald-300">
                    On
                  </span>
                ) : (
                  <span className="text-[9px] font-medium uppercase tracking-wide text-zinc-500">
                    Off
                  </span>
                )}
                <ChevronDown
                  size={13}
                  className={`text-zinc-500 transition-transform duration-200 ${
                    autoOpen ? "rotate-180" : ""
                  }`}
                />
              </span>
            </button>

            {/* Collapsed + ON → a compact one-liner of where requests route. */}
            {!autoOpen && autoOn && (
              <div className="px-2 pb-2 -mt-0.5 text-[10px] text-zinc-400">
                Routing via{" "}
                <span className="font-mono text-accent-soft">
                  {rmShort || rv?.routing_model || "cheapest"}
                </span>
              </div>
            )}

            {/* Expanded → full configuration. */}
            {autoOpen && (
              <div className="px-2 pb-2">
                <p className="text-[10px] leading-snug text-zinc-400">
                  Routes each request to the best model — cheap for simple, strong for
                  complex.
                </p>

                {/* /routing fetch states */}
                {routing.loading && !rv && (
                  <div className="mt-2 text-[11px] text-zinc-500">
                    Loading routing options…
                  </div>
                )}
                {routing.error && !rv && (
                  <div className="mt-2 text-[11px] text-rose-300">
                    {routing.error.message}
                  </div>
                )}

                {/* OFF → chooser + "Turn on Auto" */}
                {rv && !autoOn && (
                  <div className="mt-2">
                    <div className="mb-1 text-[10px] uppercase tracking-wider text-zinc-400">
                      Routing model
                    </div>
                    {rv.connected.length === 0 ? (
                      <div className="text-[11px] text-zinc-500">
                        Connect a model to enable Auto.
                      </div>
                    ) : (
                      <div className="space-y-0.5">
                        {rv.suggested && (
                          <RoutingChoice
                            pm={rv.suggested}
                            recommended
                            selected={selectedRM === fmtPM(rv.suggested)}
                            onClick={() => setPick(fmtPM(rv.suggested!))}
                          />
                        )}
                        {rv.connected
                          .filter(
                            (c) => !rv.suggested || fmtPM(c) !== fmtPM(rv.suggested),
                          )
                          .map((c) => (
                            <RoutingChoice
                              key={fmtPM(c)}
                              pm={c}
                              selected={selectedRM === fmtPM(c)}
                              onClick={() => setPick(fmtPM(c))}
                            />
                          ))}
                      </div>
                    )}
                    <button
                      onClick={() => enableAuto(selectedRM, true)}
                      disabled={routingBusy || !selectedRM}
                      className="mt-2 w-full rounded-lg bg-accent/90 px-2 py-1.5 text-[11px] font-semibold text-white transition-colors hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {routingBusy ? "Turning on…" : "Turn on Auto"}
                    </button>
                  </div>
                )}

                {/* ON → active state: routing model, tiers, change routing model */}
                {rv && autoOn && (
                  <div className="mt-2">
                    <div className="text-[11px] text-zinc-300">
                      Routing via{" "}
                      <span className="font-mono text-accent-soft">
                        {rmShort || rv.routing_model || "cheapest"}
                      </span>
                    </div>
                    <div className="mt-2 space-y-0.5 rounded-lg border border-white/[0.06] bg-white/[0.02] p-1.5">
                      {ROUTE_TIERS.map((t) => {
                        const pm = rv.tiers[t];
                        return (
                          <div
                            key={t}
                            className="flex items-center justify-between gap-2 text-[10px]"
                          >
                            <span className="text-zinc-400">{ROUTE_TIER_LABELS[t]}</span>
                            <span className="ml-2 min-w-0 truncate font-mono text-zinc-300">
                              {pm ? pm.model : "—"}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                    {rv.connected.length > 1 && (
                      <div className="mt-2">
                        <div className="mb-1 text-[10px] uppercase tracking-wider text-zinc-400">
                          Change routing model
                        </div>
                        <div className="space-y-0.5">
                          {rv.connected.map((c) => {
                            const isCur = rv.routing_model === fmtPM(c);
                            return (
                              <button
                                key={fmtPM(c)}
                                onClick={() => !isCur && enableAuto(fmtPM(c), false)}
                                disabled={routingBusy || isCur}
                                className={`flex w-full items-center justify-between gap-2 rounded-md px-1.5 py-1 text-left text-[11px] transition-colors ${
                                  isCur
                                    ? "bg-accent/[0.12] text-accent-soft"
                                    : "text-zinc-300 hover:bg-white/[0.06]"
                                }`}
                              >
                                <span className="min-w-0 truncate font-mono text-[10px]">
                                  {c.model}
                                </span>
                                {isCur && (
                                  <Check size={12} className="shrink-0 text-accent-soft" />
                                )}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {routingErr && (
                  <div className="mt-2 text-[11px] text-rose-300">{routingErr}</div>
                )}
              </div>
            )}
          </div>

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
          {autoOn && (
            <div className="px-2 pb-1 text-[10px] text-zinc-500">
              Pick a model to turn Auto off.
            </div>
          )}
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
