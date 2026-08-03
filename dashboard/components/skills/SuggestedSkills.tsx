"use client";

// Suggested skills (v1.135.0) — the review queue of the skill learning loop.
// Finished sessions distill into draft skills; skills that underperform get
// improvement drafts. Everything here is suggest-only: nothing lands in the
// skills folder until the user approves it, unless they flip the explicit
// "add automatically" setting. Both checkboxes are REAL persisted settings —
// they bind to what the server returns, never to local optimism (the v1.127.0
// "the box doesn't stick" lesson: controls that read as settings must BE
// settings). "Distill now" surfaces the daemon's answer honestly — a 400
// (no real model available) shows its detail message, never fake success.

import { useState } from "react";
import { Check, Lightbulb, Pencil, Wand2, X } from "lucide-react";
import { ApiError, patch, post } from "@/lib/api";
import type { SkillLearningOverview, SkillProposal } from "@/lib/types";
import { Card, ErrorNote, LoaderInline, SuccessNote } from "@/components/ui";

// User-facing kind badges — plain language, not the wire's "create"/"refine".
const KIND_META: Record<SkillProposal["kind"], { label: string; cls: string }> = {
  create: { label: "New skill", cls: "border-accent/30 bg-accent/10 text-accent-soft" },
  refine: { label: "Improvement", cls: "border-amber-500/30 bg-amber-500/10 text-amber-300" },
};

// Same pane styling as the page's instructions <pre>, sized for an inline peek.
const PRE_CLS =
  "max-h-64 overflow-auto whitespace-pre-wrap rounded-xl border border-white/[0.06] bg-ink-950 p-3 text-xs leading-relaxed text-zinc-300";

