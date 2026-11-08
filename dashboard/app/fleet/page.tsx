"use client";

import Link from "next/link";
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
  Pencil,
} from "lucide-react";
import { useApi, usePolledApi } from "@/lib/useApi";
import { LoadFailedLine } from "@/components/LoadFailedLine";
import { ApiError, del, get, patch, post } from "@/lib/api";
import { providerDisplay } from "@/lib/onboarding";
// v1.424.0 (D8): each model's own tools / vision, in words.
import {
  borrowedNote,
  capabilityOf,
  toolsChip,
  visionChip,
  type CapView,
} from "@/lib/modelCapability";
import {
  Badge,
  Card,
  ConfirmButton,
  Empty,
  ErrorNote,
  OfflineHint,
  SkeletonRows,
  Stat,
} from "@/components/ui";
import { CALM_ACTION, CALM_ICON_ACTION } from "@/components/connections/endpointChip";
// v1.449.0 (readiness Wave C, item 24): what Remove does to the default model.
import { DefaultImpactLine, defaultReplacedWords, fetchDefaultImpact, type DefaultImpact } from "@/components/connections/DefaultImpact";
import { useDaemon } from "@/lib/daemon";
import { IconButton } from "@/components/IconButton";
import { PageHeader } from "@/components/PageHeader";
import { AllConnectionsLink } from "@/components/connections/AllConnectionsLink";
import { PageShell, Reveal } from "@/components/motion";
import { Sparkline } from "@/components/fleet/Sparkline";
import {
  DASH,
  SPARK_POINTS,
  buildFleetTree,
  chatHint,
  chatLabel,
  chatTone,
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
  isUnchecked,
  kindLabel,
  kindTone,
  kvCache,
  nodeName,
  normHint,
  num,
  pushRates,
  running,
  snapStatusLabel,
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
  // v1.329.0: a node no probe has looked at yet says so, in a neutral tone
  // and in sentence case (the badge's capitalize would read "Not Checked Yet").
  // v1.330.0 (calm M3): the calm Badge, like Connections: no border, no fill,
  // only the dot carries the tone.
  if (isUnchecked(snap)) return <Badge variant="calm" value="Not checked yet" tone="slate" keepCase />;
  return <Badge variant="calm" value={snapStatusLabel(snap)} tone={fleetTone(snap.status)} />;
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
  const why = reason || "not reported by this model server";
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
    <pre className="mt-2 overflow-x-auto rounded-lg border border-white/[0.06] bg-ink-950/60 px-3 py-2 font-mono text-[11px] leading-relaxed text-zinc-300">
      {commands.join("\n")}
    </pre>
  );
}

/* -------------------------------------------------------------------------- */
/*  Models                                                                     */
/* -------------------------------------------------------------------------- */

/**
 * v1.424.0 (D8): what ONE model can do by itself — a live check of it, its
 * server's own statement, or "not known". Drawn only when the daemon answers
 * per model (an older one sent the node's one flag, which this row never
 * showed). Quiet words; the title says what each answer means.
 */
function CapChips({ view, model }: { view: CapView | null; model: string }) {
  if (!view) return null;
  const chips = [toolsChip(view, model), visionChip(view, model)];
  // v1.424.0 review: an answer borrowed from the server's check of ANOTHER
  // model (what routing uses when nothing speaks for this one) says so, in
  // words on the row — never as if this model had been checked.
  const borrowed = borrowedNote(view);
  return (
    <span data-testid="fleet-model-caps" className="inline-flex flex-wrap items-center gap-x-2">
      {chips.map((c) => (
        <span
          key={c.text}
          data-testid="fleet-model-cap"
          data-known={c.known ? "yes" : "no"}
          data-borrowed={c.borrowed ? "yes" : undefined}
          title={c.title}
          className={c.known && !c.borrowed ? "text-zinc-400" : "text-zinc-600"}
        >
          {c.text}
          {c.yes && (
            <>
              {" "}
              <span aria-hidden className="text-tone-success">✓</span>
            </>
          )}
        </span>
      ))}
      {borrowed && (
        <span data-testid="fleet-model-cap-borrowed" className="text-zinc-600">
          ({borrowed})
        </span>
      )}
    </span>
  );
}

