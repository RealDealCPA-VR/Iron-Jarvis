"use client";

/**
 * SETTINGS › MODEL TEAM (v1.394.0, the long-jobs plan's Phase 5).
 *
 * The user's model team: every custom agent with one or more ROLES. Each row
 * says, in the team's words (lib/modelTeam), its model (the model menu's own
 * name through `modelPickWords` and the /models catalog), where it runs
 * (this PC / your network / cloud), its cost type (free / your plan / pay per
 * use) and its current usage — read from Iron-Proxy's CACHED snapshot only,
 * "Not known yet" when absent, never 0%. Its roles and its account are
 * edited right here (PATCH /agents/{name} — the one door every custom-agent
 * change takes); the agent's own card on the Agents page edits the roles too.
 *
 * **Make a starter team** shows a PREVIEW first (GET /team/starter): each
 * connected local model ticked, Claude Code and Codex unticked ("add to the
 * team"), pay-per-use models only with the switch. Nothing is made until
 * **Create** (POST /team/starter); with no model connected it says so and
 * makes nothing. An older daemon (no /team) renders one quiet line.
 */

import { useCallback, useMemo, useState } from "react";
import { Users } from "lucide-react";
import { ApiError, get, patch, post } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { useModels } from "@/lib/useModels";
import { modelPickTitle, modelPickWords } from "@/lib/onboarding";
import {
  costWords,
  decodeStarter,
  decodeTeam,
  placeWords,
  usageWords,
  type StarterPreview,
  type StarterRow,
  type TeamMember,
} from "@/lib/modelTeam";
import type { ModelOption } from "@/lib/types";
import { Button, Card, ErrorNote, LoaderInline, SuccessNote } from "@/components/ui";
import { TeamRoleChecks } from "@/components/agents/TeamRoleChecks";

interface AccountRow {
  id: string;
  name: string;
  status: string;
}

function decodeAccounts(raw: unknown): Record<string, AccountRow[]> {
  const out: Record<string, AccountRow[]> = { claude: [], codex: [] };
  const r = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  for (const vendor of ["claude", "codex"]) {
    const rows = Array.isArray(r[vendor]) ? (r[vendor] as unknown[]) : [];
    for (const row of rows) {
      const x = row && typeof row === "object" ? (row as Record<string, unknown>) : null;
      if (!x || typeof x.id !== "string" || !x.id) continue;
      out[vendor].push({
        id: x.id,
        name: typeof x.name === "string" && x.name ? x.name : x.id,
        status: typeof x.status === "string" ? x.status : "",
      });
    }
  }
  return out;
}

/** The vendor a member's subscription model belongs to ("" = none). */
function vendorOf(m: Pick<TeamMember, "provider" | "cost" | "vendor">): string {
  return m.cost === "plan" ? m.vendor : "";
}

function modelLabel(provider: string, model: string, models: readonly ModelOption[]): string {
  if (!provider) return "The model of whoever hands it work";
  return modelPickWords({ provider, model }, models);
}

const RoleChecks = TeamRoleChecks;

