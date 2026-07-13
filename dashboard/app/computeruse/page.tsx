"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ShieldAlert,
  ShieldCheck,
  Power,
  PowerOff,
  Plus,
  X,
  Check,
  Globe,
  Eye,
  MousePointerClick,
  Keyboard,
  Camera,
  Navigation,
  ScanEye,
  Footprints,
  Lock,
  UserCheck,
  Inbox,
  RefreshCw,
  MonitorPlay,
  History,
  ChevronDown,
  ChevronRight,
  type LucideIcon,
} from "lucide-react";
import { post, ApiError } from "@/lib/api";
import { useApi, usePolledApi } from "@/lib/useApi";
import { useEvents } from "@/lib/useEvents";
import type { ComputerUseStatus, Approval } from "@/lib/types";
import {
  Card,
  OfflineHint,
  SkeletonRows,
  ErrorNote,
  Empty,
  LoaderInline,
  Dot,
} from "@/components/ui";
import { timeAgo } from "@/lib/format";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

/* -------------------------------------------------------------------------- */
/*  Local contracts (daemon additions not yet in lib/types)                    */
/* -------------------------------------------------------------------------- */

/** Approvals now carry the page screenshot taken when approval was requested
 *  ("" when absent). Optional so older daemons without the field still type. */
type ApprovalWithScreenshot = Approval & { screenshot_b64?: string };

/** GET /computeruse/screen — the browser's most recent screenshot, refreshed
 *  after every page-changing agent action. */
interface LiveScreen {
  image_b64: string;
  url: string;
  at: string;
}

interface LiveScreenState {
  screen: LiveScreen | null;
  enabled: boolean;
}

/** GET /computeruse/runs?limit=20 — recent run history, newest first. */
interface RunSummary {
  id: string;
  task: string;
  status: string;
  ok: boolean | null;
  started_at: string | null;
  finished_at: string | null;
}

/** GET /computeruse/runs/{id} — the full run row incl. its recorded trace. */
interface RunDetail {
  id: string;
  task: string;
  status: string;
  steps: number;
  trace_json: string;
  created_at?: string | null;
  finished_at?: string | null;
}

/** One TraceRecorder entry (computeruse/trace.py): monotonic seq + kind, with
 *  kind-specific fields (action/result/screenshot/error/artifact/note/approval). */
interface TraceEntry {
  seq?: number;
  ts?: string;
  kind?: string;
  [key: string]: unknown;
}

/* -------------------------------------------------------------------------- */
/*  Action vocabulary (mirrors the daemon's ActionKind / READ_ONLY_KINDS)      */
/* -------------------------------------------------------------------------- */

interface ActionMeta {
  kind: string;
  label: string;
  icon: LucideIcon;
  /** Read-only actions only observe remote state — the safe default. */
  readonly: boolean;
  hint: string;
}

const ACTIONS: ActionMeta[] = [
  { kind: "navigate", label: "navigate", icon: Navigation, readonly: true, hint: "Open an allowlisted URL" },
  { kind: "read", label: "read", icon: Eye, readonly: true, hint: "Snapshot the page DOM/a11y tree" },
  { kind: "extract", label: "extract", icon: ScanEye, readonly: true, hint: "Pull structured data from the page" },
  { kind: "screenshot", label: "screenshot", icon: Camera, readonly: true, hint: "Capture a screenshot artifact" },
  { kind: "wait", label: "wait", icon: Footprints, readonly: true, hint: "Passive wait between steps" },
  { kind: "click", label: "click", icon: MousePointerClick, readonly: false, hint: "Click an element — can mutate state" },
  { kind: "type", label: "type", icon: Keyboard, readonly: false, hint: "Type text — credentials/PII require approval" },
  { kind: "screenshot_click", label: "screenshot_click", icon: Camera, readonly: false, hint: "Pixel/visual click fallback — higher risk" },
];

/* -------------------------------------------------------------------------- */
/*  Domain normalisation                                                       */
/* -------------------------------------------------------------------------- */

