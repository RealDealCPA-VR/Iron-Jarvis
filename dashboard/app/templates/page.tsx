"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  BookMarked,
  Plus,
  Play,
  Bot,
  Cpu,
  Info,
  History,
  Pencil,
  Sparkles,
  TriangleAlert,
  X,
} from "lucide-react";
import { get, post, patch, del, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import type { AgentsResponse, ModelOption } from "@/lib/types";
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
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { timeAgo } from "@/lib/format";

/** One thing a template needs before it can actually run (v1.128.0). */
interface Requirement {
  key: string;
  label: string;
  ok: boolean;
  detail: string;
  /** Dashboard page that sets this up (e.g. "/secrets"). */
  setup_path: string;
  setup_label: string;
}

/** A saved prompt/template record returned by GET /templates. */
interface Template {
  id: string;
  name: string;
  agent_type: string;
  task: string;
  /** "Use this when…" note explaining when to reach for this template. */
  description: string;
  provider?: string | null;
  model?: string | null;
  created_at: string;
  requirements?: Requirement[];
  /** True when every requirement is met — safe to run right now. */
  ready?: boolean;
}

/** A curated starter from GET /templates/starters. */
interface Starter {
  id: string;
  name: string;
  task: string;
  description: string;
  agent_type: string;
  requirements?: Requirement[];
  ready?: boolean;
  already_added?: boolean;
}

/** Unmet needs, each with a link to the exact page that fixes it. */
function RequirementChips({ requirements }: { requirements?: Requirement[] }) {
  const unmet = (requirements ?? []).filter((r) => !r.ok);
  if (unmet.length === 0) return null;
  return (
    <div className="mt-2 space-y-1">
      {unmet.map((r) => (
        <div
          key={r.key}
          className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-400/20 bg-amber-400/[0.06] px-2.5 py-1.5 text-[12px] text-amber-200/90"
        >
          <TriangleAlert size={12} className="shrink-0" aria-hidden="true" />
          <span className="min-w-0">{r.detail}</span>
          <Link
            href={r.setup_path}
            className="ml-auto shrink-0 font-medium text-accent-soft underline-offset-2 hover:underline"
          >
            Set up in {r.setup_label} →
          </Link>
        </div>
      ))}
    </div>
  );
}

/** A repeated task mined from history (GET /templates/suggestions). */
interface TemplateSuggestion {
  name: string;
  task: string;
  count: number;
}

/** A stable key for a {provider, model} pair used as the <select> value. */
const modelKey = (m: ModelOption) => `${m.provider}|${m.model}`;

/** Fallback agent types when the daemon hasn't reported any agents yet. */
const FALLBACK_AGENTS = ["builder", "planner", "researcher", "reviewer", "supervisor"];

export default function TemplatesPage() {
  const { data, error, loading, reload } = useApi<{ templates: Template[] }>(
    "/templates",
  );
  const { data: agentsData } = useApi<AgentsResponse>("/agents");
  const { data: modelsData } = useApi<{ models: ModelOption[] }>("/models");

  const offline = error && error.status === 0;

  const templates = [...(data?.templates ?? [])].sort(
    (a, b) =>
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  );

  const agentTypes = (() => {
    const names = [
      ...(agentsData?.builtin ?? []),
      ...(agentsData?.dynamic ?? []).map((d) => d.name),
    ];
    return names.length ? names : FALLBACK_AGENTS;
  })();
  const models = modelsData?.models ?? [];

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [agentType, setAgentType] = useState("");
  const [task, setTask] = useState("");
  const [model, setModel] = useState(""); // "provider|model", "" = default
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  // Editing an existing template reuses the same form (v1.128.0 — fixing a
  // typo used to mean delete + retype everything).
  const [editingId, setEditingId] = useState<string | null>(null);

  function resetForm() {
    setName("");
    setDescription("");
    setTask("");
    setAgentType("");
    setModel("");
    setEditingId(null);
  }

  function startEdit(t: Template) {
    setEditingId(t.id);
    setName(t.name);
    setDescription(t.description ?? "");
    setAgentType(t.agent_type);
    setTask(t.task);
    setModel(t.provider && t.model ? `${t.provider}|${t.model}` : "");
    setOk(null);
    setFormError(null);
    if (typeof window !== "undefined") window.scrollTo({ top: 0, behavior: "smooth" });
  }

  /* ---- Starter library (curated, add any time) --------------------------- */
  const { data: startersData, reload: reloadStarters } = useApi<{
    starters: Starter[];
  }>("/templates/starters");
  const starters = startersData?.starters ?? [];
  const [starterBusy, setStarterBusy] = useState<string | null>(null);

  async function addStarter(s: Starter) {
    setStarterBusy(s.id);
    setOk(null);
    setFormError(null);
    try {
      await post("/templates", {
        name: s.name,
        task: s.task,
        agent_type: s.agent_type,
        description: s.description,
      });
      setOk(`Template "${s.name}" added from the starter library.`);
      reload();
      reloadStarters();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setStarterBusy(null);
    }
  }

  /* ---- Suggested templates (fetched once; card hidden when empty) -------- */
  const [suggestions, setSuggestions] = useState<TemplateSuggestion[]>([]);
  const [sugBusy, setSugBusy] = useState<string | null>(null);
  const [sugOk, setSugOk] = useState<string | null>(null);
  const [sugError, setSugError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    get<{ suggestions: TemplateSuggestion[] }>("/templates/suggestions")
      .then((d) => {
        if (!cancelled) setSuggestions(d.suggestions ?? []);
      })
      .catch(() => {
        /* best-effort — no suggestions card when unavailable */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function saveSuggestion(s: TemplateSuggestion) {
    setSugBusy(s.name);
    setSugOk(null);
    setSugError(null);
    try {
      await post<unknown>("/templates", {
        name: s.name,
        task: s.task,
        description: `Suggested — you've run this ${s.count} times`,
      });
      setSuggestions((prev) => prev.filter((x) => x.name !== s.name));
      setSugOk(`Template "${s.name}" saved.`);
      reload();
    } catch (err) {
      setSugError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSugBusy(null);
    }
  }

  // The agent_type to submit: explicit choice, else first known type.
  const effectiveAgent = agentType || agentTypes[0] || "general";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !task.trim()) return;
    setBusy(true);
    setFormError(null);
    setOk(null);
    const [provider, modelName] = model ? model.split("|") : ["", ""];
    const body: Record<string, unknown> = {
      name: name.trim(),
      task: task.trim(),
      agent_type: effectiveAgent,
      description: description.trim(),
    };
    if (provider) body.provider = provider;
    if (modelName) body.model = modelName;
    try {
      if (editingId) {
        // clear_model drops a previously pinned model when "Session default"
        // is selected — omitting the fields would leave the old pin in place.
        if (!provider) body.clear_model = true;
        await patch(`/templates/${encodeURIComponent(editingId)}`, body);
        setOk(`Template "${name.trim()}" updated.`);
      } else {
        await post("/templates", body);
        setOk(`Template "${name.trim()}" saved.`);
      }
      resetForm();
      reload();
      reloadStarters();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setOk(null);
    setFormError(null);
    try {
      await del(`/templates/${encodeURIComponent(id)}`);
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : String(err));
    }
  }

  /** Deep-link that prefills the New Session form (nav-palette-pwa contract). */
  function useHref(t: Template): string {
    let url = `/sessions?new=1&task=${encodeURIComponent(t.task)}&agent=${encodeURIComponent(t.agent_type)}`;
    // Carry the saved provider/model too, so "Use" runs the template as saved.
    if (t.provider && t.model) {
      url += `&model=${encodeURIComponent(`${t.provider}|${t.model}`)}`;
    }
    return url;
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Templates"
          subtitle="Saved prompts you reuse. Pick one to start a new session with the task and agent prefilled."
        />
      </Reveal>
      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <Reveal>
        <Card>
          <div className="flex items-start gap-2.5 text-sm text-zinc-400">
            <Info
              size={16}
              className="mt-0.5 shrink-0 text-accent-soft/80"
              aria-hidden="true"
            />
            <p>
              A template is a task you run often, saved as one click. Run it
              from here or from the Overview&apos;s &ldquo;Your apps&rdquo;
              tiles. The description tells future-you when to use it.
            </p>
          </div>
        </Card>
      </Reveal>

      <Reveal>
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-1">
            <Card
              title={editingId ? "Edit template" : "New template"}
              icon={editingId ? <Pencil size={15} /> : <Plus size={15} />}
            >
              <form onSubmit={submit} className="space-y-3.5">
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Name
                  </label>
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Daily standup digest"
                    className="field"
                  />
                </div>

                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    When to use it{" "}
                    <span className="text-zinc-600">(description)</span>
                  </label>
                  <input
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="e.g. Use each morning to get oriented"
                    className="field"
                  />
                </div>

                <div>
                  <label className="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    <Bot size={12} /> Agent type
                  </label>
                  <select
                    aria-label="Agent type"
                    value={effectiveAgent}
                    onChange={(e) => setAgentType(e.target.value)}
                    className="field"
                  >
                    {agentTypes.map((a) => (
                      <option key={a} value={a}>
                        {a}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Task
                  </label>
                  <textarea
                    value={task}
                    onChange={(e) => setTask(e.target.value)}
                    placeholder="Summarize my unread emails and draft replies…"
                    rows={4}
                    className="field resize-y"
                  />
                </div>

                <div>
                  <label className="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    <Cpu size={12} /> Model{" "}
                    <span className="text-zinc-600">(optional)</span>
                  </label>
                  <select
                    aria-label="Model"
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    className="field"
                  >
                    <option value="">Session default</option>
                    {/* A pinned model that is no longer connected still shows
                        (marked), so editing doesn't silently repoint it. */}
                    {model && !models.some((m) => modelKey(m) === model) && (
                      <option value={model}>
                        {model.replace("|", " · ")} (saved — not connected)
                      </option>
                    )}
                    {models.map((m) => (
                      <option key={modelKey(m)} value={modelKey(m)}>
                        {m.provider} · {m.model}
                      </option>
                    ))}
                  </select>
                </div>

                <button
                  type="submit"
                  disabled={busy || !name.trim() || !task.trim()}
                  className="btn-accent w-full"
                >
                  {busy ? (
                    <LoaderInline label="Saving…" />
                  ) : editingId ? (
                    <>
                      <Pencil size={14} /> Save changes
                    </>
                  ) : (
                    <>
                      <Plus size={14} /> Save template
                    </>
                  )}
                </button>
                {editingId && (
                  <button
                    type="button"
                    onClick={resetForm}
                    className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-400 transition-colors hover:text-zinc-200"
                  >
                    <X size={13} /> Cancel editing
                  </button>
                )}
                {ok && <SuccessNote>{ok}</SuccessNote>}
                {formError && <ErrorNote>{formError}</ErrorNote>}
              </form>
            </Card>
          </div>

          <div className="space-y-6 lg:col-span-2">
            {suggestions.length > 0 && (
              <Card
                title="Suggested from your history"
                icon={<History size={15} />}
              >
                <div className="space-y-2.5">
                  {suggestions.map((s) => (
                    <div
                      key={s.name}
                      className="rounded-xl border border-white/[0.06] bg-white/[0.015] px-4 py-3 transition-colors hover:border-white/10 hover:bg-white/[0.03]"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-medium text-zinc-100">
                              {s.name}
                            </span>
                            <Badge value={`seen ${s.count}×`} tone="cyan" />
                          </div>
                          <p className="mt-1.5 line-clamp-2 text-sm text-zinc-400">
                            {s.task}
                          </p>
                        </div>
                        <button
                          type="button"
                          onClick={() => void saveSuggestion(s)}
                          disabled={sugBusy !== null}
                          title="Save this repeated task as a one-click template"
                          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/[0.08] px-2.5 py-1 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14] disabled:opacity-50"
                        >
                          {sugBusy === s.name ? (
                            <LoaderInline label="Saving…" />
                          ) : (
                            <>
                              <Plus size={13} /> Save as template
                            </>
                          )}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </Card>
            )}
            {sugOk && <SuccessNote>{sugOk}</SuccessNote>}
            {sugError && <ErrorNote>{sugError}</ErrorNote>}
            {starters.length > 0 && (
              <Card title="Starter library" icon={<Sparkles size={15} />}>
                <p className="mb-3 text-[13px] text-zinc-400">
                  Curated templates you can add with one click. If a starter
                  needs a connection you don&apos;t have yet, it says so below
                  its card — with a link to the page that sets it up.
                </p>
                <div className="grid gap-3 sm:grid-cols-2">
                  {starters.map((s) => (
                    <div
                      key={s.id}
                      className="flex flex-col rounded-xl border border-white/[0.06] bg-white/[0.015] px-4 py-3 transition-colors hover:border-white/10 hover:bg-white/[0.03]"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <span className="font-medium text-zinc-100">{s.name}</span>
                        {s.already_added ? (
                          <span className="inline-flex shrink-0 items-center gap-1 text-[11px] text-zinc-500">
                            Added
                          </span>
                        ) : (
                          <button
                            type="button"
                            onClick={() => void addStarter(s)}
                            disabled={starterBusy !== null}
                            title={`Add "${s.name}" to your templates`}
                            className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-accent/30 bg-accent/[0.08] px-2 py-1 text-[11px] font-medium text-accent-soft transition-colors hover:bg-accent/[0.14] disabled:opacity-50"
                          >
                            {starterBusy === s.id ? (
                              <LoaderInline label="Adding…" />
                            ) : (
                              <>
                                <Plus size={12} /> Add
                              </>
                            )}
                          </button>
                        )}
                      </div>
                      <p className="mt-1 text-[12px] italic text-zinc-500">
                        {s.description}
                      </p>
                      <p className="mt-1.5 line-clamp-2 text-[13px] text-zinc-400">
                        {s.task}
                      </p>
                      <RequirementChips requirements={s.requirements} />
                    </div>
                  ))}
                </div>
              </Card>
            )}
            <Card
              title={`Saved templates${templates.length ? ` · ${templates.length}` : ""}`}
              icon={<BookMarked size={15} />}
            >
              {loading && !data ? (
                <SkeletonRows rows={5} />
              ) : templates.length === 0 ? (
                <Empty icon={<BookMarked size={24} />}>
                  No templates yet. Save a prompt you reuse and start sessions from
                  it in one click.
                </Empty>
              ) : (
                <div className="space-y-2.5">
                  {templates.map((t) => (
                    <div
                      key={t.id}
                      className="rounded-xl border border-white/[0.06] bg-white/[0.015] px-4 py-3 transition-colors hover:border-white/10 hover:bg-white/[0.03]"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-medium text-zinc-100">
                              {t.name}
                            </span>
                            <Badge value={t.agent_type} tone="violet" />
                            {t.provider && t.model && (
                              <span className="inline-flex items-center gap-1 text-[11px] text-zinc-500">
                                <Cpu size={11} /> {t.model}
                              </span>
                            )}
                          </div>
                          {t.description?.trim() && (
                            <p className="mt-1 text-[13px] italic text-zinc-400">
                              &mdash; {t.description.trim()}
                            </p>
                          )}
                          <p className="mt-1.5 line-clamp-2 text-sm text-zinc-400">
                            {t.task}
                          </p>
                          <RequirementChips requirements={t.requirements} />
                          <div className="mt-1.5 text-[11px] text-zinc-600">
                            {timeAgo(t.created_at)}
                          </div>
                        </div>
                        <div className="flex shrink-0 items-center gap-1.5">
                          <Link
                            href={useHref(t)}
                            title={
                              t.ready === false
                                ? "This template needs setup (see below) — it may fail as-is"
                                : "Use this template in a new session"
                            }
                            className="inline-flex items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/[0.08] px-2.5 py-1 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14]"
                          >
                            <Play size={13} /> Use
                          </Link>
                          <button
                            type="button"
                            onClick={() => startEdit(t)}
                            title={`Edit template "${t.name}"`}
                            className="inline-flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-xs text-zinc-400 transition-colors hover:text-zinc-200"
                          >
                            <Pencil size={12} /> Edit
                          </button>
                          <ConfirmButton
                            onConfirm={() => remove(t.id)}
                            label="Delete"
                            title={`Delete template "${t.name}"`}
                          />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>
        </div>
      </Reveal>
    </PageShell>
  );
}
