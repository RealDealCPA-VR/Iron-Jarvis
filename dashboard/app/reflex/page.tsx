"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import {
  Zap,
  Plus,
  X,
  Play,
  Webhook as WebhookIcon,
  MessageSquare,
  Mail,
  CalendarClock,
  Hash,
  Antenna,
  ArrowRight,
  Workflow as WorkflowIcon,
  Bot,
  TerminalSquare,
  Pencil,
  type LucideIcon,
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
  type Tone,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import {
  EVERYDAY_ACTION,
  EVERYDAY_SOURCES,
  isEveryday,
  sourceActive,
  sourceOffHref,
  sourceOffWords,
  triggersOf,
  type ReflexAction,
  type ReflexSource,
  type TriggersStatus,
} from "@/lib/reflexRules";

/* -------------------------------------------------------------------------- */
/*  Types (mirror src/iron_jarvis/reflex/models.py::ReflexRule)                */
/* -------------------------------------------------------------------------- */

interface ReflexRule {
  id: string;
  name: string;
  source: ReflexSource;
  match: string;
  action: ReflexAction;
  target: string;
  task_template: string;
  /** Context spine (v1.200.0): the project the fired work is grounded in. */
  project_id: string | null;
  enabled: boolean;
  created_at: string;
  last_fired_at: string | null;
  fire_count: number;
  /** v1.231.0 (audit AE6): why the last matched signal could not start
   *  (null once a later fire succeeds) / what the last fire started. */
  last_error?: string | null;
  last_result?: string | null;
  /** Readiness 0j.5 item 15: where the last successful start can be seen;
   *  with it, `last_result` is the start in plain words ("Started an agent
   *  run."). Absent on an older daemon's row (nothing is shown then). */
  last_href?: string | null;
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
  /** Readiness 0j.5 item 15: what started, in plain words, and where to see
   *  it (an older daemon sends neither: the old line is kept). */
  words?: string;
  href?: string;
}

/** An address the Rules page may link to: one of the app's own pages only. */
function ownPage(href: unknown): string {
  return typeof href === "string" && href.startsWith("/") && !href.startsWith("//") ? href : "";
}

const ACTION_LABEL: Record<ReflexAction, string> = {
  workflow: "workflow",
  remote_agent: "remote agent",
  session: "session",
};

/* -------------------------------------------------------------------------- */
/*  Signal-source metadata — one place drives the selector, hints, list        */
/*  grouping, and per-rule badge. Mirrors reflex/models.py::REFLEX_SOURCES.     */
/* -------------------------------------------------------------------------- */

const SOURCE_ORDER: ReflexSource[] = ["webhook", "comm", "email", "calendar", "slack"];

interface SourceMeta {
  option: string; // dropdown label
  section: string; // list section heading
  badge: string; // per-rule badge label
  tone: Tone;
  icon: LucideIcon;
  iconClass: string; // accent colour for the section icon
  matchLabel: string; // label above the match input
  matchHint: string; // helper line (keyword sources)
  matchPlaceholder: string;
}

const SOURCE_META: Record<ReflexSource, SourceMeta> = {
  webhook: {
    option: "Webhook fires",
    section: "Webhook rules",
    badge: "webhook",
    tone: "cyan",
    icon: WebhookIcon,
    iconClass: "text-accent-soft",
    matchLabel: "Webhook slug",
    matchHint: "",
    matchPlaceholder: "github-push",
  },
  comm: {
    option: "Message arrives",
    section: "Message rules",
    badge: "message",
    tone: "violet",
    icon: MessageSquare,
    iconClass: "text-violet-300",
    matchLabel: "Keyword",
    matchHint: "Matched as a whole word, case-insensitive. Blank = every message.",
    matchPlaceholder: "deploy",
  },
  email: {
    option: "Email arrives",
    section: "Email rules",
    badge: "email",
    tone: "green",
    icon: Mail,
    iconClass: "text-emerald-300",
    matchLabel: "Keyword",
    matchHint: "Keyword in the subject or body (whole word). Blank = every allowed email.",
    matchPlaceholder: "invoice",
  },
  calendar: {
    option: "Calendar event",
    section: "Calendar rules",
    badge: "calendar",
    tone: "amber",
    icon: CalendarClock,
    iconClass: "text-amber-300",
    matchLabel: "Keyword",
    matchHint: "Keyword in the event title (whole word). Empty = every event.",
    matchPlaceholder: "standup",
  },
  slack: {
    option: "Slack message",
    section: "Slack rules",
    badge: "slack",
    tone: "violet",
    icon: Hash,
    iconClass: "text-violet-300",
    matchLabel: "Keyword",
    matchHint: "Keyword in the message (whole word). Blank = every message.",
    matchPlaceholder: "deploy",
  },
};

