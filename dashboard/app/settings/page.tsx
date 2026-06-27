"use client";

import { useEffect, useMemo, useState } from "react";
import { SlidersHorizontal, Save, KeyRound, Trash2, ShieldCheck, RotateCcw } from "lucide-react";
import { get, put, ijToken, setIjToken, ApiError } from "@/lib/api";
import {
  Card,
  OfflineHint,
  SkeletonRows,
  ErrorNote,
  SuccessNote,
  SectionLabel,
  LoaderInline,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

type FieldType = "text" | "number" | "boolean" | "select";
type Value = string | number | boolean;

interface FieldDef {
  key: string;
  label: string;
  type: FieldType;
  hint?: string;
  placeholder?: string;
  options?: string[];
  /** Marks settings that only fully apply after a daemon restart. */
  restart?: boolean;
}

// Mirrors the daemon's whitelist (`_SETTINGS_KEYS`).
const FIELDS: FieldDef[] = [
  { key: "default_provider", label: "Default provider", type: "text", placeholder: "anthropic", hint: "Provider used when a session doesn't pick one." },
  { key: "default_model", label: "Default model", type: "text", placeholder: "claude-3-5-sonnet" },
  { key: "max_agent_steps", label: "Max agent steps", type: "number", hint: "Hard ceiling on tool/loop steps per agent run." },
  { key: "git_native", label: "Git-native workspaces", type: "boolean", hint: "Run sessions on real git worktrees." },
  { key: "self_dev_enabled", label: "Self-development", type: "boolean", hint: "Allow a Maintainer to edit Iron Jarvis's own source (review-gated).", restart: true },
  { key: "self_dev_root", label: "Self-development repo root", type: "text", placeholder: "C:\\path\\to\\Iron-Jarvis", hint: "Only needed when running from an installed wheel.", restart: true },
  { key: "sandbox_runtime", label: "Sandbox runtime", type: "select", options: ["none", "docker"], hint: "How tool execution is isolated.", restart: true },
  { key: "ollama_base_url", label: "Ollama base URL", type: "text", placeholder: "http://127.0.0.1:11434" },
  { key: "ollama_model", label: "Ollama model", type: "text", placeholder: "llama3.1" },
  { key: "event_retention_days", label: "Event retention (days)", type: "number", hint: "How long the event log is kept.", restart: true },
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
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
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

  const restartTouched = changedKeys.some((k) => FIELDS.find((f) => f.key === k)?.restart);

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Settings"
          subtitle="Daemon configuration. Changes are written to config.toml so they survive a restart; some settings only take full effect after the daemon restarts."
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
            <Card title="Daemon settings" icon={<SlidersHorizontal size={15} />}>
              {loading ? (
                <SkeletonRows rows={6} />
              ) : (
                <form onSubmit={save} className="space-y-4">
                  {FIELDS.map((f) => (
                    <div
                      key={f.key}
                      className="flex flex-col gap-2 border-b border-white/[0.04] pb-4 last:border-0 last:pb-0 sm:flex-row sm:items-start sm:justify-between sm:gap-6"
                    >
                      <div className="min-w-0 sm:max-w-[15rem]">
                        <label className="flex items-center gap-1.5 text-sm font-medium text-zinc-200">
                          {f.label}
                          {f.restart && (
                            <span
                              title="Takes full effect after a daemon restart"
                              className="rounded border border-amber-500/25 bg-amber-500/[0.08] px-1 py-px text-[9px] font-medium uppercase tracking-wide text-amber-300/90"
                            >
                              restart
                            </span>
                          )}
                        </label>
                        {f.hint && <p className="mt-0.5 text-[11px] text-zinc-500">{f.hint}</p>}
                      </div>

                      <div className="w-full sm:max-w-[18rem]">
                        {f.type === "boolean" ? (
                          <Toggle
                            checked={Boolean(form[f.key])}
                            onChange={(v) => update(f.key, v)}
                          />
                        ) : f.type === "select" ? (
                          <select
                            value={String(form[f.key] ?? "")}
                            onChange={(e) => update(f.key, e.target.value)}
                            className="field"
                          >
                            {(f.options ?? []).map((opt) => (
                              <option key={opt} value={opt}>
                                {opt}
                              </option>
                            ))}
                          </select>
                        ) : f.type === "number" ? (
                          <input
                            type="number"
                            value={Number(form[f.key] ?? 0)}
                            onChange={(e) => update(f.key, Number(e.target.value))}
                            className="field"
                          />
                        ) : (
                          <input
                            type="text"
                            value={String(form[f.key] ?? "")}
                            placeholder={f.placeholder}
                            onChange={(e) => update(f.key, e.target.value)}
                            className="field font-mono text-[13px]"
                          />
                        )}
                      </div>
                    </div>
                  ))}

                  {restartTouched && dirty && (
                    <p className="text-[11px] text-amber-300/80">
                      One or more changed settings need a daemon restart to fully apply.
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

          {/* Daemon access token */}
          <div className="lg:col-span-1">
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
