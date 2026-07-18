"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Server,
  RefreshCw,
  Cpu,
  Gauge,
  Zap,
  Plus,
  Cloud,
  HardDrive,
  Layers,
  Network,
  Pin,
  Timer,
  Activity,
  Clock,
} from "lucide-react";
import { useApi, usePolledApi } from "@/lib/useApi";
import { ApiError, get, post } from "@/lib/api";
import {
  Badge,
  Card,
  Dot,
  Empty,
  ErrorNote,
  OfflineHint,
  SkeletonRows,
  Stat,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { Sparkline } from "@/components/fleet/Sparkline";
import {
  DASH,
  SPARK_POINTS,
  buildFleetTree,
  codeRouteText,
  flattenTree,
  fleetTone,
  fmtBytes,
  fmtCount,
  fmtCtx,
  fmtLatency,
  fmtPct,
  fmtTokens,
  fmtTps,
  fmtUsd,
  genTps,
  hasSignal,
  hostOf,
  kindLabel,
  kindTone,
  kvCache,
  nodeName,
  normHint,
  num,
  pushRates,
  running,
  statusLabel,
  unloadIn,
  waiting,
  type FleetModel,
  type FleetProbe,
  type FleetResponse,
  type FleetUsage,
  type NodeSnapshot,
  type Series,
} from "@/lib/fleet";

/** Poll cadence. Polling also leases the daemon's 2s sampling cadence. */
const POLL_MS = 2000;

/* -------------------------------------------------------------------------- */
/*  Small shared pieces                                                        */
/* -------------------------------------------------------------------------- */

function StatusPill({ snap }: { snap: NodeSnapshot }) {
  return <Badge value={statusLabel(snap.status)} tone={fleetTone(snap.status)} />;
}

/**
 * A Stat that tells the truth about missing data. `value === null` means the
 * metric was NOT READ, and renders as an em dash carrying `reason` — never as
 * a zero, which would claim an idle server we never actually reached.
 */
function MetricStat({
  label,
  value,
  reason,
  icon,
  sub,
  accent = false,
}: {
  label: string;
  value: string | null;
  reason?: string | null;
  icon?: ReactNode;
  /** Extra detail shown only when the value IS present. */
  sub?: ReactNode;
  accent?: boolean;
}) {
  const missing = value === null;
  const why = reason || "not reported by this endpoint";
  return (
    <Stat
      label={label}
      value={
        missing ? (
          <span className="text-zinc-600" title={why}>
            {DASH}
          </span>
        ) : (
          value
        )
      }
      sub={missing ? <span title={why}>{why}</span> : sub}
      icon={icon}
      accent={accent && !missing}
    />
  );
}

/**
 * Copyable commands for a node the user has to go fix on another machine. The
 * whole point of a not-probeable node is that the fix happens elsewhere, so the
 * exact command is on screen rather than described.
 */
function Commands({ commands }: { commands: string[] }) {
  if (commands.length === 0) return null;
  return (
    <pre className="mt-2 overflow-x-auto rounded-lg border border-white/[0.06] bg-black/40 px-3 py-2 font-mono text-[11px] leading-relaxed text-zinc-300">
      {commands.join("\n")}
    </pre>
  );
}

/* -------------------------------------------------------------------------- */
/*  Models                                                                     */
/* -------------------------------------------------------------------------- */

function ModelRow({ model }: { model: FleetModel }) {
  const ctx = fmtCtx(model.context_length ?? model.max_model_len);
  const vram = num(model.size_vram);
  const unload = unloadIn(model.expires_at);
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b hairline py-2 last:border-0">
      <div className="min-w-0">
        <div className="truncate font-mono text-xs text-zinc-200">{model.id}</div>
        {model.root && model.root !== model.id && (
          <div className="truncate text-[11px] text-zinc-600">{model.root}</div>
        )}
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 text-[11px] tabular-nums text-zinc-500">
        {ctx && <span title="Context window">{ctx} ctx</span>}
        {model.parameter_size && <span>{model.parameter_size}</span>}
        {model.quantization && <span className="text-zinc-600">{model.quantization}</span>}
        {vram !== null && (
          <span title="Resident VRAM" className="text-accent-soft/80">
            {fmtBytes(vram)}
          </span>
        )}
        {unload?.kind === "pinned" && (
          <span
            className="inline-flex items-center gap-1 text-emerald-300/80"
            title="Keep-alive forever — Ollama writes a year-2318 expiry for this"
          >
            <Pin size={11} /> pinned
          </span>
        )}
        {unload?.kind === "in" && (
          <span
            className="inline-flex items-center gap-1 text-amber-300/80"
            title="Time until Ollama unloads this model from VRAM"
          >
            <Timer size={11} /> unloads in {unload.text}
          </span>
        )}
        {unload?.kind === "expired" && <span className="text-zinc-600">unloading</span>}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Children (proxy topology)                                                  */
/* -------------------------------------------------------------------------- */

/**
 * One alias behind a proxy. Everything here is SECONDHAND — the proxy told us
 * about it — so an unreachable child says "not probeable" with the bind hint
 * rather than pretending to be an idle server.
 */
function ChildRow({ snap }: { snap: NodeSnapshot }) {
  const { text, commands } = normHint(snap.hint, snap.commands);
  const model = snap.models?.[0]?.id ?? null;
  return (
    <div className="rounded-xl border border-white/[0.05] bg-white/[0.015] px-3 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="font-mono text-xs font-medium text-zinc-200">
            {snap.node.alias || nodeName(snap.node)}
          </span>
          {snap.node.tool_use && (
            <span
              className="rounded border border-violet-500/25 bg-violet-500/10 px-1.5 py-px text-[10px] text-violet-300"
              title="Verified: this endpoint calls tools"
            >
              tools
            </span>
          )}
          {snap.proxy_verdict && snap.proxy_verdict !== "unknown" && (
            <span
              className="text-[10px] text-zinc-600"
              title="Verdict from the proxy's own /health, not our probe"
            >
              proxy says {snap.proxy_verdict}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {fmtLatency(snap.latency_ms) && (
            <span className="text-[11px] tabular-nums text-zinc-600">
              {fmtLatency(snap.latency_ms)}
            </span>
          )}
          <StatusPill snap={snap} />
        </div>
      </div>
      <div className="mt-1 truncate text-[11px] text-zinc-500">
        {model && <span className="font-mono">{model}</span>}
        {model && snap.node.base_url && <span className="text-zinc-700"> · </span>}
        {snap.node.base_url && (
          <span className="text-zinc-600">{hostOf(snap.node.base_url)}</span>
        )}
        {!model && !snap.node.base_url && (
          <span className="text-zinc-600">no backing endpoint reported</span>
        )}
      </div>
      {snap.error && <div className="mt-1.5 text-[11px] text-rose-300/80">{snap.error}</div>}
      {text && <div className="mt-1.5 text-[11px] text-amber-200/80">{text}</div>}
      <Commands commands={commands} />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Node card                                                                  */
/* -------------------------------------------------------------------------- */

/**
 * One node: reachability, whatever metrics it actually reported, its resident
 * models, and — for a proxy — the upstreams it fronts, each with its own status.
 *
 * The contract this card keeps: a metric we could not read is an em dash
 * carrying its `metrics_reason`, and a node we cannot reach shows the bind hint
 * plus the command that would fix it. Nothing here renders a number we did not
 * measure.
 */
function NodeCard({ snap, series }: { snap: NodeSnapshot; series: (number | null)[] }) {
  const m = snap.metrics ?? null;
  const supported = snap.metrics_supported !== false;
  const reason = snap.metrics_reason ?? null;
  const kv = kvCache(m);
  const tps = genTps(snap.rates);
  const { text: hintText, commands } = normHint(snap.hint, snap.commands);
  const models = snap.models ?? [];
  const children = snap.children ?? [];
  const secondhand = snap.evidence === "proxy";

  return (
    <Card
      title={
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-zinc-100">{nodeName(snap.node)}</span>
          <Badge value={kindLabel(snap.node.kind)} tone={kindTone(snap.node.kind)} />
          <span className="font-mono text-[11px] font-normal text-zinc-600">
            {hostOf(snap.node.base_url)}
          </span>
        </span>
      }
      icon={<Server size={15} />}
      right={
        <span className="flex shrink-0 items-center gap-2">
          {fmtLatency(snap.latency_ms) && (
            <span
              className="text-[11px] tabular-nums text-zinc-500"
              title="Round-trip time of the last probe"
            >
              {fmtLatency(snap.latency_ms)}
            </span>
          )}
          <StatusPill snap={snap} />
        </span>
      }
    >
      {/* Why we can't reach it — the actionable part, above everything else. */}
      {(snap.error || hintText) && (
        <div className="mb-4 rounded-xl border border-amber-500/20 bg-amber-500/[0.05] px-3 py-2.5">
          {snap.error && <div className="text-xs text-rose-200/90">{snap.error}</div>}
          {hintText && <div className="mt-1 text-xs text-amber-100/80">{hintText}</div>}
          <Commands commands={commands} />
        </div>
      )}

      {/* Serving metrics. A null NEVER becomes a zero. */}
      {supported ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <MetricStat
            label="Running"
            value={fmtCount(running(m))}
            reason={reason}
            icon={<Activity size={15} />}
          />
          <MetricStat
            label="Waiting"
            value={fmtCount(waiting(m))}
            reason={reason}
            icon={<Clock size={15} />}
          />
          <MetricStat
            label="tok/s"
            value={fmtTps(tps)}
            reason={
              reason ?? "needs two consecutive samples — keep this page open a moment"
            }
            icon={<Zap size={15} />}
            accent
            sub="generation throughput"
          />
          <MetricStat
            label="KV cache"
            value={fmtPct(kv)}
            reason={reason}
            icon={<Gauge size={15} />}
            sub={
              kv !== null ? (
                <span className="mt-1.5 block h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
                  <span
                    className="block h-full rounded-full bg-gradient-to-r from-accent/50 to-accent"
                    style={{ width: `${Math.min(100, Math.max(1, kv * 100))}%` }}
                  />
                </span>
              ) : undefined
            }
          />
        </div>
      ) : (
        // No metric row at all, and a plain sentence saying why. An empty grid
        // of dashes would imply we tried to read numbers this server never had.
        <div className="rounded-xl border border-white/[0.05] bg-white/[0.015] px-3 py-2.5 text-xs text-zinc-500">
          {reason || "This endpoint exposes no serving metrics."}
        </div>
      )}

      {/* Throughput history. Gaps stay gaps — see Sparkline. */}
      {hasSignal(series) && (
        <div className="mt-4">
          <div className="mb-1 flex items-center justify-between text-[11px] text-zinc-600">
            <span>tok/s · last {SPARK_POINTS} samples</span>
            <span className="tabular-nums">{fmtTps(tps) ?? DASH}</span>
          </div>
          <Sparkline points={series} />
        </div>
      )}

      {/* Loaded / served models. */}
      {models.length > 0 && (
        <div className="mt-4">
          <div className="mb-1 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-500">
            <Layers size={12} /> Models
          </div>
          <div>
            {models.map((mod) => (
              <ModelRow key={mod.id} model={mod} />
            ))}
          </div>
        </div>
      )}

      {/* Proxy topology. */}
      {children.length > 0 && (
        <div className="mt-4">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <span className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-500">
              <Network size={12} /> Behind this proxy
            </span>
            <span className="text-[11px] text-zinc-600">
              reported by the proxy — secondhand, not probed directly
            </span>
          </div>
          <div className="space-y-2">
            {children.map((child) => (
              <ChildRow key={child.node.id} snap={child} />
            ))}
          </div>
          {snap.proxy_health && (
            <div className="mt-2 text-[11px] text-zinc-600">
              proxy /health:{" "}
              {num(snap.proxy_health.healthy) !== null
                ? `${snap.proxy_health.healthy} healthy`
                : `${DASH} healthy`}
              {" · "}
              {num(snap.proxy_health.unhealthy) !== null
                ? `${snap.proxy_health.unhealthy} unhealthy`
                : `${DASH} unhealthy`}
            </div>
          )}
        </div>
      )}

      {secondhand && (
        <div className="mt-3 text-[11px] text-zinc-600">
          Evidence: reported by a proxy, not probed directly.
        </div>
      )}
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/*  Local vs cloud                                                             */
/* -------------------------------------------------------------------------- */

/**
 * Local/cloud split. The avoided-spend figure is ALWAYS shown with the model it
 * was priced against — a bare "$14 saved" is a number nobody can check — so
 * with no comparison model we show the token split and say the basis is
 * missing instead of the dollars.
 */
function UsageStrip({ usage }: { usage: FleetUsage | null }) {
  const local = num(usage?.local_tokens);
  const cloud = num(usage?.cloud_tokens);
  const avoided = num(usage?.est_avoided_usd);
  const provider = (usage?.comparison_provider || "").trim();
  const model = (usage?.comparison_model || "").trim();
  const baseline = [provider, model].filter(Boolean).join(":");
  const cloudCost = num(usage?.cloud_cost_usd);
  const byNode = (usage?.by_node ?? []).filter((n) => num(n.est_avoided_usd) !== null);

  // The split bar needs BOTH sides. With one unread, any proportion we drew
  // would be a claim about a number we never got — so we draw nothing.
  const total = local !== null && cloud !== null ? local + cloud : null;
  const localPct = total !== null && total > 0 ? ((local as number) / total) * 100 : null;

  return (
    <Card
      title="Local vs cloud"
      icon={<HardDrive size={15} />}
      right={
        usage?.days ? (
          <span className="text-[11px] font-normal text-zinc-600">
            last {usage.days} days
          </span>
        ) : null
      }
    >
      {local === null && cloud === null ? (
        <div className="text-xs text-zinc-500">
          No local/cloud split recorded yet — run something through the fleet and
          it will show up here.
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-xs">
            <span className="text-accent-soft">
              <HardDrive size={12} className="mr-1 inline" />
              local {fmtTokens(local)} tok
            </span>
            <span className="text-zinc-400">
              <Cloud size={12} className="mr-1 inline" />
              cloud {fmtTokens(cloud)} tok
              {cloudCost !== null && (
                <span className="text-zinc-600"> · {fmtUsd(cloudCost)} billed</span>
              )}
            </span>
          </div>
          {localPct !== null && (
            <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-white/[0.04]">
              <div
                className="h-full bg-gradient-to-r from-accent/50 to-accent"
                style={{ width: `${localPct}%` }}
                title={`${Math.round(localPct)}% of tokens ran locally`}
              />
            </div>
          )}
          {avoided !== null && baseline ? (
            <div>
              <div className="text-xs text-emerald-300/90">
                est. {fmtUsd(avoided)} avoided vs{" "}
                <span className="font-mono text-emerald-200/90">{baseline}</span>
              </div>
              {usage?.basis && (
                <div className="mt-1 text-[11px] text-zinc-600">{usage.basis}</div>
              )}
            </div>
          ) : (
            <div className="text-xs text-zinc-600">
              No avoided-spend estimate: the daemon reported no comparison model
              to price local tokens against.
            </div>
          )}
          {byNode.length > 0 && baseline && (
            <div className="space-y-1 border-t hairline pt-2.5">
              {byNode.slice(0, 4).map((n) => (
                <div
                  key={n.node_id}
                  className="flex items-baseline justify-between gap-3 text-[11px]"
                >
                  <span className="truncate text-zinc-400">
                    {n.label || n.node_id}
                    {n.models?.length ? (
                      <span className="font-mono text-zinc-600"> · {n.models[0]}</span>
                    ) : null}
                  </span>
                  <span className="shrink-0 tabular-nums text-zinc-500">
                    {fmtTokens((num(n.input_tokens) ?? 0) + (num(n.output_tokens) ?? 0))}{" "}
                    tok · {fmtUsd(n.est_avoided_usd)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/*  Add node                                                                   */
/* -------------------------------------------------------------------------- */

/**
 * Add a node by URL. We probe as the user types (debounced ~700ms, the same
 * shape as the connections page's endpoint detect) so what is actually AT that
 * URL is on screen before anything is saved. Nothing here persists — the probe
 * route saves nothing, and an unreachable endpoint is still addable, because a
 * box that happens to be asleep is still part of the fleet.
 */
function AddNodeForm({ onAdded }: { onAdded: () => void }) {
  const [url, setUrl] = useState("");
  const [label, setLabel] = useState("");
  const [probe, setProbe] = useState<FleetProbe | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    const u = url.trim();
    if (!/^https?:\/\/.+/i.test(u)) {
      setProbe(null);
      setProbeError(null);
      return;
    }
    let cancelled = false;
    setProbing(true);
    setProbeError(null);
    const timer = setTimeout(async () => {
      try {
        const res = await post<FleetProbe>("/fleet/probe", { base_url: u });
        if (cancelled) return;
        setProbe(res);
        setProbeError(res.error || null);
      } catch (err) {
        if (cancelled) return;
        setProbe(null);
        setProbeError(err instanceof ApiError ? err.message : String(err));
      } finally {
        if (!cancelled) setProbing(false);
      }
    }, 700);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      setProbing(false);
    };
  }, [url]);

  const snap = probe?.snapshot ?? null;
  const kind = probe?.kind ?? null;
  const found = snap?.models ?? [];
  const kids = probe?.children ?? [];

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const u = url.trim();
    if (!u) return;
    setSaving(true);
    setSaveError(null);
    try {
      await post("/fleet/nodes", { base_url: u, label: label.trim() });
      setUrl("");
      setLabel("");
      setProbe(null);
      onAdded();
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card title="Add a node" icon={<Plus size={15} />}>
      <form onSubmit={save} className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-[2fr_1fr]">
          <input
            className="field font-mono text-xs"
            placeholder="http://100.66.161.52:8888"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            spellCheck={false}
            aria-label="Base URL"
          />
          <input
            className="field text-xs"
            placeholder="label (optional)"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            aria-label="Label"
          />
        </div>

        {/* What we found, before saving anything. */}
        {probing && <div className="text-xs text-zinc-500">probing {url.trim()}…</div>}
        {!probing && probeError && (
          <div className="text-xs text-amber-200/80">
            Could not read that endpoint: {probeError}. You can still add it — it
            will show as offline until it answers.
          </div>
        )}
        {!probing && !probeError && probe && (
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.015] px-3 py-2.5 text-xs text-zinc-400">
            <span className="flex flex-wrap items-center gap-2">
              <span className="text-zinc-300">Detected</span>
              <Badge value={kindLabel(kind)} tone={kindTone(kind)} />
              {snap && <StatusPill snap={snap} />}
              {fmtLatency(snap?.latency_ms) && (
                <span className="text-zinc-600">{fmtLatency(snap?.latency_ms)}</span>
              )}
            </span>
            {kind === "unknown" && probe.reason && (
              <div className="mt-1.5 text-amber-200/80">{probe.reason}</div>
            )}
            {found.length > 0 ? (
              <div className="mt-1.5 space-y-0.5 font-mono text-[11px] text-zinc-500">
                {found.slice(0, 6).map((mo) => (
                  <div key={mo.id} className="truncate">
                    {mo.id}
                    {fmtCtx(mo.context_length ?? mo.max_model_len) && (
                      <span className="text-zinc-600">
                        {" "}
                        · {fmtCtx(mo.context_length ?? mo.max_model_len)} ctx
                      </span>
                    )}
                  </div>
                ))}
                {found.length > 6 && (
                  <div className="text-zinc-600">+{found.length - 6} more</div>
                )}
              </div>
            ) : (
              <div className="mt-1 text-zinc-600">no models reported</div>
            )}
            {kids.length > 0 && (
              <div className="mt-1.5 text-[11px] text-zinc-500">
                fronts {kids.length} upstream{kids.length === 1 ? "" : "s"}:{" "}
                <span className="font-mono text-zinc-400">
                  {kids
                    .map((c) => c.alias || c.label || c.id)
                    .filter(Boolean)
                    .join(", ")}
                </span>
              </div>
            )}
            {snap && snap.metrics_supported === false && snap.metrics_reason && (
              <div className="mt-1 text-zinc-600">{snap.metrics_reason}</div>
            )}
          </div>
        )}

        {saveError && <ErrorNote>{saveError}</ErrorNote>}

        <button
          type="submit"
          disabled={saving || !url.trim()}
          className="btn-accent py-1.5 text-xs"
        >
          <Plus size={14} /> {saving ? "Adding…" : "Add node"}
        </button>
      </form>
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/*  Page                                                                       */
/* -------------------------------------------------------------------------- */

export default function FleetPage() {
  const { data, error, loading, reload } = usePolledApi<FleetResponse>("/fleet", POLL_MS);
  // Attribution is slow-moving and DB-backed — fetched once, refreshed with the
  // button rather than every 2s.
  const { data: usage, reload: reloadUsage } = useApi<FleetUsage>("/fleet/usage");

  // `loading` flips true on EVERY 2s poll tick, so driving the spinner (or the
  // skeletons) off it would strobe. Manual refresh gets its own flag; first-load
  // states key off "loading AND no data yet" — the same split the usage page uses.
  const [refreshing, setRefreshing] = useState(false);
  const firstLoad = loading && !data;

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      // Forces one synchronous sampling pass; the poll below then renders it.
      await get("/fleet/snapshot?refresh=1");
    } catch {
      // A failed forced pass is not fatal — the reload still shows the last
      // snapshot the sampler holds, with its own honest age and errors.
    } finally {
      setRefreshing(false);
      reload();
      reloadUsage();
    }
  }, [reload, reloadUsage]);

  // The daemon returns the fleet FLAT (a proxy's upstreams are nodes in their
  // own right); nest them so the page shows topology, not four orphan boxes.
  const roots = useMemo(() => buildFleetTree(data?.nodes), [data]);

  // Per-node tok/s history for the sparklines. `pushRates` skips snapshots it
  // has already charted, so a poll that outruns the sampler cannot stretch one
  // reading into a plateau of readings that were never taken.
  const [history, setHistory] = useState<Record<string, Series>>({});
  useEffect(() => {
    if (roots.length === 0) return;
    const all = flattenTree(roots);
    setHistory((prev) => pushRates(prev, all));
  }, [roots]);

  const offline = !!error && error.status === 0;
  const sampling = data?.sampling ?? null;
  const route = codeRouteText(data?.code_route);
  const lease = num(sampling?.lease_expires_in);
  const interval = num(sampling?.interval);

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Fleet"
          subtitle="Every inference endpoint you can reach — what's loaded, what's serving, and what we honestly can't see."
          actions={
            <div className="flex items-center gap-2">
              <span
                className="flex items-center gap-1.5 rounded-xl border border-white/[0.08] bg-ink-900/80 px-2.5 py-1.5 text-[11px] text-zinc-400"
                title={
                  sampling?.active
                    ? `Sampling every ${interval ?? POLL_MS / 1000}s${
                        lease !== null ? ` · lease ${Math.round(lease)}s left` : ""
                      }`
                    : `Not sampling right now${
                        interval !== null ? ` — idles at ${interval}s` : ""
                      }`
                }
              >
                <Dot on={!!sampling?.active} />
                {sampling?.active ? "live" : "idle"}
              </span>
              <button
                type="button"
                onClick={refresh}
                disabled={refreshing}
                title="Force a fresh probe of every node"
                className="btn-ghost py-1.5 text-xs disabled:opacity-50"
              >
                <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />{" "}
                Refresh
              </button>
            </div>
          }
        />
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      {error && !offline && (
        <Reveal>
          <ErrorNote>{error.message}</ErrorNote>
        </Reveal>
      )}

      {/* A fault inside the fleet stack, reported alongside whatever data survived. */}
      {data?.error && (
        <Reveal>
          <ErrorNote>{data.error}</ErrorNote>
        </Reveal>
      )}

      {route && (
        <Reveal>
          <div className="flex items-center gap-2 text-xs text-zinc-500">
            <Cpu size={13} className="shrink-0 text-accent-soft/70" />
            <span>{route}</span>
          </div>
        </Reveal>
      )}

      <Reveal>
        <UsageStrip usage={usage} />
      </Reveal>

      {firstLoad ? (
        <Reveal>
          <Card title="Nodes" icon={<Server size={15} />}>
            <SkeletonRows rows={4} />
          </Card>
        </Reveal>
      ) : roots.length === 0 ? (
        <Reveal>
          <Card title="Nodes" icon={<Server size={15} />}>
            <Empty
              icon={<Server size={26} />}
              action={{ label: "Open Settings", href: "/settings" }}
            >
              No nodes yet. Endpoints you configure in Settings appear here
              automatically — or add one below by URL.
            </Empty>
          </Card>
        </Reveal>
      ) : (
        roots.map((snap) => (
          <Reveal key={snap.node.id}>
            <NodeCard snap={snap} series={history[snap.node.id]?.points ?? []} />
          </Reveal>
        ))
      )}

      <Reveal>
        <AddNodeForm onAdded={refresh} />
      </Reveal>
    </PageShell>
  );
}