// GET /triggers is read through lib/reflexRules (v1.449.0): the Triggers card
// and each rule's "isn't listening" line read the SAME reading.

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
    <code className="rounded bg-ink-900/80 px-1.5 py-0.5 font-mono text-[12px] text-accent-soft">
      {t}
    </code>
  );
}

function RuleSentence({ r }: { r: ReflexRule }) {
  const kw = r.match.trim();
  let trigger: React.ReactNode;
  if (r.source === "webhook") {
    trigger = <>When webhook {chip(r.match || "?")} fires</>;
  } else if (r.source === "email") {
    trigger = kw ? <>When an email mentions {chip(kw)}</> : <>When any email arrives</>;
  } else if (r.source === "calendar") {
    trigger = kw ? <>When an event titled {chip(kw)} nears</> : <>When any event nears</>;
  } else if (r.source === "slack") {
    trigger = kw ? (
      <>When a Slack message mentions {chip(kw)}</>
    ) : (
      <>When any Slack message arrives</>
    );
  } else {
    trigger = kw ? <>When a message contains {chip(kw)}</> : <>When any message arrives</>;
  }
  const action =
    r.action === "workflow" ? (
      <>run workflow {chip(r.target || "?")}</>
    ) : r.action === "remote_agent" ? (
      <>delegate to agent {chip(r.target || "?")}</>
    ) : (
      <>run an agent</>
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
  // v1.316.0 (form-labels-not-associated): useId ties each label to its
  // field, so a screen reader names it and a click on the label focuses it.
  const fid = useId();
  const { data, error, loading, reload } = usePolledApi<{ rules: ReflexRule[] }>(
    "/reflex/rules",
    15000,
  );
  const offline = error && error.status === 0;
  const rules = data?.rules ?? [];

  // Picker sources for the add form.
  const workflows = useApi<{ workflows: { name: string; description?: string }[] }>("/workflows");
  const remoteAgents = useApi<{ agents: { name: string }[] }>("/agents/remote");
  const webhooks = useApi<{ webhooks: Webhook[] }>("/webhooks");
  // Context spine: projects a rule's fired work can be grounded in.
  const projectsApi = useApi<{ projects: { id: string; name: string }[] }>("/projects");
  const projects = projectsApi.data?.projects ?? [];
  const projectName = (id: string) => projects.find((p) => p.id === id)?.name ?? id;
  const workflowNames = workflows.data?.workflows?.map((w) => w.name) ?? [];
  const agentNames = remoteAgents.data?.agents?.map((a) => a.name) ?? [];
  const inboundSlugs =
    webhooks.data?.webhooks
      ?.filter((w) => (w.direction ?? "").toLowerCase() === "inbound")
      .map((w) => w.slug) ?? [];

  // Add form
  const [open, setOpen] = useState(false);
  // v1.316.0 (carry-empty-add-scrolls-to-form): the empty state's Add opens
  // THIS form, which sits ABOVE the list — on a phone a scrolled-down user saw
  // nothing happen. Every press bumps the counter, the effect scrolls the form
  // into view and focuses its first field (Schedules' taskFieldRef pattern),
  // also when the header already opened it. The header Add still toggles.
  const formStartRef = useRef<HTMLSelectElement | null>(null);
  const [focusForm, setFocusForm] = useState(0);
  useEffect(() => {
    if (!focusForm) return;
    const el = formStartRef.current;
    if (!el) return;
    el.scrollIntoView?.({ behavior: "smooth", block: "center" });
    el.focus();
  }, [focusForm]);
  const openFromEmpty = () => {
    resetForm();
    setOpen(true);
    setFocusForm((n) => n + 1);
  };
  const [name, setName] = useState("");
  // v1.449.0 (Wave D item 34): the form opens on the everyday rule — a
  // message arrives, an agent does a task — and the other shapes (a webhook,
  // a calendar event; a workflow, a remote agent) sit behind "Another kind".
  const [everyday, setEveryday] = useState(true);
  const [source, setSource] = useState<ReflexSource>("comm");
  const [match, setMatch] = useState("");
  const [action, setAction] = useState<ReflexAction>(EVERYDAY_ACTION);
  // The rule being edited (null = adding). Edit opens THIS form prefilled
  // and saves with PATCH /reflex/rules/{id} (partial, the daemon re-checks).
  const [editingId, setEditingId] = useState<string | null>(null);
  // Is each message source listening (GET /triggers, read through
  // lib/reflexRules — the same reading as the Triggers card below).
  const triggersApi = useApi<TriggersStatus>("/triggers");
  const [target, setTarget] = useState("");
  const [taskTemplate, setTaskTemplate] = useState("");
  const [projectId, setProjectId] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [formOk, setFormOk] = useState<string | null>(null);

  // Deep link from the workflow editor's trigger node (v1.122.0):
  // ?workflow=<name> opens the add form prefilled to fire THAT workflow.
  useEffect(() => {
    try {
      const wf = new URLSearchParams(window.location.search).get("workflow");
      if (wf) {
        setOpen(true);
        setEveryday(false);
        setAction("workflow");
        setTarget(wf);
        setName(`run-${wf}`);
      }
    } catch {
      /* malformed URL — ignore */
    }
  }, []);

  // Per-row state
  const [acting, setActing] = useState<string | null>(null); // "toggle:id" | "test:id"
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});
  const [rowError, setRowError] = useState<string | null>(null);

  const targetNeeded = action === "workflow" || action === "remote_agent";
  // A deep-linked target that isn't a saved workflow would create a rule that
  // only errors at fire time — warn and hold the Add button instead.
  const targetUnknown =
    action === "workflow" &&
    !!target.trim() &&
    workflowNames.length > 0 &&
    !workflowNames.includes(target.trim());
  const templateShown = action === "session" || action === "remote_agent";
  // A remote agent runs on someone else's endpoint — no grounding seam there,
  // so the picker (and the submitted project) applies to session/workflow only.
  const projectShown = action !== "remote_agent";
  const matchReady = source !== "webhook" || !!match.trim();
  const targetReady = !targetNeeded || !!target.trim();
  const canSubmit = matchReady && targetReady && !targetUnknown && !busy;

  function pickSource(v: ReflexSource) {
    setSource(v);
    setMatch(""); // slug vs. keyword don't transfer between sources
  }
  function pickAction(v: ReflexAction) {
    setAction(v);
    setTarget(""); // a workflow name isn't a remote-agent name
  }
  function pickShape(next: boolean) {
    setEveryday(next);
    if (next) {
      if (!(EVERYDAY_SOURCES as readonly string[]).includes(source)) pickSource("comm");
      if (action !== EVERYDAY_ACTION) pickAction(EVERYDAY_ACTION);
    }
  }
  function resetForm() {
    setName("");
    setMatch("");
    setTarget("");
    setTaskTemplate("");
    setProjectId("");
    setEnabled(true);
    setEveryday(true);
    setSource("comm");
    setAction(EVERYDAY_ACTION);
    setEditingId(null);
  }
  /** v1.449.0: Edit opens the add form prefilled with the rule. */
  function startEdit(r: ReflexRule) {
    setEditingId(r.id);
    setEveryday(isEveryday(r));
    setName(r.name);
    setSource(r.source);
    setMatch(r.match);
    setAction(r.action);
    setTarget(r.target);
    setTaskTemplate(r.task_template);
    setProjectId(r.project_id ?? "");
    setEnabled(r.enabled);
    setFormError(null);
    setFormOk(null);
    setOpen(true);
    setFocusForm((n) => n + 1);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    // Client-side mirror of the backend's 400s — surface early, still trust the server.
    if (source === "webhook" && !match.trim()) {
      setFormError("A webhook rule needs a webhook slug.");
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
      const fields = {
        name: name.trim(),
        source,
        match: match.trim(),
        action,
        target: targetNeeded ? target.trim() : "",
        task_template: templateShown ? taskTemplate : "",
      };
      if (editingId) {
        // PARTIAL on the daemon, but the form holds every field, so all ride;
        // "" clears the grounding (a remote agent has none).
        await patch(`/reflex/rules/${encodeURIComponent(editingId)}`, {
          ...fields,
          project_id: projectShown && projectId ? projectId : "",
          enabled,
        });
        setFormOk("Rule saved — the change applies to the next signal.");
        resetForm();
        setOpen(false);
      } else {
        await post("/reflex/rules", {
          ...fields,
          project_id: projectShown && projectId ? projectId : null,
          enabled,
        });
        setFormOk("Rule added — it's live now and survives restarts.");
        setName("");
        setMatch("");
        setTarget("");
        setTaskTemplate("");
        setProjectId("");
        setEnabled(true);
      }
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
    // v1.449.0 (Wave D item 34): a rule whose source cannot hear anything
    // right now says so on its row (only when /triggers answered — a guess
    // never reads as "off"; a switched-off rule says nothing more).
    const offWords = r.enabled ? sourceOffWords(r.source, triggersApi.data) : "";
    return (
      <div
        key={r.id}
        className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 transition-colors hover:bg-white/[0.03]"
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Badge value={SOURCE_META[r.source].badge} tone={SOURCE_META[r.source].tone} />
              {r.name ? (
                <span className="truncate font-medium text-zinc-100">{r.name}</span>
              ) : (
                <span className="text-zinc-600">unnamed</span>
              )}
              {r.project_id && (
                <span
                  title={`Grounded in project ${projectName(r.project_id)}`}
                  className="inline-flex min-w-0 items-center"
                >
                  <Badge value={projectName(r.project_id)} tone="cyan" />
                </span>
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
              {r.last_result && ownPage(r.last_href) && (
                <span data-testid={`reflex-last-result-${r.id}`} className="text-zinc-400">
                  <Link href={ownPage(r.last_href)} className="underline-offset-2 hover:underline">
                    {r.last_result}
                  </Link>
                </span>
              )}
              {r.last_error && (
                <span
                  data-testid={`reflex-last-error-${r.id}`}
                  className="max-w-[24rem] truncate text-amber-300"
                  title={`Last signal matched but the rule could not start: ${r.last_error}`}
                >
                  could not start: {r.last_error}
                </span>
              )}
              {offWords && (
                <span data-testid={`reflex-source-off-${r.id}`} className="text-tone-warn">
                  {sourceOffHref(r.source).startsWith("/") ? (
                    <Link href={sourceOffHref(r.source)} className="underline-offset-2 hover:underline">
                      {offWords}
                    </Link>
                  ) : (
                    <a href={sourceOffHref(r.source)} className="underline-offset-2 hover:underline">
                      {offWords}
                    </a>
                  )}
                </span>
              )}
              {templateShownFor(r) && r.task_template.trim() && (
                <span className="max-w-[18rem] truncate" title={r.task_template}>
                  task: {r.task_template.trim()}
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
              title="Try this rule now with a test signal"
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
            <button
              type="button"
              onClick={() => startEdit(r)}
              data-testid={`reflex-edit-${r.id}`}
              title={`Edit rule "${r.name || r.id}"`}
              aria-label={`Edit rule ${r.name || r.id}`}
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-xs font-medium text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft"
            >
              <Pencil size={13} /> Edit
            </button>
            <ConfirmButton
              onConfirm={() => remove(r)}
              label="Delete"
              title={`Delete rule "${r.name || r.id}"`}
            />
          </div>
        </div>

        {res && (
          <div className="mt-3">
            {res.ok && typeof res.words === "string" && res.words.trim() ? (
              <SuccessNote>
                <span data-testid={`reflex-test-result-${r.id}`}>
                  {res.words.trim()}
                  {ownPage(res.href) ? (
                    <>
                      {" "}
                      <Link href={ownPage(res.href)} className="underline underline-offset-2">
                        Open it
                      </Link>
                    </>
                  ) : null}
                </span>
              </SuccessNote>
            ) : res.ok ? (
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
          title="Rules"
          subtitle="When a webhook fires or a message arrives, run a workflow, a remote agent, or Jarvis on this PC, automatically."
          actions={
            <button
              type="button"
              onClick={() => {
                // An open Edit becomes a fresh Add (never an edit left half done).
                if (editingId) {
                  resetForm();
                  setOpen(true);
                } else setOpen((v) => !v);
              }}
              className="btn-accent"
            >
              <Plus size={14} /> Add rule
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
          <Card title={editingId ? "Edit rule" : "Add rule"} icon={editingId ? <Pencil size={15} /> : <Plus size={15} />}>
            <form onSubmit={submit} className="space-y-3.5">
              {/* v1.449.0 (Wave D item 34): the everyday shape first; the
                  other shapes behind one choice. */}
              <div role="radiogroup" aria-label="Kind of rule" className="flex flex-wrap gap-1.5">
                <button
                  type="button"
                  role="radio"
                  aria-checked={everyday}
                  onClick={() => pickShape(true)}
                  data-testid="reflex-shape-everyday"
                  className={`rounded-lg px-2.5 py-1 text-xs transition-colors ${
                    everyday ? "bg-accent/15 text-zinc-100" : "text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-200"
                  }`}
                >
                  A message arrives → an agent does a task
                </button>
                <button
                  type="button"
                  role="radio"
                  aria-checked={!everyday}
                  onClick={() => pickShape(false)}
                  data-testid="reflex-shape-other"
                  className={`rounded-lg px-2.5 py-1 text-xs transition-colors ${
                    !everyday ? "bg-accent/15 text-zinc-100" : "text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-200"
                  }`}
                >
                  Another kind of rule
                </button>
              </div>
              {/* Signal: source + match */}
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor={`${fid}-source`} className="mb-1.5 block text-[12px] font-medium text-zinc-400">
                    Signal source
                  </label>
                  <select
                    id={`${fid}-source`}
                    ref={formStartRef}
                    aria-label="Signal source"
                    value={source}
                    onChange={(e) => pickSource(e.target.value as ReflexSource)}
                    className="field"
                  >
                    {(everyday ? EVERYDAY_SOURCES : SOURCE_ORDER).map((s) => (
                      <option key={s} value={s}>
                        {SOURCE_META[s].option}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label htmlFor={`${fid}-match`} className="mb-1.5 block text-[12px] font-medium text-zinc-400">
                    {SOURCE_META[source].matchLabel}
                  </label>
                  {source === "webhook" ? (
                    inboundSlugs.length > 0 ? (
                      <select
                        id={`${fid}-match`}
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
                          id={`${fid}-match`}
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
                        id={`${fid}-match`}
                        value={match}
                        onChange={(e) => setMatch(e.target.value)}
                        placeholder={SOURCE_META[source].matchPlaceholder}
                        className="field font-mono"
                      />
                      <div className="mt-1 text-[11px] text-zinc-600">
                        {SOURCE_META[source].matchHint}
                      </div>
                    </>
                  )}
                </div>
              </div>

              {/* Action: action + target (the everyday shape is always "an
                  agent does the task below", so it shows no picker). */}
              {everyday ? (
                <p data-testid="reflex-everyday-action" className="text-[12px] text-zinc-400">
                  Then an agent does the task below, and reports back.
                </p>
              ) : (
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor={`${fid}-action`} className="mb-1.5 block text-[12px] font-medium text-zinc-400">
                    Action
                  </label>
                  <select
                    id={`${fid}-action`}
                    aria-label="Action"
                    value={action}
                    onChange={(e) => pickAction(e.target.value as ReflexAction)}
                    className="field"
                  >
                    <option value="workflow">Run a workflow</option>
                    <option value="remote_agent">Delegate to a remote agent</option>
                    <option value="session">Run an agent</option>
                  </select>
                </div>
                {targetNeeded && (
                  <div>
                    {/* v1.316.0: a <label> only when there IS a picker to
                        name — with none saved yet it is a plain heading over
                        the "create one first" note. */}
                    {(action === "workflow" ? workflowNames.length > 0 : agentNames.length > 0) ? (
                      <label htmlFor={`${fid}-target`} className="mb-1.5 block text-[12px] font-medium text-zinc-400">
                        {action === "workflow" ? "Workflow" : "Remote agent"}
                      </label>
                    ) : (
                      <p className="mb-1.5 block text-[12px] font-medium text-zinc-400">
                        {action === "workflow" ? "Workflow" : "Remote agent"}
                      </p>
                    )}
                    {action === "workflow" ? (
                      workflowNames.length > 0 ? (
                        <select
                          id={`${fid}-target`}
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
                          {targetUnknown && (
                            <option value={target}>{target} (not saved yet)</option>
                          )}
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
                        id={`${fid}-target`}
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
                    {targetUnknown && (
                      <p className="mt-1.5 text-[11px] text-amber-300/90">
                        “{target}” isn&apos;t a saved workflow yet — save it on
                        the Workflows page, then add this rule.
                      </p>
                    )}
                  </div>
                )}
              </div>
              )}

              {/* The task it hands over (session / remote_agent only).
                  v1.316.0: shown as "Task" — "template" is the saved prompt
                  on /templates; the wire field stays task_template. */}
              {templateShown && (
                <div>
                  <label htmlFor={`${fid}-task`} className="mb-1.5 block text-[12px] font-medium text-zinc-400">
                    Task
                  </label>
                  <textarea
                    id={`${fid}-task`}
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

              {/* Project grounding (context spine) — session/workflow actions */}
              {projectShown && (
                <div>
                  <label htmlFor={`${fid}-project`} className="mb-1.5 block text-[12px] font-medium text-zinc-400">
                    Run inside a project (optional)
                  </label>
                  <select
                    id={`${fid}-project`}
                    aria-label="Project"
                    value={projectId}
                    onChange={(e) => setProjectId(e.target.value)}
                    className="field"
                  >
                    <option value="">No project</option>
                    {projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                  <div className="mt-1 text-[11px] text-zinc-600">
                    {action === "workflow"
                      ? "Grounds the run in that project's context — a workflow that carries its own project pin keeps it."
                      : "The session runs with that project's brief, instructions, and files."}
                  </div>
                </div>
              )}

              {/* Name + enabled */}
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label htmlFor={`${fid}-name`} className="mb-1.5 block text-[12px] font-medium text-zinc-400">
                    Name (optional)
                  </label>
                  <input
                    id={`${fid}-name`}
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
                    <LoaderInline label={editingId ? "Saving…" : "Adding…"} />
                  ) : editingId ? (
                    "Save changes"
                  ) : (
                    <>
                      <Plus size={14} /> Add rule
                    </>
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setOpen(false);
                    if (editingId) resetForm();
                  }}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 px-3 py-2 text-sm text-zinc-400 transition-colors hover:border-white/20 hover:text-zinc-200"
                >
                  <X size={14} /> Cancel
                </button>
              </div>
              <div className="text-[11px] text-zinc-600">
                Every fired rule still runs through the normal permission engine — nothing acts
                unreviewed.
              </div>
              {formOk && <SuccessNote>{formOk}</SuccessNote>}
              {formError && <ErrorNote>{formError}</ErrorNote>}
            </form>
          </Card>
        </Reveal>
      )}

      <Reveal>
        <Card title={`Your rules${rules.length ? ` · ${rules.length}` : ""}`} icon={<Zap size={15} />}>
          {rowError && (
            <div className="mb-3">
              <ErrorNote>{rowError}</ErrorNote>
            </div>
          )}
          {loading && !data ? (
            <SkeletonRows rows={4} />
          ) : rules.length === 0 ? (
            // v1.314.0: on a fresh install the page explanation lives in the (i)
            // popover (v1.214.1), so this empty state is the one place a new
            // user learns what a reflex IS. The button opens the SAME form the
            // header button opens (it opens, never toggles) and, since
            // v1.316.0, scrolls to it and focuses its first field.
            // Every example below maps to a real source + action in the form.
            <Empty
              icon={<Zap size={24} />}
              title="Start work when something happens"
              examples={[
                "When an email about “invoice” arrives, start a task to file it",
                "When a calendar event called “standup” is coming up, run a workflow",
                "When another app calls your webhook, start a session",
              ]}
              action={{ label: "Add your first rule", onClick: openFromEmpty }}
            >
              A rule works like if-this-then-that: a signal comes in, and Iron Jarvis starts the
              work you picked. Anything risky still asks you first.
            </Empty>
          ) : (
            <div className="space-y-6">
              {SOURCE_ORDER.map((s) => {
                const group = rules.filter((r) => r.source === s);
                if (group.length === 0) return null;
                const meta = SOURCE_META[s];
                const Icon = meta.icon;
                return (
                  <div key={s} className="space-y-2.5">
                    <div className="flex items-center gap-2">
                      <Icon size={13} className={meta.iconClass} />
                      <SectionLabel>
                        {meta.section} · {group.length}
                      </SectionLabel>
                    </div>
                    {group.map(renderRule)}
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      </Reveal>

      <Reveal>
        <div id="reflex-triggers">
          <TriggersCard />
        </div>
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
                <Bot size={13} className="text-violet-300" /> remote agent — hands the task to
                an agent on another machine
              </span>
              <span className="inline-flex items-center gap-1.5">
                <TerminalSquare size={13} className="text-emerald-300" /> agent — Jarvis takes the task
                on this PC
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

/* -------------------------------------------------------------------------- */
/*  Triggers card — makes the "the world starts work" story legible.           */
/*  Calendar polling is configured here; email + Slack point at Channels.      */
/* -------------------------------------------------------------------------- */

function TriggersCard() {
  const fid = useId(); // v1.316.0: ties the calendar labels
  const { data, reload } = useApi<TriggersStatus>("/triggers");
  const parts = triggersOf(data);
  const cal = parts.calendar;
  const emailActive = sourceActive(parts.email);
  const slackActive = sourceActive(parts.slack);

  // Calendar form (POST is one Save; ICS URL is write-only — never shown back).
  const [enabled, setEnabled] = useState(false);
  const [icsUrl, setIcsUrl] = useState("");
  const [lead, setLead] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const seeded = useRef(false);

  // Seed the form from the server ONCE — later polls/reloads must not clobber edits.
  useEffect(() => {
    if (data && !seeded.current) {
      seeded.current = true;
      const seed = triggersOf(data).calendar;
      setEnabled(!!seed?.enabled);
      if (seed?.lead_minutes != null) setLead(String(seed.lead_minutes));
    }
  }, [data]);

  async function saveCalendar() {
    setBusy(true);
    setErr(null);
    setOk(null);
    try {
      const body: { enabled: boolean; ics_url?: string; lead_minutes?: number } = { enabled };
      if (icsUrl.trim()) body.ics_url = icsUrl.trim();
      const n = parseInt(lead, 10);
      if (!Number.isNaN(n)) body.lead_minutes = n;
      await post("/triggers/calendar", body);
      setIcsUrl(""); // clear — the URL is a secret, never re-displayed
      setOk(
        enabled
          ? "Calendar polling on — upcoming events will fire matching rules."
          : "Calendar polling off.",
      );
      reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  /** v1.439.0: DELETE /triggers/calendar/url — the daemon says whether a
   *  saved link was really there (`removed`). */
  async function removeCalendarUrl() {
    setErr(null);
    setOk(null);
    try {
      const res = await del<{ removed?: boolean }>("/triggers/calendar/url");
      setOk(
        res?.removed
          ? "The saved calendar link was removed. Calendar events no longer start rules until you paste a new link."
          : "There was no saved calendar link to remove.",
      );
      reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <Card title="Triggers" icon={<Antenna size={15} />}>
      <p className="mb-4 text-[12px] text-zinc-500">
        Let the world start the work. These sources fire the same rules above — each still runs
        through the normal permission engine, and each is off until you turn it on.
      </p>
      <div className="space-y-3">
        {/* Calendar — the one trigger configured entirely here. */}
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <CalendarClock size={15} className="text-amber-300" />
              <span className="text-sm font-medium text-zinc-100">Calendar</span>
              <Badge value={cal?.enabled ? "on" : "off"} tone={cal?.enabled ? "green" : "slate"} />
              {cal?.has_url && (
                <>
                  <span className="text-[11px] text-zinc-500">URL saved</span>
                  {/* v1.439.0 (chat-first Wave 6): DELETE /triggers/calendar/url
                      had no door. Two presses, calm; the switch is left alone. */}
                  <span data-testid="calendar-url-remove">
                    <ConfirmButton
                      variant="calm"
                      onConfirm={removeCalendarUrl}
                      label="Remove"
                      confirmLabel="Remove the link?"
                      title="Forget the saved calendar link (press twice). Calendar events stop starting rules until you paste a new one."
                    />
                  </span>
                </>
              )}
            </div>
            <Toggle enabled={enabled} busy={busy} onChange={() => setEnabled((v) => !v)} />
          </div>
          <div className="mt-3 grid gap-3 sm:grid-cols-[1fr_auto]">
            <div>
              <label htmlFor={`${fid}-ical`} className="mb-1.5 block text-[12px] font-medium text-zinc-400">
                iCal URL
              </label>
              <input
                id={`${fid}-ical`}
                type="url"
                value={icsUrl}
                onChange={(e) => setIcsUrl(e.target.value)}
                placeholder={
                  cal?.has_url
                    ? "•••••• saved — paste a new URL to replace"
                    : "https://calendar.google.com/…/basic.ics"
                }
                className="field font-mono"
              />
            </div>
            <div>
              <label htmlFor={`${fid}-lead`} className="mb-1.5 block text-[12px] font-medium text-zinc-400">
                Lead (min)
              </label>
              <input
                id={`${fid}-lead`}
                type="number"
                min={0}
                value={lead}
                onChange={(e) => setLead(e.target.value)}
                placeholder="15"
                className="field w-24 font-mono"
              />
            </div>
          </div>
          <div className="mt-1.5 text-[11px] text-zinc-600">
            Paste your calendar&apos;s secret iCal address (Google/Outlook/iCloud all provide one).
            Fires this many minutes before each event.
          </div>
          <div className="mt-3">
            <button type="button" onClick={saveCalendar} disabled={busy} className="btn-accent">
              {busy ? <LoaderInline label="Saving…" /> : "Save calendar trigger"}
            </button>
          </div>
          {ok && (
            <div className="mt-2">
              <SuccessNote>{ok}</SuccessNote>
            </div>
          )}
          {err && (
            <div className="mt-2">
              <ErrorNote>{err}</ErrorNote>
            </div>
          )}
        </div>

        {/* Email — configured as an inbound channel elsewhere. */}
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Mail size={15} className="text-emerald-300" />
              <span className="text-sm font-medium text-zinc-100">Email</span>
              <Badge
                value={emailActive ? "active" : "not set up"}
                tone={emailActive ? "green" : "slate"}
              />
            </div>
            <Link
              href="/channels"
              className="inline-flex items-center gap-1 text-[12px] text-accent-soft underline-offset-2 hover:underline"
            >
              Notifications <ArrowRight size={12} />
            </Link>
          </div>
          {/* v1.314.0: the page is Notifications and a row is a destination
              (VOCABULARY.md) — "Channels" was the retired word. Route unchanged. */}
          <div className="mt-2 text-[11px] text-zinc-600">
            Email triggers fire when a message lands in an email destination that listens for
            incoming mail. Add one on the{" "}
            <Link href="/channels" className="text-accent-soft underline-offset-2 hover:underline">
              Notifications
            </Link>{" "}
            page, then add an <span className="text-zinc-400">Email arrives</span> rule above.
          </div>
        </div>

        {/* Slack — inbound Slack channel elsewhere. */}
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Hash size={15} className="text-violet-300" />
              <span className="text-sm font-medium text-zinc-100">Slack</span>
              <Badge
                value={slackActive ? "active" : "not set up"}
                tone={slackActive ? "green" : "slate"}
              />
            </div>
            <Link
              href="/channels"
              className="inline-flex items-center gap-1 text-[12px] text-accent-soft underline-offset-2 hover:underline"
            >
              Notifications <ArrowRight size={12} />
            </Link>
          </div>
          <div className="mt-2 text-[11px] text-zinc-600">
            Slack triggers fire on incoming Slack messages. Set up a Slack destination that listens
            for incoming messages on the{" "}
            <Link href="/channels" className="text-accent-soft underline-offset-2 hover:underline">
              Notifications
            </Link>{" "}
            page, then add a <span className="text-zinc-400">Slack message</span> rule above.
          </div>
        </div>
      </div>
    </Card>
  );
}
