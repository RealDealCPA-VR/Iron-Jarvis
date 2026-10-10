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
import {
  EndpointModelPicker,
  EndpointProtocolChoice,
  type EndpointProtocol,
} from "@/components/connections/EndpointModelPicker";
import { useApi } from "@/lib/useApi";
import { useFocusRef } from "@/lib/useFocusRef";
import { useDaemon } from "@/lib/daemon";
import { providerDisplay } from "@/lib/onboarding";
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
  /** v1.329.0: the API the endpoint chats in ("openai" when absent). */
  protocol: EndpointProtocol;
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
  protocol?: string;
}

/** POST /fleet/nodes/{id}/verify response (tool + vision capability probes). */
interface VerifyResult {
  tool_use: boolean | null;
  vision?: boolean | null;
  error?: string;
}
import {
  Card,
  Code,
  OfflineHint,
  SkeletonRows,
  ErrorNote,
  SuccessNote,
  LoaderInline,
  ConfirmButton,
} from "@/components/ui";
import { RestHookups } from "@/components/connections/RestHookups";
import { IronProxyCard } from "@/components/connections/IronProxyCard";
import {
  EnvelopeRowControls,
  MeasuredEndpoints,
  type MeasuredEntry,
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
              className="flex items-start gap-1 text-[11px] leading-relaxed text-zinc-500"
            >
              <Gauge size={10} className="mt-0.5 shrink-0 text-zinc-600" />
              <span className="min-w-0">
                {mine.length > 1 && r.model ? (
                  <span className="font-mono text-zinc-400">{r.model}: </span>
                ) : null}
                {qualityLine(r, classRows)}
              </span>
            </p>
            {/* Envelope measurements moved OUT of the tiles (v1.204.0, live
                user feedback: they made the cards enormous) — they render in
                the MeasuredEndpoints section below the connect cards. The
                Measure button + provenance chip stay on the endpoint rows. */}
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
    tint: "text-tone-warn",
    keyUrl: "https://console.anthropic.com/settings/keys",
    keyLabel: "console.anthropic.com",
    placeholder: "sk-ant-…",
  },
  openai: {
    icon: Bot,
    tint: "text-tone-success",
    keyUrl: "https://platform.openai.com/api-keys",
    keyLabel: "platform.openai.com",
    placeholder: "sk-…",
  },
  google: {
    icon: Globe,
    tint: "text-tone-info",
    docsUrl: "https://console.cloud.google.com/apis/credentials",
    docsLabel: "Google Cloud Console",
  },
  xai: {
    icon: Zap,
    tint: "text-tone-violet",
    keyUrl: "https://console.x.ai",
    keyLabel: "console.x.ai",
    placeholder: "xai-…",
  },
  openrouter: {
    icon: PlugZap,
    tint: "text-tone-danger",
    keyUrl: "https://openrouter.ai/settings/keys",
    keyLabel: "openrouter.ai",
    placeholder: "sk-or-…",
  },
  custom: {
    icon: Cpu,
    tint: "text-tone-info",
    placeholder: "key (optional for local servers)",
  },
  mock: { icon: MoonStar, tint: "text-tone-warn" },
};

function metaFor(provider: string): ProviderMeta {
  return META[provider] ?? { icon: Cpu, tint: "text-zinc-300" };
}

/**
 * v1.316.0 (UX wave 4, oauth-cards-hidden-dev-setup): the daemon now says
 * whether an account login could START (`oauth_client_configured`, a bool —
 * never the id). Additive and local here: an older daemon sends nothing, and
 * then the card keeps today's "Log in with your account".
 */
type ConnRow = Connection & { oauth_client_configured?: boolean };

/**
 * v1.316.0 (UX wave 4, connections-model-path-buried): the cards came in the
 * daemon's alphabetical order — AI models, memory drives and creative media
 * mixed in one grid, so someone who came to "connect a model" read ten equal
 * cards. Grouped now, AI models first. An UNKNOWN provider lands in AI models
 * (most new providers are models), so no card is ever dropped; every card
 * keeps its `conn-card-${provider}` id (deep links and Fleet target it).
 */
