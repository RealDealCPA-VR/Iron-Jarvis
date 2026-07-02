"use client";

import { useEffect, useMemo, useState } from "react";
import {
  SlidersHorizontal,
  Save,
  KeyRound,
  Trash2,
  ShieldCheck,
  RotateCcw,
  Wrench,
  DatabaseBackup,
} from "lucide-react";
import { get, put, post, ijToken, setIjToken, ApiError } from "@/lib/api";
import {
  Card,
  OfflineHint,
  SkeletonRows,
  ErrorNote,
  SuccessNote,
  SectionLabel,
  LoaderInline,
  ConfirmButton,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { useDaemon } from "@/lib/daemon";

type FieldType = "text" | "number" | "boolean" | "select";
type Value = string | number | boolean;
type SectionId = "models" | "local" | "automation" | "advanced";

interface FieldDef {
  key: string;
  label: string;
  type: FieldType;
  section: SectionId;
  hint?: string;
  placeholder?: string;
  options?: string[];
  /** Marks settings that only fully apply after a daemon restart. */
  restart?: boolean;
}

interface SectionDef {
  id: SectionId;
  title: string;
  description: string;
  /** Advanced sections render collapsed inside a <details>. */
  advanced?: boolean;
}

// Friendly, plain-language grouping. Each section gets a heading + one-liner.
const SECTIONS: SectionDef[] = [
  {
    id: "models",
    title: "Models & routing",
    description:
      "Which AI answers by default, and how much history Iron Jarvis keeps. Add providers and API keys on the Connections page.",
  },
  {
    id: "local",
    title: "Local & custom models",
    description:
      "Run against a local Ollama server or any OpenAI-compatible endpoint. Leave blank to turn these off.",
  },
  {
    id: "automation",
    title: "Automation & autonomy",
    description:
      "Advanced, and off by default. Lets Iron Jarvis act on your standing goals — always within the caps you set here. Manage goals on the Autonomy page.",
  },
  {
    id: "advanced",
    title: "Advanced",
    description: "Power-user options. Leave these alone unless you know you need them.",
    advanced: true,
  },
];

// Mirrors the daemon's whitelist (`_SETTINGS_KEYS`), grouped into friendly
// sections with plain-language labels + descriptions.
const FIELDS: FieldDef[] = [
  // --- Models & routing ---------------------------------------------------
  {
    key: "default_provider",
    label: "Default provider",
    type: "text",
    section: "models",
    placeholder: "anthropic",
    hint: "The AI service used when a chat doesn't pick one. Manage providers + keys on Connections.",
  },
  {
    key: "default_model",
    label: "Default model",
    type: "text",
    section: "models",
    placeholder: "claude-opus-4-8",
    hint: "Model id used by default for new sessions.",
  },
  {
    key: "event_retention_days",
    label: "Keep activity history",
    type: "number",
    section: "models",
    restart: true,
    hint: "How long the activity log is kept, in days. Use 0 to keep everything forever.",
  },

  // --- Local & custom models ----------------------------------------------
  {
    key: "ollama_base_url",
    label: "Ollama server URL",
    type: "text",
    section: "local",
    placeholder: "http://127.0.0.1:11434",
    hint: "Point at a local Ollama server. Leave blank to disable local models.",
  },
  {
    key: "ollama_model",
    label: "Ollama model",
    type: "text",
    section: "local",
    placeholder: "llama3.1",
    hint: "Default model to use on that Ollama server.",
  },
  {
    key: "custom_base_url",
    label: "Custom endpoint URL",
    type: "text",
    section: "local",
    placeholder: "https://ollama.com",
    hint: "Any OpenAI-compatible endpoint — Ollama Cloud, LM Studio, vLLM, or a private gateway. Add its API key under Connections.",
  },
  {
    key: "custom_model",
    label: "Custom endpoint model",
    type: "text",
    section: "local",
    placeholder: "qwen3-coder",
    hint: "Default model id for that custom endpoint.",
  },

  // --- Automation & autonomy ----------------------------------------------
  {
    key: "max_agent_steps",
    label: "Max steps per run",
    type: "number",
    section: "automation",
    hint: "Safety ceiling on how many tool/loop steps a single agent run may take.",
  },
  {
    key: "autonomy_enabled",
    label: "Autonomy (the pulse)",
    type: "boolean",
    section: "automation",
    restart: true,
    hint: "Let Iron Jarvis deliberate on your standing goals and propose (or, within budget, act). Off by default.",
  },
  {
    key: "autonomy_level",
    label: "Autonomy ceiling",
    type: "select",
    section: "automation",
    options: ["suggest", "act_low", "act_all"],
    hint: "How far it may go. 'suggest' always proposes and never auto-acts; the others let it act, up to the caps below.",
  },
  {
    key: "autonomy_dry_run",
    label: "Dry-run mode",
    type: "boolean",
    section: "automation",
    hint: "Log/propose what it WOULD do, without executing anything.",
  },
  {
    key: "autonomy_kill_switch",
    label: "Emergency stop",
    type: "boolean",
    section: "automation",
    hint: "Immediately blocks every self-initiated action, regardless of the settings above.",
  },
  {
    key: "autonomy_tick_seconds",
    label: "Think every (seconds)",
    type: "number",
    section: "automation",
    restart: true,
    hint: "How often the background loop wakes up to deliberate.",
  },
  {
    key: "autonomy_max_actions_per_day",
    label: "Max actions / day",
    type: "number",
    section: "automation",
    hint: "Global rolling cap on self-initiated actions.",
  },
  {
    key: "autonomy_max_tokens_per_day",
    label: "Max tokens / day",
    type: "number",
    section: "automation",
    hint: "Global rolling token budget for self-initiated work.",
  },
  {
    key: "sentinels_enabled",
    label: "Sentinels (watchers)",
    type: "boolean",
    section: "automation",
    restart: true,
    hint: "Always-on watchers that notice changes and add suggestions to the Autonomy backlog (they never act on their own). Off by default.",
  },
  {
    key: "sentinels_tick_seconds",
    label: "Watch every (seconds)",
    type: "number",
    section: "automation",
    restart: true,
    hint: "How often the watchers check for changes.",
  },

  // --- Advanced -----------------------------------------------------------
  {
    key: "git_native",
    label: "Git-native workspaces",
    type: "boolean",
    section: "advanced",
    hint: "Run each session on its own real git worktree/branch.",
  },
  {
    key: "self_dev_enabled",
    label: "Self-development",
    type: "boolean",
    section: "advanced",
    restart: true,
    hint: "Allow Iron Jarvis to edit its own source code (still review-gated — never auto-merged).",
  },
  {
    key: "self_dev_root",
    label: "Self-development repo root",
    type: "text",
    section: "advanced",
    restart: true,
    placeholder: "C:\\path\\to\\Iron-Jarvis",
    hint: "Path to the Iron Jarvis repo. Only needed when running from an installed package.",
  },
  {
    key: "sandbox_runtime",
    label: "Sandbox runtime",
    type: "select",
    section: "advanced",
    options: ["native", "docker"],
    restart: true,
    hint: "How tool execution is isolated. 'docker' requires Docker to be installed.",
  },
];

/** Coerce a raw API value into the editor value for a field. */
function toValue(def: FieldDef, raw: unknown): Value {
  if (def.type === "boolean") return Boolean(raw);
  if (def.type === "number") return raw === null || raw === undefined ? 0 : Number(raw);
  return raw === null || raw === undefined ? "" : String(raw);
}

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative h-6 w-11 shrink-0 rounded-full border transition-colors ${
        checked
          ? "border-accent/40 bg-accent/30"
          : "border-white/10 bg-white/[0.05]"
      }`}
    >
      <span
        className={`absolute top-1/2 h-4 w-4 -translate-y-1/2 rounded-full transition-all ${
          checked ? "left-[1.6rem] bg-accent shadow-glow-sm" : "left-1 bg-zinc-400"
        }`}
      />
    </button>
  );
}

/** One editable setting row: friendly label + description on the left, control on the right. */
function FieldRow({
  def,
  value,
  onChange,
}: {
  def: FieldDef;
  value: Value;
  onChange: (v: Value) => void;
}) {
  let control;
  if (def.type === "boolean") {
    control = (
      <Toggle checked={Boolean(value)} onChange={(v) => onChange(v)} label={def.label} />
    );
  } else if (def.type === "select") {
    const opts = def.options ?? [];
    const cur = String(value ?? "");
    // Keep the current value selectable even if it's not a known option.
    const allOpts = cur && !opts.includes(cur) ? [...opts, cur] : opts;
    control = (
      <select
        value={cur}
        onChange={(e) => onChange(e.target.value)}
        aria-label={def.label}
        className="field"
      >
        {allOpts.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    );
  } else if (def.type === "number") {
    control = (
      <input
        type="number"
        value={Number(value ?? 0)}
        onChange={(e) => onChange(Number(e.target.value))}
        aria-label={def.label}
        className="field"
      />
    );
  } else {
    control = (
      <input
        type="text"
        value={String(value ?? "")}
        placeholder={def.placeholder}
        onChange={(e) => onChange(e.target.value)}
        aria-label={def.label}
        className="field font-mono text-[13px]"
      />
    );
  }

  return (
    <div className="flex flex-col gap-2 border-b border-white/[0.04] pb-4 last:border-0 last:pb-0 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
      <div className="min-w-0 sm:max-w-[16rem]">
        <label className="flex items-center gap-1.5 text-sm font-medium text-zinc-200">
          {def.label}
          {def.restart && (
            <span
              title="Takes full effect after a daemon restart"
              className="rounded border border-amber-500/25 bg-amber-500/[0.08] px-1 py-px text-[9px] font-medium uppercase tracking-wide text-amber-300/90"
            >
              restart
            </span>
          )}
        </label>
        {def.hint && (
          <p className="mt-0.5 text-[11px] leading-relaxed text-zinc-500">{def.hint}</p>
        )}
      </div>
      <div className="w-full sm:max-w-[18rem]">{control}</div>
    </div>
  );
}

export default function SettingsPage() {
  const [original, setOriginal] = useState<Record<string, Value> | null>(null);
  const [form, setForm] = useState<Record<string, Value>>({});
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);

  const [busy, setBusy] = useState(false);
  const [ok, setOk] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Daemon token box (lives in localStorage, applies without a rebuild).
  const [token, setToken] = useState("");
  const [tokenNote, setTokenNote] = useState<string | null>(null);

  // Maintenance actions (backup / restart).
  const { refresh } = useDaemon();
  const [backupBusy, setBackupBusy] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [maintOk, setMaintOk] = useState<string | null>(null);
  const [maintErr, setMaintErr] = useState<string | null>(null);

  useEffect(() => {
    setToken(ijToken());
    let cancelled = false;
    (async () => {
      try {
        const data = await get<{ settings: Record<string, unknown> }>("/settings");
        if (cancelled) return;
        const init: Record<string, Value> = {};
        for (const f of FIELDS) init[f.key] = toValue(f, data.settings?.[f.key]);
        setOriginal(init);
        setForm(init);
      } catch (err) {
        if (cancelled) return;
        setLoadError(err instanceof ApiError ? err : new ApiError(String(err), 0));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const offline = loadError && loadError.status === 0;

  const changed = useMemo(() => {
    if (!original) return {} as Record<string, Value>;
    const diff: Record<string, Value> = {};
    for (const f of FIELDS) {
      if (form[f.key] !== original[f.key]) diff[f.key] = form[f.key];
    }
    return diff;
  }, [form, original]);

  const changedKeys = Object.keys(changed);
  const dirty = changedKeys.length > 0;

  function update(key: string, value: Value) {
    setForm((f) => ({ ...f, [key]: value }));
    setOk(null);
    setError(null);
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!dirty) return;
    setBusy(true);
    setOk(null);
    setError(null);
    try {
      await put("/settings", { values: changed });
      setOriginal((prev) => ({ ...(prev ?? {}), ...changed }));
      setOk(`Saved ${changedKeys.length} setting${changedKeys.length === 1 ? "" : "s"}.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    if (original) setForm(original);
    setOk(null);
    setError(null);
  }

  function saveToken() {
    setIjToken(token);
    setTokenNote(token.trim() ? "Token saved — it's now sent with every request." : "Token cleared.");
  }

  function clearToken() {
    setIjToken("");
    setToken("");
    setTokenNote("Token cleared.");
  }

  async function backupNow() {
    setMaintOk(null);
    setMaintErr(null);
    setBackupBusy(true);
    try {
      const r = await post<{ action: string; ok: boolean; result: string }>(
        "/diagnostics/repair",
        { action: "backup_now" },
      );
      if (r.ok) setMaintOk(`Backup written to ${r.result}`);
      else setMaintErr("The daemon reported the backup did not complete.");
    } catch (err) {
      setMaintErr(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBackupBusy(false);
    }
  }

  async function restartDaemon() {
    setMaintOk(null);
    setMaintErr(null);
    setRestarting(true);
    try {
      await post("/shutdown");
    } catch {
      // The connection may reset as the daemon stops — that's expected here.
    }
    // The desktop app relaunches the daemon within ~2s; give it a beat, then
    // let the shared /health poll pick the new process back up.
    await new Promise((r) => setTimeout(r, 2500));
    refresh();
    setRestarting(false);
    setMaintOk("Restart requested — reconnecting. Watch the status dot in the sidebar.");
  }

  const restartTouched = changedKeys.some((k) => FIELDS.find((f) => f.key === k)?.restart);

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Settings"
          subtitle="Tune how Iron Jarvis behaves. Changes are written to config.toml so they survive a restart; a few settings (marked “restart”) only take full effect once the daemon restarts."
        />
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <Reveal>
        <div className="grid gap-6 lg:grid-cols-3">
          {/* Settings form */}
          <div className="lg:col-span-2">
            <Card title="Preferences" icon={<SlidersHorizontal size={15} />}>
              {loading ? (
                <SkeletonRows rows={8} />
              ) : (
                <form onSubmit={save} className="space-y-8">
                  {SECTIONS.map((section) => {
                    const fields = FIELDS.filter((f) => f.section === section.id);
                    if (fields.length === 0) return null;

                    const rows = (
                      <div className="space-y-4">
                        {fields.map((f) => (
                          <FieldRow
                            key={f.key}
                            def={f}
                            value={form[f.key]}
                            onChange={(v) => update(f.key, v)}
                          />
                        ))}
                      </div>
                    );

                    if (section.advanced) {
                      return (
                        <details
                          key={section.id}
                          className="group rounded-xl border border-white/[0.05] bg-white/[0.015] px-4 py-3.5"
                        >
                          <summary className="cursor-pointer list-none">
                            <span className="text-[12px] font-semibold uppercase tracking-[0.12em] text-accent-soft/80">
                              {section.title}
                            </span>
                            <span className="ml-2 text-[11px] text-zinc-500 group-open:hidden">
                              (click to expand)
                            </span>
                            <p className="mt-0.5 text-[11px] text-zinc-500">
                              {section.description}
                            </p>
                          </summary>
                          <div className="mt-4">{rows}</div>
                        </details>
                      );
                    }

                    return (
                      <div key={section.id} className="space-y-4">
                        <div>
                          <h3 className="text-[12px] font-semibold uppercase tracking-[0.12em] text-accent-soft/80">
                            {section.title}
                          </h3>
                          <p className="mt-0.5 text-[11px] leading-relaxed text-zinc-500">
                            {section.description}
                          </p>
                        </div>
                        {rows}
                      </div>
                    );
                  })}

                  {restartTouched && dirty && (
                    <p className="text-[11px] text-amber-300/80">
                      One or more changed settings need a daemon restart to fully apply — use
                      “Restart daemon” under Maintenance after saving.
                    </p>
                  )}

                  <div className="flex items-center gap-2 pt-1">
                    <button type="submit" disabled={busy || !dirty} className="btn-accent">
                      {busy ? (
                        <LoaderInline label="Saving…" />
                      ) : (
                        <>
                          <Save size={14} /> Save changes
                        </>
                      )}
                    </button>
                    {dirty && (
                      <button type="button" onClick={reset} className="btn-ghost">
                        <RotateCcw size={14} /> Reset
                      </button>
                    )}
                    {dirty && (
                      <span className="text-[11px] text-zinc-500">
                        {changedKeys.length} unsaved change{changedKeys.length === 1 ? "" : "s"}
                      </span>
                    )}
                  </div>
                  {ok && <SuccessNote>{ok}</SuccessNote>}
                  {error && <ErrorNote>{error}</ErrorNote>}
                </form>
              )}
            </Card>
          </div>

          {/* Sidebar: maintenance + access token */}
          <div className="space-y-6 lg:col-span-1">
            {/* Maintenance */}
            <Card title="Maintenance" icon={<Wrench size={15} />}>
              <div className="space-y-4">
                <div>
                  <SectionLabel>Back up now</SectionLabel>
                  <p className="mt-1 text-[12px] leading-relaxed text-zinc-500">
                    Save a snapshot of your database and settings right now. Backups also run
                    automatically in the background.
                  </p>
                  <button
                    type="button"
                    onClick={backupNow}
                    disabled={backupBusy || restarting}
                    className="btn-accent mt-2.5 w-full justify-center py-1.5 text-xs"
                  >
                    {backupBusy ? (
                      <LoaderInline label="Backing up…" />
                    ) : (
                      <>
                        <DatabaseBackup size={14} /> Back up now
                      </>
                    )}
                  </button>
                </div>

                <div className="border-t hairline pt-4">
                  <SectionLabel>Restart daemon</SectionLabel>
                  <p className="mt-1 text-[12px] leading-relaxed text-zinc-500">
                    Applies “restart” settings and clears a stuck state. It briefly interrupts
                    Iron Jarvis; the desktop app brings it right back.
                  </p>
                  <div className="mt-2.5">
                    <ConfirmButton
                      onConfirm={restartDaemon}
                      label="Restart daemon"
                      confirmLabel={restarting ? "Restarting…" : "Confirm restart"}
                      className="w-full justify-center border-amber-500/30 py-1.5 text-amber-200 hover:border-amber-500/50 hover:text-amber-100"
                      title="Gracefully stops the daemon; the desktop app restarts it within ~2s."
                    />
                  </div>
                  {restarting && (
                    <p className="mt-2 text-[11px] text-amber-300/80">
                      Restarting… reconnecting.
                    </p>
                  )}
                </div>

                {maintOk && <SuccessNote>{maintOk}</SuccessNote>}
                {maintErr && <ErrorNote>{maintErr}</ErrorNote>}
              </div>
            </Card>

            {/* Daemon access token */}
            <Card title="Daemon access token" icon={<KeyRound size={15} />}>
              <div className="space-y-3.5">
                <p className="text-[12px] leading-relaxed text-zinc-500">
                  If the daemon is protected with a bearer token, paste it here. It&apos;s stored in
                  your browser and sent with every request — so you can log into a deployed instance
                  without a rebuild.
                </p>
                <div>
                  <SectionLabel>Token</SectionLabel>
                  <input
                    type="password"
                    value={token}
                    onChange={(e) => {
                      setToken(e.target.value);
                      setTokenNote(null);
                    }}
                    placeholder="paste IRONJARVIS_TOKEN"
                    autoComplete="off"
                    className="field mt-1.5 font-mono text-[13px]"
                  />
                </div>
                <div className="flex items-center gap-2">
                  <button type="button" onClick={saveToken} className="btn-accent flex-1 py-1.5 text-xs">
                    <ShieldCheck size={14} /> Save token
                  </button>
                  <button
                    type="button"
                    onClick={clearToken}
                    className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-white/10 px-3 py-1.5 text-xs font-medium text-zinc-400 transition-colors hover:border-rose-500/40 hover:text-rose-300"
                  >
                    <Trash2 size={14} /> Clear
                  </button>
                </div>
                {tokenNote && <SuccessNote>{tokenNote}</SuccessNote>}
                <p className="text-[11px] text-zinc-600">
                  Local installs usually need no token — leave this empty.
                </p>
              </div>
            </Card>
          </div>
        </div>
      </Reveal>
    </PageShell>
  );
}
