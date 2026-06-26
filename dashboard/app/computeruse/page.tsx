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
  type LucideIcon,
} from "lucide-react";
import { post, ApiError } from "@/lib/api";
import { useApi, usePolledApi } from "@/lib/useApi";
import type { ComputerUseStatus, Approval } from "@/lib/types";
import {
  Card,
  OfflineHint,
  SkeletonRows,
  ErrorNote,
  Empty,
  LoaderInline,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

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
      <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-500">
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
  approval: Approval;
  onResolved: () => void;
}) {
  const [busy, setBusy] = useState<"approve" | "deny" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const { kind, detail } = prettyAction(approval.action_json);

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
  const approvalsState = usePolledApi<{ approvals: Approval[] }>(
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

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Computer Use"
          subtitle="Let agents drive a real browser to finish tasks — gated behind allowlists and your explicit approval."
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