type ConnGroupKey = "ai" | "drives" | "creative";
const CONN_GROUPS: { key: ConnGroupKey; title: string; hint: string }[] = [
  { key: "ai", title: "AI models", hint: "Where answers come from." },
  {
    key: "drives",
    title: "Cloud drives for memory",
    hint: "Files Jarvis can search and remember.",
  },
  { key: "creative", title: "Creative media", hint: "Images, video and audio." },
];
const GROUP_OF: Record<string, ConnGroupKey> = {
  dropbox: "drives",
  google_drive: "drives",
  onedrive: "drives",
  box: "drives",
  pixio: "creative",
};
/** Order inside AI models: the big model makers first, your own endpoint
 *  last. A fixed order (not "connected first"), so a card never jumps away
 *  from under the pointer the moment it connects. */
const AI_ORDER = ["anthropic", "openai", "google", "xai", "openrouter", "custom"];
function groupOf(provider: string): ConnGroupKey {
  return GROUP_OF[provider] ?? "ai";
}
function aiRank(provider: string): number {
  if (provider === "mock") return AI_ORDER.length + 2;
  const i = AI_ORDER.indexOf(provider);
  return i === -1 ? AI_ORDER.length + 1 : i;
}
/** The drives the Directory also connects under the SAME connection id
 *  (its connectors use provider=google_drive / onedrive / dropbox), so the
 *  card can say it is one connection in two places. */
const DIRECTORY_DRIVES = new Set(["dropbox", "google_drive", "onedrive"]);

/** The address a user-registered app must allow as its redirect — the
 *  platform resolver's default (`platform.py`; a
 *  `<provider>_oauth_redirect_uri` secret overrides it). */
function oauthCallback(provider: string): string {
  return `http://localhost:8787/oauth/${provider}/callback`;
}

/* -------------------------------------------------------------------------- */
/*  Status pill                                                                */
/* -------------------------------------------------------------------------- */

/** The CLI a keyless provider is served through ("claude-cli"), else null —
 *  read off the daemon's `source` so this page and /health agree (v1.230.0). */
function inheritedVia(conn: Pick<Connection, "source">): string | null {
  const m = /^inherited from (\S+)/.exec(conn.source ?? "");
  return m ? m[1] : null;
}

