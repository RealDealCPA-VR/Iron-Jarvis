"use client";

import { useRef, useState } from "react";
import { Sparkles, BookOpen, Plus, Save, RefreshCw, Play, Copy, Check, Cpu } from "lucide-react";
import { useApi } from "@/lib/useApi";
import { post, ApiError } from "@/lib/api";
import type { Skill, SkillDetail, SkillLearningOverview } from "@/lib/types";
import { SuggestedSkills } from "@/components/skills/SuggestedSkills";

/** Response of POST /skills/{name}/apply — a one-shot "use it right now" run. */
interface SkillApplyResult {
  reply: string;
  skill: string;
  provider: string;
  model: string;
}

/** Small copy-to-clipboard button with a transient "Copied" state. */
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable (permissions / insecure context) — quiet no-op */
    }
  }
  return (
    <button
      type="button"
      onClick={() => void copy()}
      title="Copy the reply"
      className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft"
    >
      {copied ? <Check size={12} /> : <Copy size={12} />} {copied ? "Copied" : "Copy"}
    </button>
  );
}

// Where a skill came from — a small colored badge so Claude/Codex skills are
// visually distinct from Iron Jarvis's own.
const SOURCE_META: Record<string, { label: string; cls: string }> = {
  claude: { label: "Claude", cls: "border-orange-500/30 bg-orange-500/10 text-orange-300" },
  codex: { label: "Codex", cls: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300" },
  builtin: { label: "Built-in", cls: "border-accent/30 bg-accent/10 text-accent-soft" },
  user: { label: "Yours", cls: "border-violet-500/30 bg-violet-500/10 text-violet-300" },
  custom: { label: "Custom", cls: "border-sky-500/30 bg-sky-500/10 text-sky-300" },
};

function SourceBadge({ source }: { source?: string }) {
  const meta = SOURCE_META[source ?? "user"] ?? SOURCE_META.user;
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${meta.cls}`}
    >
      {meta.label}
    </span>
  );
}
import {
  Card,
  Spinner,
  OfflineHint,
  Empty,
  SkeletonRows,
  SuccessNote,
  ErrorNote,
  LoaderInline,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

export default function SkillsPage() {
  const { data, error, loading, reload } = useApi<{
    skills: Skill[];
    counts?: Record<string, number>;
  }>("/skills");
  const [selected, setSelected] = useState<string | null>(null);
  const detail = useApi<SkillDetail>(selected ? `/skills/${selected}` : null, [selected]);

  // The skill learning loop (v1.135.0): pending suggestions + settings + per-
  // skill stats. On daemons without the endpoint this just stays null and the
  // page renders exactly as before.
  const learning = useApi<SkillLearningOverview>("/skills/learning");
  const statByName = new Map((learning.data?.stats ?? []).map((st) => [st.skill_name, st]));

  // --- "Use this skill" bubble state -----------------------------------------
  // Cleared whenever a different skill is selected; the ref lets an in-flight
  // run detect that the user switched away so a stale reply never leaks in.
  const [applyReq, setApplyReq] = useState("");
  const [applyBusy, setApplyBusy] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [applyResult, setApplyResult] = useState<SkillApplyResult | null>(null);
  const selectedRef = useRef<string | null>(null);

  function selectSkill(name: string) {
    if (name !== selected) {
      setApplyReq("");
      setApplyError(null);
      setApplyResult(null);
    }
    selectedRef.current = name;
    setSelected(name);
  }

  async function runSkill(e: React.FormEvent) {
    e.preventDefault();
    const target = selected;
    const request = applyReq.trim();
    if (!target || !request || applyBusy) return;
    setApplyBusy(true);
    setApplyError(null);
    setApplyResult(null);
    try {
      const res = await post<SkillApplyResult>(
        `/skills/${encodeURIComponent(target)}/apply`,
        { request },
      );
      if (selectedRef.current !== target) return; // switched skills mid-run
      setApplyResult(res);
    } catch (err) {
      if (selectedRef.current !== target) return;
      setApplyError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setApplyBusy(false);
    }
  }

  // Filter by source (All / Claude / Codex / …) + a re-scan action so newly
  // added external skills show up without restarting the daemon.
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [rescanning, setRescanning] = useState(false);
  async function rescan() {
    setRescanning(true);
    try {
      await post("/skills/rescan");
      reload();
    } catch {
      /* offline — the list just stays as-is */
    } finally {
      setRescanning(false);
    }
  }

  // --- New-skill form state -------------------------------------------------
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [created, setCreated] = useState<string | null>(null);

  const offline = error && error.status === 0;
  const allSkills = data?.skills ?? [];
  const counts = data?.counts ?? {};
  const skills =
    sourceFilter === "all"
      ? allSkills
      : allSkills.filter((s) => (s.source ?? "user") === sourceFilter);
  // Sources present, ordered, for the filter chips (only show chips that exist).
  const sourceOrder = ["user", "claude", "codex", "builtin", "custom"];
  const presentSources = sourceOrder.filter((s) => (counts[s] ?? 0) > 0);
  const canSubmit = !busy && name.trim().length > 0 && instructions.trim().length > 0;

  function resetForm() {
    setName("");
    setDescription("");
    setInstructions("");
    setFormError(null);
  }

  async function createSkill(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setFormError(null);
    setCreated(null);
    try {
      await post("/skills", {
        name: name.trim(),
        description: description.trim(),
        instructions: instructions.trim(),
      });
      setCreated(name.trim());
      setShowForm(false);
      resetForm();
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Skills"
          subtitle="Reusable skills your agents call on for specialized tasks — including the ones you already have in Claude Code and Codex, discovered automatically."
          actions={
            <div className="flex items-center gap-2">
              <button
                onClick={rescan}
                disabled={rescanning}
                title="Re-scan Claude, Codex, and your skill folders"
                className="btn-ghost py-1.5 text-xs disabled:opacity-50"
              >
                <RefreshCw size={14} className={rescanning ? "animate-spin" : ""} /> Rescan
              </button>
              <button
                onClick={() => {
                  setShowForm((v) => !v);
                  setCreated(null);
                  setFormError(null);
                }}
                className={`${showForm ? "btn-ghost" : "btn-accent"} py-1.5 text-xs`}
              >
                <Plus size={14} /> New skill
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

      {created && !showForm && (
        <Reveal>
          <SuccessNote>Skill &ldquo;{created}&rdquo; created.</SuccessNote>
        </Reveal>
      )}

      {/* Suggested skills — full-width review strip above the lists so drafts
          awaiting a decision are seen before the catalog. Absent entirely on
          daemons without the learning loop. */}
      {learning.data && (
        <Reveal>
          <SuggestedSkills
            overview={learning.data}
            onRefresh={learning.reload}
            onSkillsChanged={reload}
          />
        </Reveal>
      )}

      <Reveal>
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="space-y-6 lg:col-span-1">
            {showForm && (
              <Card title="New skill" icon={<Plus size={15} />}>
                <form onSubmit={createSkill} className="space-y-3">
                  <div className="space-y-1.5">
                    <label className="text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-400">
                      Name
                    </label>
                    <input
                      type="text"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="e.g. summarize-pdf"
                      aria-label="Skill name"
                      autoComplete="off"
                      autoFocus
                      className="field text-sm"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-400">
                      Description
                    </label>
                    <input
                      type="text"
                      value={description}
                      onChange={(e) => setDescription(e.target.value)}
                      placeholder="One line on when to use it"
                      aria-label="Skill description"
                      autoComplete="off"
                      className="field text-sm"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-400">
                      Instructions
                    </label>
                    <textarea
                      value={instructions}
                      onChange={(e) => setInstructions(e.target.value)}
                      placeholder={"# How to…\n\nStep-by-step guidance the agent follows."}
                      aria-label="Skill instructions"
                      rows={8}
                      className="field font-mono text-xs leading-relaxed"
                    />
                    <p className="text-[11px] leading-relaxed text-zinc-500">
                      Instructions are what an agent reads when it uses this skill — write
                      them like a how-to.
                    </p>
                  </div>
                  {formError && <ErrorNote>{formError}</ErrorNote>}
                  <div className="flex items-center gap-2">
                    <button
                      type="submit"
                      disabled={!canSubmit}
                      className="btn-accent flex-1 py-1.5 text-xs"
                    >
                      {busy ? (
                        <LoaderInline label="Creating…" />
                      ) : (
                        <>
                          <Save size={14} /> Create skill
                        </>
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setShowForm(false);
                        resetForm();
                      }}
                      className="btn-ghost py-1.5 text-xs"
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              </Card>
            )}

            <Card title={`Available · ${allSkills.length}`} icon={<Sparkles size={15} />}>
              {/* Source filter chips — only sources that actually exist show up. */}
              {presentSources.length > 1 && (
                <div className="mb-3 flex flex-wrap gap-1.5">
                  <button
                    onClick={() => setSourceFilter("all")}
                    className={`rounded-full border px-2 py-0.5 text-[11px] transition-colors ${
                      sourceFilter === "all"
                        ? "border-white/20 bg-white/10 text-zinc-100"
                        : "border-white/10 text-zinc-400 hover:bg-white/[0.04]"
                    }`}
                  >
                    All {allSkills.length}
                  </button>
                  {presentSources.map((src) => {
                    const meta = SOURCE_META[src] ?? SOURCE_META.user;
                    const active = sourceFilter === src;
                    return (
                      <button
                        key={src}
                        onClick={() => setSourceFilter(src)}
                        className={`rounded-full border px-2 py-0.5 text-[11px] transition-colors ${
                          active ? meta.cls : "border-white/10 text-zinc-400 hover:bg-white/[0.04]"
                        }`}
                      >
                        {meta.label} {counts[src]}
                      </button>
                    );
                  })}
                </div>
              )}
              {loading && !data ? (
                <SkeletonRows rows={5} />
              ) : skills.length === 0 ? (
                <Empty icon={<Sparkles size={22} />}>No skills.</Empty>
              ) : (
                <ul className="max-h-[70vh] space-y-1 overflow-auto">
                  {skills.map((s) => {
                    // Subtle usage hint from the learning loop, when it has one.
                    const stat = statByName.get(s.name);
                    const pct =
                      stat?.success_rate == null
                        ? null
                        : Math.round(stat.success_rate <= 1 ? stat.success_rate * 100 : stat.success_rate);
                    return (
                      <li key={s.name}>
                        <button
                          onClick={() => selectSkill(s.name)}
                          className={`w-full rounded-xl border px-3 py-2.5 text-left text-sm transition-colors ${
                            selected === s.name
                              ? "border-accent/30 bg-accent/[0.08] text-accent-soft"
                              : "border-transparent text-zinc-300 hover:border-white/10 hover:bg-white/[0.04]"
                          }`}
                        >
                          <div className="flex items-center gap-2">
                            <span className="min-w-0 flex-1 truncate font-medium">{s.name}</span>
                            <SourceBadge source={s.source} />
                          </div>
                          <div className="truncate text-xs text-zinc-500">{s.description}</div>
                          {stat && stat.use_count > 0 && (
                            <div className="mt-0.5 truncate text-[10px] text-zinc-600">
                              {stat.use_count} {stat.use_count === 1 ? "use" : "uses"}
                              {pct != null ? ` · ${pct}% success` : ""}
                            </div>
                          )}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </Card>
          </div>

          <div className="lg:col-span-2">
            <Card title={selected ?? "Instructions"} icon={<BookOpen size={15} />}>
              {!selected ? (
                <Empty icon={<BookOpen size={22} />}>Select a skill to view its instructions.</Empty>
              ) : detail.loading && !detail.data ? (
                <Spinner />
              ) : detail.data ? (
                <div className="space-y-3">
                  <p className="text-sm text-zinc-400">{detail.data.description}</p>
                  <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-xl border border-white/[0.06] bg-ink-950 p-4 text-xs leading-relaxed text-zinc-300">
                    {detail.data.instructions}
                  </pre>

                  {/* "Use this skill" bubble — run it on a request right here */}
                  <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-3.5">
                    <div className="mb-2.5 text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-400">
                      Use this skill
                    </div>
                    <form onSubmit={runSkill} className="space-y-2.5">
                      <textarea
                        value={applyReq}
                        onChange={(e) => setApplyReq(e.target.value)}
                        rows={3}
                        placeholder="Ask for something using this skill — e.g. 'draft the storyboard for a 30s ad'"
                        aria-label="What do you want this skill to do?"
                        className="field resize-y text-sm"
                      />
                      <div className="flex flex-wrap items-center gap-3">
                        <button
                          type="submit"
                          disabled={applyBusy || !applyReq.trim()}
                          className="btn-accent py-1.5 text-xs"
                        >
                          {applyBusy ? (
                            <LoaderInline label="Running…" />
                          ) : (
                            <>
                              <Play size={14} /> Run
                            </>
                          )}
                        </button>
                        <span className="text-[11px] text-zinc-600">
                          {applyBusy
                            ? "Following the skill's instructions — usually 5–30 seconds."
                            : "Answers right here, following the skill's instructions."}
                        </span>
                      </div>
                    </form>
                    {applyError && (
                      <div className="mt-2.5">
                        <ErrorNote>{applyError}</ErrorNote>
                      </div>
                    )}
                    {applyResult && (
                      <div className="mt-3 overflow-hidden rounded-xl border border-accent/20 bg-accent/[0.04]">
                        <div className="flex items-center justify-between gap-2 border-b border-white/[0.06] px-3.5 py-2">
                          <span className="inline-flex min-w-0 items-center gap-1.5 font-mono text-[11px] text-zinc-500">
                            <Cpu size={11} className="shrink-0" />
                            <span className="truncate">
                              {applyResult.provider} · {applyResult.model}
                            </span>
                          </span>
                          <CopyButton text={applyResult.reply} />
                        </div>
                        <div className="max-h-[45vh] overflow-auto whitespace-pre-wrap px-3.5 py-3 text-sm leading-relaxed text-zinc-200">
                          {applyResult.reply}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <Empty>Could not load skill.</Empty>
              )}
            </Card>
          </div>
        </div>
      </Reveal>
    </PageShell>
  );
}
