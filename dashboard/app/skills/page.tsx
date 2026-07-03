"use client";

import { useState } from "react";
import { Sparkles, BookOpen, Plus, Save, RefreshCw } from "lucide-react";
import { useApi } from "@/lib/useApi";
import { post, ApiError } from "@/lib/api";
import type { Skill, SkillDetail } from "@/lib/types";

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
                  {skills.map((s) => (
                    <li key={s.name}>
                      <button
                        onClick={() => setSelected(s.name)}
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
                      </button>
                    </li>
                  ))}
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