function normalizeDomain(raw: string): string {
  return raw
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, "")
    .replace(/\/.*$/, "")
    .replace(/^\.+/, "");
}

function sameSet(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const sb = new Set(b);
  return a.every((x) => sb.has(x));
}

/* -------------------------------------------------------------------------- */
/*  Read-only chip                                                             */
/* -------------------------------------------------------------------------- */

function Chip({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 rounded-xl border border-white/[0.08] bg-white/[0.02] px-3 py-2">
      <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-400">
        {label}
      </span>
      <span className="font-mono text-sm text-zinc-200">{value}</span>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Approval card                                                              */
/* -------------------------------------------------------------------------- */

function prettyAction(actionJson: string): { kind: string; detail: string } {
  try {
    const a = JSON.parse(actionJson) as Record<string, unknown>;
    const kind = String(a.kind ?? "action");
    const sel = (a.selector ?? {}) as Record<string, unknown>;
    const parts: string[] = [];
    if (sel.role) parts.push(`role=${String(sel.role)}`);
    if (sel.name) parts.push(`"${String(sel.name)}"`);
    if (sel.text) parts.push(`text="${String(sel.text)}"`);
    if (sel.css) parts.push(`css=${String(sel.css)}`);
    if (a.value) parts.push(`value="${String(a.value)}"`);
    return { kind, detail: parts.join("  ") || "—" };
  } catch {
    return { kind: "action", detail: actionJson };
  }
}

function ApprovalCard({
  approval,
  onResolved,
}: {
  approval: ApprovalWithScreenshot;
  onResolved: () => void;
}) {
  const [busy, setBusy] = useState<"approve" | "deny" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const { kind, detail } = prettyAction(approval.action_json);
  const screenshot = approval.screenshot_b64 ?? "";

  async function resolve(verdict: "approve" | "deny") {
    setBusy(verdict);
    setErr(null);
    try {
      await post(`/computeruse/approvals/${approval.id}/${verdict}`);
      onResolved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
      setBusy(null);
    }
  }

  return (
    <div className="card-surface flex flex-col gap-3 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span className="grid h-9 w-9 place-items-center rounded-xl border border-amber-500/25 bg-amber-500/[0.08]">
            <ShieldAlert size={17} className="text-amber-300" />
          </span>
          <div>
            <div className="flex items-center gap-2">
              <span className="rounded-md border border-violet-500/25 bg-violet-500/10 px-2 py-0.5 font-mono text-xs font-medium text-violet-200">
                {kind}
              </span>
              <span className="text-[11px] text-zinc-500">run {approval.run_id}</span>
            </div>
            <div className="mt-1 text-sm text-amber-100/90">{approval.reason}</div>
          </div>
        </div>
      </div>

      {screenshot !== "" && (
        <figure className="space-y-1.5">
          <button
            type="button"
            onClick={() => setExpanded((x) => !x)}
            title={expanded ? "Click to shrink" : "Click to expand"}
            className="block w-full overflow-hidden rounded-lg border border-white/[0.08] bg-black/30 transition-colors hover:border-accent/30"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={`data:image/png;base64,${screenshot}`}
              alt="Screenshot of the page when this approval was requested"
              className={`w-full object-contain ${expanded ? "" : "max-h-56"}`}
            />
          </button>
          <figcaption className="flex items-center justify-between text-[11px] text-zinc-500">
            <span>The page at request time</span>
            <span className="text-zinc-600">
              {expanded ? "click to shrink" : "click to expand"}
            </span>
          </figcaption>
        </figure>
      )}

      <pre className="overflow-x-auto rounded-lg border border-white/[0.06] bg-black/30 px-3 py-2 font-mono text-[11px] leading-relaxed text-zinc-300">
        {detail}
      </pre>

      {err && <ErrorNote>{err}</ErrorNote>}

      <div className="flex items-center gap-2">
        <button
          onClick={() => resolve("approve")}
          disabled={busy !== null}
          className="btn-accent flex-1 py-1.5 text-xs"
        >
          {busy === "approve" ? <LoaderInline label="Approving…" /> : (<><Check size={14} /> Approve</>)}
        </button>
        <button
          onClick={() => resolve("deny")}
          disabled={busy !== null}
          className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-xl border border-white/10 px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:border-rose-500/40 hover:text-rose-300 disabled:opacity-40"
        >
          {busy === "deny" ? <LoaderInline label="Denying…" /> : (<><X size={14} /> Deny</>)}
        </button>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Run history                                                                */
/* -------------------------------------------------------------------------- */

/** Badge colors for a run's terminal state: completed = good, failed/blocked =
 *  bad, anything still in flight (running / awaiting_approval) = amber. */
function runBadgeClass(status: string, ok: boolean | null): string {
  if (ok === true || status === "completed")
    return "border-emerald-500/25 bg-emerald-500/10 text-emerald-300";
  if (ok === false || status === "failed" || status === "blocked")
    return "border-rose-500/25 bg-rose-500/10 text-rose-300";
  return "border-amber-500/25 bg-amber-500/10 text-amber-300";
}

function runBadgeLabel(status: string, ok: boolean | null): string {
  if (status === "running" || status === "awaiting_approval") return status;
  if (ok === true || status === "completed") return `${status} · ok`;
  if (ok === false || status === "failed" || status === "blocked")
    return `${status} · failed`;
  return status;
}

const TRACE_KIND_COLOR: Record<string, string> = {
  action: "text-accent-soft",
  result: "text-zinc-300",
  screenshot: "text-violet-300",
  error: "text-rose-300",
  artifact: "text-amber-300",
  note: "text-zinc-400",
  approval: "text-amber-300",
};

/** Selector/value summary shared by action + result entries (both embed the
 *  serialized Action — kind, selector {role,name,text,css}, value). */
function describeTraceAction(a: unknown): string {
  if (!a || typeof a !== "object") return "";
  const action = a as Record<string, unknown>;
  const parts: string[] = [String(action.kind ?? "action")];
  const sel = action.selector;
  if (sel && typeof sel === "object") {
    const s = sel as Record<string, unknown>;
    if (s.role) parts.push(`role=${String(s.role)}`);
    if (s.name) parts.push(`"${String(s.name)}"`);
    if (s.text) parts.push(`text="${String(s.text)}"`);
    if (s.css) parts.push(`css=${String(s.css)}`);
  }
  if (action.value !== undefined && action.value !== null && action.value !== "")
    parts.push(`value="${String(action.value)}"`);
  return parts.join(" ");
}

/** One-line summary for a trace entry, keyed on its kind. */
function traceSummary(e: TraceEntry): string {
  switch (e.kind) {
    case "action": {
      const bits = [describeTraceAction(e.action)];
      if (e.checkpoint) bits.push(`→ ${String(e.checkpoint)}`);
      return bits.filter(Boolean).join(" ");
    }
    case "result": {
      const bits = [describeTraceAction(e.action)];
      if (e.ok === true) bits.push("ok");
      if (e.ok === false) bits.push("FAILED");
      if (e.error) bits.push(String(e.error));
      else if (e.output) bits.push(String(e.output).slice(0, 160));
      if (e.url) bits.push(String(e.url));
      return bits.filter(Boolean).join(" · ");
    }
    case "screenshot":
      return `${String(e.label ?? "screenshot")} → ${String(e.path ?? "")}`;
    case "error":
      return `${String(e.message ?? "")}${e.where ? ` (${String(e.where)})` : ""}`;
    case "artifact":
      return `${String(e.name ?? "")} → ${String(e.path ?? "")}`;
    case "note":
      return String(e.message ?? "");
    case "approval":
      return `${String(e.status ?? "")} — ${String(e.reason ?? "")}`;
    default:
      return "";
  }
}

/** Inline detail for one expanded run: fetches the EXISTING per-run endpoint
 *  and renders the recorded trace compactly (seq · kind · summary). */
function RunDetailView({ runId }: { runId: string }) {
  const { data, error, loading } = useApi<RunDetail>(`/computeruse/runs/${runId}`);
  const trace = useMemo<TraceEntry[]>(() => {
    if (!data?.trace_json) return [];
    try {
      const parsed: unknown = JSON.parse(data.trace_json);
      return Array.isArray(parsed) ? (parsed as TraceEntry[]) : [];
    } catch {
      return [];
    }
  }, [data]);

  if (loading && !data) return <SkeletonRows rows={2} />;
  if (error)
    return (
      <div className="py-1 text-xs text-zinc-500">
        Couldn&apos;t load the trace — {error.message}
      </div>
    );
  if (trace.length === 0)
    return <div className="py-1 text-xs text-zinc-600">No trace recorded for this run.</div>;

  return (
    <ol className="max-h-64 space-y-0.5 overflow-y-auto pr-1 font-mono text-[11px]">
      {trace.map((t, i) => {
        const summary = traceSummary(t);
        return (
          <li
            key={i}
            className="flex items-baseline gap-2.5 rounded-md px-2 py-1 hover:bg-white/[0.03]"
          >
            <span className="w-7 shrink-0 text-right tabular-nums text-zinc-600">
              {t.seq ?? i + 1}
            </span>
            <span
              className={`w-20 shrink-0 font-medium ${
                TRACE_KIND_COLOR[String(t.kind)] ?? "text-zinc-300"
              }`}
            >
              {String(t.kind ?? "?")}
            </span>
            <span className="min-w-0 flex-1 truncate text-zinc-400" title={summary}>
              {summary}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/* -------------------------------------------------------------------------- */
/*  Best-practices list                                                        */
/* -------------------------------------------------------------------------- */

const BEST_PRACTICES: { icon: LucideIcon; text: string }[] = [
  { icon: Eye, text: "DOM / accessibility-first targeting — role + name + label text, never raw pixel coordinates." },
  { icon: Camera, text: "Screenshots are a labelled fallback only, used when the accessibility tree can't locate a target." },
  { icon: ShieldAlert, text: "All page content is treated as untrusted; the model never decides what's safe — the policy does." },
  { icon: Check, text: "Every step is verified programmatically against an expected checkpoint, not by asking a model." },
  { icon: ScanEye, text: "Full action/result/screenshot tracing is recorded for every run for audit and replay." },
  { icon: Footprints, text: "Hard step + retry budgets stop runaway loops before they can wander off-task." },
];

/* -------------------------------------------------------------------------- */
/*  Page                                                                       */
/* -------------------------------------------------------------------------- */

export default function ComputerUsePage() {
  const { data, error, loading, reload } = useApi<ComputerUseStatus>("/computeruse");
  const approvalsState = usePolledApi<{ approvals: ApprovalWithScreenshot[] }>(
    "/computeruse/approvals",
    4000,
  );

  const offline = (error && error.status === 0) || (approvalsState.error?.status === 0);

  // Local draft of the allowlists, seeded from (and re-seeded after) each load.
  const [domains, setDomains] = useState<string[]>([]);
  const [actions, setActions] = useState<string[]>([]);
  const [domainInput, setDomainInput] = useState("");
  const [saving, setSaving] = useState<"toggle" | "save" | null>(null);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  useEffect(() => {
    if (data) {
      setDomains(data.domain_allowlist ?? []);
      setActions(data.action_allowlist ?? []);
    }
  }, [data]);

  const enabled = data?.enabled ?? false;

  // Live view: poll the latest browser screenshot every 2s, but ONLY while
  // computer use is enabled — a null path disables both the fetch and the
  // interval inside usePolledApi, so a disabled install makes zero requests.
  const screenState = usePolledApi<LiveScreenState>(
    enabled ? "/computeruse/screen" : null,
    2000,
  );
  const screen = enabled ? (screenState.data?.screen ?? null) : null;

  const dirty = useMemo(
    () =>
      !!data &&
      (!sameSet(domains, data.domain_allowlist ?? []) ||
        !sameSet(actions, data.action_allowlist ?? [])),
    [data, domains, actions],
  );

  async function apply(nextEnabled: boolean, which: "toggle" | "save") {
    setSaving(which);
    setSaveErr(null);
    try {
      await post("/computeruse/enable", {
        enabled: nextEnabled,
        domain_allowlist: domains,
        action_allowlist: actions,
      });
      reload();
    } catch (e) {
      setSaveErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSaving(null);
    }
  }

  function addDomain() {
    const d = normalizeDomain(domainInput);
    if (!d) return;
    if (!domains.includes(d)) setDomains((xs) => [...xs, d]);
    setDomainInput("");
  }

  function toggleAction(kind: string) {
    setActions((xs) =>
      xs.includes(kind) ? xs.filter((k) => k !== kind) : [...xs, kind],
    );
  }

  const approvals = approvalsState.data?.approvals ?? [];

  // Run history. A 404 just means the daemon predates GET /computeruse/runs
  // (not restarted since the update) — useApi captures it, data stays null,
  // and the section renders its quiet empty state instead of crashing.
  const runsState = useApi<{ runs: RunSummary[] }>("/computeruse/runs?limit=20");
  const runs = runsState.data?.runs ?? [];
  const [expandedRun, setExpandedRun] = useState<string | null>(null);

  // Auto-refresh the list when a run reaches a terminal state. The buffer is
  // newest-first, so the first computeruse.run_finished id changes on each new
  // finish — a stable key that re-triggers exactly one reload per event.
  const { events } = useEvents(30);
  const lastFinishedEventId = useMemo(
    () => events.find((e) => e.type === "computeruse.run_finished")?.id ?? null,
    [events],
  );
  const reloadRuns = runsState.reload;
  useEffect(() => {
    if (lastFinishedEventId) reloadRuns();
  }, [lastFinishedEventId, reloadRuns]);

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Computer Use"
          subtitle="Let agents drive a real browser to finish tasks — gated behind allowlists and your explicit approval, with a live view so you can watch the agent work."
          actions={
            data ? (
              <span
                className={`inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium ${
                  enabled
                    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                    : "border-zinc-500/25 bg-zinc-500/10 text-zinc-400"
                }`}
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    enabled
                      ? "bg-emerald-400 shadow-[0_0_8px_2px_rgba(52,211,153,0.5)] animate-pulse-glow"
                      : "bg-zinc-500"
                  }`}
                />
                {enabled ? "Enabled" : "Disabled"}
              </span>
            ) : null
          }
        />
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      {/* Safety explainer — lead, slightly cautionary. */}
      <Reveal>
        <div className="flex items-start gap-3.5 rounded-2xl border border-amber-500/25 bg-amber-500/[0.06] px-5 py-4">
          <ShieldAlert size={22} className="mt-0.5 shrink-0 text-amber-300" />
          <div className="space-y-1.5 text-sm">
            <div className="font-semibold text-amber-200">
              Read this before you turn it on.
            </div>
            <p className="leading-relaxed text-amber-100/80">
              Computer use lets an agent control a browser (and, later, the desktop) on your
              behalf. It is{" "}
              <span className="font-semibold text-amber-100">off by default</span>. Only enable it
              on an <span className="font-semibold text-amber-100">isolated, disposable VM</span> —
              never on a machine with logged-in accounts or files you can&apos;t afford to lose.
              The agent can only reach domains and perform actions you put on the allowlists below,
              and anything sensitive — typing credentials, payments, or destructive/transactional
              clicks — <span className="font-semibold text-amber-100">pauses for your explicit
              approval</span>.
            </p>
          </div>
        </div>
      </Reveal>

      {/* Live view — only exists while computer use is enabled */}
      {enabled && (
        <Reveal>
          <Card
            title="Live view — what the agent sees"
            icon={<MonitorPlay size={15} />}
            right={
              <span className="flex items-center gap-2 text-[11px] text-zinc-500">
                <RefreshCw size={12} className="text-accent-soft/70" /> refreshes every 2s
              </span>
            }
          >
            {screen ? (
              <div className="space-y-2.5">
                <div className="overflow-hidden rounded-xl border border-accent/30 shadow-[0_0_28px_-6px_rgb(var(--accent-rgb)/0.35)]">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={`data:image/png;base64,${screen.image_b64}`}
                    alt={`Live browser view — ${screen.url}`}
                    className="w-full"
                  />
                </div>
                <div className="flex items-center gap-2.5 text-[11px] text-zinc-500">
                  <Dot on />
                  <span
                    className="min-w-0 flex-1 truncate font-mono text-xs text-zinc-300"
                    title={screen.url}
                  >
                    {screen.url}
                  </span>
                  <span className="shrink-0">updated {timeAgo(screen.at)}</span>
                </div>
              </div>
            ) : (
              <Empty icon={<MonitorPlay size={28} />}>
                No activity yet — the view appears the moment an agent touches the browser.
              </Empty>
            )}
          </Card>
        </Reveal>
      )}

      {/* Status + enable toggle */}
      <Reveal>
        <Card title="Status & enablement" icon={<Power size={15} />}>
          {loading && !data ? (
            <SkeletonRows rows={3} />
          ) : (
            <div className="space-y-5">
              {/* The prominent toggle */}
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-3">
                  <span
                    className={`grid h-11 w-11 place-items-center rounded-xl border ${
                      enabled
                        ? "border-emerald-500/30 bg-emerald-500/10"
                        : "border-white/[0.08] bg-white/[0.03]"
                    }`}
                  >
                    {enabled ? (
                      <ShieldCheck size={21} className="text-emerald-300" />
                    ) : (
                      <PowerOff size={21} className="text-zinc-500" />
                    )}
                  </span>
                  <div>
                    <div className="text-sm font-semibold text-zinc-100">
                      Computer use is {enabled ? "enabled" : "disabled"}
                    </div>
                    <div className="text-xs text-zinc-500">
                      {enabled
                        ? "Agents may drive the browser within the limits below."
                        : "Agents cannot drive a browser or desktop."}
                    </div>
                  </div>
                </div>
                <button
                  onClick={() => apply(!enabled, "toggle")}
                  disabled={saving !== null || !data}
                  className={
                    enabled
                      ? "inline-flex items-center justify-center gap-2 rounded-xl border border-rose-500/30 bg-rose-500/[0.08] px-4 py-2 text-sm font-semibold text-rose-200 transition-colors hover:bg-rose-500/[0.16] disabled:opacity-40"
                      : "btn-accent px-4 py-2 text-sm"
                  }
                >
                  {saving === "toggle" ? (
                    <LoaderInline label={enabled ? "Disabling…" : "Enabling…"} />
                  ) : enabled ? (
                    <><PowerOff size={15} /> Disable</>
                  ) : (
                    <><Power size={15} /> Enable computer use</>
                  )}
                </button>
              </div>

              {/* Read-only operational chips */}
              <div className="flex flex-wrap gap-2.5">
                <Chip
                  label="isolation"
                  value={
                    <span className="inline-flex items-center gap-1.5">
                      <Lock size={12} className="text-accent-soft" />
                      {data?.isolation ?? "—"}
                    </span>
                  }
                />
                <Chip label="max steps" value={data?.max_steps ?? "—"} />
                <Chip label="max retries" value={data?.max_retries ?? "—"} />
                <Chip
                  label="pending"
                  value={
                    <span className={data && data.pending_approvals > 0 ? "text-amber-300" : ""}>
                      {data?.pending_approvals ?? 0}
                    </span>
                  }
                />
              </div>

              {saveErr && <ErrorNote>{saveErr}</ErrorNote>}
            </div>
          )}
        </Card>
      </Reveal>

      {/* Allowlist editors */}
      <Reveal>
        <div className="grid gap-4 lg:grid-cols-2">
          {/* Domain allowlist */}
          <Card title="Domain allowlist" icon={<Globe size={15} />}>
            <p className="mb-3 text-xs leading-relaxed text-zinc-500">
              The agent may only navigate to these hosts (and their subdomains). Any domain not on
              the list is <span className="font-medium text-zinc-300">denied outright</span>. An
              empty list blocks all navigation.
            </p>
            <div className="flex items-center gap-2">
              <input
                value={domainInput}
                onChange={(e) => setDomainInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addDomain();
                  }
                }}
                placeholder="github.com"
                spellCheck={false}
                className="field font-mono text-xs"
              />
              <button
                onClick={addDomain}
                disabled={!normalizeDomain(domainInput)}
                className="btn-ghost shrink-0 px-3 py-2 text-xs"
              >
                <Plus size={14} /> Add
              </button>
            </div>

            <div className="mt-3 flex flex-wrap gap-2">
              {domains.length === 0 ? (
                <span className="text-xs text-zinc-600">No domains allowed yet.</span>
              ) : (
                domains.map((d) => (
                  <span
                    key={d}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-accent/25 bg-accent/[0.08] py-1 pl-2.5 pr-1.5 font-mono text-xs text-accent-soft"
                  >
                    {d}
                    <button
                      onClick={() => setDomains((xs) => xs.filter((x) => x !== d))}
                      className="rounded p-0.5 text-accent-soft/70 transition-colors hover:bg-rose-500/20 hover:text-rose-300"
                      aria-label={`Remove ${d}`}
                    >
                      <X size={12} />
                    </button>
                  </span>
                ))
              )}
            </div>
          </Card>

          {/* Action allowlist */}
          <Card title="Action allowlist" icon={<MousePointerClick size={15} />}>
            <p className="mb-3 text-xs leading-relaxed text-zinc-500">
              Pick which action kinds are permitted. Reads are safe defaults;{" "}
              <span className="font-medium text-amber-200/90">click / type / screenshot_click</span>{" "}
              mutate state and trigger approval on anything sensitive. Anything unchecked is denied.
            </p>
            <div className="space-y-1.5">
              {ACTIONS.map((a) => {
                const on = actions.includes(a.kind);
                const Icon = a.icon;
                return (
                  <button
                    key={a.kind}
                    onClick={() => toggleAction(a.kind)}
                    className={`flex w-full items-center gap-3 rounded-xl border px-3 py-2 text-left transition-colors ${
                      on
                        ? "border-accent/30 bg-accent/[0.06]"
                        : "border-white/[0.06] bg-white/[0.02] hover:border-white/15"
                    }`}
                  >
                    <span
                      className={`grid h-5 w-5 shrink-0 place-items-center rounded-md border ${
                        on
                          ? "border-accent/50 bg-accent text-ink-950"
                          : "border-white/15 bg-transparent text-transparent"
                      }`}
                    >
                      <Check size={13} strokeWidth={3} />
                    </span>
                    <Icon
                      size={15}
                      className={on ? "text-accent-soft" : "text-zinc-500"}
                    />
                    <span className="flex-1">
                      <span className="font-mono text-xs font-medium text-zinc-200">
                        {a.label}
                      </span>
                      <span className="ml-2 text-[11px] text-zinc-500">{a.hint}</span>
                    </span>
                    {a.readonly ? (
                      <span className="rounded-full border border-emerald-500/25 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
                        read-only
                      </span>
                    ) : (
                      <span className="rounded-full border border-amber-500/25 bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-300">
                        higher-risk
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </Card>
        </div>
      </Reveal>

      {/* Save bar for allowlist edits */}
      {data && (
        <Reveal>
          <div className="flex items-center justify-between gap-3 rounded-2xl border border-white/[0.06] bg-ink-850/60 px-4 py-3">
            <div className="flex items-center gap-2 text-xs text-zinc-500">
              {dirty ? (
                <>
                  <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
                  Unsaved allowlist changes
                </>
              ) : (
                <>
                  <Check size={14} className="text-emerald-400" />
                  Allowlists in sync with the daemon
                </>
              )}
            </div>
            <button
              onClick={() => apply(enabled, "save")}
              disabled={!dirty || saving !== null}
              className="btn-accent px-4 py-1.5 text-xs"
            >
              {saving === "save" ? <LoaderInline label="Saving…" /> : (<><Check size={14} /> Apply allowlists</>)}
            </button>
          </div>
        </Reveal>
      )}

      {/* Approval queue — human-in-the-loop gate */}
      <Reveal>
        <Card
          title="Approval queue"
          icon={<UserCheck size={15} />}
          right={
            <span className="flex items-center gap-2 text-[11px] text-zinc-500">
              <RefreshCw size={12} className="text-accent-soft/70" /> live
              {approvals.length > 0 && (
                <span className="rounded-full border border-amber-500/25 bg-amber-500/10 px-2 py-0.5 font-medium text-amber-300">
                  {approvals.length} pending
                </span>
              )}
            </span>
          }
        >
          <p className="mb-4 text-xs leading-relaxed text-zinc-500">
            When the agent proposes a sensitive or destructive action it pauses here and waits for
            you. Nothing runs until you approve it — this is the human-in-the-loop safety gate.
          </p>
          {approvalsState.loading && !approvalsState.data ? (
            <SkeletonRows rows={2} />
          ) : approvals.length === 0 ? (
            <Empty icon={<Inbox size={28} />}>No actions awaiting your approval.</Empty>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              {approvals.map((a) => (
                <ApprovalCard key={a.id} approval={a} onResolved={approvalsState.reload} />
              ))}
            </div>
          )}
        </Card>
      </Reveal>

      {/* Run history — every run, expandable to its full audit trace */}
      <Reveal>
        <Card
          title="Recent runs"
          icon={<History size={15} />}
          right={
            <span className="flex items-center gap-2 text-[11px] text-zinc-500">
              <RefreshCw size={12} className="text-accent-soft/70" /> updates when a run
              finishes
            </span>
          }
        >
          <p className="mb-4 text-xs leading-relaxed text-zinc-500">
            The last 20 runs, newest first. Click a run to inspect its recorded trace —
            every action, result, screenshot reference, and error, in order.
          </p>
          {runsState.loading && !runsState.data ? (
            <SkeletonRows rows={3} />
          ) : runs.length === 0 ? (
            <Empty icon={<History size={28} />}>
              No runs recorded yet — history appears after the first computer-use run.
            </Empty>
          ) : (
            <ul className="space-y-2">
              {runs.map((r) => {
                const isOpen = expandedRun === r.id;
                const Chevron = isOpen ? ChevronDown : ChevronRight;
                return (
                  <li
                    key={r.id}
                    className="rounded-xl border border-white/[0.06] bg-white/[0.02]"
                  >
                    <button
                      type="button"
                      onClick={() => setExpandedRun(isOpen ? null : r.id)}
                      className="flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-white/[0.03]"
                    >
                      <Chevron size={14} className="shrink-0 text-zinc-500" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm text-zinc-200" title={r.task}>
                          {r.task || "(no task)"}
                        </span>
                        <span className="mt-0.5 block truncate font-mono text-[10px] text-zinc-600">
                          {r.id} · started {timeAgo(r.started_at)}
                          {r.finished_at ? ` · finished ${timeAgo(r.finished_at)}` : ""}
                        </span>
                      </span>
                      <span
                        className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium ${runBadgeClass(r.status, r.ok)}`}
                      >
                        {runBadgeLabel(r.status, r.ok)}
                      </span>
                    </button>
                    {isOpen && (
                      <div className="border-t border-white/[0.06] px-3 py-2.5">
                        <RunDetailView runId={r.id} />
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </Reveal>

      {/* Best practices — why you can trust it */}
      <Reveal>
        <Card title="How Iron Jarvis keeps this safe" icon={<ShieldCheck size={15} />}>
          <ul className="grid gap-3 sm:grid-cols-2">
            {BEST_PRACTICES.map(({ icon: Icon, text }, i) => (
              <li key={i} className="flex items-start gap-2.5 text-sm text-zinc-400">
                <Icon size={16} className="mt-0.5 shrink-0 text-accent-soft/80" />
                <span className="leading-relaxed">{text}</span>
              </li>
            ))}
          </ul>
        </Card>
      </Reveal>
    </PageShell>
  );
}
