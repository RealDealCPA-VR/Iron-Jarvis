"use client";

import { useState } from "react";
import Link from "next/link";
import {
  Zap,
  Plus,
  X,
  Play,
  Webhook as WebhookIcon,
  MessageSquare,
  ArrowRight,
  Workflow as WorkflowIcon,
  Bot,
  TerminalSquare,
} from "lucide-react";
import { post, patch, del, ApiError } from "@/lib/api";
import { usePolledApi, useApi } from "@/lib/useApi";
import type { Webhook } from "@/lib/types";
import {
  Card,
  Badge,
  OfflineHint,
  Empty,
  SkeletonRows,
  ErrorNote,
  SuccessNote,
  LoaderInline,
  ConfirmButton,
  SectionLabel,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

/* -------------------------------------------------------------------------- */
/*  Types (mirror src/iron_jarvis/reflex/models.py::ReflexRule)                */
/* -------------------------------------------------------------------------- */

type ReflexSource = "webhook" | "comm";
type ReflexAction = "workflow" | "remote_agent" | "session";

interface ReflexRule {
  id: string;
  name: string;
  source: ReflexSource;
  match: string;
  action: ReflexAction;
  target: string;
  task_template: string;
  enabled: boolean;
  created_at: string;
  last_fired_at: string | null;
  fire_count: number;
}

/** POST /reflex/rules/{id}/test result. */
interface TestResult {
  rule_id: string;
  ok: boolean;
  kind: string;
  run_id?: string;
  session_id?: string;
  agent?: string;
  error?: string;
}

const ACTION_LABEL: Record<ReflexAction, string> = {
  workflow: "workflow",
  remote_agent: "remote agent",
  session: "session",
};

/* -------------------------------------------------------------------------- */
/*  Small inline toggle switch (arc-reactor cyan when on)                      */
/* -------------------------------------------------------------------------- */

function Toggle({
  enabled,
  busy,
  onChange,
}: {
  enabled: boolean;
  busy?: boolean;
  onChange: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      disabled={busy}
      onClick={onChange}
      title={enabled ? "Enabled — click to disable" : "Disabled — click to enable"}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
        enabled ? "bg-accent/70 shadow-[0_0_8px_1px_rgb(var(--accent-rgb)/0.35)]" : "bg-zinc-700"
      }`}
    >
      <span
        className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
          enabled ? "translate-x-4" : "translate-x-1"
        }`}
      />
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/*  Readable "When … → …" sentence for a rule                                  */
/* -------------------------------------------------------------------------- */

function chip(t: string) {
  return (
    <code className="rounded bg-black/30 px-1.5 py-0.5 font-mono text-[12px] text-accent-soft">
      {t}
    </code>
  );
}

function RuleSentence({ r }: { r: ReflexRule }) {
  const trigger =
    r.source === "webhook" ? (
      <>When webhook {chip(r.match || "?")} fires</>
    ) : r.match.trim() ? (
      <>When a message contains {chip(r.match.trim())}</>
    ) : (
      <>When any message arrives</>
    );
  const action =
    r.action === "workflow" ? (
      <>run workflow {chip(r.target || "?")}</>
    ) : r.action === "remote_agent" ? (
      <>delegate to agent {chip(r.target || "?")}</>
    ) : (
      <>start a session</>
    );
  return (
    <span className="text-sm text-zinc-300">
      {trigger} <ArrowRight size={13} className="inline align-middle text-zinc-600" /> {action}
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/*  Page                                                                       */
/* -------------------------------------------------------------------------- */

export default function ReflexPage() {
  const { data, error, loading, reload } = usePolledApi<{ rules: ReflexRule[] }>(
    "/reflex/rules",
    15000,
  );
  const offline = error && error.status === 0;
  const rules = data?.rules ?? [];
  const webhookRules = rules.filter((r) => r.source === "webhook");
  const commRules = rules.filter((r) => r.source === "comm");

  // Picker sources for the add form.
  const workflows = useApi<{ workflows: { name: string; description?: string }[] }>("/workflows");
  const remoteAgents = useApi<{ agents: { name: string }[] }>("/agents/remote");
  const webhooks = useApi<{ webhooks: Webhook[] }>("/webhooks");
  const workflowNames = workflows.data?.workflows?.map((w) => w.name) ?? [];
  const agentNames = remoteAgents.data?.agents?.map((a) => a.name) ?? [];
  const inboundSlugs =
    webhooks.data?.webhooks
      ?.filter((w) => (w.direction ?? "").toLowerCase() === "inbound")
      .map((w) => w.slug) ?? [];

  // Add form
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [source, setSource] = useState<ReflexSource>("webhook");
  const [match, setMatch] = useState("");
  const [action, setAction] = useState<ReflexAction>("workflow");
  const [target, setTarget] = useState("");
  const [taskTemplate, setTaskTemplate] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [formOk, setFormOk] = useState<string | null>(null);

  // Per-row state
  const [acting, setActing] = useState<string | null>(null); // "toggle:id" | "test:id"
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});
  const [rowError, setRowError] = useState<string | null>(null);

  const targetNeeded = action === "workflow" || action === "remote_agent";
  const templateShown = action === "session" || action === "remote_agent";
  const matchReady = source !== "webhook" || !!match.trim();
  const targetReady = !targetNeeded || !!target.trim();
  const canSubmit = matchReady && targetReady && !busy;

  function pickSource(v: ReflexSource) {
    setSource(v);
    setMatch(""); // slug vs. keyword don't transfer between sources
  }
  function pickAction(v: ReflexAction) {
    setAction(v);
    setTarget(""); // a workflow name isn't a remote-agent name
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    // Client-side mirror of the backend's 400s — surface early, still trust the server.
    if (source === "webhook" && !match.trim()) {
      setFormError("A webhook reflex needs a webhook slug.");
      return;
    }
    if (targetNeeded && !target.trim()) {
      setFormError(`A '${ACTION_LABEL[action]}' action needs a target.`);
      return;
    }
    setBusy(true);
    setFormError(null);
    setFormOk(null);
    try {
      await post("/reflex/rules", {
        name: name.trim(),
        source,
        match: match.trim(),
        action,
        target: targetNeeded ? target.trim() : "",
        task_template: templateShown ? taskTemplate : "",
        enabled,
      });
      setFormOk("Reflex added — it's live now and survives restarts.");
      setName("");
      setMatch("");
      setTarget("");
      setTaskTemplate("");
      setEnabled(true);
      reload();
    } catch (err) {
      // The daemon's 400 detail is already specific — show it verbatim.
      setFormError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function toggle(r: ReflexRule) {
    setActing(`toggle:${r.id}`);
    setRowError(null);
    try {
      await patch(`/reflex/rules/${encodeURIComponent(r.id)}`, { enabled: !r.enabled });
      reload();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActing(null);
    }
  }

  async function test(r: ReflexRule) {
    setActing(`test:${r.id}`);
    setRowError(null);
    try {
      const res = await post<TestResult>(`/reflex/rules/${encodeURIComponent(r.id)}/test`);
      setTestResults((prev) => ({ ...prev, [r.id]: res }));
      reload(); // fire_count / last_fired_at just changed
    } catch (err) {
      setTestResults((prev) => ({
        ...prev,
        [r.id]: {
          rule_id: r.id,
          ok: false,
          kind: r.action,
          error: err instanceof ApiError ? err.message : String(err),
        },
      }));
    } finally {
      setActing(null);
    }
  }

  async function remove(r: ReflexRule) {
    setRowError(null);
    try {
      await del(`/reflex/rules/${encodeURIComponent(r.id)}`);
      setTestResults((prev) => {
        const next = { ...prev };
        delete next[r.id];
        return next;
      });
      reload();
    } catch (err) {
      setRowError(err instanceof ApiError ? err.message : String(err));
    }
  }

  function testDetail(res: TestResult): string {
    if (res.run_id) return `run ${res.run_id}`;
    if (res.session_id) return `session ${res.session_id}`;
    if (res.agent) return `agent ${res.agent}`;
    return "";
  }

  function renderRule(r: ReflexRule) {
    const res = testResults[r.id];
    return (
      <div
        key={r.id}
        className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 transition-colors hover:bg-white/[0.03]"
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Badge
                value={r.source === "webhook" ? "webhook" : "message"}
                tone={r.source === "webhook" ? "cyan" : "violet"}
              />
              {r.name ? (
                <span className="truncate font-medium text-zinc-100">{r.name}</span>
              ) : (
                <span className="text-zinc-600">unnamed</span>
              )}
            </div>
            <div className="mt-1.5">
              <RuleSentence r={r} />
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-zinc-600">
              <span>
                Fired <span className="text-zinc-400">{r.fire_count}×</span>
              </span>
              {r.last_fired_at && (
                <span>last {new Date(r.last_fired_at).toLocaleString()}</span>
              )}
              {templateShownFor(r) && r.task_template.trim() && (
                <span className="max-w-[18rem] truncate" title={r.task_template}>
                  template: {r.task_template.trim()}
                </span>
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Toggle
              enabled={r.enabled}
              busy={acting === `toggle:${r.id}`}
              onChange={() => toggle(r)}
            />
            <button
              type="button"
              onClick={() => test(r)}
              disabled={acting === `test:${r.id}`}
              title="Fire this reflex now with a synthetic signal"
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-xs font-medium text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft disabled:opacity-40"
            >
              {acting === `test:${r.id}` ? (
                <LoaderInline label="Firing…" />
              ) : (
                <>
                  <Play size={13} /> Test
                </>
              )}
            </button>
            <ConfirmButton
              onConfirm={() => remove(r)}
              label="Delete"
              title={`Delete reflex "${r.name || r.id}"`}
            />
          </div>
        </div>

        {res && (
          <div className="mt-3">
            {res.ok ? (
              <SuccessNote>
                Fired {res.kind}
                {testDetail(res) ? ` · ${testDetail(res)}` : ""}.
              </SuccessNote>
            ) : (
              <ErrorNote>{res.error || `Test failed (${res.kind}).`}</ErrorNote>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Reflexes"
          subtitle="When a webhook fires or a message arrives, run a workflow, a remote agent, or a session — automatically."
          actions={
            <button type="button" onClick={() => setOpen((v) => !v)} className="btn-accent">
              <Plus size={14} /> Add reflex
            </button>
          }
        />
      </Reveal>
      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      {open && (
        <Reveal>
          <Card title="Add reflex" icon={<Plus size={15} />}>
            <form onSubmit={submit} className="space-y-3.5">
              {/* Signal: source + match */}
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Signal source
                  </label>
                  <select
                    aria-label="Signal source"
                    value={source}
                    onChange={(e) => pickSource(e.target.value as ReflexSource)}
                    className="field"
                  >
                    <option value="webhook">Webhook fires</option>
                    <option value="comm">Message arrives</option>
                  </select>
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    {source === "webhook" ? "Webhook slug" : "Keyword"}
                  </label>
                  {source === "webhook" ? (
                    inboundSlugs.length > 0 ? (
                      <select
                        aria-label="Webhook slug"
                        value={match}
                        onChange={(e) => setMatch(e.target.value)}
                        className="field"
                      >
                        <option value="">Select a webhook…</option>
                        {inboundSlugs.map((s) => (
                          <option key={s} value={s}>
                            {s}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <>
                        <input
                          value={match}
                          onChange={(e) => setMatch(e.target.value)}
                          placeholder="github-push"
                          className="field font-mono"
                        />
                        <div className="mt-1 text-[11px] text-zinc-600">
                          No inbound webhooks yet — type a slug, or register one on the{" "}
                          <Link
                            href="/webhooks"
                            className="text-accent-soft underline-offset-2 hover:underline"
                          >
                            Webhooks
                          </Link>{" "}
                          page.
                        </div>
                      </>
                    )
                  ) : (
                    <>
                      <input
                        value={match}
                        onChange={(e) => setMatch(e.target.value)}
                        placeholder="deploy"
                        className="field font-mono"
                      />
                      <div className="mt-1 text-[11px] text-zinc-600">
                        Matched as a whole word, case-insensitive. Blank = every message.
                      </div>
                    </>
                  )}
                </div>
              </div>

              {/* Action: action + target */}
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Action
                  </label>
                  <select
                    aria-label="Action"
                    value={action}
                    onChange={(e) => pickAction(e.target.value as ReflexAction)}
                    className="field"
                  >
                    <option value="workflow">Run a workflow</option>
                    <option value="remote_agent">Delegate to a remote agent</option>
                    <option value="session">Start a session</option>
                  </select>
                </div>
                {targetNeeded && (
                  <div>
                    <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                      {action === "workflow" ? "Workflow" : "Remote agent"}
                    </label>
                    {action === "workflow" ? (
                      workflowNames.length > 0 ? (
                        <select
                          aria-label="Workflow"
                          value={target}
                          onChange={(e) => setTarget(e.target.value)}
                          className="field"
                        >
                          <option value="">Select a workflow…</option>
                          {workflowNames.map((w) => (
                            <option key={w} value={w}>
                              {w}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <div className="rounded-xl border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2 text-[11px] text-amber-300/90">
                          No saved workflows yet — create one on the{" "}
                          <Link href="/workflows" className="underline-offset-2 hover:underline">
                            Workflows
                          </Link>{" "}
                          page first.
                        </div>
                      )
                    ) : agentNames.length > 0 ? (
                      <select
                        aria-label="Remote agent"
                        value={target}
                        onChange={(e) => setTarget(e.target.value)}
                        className="field"
                      >
                        <option value="">Select a remote agent…</option>
                        {agentNames.map((a) => (
                          <option key={a} value={a}>
                            {a}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <div className="rounded-xl border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2 text-[11px] text-amber-300/90">
                        No remote agents yet — add one on the{" "}
                        <Link href="/agents" className="underline-offset-2 hover:underline">
                          Agents
                        </Link>{" "}
                        page first.
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Task template (session / remote_agent only) */}
              {templateShown && (
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Task template
                  </label>
                  <textarea
                    value={taskTemplate}
                    onChange={(e) => setTaskTemplate(e.target.value)}
                    rows={3}
                    placeholder="Triage this: {body}"
                    className="field font-mono"
                  />
                  <div className="mt-1 text-[11px] text-zinc-600">
                    Placeholders <code className="font-mono text-accent-soft">{"{body}"}</code> /{" "}
                    <code className="font-mono text-accent-soft">{"{text}"}</code> /{" "}
                    <code className="font-mono text-accent-soft">{"{slug}"}</code> are filled from
                    the triggering signal. Blank = a sensible default.
                  </div>
                </div>
              )}

              {/* Name + enabled */}
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Name (optional)
                  </label>
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="deploy → nightly"
                    className="field"
                  />
                </div>
                <div className="flex items-end">
                  <label className="flex cursor-pointer items-center gap-2 pb-2.5 text-sm text-zinc-300">
                    <input
                      type="checkbox"
                      checked={enabled}
                      onChange={(e) => setEnabled(e.target.checked)}
                      className="h-4 w-4 rounded border-white/20 bg-transparent accent-cyan-500"
                    />
                    Enabled
                  </label>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <button type="submit" disabled={!canSubmit} className="btn-accent">
                  {busy ? (
                    <LoaderInline label="Adding…" />
                  ) : (
                    <>
                      <Plus size={14} /> Add reflex
                    </>
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 px-3 py-2 text-sm text-zinc-400 transition-colors hover:border-white/20 hover:text-zinc-200"
                >
                  <X size={14} /> Cancel
                </button>
              </div>
              <div className="text-[11px] text-zinc-600">
                Every fired reflex still runs through the normal permission engine — nothing acts
                unreviewed.
              </div>
              {formOk && <SuccessNote>{formOk}</SuccessNote>}
              {formError && <ErrorNote>{formError}</ErrorNote>}
            </form>
          </Card>
        </Reveal>
      )}

      <Reveal>
        <Card title={`Reflexes${rules.length ? ` · ${rules.length}` : ""}`} icon={<Zap size={15} />}>
          {rowError && (
            <div className="mb-3">
              <ErrorNote>{rowError}</ErrorNote>
            </div>
          )}
          {loading && !data ? (
            <SkeletonRows rows={4} />
          ) : rules.length === 0 ? (
            <Empty icon={<Zap size={24} />}>
              No reflexes yet — add one to make Iron Jarvis act on its own.
            </Empty>
          ) : (
            <div className="space-y-6">
              {webhookRules.length > 0 && (
                <div className="space-y-2.5">
                  <div className="flex items-center gap-2">
                    <WebhookIcon size={13} className="text-accent-soft" />
                    <SectionLabel>Webhook reflexes · {webhookRules.length}</SectionLabel>
                  </div>
                  {webhookRules.map(renderRule)}
                </div>
              )}
              {commRules.length > 0 && (
                <div className="space-y-2.5">
                  <div className="flex items-center gap-2">
                    <MessageSquare size={13} className="text-violet-300" />
                    <SectionLabel>Message reflexes · {commRules.length}</SectionLabel>
                  </div>
                  {commRules.map(renderRule)}
                </div>
              )}
            </div>
          )}
        </Card>
      </Reveal>

      {/* Legend of what the three actions do (quiet reference). */}
      {rules.length > 0 && (
        <Reveal>
          <Card>
            <div className="flex flex-wrap gap-x-6 gap-y-2 text-[11px] text-zinc-500">
              <span className="inline-flex items-center gap-1.5">
                <WorkflowIcon size={13} className="text-accent-soft" /> workflow — runs a saved
                multi-step process
              </span>
              <span className="inline-flex items-center gap-1.5">
                <Bot size={13} className="text-violet-300" /> remote agent — delegates the task to
                a remote endpoint
              </span>
              <span className="inline-flex items-center gap-1.5">
                <TerminalSquare size={13} className="text-emerald-300" /> session — starts a
                supervised local session
              </span>
            </div>
          </Card>
        </Reveal>
      )}
    </PageShell>
  );
}

/** Whether a rule's action carries a task template (mirrors `templateShown`). */
function templateShownFor(r: ReflexRule): boolean {
  return r.action === "session" || r.action === "remote_agent";
}
