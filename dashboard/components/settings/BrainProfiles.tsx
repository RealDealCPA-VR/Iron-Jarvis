"use client";

/**
 * Settings › Memory & you › Memory profile (v1.406.0; called "Brain
 * profile" until v1.408.0 renamed it).
 *
 * ONE card: the app-wide choice, the profiles, and the form that makes or
 * edits one. The app-wide choice is the schema's `brain_profile` setting,
 * saved through the ONE settings writer (`PUT /settings/values`); the plain
 * schema row is not drawn beside it (SettingsHome.CONTROLLED_IN_SECTION).
 *
 * An older Iron Jarvis has no `/brain` (404): nothing renders. The words live
 * in lib/brainProfiles (pure, no lib/api import — the tests that mock lib/api
 * wholesale are unaffected).
 */

import { useCallback, useEffect, useState } from "react";
import { X } from "lucide-react";
import { ApiError, del, get, patch, post, put } from "@/lib/api";
import { Card, ConfirmButton, ErrorNote, Field, LoaderInline, SuccessNote } from "@/components/ui";
import {
  CONFLICT_VALUES,
  CONFLICT_WORDS,
  GUIDE_HELP,
  SAVE_TO_VALUES,
  SAVE_TO_WORDS,
  STRUCTURAL_ASKS_LINE,
  baseLabel,
  blankDraft,
  changedFields,
  decodeBrainProfile,
  decodeBrainState,
  draftOf,
  type BrainProfileDraft,
  type BrainState,
  type OnConflict,
  type SaveTo,
} from "@/lib/brainProfiles";
import { ProfileCheck } from "./ProfileCheck";

const msg = (e: unknown, fallback: string) => (e as { message?: string })?.message || fallback;