function StatusPill({ conn }: { conn: Connection }) {
  let tone: string;
  let label: string;
  // A connection that loaded ZERO tools is not usable, however green it looks
  // (v1.172.0): MCP tools load once at daemon boot, so a server added since
  // startup — or one whose command failed to launch — delivers nothing while
  // the old flat "Connected" badge insisted it was fine. That badge is exactly
  // what hid a dark wiki from a user who then found Jarvis "blind as a bat".
  if (conn.status === "no_tools") {
    tone = "border-tone-warn/25 bg-tone-warn/10 text-tone-warn";
    label = "0 tools — restart";
  } else if (conn.connected) {
    tone = "border-tone-success/25 bg-tone-success/10 text-tone-success";
    // Inherited (v1.230.0, U5): connected THROUGH the logged-in CLI, no key
    // stored here — say so, instead of "Not connected" under an available
    // provider (the audit's live finding).
    // v1.314.0 (UX wave 2): the product the user signed in to ("Claude
    // Code"), never the daemon's CLI id ("claude-cli"), and still told apart
    // from a key stored here ("Connected"). The raw `source` rides the title.
    const via = inheritedVia(conn);
    label = via ? `Connected · via ${providerDisplay(via)}` : "Connected";
  } else if (conn.status === "needs_auth") {
    tone = "border-tone-warn/25 bg-tone-warn/10 text-tone-warn";
    label = "Needs auth";
  } else {
    tone = "border-zinc-500/25 bg-zinc-500/10 text-zinc-300";
    label = "Not connected";
  }
  return (
    <span
      data-testid="conn-status-pill"
      title={conn.source || undefined}
      // whitespace-nowrap (v1.314.0): the inherited label wrapped to two
      // lines on desktop and three on a phone.
      className={`inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${tone}`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${
          conn.status === "no_tools"
            ? "bg-tone-warn"
            : conn.connected
              ? "bg-tone-success"
              : conn.status === "needs_auth"
                ? "bg-tone-warn"
                : "bg-zinc-500"
        }`}
      />
      {label}
    </span>
  );
}

/**
 * v1.329.0 (calm J3): the ONE quiet chip a saved-endpoint row draws its tags
 * with: the Anthropic tag, the model, tools / vision and Verify tools. The
 * rows mixed two looks (a hairline "Anthropic" tag beside filled emerald and
 * amber chips Daylight did not re-ink); now the shell is the same for all and
 * only a small mark inside carries a tone token (a check for a verified yes,
 * a dot for a "no" that sends work elsewhere).
 */
const ENDPOINT_CHIP =
  "inline-flex shrink-0 items-center gap-1 rounded-full border border-white/10 px-1.5 py-0.5 text-[11px] leading-none text-zinc-400";

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
  // The model id. v1.328.0 (B6): its list comes from the server only when
  // the user presses "Fetch available models" (EndpointModelPicker) — the old
  // probe ran on every keystroke of the address AND the key, sending a
  // half-typed key to a half-typed host.
  const [model, setModel] = useState("");
  // v1.329.0 (H3): the API the endpoint chats in. Saved on the node
  // (POST /fleet/nodes `protocol`) and used for every reply, so a model the
  // server listed the Anthropic way also answers the Anthropic way. Set by
  // the user's choice, or by the way "Fetch available models" got an answer.
  const [protocol, setProtocol] = useState<EndpointProtocol>("openai");
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
  // v1.316.0: the login needs an app the USER registers first, and the daemon
  // says none exists yet. Keyed on canOAuth (never on oauth_help — xAI carries
  // help text but is key-only). Absent field (older daemon) = today's card.
  const needsSetup = canOAuth && (conn as ConnRow).oauth_client_configured === false;

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
            protocol: (n.protocol === "anthropic" ? "anthropic" : "openai") as EndpointProtocol,
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
            protocol,
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
                ? " It runs tools, so turns that use the web or files can run here."
                : v.tool_use === false
                  ? " This server can't run tools, so turns that use the web or files go to another model."
                  : " Tool support could not be checked yet. The server may be asleep; press Verify tools on its row later.";
          } catch {
            verifyNote = "";
          }
        }
        setTest({
          ok: true,
          detail: `Endpoint saved. Pick "${shown}" in any model picker.${verifyNote}`,
        });
        setEpName("");
        setBaseUrl("");
        setModel("");
        setProtocol("openai");
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
      {/* Header: icon + name + status. v1.329.0: the row WRAPS. In a narrow
          card (three across beside the Settings sidebar) the nowrap pill used
          to take its whole width and squeeze the name under it, so "Custom
          endpoint" ran beneath "Not connected". Now the name keeps at least
          11rem and the pill drops to its own line when both do not fit. */}
      <div data-testid="conn-card-header" className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
        <div className="flex min-w-[min(11rem,100%)] flex-1 items-center gap-3">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-white/[0.08] bg-white/[0.03]">
            <ProviderMark
              id={conn.provider}
              size={19}
              fallback={<Icon size={19} className={meta.tint} />}
            />
          </span>
          <div className="min-w-0">
            {/* v1.314.0: the scripted model is never named "Mock" to a user
                (lib/onboarding providerDisplay); the daemon's name stays in
                the title. */}
            <div
              className="text-sm font-semibold text-zinc-100"
              title={isMock ? conn.display_name : undefined}
            >
              {isMock ? providerDisplay("mock") : conn.display_name}
            </div>
            {/* v1.314.0 (UX wave 2): an inherited login has no key, so no
                "API key" method chip; where the login lives is its own line
                (it was squeezed into this row and stacked into a column). */}
            {inheritedVia(conn) ? (
              conn.account ? (
                <div className="text-[11px] text-zinc-600">{conn.account}</div>
              ) : null
            ) : (
              // v1.329.0 (H3): the method chip never breaks inside itself
              // ("API / key" on the Pixio card) and the account wraps under
              // it, word by word, when the card is narrow.
              <div
                data-testid="conn-card-auth"
                className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[11px] text-zinc-500"
              >
                <span className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap">
                  {conn.method === "oauth" ? (
                    <>
                      <ShieldCheck size={11} /> OAuth 2.0
                    </>
                  ) : (
                    <>
                      <KeyRound size={11} /> API key
                    </>
                  )}
                </span>
                {conn.account && (
                  <span className="min-w-0 break-words text-zinc-600">· {conn.account}</span>
                )}
              </div>
            )}
          </div>
        </div>
        <StatusPill conn={conn} />
      </div>
      {/* v1.314.0: its own full-width line, so it never squeezes beside the
          pill. */}
      {inheritedVia(conn) && (
        <p className="-mt-2 text-[11px] leading-snug text-zinc-500">
          Uses your {providerDisplay(inheritedVia(conn))} sign-in — no key stored here.
        </p>
      )}

      {conn.status === "no_tools" && conn.detail ? (
        <p className="rounded-lg border border-tone-warn/20 bg-tone-warn/[0.06] px-2.5 py-1.5 text-[11px] leading-relaxed text-tone-warn">
          {conn.detail}
        </p>
      ) : null}

      {/* Body */}
      {isMock ? (
        <p className="text-xs leading-relaxed text-zinc-500">
          The built-in demo model: scripted replies, no AI. Always available for testing — no
          key required.
        </p>
      ) : conn.connected ? (
        // v1.329.0 (H3): the actions row WRAPS inside the card. Three across
        // beside the Settings sidebar, "Make default" + Test + Disconnect did
        // not fit one line, so Disconnect spilled past the card's right edge
        // and "Make default" broke onto two lines. Each label stays whole and
        // the last button drops to its own line instead.
        <div data-testid="conn-card-actions" className="flex min-w-0 flex-wrap items-center gap-2">
          {isDefault ? (
            <span
              title="Sessions use this provider by default"
              className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-tone-success/25 bg-tone-success/10 px-3 py-1.5 text-xs font-medium text-tone-success"
            >
              <Check size={14} /> Default
            </span>
          ) : (
            <button
              onClick={makeDefault}
              disabled={busy}
              title={`Use ${conn.display_name} for new sessions`}
              className="btn-ghost whitespace-nowrap py-1.5 text-xs"
            >
              {busy ? <LoaderInline label="Setting…" /> : <><Star size={14} /> Make default</>}
            </button>
          )}
          <button onClick={runTest} disabled={busy} className="btn-ghost flex-1 whitespace-nowrap py-1.5 text-xs">
            {busy ? <LoaderInline label="Testing…" /> : <><CheckCircle2 size={14} /> Test</>}
          </button>
          {/* Nothing to disconnect for an inherited login — the key lives in
              the CLI; log out of that CLI to drop it (v1.230.0). */}
          {!inheritedVia(conn) && (
            <ConfirmButton
              onConfirm={disconnect}
              label="Disconnect"
              title={`Disconnect ${conn.display_name}`}
              className="whitespace-nowrap py-1.5"
            />
          )}
        </div>
      ) : (
        <div className="space-y-3">
          {/* Account login (OAuth) — only for user-registered-app providers
              (Google/Gemini, Dropbox, Drive, OneDrive). Anthropic/OpenAI are
              API-key-only; their subscription is inherited from the CLI, so
              canOAuth is false and this button never shows for them. */}
          {canOAuth && (
            <div className="space-y-2">
              {/* v1.316.0 (UX wave 4): when the login needs an app the user
                  registers first, say so BEFORE the press — folded, as steps
                  — instead of after a 400. The button below stays clickable
                  (never gated behind opening the fold), and the old 400 note
                  further down still catches anything this misses. */}
              {needsSetup && (
                <details className="group rounded-xl border border-white/[0.08] bg-white/[0.02] px-3 py-2">
                  <summary className="flex cursor-pointer select-none items-center gap-1.5 text-[12px] font-medium text-zinc-300 transition-colors hover:text-zinc-100 [&::-webkit-details-marker]:hidden">
                    <ChevronRight
                      size={13}
                      aria-hidden
                      className="shrink-0 transition-transform duration-200 group-open:rotate-90"
                    />
                    One-time setup (about 5 minutes)
                  </summary>
                  <ol className="mt-2 list-decimal space-y-1.5 pl-5 text-[11px] leading-relaxed text-zinc-400">
                    <li>
                      {conn.oauth_help ||
                        `${conn.display_name} needs an OAuth app that you register yourself.`}
                    </li>
                    <li>
                      Allow this redirect address:{" "}
                      <Code className="break-all">{oauthCallback(conn.provider)}</Code>
                    </li>
                    <li>
                      In{" "}
                      <Link href="/secrets" className="font-medium text-accent-soft underline">
                        Secrets
                      </Link>
                      , save its client id as <Code>{conn.provider}_oauth_client_id</Code>{" "}
                      and, if it has one, its secret as{" "}
                      <Code>{conn.provider}_oauth_client_secret</Code>.
                    </li>
                    <li>Come back and press Set up &amp; log in.</li>
                  </ol>
                  {meta.docsUrl && (
                    <a
                      href={meta.docsUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-2 flex items-center gap-1 text-[11px] text-zinc-500 transition-colors hover:text-accent-soft"
                    >
                      Manage OAuth app in {meta.docsLabel} <ExternalLink size={11} />
                    </a>
                  )}
                </details>
              )}
              {/* v1.316.0 (accent-overuse-no-primary): outlined, not filled —
                  the page's one filled accent is "Add connection". Full width
                  for a thumb on a phone, its own width from sm. */}
              <button
                onClick={connectOAuth}
                disabled={busy}
                className="btn-soft w-full py-1.5 text-xs sm:w-auto"
              >
                {busy ? (
                  <LoaderInline label="Starting…" />
                ) : (
                  <>
                    <ShieldCheck size={14} />{" "}
                    {needsSetup ? "Set up & log in" : "Log in with your account"}
                  </>
                )}
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
              {conn.oauth_help && !needsSetup && (
                <p className="text-[11px] leading-relaxed text-zinc-500">{conn.oauth_help}</p>
              )}
              {meta.docsUrl && !needsSetup && (
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
                <div className="rounded-xl border border-tone-warn/25 bg-tone-warn/[0.07] px-3 py-2.5 text-[11px] leading-relaxed text-zinc-300">
                  No OAuth client configured. Set{" "}
                  <code className="rounded bg-ink-900/80 px-1 font-mono text-tone-warn">
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
                  {/* basis-24 (v1.329.0): in the narrow card the address used
                      to shrink to one letter beside the name; now it wraps
                      to its own line when it cannot have 6rem. */}
                  <span
                    className="min-w-0 flex-1 basis-24 truncate font-mono text-[11px] text-zinc-500"
                    title={ep.base_url}
                  >
                    {ep.base_url}
                  </span>
                  {/* v1.329.0: an endpoint that chats the Anthropic way says
                      so; the OpenAI way is the long-standing default and stays
                      unmarked. */}
                  {ep.protocol === "anthropic" && (
                    <span
                      data-testid="endpoint-row-protocol"
                      className={ENDPOINT_CHIP}
                      title="Replies use the Anthropic Messages API."
                    >
                      Anthropic
                    </span>
                  )}
                  {ep.default_model && (
                    <span data-testid="endpoint-row-model" className={`${ENDPOINT_CHIP} font-mono`}>
                      {ep.default_model}
                    </span>
                  )}
                  {/* Capability chips. They decide whether tool and image
                      turns stay on this endpoint or go to another model. */}
                  {ep.vision === true && (
                    <span
                      data-testid="endpoint-row-vision"
                      className={ENDPOINT_CHIP}
                      title="Checked: this model saw the test image, so image turns and scanned PDFs can run here."
                    >
                      vision <span aria-hidden className="text-tone-success">✓</span>
                    </span>
                  )}
                  {ep.vision === false && (
                    <span
                      data-testid="endpoint-row-vision"
                      className={ENDPOINT_CHIP}
                      title="Checked: this model answered but did not see the test image, so image turns go to a model that can."
                    >
                      no vision
                    </span>
                  )}
                  {ep.tool_use === true ? (
                    <span
                      data-testid="endpoint-row-tools"
                      className={ENDPOINT_CHIP}
                      title="Checked: this server runs tools, so turns that use the web or files stay here."
                    >
                      tools <span aria-hidden className="text-tone-success">✓</span>
                    </span>
                  ) : ep.tool_use === false ? (
                    <span
                      data-testid="endpoint-row-tools"
                      className={ENDPOINT_CHIP}
                      title="Checked: this server can't run tools, so turns that use the web or files go to another model. The reply says so."
                    >
                      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-tone-warn" />
                      no tools
                    </span>
                  ) : (
                    <button
                      type="button"
                      data-testid="endpoint-row-verify"
                      onClick={() => void verifyEndpoint(ep)}
                      disabled={epBusy === ep.id}
                      className={`${ENDPOINT_CHIP} transition-colors hover:bg-white/[0.06] hover:text-zinc-200 disabled:opacity-50`}
                      title="Tool support is not checked yet, so tool turns go to another model for now. Press to check this server."
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
                className={`${canOAuth ? "btn-ghost" : "btn-soft"} w-full py-1.5 text-xs sm:w-auto`}
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
                        placeholder="http://localhost:1234/v1"
                        autoComplete="off"
                        autoFocus
                        className="field font-mono text-xs"
                      />
                    </label>
                    {/* v1.329.0 (H3): which API the server speaks. Quiet,
                        two options; Fetch below sets it to the way the server
                        answered, and the user can always change it. */}
                    <EndpointProtocolChoice value={protocol} onChange={setProtocol} />
                    {/* v1.328.0 (B6): the Model field + "Fetch available
                        models" — the server lists its own models on request,
                        a searchable list fills the field, typing still works. */}
                    <EndpointModelPicker
                      baseUrl={baseUrl}
                      apiKey={key}
                      value={model}
                      onChange={setModel}
                      protocol={protocol}
                      onProtocol={setProtocol}
                    />
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
                    ? "The key is optional. Local servers usually don't need one. If you set it, it is stored encrypted."
                    : "Paste your API key. It is stored encrypted and never shown again."}
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
                {/* v1.329.0 (H3): wraps like the actions row; "Save endpoint" stays one line. */}
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="submit"
                    disabled={busy || (isCustom ? !baseUrl.trim() : !key.trim())}
                    className="btn-accent flex-1 whitespace-nowrap py-1.5 text-xs"
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

      {/* v1.316.0 (three-overlapping-catalogs): the Directory connects this
          same drive under the SAME connection id — one connection, two doors.
          One name for that page everywhere: "Directory". */}
      {DIRECTORY_DRIVES.has(conn.provider) && (
        <Link
          href="/marketplace"
          className="inline-flex w-fit items-center gap-1 text-[11px] text-zinc-500 transition-colors hover:text-accent-soft"
          title="The Directory's storage cards connect this same account — connecting in either place is one connection"
        >
          Same connection in the Directory <ArrowRight size={11} aria-hidden />
        </Link>
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
    tint: "text-tone-warn",
  },
  {
    provider: "codex-cli",
    name: "Codex CLI",
    description: "Your ChatGPT plan",
    hint: "install / log in via its CLI; appears automatically",
    icon: Bot,
    tint: "text-tone-success",
  },
  {
    provider: "grok-cli",
    name: "Grok CLI",
    description: "Your Grok subscription",
    hint: "install / log in via its CLI; appears automatically",
    icon: Zap,
    tint: "text-tone-violet",
  },
  {
    provider: "opencode-cli",
    name: "OpenCode CLI",
    description: "Your local models only",
    hint: "point an OpenCode provider at a server on your own network",
    icon: Terminal,
    tint: "text-tone-info",
  },
  {
    provider: "ollama",
    name: "Local Ollama",
    description: "Free models running on this machine",
    hint: "install Ollama and pull a model; appears automatically",
    icon: Cpu,
    tint: "text-tone-info",
  },
];

/** v1.234.0: the sign-in remedy per subscription CLI, in the user's words.
 *  Mirrors the daemon's `SIGN_IN_FIX` (providers/cli_auth.py). */
const SIGN_IN_HINT: Record<string, string> = {
  "claude-cli": "run `claude` in a terminal, then /login, then Re-detect",
  "codex-cli": "run `codex login` in a terminal, then Re-detect",
};

function CliProviderRow({
  info,
  available,
  signedOut = false,
  report = [],
  envelopeModels = [],
}: {
  info: CliProviderInfo;
  available: boolean;
  /** v1.234.0: installed on this machine but the CLI says it is NOT signed
   *  in — a third state between "ready" and "not detected", because a
   *  logged-out CLI used to read "Detected — ready to use" and refuse the
   *  user's first message. */
  signedOut?: boolean;
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
            {!available && (
              <span className="text-zinc-600">
                {" "}
                · {signedOut ? (SIGN_IN_HINT[info.provider] ?? info.hint) : info.hint}
              </span>
            )}
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
        <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-tone-success/25 bg-tone-success/10 px-2.5 py-0.5 text-[11px] font-medium text-tone-success">
          <span className="h-1.5 w-1.5 rounded-full bg-tone-success" />
          Detected — ready to use
        </span>
      ) : signedOut ? (
        <span
          data-testid={`cli-signed-out-${info.provider}`}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-tone-warn/25 bg-tone-warn/10 px-2.5 py-0.5 text-[11px] font-medium text-tone-warn"
        >
          <span className="h-1.5 w-1.5 rounded-full bg-tone-warn" />
          Installed — not signed in
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
    title: "Extensions (MCP)",
    desc: "Ready-made extensions that give Jarvis new abilities.",
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
  // The fleet snapshot, for the MeasuredEndpoints section (v1.204.0): the
  // stored endpoints' (provider, model) pairs — the same measurable list the
  // endpoint rows offer Measure for. Best-effort like everything else here.
  const { data: fleetData } = useApi<{ nodes?: { node?: EndpointNodeDump }[] }>("/fleet");
  const { health, refresh: refreshHealth } = useDaemon();
  const offline = error && error.status === 0;
  const connections = data?.connections ?? [];
  // v1.316.0: the cards in titled groups, AI models first (see CONN_GROUPS).
  const grouped = CONN_GROUPS.map((g) => ({
    ...g,
    items: connections
      .filter((c) => groupOf(c.provider) === g.key)
      .map((c, i) => ({ c, i }))
      .sort((a, b) =>
        g.key === "ai" ? aiRank(a.c.provider) - aiRank(b.c.provider) || a.i - b.i : a.i - b.i,
      )
      .map(({ c }) => c),
  })).filter((g) => g.items.length > 0);
  const connectedCount = connections.filter((c) => c.connected).length;
  // The "+ Add connection" dropdown lists these: everything not yet connected
  // (mock is built-in — nothing to connect).
  const notConnected = connections.filter((c) => !c.connected && c.provider !== "mock");

  // Subscription / local providers are DETECTED by the daemon, not configured
  // here — availability comes from the shared /health poll.
  const daemonProviders = health?.providers ?? [];
  const isDetected = (provider: string) =>
    daemonProviders.some((p) => p.provider === provider && p.available);
  // v1.234.0: installed but the CLI itself says "not signed in" — shown as
  // its own amber state with the remedy, never as "Not detected".
  // v1.316.0 (connections-model-path-buried): what ALREADY works with no key,
  // said first — from the SAME detection the Subscription card below uses, so
  // the two can never disagree. A signed-out CLI is not "ready"; nothing
  // detected = no line at all.
  const readyNow = CLI_PROVIDERS.filter((info) => isDetected(info.provider));
  const isSignedOut = (provider: string) =>
    daemonProviders.some(
      (p) => p.provider === provider && Boolean(p.installed) && p.signed_in === false,
    );

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

  // Every (provider, model) the envelope could have measured — endpoint
  // default models, quality-judged local models, and the health default.
  // MeasuredEndpoints GETs each one and shows ONLY those with a stored
  // profile; when nothing is measured the section is absent entirely.
  const measuredEntries: MeasuredEntry[] = (() => {
    const seen = new Set<string>();
    const out: MeasuredEntry[] = [];
    const add = (provider: string, model: string, label: string) => {
      if (!provider || !model || !hasEnvelopeSurface(provider)) return;
      const k = `${provider}\u0000${model}`;
      if (seen.has(k)) return;
      seen.add(k);
      out.push({ provider, model, label });
    };
    for (const row of fleetData?.nodes ?? []) {
      const n = row.node;
      if (!n?.id || !n.routable || !n.default_model) continue;
      if (n.source !== "user" && n.source !== "config") continue;
      // Config-seeded slots ARE their provider (id "ollama"/"custom");
      // user nodes are their own "fleet-<id>" provider — same addressing
      // as the rows' EnvelopeRowControls (the v1.201 defect pin).
      add(
        n.source === "config" ? n.id : `fleet-${n.id}`,
        n.default_model,
        n.label || n.id,
      );
    }
    for (const r of qualityRows) {
      if (r.task_class == null && r.model) add(r.provider, r.model, r.provider);
    }
    if (health?.default_provider && health.default_model) {
      add(health.default_provider, health.default_model, health.default_provider);
    }
    return out;
  })();

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
                      <HardDrive size={14} className="text-tone-info" />
                      <span className="flex-1">Cloud memory drives</span>
                      <ChevronRight size={13} className="text-zinc-600" />
                    </Link>
                    <Link
                      href="/tools"
                      role="menuitem"
                      onClick={() => setMenuOpen(false)}
                      className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-xs text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
                    >
                      <Wrench size={14} className="text-tone-warn" />
                      <span className="flex-1">Extensions (MCP)</span>
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

      {readyNow.length > 0 && (
        <Reveal>
          <div
            data-testid="connections-ready-banner"
            className="flex flex-wrap items-start gap-x-3 gap-y-1.5 rounded-xl border border-tone-success/20 bg-tone-success/[0.05] px-4 py-3 text-[13px] text-zinc-300"
          >
            <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-tone-success" aria-hidden />
            {/* basis-64: on a phone the link wraps below the sentence
                instead of squeezing it into a one-word column. */}
            <span className="min-w-0 flex-1 basis-64">
              <span className="font-medium text-zinc-100">Ready now, no key needed:</span>{" "}
              {readyNow
                .map(
                  (info) =>
                    // providerDisplay knows the common CLIs ("Claude Code");
                    // a row it does not know keeps the row's own name, never
                    // a raw id like "opencode-cli".
                    `${providerDisplay(info.provider) !== info.provider ? providerDisplay(info.provider) : info.name} (${info.description.charAt(0).toLowerCase()}${info.description.slice(1)})`,
                )
                .join(", ")}
              . Choose {readyNow.length === 1 ? "it" : "one"} in any model picker.
            </span>
            <a
              href="#subscription-providers"
              className="ml-7 inline-flex shrink-0 items-center gap-1 text-xs font-medium text-accent-soft hover:text-accent sm:ml-0 sm:mt-0.5"
            >
              See subscription &amp; local providers <ArrowRight size={12} aria-hidden />
            </a>
          </div>
        </Reveal>
      )}

      {loading && !data ? (
        <Reveal>
          <Card>
            <SkeletonRows rows={4} />
          </Card>
        </Reveal>
      ) : (
        grouped.map((group) => (
          <Reveal key={group.key}>
            <section className="space-y-3.5" aria-labelledby={`conn-group-${group.key}`}>
              {/* The Directory's section header (accent bar + uppercase h2). */}
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span
                  aria-hidden="true"
                  className="h-4 w-1 rounded-full bg-gradient-to-b from-accent to-accent/20 shadow-[0_0_8px_rgb(var(--accent-rgb)/0.4)]"
                />
                <h2
                  id={`conn-group-${group.key}`}
                  className="text-[12px] font-semibold uppercase tracking-[0.14em] text-zinc-300"
                >
                  {group.title}
                </h2>
                <span className="text-[11px] text-zinc-500">{group.hint}</span>
                <span
                  aria-hidden="true"
                  className="h-px min-w-[2rem] flex-1 bg-gradient-to-r from-white/[0.08] to-transparent"
                />
              </div>
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {group.items.map((conn) => (
                  <ConnectionCard
                    key={conn.provider}
                    conn={conn}
                    onChanged={reload}
                    id={`conn-card-${conn.provider}`}
                    quality={qualityRows}
                  />
                ))}
              </div>
            </section>
          </Reveal>
        ))
      )}

      {/* Measured endpoints (v1.204.0): the capability measurements, BELOW
          the connect cards as their own section. Renders nothing at all when
          no endpoint has a stored profile — deliberately not wrapped in
          Reveal so an empty section leaves no husk in the layout. */}
      <MeasuredEndpoints entries={measuredEntries} />

      {/* v1.316.0: the anchor the "Ready now" line links to. The card keeps
          its place (the measured-endpoints order is unchanged). */}
      <Reveal>
        <div id="subscription-providers" className="scroll-mt-24">
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
                signedOut={isSignedOut(info.provider)}
                report={qualityRows}
                envelopeModels={envelopeModelsFor(info.provider)}
              />
            ))}
          </div>
          <p className="mt-3 text-[11px] leading-relaxed text-zinc-600">
            These use plans you already pay for — no API keys. Pick them in any model picker.
          </p>
        </Card>
        </div>
      </Reveal>

      {/* Iron-Proxy accounts (v1.301.0): renders nothing on an older daemon,
          so it is not wrapped in Reveal (no empty husk). */}
      <IronProxyCard />

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
