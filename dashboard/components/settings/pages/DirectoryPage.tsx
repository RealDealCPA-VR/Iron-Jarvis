"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Brain,
  Search,
  Plug,
  Check,
  CheckCircle2,
  Radio,
  ExternalLink,
  Sparkles,
  ArrowRight,
  FolderInput,
  Info,
  KeyRound,
  X,
} from "lucide-react";
import { get, post, del, patch, ApiError } from "@/lib/api";
import { useApi, usePolledApi } from "@/lib/useApi";
import {
  Badge,
  OfflineHint,
  Empty,
  Skeleton,
  ErrorNote,
  SuccessNote,
  LoaderInline,
  ConfirmButton,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { BrandGlyph } from "@/components/BrandGlyph";
import { SourcesDoor } from "@/components/tools/AddPack";
import { DOOR_LABELS } from "@/lib/addPackWords";
import type { ImportSource } from "@/lib/mcpImport";
import { discover, discoveryLine } from "@/lib/importDiscovery";
import { EditPack, type EditSaved } from "@/components/tools/EditPack";
import { PackSwitch } from "@/components/tools/PackSwitch";
import { packStartReason } from "@/lib/packStartWords";
import { packPowersLine } from "@/lib/packPowers";
import { AppSignIn, ChoiceField, type AppSigninRow, type FieldOption } from "@/components/connections/AppSignIn";
import {
  PACK_OFF_LINE,
  PACK_OFF_WORD,
  isPackOff,
  packSwitchLabel,
  packSwitchTitle,
  savedWhileOff,
  switchedNote,
  type SwitchAnswer,
} from "@/lib/extensionWords";
import { attentionWords, cardHealth, healthCounts, keySaveNote } from "@/lib/directoryHealth";

/* -------------------------------------------------------------------------- */
/*  Types (local — lib/types.ts is intentionally untouched)                    */
/* -------------------------------------------------------------------------- */

/** v1.353.0 (G43): "link" = a card whose one action opens the page that sets
 *  it up (the browser add-on); it is never connected here. */
type ConnectVia = "mcp" | "oauth" | "api_key" | "memory" | "link";
type FieldKind = "secret" | "env" | "arg";

/** One value the user supplies to connect an MCP connector. */
interface ConnectorField {
  name: string;
  label: string;
  help: string;
  kind: FieldKind;
  optional: boolean;
  /** v1.356.0 (G60): a field answered from a list (the daemon sends
   *  `options` only for one) - drawn as a plain choice. */
  options?: FieldOption[];
}

/** One entry of GET /connectors — catalog data merged with live status. */
interface Connector {
  id: string;
  name: string;
  category: string;
  glyph: string;
  blurb: string;
  unlocks: string;
  connect_via: ConnectVia;
  scopes: string[];
  docs_url: string;
  fields: ConnectorField[];
  provider: string;
  connected: boolean;
  status: string;
  tools_loaded: number;
  tool_names?: string[];
  account?: string;
  /** The backend's words for a `no_tools` / `failed` status (the pack's
   *  plain cause); "" when there is nothing to say. */
  detail?: string | null;
  /** v1.342.0: the pack's load record, when the backend carries it. */
  reason?: string | null;
  fix?: string | null;
  stderr_tail?: string | null;
  /** v1.353.0 (G45): a one-time setup it needs outside Iron Jarvis ("" none;
   *  an OAuth app's is "" once its OAuth client is registered). */
  needs?: string | null;
  /** v1.353.0 (G45): would this PC start the pack's helper program now?
   *  `false` = say "Needs <runtime>" before Connect. Absent = unknown. */
  runtime_ready?: boolean | null;
  runtime?: string | null;
  runtime_url?: string | null;
  /** v1.353.0 (G45): an OAuth drive's sign-in is not yet a memory base. */
  memory_source?: boolean | null;
  /** v1.353.0 (G43): a word about the package itself ("older package"). */
  package_note?: string | null;
  /** v1.353.0 (G43): a LINK card's one action. */
  href?: string | null;
  action?: string | null;
  /** v1.355.0 (G70): what a connected app's loaded tools can do — how many
   *  can change things, whether they ask first, how many arrived since it was
   *  connected (they ask until trusted). Absent on an older daemon. */
  can_change?: number | null;
  asks_first?: boolean | null;
  held_back?: number | null;
  /** v1.355.0 (G69): the app holds a key or token the card can change. */
  keyed?: boolean | null;
  /** v1.356.0 (G60): an app that signs in on this PC - what its Sign in
   *  press opens (null until it is saved). Absent on every other card. */
  signin?: AppSigninRow | null;
  /** v1.356.0 (G78): false = the user switched this app's pack off (kept
   *  with its key, never loaded). The daemon's connectors rows carry it
   *  (the pack's `mcp_servers` entry `enabled`); absent = on. */
  enabled?: boolean | null;
  /** v1.358.0 (lane 8D): a SEPARATE version of something built in, in the
   *  built-in line's words ("Files & documents"), and how it differs. Such
   *  a card lives under its own fold, never beside the apps. */
  built_in?: string | null;
  built_in_note?: string | null;
  /** v1.358.0 (lane 8D): an app that signs in on this PC — true once its own
   *  sign-in check (or a successful call) says signed in. */
  signed_in?: boolean | null;
  /** v1.358.0 (lane 8D): a key card's check said what (`technical` = the
   *  check's own text, shown only on hover). */
  checked?: boolean | null;
  technical?: string | null;
}

interface ConnectorsResponse {
  connectors: Connector[];
  categories: string[];
}

/* --- POST /connectors/{id}/connect response variants --------------------- */
interface McpConnectResult {
  ok: boolean;
  connector: string;
  tools_loaded: number;
  note: string | null;
}
interface OAuthConnectResult {
  ok: boolean;
  connector: string;
  oauth?: { authorization_url: string; state: string };
}
/** v1.358.0 (lane 8D): a key card's save answers the check run on save. */
interface KeyConnectResult {
  ok: boolean;
  connector: string;
  test?: { ok?: boolean; detail?: string } | null;
}

/* --- POST /connectors/{id}/test response variants ------------------------ */
interface McpTestResult {
  ok: boolean;
  count: number;
  tools: string[];
  error: string | null;
  /** v1.342.0: the real load error's plain cause, next step and last output. */
  reason?: string | null;
  fix?: string | null;
  stderr_tail?: string | null;
}

/** Response of POST /mcp/servers/{name}/reload: the Retry for an MCP
 *  connector that did not start (a connector id IS its mcp_servers name). */
interface McpReloadResult {
  ok: boolean;
  tools_loaded: number;
  last_error: string | null;
  reason?: string | null;
  fix?: string | null;
  stderr_tail?: string | null;
}

/** v1.342.0: an MCP pack's last output, folded; monospace, wrapped, twelve
 *  lines (192px) visible with its own scroll. Nothing when empty. */
function LastOutput({ tail, testId }: { tail: string | null | undefined; testId: string }) {
  const text = (tail ?? "").trim();
  if (!text) return null;
  return (
    <details data-testid={testId} className="w-full">
      <summary className="cursor-pointer select-none text-[11px] font-medium text-zinc-500 transition-colors hover:text-zinc-300">
        Last output
      </summary>
      <pre className="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap break-all rounded-lg border border-white/[0.06] bg-ink-900/60 px-2 py-1 font-mono text-[11px] leading-4 text-zinc-400">
        {text}
      </pre>
    </details>
  );
}
interface ConnTestResult {
  ok: boolean;
  detail: string;
}
/** Memory sources (LTM brains) test to a bare ok + optional error. */
interface MemTestResult {
  ok: boolean;
  kind?: string;
  error?: string | null;
}
type TestResult = McpTestResult | ConnTestResult | MemTestResult;

/* -------------------------------------------------------------------------- */
/*  Per-`connect_via` presentation                                             */
/* -------------------------------------------------------------------------- */

/**
 * v1.353.0 (G42): HOW a card connects, in a tooltip on its category line. It
 * used to be a tag on every card ("MCP" / "OAUTH" / "API KEY") — the
 * mechanism, in capitals, on a page for people who just want their apps.
 * An unknown value from a newer daemon reads as nothing.
 */
const VIA_TITLE: Record<ConnectVia, string> = {
  mcp: "Runs a small helper program on this PC",
  oauth: "Connects by signing in to your account",
  api_key: "Connects with a key from the service",
  memory: "A memory base",
  link: "Set up on its own page",
};

/** Where "choose a folder for memory" goes: the Memory page's long-term tab,
 *  at its "Add a memory base" control (both params, as the palette's). */
const ADD_MEMORY_BASE = "/memory?scope=longterm&focus=add-base";

/** The OAuth drives whose files reach chat only through a memory base. */
const MEMORY_DRIVES: ReadonlySet<string> = new Set(["google_drive", "onedrive", "dropbox"]);

/**
 * Settings › Connections › Notifications, by its old address (lib/relocated
 * redirects it there). This page is itself a Settings section, and a link to
 * another section's own address from inside Settings only changes the URL:
 * SettingsHome reads its section once, on mount, and the App Router keeps a
 * page mounted across a search-param change. The old address goes through
 * another route, so Settings mounts again on the right section (the same
 * reason the cards' "/connections" and "/tools" links work).
 */
const NOTIFICATIONS = "/channels";

/**
 * v1.353.0 (G45): one sentence that ties an app to the OTHER place people
 * look for it — the Gmail app is not how Jarvis emails you, the Slack app is
 * not how Jarvis messages you, and Notion reaches memory as a memory base.
 * Keyed by card id, like TOOLS_TWINS below.
 */
const ALSO: Readonly<Record<string, { lead: string; label: string; href: string }>> = {
  gmail: {
    lead: "To have Jarvis email you, use",
    label: "Notifications › Email",
    href: NOTIFICATIONS,
  },
  slack: {
    lead: "To have Jarvis message you in Slack, use",
    label: "Notifications › Slack",
    href: NOTIFICATIONS,
  },
  notion: {
    lead: "To make your Notion pages part of memory, add",
    label: "a memory base",
    href: ADD_MEMORY_BASE,
  },
};

/** A muted amber "heads up" note — used for the honest MCP restart note. */
function InfoNote({ children }: { children: React.ReactNode }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-start gap-2.5 rounded-xl border border-amber-500/25 bg-amber-500/[0.07] px-3 py-2.5 text-sm text-amber-100/90"
    >
      <Info size={16} className="mt-0.5 shrink-0 text-amber-300" aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  One connector card                                                         */
/* -------------------------------------------------------------------------- */

function ConnectorCard({ c, onChanged }: { c: Connector; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [oauthPending, setOauthPending] = useState(false);
  const [needsApp, setNeedsApp] = useState<string | null>(null);

  const [testBusy, setTestBusy] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);

  const requiredFields = c.fields.filter((f) => !f.optional);
  const canConnect =
    c.connect_via === "oauth"
      ? true
      : c.connect_via === "api_key"
        ? (values.key ?? "").trim().length > 0
        : requiredFields.every((f) => (values[f.name] ?? "").trim().length > 0);

  function clearNotes() {
    setError(null);
    setNote(null);
    setSuccess(null);
    setNeedsApp(null);
    setOauthPending(false);
  }

  const connectPath = `/connectors/${encodeURIComponent(c.id)}/connect`;

  async function connect(e?: React.FormEvent) {
    e?.preventDefault();
    if (!canConnect || busy) return;
    setBusy(true);
    clearNotes();
    setTestResult(null);
    try {
      if (c.connect_via === "api_key") {
        // v1.358.0 (lane 8D): the daemon CHECKS the key on save (the
        // Connections page's own check) and says what it answered; a key it
        // refused is saved but said so, never "connected" on its own word.
        const res = await post<KeyConnectResult>(connectPath, {
          values: { key: (values.key ?? "").trim() },
        });
        const said = keySaveNote(c.name, res?.test);
        if (said.ok) setSuccess(said.text);
        else setNote(said.text);
        setOpen(false);
        setValues({});
        onChanged();
      } else if (c.connect_via === "mcp") {
        const payload: Record<string, string> = {};
        for (const f of c.fields) {
          const v = (values[f.name] ?? "").trim();
          if (v) payload[f.name] = v;
        }
        const res = await post<McpConnectResult>(connectPath, { values: payload });
        if (res.tools_loaded > 0) {
          setSuccess(
            `Connected — ${res.tools_loaded} tool${res.tools_loaded === 1 ? "" : "s"} ready to use.`,
          );
        } else {
          // v1.351.0 (G08): the card's Retry loads it; no restart needed.
          setNote(res.note ?? "Saved, but it didn't start. Press Retry.");
        }
        setOpen(false);
        setValues({});
        onChanged();
      } else {
        // oauth
        const res = await post<OAuthConnectResult>(connectPath, { values: {} });
        if (res.oauth?.authorization_url) {
          window.open(res.oauth.authorization_url, "_blank", "noopener,noreferrer");
          setOauthPending(true);
        }
        onChanged();
      }
    } catch (err) {
      // OAuth connectors that need a user-registered app fail 422/400 with a
      // helpful detail — surface it plus a route to the Connections page.
      if (
        c.connect_via === "oauth" &&
        err instanceof ApiError &&
        (err.status === 422 || err.status === 400)
      ) {
        setNeedsApp(err.message);
      } else {
        setError(err instanceof ApiError ? err.message : String(err));
      }
    } finally {
      setBusy(false);
    }
  }

  async function runTest() {
    if (testBusy) return;
    setTestBusy(true);
    setError(null);
    setTestResult(null);
    try {
      const res = await post<TestResult>(`/connectors/${encodeURIComponent(c.id)}/test`);
      setTestResult(res);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setTestBusy(false);
    }
  }

  /** v1.342.0: Retry an MCP connector whose pack did not start. The connector
   *  id is the pack's mcp_servers name, so this is the Tools page's own
   *  reload; the card re-reads the gallery afterwards so the status line
   *  tells the new truth (the words come from the daemon, never guessed). */
  const [retryBusy, setRetryBusy] = useState(false);
  async function retry() {
    if (retryBusy) return;
    setRetryBusy(true);
    setError(null);
    setSuccess(null);
    setNote(null);
    setTestResult(null);
    try {
      const res = await post<McpReloadResult>(
        `/mcp/servers/${encodeURIComponent(c.id)}/reload`,
      );
      if (res.ok && res.tools_loaded > 0) {
        setSuccess(
          `Connected, ${res.tools_loaded} tool${res.tools_loaded === 1 ? "" : "s"} ready to use.`,
        );
      } else {
        setTestResult({
          ok: false,
          count: res.tools_loaded ?? 0,
          tools: [],
          error: res.last_error ?? res.reason ?? "It did not start.",
          reason: res.reason ?? null,
          fix: res.fix ?? null,
          stderr_tail: res.stderr_tail ?? null,
        });
      }
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setRetryBusy(false);
    }
  }

  /** v1.355.0 (G69): Change key — the Tools page's own editor (EditPack,
   *  PATCH /mcp/servers/{name}) for THIS app, opened on its card. A token that
   *  expired is replaced in place: nothing is disconnected, the app reloads
   *  with the new one. The editor is seeded from the app's GET /mcp/servers
   *  row (key NAMES only, never a value). */
  const [editRow, setEditRow] = useState<Record<string, unknown> | null>(null);
  const [keyBusy, setKeyBusy] = useState(false);
  async function openChangeKey() {
    if (keyBusy) return;
    if (editRow) {
      setEditRow(null);
      return;
    }
    setKeyBusy(true);
    clearNotes();
    setTestResult(null);
    try {
      const res = await get<{ servers?: Record<string, unknown>[] }>("/mcp/servers");
      const row = (res?.servers ?? []).find((s) => s && s.name === c.id) ?? null;
      if (row) setEditRow(row);
      else setError(`${c.name} isn't saved yet. Press Connect.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setKeyBusy(false);
    }
  }
  function onKeyChanged(res: EditSaved) {
    setEditRow(null);
    // v1.356.0 (G78): a key changed on an app that is switched off is saved
    // and stays off — the daemon loads nothing behind the switch.
    if (isPackOff(res as { enabled?: boolean | null; state?: string | null })) {
      setSuccess(savedWhileOff(c.name));
      onChanged();
      return;
    }
    const loaded = typeof res.tools_loaded === "number" ? res.tools_loaded : 0;
    if (res.reloaded) {
      setSuccess(`Key changed. ${loaded} tool${loaded === 1 ? "" : "s"} ready to use.`);
    } else {
      // The card's Retry (on its status line) loads it again; the reason is
      // the daemon's own words, said once.
      const reason = packStartReason(res.note);
      setNote(`Key saved, but ${c.name} didn't start${reason ? `: ${reason}` : ""}. Press Retry.`);
    }
    onChanged();
  }

  /** v1.356.0 (G78): On / Off without losing it — the Tools row's switch
   *  (PATCH /mcp/servers/{name} {enabled}; the card id IS the pack's
   *  mcp_servers name, as Retry and Change key use it). Off takes its tools
   *  out of every chat and agent and keeps its key; on loads it again with
   *  nothing typed. */
  const [switchBusy, setSwitchBusy] = useState(false);
  async function setOn(next: boolean) {
    if (switchBusy) return;
    setSwitchBusy(true);
    clearNotes();
    setTestResult(null);
    try {
      const res = await patch<SwitchAnswer>(`/mcp/servers/${encodeURIComponent(c.id)}`, { enabled: next });
      const said = switchedNote(c.name, next, res);
      if (said.warn) setNote(said.text);
      else setSuccess(said.text);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSwitchBusy(false);
    }
  }

  async function disconnect() {
    setError(null);
    setTestResult(null);
    try {
      await del(`/connectors/${encodeURIComponent(c.id)}`);
      clearNotes();
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  function toggleForm() {
    clearNotes();
    setTestResult(null);
    if (c.connect_via === "oauth") {
      void connect();
      return;
    }
    setOpen((v) => !v);
  }

  // v1.351.0 (G08): green ONLY when it works. `connected` stays true for a
  // saved pack that did not start (service._mcp_status: the connection is
  // real and survives restarts), so the green badge, ring and glyph read the
  // backend's `status` instead: "connected" is green; a saved connection in
  // any other state (failed, no_tools) "Needs attention", in the warn tone.
  // v1.356.0 (G78): a saved app the user switched off is neither working nor
  // broken: "Off", no Retry, its switch says how to turn it back on. Read off
  // the row's `enabled` (absent = on), whatever its status says.
  // v1.358.0 (lane 8D): ONE reading (lib/directoryHealth), the same the
  // page's "N connected" count and its connected-only filter use. A saved
  // app whose own sign-in has not landed yet (Microsoft 365) is its own
  // state: never green, the line says what to do and the Sign in press is
  // right under it.
  const health = cardHealth(c);
  const off = health === "off";
  const healthy = health === "working";
  const signinNeeded = health === "signin";
  const attention = health === "attention";
  // v1.353.0 (G45): the needs said before Connect.
  const setupNeeds = (c.needs ?? "").trim();
  const runtimeMissing = c.runtime_ready === false && Boolean((c.runtime ?? "").trim());
  // v1.353.0 (G45): a signed-in drive searches nothing until a memory base
  // points at one of its folders. `false` only when the daemon checked.
  const needsMemoryFolder =
    healthy && c.connect_via === "oauth" && MEMORY_DRIVES.has(c.provider) && c.memory_source === false;
  const also = ALSO[c.id];
  // v1.355.0 (G70): a connected app's card says what its tools can do; the
  // hand-written scopes stay for a card not connected yet.
  const powers = c.connect_via === "mcp" && healthy ? packPowersLine(c) : null;
  // v1.355.0 (G69): Change key, on a saved app that holds a key or token.
  const canChangeKey = c.connect_via === "mcp" && c.connected && c.keyed === true;

  return (
    <div
      data-testid={`connector-card-${c.id}`}
      data-health={healthy ? "connected" : signinNeeded ? "signin" : attention ? "attention" : "off"}
      data-switched-off={off ? "1" : undefined}
      className={`card-surface relative flex flex-col gap-3.5 p-5 transition-all duration-300 hover:-translate-y-0.5 hover:shadow-card-hover hover:ring-1 hover:ring-accent/20 ${
        healthy
          ? "ring-1 ring-emerald-500/25 shadow-[0_0_24px_-10px_rgba(52,211,153,0.35)]"
          : attention || signinNeeded
            ? "ring-1 ring-tone-warn/30"
            : ""
      }`}
    >
      {/* Header: glyph + name + status. v1.353.0 (G42): no mechanism tag;
          how it connects is the category line's tooltip. */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <BrandGlyph id={c.id} glyph={c.glyph} connected={healthy} />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h3 className="truncate text-sm font-semibold text-zinc-100">{c.name}</h3>
            </div>
            <p className="mt-0.5 truncate text-[11px] text-zinc-500" title={VIA_TITLE[c.connect_via]}>
              {c.category}
            </p>
          </div>
        </div>
        {healthy ? (
          <span className="shrink-0">
            <Badge value="Connected" tone="green" />
          </span>
        ) : signinNeeded ? (
          /* v1.358.0 (lane 8D): saved and loaded, but nobody signed in to
             its account yet — never the green "Connected". */
          <span className="shrink-0">
            <Badge
              value="Sign in to finish"
              tone="amber"
              keepCase
              data-testid={`connector-signin-needed-${c.id}`}
            />
          </span>
        ) : attention ? (
          <span className="shrink-0">
            <Badge
              value="Needs attention"
              tone="amber"
              keepCase
              data-testid={`connector-attention-${c.id}`}
            />
          </span>
        ) : off ? (
          <span className="shrink-0">
            <Badge
              value={PACK_OFF_WORD}
              tone="slate"
              variant="calm"
              keepCase
              title={PACK_OFF_LINE}
              data-testid={`connector-off-${c.id}`}
            />
          </span>
        ) : null}
      </div>

      {/* Blurb */}
      <p className="text-[13px] text-zinc-400">{c.blurb}</p>

      {/* v1.358.0 (lane 8D): a separate version of something built in says
          so, in the daemon's words, before anything else. */}
      {(c.built_in_note ?? "").trim() && (
        <p data-testid={`connector-built-in-note-${c.id}`} className="text-[11px] text-zinc-500">
          {c.built_in_note}
        </p>
      )}

      {/* v1.353.0 (G45): what it needs, said BEFORE Connect — a one-time
          setup outside Iron Jarvis (Gmail; a drive with no OAuth client yet)
          and the helper program this PC lacks ("Needs Node.js"). Words in
          the warn tone with a dot, never colour alone. */}
      {!c.connected && (setupNeeds || runtimeMissing) && (
        <div data-testid={`connector-needs-${c.id}`} className="flex flex-wrap gap-1.5">
          {setupNeeds && (
            <Badge
              value={setupNeeds}
              tone="amber"
              variant="calm"
              keepCase
              title={c.docs_url ? "The Docs link on this card shows how" : undefined}
              data-testid={`connector-needs-setup-${c.id}`}
            />
          )}
          {setupNeeds && c.connect_via === "oauth" && (
            /* The same place the after-the-press note points to. */
            <Link
              href="/connections"
              data-testid={`connector-needs-howto-${c.id}`}
              className="inline-flex items-center gap-1 text-[11px] text-zinc-400 transition-colors hover:text-accent-soft"
            >
              How to set it up <ArrowRight size={11} aria-hidden="true" />
            </Link>
          )}
          {runtimeMissing && (
            <Badge
              value={`Needs ${c.runtime}`}
              tone="amber"
              variant="calm"
              keepCase
              title={`${c.runtime} was not found on this PC.${c.runtime_url ? ` Get it free from ${c.runtime_url}` : ""}`}
              data-testid={`connector-needs-runtime-${c.id}`}
            />
          )}
        </div>
      )}

      {/* What it unlocks — the plain-English payoff */}
      <div className="flex items-start gap-2 rounded-xl border border-accent/15 bg-accent/[0.04] px-3 py-2.5">
        <Sparkles size={14} className="mt-0.5 shrink-0 text-accent-soft" aria-hidden="true" />
        <p className="text-[12.5px] leading-relaxed text-zinc-300">{c.unlocks}</p>
      </div>

      {/* Scopes / permissions. v1.355.0 (G70): a connected app says what its
          tools can actually do, read off the app itself. */}
      {powers ? (
        <p data-testid={`connector-powers-${c.id}`} className="text-[11px] text-zinc-400">
          {powers}
        </p>
      ) : c.scopes.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {c.scopes.map((s) => (
            <span
              key={s}
              className="rounded-md border border-white/[0.06] bg-white/[0.03] px-1.5 py-0.5 text-[11px] text-zinc-400"
            >
              {s}
            </span>
          ))}
        </div>
      )}

      {/* Connected detail (tools loaded / account) + loaded tool chips */}
      {c.connected && (
        <div className="space-y-2">
          {/* v1.342.0: an MCP connector says the backend's status in words.
              "0 tools loaded" was a count where the user needed a cause: the
              daemon knows whether the pack failed or loaded nothing, says
              why, and names the fix. Retry reloads the pack in place. */}
          {off ? (
            /* v1.356.0 (G78): what off means, and that nothing is lost. */
            <p data-testid={`connector-off-line-${c.id}`} className="text-[12px] text-zinc-400">
              {PACK_OFF_LINE}
            </p>
          ) : signinNeeded ? (
            /* v1.358.0 (lane 8D): the daemon's own words ("Sign in to
               Microsoft to finish."), right above the card's Sign in press. */
            <p data-testid={`connector-signin-line-${c.id}`} className="text-[12px] text-tone-warn">
              {(c.detail ?? "").trim() || "Sign in to finish."}
            </p>
          ) : c.connect_via === "api_key" && c.status === "failed" ? (
            /* v1.358.0 (lane 8D): a key checked and refused says so here
               (the check's own text on hover), never "Ready to use". */
            <p
              data-testid={`connector-key-failed-${c.id}`}
              title={(c.technical ?? "").trim() || undefined}
              className="text-[12px] text-tone-warn"
            >
              {(c.detail ?? "").trim() || "The key didn't work."}
            </p>
          ) : c.connect_via === "mcp" && (c.status === "no_tools" || c.status === "failed") ? (
            <div
              data-testid={`connector-status-${c.id}`}
              className="flex flex-wrap items-center gap-2 text-[12px] text-amber-200"
            >
              <span className="min-w-0 break-all">
                {c.status === "failed" ? "Didn’t start" : "No tools loaded"}
                {c.reason || c.detail ? `: ${c.reason || c.detail}` : ""}
              </span>
              <button
                type="button"
                data-testid={`connector-retry-${c.id}`}
                onClick={() => void retry()}
                disabled={retryBusy || testBusy}
                className="inline-flex items-center gap-1 rounded-md border border-amber-400/30 bg-amber-400/[0.08] px-2 py-0.5 text-[11px] font-medium text-amber-100 transition-colors hover:bg-amber-400/[0.14] disabled:opacity-50"
              >
                {retryBusy ? "Retrying…" : "Retry"}
              </button>
              {c.fix && (
                <span
                  data-testid={`connector-fix-${c.id}`}
                  className="w-full text-[11px] text-amber-100/80"
                >
                  {c.fix}
                </span>
              )}
              <LastOutput tail={c.stderr_tail} testId={`connector-stderr-${c.id}`} />
            </div>
          ) : needsMemoryFolder ? (
            /* v1.353.0 (G45): "Ready to use" over a drive nothing searched. */
            <p
              data-testid={`connector-memory-next-${c.id}`}
              className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-zinc-300"
            >
              <span>
                {c.account ? `Signed in as ${c.account}. ` : "Connected. "}Now choose a folder for memory.
              </span>
              <Link
                href={ADD_MEMORY_BASE}
                className="inline-flex items-center gap-1 font-medium text-accent-soft transition-colors hover:text-accent"
              >
                Choose a folder <ArrowRight size={12} aria-hidden="true" />
              </Link>
            </p>
          ) : (
            <p className="text-[11px] text-zinc-500">
              {c.connect_via === "mcp"
                ? `${c.tools_loaded} tool${c.tools_loaded === 1 ? "" : "s"} loaded`
                : c.connect_via === "memory"
                  ? "Grounds chat & agents — toggle it on in the chat + menu"
                  : c.account
                    ? `Signed in as ${c.account}`
                    : "Ready to use"}
            </p>
          )}
          {/* v1.351.0 (G08): where a working pack is used, said on the card
              (it was only a tooltip on the page's Back to chat door). */}
          {c.connect_via === "mcp" && healthy && (
            <p data-testid={`connector-chat-hint-${c.id}`} className="text-[11px] text-zinc-500">
              Turn it on in a chat with + › Connections
            </p>
          )}
          {/* v1.356.0 (G60): an app that signs in on this PC (Microsoft 365):
              its Sign in opens a Build pane running the app's own sign-in,
              which shows a web address and a code. */}
          {c.connect_via === "mcp" && c.signin && !off && (
            <AppSignIn
              id={c.id}
              signin={c.signin}
              signedIn={c.signed_in === true}
              account={c.account ?? ""}
            />
          )}
          {/* v1.358.0 (lane 8D): no tool list while its sign-in is still to
              finish — every one of them would fail until then. */}
          {c.connect_via === "mcp" && !off && !signinNeeded && (c.tool_names?.length ?? 0) > 0 && (
            <div className="flex flex-wrap gap-1">
              {c.tool_names!.map((n) => (
                <span
                  key={n}
                  className="rounded-md border border-white/[0.06] bg-white/[0.03] px-1.5 py-0.5 font-mono text-[11px] text-zinc-300"
                >
                  {n}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* v1.353.0 (G43): an honest word about the package itself. */}
      {c.package_note && (
        <p data-testid={`connector-package-note-${c.id}`} className="text-[11px] text-zinc-500">
          {c.package_note}
        </p>
      )}

      {/* v1.353.0 (G45): the OTHER place people look for this app. */}
      {also && (
        <p data-testid={`connector-also-${c.id}`} className="text-[11px] text-zinc-500">
          {also.lead}{" "}
          <Link href={also.href} className="text-zinc-400 underline-offset-2 transition-colors hover:text-accent-soft hover:underline">
            {also.label}
          </Link>
          .
        </p>
      )}

      {/* Docs link */}
      {c.docs_url && (
        <a
          href={c.docs_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex w-fit items-center gap-1 text-[11px] text-zinc-500 transition-colors hover:text-accent-soft"
        >
          Docs <ExternalLink size={11} />
        </a>
      )}

      {/* v1.316.0 (three-overlapping-catalogs): the SAME pack on the Tools
          page — only where both write one mcp_servers entry under this id. */}
      {TOOLS_TWINS.has(c.id) && (
        <Link
          href="/tools"
          className="inline-flex w-fit items-center gap-1 text-[11px] text-zinc-500 transition-colors hover:text-accent-soft"
          title="Tools lists this same extension with how it starts — adding it in either place is the same thing"
        >
          Also in Tools — advanced setup <ArrowRight size={11} aria-hidden="true" />
        </Link>
      )}

      {/* Spacer pushes actions to the bottom for even card heights */}
      <div className="flex-1" />

      {/* Action row */}
      {c.connected ? (
        <div className="flex items-center gap-2">
          {/* v1.356.0 (G78): On / Off for an app that runs as a pack. */}
          {c.connect_via === "mcp" && (
            <PackSwitch
              on={!off}
              label={packSwitchLabel(c.name)}
              title={packSwitchTitle(!off)}
              busy={switchBusy}
              testId={`connector-switch-${c.id}`}
              onChange={(next) => void setOn(next)}
            />
          )}
          <button
            type="button"
            onClick={runTest}
            disabled={testBusy}
            className="btn-ghost flex-1 py-1.5 text-xs"
          >
            {testBusy ? (
              <LoaderInline label="Testing…" />
            ) : (
              <>
                <CheckCircle2 size={14} /> Test
              </>
            )}
          </button>
          {canChangeKey && (
            <button
              type="button"
              data-testid={`connector-change-key-${c.id}`}
              onClick={() => void openChangeKey()}
              disabled={keyBusy}
              aria-expanded={editRow !== null}
              className="btn-ghost flex-1 py-1.5 text-xs"
              title={`Paste a new key or token for ${c.name}; it stays connected`}
            >
              {keyBusy ? (
                <LoaderInline label="Opening…" />
              ) : (
                <>
                  <KeyRound size={14} /> Change key
                </>
              )}
            </button>
          )}
          {/* v1.356.0 (G74): one word for taking an app away, the same as
              the Tools page: Remove (it said Disconnect here, Delete there).
              To pause it instead, the switch keeps its key. */}
          <ConfirmButton
            onConfirm={disconnect}
            label="Remove"
            title={
              c.connect_via === "mcp"
                ? `Remove ${c.name} and its saved key (to pause it instead, switch it off)`
                : `Remove ${c.name}`
            }
            className="py-1.5"
          />
        </div>
      ) : c.connect_via === "memory" ? (
        // Memory sources are added on the Memory page, not connected here.
        <Link href="/memory" className="btn-ghost w-full py-1.5 text-center text-xs">
          <Brain size={14} /> Manage on the Memory page
        </Link>
      ) : c.connect_via === "link" ? (
        // v1.353.0 (G43): a card that is set up on its own page (the browser
        // add-on, where Puppeteer used to be offered as an app).
        <Link
          href={c.href || "/settings"}
          data-testid={`connector-link-${c.id}`}
          className="btn-ghost w-full py-1.5 text-center text-xs"
        >
          {c.action || "Open its settings"} <ArrowRight size={14} aria-hidden="true" />
        </Link>
      ) : (
        <div className="space-y-2.5">
          {!open ? (
            /* v1.316.0 (UX wave 4): outlined, not filled — nineteen equal
               cyan bars gave the eye nowhere to land. The accent lives in
               the icon; the press is unchanged. */
            <button
              type="button"
              onClick={toggleForm}
              disabled={busy}
              className="btn-ghost w-full py-1.5 text-xs"
            >
              {busy && c.connect_via === "oauth" ? (
                <LoaderInline label="Starting…" />
              ) : (
                <>
                  <Plug size={14} className="text-accent-soft" /> Connect
                </>
              )}
            </button>
          ) : (
            <form onSubmit={connect} className="space-y-2.5">
              {c.connect_via === "api_key" ? (
                <label className="block space-y-1">
                  <span className="text-[11px] font-medium text-zinc-400">API key</span>
                  <input
                    type="password"
                    value={values.key ?? ""}
                    onChange={(e) => setValues((v) => ({ ...v, key: e.target.value }))}
                    placeholder="Paste your API key"
                    aria-label={`${c.name} API key`}
                    autoComplete="off"
                    autoFocus
                    className="field font-mono text-xs"
                  />
                  <span className="block text-[11px] leading-relaxed text-zinc-500">
                    Stored encrypted — never shown again.
                  </span>
                </label>
              ) : (
                c.fields.map((f, i) => f.options?.length ? (
                  /* v1.356.0 (G60): a field answered from a list (Microsoft
                     365's account kind): components/connections/AppSignIn. */
                  <ChoiceField
                    key={f.name}
                    name={f.name}
                    label={f.label}
                    help={f.help}
                    options={f.options}
                    optional={f.optional}
                    value={values[f.name] ?? ""}
                    autoFocus={i === 0}
                    onChange={(v) => setValues((cur) => ({ ...cur, [f.name]: v }))}
                  />
                ) : (
                  <label key={f.name} className="block space-y-1">
                    <span className="text-[11px] font-medium text-zinc-400">
                      {f.label}
                      {!f.optional && <span className="ml-0.5 text-rose-300">*</span>}
                      {f.optional && <span className="ml-1 text-zinc-600">(optional)</span>}
                    </span>
                    <input
                      type={f.kind === "secret" ? "password" : "text"}
                      value={values[f.name] ?? ""}
                      onChange={(e) =>
                        setValues((v) => ({ ...v, [f.name]: e.target.value }))
                      }
                      placeholder={f.kind === "secret" ? "••••••••" : f.label}
                      aria-label={f.label}
                      autoComplete="off"
                      autoFocus={i === 0}
                      className="field font-mono text-xs"
                    />
                    {f.help && (
                      <span className="block text-[11px] leading-relaxed text-zinc-500">
                        {f.help}
                      </span>
                    )}
                  </label>
                ))
              )}
              <div className="flex items-center gap-2">
                <button
                  type="submit"
                  disabled={busy || !canConnect}
                  className="btn-accent flex-1 py-1.5 text-xs"
                >
                  {busy ? (
                    <LoaderInline label="Connecting…" />
                  ) : (
                    <>
                      <Plug size={14} /> Connect
                    </>
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setOpen(false);
                    setValues({});
                    setError(null);
                  }}
                  className="btn-ghost py-1.5 text-xs"
                >
                  <X size={14} /> Cancel
                </button>
              </div>
            </form>
          )}

          {/* OAuth in-progress hint */}
          {oauthPending && (
            <InfoNote>
              Finish signing in — the card updates when it&apos;s connected.
            </InfoNote>
          )}

          {/* OAuth needs a registered app (Drive/OneDrive/Dropbox) */}
          {needsApp && (
            <div className="rounded-xl border border-amber-500/25 bg-amber-500/[0.07] px-3 py-2.5 text-[12px] leading-relaxed text-amber-100/90">
              <div className="flex items-start gap-2.5">
                <Info size={16} className="mt-0.5 shrink-0 text-amber-300" aria-hidden="true" />
                <span>{needsApp}</span>
              </div>
              <Link
                href="/connections"
                className="mt-2 inline-flex items-center gap-1 font-medium text-accent-soft underline underline-offset-2 hover:text-accent"
              >
                Set it up on the Connections page <ArrowRight size={12} />
              </Link>
            </div>
          )}
        </div>
      )}

      {/* v1.355.0 (G69): the Tools page's own editor, for this app. */}
      {editRow && (
        <EditPack
          key={c.id}
          row={editRow}
          onSaved={onKeyChanged}
          onCancel={() => setEditRow(null)}
        />
      )}

      {/* Feedback: connect note / success / error */}
      {note && <InfoNote>{note}</InfoNote>}
      {success && <SuccessNote>{success}</SuccessNote>}
      {error && <ErrorNote>{error}</ErrorNote>}

      {/* Test result — mcp (tools) vs connection (detail) vs memory (bare ok) */}
      {testResult &&
        ("detail" in testResult ? (
          testResult.ok ? (
            <SuccessNote>{testResult.detail}</SuccessNote>
          ) : (
            <ErrorNote>{testResult.detail}</ErrorNote>
          )
        ) : "count" in testResult ? (
          testResult.ok ? (
            <div className="rounded-xl border border-emerald-500/25 bg-emerald-500/[0.06] px-3 py-2.5">
              <div className="flex items-center gap-1.5 text-[12px] font-medium text-emerald-300">
                <Check size={13} /> Connected — {testResult.count} tool
                {testResult.count === 1 ? "" : "s"} available now
              </div>
              {testResult.tools.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {testResult.tools.map((n) => (
                    <span
                      key={n}
                      className="rounded-md border border-emerald-500/20 bg-emerald-500/[0.08] px-1.5 py-0.5 font-mono text-[11px] text-emerald-200"
                    >
                      {n}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ) : (
            // v1.342.0: a failed Test says the real load error, the plain
            // cause, the next step and the pack's last output.
            <div data-testid={`connector-test-fail-${c.id}`} className="space-y-1.5">
              <ErrorNote>{testResult.error || testResult.reason || "Test failed."}</ErrorNote>
              {testResult.reason && testResult.reason !== testResult.error && (
                <p className="text-[12px] text-amber-200">{testResult.reason}</p>
              )}
              {testResult.fix && (
                <p className="text-[11px] text-amber-100/80">{testResult.fix}</p>
              )}
              <LastOutput
                tail={testResult.stderr_tail}
                testId={`connector-test-stderr-${c.id}`}
              />
            </div>
          )
        ) : testResult.ok ? (
          <SuccessNote>Memory base is live and searchable.</SuccessNote>
        ) : (
          <ErrorNote>{testResult.error ?? "Test failed."}</ErrorNote>
        ))}
    </div>
  );
}

/**
 * v1.316.0 (three-overlapping-catalogs): Directory connectors the Tools page
 * ALSO adds under the same id — both write ONE `mcp_servers` entry named by
 * it, so they are the same pack, never a second copy. Exact twins only:
 * the browser card is not Tools' Playwright, and `sequential_thinking` is not
 * Tools' `sequentialthinking` (since v1.353.0 the daemon lights the Sequential
 * Thinking card from that pack — `connectors.service.PACK_ALIASES` — and the
 * row then carries the pack's own name as its id). Since v1.357.0 the Tools
 * page reads each row's `directory_twin` off the one catalog file
 * (`connectors/catalog.tools_twin`); `tests/test_one_catalog_v13570.py` keeps
 * this set equal to it.
 */
const TOOLS_TWINS: ReadonlySet<string> = new Set([
  "github",
  "filesystem",
  "fetch",
  "memory",
  "box",
]);

/* -------------------------------------------------------------------------- */
/*  v1.353.0 (G42): one Connections home                                       */
/* -------------------------------------------------------------------------- */

/**
 * "Import from your other apps", where people come to connect things: one
 * line about what Claude, Codex, Cursor and VS Code already use (read ONCE,
 * `GET /mcp/import/sources` — the Tools page's door reads the same answer),
 * and an Import button that opens that SAME door here. Nothing is added
 * without a press: the door lists the tools, the user picks, Add selected.
 */
function ImportFromApps({ onImported }: { onImported: () => void }) {
  const { data, error, loading, reload } = useApi<{ sources?: ImportSource[] }>(
    "/mcp/import/sources",
  );
  const [open, setOpen] = useState(false);
  const sources = useMemo(
    () => (data ? (Array.isArray(data.sources) ? data.sources : []) : null),
    [data],
  );
  const found = useMemo(() => discover(sources), [sources]);
  const line = discoveryLine(found);
  // Nothing to say while the first read is out, after a failed read, or on a
  // daemon too old to answer (the page's own offline note speaks for that).
  if (!data) {
    if (!loading || error) return null;
    return (
      <p data-testid="directory-import" className="flex items-center gap-2 text-[13px] text-zinc-500">
        <FolderInput size={14} aria-hidden="true" />
        <LoaderInline label="Looking in your other apps" />
      </p>
    );
  }
  if (!line) return null;
  const canOpen = found.found > 0;
  return (
    // v1.354.0 integrate: the SAME door as the Tools page's add area, so it
    // is named with that door's own words (lib/addPackWords DOOR_LABELS).
    <section data-testid="directory-import" aria-label={DOOR_LABELS.sources} className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[13px]">
        <FolderInput size={14} className="shrink-0 text-accent-soft" aria-hidden="true" />
        <span data-testid="directory-import-line" className={found.fresh > 0 ? "text-zinc-300" : "text-zinc-500"}>
          {line}
        </span>
        {canOpen && (
          <button
            type="button"
            data-testid="directory-import-open"
            aria-expanded={open}
            // Only while the door is on the page: an id that names nothing
            // is a broken reference for a screen reader (v1.353.0 integrate).
            aria-controls={open ? "directory-import-door" : undefined}
            onClick={() => setOpen((v) => !v)}
            className="btn-ghost px-3 py-1 text-[12px]"
          >
            {open ? "Hide" : found.fresh > 0 ? "Import" : "See them"}
          </button>
        )}
      </div>
      {open && (
        <div id="directory-import-door">
          <SourcesDoor
            sources={sources}
            onAdded={() => {
              onImported();
              reload();
            }}
          />
        </div>
      )}
    </section>
  );
}

/** What works with nothing to connect (built-in tools). v1.358.0: the
 *  daemon's `connectors/catalog.BUILT_IN_TOOLS` says the same words (pinned
 *  equal), and a card whose `built_in` names one of them is a separate
 *  version of it, folded apart (`isBuiltInExtra`). */
const BUILT_IN = ["Files & documents", "Web", "Images", "Spreadsheets", "Memory"];

/** v1.358.0 (lane 8D): a card that offers a SEPARATE version of something
 *  built in (the daemon names it in `built_in`). */
function isBuiltInExtra(c: Pick<Connector, "built_in">): boolean {
  return Boolean((c.built_in ?? "").trim());
}

/** v1.353.0 (G42): the other doors, so this page is the one place to start. */
const MORE_WAYS: readonly { label: string; href: string; title: string }[] = [
  {
    label: "An agent on another computer",
    href: "/agents?view=team",
    title: "Add a remote agent to your team",
  },
  {
    label: "A model server on your network",
    href: "/fleet",
    title: "Add a model running on another computer",
  },
  {
    label: "Add a tool by config (advanced)",
    // v1.354.0 integrate: lands on the add area itself (useFocusRef "add"),
    // whose doors this title names in their own words.
    href: "/tools?focus=add",
    title: `${DOOR_LABELS.paste}, add from npm, PyPI or GitHub, or reach a server by URL`,
  },
];

/* -------------------------------------------------------------------------- */
/*  Skeleton card (first load)                                                  */
/* -------------------------------------------------------------------------- */

function CardSkeleton() {
  return (
    <div className="card-surface flex flex-col gap-3.5 p-5">
      <div className="flex items-center gap-3">
        <Skeleton className="h-12 w-12 rounded-2xl" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-3.5 w-2/5" />
          <Skeleton className="h-2.5 w-1/4" />
        </div>
      </div>
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-12 w-full rounded-xl" />
      <Skeleton className="h-8 w-full rounded-xl" />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Page                                                                       */
/* -------------------------------------------------------------------------- */

export default function MarketplacePage() {
  // Poll so a card flips to "Connected" shortly after an OAuth hand-off / connect.
  const { data, error, loading, reload } = usePolledApi<ConnectorsResponse>(
    "/connectors",
    8000,
  );
  const offline = error != null && error.status === 0;

  const connectors = useMemo(() => data?.connectors ?? [], [data]);
  const categories = useMemo(() => data?.categories ?? [], [data]);

  const [search, setSearch] = useState("");
  const [activeCat, setActiveCat] = useState<string>("All");
  // v1.316.0: "show only what I have connected" — a toggle on the summary
  // line's count, beside (not instead of) the category chips and search.
  const [connectedOnly, setConnectedOnly] = useState(false);

  const total = connectors.length;
  // v1.358.0 (lane 8D): the count, the filter and "tools live" read each
  // card's OWN health (lib/directoryHealth) — `connected` stays true for an
  // app the user switched off or that did not start, so "3 connected" used to
  // sit over cards reading Off and Needs attention. Those are said apart.
  const counts = useMemo(() => healthCounts(connectors), [connectors]);
  const connectedCount = counts.working;
  const toolsLive = useMemo(
    () =>
      connectors
        .filter((c) => cardHealth(c) === "working")
        .reduce((n, c) => n + (c.tools_loaded || 0), 0),
    [connectors],
  );

  const q = search.trim().toLowerCase();
  const visible = useMemo(
    () =>
      connectors.filter((c) => {
        if (connectedOnly && cardHealth(c) !== "working") return false;
        const inCat = activeCat === "All" || c.category === activeCat;
        const inSearch =
          !q ||
          c.name.toLowerCase().includes(q) ||
          c.blurb.toLowerCase().includes(q) ||
          c.unlocks.toLowerCase().includes(q);
        return inCat && inSearch;
      }),
    [connectors, activeCat, q, connectedOnly],
  );

  // v1.358.0 (lane 8D): a separate version of something built in (Files &
  // folders, Fetch web pages, Knowledge graph) never sits among the apps
  // under the "Built in, nothing to connect" line as if it were missing: it
  // lives in its own fold at the end ("Separate versions of built-in tools").
  const mainVisible = useMemo(() => visible.filter((c) => !isBuiltInExtra(c)), [visible]);
  const extrasVisible = useMemo(() => visible.filter(isBuiltInExtra), [visible]);
  // The fold opens by itself while a search is typed (its cards answer it) or
  // when one of them is saved (it must be findable); otherwise the user's own
  // press decides (null = not pressed yet: closed).
  const [extrasChoice, setExtrasChoice] = useState<boolean | null>(null);
  const extrasOpen = q
    ? true
    : (extrasChoice ?? extrasVisible.some((c) => Boolean(c.connected)));

  // Group visible connectors by category, honoring the returned category order.
  // Any category not listed falls to the end (defensive).
  const grouped = useMemo(() => {
    const order = categories.length
      ? categories
      : Array.from(new Set(connectors.map((c) => c.category)));
    const seen = new Set(order);
    const extra = Array.from(new Set(mainVisible.map((c) => c.category))).filter(
      (cat) => !seen.has(cat),
    );
    return [...order, ...extra]
      .map((cat) => ({ cat, items: mainVisible.filter((c) => c.category === cat) }))
      .filter((g) => g.items.length > 0);
  }, [categories, connectors, mainVisible]);

  // v1.353.0: a group the daemon's category list does not name yet (a card's
  // own category, e.g. "Added by you") still gets its chip, after the rest —
  // the same rule the grouping above follows.
  // v1.353.0 integrate: and a group with no card gets no chip — the daemon's
  // list names "Added by you" and "Memory" on every install, and a chip that
  // opens an empty gallery is a dead end.
  const chips = useMemo(() => {
    const named = new Set(categories);
    const carried = new Set(connectors.map((c) => c.category));
    const extra = Array.from(carried).filter((cat) => cat && !named.has(cat));
    return ["All", ...categories.filter((cat) => carried.has(cat)), ...extra];
  }, [categories, connectors]);

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Directory"
          subtitle="Connect Iron Jarvis to the apps you already use. Keys are stored encrypted; nothing is sent anywhere but the service you connect."
          actions={
            /* Users arrive from the chat "+" menu (the Directory has no nav
               row) — give them the door back so a collapsed sidebar never
               strands them here. v1.316.0: a quiet door, not the page's one
               filled accent — the first thing the eye met was "leave". */
            <Link
              href="/chat"
              className="btn-ghost px-3 py-1.5 text-[13px]"
              title="Back to the conversation — new connections appear in the + menu"
            >
              <ArrowLeft size={14} /> Back to chat
            </Link>
          }
        />
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      {/* v1.316.0 (UX wave 4): the four stat tiles (~110 px of prime space)
          are one summary line — all four numbers kept, tools live 0 included.
          "N connected" is a toggle that narrows the cards to connected ones. */}
      {data && !offline && (
        <Reveal>
          <p
            data-testid="directory-summary"
            className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-zinc-500"
          >
            <button
              type="button"
              onClick={() => setConnectedOnly((v) => !v)}
              aria-pressed={connectedOnly}
              disabled={connectedCount === 0 && !connectedOnly}
              title={
                connectedOnly
                  ? "Show every connection again"
                  : "Show only what is connected and working"
              }
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-medium transition-colors disabled:cursor-default disabled:opacity-60 ${
                connectedOnly
                  ? "border-accent/40 bg-accent/[0.12] text-accent-soft"
                  : "border-white/10 bg-white/[0.02] text-zinc-300 hover:border-white/20 hover:text-zinc-100"
              }`}
            >
              <CheckCircle2 size={13} aria-hidden="true" />
              {connectedCount} connected
            </button>
            {/* v1.358.0 (lane 8D): saved but not working, said apart from
                the count — never folded into "connected". */}
            {counts.off > 0 && (
              <>
                <span aria-hidden="true">·</span>
                <span data-testid="directory-count-off">{counts.off} off</span>
              </>
            )}
            {counts.attention > 0 && (
              <>
                <span aria-hidden="true">·</span>
                <span data-testid="directory-count-attention" className="text-tone-warn">
                  {attentionWords(counts.attention)}
                </span>
              </>
            )}
            <span aria-hidden="true">·</span>
            <span>{total} available</span>
            <span aria-hidden="true">·</span>
            <span>
              {toolsLive} tool{toolsLive === 1 ? "" : "s"} live
            </span>
            <span aria-hidden="true">·</span>
            <span>
              {chips.length - 1} categor{chips.length - 1 === 1 ? "y" : "ies"}
            </span>
          </p>
        </Reveal>
      )}

      {/* v1.353.0 (G42): one Connections home. What the user's other apps
          already use (with Import), and what needs no connecting at all. */}
      {!offline && (
        <Reveal>
          <div className="space-y-2.5">
            <ImportFromApps onImported={reload} />
            <p
              data-testid="directory-built-in"
              className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-zinc-500"
            >
              <CheckCircle2 size={14} className="shrink-0" aria-hidden="true" />
              <span>Built in, nothing to connect: {BUILT_IN.join(", ")}.</span>
            </p>
          </div>
        </Reveal>
      )}

      {/* Search + category filter */}
      <Reveal>
        <div className="space-y-3">
          {/* v1.313.0: `isolate` + `z-[1]`: the .field input has a backdrop blur,
              which painted over the magnifier and left an empty gap. */}
          <div className="relative isolate">
            <Search
              size={15}
              className="pointer-events-none absolute left-3 top-1/2 z-[1] -translate-y-1/2 text-zinc-500"
              aria-hidden="true"
            />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search connections…"
              aria-label="Search connections"
              className="field pl-9"
            />
          </div>
          {chips.length > 1 && (
            <div className="flex flex-wrap gap-2" role="group" aria-label="Filter by category">
              {chips.map((cat) => {
                const active = activeCat === cat;
                return (
                  <button
                    key={cat}
                    type="button"
                    onClick={() => setActiveCat(cat)}
                    aria-pressed={active}
                    className={`rounded-full border px-3 py-1 text-xs font-medium transition-all ${
                      active
                        ? "border-accent/40 bg-accent/[0.12] text-accent-soft shadow-[0_0_14px_-4px_rgb(var(--accent-rgb)/0.5)]"
                        : "border-white/10 bg-white/[0.02] text-zinc-400 hover:border-white/20 hover:text-zinc-200"
                    }`}
                  >
                    {cat}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </Reveal>

      {/* Body */}
      {loading && !data ? (
        <Reveal>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <CardSkeleton key={i} />
            ))}
          </div>
        </Reveal>
      ) : offline ? null : mainVisible.length === 0 && extrasVisible.length === 0 ? (
        <Reveal>
          <Empty icon={<Search size={24} />}>
            {/* v1.358.0 review: the filter keeps WORKING cards only, so with
                saved apps that are off or need attention "Nothing is
                connected yet" would be untrue. */}
            {connectedOnly && !q && activeCat === "All" && counts.off + counts.attention > 0
              ? "Nothing you connected is working right now — press the connected count again to see every connection."
              : connectedOnly && !q && activeCat === "All"
              ? "Nothing is connected yet — press the connected count again to see every connection."
              : connectedOnly && (q || activeCat !== "All")
              ? // v1.358.0 fix round: the filter hides an Off / needs-attention
                // app that DOES match, so "nothing you have connected matches"
                // would be untrue; name what the filter keeps.
                "Nothing connected and working matches your search — press the connected count to show every connection."
              : q || activeCat !== "All" || connectedOnly
              ? "No connections match your search. Try a different term or category."
              : "No connections available."}
          </Empty>
        </Reveal>
      ) : (
        grouped.map((group) => (
          <Reveal key={group.cat}>
            <section className="space-y-3.5">
              <div className="flex items-center gap-3">
                <span
                  aria-hidden="true"
                  className="h-4 w-1 rounded-full bg-gradient-to-b from-accent to-accent/20 shadow-[0_0_8px_rgb(var(--accent-rgb)/0.4)]"
                />
                <h2 className="text-[12px] font-semibold uppercase tracking-[0.14em] text-zinc-300">
                  {group.cat}
                </h2>
                <span className="rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[10px] font-medium text-zinc-500">
                  {group.items.length}
                </span>
                <span
                  aria-hidden="true"
                  className="h-px flex-1 bg-gradient-to-r from-white/[0.08] to-transparent"
                />
              </div>
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {group.items.map((c) => (
                  <ConnectorCard key={c.id} c={c} onChanged={reload} />
                ))}
              </div>
            </section>
          </Reveal>
        ))
      )}

      {/* v1.358.0 (lane 8D): separate versions of what is built in, folded
          apart so the "Built in, nothing to connect" line above stays true:
          nothing here is needed for files, the web or memory to work. A
          quiet native fold (keyboard and screen reader for free), no box. */}
      {!offline && !(loading && !data) && extrasVisible.length > 0 && (
        <Reveal>
          <details
            data-testid="directory-built-in-extras"
            open={extrasOpen}
            onToggle={(e) => setExtrasChoice((e.currentTarget as HTMLDetailsElement).open)}
          >
            <summary className="cursor-pointer select-none text-[12px] font-medium text-zinc-500 transition-colors hover:text-zinc-300">
              Separate versions of built-in tools ({extrasVisible.length})
            </summary>
            <p className="mt-2 text-[12px] text-zinc-500">
              Iron Jarvis already does these with nothing to connect. Add one only if you want a
              separate version for an advanced setup.
            </p>
            <div className="mt-3.5 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {extrasVisible.map((c) => (
                <ConnectorCard key={c.id} c={c} onChanged={reload} />
              ))}
            </div>
          </details>
        </Reveal>
      )}

      {/* v1.353.0 (G42): the doors that live elsewhere, named here so this
          page is the one place to start. Quiet links, like the cards'. */}
      {!offline && (
        <Reveal>
          <nav
            aria-label="More ways to connect"
            data-testid="directory-more-ways"
            className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[12px] text-zinc-500"
          >
            <span>More ways to connect:</span>
            {MORE_WAYS.map((w) => (
              <Link
                key={w.href}
                href={w.href}
                title={w.title}
                className="inline-flex items-center gap-1 text-zinc-400 transition-colors hover:text-accent-soft"
              >
                {w.label} <ArrowRight size={11} aria-hidden="true" />
              </Link>
            ))}
          </nav>
        </Reveal>
      )}
    </PageShell>
  );
}