export function BrainProfiles() {
  const [state, setState] = useState<BrainState | null>(null);
  const [gone, setGone] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [skills, setSkills] = useState<string[]>([]);
  // The form: null closed, "" a new profile, else the id being edited.
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<BrainProfileDraft>(blankDraft());
  const [before, setBefore] = useState<BrainProfileDraft>(blankDraft());
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [note, setNote] = useState("");
  const [globalError, setGlobalError] = useState("");
  // v1.408.0: an older Iron Jarvis has no check routes (404): no buttons.
  const [noCheck, setNoCheck] = useState(false);

  const load = useCallback(async () => {
    setLoadError("");
    try {
      const s = decodeBrainState(await get<unknown>("/brain"));
      if (!s) {
        setGone(true);
        return;
      }
      setState(s);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setGone(true);
      else setLoadError(msg(e, "The profiles couldn't load."));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (editing === null || skills.length) return;
    get<{ skills?: { name?: unknown }[] }>("/skills")
      .then((d) =>
        setSkills(
          (Array.isArray(d?.skills) ? d.skills : [])
            .map((s) => (typeof s?.name === "string" ? s.name : ""))
            .filter(Boolean),
        ),
      )
      .catch(() => setSkills([]));
  }, [editing, skills.length]);

  if (gone) return null;
  if (!state) {
    return loadError ? (
      <ErrorNote>
        {loadError}{" "}
        <button type="button" onClick={() => void load()} className="underline underline-offset-2">
          Try again
        </button>
      </ErrorNote>
    ) : (
      <LoaderInline label="Loading profiles…" />
    );
  }

  const { profiles, bases, selected } = state;

  async function chooseGlobal(id: string) {
    setGlobalError("");
    setNote("");
    try {
      await put("/settings/values", { values: { brain_profile: id } });
      setState((s) => (s ? { ...s, selected: id } : s));
      setNote(id ? "Saved. Every chat and agent run now uses this profile." : "Saved. No profile is used by default.");
    } catch (e) {
      setGlobalError(msg(e, "Couldn't save the choice."));
    }
  }

  function openNew() {
    const fresh = blankDraft(bases[0]?.name ?? "");
    setDraft(fresh);
    setBefore(fresh);
    setFormError("");
    setNote("");
    setEditing("");
  }

  function openEdit(id: string) {
    const p = profiles.find((x) => x.id === id);
    if (!p) return;
    const d = draftOf(p);
    setDraft(d);
    setBefore(d);
    setFormError("");
    setNote("");
    setEditing(id);
  }

  async function saveForm() {
    setSaving(true);
    setFormError("");
    try {
      if (editing === "") {
        const made = decodeBrainProfile(await post<unknown>("/brain/profiles", draft));
        setNote(made ? `Made ${made.name}.` : "Made the profile.");
      } else if (editing) {
        const body = changedFields(before, draft);
        if (Object.keys(body).length) {
          await patch(`/brain/profiles/${encodeURIComponent(editing)}`, body);
        }
        setNote("Saved.");
      }
      setEditing(null);
      await load();
    } catch (e) {
      setFormError(msg(e, "Couldn't save the profile."));
    } finally {
      setSaving(false);
    }
  }

  async function remove(id: string) {
    setNote("");
    setGlobalError("");
    try {
      const res = await del<{ cleared?: { app?: unknown; projects?: unknown } }>(
        `/brain/profiles/${encodeURIComponent(id)}`,
      );
      const app = res?.cleared?.app === true;
      const n = typeof res?.cleared?.projects === "number" ? res.cleared.projects : 0;
      const parts = [app ? "the app" : "", n > 0 ? `${n} project${n === 1 ? "" : "s"}` : ""].filter(Boolean);
      setNote(parts.length ? `Deleted. It is no longer used by ${parts.join(" and ")}.` : "Deleted.");
      if (editing === id) setEditing(null);
      await load();
    } catch (e) {
      setGlobalError(msg(e, "Couldn't delete the profile."));
    }
  }

  const set = <K extends keyof BrainProfileDraft>(k: K, v: BrainProfileDraft[K]) =>
    setDraft((d) => ({ ...d, [k]: v }));
  const skillChoices = skills.filter((s) => !draft.skills.includes(s));

  return (
    <Card>
      <div className="space-y-5" data-testid="brain-profiles">
        <Field
          label="Used everywhere"
          hint="Every chat and agent run searches and saves to this profile's memory base, unless a project picks its own."
        >
          <select
            data-testid="brain-global"
            value={profiles.some((p) => p.id === selected) ? selected : ""}
            onChange={(e) => void chooseGlobal(e.target.value)}
            className="field w-full max-w-sm"
          >
            <option value="">No memory profile</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>
        {globalError && <ErrorNote>{globalError}</ErrorNote>}
        {note && <SuccessNote>{note}</SuccessNote>}

        <div>
          <div className="mb-1.5 flex items-center justify-between gap-3">
            <span className="text-[11px] uppercase tracking-[0.1em] text-zinc-400">Profiles</span>
            {editing === null && (
              <button type="button" data-testid="brain-new" onClick={openNew} className="btn-ghost btn-sm">
                New profile
              </button>
            )}
          </div>
          {profiles.length === 0 ? (
            <p className="text-[13px] text-zinc-500">No profiles yet. Make one to pick your main memory base.</p>
          ) : (
            <ul className="divide-y divide-white/[0.05]" data-testid="brain-list">
              {profiles.map((p) => {
                const base = bases.find((b) => b.name === p.primary);
                return (
                  <li key={p.id} data-testid={`brain-row-${p.id}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                    <span className="min-w-0 flex-1">
                      <span className="block text-[14px] text-zinc-100">{p.name}</span>
                      <span className="block text-[12px] text-zinc-500">
                        {baseLabel(bases, p.primary)}
                        {base?.available === false && (
                          <span data-testid={`brain-unreachable-${p.id}`} className="text-tone-warn">
                            {" "}
                            · not reachable right now
                          </span>
                        )}
                      </span>
                    </span>
                    <button
                      type="button"
                      data-testid={`brain-edit-${p.id}`}
                      onClick={() => openEdit(p.id)}
                      className="btn-ghost btn-sm"
                    >
                      Edit
                    </button>
                    <ConfirmButton
                      variant="calm"
                      label="Delete"
                      confirmLabel="Delete this profile?"
                      onConfirm={() => remove(p.id)}
                    />
                    {!noCheck && <ProfileCheck id={p.id} onGone={() => setNoCheck(true)} />}
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {editing !== null && (
          <form
            data-testid="brain-form"
            className="space-y-4 border-t border-white/[0.06] pt-4"
            onSubmit={(e) => {
              e.preventDefault();
              void saveForm();
            }}
          >
            <Field label="Name">
              <input
                data-testid="brain-name"
                value={draft.name}
                onChange={(e) => set("name", e.target.value)}
                placeholder="Notion"
                className="field w-full max-w-sm"
              />
            </Field>
            <Field label="Primary memory base">
              <select
                data-testid="brain-primary"
                value={draft.primary}
                onChange={(e) => set("primary", e.target.value)}
                className="field w-full max-w-sm"
              >
                {!bases.some((b) => b.name === draft.primary) && (
                  <option value={draft.primary}>{draft.primary || "Choose a memory base"}</option>
                )}
                {bases.map((b) => (
                  <option key={b.name} value={b.name}>
                    {b.label}
                    {b.available === false ? " (not reachable)" : ""}
                  </option>
                ))}
              </select>
            </Field>
            <label className="flex items-center gap-2 text-[13px] text-zinc-300">
              <input
                type="checkbox"
                data-testid="brain-search-first"
                checked={draft.search_first}
                onChange={(e) => set("search_first", e.target.checked)}
              />
              Search it before answering
            </label>
            <Field label="Save new knowledge to">
              <select
                data-testid="brain-save-to"
                value={draft.save_to}
                onChange={(e) => set("save_to", e.target.value as SaveTo)}
                className="field w-full max-w-sm"
              >
                {SAVE_TO_VALUES.map((v) => (
                  <option key={v} value={v}>
                    {SAVE_TO_WORDS[v]}
                  </option>
                ))}
              </select>
            </Field>
            <label className="flex items-center gap-2 text-[13px] text-zinc-300">
              <input
                type="checkbox"
                data-testid="brain-prefer-update"
                checked={draft.prefer_update}
                onChange={(e) => set("prefer_update", e.target.checked)}
              />
              Update an existing note instead of adding a duplicate
            </label>
            <Field label="When memory and the conversation disagree">
              <select
                data-testid="brain-conflict"
                value={draft.on_conflict}
                onChange={(e) => set("on_conflict", e.target.value as OnConflict)}
                className="field w-full max-w-sm"
              >
                {CONFLICT_VALUES.map((v) => (
                  <option key={v} value={v}>
                    {CONFLICT_WORDS[v]}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Operating guide" hint={GUIDE_HELP}>
              <input
                data-testid="brain-guide"
                value={draft.guide_ref}
                onChange={(e) => set("guide_ref", e.target.value)}
                placeholder="The note's title or link in the primary base"
                className="field w-full"
              />
            </Field>
            <Field label="Skills to load at the start">
              <select
                data-testid="brain-skill-add"
                value=""
                onChange={(e) => {
                  const v = e.target.value;
                  if (v && !draft.skills.includes(v)) set("skills", [...draft.skills, v]);
                }}
                className="field w-full max-w-sm"
              >
                <option value="">{skillChoices.length ? "Add a skill…" : "No more skills to add"}</option>
                {skillChoices.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </Field>
            {draft.skills.length > 0 && (
              <ul className="-mt-2 flex flex-wrap gap-1.5" data-testid="brain-skills">
                {draft.skills.map((s) => (
                  <li
                    key={s}
                    className="inline-flex items-center gap-1 rounded-full bg-white/[0.05] px-2 py-0.5 text-[12px] text-zinc-300"
                  >
                    {s}
                    <button
                      type="button"
                      aria-label={`Remove ${s}`}
                      onClick={() => set("skills", draft.skills.filter((x) => x !== s))}
                      className="text-zinc-500 hover:text-zinc-200"
                    >
                      <X size={12} aria-hidden />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <label className="flex items-center gap-2 text-[13px] text-zinc-300">
              <input
                type="checkbox"
                data-testid="brain-repo-routing"
                checked={draft.repo_routing}
                onChange={(e) => set("repo_routing", e.target.checked)}
              />
              Send knowledge about code to the repository
            </label>
            {formError && <ErrorNote>{formError}</ErrorNote>}
            <div className="flex flex-wrap items-center gap-2">
              <button type="submit" data-testid="brain-save" disabled={saving} className="btn-accent btn-sm">
                {saving ? <LoaderInline label="Saving…" /> : editing === "" ? "Make profile" : "Save changes"}
              </button>
              <button type="button" onClick={() => setEditing(null)} className="btn-ghost btn-sm">
                Cancel
              </button>
            </div>
          </form>
        )}

        <p className="text-[12px] text-zinc-500" data-testid="brain-structural">
          {STRUCTURAL_ASKS_LINE}
        </p>
      </div>
    </Card>
  );
}
