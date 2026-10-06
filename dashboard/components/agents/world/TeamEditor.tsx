"use client";

/**
 * A project's TEAM (v1.304.0) — "you pick, Jarvis suggests".
 *
 * The user curates who sits at a project's round table: add or remove faces
 * from the roster, with Jarvis's suggestions first — agents that already
 * worked on this project, each with the daemon's WHY ("worked on 3 tasks here
 * in the last 30 days"). An agent may sit on several teams. Nothing is saved
 * until Save: the draft is the user's, and a half-built team never reaches
 * the daemon (or the room's panel).
 *
 * Two shapes, one component: `build` is the empty-team step that stands where
 * the table will be; `edit` is the same editor opened from the world header.
 */

import { useEffect, useMemo, useState } from "react";
import { Check, Plus, Sparkles, Users, X } from "lucide-react";
import { put, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import AgentFace from "@/components/agents/AgentFace";
import { rosterAvatarSrc, type RosterEntry } from "@/components/agents/RosterStrip";
import { ErrorNote, LoaderInline, SkeletonRows } from "@/components/ui";
import {
  REMOTE_SEES,
  bareMemberName,
  memberAvatar,
  memberSource,
  teamWireNames,
  type TeamResponse,
  type WorldMember,
} from "@/lib/agentWorlds";

function Face({ m, size }: { m: WorldMember; size: number }) {
  const rel = memberAvatar(m);
  return (
    <AgentFace
      name={bareMemberName(m.name)}
      size={size}
      avatarUrl={rel ? rosterAvatarSrc(rel, m.last_active) : undefined}
      title=""
    />
  );
}

export function TeamEditor({
  projectId,
  projectName,
  roster,
  mode,
  onSaved,
  onCancel,
}: {
  projectId: string;
  projectName?: string;
  /** The page's GET /agents/roster rows — the picker's catalog. */
  roster: RosterEntry[];
  mode: "build" | "edit";
  /** The saved team, as the daemon answered it (or the draft when it
   *  answered nothing usable), and the room the daemon re-seated (if any). */
  onSaved: (team: WorldMember[], threadId: string | null) => void | Promise<void>;
  onCancel?: () => void;
}) {
  const path = `/projects/${encodeURIComponent(projectId)}/team`;
  const { data, error, loading } = useApi<TeamResponse>(path);
  const [draft, setDraft] = useState<WorldMember[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Seed the draft ONCE from the daemon's team; later polls of nothing (this
  // is a single GET) never clobber what the user is arranging.
  useEffect(() => {
    if (draft === null && data) setDraft(Array.isArray(data.team) ? data.team : []);
  }, [data, draft]);

  const team = draft ?? [];
  const onTeam = useMemo(() => new Set(team.map((m) => m.name)), [team]);
  const suggestions = (Array.isArray(data?.suggestions) ? data!.suggestions : []).filter(
    (s) => s && typeof s.name === "string" && !onTeam.has(s.name),
  );
  const suggested = new Set(suggestions.map((s) => s.name));
  const others = roster.filter((e) => !onTeam.has(e.name) && !suggested.has(e.name));

  function add(m: WorldMember) {
    setDraft((cur) => {
      const list = cur ?? [];
      return list.some((x) => x.name === m.name) ? list : [...list, m];
    });
  }
  function remove(name: string) {
    setDraft((cur) => (cur ?? []).filter((m) => m.name !== name));
  }

  async function save() {
    setSaving(true);
    setSaveError(null);
    try {
      // The daemon validates, saves AND re-seats the room's panel; it answers
      // {team, suggestions, thread_id}.
      const res = await put<Partial<TeamResponse> & { thread_id?: string | null }>(path, {
        members: teamWireNames(team),
      });
      const saved = res && Array.isArray(res.team) ? res.team : team;
      const tid = res && typeof res.thread_id === "string" && res.thread_id ? res.thread_id : null;
      await onSaved(saved, tid);
    } catch (e) {
      setSaveError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  const changed =
    draft !== null &&
    JSON.stringify(teamWireNames(draft)) !==
      JSON.stringify(teamWireNames(Array.isArray(data?.team) ? data!.team : []));

  return (
    <section
      data-testid="team-editor"
      data-mode={mode}
      className="card-surface space-y-4 p-4"
      aria-label={mode === "build" ? "Build the team" : "Edit the team"}
    >
      <header className="space-y-1">
        <h2 className="flex items-center gap-2 text-[15px] font-semibold text-zinc-100">
          <Users size={16} className="text-accent-soft" aria-hidden />
          {mode === "build" ? "Build the team" : "Edit the team"}
        </h2>
        <p className="text-sm text-zinc-500">
          {mode === "build"
            ? `Pick who sits at ${projectName ? `${projectName}'s` : "this project's"} round table. Jarvis suggests the agents that already worked here; an agent can sit on several teams.`
            : "Add or remove faces. The round table seats whoever is on the team."}
        </p>
      </header>

      {!data && (loading || !error) ? (
        <SkeletonRows rows={3} />
      ) : !data && error ? (
        <ErrorNote>
          {error.status === 0
            ? "The daemon looks offline — the team can't be read right now."
            : error.message || `The team could not be read (HTTP ${error.status}).`}
        </ErrorNote>
      ) : (
        <>
          <div>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
              On the team · {team.length}
            </h3>
            {team.length === 0 ? (
              <p data-testid="team-empty" className="text-sm text-zinc-500">
                Nobody yet — add a suggestion or anyone from your agents below.
              </p>
            ) : (
              <ul className="flex flex-wrap gap-2">
                {team.map((m) => (
                  <li
                    key={m.name}
                    data-testid={`team-member-${m.name}`}
                    className="flex items-center gap-2 rounded-full border hairline bg-white/[0.03] py-1 pl-1 pr-2"
                  >
                    <Face m={m} size={40} />
                    <span className="text-sm text-zinc-200">{bareMemberName(m.name)}</span>
                    <button
                      type="button"
                      data-testid={`team-remove-${m.name}`}
                      onClick={() => remove(m.name)}
                      aria-label={`Remove ${bareMemberName(m.name)} from the team`}
                      className="rounded-full p-1 text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-200"
                    >
                      <X size={13} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {team.some((m) => memberSource(m) === "remote") && (
            <ul className="space-y-1" aria-label="What remote agents see">
              {team
                .filter((m) => memberSource(m) === "remote")
                .map((m) => (
                  <li
                    key={m.name}
                    data-testid={`team-sees-${m.name}`}
                    className="rounded-lg border hairline bg-white/[0.02] px-2.5 py-1.5 text-xs text-zinc-400"
                  >
                    <span className="font-medium text-zinc-300">{bareMemberName(m.name)}:</span>{" "}
                    {m.sees || REMOTE_SEES}.
                  </li>
                ))}
            </ul>
          )}

          {suggestions.length > 0 && (
            <div>
              <h3 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
                <Sparkles size={12} aria-hidden /> Jarvis suggests
              </h3>
              <ul className="space-y-1.5">
                {suggestions.map((s) => (
                  <li
                    key={s.name}
                    data-testid={`team-suggestion-${s.name}`}
                    className="flex items-center gap-3 rounded-xl border hairline px-2 py-1.5"
                  >
                    <Face m={s} size={40} />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm text-zinc-200">{bareMemberName(s.name)}</span>
                      {s.why && (
                        <span className="block truncate text-xs text-zinc-500">{s.why}</span>
                      )}
                    </span>
                    <button
                      type="button"
                      data-testid={`team-add-${s.name}`}
                      onClick={() => add(s)}
                      className="btn-ghost shrink-0 text-xs"
                    >
                      <Plus size={13} /> Add
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
              Your agents
            </h3>
            {others.length === 0 ? (
              <p className="text-sm text-zinc-500">Everyone you have is on this team.</p>
            ) : (
              <ul className="grid gap-1.5 sm:grid-cols-2">
                {others.map((e) => (
                  <li key={e.name}>
                    <button
                      type="button"
                      data-testid={`team-add-${e.name}`}
                      onClick={() =>
                        add({
                          name: e.name,
                          kind: e.kind,
                          avatar: e.avatar ?? null,
                          last_active: e.last_active ?? null,
                          description: e.description,
                        })
                      }
                      className="flex w-full items-center gap-3 rounded-xl border hairline px-2 py-1.5 text-left hover:border-accent/40"
                      title={e.description || undefined}
                    >
                      <Face m={{ name: e.name, avatar: e.avatar, last_active: e.last_active }} size={40} />
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm text-zinc-200">{bareMemberName(e.name)}</span>
                        {e.kind === "remote" && (
                          <span className="block truncate text-xs text-zinc-500" title={REMOTE_SEES}>
                            Sees only what you type here
                          </span>
                        )}
                        {e.description && (
                          <span className="block truncate text-xs text-zinc-500">
                            {e.description}
                          </span>
                        )}
                      </span>
                      <Plus size={13} className="shrink-0 text-zinc-500" aria-hidden />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <p data-testid="team-reseat-note" className="text-xs text-zinc-500">
            Saving re-seats the round table: everyone on the team gets a seat, and anyone seated
            in the room who is not on the team is removed from it.
          </p>

          {saveError && <ErrorNote>{saveError}</ErrorNote>}

          <div className="flex flex-wrap items-center justify-end gap-2">
            {onCancel && (
              <button type="button" onClick={onCancel} className="btn-ghost text-sm" disabled={saving}>
                Cancel
              </button>
            )}
            <button
              type="button"
              data-testid="team-save"
              onClick={() => void save()}
              disabled={saving || team.length === 0 || (mode === "edit" && !changed)}
              className="btn-accent text-sm"
            >
              {saving ? (
                <LoaderInline label="Saving…" />
              ) : (
                <>
                  <Check size={13} /> {mode === "build" ? "Seat the team" : "Save team"}
                </>
              )}
            </button>
          </div>
        </>
      )}
    </section>
  );
}

export default TeamEditor;
