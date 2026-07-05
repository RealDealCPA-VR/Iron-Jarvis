"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  PlugZap,
  Sparkles,
  Bot,
  Globe,
  MoonStar,
  Cpu,
  KeyRound,
  ShieldCheck,
  ExternalLink,
  Plug,
  CheckCircle2,
  Zap,
  Star,
  Check,
  Plus,
  ChevronRight,
  HardDrive,
  Terminal,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { get, post, put, del, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { useDaemon } from "@/lib/daemon";
import type { Connection, ConnectionTestResult, OAuthStart } from "@/lib/types";
import {
  Card,
  OfflineHint,
  SkeletonRows,
  ErrorNote,
  SuccessNote,
  LoaderInline,
  ConfirmButton,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

/* -------------------------------------------------------------------------- */
/*  Per-provider presentation (the /connections payload carries no help text)  */
/* -------------------------------------------------------------------------- */

interface ProviderMeta {
  icon: LucideIcon;
  /** Tailwind text color for the icon tile. */
  tint: string;
  /** Where to get an API key (api_key providers). */
  keyUrl?: string;
  keyLabel?: string;
  placeholder?: string;
  /** Where OAuth app credentials come from (oauth providers). */
  docsUrl?: string;
  docsLabel?: string;
}

const META: Record<string, ProviderMeta> = {
  anthropic: {
    icon: Sparkles,
    tint: "text-orange-300",
    keyUrl: "https://console.anthropic.com/settings/keys",
    keyLabel: "console.anthropic.com",
    placeholder: "sk-ant-…",
  },
  openai: {
    icon: Bot,
    tint: "text-emerald-300",
    keyUrl: "https://platform.openai.com/api-keys",
    keyLabel: "platform.openai.com",
    placeholder: "sk-…",
  },
  google: {
    icon: Globe,
    tint: "text-sky-300",
    docsUrl: "https://console.cloud.google.com/apis/credentials",
    docsLabel: "Google Cloud Console",
  },
  xai: {
    icon: Zap,
    tint: "text-violet-300",
    keyUrl: "https://console.x.ai",
    keyLabel: "console.x.ai",
    placeholder: "xai-…",
  },
  openrouter: {
    icon: PlugZap,
    tint: "text-rose-300",
    keyUrl: "https://openrouter.ai/settings/keys",
    keyLabel: "openrouter.ai",
    placeholder: "sk-or-…",
  },
  custom: {
    icon: Cpu,
    tint: "text-teal-300",
    placeholder: "key (optional for local servers)",
  },
  mock: { icon: MoonStar, tint: "text-amber-300" },
};

function metaFor(provider: string): ProviderMeta {
  return META[provider] ?? { icon: Cpu, tint: "text-zinc-300" };
}

/* -------------------------------------------------------------------------- */
/*  Status pill                                                                */
/* -------------------------------------------------------------------------- */

function StatusPill({ conn }: { conn: Connection }) {
  let tone: string;
  let label: string;
  if (conn.connected) {
    tone = "border-emerald-500/25 bg-emerald-500/10 text-emerald-300";
    label = "Connected";
  } else if (conn.status === "needs_auth") {
    tone = "border-amber-500/25 bg-amber-500/10 text-amber-300";
    label = "Needs auth";
  } else {
    tone = "border-zinc-500/25 bg-zinc-500/10 text-zinc-300";
    label = "Not connected";
  }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${tone}`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${
          conn.connected
            ? "bg-emerald-400 shadow-[0_0_8px_2px_rgba(52,211,153,0.5)]"
            : conn.status === "needs_auth"
              ? "bg-amber-400"
              : "bg-zinc-500"
        }`}
      />
      {label}
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/*  One connection card                                                        */
/* -------------------------------------------------------------------------- */

function ConnectionCard({
  conn,
  onChanged,
  id,
}: {
  conn: Connection;
  onChanged: () => void;
  /** Anchor id (`conn-card-${provider}`) the header dropdown smooth-scrolls to. */
  id: string;
}) {
  const meta = metaFor(conn.provider);
  const Icon = meta.icon;
  const isCustom = conn.provider === "custom";

  // The active default provider comes from the shared /health poll. Calling
  // refresh() after switching keeps this card's badge and the topbar model
  // switcher in lock-step.
  const { health, refresh: refreshDaemon } = useDaemon();
  const isDefault = health?.default_provider === conn.provider;

  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  // Custom (OpenAI-compatible) endpoint config — lives in /settings, not the vault.
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsSecrets, setNeedsSecrets] = useState(false);
  const [test, setTest] = useState<ConnectionTestResult | null>(null);
  // Manual-code OAuth (Anthropic): the provider shows a code to paste back —
  // completion arrives via POST /oauth/{provider}/complete, not a redirect.
  const manualCodeFlow = conn.oauth_manual_code === true;
  const [manualOpen, setManualOpen] = useState(false);
  const [manualCode, setManualCode] = useState("");
  // Redirect-based flows in the DESKTOP app open the provider in the external
  // browser — no window.opener, so no postMessage back. Poll until connected.
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(
    () => () => {
      if (pollRef.current) clearInterval(pollRef.current);
    },
    [],
  );

  function startCompletionPoll() {
    if (pollRef.current) clearInterval(pollRef.current);
    const startedAt = Date.now();
    pollRef.current = setInterval(async () => {
      try {
        const d = await get<{ connections: Connection[] }>("/connections");
        const me = d.connections.find((c) => c.provider === conn.provider);
        if (me?.connected) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setTest({ ok: true, detail: `${conn.display_name} connected via OAuth.` });
          onChanged();
        } else if (Date.now() - startedAt > 120_000) {
          if (pollRef.current) clearInterval(pollRef.current); // give up quietly
          pollRef.current = null;
        }
      } catch {
        /* daemon hiccup — keep polling until the cap */
      }
    }, 2000);
  }

  const isMock = conn.provider === "mock";
  // A provider may offer account-login (OAuth), an API key, or BOTH.
  const canOAuth = (conn.supports_oauth ?? conn.method === "oauth") && !isMock;
  const canKey = (conn.supports_api_key ?? conn.method === "api_key") && !isMock;

  // Prefill the custom endpoint fields from the daemon's saved settings.
  useEffect(() => {
    if (!isCustom) return;
    let cancelled = false;
    (async () => {
      try {
        const d = await get<{ settings?: Record<string, unknown> }>("/settings");
        if (cancelled) return;
        const savedUrl = d.settings?.custom_base_url;
        const savedModel = d.settings?.custom_model;
        if (typeof savedUrl === "string" && savedUrl) setBaseUrl((v) => v || savedUrl);
        if (typeof savedModel === "string" && savedModel) setModel((v) => v || savedModel);
      } catch {
        /* prefill is best-effort — the fields just start empty */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isCustom]);

  /* --- API key connect ----------------------------------------------------- */
  async function connectKey(e: React.FormEvent) {
    e.preventDefault();
    // For the custom provider the ENDPOINT is the required bit; the key is
    // optional (local servers like LM Studio / llama.cpp don't need one).
    if (isCustom ? !baseUrl.trim() : !key.trim()) return;
    setBusy(true);
    setError(null);
    setTest(null);
    try {
      if (isCustom) {
        // Save the endpoint config FIRST so a key-less save still sticks.
        await put("/settings", {
          values: { custom_base_url: baseUrl.trim(), custom_model: model.trim() },
        });
        if (key.trim()) {
          await post(`/connections/${conn.provider}/key`, { key: key.trim() });
        }
        setTest({
          ok: true,
          detail: "Custom endpoint saved — pick 'custom' in any model picker.",
        });
      } else {
        await post(`/connections/${conn.provider}/key`, { key: key.trim() });
        const result = await post<ConnectionTestResult>(`/connections/${conn.provider}/test`);
        setTest(result);
      }
      setKey("");
      setOpen(false);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  /* --- Test ---------------------------------------------------------------- */
  async function runTest() {
    setBusy(true);
    setError(null);
    try {
      const result = await post<ConnectionTestResult>(`/connections/${conn.provider}/test`);
      setTest(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  /* --- Disconnect ---------------------------------------------------------- */
  async function disconnect() {
    setBusy(true);
    setError(null);
    setTest(null);
    try {
      await del(`/connections/${conn.provider}`);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  /* --- Make default -------------------------------------------------------- */
  async function makeDefault() {
    setBusy(true);
    setError(null);
    try {
      await post(`/connections/${conn.provider}/default`);
      onChanged(); // reload the connections list (this card's badge)
      refreshDaemon(); // re-poll /health so the topbar model switcher updates too
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  /* --- OAuth --------------------------------------------------------------- */
  async function connectOAuth() {
    setBusy(true);
    setError(null);
    setNeedsSecrets(false);
    setTest(null);
    try {
      const { authorization_url } = await get<OAuthStart>(`/oauth/${conn.provider}/start`);
      window.open(
        authorization_url,
        "ironjarvis-oauth",
        "width=520,height=640,menubar=no,toolbar=no",
      );
      // Manual-code providers never redirect back — open the paste box now.
      // Redirect flows may complete in an external browser — poll for it.
      if (manualCodeFlow) setManualOpen(true);
      else startCompletionPoll();
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) {
        setNeedsSecrets(true);
      } else {
        setError(err instanceof ApiError ? err.message : String(err));
      }
    } finally {
      setBusy(false);
    }
  }

  /* --- Manual-code OAuth completion (paste the code the provider showed) --- */
  async function submitManualCode(e: React.FormEvent) {
    e.preventDefault();
    if (!manualCode.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await post(`/oauth/${conn.provider}/complete`, { code: manualCode.trim() });
      setTest({ ok: true, detail: `${conn.display_name} connected via OAuth.` });
      setManualCode("");
      setManualOpen(false);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  // Listen for the daemon callback's postMessage (OAuth completion).
  useEffect(() => {
    if (!canOAuth) return;
    function onMessage(ev: MessageEvent) {
      const d = ev.data;
      if (!d || d.type !== "ironjarvis-oauth" || d.provider !== conn.provider) return;
      if (d.ok) {
        setTest({ ok: true, detail: `${conn.display_name} connected via OAuth.` });
        onChanged();
      } else {
        setError("OAuth was cancelled or failed. Please try again.");
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [conn.method, conn.provider, conn.display_name, onChanged]);

  return (
    <div
      id={id}
      className="card-surface flex scroll-mt-24 flex-col gap-4 p-5 transition-all duration-300 hover:-translate-y-0.5 hover:shadow-card-hover"
    >
      {/* Header: icon + name + status */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl border border-white/[0.08] bg-white/[0.03]">
            <Icon size={19} className={meta.tint} />
          </span>
          <div>
            <div className="text-sm font-semibold text-zinc-100">{conn.display_name}</div>
            <div className="flex items-center gap-1.5 text-[11px] text-zinc-500">
              {conn.method === "oauth" ? (
                <>
                  <ShieldCheck size={11} /> OAuth 2.0
                </>
              ) : (
                <>
                  <KeyRound size={11} /> API key
                </>
              )}
              {conn.account && <span className="text-zinc-600">· {conn.account}</span>}
            </div>
          </div>
        </div>
        <StatusPill conn={conn} />
      </div>

      {/* Body */}
      {isMock ? (
        <p className="text-xs leading-relaxed text-zinc-500">
          The built-in offline model. Always available for testing — no key required.
        </p>
      ) : conn.connected ? (
        <div className="flex items-center gap-2">
          {isDefault ? (
            <span
              title="Sessions use this provider by default"
              className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-500/25 bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-300"
            >
              <Check size={14} /> Default
            </span>
          ) : (
            <button
              onClick={makeDefault}
              disabled={busy}
              title={`Use ${conn.display_name} for new sessions`}
              className="btn-ghost py-1.5 text-xs"
            >
              {busy ? <LoaderInline label="Setting…" /> : <><Star size={14} /> Make default</>}
            </button>
          )}
          <button onClick={runTest} disabled={busy} className="btn-ghost flex-1 py-1.5 text-xs">
            {busy ? <LoaderInline label="Testing…" /> : <><CheckCircle2 size={14} /> Test</>}
          </button>
          <ConfirmButton
            onConfirm={disconnect}
            label="Disconnect"
            title={`Disconnect ${conn.display_name}`}
            className="py-1.5"
          />
        </div>
      ) : (
        <div className="space-y-3">
          {/* Account login (OAuth) — log in with your Anthropic/OpenAI/Google account */}
          {canOAuth && (
            <div className="space-y-2">
              <button onClick={connectOAuth} disabled={busy} className="btn-accent w-full py-1.5 text-xs">
                {busy ? <LoaderInline label="Starting…" /> : <><ShieldCheck size={14} /> Log in with your account</>}
              </button>
              {manualOpen && (
                <form onSubmit={submitManualCode} className="space-y-2">
                  <input
                    type="text"
                    value={manualCode}
                    onChange={(e) => setManualCode(e.target.value)}
                    placeholder="Paste the authorization code"
                    aria-label="Authorization code"
                    autoComplete="off"
                    autoFocus
                    className="field font-mono text-xs"
                  />
                  <p className="text-[11px] leading-relaxed text-zinc-500">
                    After you approve access, {conn.display_name} shows an authorization
                    code — copy it and paste it here to finish connecting.
                  </p>
                  <div className="flex items-center gap-2">
                    <button
                      type="submit"
                      disabled={busy || !manualCode.trim()}
                      className="btn-accent flex-1 py-1.5 text-xs"
                    >
                      {busy ? <LoaderInline label="Connecting…" /> : <><Plug size={14} /> Complete sign-in</>}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setManualOpen(false);
                        setManualCode("");
                        setError(null);
                      }}
                      className="btn-ghost py-1.5 text-xs"
                    >
                      Cancel
                    </button>
                  </div>
                </form>
              )}
              {conn.oauth_help && (
                <p className="text-[11px] leading-relaxed text-zinc-500">{conn.oauth_help}</p>
              )}
              {meta.docsUrl && (
                <a
                  href={meta.docsUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1 text-[11px] text-zinc-500 transition-colors hover:text-accent-soft"
                >
                  Manage OAuth app in {meta.docsLabel} <ExternalLink size={11} />
                </a>
              )}
              {needsSecrets && (
                <div className="rounded-xl border border-amber-500/25 bg-amber-500/[0.07] px-3 py-2.5 text-[11px] leading-relaxed text-amber-100/90">
                  No OAuth client configured. Set{" "}
                  <code className="rounded bg-black/40 px-1 font-mono text-amber-200">
                    {conn.provider}_oauth_client_id
                  </code>{" "}
                  in{" "}
                  <Link href="/secrets" className="font-medium text-accent-soft underline">
                    Secrets
                  </Link>{" "}
                  to override the built-in client, then connect.
                </div>
              )}
            </div>
          )}

          {canOAuth && canKey && (
            <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-zinc-600">
              <span className="h-px flex-1 bg-white/[0.08]" />
              or use an API key
              <span className="h-px flex-1 bg-white/[0.08]" />
            </div>
          )}

          {/* API key */}
          {canKey &&
            (!open ? (
              <button
                onClick={() => setOpen(true)}
                className={`${canOAuth ? "btn-ghost" : "btn-accent"} w-full py-1.5 text-xs`}
              >
                <KeyRound size={14} /> {canOAuth ? "Use an API key instead" : "Connect"}
              </button>
            ) : (
              <form onSubmit={connectKey} className="space-y-2.5">
                {isCustom && (
                  <>
                    <label className="block space-y-1">
                      <span className="text-[11px] font-medium text-zinc-400">
                        Endpoint base URL
                      </span>
                      <input
                        type="text"
                        value={baseUrl}
                        onChange={(e) => setBaseUrl(e.target.value)}
                        placeholder="http://localhost:1234/v1 — any OpenAI-compatible server"
                        autoComplete="off"
                        autoFocus
                        className="field font-mono text-xs"
                      />
                    </label>
                    <label className="block space-y-1">
                      <span className="text-[11px] font-medium text-zinc-400">Model id</span>
                      <input
                        type="text"
                        value={model}
                        onChange={(e) => setModel(e.target.value)}
                        placeholder="e.g. glm-4.7-flash / llama3"
                        autoComplete="off"
                        className="field font-mono text-xs"
                      />
                    </label>
                  </>
                )}
                <input
                  type="password"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  placeholder={meta.placeholder ?? "Paste your API key"}
                  aria-label={isCustom ? "API key (optional)" : "API key"}
                  autoComplete="off"
                  autoFocus={!isCustom}
                  className="field font-mono text-xs"
                />
                <p className="text-[11px] leading-relaxed text-zinc-500">
                  {isCustom
                    ? "The key is optional (local servers usually don't need one) — if set, it's stored encrypted and never shown again."
                    : "Paste your API key — it's stored encrypted and never shown again."}
                  {meta.keyUrl && (
                    <>
                      {" "}Get one at{" "}
                      <a
                        href={meta.keyUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-0.5 text-accent-soft hover:text-accent"
                      >
                        {meta.keyLabel} <ExternalLink size={10} />
                      </a>
                      .
                    </>
                  )}
                </p>
                <div className="flex items-center gap-2">
                  <button
                    type="submit"
                    disabled={busy || (isCustom ? !baseUrl.trim() : !key.trim())}
                    className="btn-accent flex-1 py-1.5 text-xs"
                  >
                    {busy ? (
                      <LoaderInline label={isCustom ? "Saving…" : "Connecting…"} />
                    ) : (
                      <><Plug size={14} /> {isCustom ? "Save endpoint" : "Connect"}</>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setOpen(false);
                      setKey("");
                      setError(null);
                    }}
                    className="btn-ghost py-1.5 text-xs"
                  >
                    Cancel
                  </button>
                </div>
              </form>
            ))}
        </div>
      )}

      {/* Test result + errors */}
      {test &&
        (test.ok ? <SuccessNote>{test.detail}</SuccessNote> : <ErrorNote>{test.detail}</ErrorNote>)}
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Subscription & local providers (CLI-backed — detected, never configured)   */
/* -------------------------------------------------------------------------- */

interface CliProviderInfo {
  provider: string;
  name: string;
  description: string;
  hint: string;
  icon: LucideIcon;
  tint: string;
}

const CLI_PROVIDERS: CliProviderInfo[] = [
  {
    provider: "claude-cli",
    name: "Claude Code CLI",
    description: "Your Claude Max plan",
    hint: "install / log in via its CLI; appears automatically",
    icon: Sparkles,
    tint: "text-orange-300",
  },
  {
    provider: "codex-cli",
    name: "Codex CLI",
    description: "Your ChatGPT plan",
    hint: "install / log in via its CLI; appears automatically",
    icon: Bot,
    tint: "text-emerald-300",
  },
  {
    provider: "grok-cli",
    name: "Grok CLI",
    description: "Your Grok subscription",
    hint: "install / log in via its CLI; appears automatically",
    icon: Zap,
    tint: "text-violet-300",
  },
  {
    provider: "ollama",
    name: "Local Ollama",
    description: "Free models running on this machine",
    hint: "install Ollama and pull a model; appears automatically",
    icon: Cpu,
    tint: "text-teal-300",
  },
];

function CliProviderRow({ info, available }: { info: CliProviderInfo; available: boolean }) {
  const Icon = info.icon;
  return (
    <div className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
      <div className="flex min-w-0 items-center gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-white/[0.08] bg-white/[0.03]">
          <Icon size={16} className={info.tint} />
        </span>
        <div className="min-w-0">
          <div className="text-sm font-medium text-zinc-100">{info.name}</div>
          <div className="truncate text-[11px] text-zinc-500">
            {info.description}
            {!available && <span className="text-zinc-600"> · {info.hint}</span>}
          </div>
        </div>
      </div>
      {available ? (
        <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-emerald-500/25 bg-emerald-500/10 px-2.5 py-0.5 text-[11px] font-medium text-emerald-300">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_8px_2px_rgba(52,211,153,0.5)]" />
          Detected — ready to use
        </span>
      ) : (
        <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-zinc-500/25 bg-zinc-500/10 px-2.5 py-0.5 text-[11px] font-medium text-zinc-400">
          <span className="h-1.5 w-1.5 rounded-full bg-zinc-500" />
          Not detected
        </span>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Page                                                                       */
/* -------------------------------------------------------------------------- */

export default function ConnectionsPage() {
  const { data, error, loading, reload } = useApi<{ connections: Connection[] }>("/connections");
  const { health } = useDaemon();
  const offline = error && error.status === 0;
  const connections = data?.connections ?? [];
  const connectedCount = connections.filter((c) => c.connected).length;
  // The "+ Add connection" dropdown lists these: everything not yet connected
  // (mock is built-in — nothing to connect).
  const notConnected = connections.filter((c) => !c.connected && c.provider !== "mock");

  // Subscription / local providers are DETECTED by the daemon, not configured
  // here — availability comes from the shared /health poll.
  const daemonProviders = health?.providers ?? [];
  const isDetected = (provider: string) =>
    daemonProviders.some((p) => p.provider === provider && p.available);

  /* --- "+ Add connection" dropdown ----------------------------------------- */
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!menuOpen) return;
    function onPointerDown(e: PointerEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setMenuOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menuOpen]);

  function scrollToCard(provider: string) {
    setMenuOpen(false);
    document
      .getElementById(`conn-card-${provider}`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Connections"
          subtitle="Your accounts — AI models, cloud drives, and services. Connect once; everything in Iron Jarvis can use them."
          actions={
            <div className="flex items-center gap-2">
              {data ? (
                <span className="flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs text-zinc-300">
                  <PlugZap size={14} className="text-accent-soft" />
                  {connectedCount} connected
                </span>
              ) : null}
              <div ref={menuRef} className="relative">
                <button
                  type="button"
                  onClick={() => setMenuOpen((v) => !v)}
                  aria-haspopup="menu"
                  aria-expanded={menuOpen}
                  className="btn-accent px-3 py-1.5 text-xs"
                >
                  <Plus size={14} /> Add connection
                </button>
                {menuOpen && (
                  <div
                    role="menu"
                    className="absolute right-0 top-full z-50 mt-2 w-64 rounded-xl border border-white/10 bg-zinc-900/95 p-1.5 shadow-2xl shadow-black/50 backdrop-blur"
                  >
                    {notConnected.length === 0 ? (
                      <div className="px-3 py-2 text-xs text-zinc-400">
                        All providers connected 🎉
                      </div>
                    ) : (
                      notConnected.map((c) => {
                        const m = metaFor(c.provider);
                        const MenuIcon = m.icon;
                        return (
                          <button
                            key={c.provider}
                            type="button"
                            role="menuitem"
                            onClick={() => scrollToCard(c.provider)}
                            className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-xs text-zinc-200 transition-colors hover:bg-white/[0.06]"
                          >
                            <MenuIcon size={14} className={m.tint} />
                            <span className="flex-1 truncate">{c.display_name}</span>
                          </button>
                        );
                      })
                    )}
                    <div className="my-1.5 h-px bg-white/[0.08]" />
                    <Link
                      href="/ltm"
                      role="menuitem"
                      onClick={() => setMenuOpen(false)}
                      className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-xs text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
                    >
                      <HardDrive size={14} className="text-sky-300" />
                      <span className="flex-1">Cloud memory drives</span>
                      <ChevronRight size={13} className="text-zinc-600" />
                    </Link>
                    <Link
                      href="/tools"
                      role="menuitem"
                      onClick={() => setMenuOpen(false)}
                      className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-xs text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
                    >
                      <Wrench size={14} className="text-amber-300" />
                      <span className="flex-1">Tool packs (MCP)</span>
                      <ChevronRight size={13} className="text-zinc-600" />
                    </Link>
                  </div>
                )}
              </div>
            </div>
          }
        />
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <Reveal>
        {loading && !data ? (
          <Card>
            <SkeletonRows rows={4} />
          </Card>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {connections.map((conn) => (
              <ConnectionCard
                key={conn.provider}
                conn={conn}
                onChanged={reload}
                id={`conn-card-${conn.provider}`}
              />
            ))}
          </div>
        )}
      </Reveal>

      <Reveal>
        <Card
          title="Subscription & local providers"
          icon={<Terminal size={16} className="text-accent-soft" />}
        >
          <div className="divide-y divide-white/[0.06]">
            {CLI_PROVIDERS.map((info) => (
              <CliProviderRow
                key={info.provider}
                info={info}
                available={isDetected(info.provider)}
              />
            ))}
          </div>
          <p className="mt-3 text-[11px] leading-relaxed text-zinc-600">
            These use plans you already pay for — no API keys. Pick them in any model picker.
          </p>
        </Card>
      </Reveal>

      {!offline && (
        <Reveal>
          <p className="flex items-center gap-2 text-xs text-zinc-600">
            <KeyRound size={13} />
            Keys and tokens live in the encrypted vault. Manage them anytime in{" "}
            <Link href="/secrets" className="text-accent-soft hover:text-accent">
              Secrets
            </Link>
            .
          </p>
        </Reveal>
      )}
    </PageShell>
  );
}