function MemberRow({
  m,
  models,
  accounts,
  onChanged,
}: {
  m: TeamMember;
  models: readonly ModelOption[];
  accounts: Record<string, AccountRow[]>;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const vendor = vendorOf(m);
  const choices = vendor ? accounts[vendor] ?? [] : [];

  async function save(body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await patch(`/agents/${encodeURIComponent(m.name)}`, body);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const where = [placeWords(m.place), costWords(m.cost)].filter(Boolean).join(" · ");
  return (
    <li
      data-testid={`team-member-${m.name}`}
      className="space-y-1.5 rounded-lg border border-white/[0.05] bg-white/[0.015] px-3 py-2.5"
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="text-sm font-medium text-zinc-100">{m.name}</span>
        <span
          data-testid={`team-model-${m.name}`}
          className="text-[12px] text-zinc-300"
          title={m.provider ? modelPickTitle({ provider: m.provider, model: m.model }) : undefined}
        >
          {modelLabel(m.provider, m.model, models)}
        </span>
        {where ? (
          <span data-testid={`team-where-${m.name}`} className="text-[12px] text-zinc-500">
            {where}
          </span>
        ) : null}
        {m.available === false ? <span className="text-[12px] text-tone-warn">Not connected</span> : null}
        {m.paused ? <span className="text-[12px] text-tone-warn">Paused</span> : null}
      </div>
      {m.description ? <p className="text-[12px] text-zinc-500">{m.description}</p> : null}
      <p data-testid={`team-usage-${m.name}`} className="text-[12px] text-zinc-400">
        Usage: {usageWords(m)}
      </p>
      <RoleChecks
        scope={m.name}
        value={m.roles}
        disabled={busy}
        onChange={(roles) => void save({ roles })}
      />
      {vendor ? (
        <label className="flex flex-wrap items-center gap-2 text-[12px] text-zinc-400">
          Account
          <select
            data-testid={`team-account-${m.name}`}
            aria-label={`Account for ${m.name}`}
            className="field max-w-xs text-xs"
            value={m.account}
            disabled={busy}
            onChange={(e) => void save({ account: e.target.value })}
          >
            <option value="">Iron-Proxy&apos;s pick</option>
            {m.account && !choices.some((c) => c.id === m.account) ? (
              <option value={m.account}>{m.accountName || "The account it was set to"}</option>
            ) : null}
            {choices.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {error ? <ErrorNote>{error}</ErrorNote> : null}
    </li>
  );
}

const GROUP_TITLES: Record<StarterRow["group"], string> = {
  local: "Your local models · free",
  subscription: "Your plans · add to the team",
  paid: "Pay per use · each use is billed",
};

interface StarterPick {
  ticked: boolean;
  name: string;
  roles: string[];
}

function StarterPanel({
  preview,
  models,
  busy,
  onPaid,
  onCreate,
  onCancel,
}: {
  preview: StarterPreview;
  models: readonly ModelOption[];
  busy: boolean;
  onPaid: (on: boolean) => void;
  onCreate: (rows: { key: string; name: string; roles: string[] }[]) => void;
  onCancel: () => void;
}) {
  const [picks, setPicks] = useState<Record<string, StarterPick>>(() =>
    Object.fromEntries(preview.rows.map((r) => [r.key, { ticked: r.ticked, name: r.name, roles: r.roles }])),
  );
  const pickOf = (r: StarterRow): StarterPick => picks[r.key] ?? { ticked: r.ticked, name: r.name, roles: r.roles };
  const set = (key: string, next: Partial<StarterPick>, r: StarterRow) =>
    setPicks((prev) => ({ ...prev, [key]: { ...pickOf(r), ...prev[key], ...next } }));
  const ticked = preview.rows.filter((r) => !r.exists && pickOf(r).ticked);

  return (
    <div data-testid="team-starter" className="space-y-3 rounded-xl border border-white/[0.06] p-3">
      <p className="text-[12px] text-zinc-400">
        A preview: nothing is added until you press Create. Local models come first and cost nothing.
      </p>
      <label className="inline-flex items-center gap-2 text-[12px] text-zinc-300">
        <input
          type="checkbox"
          data-testid="team-starter-paid"
          checked={preview.paid}
          disabled={busy}
          onChange={(e) => onPaid(e.target.checked)}
        />
        Include pay-per-use models
        {!preview.paid && preview.paidAvailable ? (
          <span className="text-zinc-500">({preview.paidAvailable} connected)</span>
        ) : null}
      </label>
      {preview.rows.length === 0 ? (
        <p data-testid="team-starter-note" className="text-sm text-zinc-400">
          {preview.note || "No model is connected yet."}
        </p>
      ) : (
        (["local", "subscription", "paid"] as const).map((group) => {
          const rows = preview.rows.filter((r) => r.group === group);
          if (!rows.length) return null;
          return (
            <div key={group} className="space-y-1.5">
              <p className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">{GROUP_TITLES[group]}</p>
              <ul className="space-y-1.5">
                {rows.map((r) => {
                  const p = pickOf(r);
                  return (
                    <li
                      key={r.key}
                      data-testid={`team-starter-row-${r.key}`}
                      className="space-y-1 rounded-lg border border-white/[0.05] px-3 py-2"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <input
                          type="checkbox"
                          aria-label={`Add ${modelPickWords({ provider: r.provider, model: r.model }, models)} to the team`}
                          data-testid={`team-starter-tick-${r.key}`}
                          checked={!r.exists && p.ticked}
                          disabled={busy || !!r.exists}
                          onChange={(e) => set(r.key, { ticked: e.target.checked }, r)}
                        />
                        <span className="text-[13px] text-zinc-100">
                          {modelPickWords({ provider: r.provider, model: r.model, name: r.label || undefined }, models)}
                        </span>
                        <span className="text-[12px] text-zinc-500">
                          {[placeWords(r.place), costWords(r.cost)].filter(Boolean).join(" · ")}
                        </span>
                      </div>
                      {r.exists ? (
                        <p className="text-[12px] text-zinc-500">Already on the team as {r.exists}.</p>
                      ) : (
                        <>
                          <label className="flex items-center gap-2 text-[12px] text-zinc-400">
                            Name
                            <input
                              className="field max-w-[14rem] text-xs"
                              data-testid={`team-starter-name-${r.key}`}
                              value={p.name}
                              disabled={busy || !p.ticked}
                              onChange={(e) => set(r.key, { name: e.target.value }, r)}
                            />
                          </label>
                          <RoleChecks
                            scope={`starter-${r.key}`}
                            value={p.roles}
                            disabled={busy || !p.ticked}
                            onChange={(roles) => set(r.key, { roles }, r)}
                          />
                        </>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          data-testid="team-starter-create"
          disabled={busy || ticked.length === 0}
          onClick={() => onCreate(ticked.map((r) => ({ key: r.key, name: pickOf(r).name, roles: pickOf(r).roles })))}
        >
          {busy ? "Creating…" : ticked.length ? `Create (${ticked.length})` : "Create"}
        </Button>
        <Button size="sm" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

export function ModelTeam() {
  const team = useApi<unknown>("/team");
  const accountsApi = useApi<unknown>("/build/accounts");
  const { models } = useModels();
  const [preview, setPreview] = useState<StarterPreview | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const reloadTeam = team.reload;

  const view = useMemo(() => decodeTeam(team.data), [team.data]);
  const accounts = useMemo(() => decodeAccounts(accountsApi.data), [accountsApi.data]);

  const openPreview = useCallback(async (paid: boolean) => {
    setLoadingPreview(true);
    setError(null);
    setDone(null);
    try {
      setPreview(decodeStarter(await get(`/team/starter${paid ? "?paid=1" : ""}`)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLoadingPreview(false);
    }
  }, []);

  async function create(rows: { key: string; name: string; roles: string[] }[]) {
    if (!preview) return;
    setCreating(true);
    setError(null);
    try {
      const out = (await post("/team/starter", { rows, paid: preview.paid })) as {
        created?: unknown;
        skipped?: unknown;
      };
      const created = Array.isArray(out?.created) ? out.created.filter((n) => typeof n === "string") : [];
      const skipped = Array.isArray(out?.skipped) ? out.skipped.length : 0;
      setDone(
        `Added ${created.length} to the team${created.length ? `: ${created.join(", ")}` : ""}.` +
          (skipped ? ` ${skipped} could not be added.` : ""),
      );
      setPreview(null);
      reloadTeam();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setCreating(false);
    }
  }

  if (team.error && team.error.status === 404) {
    return <p className="text-sm text-zinc-500">This version of Iron Jarvis has no model team yet.</p>;
  }

  return (
    <Card title={`Model team${view.members.length ? ` · ${view.members.length}` : ""}`} icon={<Users size={15} />}>
      <div data-testid="model-team" className="space-y-3">
        <p className="text-[12px] leading-relaxed text-zinc-500">
          Agents with one or more roles. When a coding session or an objective hands out a piece of work, the
          planner sees each member&apos;s model, where it runs, what it costs and how much of its plan it has used.
          The model you talk to stays the one you picked.
        </p>
        {team.loading && !team.data ? <LoaderInline /> : null}
        {team.error ? <ErrorNote>{team.error.message}</ErrorNote> : null}
        {team.data && view.members.length === 0 ? (
          <p data-testid="team-empty" className="text-sm text-zinc-400">
            No one is on the team yet. Make a starter team from the models you have connected, or give any
            agent a role on the Agents page.
          </p>
        ) : null}
        {view.members.length ? (
          <ul className="space-y-2">
            {view.members.map((m) => (
              <MemberRow key={m.name} m={m} models={models} accounts={accounts} onChanged={reloadTeam} />
            ))}
          </ul>
        ) : null}
        {done ? <SuccessNote>{done}</SuccessNote> : null}
        {error ? <ErrorNote>{error}</ErrorNote> : null}
        {preview ? (
          <StarterPanel
            key={`${preview.paid}`}
            preview={preview}
            models={models}
            busy={creating || loadingPreview}
            onPaid={(on) => void openPreview(on)}
            onCreate={(rows) => void create(rows)}
            onCancel={() => setPreview(null)}
          />
        ) : (
          <Button
            data-testid="team-starter-open"
            size="sm"
            disabled={loadingPreview}
            onClick={() => void openPreview(false)}
          >
            {loadingPreview ? "Looking at your models…" : "Make a starter team"}
          </Button>
        )}
      </div>
    </Card>
  );
}