/**
 * v1.424.0 review (D8): the per-model Check press — the live check
 * (`POST /fleet/nodes/{id}/verify` with this model's name) asks THIS model
 * whether it calls tools and sees pictures; its answer then speaks for it,
 * on this page and in routing. `nodeId` is the node that serves the name (a
 * proxy's model is asked through its proxy, by the name the proxy serves).
 */
function CheckModel({
  nodeId,
  model,
  onChecked,
}: {
  nodeId: string;
  model: string;
  onChecked?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const check = async () => {
    setBusy(true);
    setNote("");
    try {
      const r = await post<{ tool_use?: boolean | null; error?: string }>(
        `/fleet/nodes/${encodeURIComponent(nodeId)}/verify`,
        { model },
      );
      if (r?.tool_use === true || r?.tool_use === false) {
        setNote("Checked.");
      } else {
        setNote(r?.error ? `Couldn't check ${model}: ${r.error}` : `Couldn't check ${model}.`);
      }
      onChecked?.();
    } catch (e) {
      setNote(`Couldn't check ${model}: ${e instanceof ApiError ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2">
      <button
        type="button"
        data-testid="fleet-model-check"
        onClick={() => void check()}
        disabled={busy}
        className={CALM_ACTION}
        title={`Ask ${model} itself whether it calls tools and sees pictures.`}
      >
        {busy ? "Checking…" : "Check"}
      </button>
      {note && (
        <span data-testid="fleet-model-check-note" role="status" className="text-zinc-500">
          {note}
        </span>
      )}
    </span>
  );
}

/** The capability view a model entry carries (a v1.424.0 daemon), else null. */
function entryCaps(model: FleetModel): CapView | null {
  return "tool_use" in model || "vision" in model ? (model as CapView) : null;
}

function ModelRow({
  model,
  nodeId,
  onChecked,
}: {
  model: FleetModel;
  /** v1.424.0 review: the node serving it (the Check press asks through it). */
  nodeId?: string;
  onChecked?: () => void;
}) {
  const caps = entryCaps(model);
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
            className="inline-flex items-center gap-1 text-tone-success/80"
            title="Kept loaded for good. Ollama writes a year-2318 expiry for this."
          >
            <Pin size={11} /> pinned
          </span>
        )}
        {unload?.kind === "in" && (
          <span
            className="inline-flex items-center gap-1 text-tone-warn/80"
            title="Time until Ollama unloads this model from VRAM"
          >
            <Timer size={11} /> unloads in {unload.text}
          </span>
        )}
        {unload?.kind === "expired" && <span className="text-zinc-600">unloading</span>}
        <CapChips view={caps} model={model.id} />
        {/* Only a daemon that answers per model records a per-model check. */}
        {caps && nodeId && <CheckModel nodeId={nodeId} model={model.id} onChecked={onChecked} />}
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
function ChildRow({ snap, onChecked }: { snap: NodeSnapshot; onChecked?: () => void }) {
  const { text, commands } = normHint(snap.hint, snap.commands);
  const model = snap.models?.[0]?.id ?? null;
  return (
    <div className="rounded-xl border border-white/[0.05] bg-white/[0.015] px-3 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="font-mono text-xs font-medium text-zinc-200">
            {snap.node.alias || nodeName(snap.node)}
          </span>
          {/* v1.424.0 (D8): the PROXY's answer for this name (a live check
              of it, or the proxy's own statement) — never the endpoint's
              one flag. An older daemon: the child's own flag, as before. */}
          {snap.model_capabilities ? (
            <span className="inline-flex flex-wrap items-center gap-x-2 text-[11px] leading-none text-zinc-500">
              <CapChips
                view={capabilityOf(snap, snap.node.alias || model || "")}
                model={snap.node.alias || model || ""}
              />
              {/* v1.424.0 review: asked through its PROXY, by the name the
                  proxy serves (a proxy's model is never verified alone). */}
              {snap.node.parent_id && (snap.node.alias || model) && (
                <CheckModel
                  nodeId={snap.node.parent_id}
                  model={snap.node.alias || model || ""}
                  onChecked={onChecked}
                />
              )}
            </span>
          ) : (
            snap.node.tool_use && (
              <span
                className="inline-flex items-center gap-1 rounded-full border border-white/10 px-1.5 py-0.5 text-[11px] leading-none text-zinc-400"
                title="Checked: this model server calls tools."
              >
                tools <span className="text-tone-success">✓</span>
              </span>
            )
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
          <span className="text-zinc-600">no backing server reported</span>
        )}
      </div>
      {snap.error && <div className="mt-1.5 text-[11px] text-tone-danger/80">{snap.error}</div>}
      {text && <div className="mt-1.5 text-[11px] text-tone-warn/80">{text}</div>}
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
/**
 * Rename + Remove for one endpoint (v1.100.0).
 *
 * The backend has had `PATCH /fleet/nodes/{id}` (label, routable, default_model)
 * and `DELETE /fleet/nodes/{id}` all along; this page only ever called
 * `POST /fleet/nodes`, so the fleet was add-only — a box you retired stayed
 * pinned to the page forever with no way to rename or remove it.
 */
function NodeActions({ snap, onChanged }: { snap: NodeSnapshot; onChanged: () => void }) {
  const node = snap.node;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(node.label || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [cleared, setCleared] = useState<string[] | null>(null);
  // v1.449.0 (item 24): when this endpoint is what answers chats by default,
  // the confirm says so and names the replacement (the daemon's own rule),
  // read once before any press; the removal's answer says what happened.
  const { health, refresh: refreshDaemon } = useDaemon();
  const isDefault =
    Boolean(health?.default_provider) &&
    (health?.default_provider === `fleet-${node.id}` ||
      (node.source === "config" && health?.default_provider === node.id));
  const [impact, setImpact] = useState<DefaultImpact | null>(null);
  const [moved, setMoved] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    if (!isDefault) {
      setImpact(null);
      return;
    }
    void fetchDefaultImpact([node.id]).then((r) => {
      if (live) setImpact(r);
    });
    return () => {
      live = false;
    };
  }, [isDefault, node.id]);

  async function save() {
    const label = draft.trim();
    if (!label || label === node.label) {
      setEditing(false);
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await patch(`/fleet/nodes/${encodeURIComponent(node.id)}`, { label });
      setEditing(false);
      onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setErr(null);
    try {
      const res = await del<{ ok: boolean; cleared_settings?: string[] }>(
        `/fleet/nodes/${encodeURIComponent(node.id)}`,
      );
      // Removing a config-seeded endpoint clears its Settings keys and retires
      // the matching provider — say so rather than doing it silently.
      if (res?.cleared_settings?.length) setCleared(res.cleared_settings);
      // v1.449.0 (item 24): the daemon moved the default BEFORE the server
      // went; say where it landed, in its own words.
      const words = defaultReplacedWords(res);
      if (words) {
        setMoved(words);
        refreshDaemon();
      }
      onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  }

  // v1.330.0: a model a proxy reports is rebuilt from the proxy's own list on
  // every pass, so the daemon refuses (409) to remove, rename, re-detect or
  // verify it on its own while its proxy is a node. Say where the real
  // control is instead of offering one that cannot work.
  if (node.parent_id) {
    return (
      <span
        data-testid="fleet-child-note"
        className="text-[11px] text-zinc-500"
        title={`Reported by the proxy ${node.parent_id}`}
      >
        Remove the proxy to remove this.
      </span>
    );
  }

  if (editing) {
    return (
      <span className="flex items-center gap-1.5">
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void save();
            if (e.key === "Escape") setEditing(false);
          }}
          disabled={busy}
          aria-label="Server name"
          className="w-44 rounded-md border border-white/10 bg-ink-950 px-2 py-1 text-xs text-zinc-200 outline-none focus:border-accent/40"
        />
        <button
          type="button"
          onClick={() => void save()}
          disabled={busy}
          className="btn-soft min-h-7 px-2.5 py-1 text-xs"
        >
          {busy ? "…" : "Save"}
        </button>
      </span>
    );
  }

  return (
    <span className="flex flex-wrap items-center justify-end gap-1.5">
      {err && <span className="text-[11px] text-tone-danger">{err}</span>}
      {cleared && (
        <span className="text-[11px] text-tone-warn" title={cleared.join(", ")}>
          cleared {cleared.length} setting{cleared.length === 1 ? "" : "s"}
        </span>
      )}
      {isDefault && <DefaultImpactLine impact={impact} testId={`fleet-default-impact-${node.id}`} />}
      {moved && (
        <span role="status" data-testid={`fleet-default-moved-${node.id}`} className="text-[11px] text-tone-warn">
          {moved}
        </span>
      )}
      <button
        type="button"
        onClick={() => {
          setDraft(node.label || "");
          setEditing(true);
        }}
        title="Rename this model server"
        aria-label="Rename this model server"
        className={CALM_ICON_ACTION}
      >
        <Pencil size={12} />
      </button>
      <ConfirmButton
        variant="calm"
        label="Remove"
        onConfirm={remove}
        title={
          node.source === "config"
            ? "Remove this model server (also clears its address in Settings, so it leaves every model list)"
            : "Remove this model server"
        }
      />
    </span>
  );
}

function NodeCard({
  snap,
  series,
  onChanged,
}: {
  snap: NodeSnapshot;
  series: (number | null)[];
  onChanged: () => void;
}) {
  const m = snap.metrics ?? null;
  const supported = snap.metrics_supported !== false;
  const reason = snap.metrics_reason ?? null;
  const kv = kvCache(m);
  const tps = genTps(snap.rates);
  const { text: hintText, commands } = normHint(snap.hint, snap.commands);
  const models = snap.models ?? [];
  const children = snap.children ?? [];
  const secondhand = snap.evidence === "proxy";
  const unchecked = isUnchecked(snap);

  return (
    <Card
      // v1.329.0 (calm K2): the header WRAPS. With J3's longer
      // "OpenAI-compatible" badge, the name + badge + host and the latency +
      // status + rename + Remove no longer fit one 358px row on a phone, and
      // the Card's single-row header pushed Remove past the card's edge
      // (clipped to "Remov"). Now the controls drop to their own line,
      // right-aligned, and a long name or host truncates with its title.
      className="[&>header>h2]:min-w-0 [&>header]:flex-wrap"
      title={
        <span data-testid="fleet-node-title" className="flex min-w-0 flex-wrap items-center gap-2">
          <span
            className="min-w-0 max-w-full truncate text-zinc-100"
            title={nodeName(snap.node)}
          >
            {nodeName(snap.node)}
          </span>
          {/* v1.329.0: the way this node CHATS (the saved protocol wins over
              the detected kind, so an Anthropic endpoint whose server also
              answers /v1/models is not called "OpenAI-compatible"); what
              detection saw stays in the title. keepCase so "vLLM" is not
              shown as "VLLM". */}
          <span
            data-testid="fleet-node-chat"
            title={chatHint(snap.node)}
            className="inline-flex whitespace-nowrap"
          >
            <Badge variant="calm" value={chatLabel(snap.node)} tone={chatTone(snap.node)} keepCase />
          </span>
          <span
            className="min-w-0 max-w-full truncate font-mono text-[11px] font-normal text-zinc-600"
            title={snap.node.base_url}
          >
            {hostOf(snap.node.base_url)}
          </span>
        </span>
      }
      icon={<Server size={15} />}
      right={
        <span
          data-testid="fleet-node-controls"
          className="ml-auto flex max-w-full flex-wrap items-center justify-end gap-2"
        >
          {fmtLatency(snap.latency_ms) && (
            <span
              className="text-[11px] tabular-nums text-zinc-500"
              title="Round-trip time of the last probe"
            >
              {fmtLatency(snap.latency_ms)}
            </span>
          )}
          <StatusPill snap={snap} />
          <NodeActions snap={snap} onChanged={onChanged} />
        </span>
      }
    >
      {/* Why we can't reach it — the actionable part, above everything else. */}
      {(snap.error || hintText) && (
        <div className="mb-4 rounded-xl border border-tone-warn/20 bg-tone-warn/[0.05] px-3 py-2.5">
          {snap.error && <div className="text-xs text-tone-danger">{snap.error}</div>}
          {hintText && <div className="mt-1 text-xs text-tone-warn">{hintText}</div>}
          <Commands commands={commands} />
        </div>
      )}

      {/* Serving metrics. A null NEVER becomes a zero. v1.329.0: a node no
          probe has looked at yet has nothing to show, and says so plainly
          rather than "exposes no serving metrics" (which would be a claim). */}
      {unchecked ? (
        <div
          data-testid="fleet-node-unchecked"
          className="rounded-xl border border-white/[0.05] bg-white/[0.015] px-3 py-2.5 text-xs text-zinc-500"
        >
          Not checked yet. Refresh checks it now.
        </div>
      ) : supported ? (
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
              reason ?? "Needs two readings in a row. Keep this page open a moment."
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
          {reason || "This model server reports no serving numbers."}
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
              <ModelRow key={mod.id} model={mod} nodeId={snap.node.id} onChecked={onChanged} />
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
              reported by the proxy, not probed directly
            </span>
          </div>
          <div className="space-y-2">
            {children.map((child) => (
              <ChildRow key={child.node.id} snap={child} onChecked={onChanged} />
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
 * v1.314.0 (UX wave 2): a comparison model in words. Claude ids read as their
 * product names ("claude-opus-4-8" -> "Claude Opus 4.8"); anything else keeps
 * its id after the provider's plain name. Never invents a model.
 */
function baselineName(provider: string, model: string): string {
  const claude = /^claude-(opus|sonnet|haiku)-(\d+)(?:-(\d+))?(?:-\d{8})?$/i.exec(model);
  if (claude) {
    const family = claude[1].charAt(0).toUpperCase() + claude[1].slice(1).toLowerCase();
    return `Claude ${family} ${claude[2]}${claude[3] ? `.${claude[3]}` : ""}`;
  }
  const who = providerDisplay(provider);
  return [who, model].filter(Boolean).join(" ");
}

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
  // v1.314.0: the baseline in words ("Claude Opus 4.8"); the raw
  // provider:model id stays in the title — it is what the estimate priced.
  const baselineWords = baselineName(provider, model);
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
          No local and cloud split recorded yet. Run something on a local model and
          it shows up here.
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
              <div className="text-xs text-tone-success">
                est. {fmtUsd(avoided)} avoided vs{" "}
                <span className="text-tone-success" title={`Priced as ${baseline}`}>
                  {baselineWords}
                </span>
              </div>
              {/* v1.314.0: the daemon's basis line names the raw
                  provider:model id ("…on anthropic:claude-opus-4-8 at list
                  price"). It is the record of what the estimate priced, so it
                  stays — one press down, after a sentence in words. */}
              {usage?.basis && (
                <details className="mt-1 text-[11px] text-zinc-600">
                  <summary className="cursor-pointer select-none hover:text-zinc-400">
                    How this is estimated
                  </summary>
                  <p className="mt-1">
                    What the tokens that ran on local models would have cost on{" "}
                    {baselineWords} at its list price.
                  </p>
                  <p className="mt-0.5 break-words font-mono text-[10px] text-zinc-600">
                    {usage.basis}
                  </p>
                </details>
              )}
            </div>
          ) : (
            <div className="text-xs text-zinc-600">
              No avoided-spend estimate: Iron Jarvis has no comparison model to
              price local tokens against.
            </div>
          )}
          {byNode.length > 0 && baseline && (
            <div className="space-y-1 border-t hairline pt-2.5">
              {byNode.slice(0, 4).map((n) => (
                <div
                  key={n.node_id}
                  className="flex items-baseline justify-between gap-3 text-[11px]"
                >
                  <span
                    className={`truncate ${n.retired ? "text-zinc-600" : "text-zinc-400"}`}
                    title={
                      n.retired
                        ? "This model server was removed. Its past usage is kept here."
                        : undefined
                    }
                  >
                    {n.label || n.node_id}
                    {n.models?.length ? (
                      <span
                        className="font-mono text-zinc-600"
                        /* HISTORY, not the live catalogue: these are models this
                           endpoint has run. The current list comes from probing
                           (snapshot.models), and conflating the two is why a
                           single old gemma run looked like a model on offer. */
                        title={`Models run here: ${n.models.join(", ")}`}
                      >
                        {" "}· {n.models[0]}
                        {n.models.length > 1 ? ` +${n.models.length - 1}` : ""}
                      </span>
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
/*  Add a model server: ONE form, on Settings › Connections                   */
/* -------------------------------------------------------------------------- */

/** v1.455.0 (readiness Wave H item 31): the address the one add form lives at
 *  (Connections' model-server card, `useFocusRef("endpoints")`). */
const ADD_MODEL_SERVER_HREF = "/connections?focus=endpoints";

/**
 * v1.455.0 (readiness Wave H item 31): a model server is added in ONE place.
 * This page had its own add form beside the Connections one — two forms, two
 * ways of saving a key, two sets of words — so a person did not know which
 * to use. The form now lives only on Settings › Connections (it reads the
 * server's models, takes its key and checks it answers; what it saves lists
 * here on its own), and this page links to it.
 */
function AddModelServerLink() {
  return (
    <Card title="Add a model server" icon={<Plus size={15} />}>
      <div data-testid="fleet-add-link" className="space-y-2 text-xs leading-relaxed text-zinc-400">
        <p>
          Model servers are added in one place: Settings › Connections, where Iron Jarvis reads the server&apos;s
          models, takes its key if it asks for one, and checks it answers. It shows up here on its own.
        </p>
        <Link href={ADD_MODEL_SERVER_HREF} className="btn-soft inline-flex w-fit items-center gap-1.5 py-1.5 text-xs">
          <Plus size={13} aria-hidden /> Add a model server
        </Link>
        <div>
          <AllConnectionsLink testId="fleet-all-connections" />
        </div>
      </div>
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
          title="Model servers"
          subtitle="Every model server you can reach: what is loaded, what is serving, and what we cannot see."
          actions={
            <div className="flex items-center gap-2">
              {/* v1.314.0: say what the chip means ("live" was unexplained).
                  v1.330.0: the calm Badge (no border, no fill, the dot carries
                  the tone), not a hand-made bordered pill beside the calm
                  Refresh. */}
              <Badge
                variant="calm"
                keepCase
                data-testid="fleet-sampling-chip"
                tone={sampling?.active ? "green" : "slate"}
                value={sampling?.active ? "Live updates" : "Idle"}
                title={
                  sampling?.active
                    ? `Sampling every ${interval ?? POLL_MS / 1000}s${
                        lease !== null ? ` · lease ${Math.round(lease)}s left` : ""
                      }`
                    : `Not sampling right now${
                        interval !== null ? `. Idles at ${interval}s` : ""
                      }`
                }
              />
              {/* v1.447.0 (calm sweep): the header's refresh arrow, named
                  "Refresh" with its tip. */}
              <IconButton
                label="Refresh"
                tip="Check every model server again now"
                testId="fleet-refresh"
                icon={<RefreshCw size={15} aria-hidden className={refreshing ? "animate-spin" : ""} />}
                onClick={refresh}
                disabled={refreshing}
              />
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
          <LoadFailedLine thing="Your model servers" error={error} onRetry={reload} />
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
          {/* v1.314.0: a quiet chip with a sentence; the daemon's own words
              for the route stay in its title. */}
          <div
            className="inline-flex max-w-full items-center gap-2 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-1 text-xs text-zinc-400"
            title={data?.code_route?.effective?.why || undefined}
          >
            <Cpu size={13} className="shrink-0 text-accent-soft/70" />
            <span className="min-w-0">{route}</span>
          </div>
        </Reveal>
      )}

      <Reveal>
        <UsageStrip usage={usage} />
      </Reveal>

      {firstLoad ? (
        <Reveal>
          <Card title="Your model servers" icon={<Server size={15} />}>
            <SkeletonRows rows={4} />
          </Card>
        </Reveal>
      ) : roots.length === 0 ? (
        <Reveal>
          <Card title="Your model servers" icon={<Server size={15} />}>
            {/* v1.314.0 (UX wave 2): the primary way forward is the place a
                user actually adds an endpoint — Connections' custom-endpoint
                card, whose form posts /fleet/nodes (so it lists here).
                Settings' Ollama/custom URLs stay as the second way in. */}
            <Empty
              icon={<Server size={26} />}
              title="No machines yet"
              action={{ label: "Add a model server", href: ADD_MODEL_SERVER_HREF }}
              secondary={{ label: "Open Settings", href: "/settings" }}
            >
              Add a model server on Connections, or set the Ollama or custom URL in Settings.
              They show up here on their own.
            </Empty>
          </Card>
        </Reveal>
      ) : (
        roots.map((snap) => (
          <Reveal key={snap.node.id}>
            <NodeCard snap={snap} series={history[snap.node.id]?.points ?? []} onChanged={reload} />
          </Reveal>
        ))
      )}

      <Reveal>
        <AddModelServerLink />
      </Reveal>
    </PageShell>
  );
}