export function SuggestedSkills({
  overview,
  onRefresh,
  onSkillsChanged,
}: {
  /** GET /skills/learning — settings + pending proposals + stats. */
  overview: SkillLearningOverview;
  /** Re-fetch the learning overview (after any state-changing call). */
  onRefresh: () => void;
  /** Re-fetch the skills list (an approval writes a real skill). */
  onSkillsChanged: () => void;
}) {
  // Section-level feedback (settings saves, distill outcome).
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [distilling, setDistilling] = useState(false);

  // Per-proposal state, keyed by proposal id.
  const [busy, setBusy] = useState<{ id: string; action: "approve" | "reject" } | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  // Presence of a key = that proposal is in edit mode; the value is the draft.
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});

  // Defensive: never crash the page if the endpoint answered with an
  // unexpected shape (e.g. an older daemon where /skills/{name} shadows
  // /skills/learning and returns a skill named "learning").
  const pending = (overview.proposals ?? []).filter((p) => p.status === "pending");
  const queued = overview.pending_candidates ?? 0;

  /** PATCH /skills/learning/settings — saves the moment it's clicked; the
   *  checkbox re-reads the server's effective state via onRefresh. */
  async function saveSettings(payload: { enabled?: boolean; auto_approve?: boolean }) {
    if (settingsBusy) return;
    setSettingsBusy(true);
    setError(null);
    setNote(null);
    try {
      await patch("/skills/learning/settings", payload);
      onRefresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSettingsBusy(false);
    }
  }

  /** POST /skills/learning/distill — a 400 (no real model) shows its detail. */
  async function distillNow() {
    if (distilling) return;
    setDistilling(true);
    setError(null);
    setNote(null);
    try {
      const res = await post<{ distilled: number }>("/skills/learning/distill");
      const n = res?.distilled ?? 0;
      setNote(
        n > 0
          ? `Distilled ${n} new suggestion${n === 1 ? "" : "s"}.`
          : "Nothing new to distill right now.",
      );
      onRefresh();
      onSkillsChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setDistilling(false);
    }
  }

  /** Approve (optionally with an edited body) or dismiss one proposal. */
  async function decide(p: SkillProposal, action: "approve" | "reject", bodyMd?: string) {
    if (busy) return;
    setBusy({ id: p.id, action });
    setNote(null);
    setRowErrors((e) => {
      const next = { ...e };
      delete next[p.id];
      return next;
    });
    try {
      if (action === "approve") {
        await post<SkillProposal>(
          `/skills/proposals/${encodeURIComponent(p.id)}/approve`,
          bodyMd != null ? { body_md: bodyMd } : {},
        );
      } else {
        await post<SkillProposal>(`/skills/proposals/${encodeURIComponent(p.id)}/reject`);
      }
      setEdits((e) => {
        const next = { ...e };
        delete next[p.id];
        return next;
      });
      setNote(
        action === "approve"
          ? p.kind === "refine"
            ? `Skill “${p.skill_name}” updated.`
            : `Skill “${p.skill_name}” added.`
          : "Suggestion dismissed.",
      );
      onRefresh();
      onSkillsChanged();
    } catch (err) {
      setRowErrors((e) => ({
        ...e,
        [p.id]: err instanceof ApiError ? err.message : String(err),
      }));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card
      title={pending.length > 0 ? `Suggested skills · ${pending.length}` : "Suggested skills"}
      icon={<Lightbulb size={15} />}
      right={
        <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-1.5">
          <label
            className="flex cursor-pointer items-center gap-1.5 text-[11px] text-zinc-400"
            title="After a task finishes, draft a reusable skill from what worked — you review every draft here."
          >
            <input
              type="checkbox"
              checked={overview.enabled}
              disabled={settingsBusy}
              onChange={(e) => void saveSettings({ enabled: e.target.checked })}
              className="h-3.5 w-3.5 shrink-0 accent-accent disabled:opacity-50"
            />
            Learn new skills from finished tasks
          </label>
          <label
            className={`flex items-center gap-1.5 text-[11px] ${
              overview.enabled ? "cursor-pointer text-zinc-400" : "cursor-not-allowed text-zinc-600"
            }`}
            title="Skip the review step: learned skills are added the moment they're drafted. You can still remove them later."
          >
            <input
              type="checkbox"
              checked={overview.auto_approve}
              disabled={settingsBusy || !overview.enabled}
              onChange={(e) => void saveSettings({ auto_approve: e.target.checked })}
              className="h-3.5 w-3.5 shrink-0 accent-accent disabled:opacity-50"
            />
            Add learned skills automatically (skip review)
          </label>
          <button
            type="button"
            onClick={() => void distillNow()}
            disabled={distilling}
            title="Turn recently finished tasks into skill suggestions right now"
            className="btn-ghost py-1 text-[11px] disabled:opacity-50"
          >
            {distilling ? (
              <LoaderInline label="Distilling…" />
            ) : (
              <>
                <Wand2 size={12} /> Distill now
              </>
            )}
          </button>
        </div>
      }
    >
      {note && (
        <div className="mb-3">
          <SuccessNote>{note}</SuccessNote>
        </div>
      )}
      {error && (
        <div className="mb-3">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}

      {pending.length === 0 ? (
        <p className="text-[13px] leading-relaxed text-zinc-600">
          {!overview.enabled
            ? "Skill learning is off. Turn on “Learn new skills from finished tasks” and Iron Jarvis will suggest skills here."
            : queued > 0
              ? `No suggestions to review right now — ${queued} finished ${
                  queued === 1 ? "task is" : "tasks are"
                } queued for the next distill.`
              : "No suggestions right now. As tasks finish, Iron Jarvis drafts skills from what worked — they show up here for your review."}
        </p>
      ) : (
        <ul className="space-y-3">
          {pending.map((p) => {
            const meta = KIND_META[p.kind] ?? KIND_META.create;
            const rowBusy = busy?.id === p.id;
            const approving = rowBusy && busy?.action === "approve";
            const rejecting = rowBusy && busy?.action === "reject";
            const isEditing = p.id in edits;
            const isExpanded = Boolean(expanded[p.id]);
            return (
              <li
                key={p.id}
                className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-3.5"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[13px] text-zinc-100">{p.skill_name}</span>
                  <span
                    className={`inline-flex shrink-0 items-center rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${meta.cls}`}
                  >
                    {meta.label}
                  </span>
                  {!isEditing && (
                    <button
                      type="button"
                      onClick={() => setExpanded((x) => ({ ...x, [p.id]: !x[p.id] }))}
                      className="ml-auto btn-ghost py-1 text-[11px]"
                    >
                      {isExpanded ? "Hide preview" : "Preview"}
                    </button>
                  )}
                </div>
                {p.description && (
                  <p className="mt-1 text-xs leading-relaxed text-zinc-500">{p.description}</p>
                )}

                {isEditing ? (
                  <div className="mt-2.5 space-y-2">
                    <textarea
                      value={edits[p.id]}
                      onChange={(e) => setEdits((x) => ({ ...x, [p.id]: e.target.value }))}
                      rows={12}
                      aria-label="Edit the suggested skill before approving"
                      className="field font-mono text-xs leading-relaxed"
                    />
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() => void decide(p, "approve", edits[p.id])}
                        disabled={rowBusy || !edits[p.id]?.trim()}
                        className="btn-accent py-1.5 text-xs disabled:opacity-50"
                      >
                        {approving ? (
                          <LoaderInline label="Approving…" />
                        ) : (
                          <>
                            <Check size={13} /> Approve with edits
                          </>
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          setEdits((x) => {
                            const next = { ...x };
                            delete next[p.id];
                            return next;
                          })
                        }
                        disabled={rowBusy}
                        className="btn-ghost py-1.5 text-xs disabled:opacity-50"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    {isExpanded &&
                      (p.kind === "refine" && p.prev_body_md ? (
                        // Before/after: the on-disk skill vs the proposed rewrite.
                        <div className="mt-2.5 grid gap-2 md:grid-cols-2">
                          <div>
                            <div className="mb-1 text-[10px] font-medium uppercase tracking-[0.12em] text-zinc-500">
                              Current
                            </div>
                            <pre className={PRE_CLS}>{p.prev_body_md}</pre>
                          </div>
                          <div>
                            <div className="mb-1 text-[10px] font-medium uppercase tracking-[0.12em] text-accent-soft">
                              Proposed
                            </div>
                            <pre className={PRE_CLS}>{p.body_md}</pre>
                          </div>
                        </div>
                      ) : (
                        <pre className={`mt-2.5 ${PRE_CLS}`}>{p.body_md}</pre>
                      ))}
                    <div className="mt-2.5 flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() => void decide(p, "approve")}
                        disabled={rowBusy}
                        className="btn-accent py-1.5 text-xs disabled:opacity-50"
                      >
                        {approving ? (
                          <LoaderInline label="Approving…" />
                        ) : (
                          <>
                            <Check size={13} /> Approve
                          </>
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => setEdits((x) => ({ ...x, [p.id]: p.body_md }))}
                        disabled={rowBusy}
                        className="btn-ghost py-1.5 text-xs disabled:opacity-50"
                      >
                        <Pencil size={13} /> Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => void decide(p, "reject")}
                        disabled={rowBusy}
                        className="btn-ghost py-1.5 text-xs disabled:opacity-50"
                      >
                        {rejecting ? (
                          <LoaderInline label="Dismissing…" />
                        ) : (
                          <>
                            <X size={13} /> Dismiss
                          </>
                        )}
                      </button>
                    </div>
                  </>
                )}

                {rowErrors[p.id] && (
                  <div className="mt-2">
                    <ErrorNote>{rowErrors[p.id]}</ErrorNote>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
