"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  Blocks,
  Bot,
  Check,
  CheckCircle2,
  ChevronRight,
  Cloud,
  Compass,
  Cpu,
  ExternalLink,
  Gauge,
  Globe,
  HardDrive,
  KeyRound,
  MessagesSquare,
  MoonStar,
  Pencil,
  Plug,
  PlugZap,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Star,
  Terminal,
  Wrench,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { get, post, put, patch, del, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { useFocusRef } from "@/lib/useFocusRef";
import { useDaemon } from "@/lib/daemon";
import type { Connection, ConnectionTestResult, OAuthStart } from "@/lib/types";

/** A user-added custom endpoint (a routable fleet node) as the card shows it. */
interface EndpointRow {
  id: string;
  label: string;
  /** True for the config-seeded slot (custom_base_url). It renders like any
   *  other endpoint now, but deleting it clears the Settings keys it derives
   *  from rather than removing a stored row. */
  seeded: boolean;
  base_url: string;
  default_model: string;
  api_key_name: string;
  /** Live-verified tool support: true/false = asked the server; null = never
   *  verified (tool turns then route elsewhere — the chip says so). */
  tool_use: boolean | null;
  /** Live-verified vision support (same probe run): null = unknown. */
  vision: boolean | null;
}

/** The node fields we read out of GET /fleet's snapshot rows. */
interface EndpointNodeDump {
  id: string;
  label?: string;
  base_url?: string;
  source?: string;
  routable?: boolean;
  default_model?: string;
  api_key_name?: string;
  tool_use?: boolean | null;
  vision?: boolean | null;
}

/** POST /fleet/nodes/{id}/verify response (tool + vision capability probes). */
interface VerifyResult {
  tool_use: boolean | null;
  vision?: boolean | null;
  error?: string;
}
import {
  Card,
  OfflineHint,
  SkeletonRows,
  ErrorNote,
  SuccessNote,
  LoaderInline,
  ConfirmButton,
} from "@/components/ui";
import { RestHookups } from "@/components/connections/RestHookups";
import {
  EnvelopeRowControls,
  EnvelopeSection,
} from "@/components/connections/EnvelopeCard";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { ProviderMark } from "@/components/BrandGlyph";

/* -------------------------------------------------------------------------- */
/*  Model report card (v1.169.0) — the evidence auto-tier judges on            */
/* -------------------------------------------------------------------------- */

/**
 * One row of GET /routing/quality: the router's OWN judgment of a local
 * (provider, model) — avg completion over evaluated sessions vs the user's
 * quality bar. `avg` is reported even below the evidence gate; `clears` is
 * the server's real gated verdict (same function `_local_oracle` consults).
 */
interface QualityRow {
  provider: string;
  model: string;
  task_class: string | null;
  avg: number | null;
  samples: number;
  bar: number;
  min_samples: number;
  clears: boolean;
}

/** The bar judges LOCAL models only — a report line on a cloud provider would
 *  imply it is being judged too. Mirrors providers/local.is_local_provider. */
function isLocalReportProvider(provider: string): boolean {
  return (
    provider === "ollama" ||
    provider === "custom" ||
    provider === "opencode-cli" ||
    provider.startsWith("fleet-")
  );
}

/**
 * Which providers get ENVELOPE surfaces (v1.201.0) — a narrower set than the
 * quality report. opencode-cli's models are local, but the backend treats
 * every *-cli provider as `trusted` (the CLI owns its own harness), so its
 * GET would answer "fully capable — no measurement needed" right beside a
 * quality line that may say the opposite: a frontier claim on an unmeasured
 * local model is the exact lie the provenance gating exists to prevent.
 * Envelope treatment for opencode-cli is future work — until then it gets NO
 * section, NO chip, NO Measure.
 */
function hasEnvelopeSurface(provider: string): boolean {
  return isLocalReportProvider(provider) && provider !== "opencode-cli";
}

/**
 * avg, displayed so it can never sit on the wrong side of the displayed bar.
 * toFixed(2) rounds 0.7477 to "0.75", which would read "avg 0.75 … below your
 * 0.75 bar" — the verdict is right (the server compares the unrounded value),
 * but the visible evidence would contradict it. Add decimals until the parsed
 * display agrees with `clears`; as a last resort round AWAY from the bar.
 */
function fmtAvg(row: QualityRow): string {
  const avg = row.avg ?? 0;
  for (let dp = 2; dp <= 4; dp++) {
    const s = avg.toFixed(dp);
    if ((Number(s) >= row.bar) === row.clears) return s;
  }
  const scaled = row.clears ? Math.ceil(avg * 100) : Math.floor(avg * 100);
  return (scaled / 100).toFixed(2);
}

/** True when a row carries a real gated verdict (enough evidence to judge). */
function isJudged(row: QualityRow): boolean {
  return row.samples >= row.min_samples && row.avg != null;
}

/** One task class's verdict, for the title tooltip — mirrors qualityLine's
 *  three states so the hover always carries the full per-class picture. */
function classVerdict(r: QualityRow): string {
  if (!isJudged(r)) {
    return `${r.task_class}: not enough evidence (${r.samples} of ${r.min_samples})`;
  }
  return `${r.task_class}: avg ${fmtAvg(r)} (${r.clears ? "clears" : "below the bar"})`;
}

/**
 * The compact report line — honest about which of the three states holds:
 * not enough evidence / below the bar (eligible work routes up) / clears.
 * The verdict word comes from the SERVER's `clears` (the router's own gated
 * check), never re-derived client-side.
 *
 * PER-CLASS HONESTY: the router never judges the aggregate — every live call
 * carries a task class ("chat" or the agent type), so the aggregate verdict is
 * a synthetic judgment no request ever receives. When judged classes DISAGREE
 * with it, a single categorical consequence would be false for some of them
 * (the exact state-collapse v1.165.0 forbids) — render the consequence per
 * class instead, and drop the categorical claim.
 */
function qualityLine(row: QualityRow, classRows: QualityRow[] = []): string {
  if (!isJudged(row)) {
    return `not enough evidence yet (${row.samples} of ${row.min_samples} session${
      row.min_samples === 1 ? "" : "s"
    })`;
  }
  const avg = fmtAvg(row);
  const n = `${row.samples} session${row.samples === 1 ? "" : "s"}`;
  const judged = classRows.filter(isJudged);
  const cleared = judged.filter((r) => r.clears).map((r) => String(r.task_class));
  const failed = judged.filter((r) => !r.clears).map((r) => String(r.task_class));
  const diverges = row.clears ? failed.length > 0 : cleared.length > 0;
  if (diverges) {
    const parts: string[] = [];
    if (cleared.length > 0) {
      parts.push(
        `clears your ${row.bar} bar for ${cleared.join(", ")} work, which can stay local`,
      );
    }
    if (failed.length > 0) {
      parts.push(
        `${cleared.length > 0 ? "below it" : `below your ${row.bar} bar`} for ${failed.join(
          ", ",
        )} work, which routes up`,
      );
    } else {
      // Any class not demonstrably clearing routes up (no evidence => the
      // router does not prefer local) — say so instead of implying the
      // clearing classes speak for everything.
      parts.push("other eligible work routes up");
    }
    return `avg ${avg} over ${n} — ${parts.join("; ")}`;
  }
  return row.clears
    ? `avg ${avg} over ${n} — clears your ${row.bar} bar, so eligible work can stay local`
    : `avg ${avg} over ${n} — below your ${row.bar} bar, so eligible work routes up`;
}

/**
 * The report line(s) for ONE local provider — one line per model the router
 * judges. The line renders from the aggregate row, but its CONSEQUENCE is
 * qualified per task class when the class verdicts diverge (see qualityLine),
 * and the tooltip always carries every class's verdict.
 * Renders nothing for cloud providers or when the report has no rows.
 */
function ModelReportLine({
  rows,
  provider,
  surface,
}: {
  rows: QualityRow[];
  provider: string;
  /** Distinguishes the testid when the same provider's line renders on more
   *  than one surface (its ConnectionCard vs the CLI-tools row) — duplicate
   *  testids on one page would make either instance unaddressable. */
  surface?: string;
}) {
  if (!isLocalReportProvider(provider)) return null;
  const mine = rows.filter(
    (r) => r.provider === provider && r.task_class == null,
  );
  if (mine.length === 0) return null;
  return (
    <div
      className="mt-1 space-y-0.5"
      data-testid={`model-report-${surface ? `${surface}-` : ""}${provider}`}
    >
      {mine.map((r) => {
        const classRows = rows.filter(
          (c) =>
            c.provider === provider &&
            c.model === r.model &&
            c.task_class != null,
        );
        const detail = classRows.map(classVerdict).join("; ");
        return (
          <div key={r.model || "_"}>
            <p
              title={`Auto-tier judges local models on the average completion score of their evaluated sessions — below the bar (or without enough evidence), eligible work routes to a stronger model. Tune the bar in Settings.${
                detail ? ` Per task class — ${detail}.` : ""
              }`}
              className="flex items-start gap-1 text-[10.5px] leading-relaxed text-zinc-500"
            >
              <Gauge size={10} className="mt-0.5 shrink-0 text-zinc-600" />
              <span className="min-w-0">
                {mine.length > 1 && r.model ? (
                  <span className="font-mono text-zinc-400">{r.model}: </span>
                ) : null}
                {qualityLine(r, classRows)}
              </span>
            </p>
            {/* The Capability Envelope section (v1.201.0): what this model
                was MEASURED to do, provenance always shown next to the
                numbers. Renders nothing until GET /envelope answers; gated
                to hasEnvelopeSurface (opencode-cli is trusted server-side —
                see the helper's comment). */}
            {hasEnvelopeSurface(provider) && (
              <EnvelopeSection provider={provider} model={r.model} surface={surface} />
            )}
          </div>
        );
      })}
    </div>
  );
}

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
  // A connection that loaded ZERO tools is not usable, however green it looks
  // (v1.172.0): MCP tools load once at daemon boot, so a server added since
  // startup — or one whose command failed to launch — delivers nothing while
  // the old flat "Connected" badge insisted it was fine. That badge is exactly
  // what hid a dark wiki from a user who then found Jarvis "blind as a bat".
  if (conn.status === "no_tools") {
    tone = "border-amber-500/25 bg-amber-500/10 text-amber-300";
    label = "0 tools — restart";
  } else if (conn.connected) {
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
          conn.status === "no_tools"
            ? "bg-amber-400"
            : conn.connected
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
  quality = [],
}: {
  conn: Connection;
  onChanged: () => void;
  /** Anchor id (`conn-card-${provider}`) the header dropdown smooth-scrolls to. */
  id: string;
  /** Model report card rows (v1.169.0) — the custom card shows a line per
   *  local endpoint ("fleet-<id>") and for the legacy "custom" slot. */
  quality?: QualityRow[];
}) {
  const meta = metaFor(conn.provider);
  const Icon = meta.icon;
  const isCustom = conn.provider === "custom";
  // Deep-link target: /connections?focus=endpoints lands on the custom-endpoint
  // card (where saved endpoints are added, renamed and deleted). One card owns
  // the key — every other provider passes "" so its instance stays inert.
  const endpointsFocusRef = useFocusRef<HTMLDivElement>(isCustom ? "endpoints" : "");

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
  // Live model discovery for the endpoint being typed: the server can list its
  // own models (/v1/models or Ollama /api/tags) — nobody should have to know
  // model ids by heart. null = not probed yet.
  const [detected, setDetected] = useState<string[] | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [detectError, setDetectError] = useState<string | null>(null);
  const [manualModel, setManualModel] = useState(false); // "type it myself" escape
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

  // SAVED ENDPOINTS (custom card): every routable custom endpoint —
  // user-added routable fleet node — each one its own provider ("fleet-<id>")
  // in every model picker. Loaded for DISPLAY ONLY: the add form always starts
  // EMPTY. (It used to prefill from the saved slot, so "add another endpoint"
  // silently round-tripped and overwrote the first one — the bug this fixes.)
  const [endpoints, setEndpoints] = useState<EndpointRow[]>([]);
  const [epName, setEpName] = useState("");
  const [epBusy, setEpBusy] = useState<string | null>(null);
  const [epError, setEpError] = useState<string | null>(null);
  // Inline rename (v1.102.1). PATCH /fleet/nodes/{id} has always accepted a
  // label — the Fleet page got the control in v1.100.0, but this is the page
  // where endpoints are actually managed, so it was missing where it counts.
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");

  async function reloadEndpoints() {
    try {
      // /fleet alone (v1.103.1). The seeded slot used to be read separately
      // from /settings to render its own row; it now comes through as a node
      // like any other, so that extra request bought nothing.
      const f = await get<{ nodes?: { node?: EndpointNodeDump }[] }>("/fleet");
      setEndpoints(
        (f.nodes ?? [])
          .map((row) => row.node)
          // Include the config-seeded slot (v1.103.1). Filtering to
          // source === "user" pushed it into a bespoke "legacy" row with the
          // name hardcoded to "custom" and no rename — and registry.update()
          // deliberately KEEPS source="config" after promoting a seed, so it
          // would have stayed excluded even once renamed.
          .filter(
            (n): n is EndpointNodeDump =>
              Boolean(n && n.id && (n.source === "user" || n.source === "config") && n.routable),
          )
          .map((n) => ({
            id: n.id,
            seeded: n.source === "config",
            label: n.label || n.id,
            base_url: n.base_url || "",
            default_model: n.default_model || "",
            api_key_name: n.api_key_name || "",
            tool_use: n.tool_use ?? null,
            vision: n.vision ?? null,
          })),
      );
    } catch {
      /* the list is best-effort — an unreachable daemon just shows nothing */
    }
  }
  useEffect(() => {
    if (isCustom) void reloadEndpoints();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isCustom]);

  /** Re-run the live tool-capability probe for one endpoint row. */
  async function verifyEndpoint(ep: EndpointRow) {
    setEpBusy(ep.id);
    setEpError(null);
    try {
      const v = await post<VerifyResult>(
        `/fleet/nodes/${encodeURIComponent(ep.id)}/verify`,
        { model: ep.default_model },
      );
      if (v.tool_use === null && v.error) setEpError(`verify: ${v.error}`);
      void reloadEndpoints();
    } catch (err) {
      setEpError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setEpBusy(null);
    }
  }

  /** Delete a user-added endpoint (its provider unregisters live); the vault
   *  key created with it is cleaned up best-effort. */
  async function saveRename(ep: EndpointRow) {
    const label = renameDraft.trim();
    setRenaming(null);
    if (!label || label === ep.label) return; // nothing to do — not an error
    setEpBusy(ep.id);
    setEpError(null);
    try {
      await patch(`/fleet/nodes/${encodeURIComponent(ep.id)}`, { label });
      void reloadEndpoints();
    } catch (err) {
      setEpError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setEpBusy(null);
    }
  }

  async function removeEndpoint(ep: EndpointRow) {
    setEpBusy(ep.id);
    setEpError(null);
    try {
      await del(`/fleet/nodes/${encodeURIComponent(ep.id)}`);
      if (ep.api_key_name) {
        try {
          await del(`/secrets/${encodeURIComponent(ep.api_key_name)}`);
        } catch {
          /* the key may already be gone */
        }
      }
      void reloadEndpoints();
    } catch (err) {
      setEpError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setEpBusy(null);
    }
  }

  /** Clear the legacy single-slot endpoint (settings-managed "custom"). */
  async function removeLegacy() {
    setEpBusy("legacy");
    setEpError(null);
    try {
      await put("/settings", { values: { custom_base_url: "", custom_model: "" } });
      try {
        await del("/connections/custom");
      } catch {
        /* no stored key — fine */
      }
      void reloadEndpoints();
      onChanged();
    } catch (err) {
      setEpError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setEpBusy(null);
    }
  }

  // Probe the endpoint for ITS OWN model list as the user types (debounced) —
  // /v1/models (or Ollama's /api/tags) knows the ids, the user shouldn't have
  // to. The optional key rides along: some gateways guard /v1/models too.
  useEffect(() => {
    if (!isCustom || !open) return;
    const url = baseUrl.trim();
    if (!/^https?:\/\/.+/i.test(url)) {
      setDetected(null);
      setDetectError(null);
      return;
    }
    let cancelled = false;
    setDetecting(true);
    setDetectError(null);
    const timer = setTimeout(async () => {
      try {
        const res = await post<{ models: string[]; error?: string }>(
          "/providers/endpoint-models",
          { base_url: url, api_key: key.trim() },
        );
        if (cancelled) return;
        if (res.error || res.models.length === 0) {
          setDetected([]);
          setDetectError(res.error || "the endpoint reported no models");
        } else {
          setDetected(res.models);
          setDetectError(null);
          // Zero-typing path: an empty model field auto-picks the first one.
          setModel((m) => m || res.models[0]);
        }
      } catch (err) {
        if (!cancelled) {
          setDetected([]);
          setDetectError(err instanceof ApiError ? err.message : String(err));
        }
      } finally {
        if (!cancelled) setDetecting(false);
      }
    }, 700);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      setDetecting(false);
    };
  }, [isCustom, open, baseUrl, key]);

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
        // Every save creates a NEW endpoint (its own provider) — nothing is
        // ever overwritten; delete rows in the Saved-endpoints list instead.
        const created = await post<{ node?: { id?: string; label?: string } }>(
          "/fleet/nodes",
          {
            base_url: baseUrl.trim(),
            label: epName.trim(),
            routable: true,
            default_model: model.trim(),
          },
        );
        const nodeId = created.node?.id ?? "";
        if (key.trim() && nodeId) {
          // The optional key: vaulted under a per-endpoint name, then wired to
          // the node so its adapter sends Authorization on every request.
          const secretName = `endpoint_${nodeId}_key`;
          await post("/secrets", {
            name: secretName,
            value: key.trim(),
            kind: "api_key",
            description: `API key for endpoint ${epName.trim() || baseUrl.trim()}`,
          });
          await patch(`/fleet/nodes/${encodeURIComponent(nodeId)}`, {
            api_key_name: secretName,
          });
        }
        const shown = epName.trim() || created.node?.label || nodeId || "it";
        // AUTO-VERIFY tool support right away (live ping-tool probe): without
        // it the router treats the endpoint as tools-incapable and quietly
        // sends every tool-using turn to another provider.
        let verifyNote = "";
        if (nodeId) {
          try {
            const v = await post<VerifyResult>(
              `/fleet/nodes/${encodeURIComponent(nodeId)}/verify`,
              { model: model.trim() },
            );
            verifyNote =
              v.tool_use === true
                ? " Tool support verified ✓ — web/file turns can run here."
                : v.tool_use === false
                  ? " Heads-up: this server can't run tools — turns that use web/files will route to another provider."
                  : " Couldn't verify tool support yet (endpoint asleep?) — use Verify on its row later.";
          } catch {
            verifyNote = "";
          }
        }
        setTest({
          ok: true,
          detail: `Endpoint saved — pick "${shown}" in any model picker.${verifyNote}`,
        });
        setEpName("");
        setBaseUrl("");
        setModel("");
        void reloadEndpoints();
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
      ref={endpointsFocusRef}
      id={id}
      className="card-surface flex scroll-mt-24 flex-col gap-4 p-5 transition-all duration-300 hover:-translate-y-0.5 hover:shadow-card-hover"
    >
      {/* Header: icon + name + status */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl border border-white/[0.08] bg-white/[0.03]">
            <ProviderMark
              id={conn.provider}
              size={19}
              fallback={<Icon size={19} className={meta.tint} />}
            />
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

      {conn.status === "no_tools" && conn.detail ? (
        <p className="rounded-lg border border-amber-500/20 bg-amber-500/[0.06] px-2.5 py-1.5 text-[11px] leading-relaxed text-amber-200/90">
          {conn.detail}
        </p>
      ) : null}

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
          {/* Account login (OAuth) — only for user-registered-app providers
              (Google/Gemini, Dropbox, Drive, OneDrive). Anthropic/OpenAI are
              API-key-only; their subscription is inherited from the CLI, so
              canOAuth is false and this button never shows for them. */}
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

          {/* Saved endpoints (custom card): every endpoint added, each its own
              provider — with delete. The add form below always starts empty. */}
          {isCustom && endpoints.length > 0 && (
            <div className="space-y-1.5">
              <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
                Saved endpoints
              </span>
              {endpoints.map((ep) => (
                <div
                  key={ep.id}
                  className="flex flex-wrap items-center gap-2 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2"
                >
                  {renaming === ep.id ? (
                    <input
                      autoFocus
                      value={renameDraft}
                      onChange={(e) => setRenameDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") void saveRename(ep);
                        if (e.key === "Escape") setRenaming(null);
                      }}
                      onBlur={() => void saveRename(ep)}
                      aria-label="Endpoint name"
                      className="w-32 shrink-0 rounded-md border border-accent/40 bg-ink-950 px-1.5 py-0.5 text-[11px] text-zinc-100 outline-none"
                    />
                  ) : (
                    <button
                      type="button"
                      onClick={() => {
                        setRenameDraft(ep.label);
                        setRenaming(ep.id);
                      }}
                      title={`${ep.label} — click to rename`}
                      className="group/rn flex max-w-[9rem] shrink-0 items-center gap-1 truncate rounded px-1 py-0.5 text-[11px] font-medium text-zinc-300 transition-colors hover:bg-white/[0.06]"
                    >
                      <span className="truncate">{ep.label}</span>
                      <Pencil
                        size={10}
                        className="shrink-0 text-zinc-600 opacity-0 transition-opacity group-hover/rn:opacity-100"
                      />
                    </button>
                  )}
                  <span
                    className="min-w-0 flex-1 truncate font-mono text-[10px] text-zinc-500"
                    title={ep.base_url}
                  >
                    {ep.base_url}
                  </span>
                  {ep.default_model && (
                    <span className="shrink-0 rounded bg-white/[0.05] px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
                      {ep.default_model}
                    </span>
                  )}
                  {/* Tool-capability chip — decides whether tool turns can
                      stay on this endpoint or route to another provider. */}
                  {ep.vision === true && (
                    <span
                      className="shrink-0 rounded-full border border-emerald-400/25 bg-emerald-400/[0.08] px-1.5 py-0.5 text-[10px] text-emerald-300/90"
                      title="Verified: this model SAW the probe image — image turns and scanned-PDF OCR can run here"
                    >
                      vision ✓
                    </span>
                  )}
                  {ep.vision === false && (
                    <span
                      className="shrink-0 rounded-full border border-white/10 px-1.5 py-0.5 text-[10px] text-zinc-500"
                      title="Verified: this model answered but did not see the probe image — image turns route to a vision-capable provider"
                    >
                      no vision
                    </span>
                  )}
                  {ep.tool_use === true ? (
                    <span
                      className="shrink-0 rounded-full border border-emerald-400/25 bg-emerald-400/[0.08] px-1.5 py-0.5 text-[10px] text-emerald-300/90"
                      title="Verified: this server runs tools — web/file turns stay here"
                    >
                      tools ✓
                    </span>
                  ) : ep.tool_use === false ? (
                    <span
                      className="shrink-0 rounded-full border border-amber-400/25 bg-amber-400/[0.08] px-1.5 py-0.5 text-[10px] text-amber-200/90"
                      title="Verified: this server can't run tools — turns that use web/files route to another provider (the reply says so)"
                    >
                      no tools
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => void verifyEndpoint(ep)}
                      disabled={epBusy === ep.id}
                      className="shrink-0 rounded-full border border-white/10 px-1.5 py-0.5 text-[10px] text-zinc-400 transition-colors hover:border-accent/30 hover:text-accent-soft disabled:opacity-50"
                      title="Tool support unverified — tool turns route elsewhere until verified. Click to probe this server now."
                    >
                      {epBusy === ep.id ? "…" : "Verify tools"}
                    </button>
                  )}
                  {/* Capability envelope (v1.201.0): provenance chip once a
                      profile exists + Measure. Addressing: config-seeded
                      slots use the node id AS the provider — fleet/registry
                      renders BOTH ollama_base_url (id="ollama") and
                      custom_base_url (id="custom") as source="config" nodes,
                      so hardcoding "custom" here would probe the WRONG
                      server for the ollama slot and file the measurement
                      under custom__<model>.json. User nodes are their own
                      "fleet-<id>" provider. */}
                  {ep.default_model && (
                    <EnvelopeRowControls
                      provider={ep.seeded ? ep.id : `fleet-${ep.id}`}
                      model={ep.default_model}
                    />
                  )}
                  <ConfirmButton
                    className="shrink-0"
                    onConfirm={() => void (ep.seeded ? removeLegacy() : removeEndpoint(ep))}
                    label={epBusy === ep.id ? "…" : "Delete"}
                    confirmLabel="Delete?"
                    title={`Remove "${ep.label}" — its provider disappears from every picker; the saved key is cleaned up`}
                  />
                  {/* The report card for THIS endpoint's provider
                      ("fleet-<id>") — what auto-tier's quality judgment sees
                      (v1.169.0). Full-width so it wraps under the row; the
                      wrapper renders only when a report exists, so an
                      empty div never adds a phantom gap row. */}
                  {quality.some(
                    (r) =>
                      r.provider === `fleet-${ep.id}` && r.task_class == null,
                  ) && (
                    <div className="w-full basis-full">
                      <ModelReportLine
                        rows={quality}
                        provider={`fleet-${ep.id}`}
                      />
                    </div>
                  )}
                </div>
              ))}
              {epError && <ErrorNote>{epError}</ErrorNote>}
              <p className="text-[10px] leading-relaxed text-zinc-600">
                Each endpoint is its own provider in every model picker.
              </p>
            </div>
          )}

          {/* API key */}
          {canKey &&
            (!open ? (
              <button
                onClick={() => setOpen(true)}
                className={`${canOAuth ? "btn-ghost" : "btn-accent"} w-full py-1.5 text-xs`}
              >
                {isCustom ? <Plus size={14} /> : <KeyRound size={14} />}{" "}
                {canOAuth
                  ? "Use an API key instead"
                  : isCustom
                    ? endpoints.length > 0
                      ? "Add another endpoint"
                      : "Add an endpoint"
                    : "Connect"}
              </button>
            ) : (
              <form onSubmit={connectKey} className="space-y-2.5">
                {isCustom && (
                  <>
                    <label className="block space-y-1">
                      <span className="text-[11px] font-medium text-zinc-400">
                        Name <span className="font-normal text-zinc-600">(how it shows in pickers)</span>
                      </span>
                      <input
                        type="text"
                        value={epName}
                        onChange={(e) => setEpName(e.target.value)}
                        placeholder="e.g. vLLM box / Ollama Cloud"
                        autoComplete="off"
                        className="field text-xs"
                      />
                    </label>
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
                      <span className="flex items-center justify-between text-[11px] font-medium text-zinc-400">
                        <span>Model</span>
                        <span className="font-normal text-zinc-500">
                          {detecting
                            ? "checking endpoint…"
                            : detected && detected.length > 0
                              ? `${detected.length} model${detected.length === 1 ? "" : "s"} on this endpoint`
                              : null}
                        </span>
                      </span>
                      {detected && detected.length > 0 && !manualModel ? (
                        <>
                          <select
                            value={model}
                            onChange={(e) => setModel(e.target.value)}
                            className="field w-full font-mono text-xs"
                          >
                            {model && !detected.includes(model) && (
                              <option value={model}>{model} (saved)</option>
                            )}
                            {detected.map((m) => (
                              <option key={m} value={m}>
                                {m}
                              </option>
                            ))}
                          </select>
                          <button
                            type="button"
                            onClick={() => setManualModel(true)}
                            className="text-[10px] text-zinc-500 transition-colors hover:text-zinc-300"
                          >
                            type a model id manually instead
                          </button>
                        </>
                      ) : (
                        <>
                          <input
                            type="text"
                            value={model}
                            onChange={(e) => setModel(e.target.value)}
                            placeholder="e.g. glm-4.7-flash / llama3"
                            autoComplete="off"
                            className="field font-mono text-xs"
                          />
                          {detected && detected.length > 0 && manualModel && (
                            <button
                              type="button"
                              onClick={() => setManualModel(false)}
                              className="text-[10px] text-zinc-500 transition-colors hover:text-zinc-300"
                            >
                              pick from the {detected.length} detected model
                              {detected.length === 1 ? "" : "s"}
                            </button>
                          )}
                          {detectError && (
                            <p className="text-[10px] leading-relaxed text-amber-300/80">
                              Couldn&apos;t list this endpoint&apos;s models ({detectError}) —
                              type the id manually.
                            </p>
                          )}
                        </>
                      )}
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

      {/* Runs on the legacy config slot are recorded under provider "custom"
          — that report card belongs on this card too (v1.169.0). Rendered for
          the card's OWN provider, not just "custom": any LOCAL provider that
          gets a ConnectionCard shows its report where the user configures it
          (the plan's "on each local provider's card"), and the guard inside
          ModelReportLine keeps every cloud card silent. `surface="card"`
          scopes the testid so a provider that also appears on the CLI-tools
          row (ollama) never renders two nodes with one testid. */}
      <ModelReportLine rows={quality} provider={conn.provider} surface="card" />

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
    provider: "opencode-cli",
    name: "OpenCode CLI",
    description: "Your local models only",
    hint: "point an OpenCode provider at a server on your own network",
    icon: Terminal,
    tint: "text-sky-300",
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

function CliProviderRow({
  info,
  available,
  report = [],
  envelopeModels = [],
}: {
  info: CliProviderInfo;
  available: boolean;
  /** Model report card rows (v1.169.0) — rendered only for LOCAL providers
   *  (ollama, opencode-cli); the cloud CLI rows never get a report line. */
  report?: QualityRow[];
  /** Models on this LOCAL provider that the envelope can measure (v1.201.0):
   *  derived by the page from quality rows + the health default. Cloud CLI
   *  rows always get [] — a trusted provider has nothing to measure. */
  envelopeModels?: string[];
}) {
  const Icon = info.icon;
  return (
    <div className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
      <div className="flex min-w-0 items-center gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-white/[0.08] bg-white/[0.03]">
          <ProviderMark
            id={info.provider}
            size={16}
            fallback={<Icon size={16} className={info.tint} />}
          />
        </span>
        <div className="min-w-0">
          <div className="text-sm font-medium text-zinc-100">{info.name}</div>
          <div className="truncate text-[11px] text-zinc-500">
            {info.description}
            {!available && <span className="text-zinc-600"> · {info.hint}</span>}
          </div>
          <ModelReportLine rows={report} provider={info.provider} />
          {envelopeModels.map((m) => (
            <div key={m} className="mt-1 flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-[10px] text-zinc-500">{m}</span>
              <EnvelopeRowControls provider={info.provider} model={m} />
            </div>
          ))}
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

/** One entry of POST /providers/rescan's `detected` list (DetectedModel.as_dict). */
interface RescannedModel {
  provider: string;
  model: string;
  name: string;
  available: boolean;
  source: string;
  base_url: string | null;
  exec_path: string | null;
  context_window: number | null;
  detail: string;
}


/* -------------------------------------------------------------------------- */
/*  Where else things connect                                                  */
/* -------------------------------------------------------------------------- */

/**
 * The rest of the connect surfaces (v1.100.0).
 *
 * This directory used to live on a separate Integrations page. It is NOT
 * redundant with the sidebar: /tools and /channels are Advanced-only, so in
 * Simple mode — the default — these tiles are the ONLY way to reach them. The
 * "AI accounts" tile is gone because you are already on that page.
 */
const CONNECT_ELSEWHERE = [
  {
    href: "/tools",
    title: "Plug-ins (MCP)",
    desc: "Ready-made plug-ins that give Jarvis new abilities.",
    icon: <Blocks size={17} />,
  },
  {
    href: "/channels",
    title: "Slack / Telegram / Email",
    desc: "Get updates and reply to Jarvis where you already chat.",
    icon: <MessagesSquare size={17} />,
  },
  {
    href: "/memory?scope=longterm",
    title: "Cloud drives for memory",
    desc: "Box, Drive, Dropbox and more — long-term memory storage.",
    icon: <Cloud size={17} />,
  },
];

function ConnectElsewhereTile({
  tile,
}: {
  tile: (typeof CONNECT_ELSEWHERE)[number];
}) {
  return (
    <Link
      href={tile.href}
      className="group flex items-start gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3.5 py-3 transition-colors hover:border-accent/25 hover:bg-accent/[0.04]"
    >
      <span className="mt-0.5 shrink-0 text-zinc-500 transition-colors group-hover:text-accent-soft">
        {tile.icon}
      </span>
      <span className="min-w-0">
        <span className="flex items-center gap-1.5 text-[13px] font-medium text-zinc-200">
          {tile.title}
          <ArrowRight
            size={13}
            className="shrink-0 text-zinc-600 transition-all group-hover:translate-x-0.5 group-hover:text-accent-soft"
          />
        </span>
        <span className="mt-0.5 block text-xs leading-relaxed text-zinc-500">
          {tile.desc}
        </span>
      </span>
    </Link>
  );
}

export default function ConnectionsPage() {
  const { data, error, loading, reload } = useApi<{ connections: Connection[] }>("/connections");
  // The model report card (v1.169.0): the router's own quality judgment of
  // each LOCAL model — server truth, fetched once; best-effort (a missing
  // report just renders no lines).
  const { data: qualityData } = useApi<{
    bar: number;
    min_samples: number;
    rows: QualityRow[];
  }>("/routing/quality");
  const qualityRows = qualityData?.rows ?? [];
  const { health, refresh: refreshHealth } = useDaemon();
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

  // Which models a LOCAL provider's row can Measure (v1.201.0): the models
  // the router has judged (quality rows), plus the health default when this
  // provider IS the default. Cloud providers get [] — trusted by
  // construction, nothing to measure — and so does opencode-cli (see
  // hasEnvelopeSurface).
  const envelopeModelsFor = (provider: string): string[] => {
    if (!hasEnvelopeSurface(provider)) return [];
    const models = qualityRows
      .filter((r) => r.provider === provider && r.task_class == null && r.model)
      .map((r) => r.model);
    if (health?.default_provider === provider && health.default_model) {
      models.push(health.default_model);
    }
    return [...new Set(models)];
  };

  /* --- Rescan local CLIs (POST /providers/rescan) --------------------------- */
  // Re-detects locally installed CLI inference providers (Claude/Codex/Grok
  // CLIs) on demand, so a CLI installed mid-session shows up without a
  // daemon restart.
  const [rescanBusy, setRescanBusy] = useState(false);
  const [rescanNote, setRescanNote] = useState<{ ok: boolean; text: string } | null>(null);

  async function rescanClis() {
    setRescanBusy(true);
    setRescanNote(null);
    try {
      const r = await post<{ detected: RescannedModel[] }>("/providers/rescan");
      const detected = r.detected ?? [];
      const label = (id: string) => CLI_PROVIDERS.find((p) => p.provider === id)?.name ?? id;
      const ready = [...new Set(detected.filter((m) => m.available).map((m) => m.provider))];
      const notReady = [
        ...new Set(detected.filter((m) => !m.available).map((m) => m.provider)),
      ].filter((p) => !ready.includes(p));
      const parts: string[] = [];
      if (ready.length) {
        const n = detected.filter((m) => m.available).length;
        parts.push(
          `${ready.map(label).join(", ")} ready to use (${n} model${n === 1 ? "" : "s"})`,
        );
      }
      for (const p of notReady) {
        const d = detected.find((m) => m.provider === p && m.detail)?.detail;
        parts.push(`${label(p)} found but not usable${d ? ` — ${d}` : ""}`);
      }
      setRescanNote({
        ok: true,
        text: parts.length
          ? `Rescan complete: ${parts.join("; ")}.`
          : "Rescan complete — no local CLI providers detected. Install (and log into) the Claude, Codex, or Grok CLI and it will appear here.",
      });
      reload(); // connections list
      refreshHealth(); // /health providers → the "Detected" pills below
    } catch (err) {
      setRescanNote({
        ok: false,
        text: err instanceof ApiError ? err.message : String(err),
      });
    } finally {
      setRescanBusy(false);
    }
  }

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
                            <ProviderMark
                              id={c.provider}
                              size={14}
                              fallback={<MenuIcon size={14} className={m.tint} />}
                            />
                            <span className="flex-1 truncate">{c.display_name}</span>
                          </button>
                        );
                      })
                    )}
                    <div className="my-1.5 h-px bg-white/[0.08]" />
                    <Link
                      href="/memory?scope=longterm"
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
                      <span className="flex-1">Plug-ins (MCP)</span>
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
                quality={qualityRows}
              />
            ))}
          </div>
        )}
      </Reveal>

      <Reveal>
        <Card
          title="Subscription & local providers"
          icon={<Terminal size={16} className="text-accent-soft" />}
          right={
            <button
              type="button"
              onClick={rescanClis}
              disabled={rescanBusy}
              title="Re-detect locally installed CLI providers (Claude, Codex, Grok) without restarting the daemon"
              className="btn-ghost px-2.5 py-1 text-xs"
            >
              {rescanBusy ? (
                <LoaderInline label="Scanning…" />
              ) : (
                <>
                  <RefreshCw size={13} /> Rescan local CLIs
                </>
              )}
            </button>
          }
        >
          {rescanNote && (
            <div className="mb-3">
              {rescanNote.ok ? (
                <SuccessNote>{rescanNote.text}</SuccessNote>
              ) : (
                <ErrorNote>{rescanNote.text}</ErrorNote>
              )}
            </div>
          )}
          <div className="divide-y divide-white/[0.06]">
            {CLI_PROVIDERS.map((info) => (
              <CliProviderRow
                key={info.provider}
                info={info}
                available={isDetected(info.provider)}
                report={qualityRows}
                envelopeModels={envelopeModelsFor(info.provider)}
              />
            ))}
          </div>
          <p className="mt-3 text-[11px] leading-relaxed text-zinc-600">
            These use plans you already pay for — no API keys. Pick them in any model picker.
          </p>
        </Card>
      </Reveal>

      <Reveal>
        <RestHookups />
      </Reveal>

      <Reveal>
        <Card title="Where else things connect" icon={<Compass size={15} />}>
          <div className="grid gap-3 sm:grid-cols-2">
            {CONNECT_ELSEWHERE.map((tile) => (
              <ConnectElsewhereTile key={tile.href} tile={tile} />
            ))}
          </div>
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
