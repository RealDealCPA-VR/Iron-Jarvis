"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Brain,
  Store,
  Search,
  Plug,
  ShieldCheck,
  KeyRound,
  Check,
  CheckCircle2,
  Radio,
  ExternalLink,
  Sparkles,
  ArrowRight,
  Info,
  Zap,
  Layers,
  X,
} from "lucide-react";
import { post, del, ApiError } from "@/lib/api";
import { usePolledApi } from "@/lib/useApi";
import {
  Badge,
  Stat,
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

/* -------------------------------------------------------------------------- */
/*  Types (local — lib/types.ts is intentionally untouched)                    */
/* -------------------------------------------------------------------------- */

type ConnectVia = "mcp" | "oauth" | "api_key" | "memory";
type FieldKind = "secret" | "env" | "arg";

/** One value the user supplies to connect an MCP connector. */
interface ConnectorField {
  name: string;
  label: string;
  help: string;
  kind: FieldKind;
  optional: boolean;
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

/* --- POST /connectors/{id}/test response variants ------------------------ */
interface McpTestResult {
  ok: boolean;
  count: number;
  tools: string[];
  error: string | null;
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

const VIA: Record<ConnectVia, { label: string; icon: typeof Plug }> = {
  mcp: { label: "MCP", icon: Plug },
  oauth: { label: "OAuth", icon: ShieldCheck },
  api_key: { label: "API key", icon: KeyRound },
  memory: { label: "Memory", icon: Brain },
};

function ViaTag({ via }: { via: ConnectVia }) {
  // Defensive fallback: an unknown via from a newer daemon must not crash.
  const { label, icon: Icon } = VIA[via] ?? { label: via, icon: Plug };
  return (
    <span className="inline-flex shrink-0 items-center gap-1 rounded-md border border-white/[0.08] bg-white/[0.03] px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-400">
      <Icon size={11} className="text-accent-soft/80" />
      {label}
    </span>
  );
}

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
        await post(connectPath, { values: { key: (values.key ?? "").trim() } });
        setSuccess(`${c.name} connected.`);
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
          setNote(
            res.note ??
              "Saved — restart Iron Jarvis (or check the command is installed) to load its tools.",
          );
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

  return (
    <div
      className={`card-surface relative flex flex-col gap-3.5 p-5 transition-all duration-300 hover:-translate-y-0.5 hover:shadow-card-hover hover:ring-1 hover:ring-accent/20 ${
        c.connected
          ? "ring-1 ring-emerald-500/25 shadow-[0_0_24px_-10px_rgba(52,211,153,0.35)]"
          : ""
      }`}
    >
      {/* Header: glyph + name + via tag + status */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <BrandGlyph id={c.id} glyph={c.glyph} connected={c.connected} />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h3 className="truncate text-sm font-semibold text-zinc-100">{c.name}</h3>
              <ViaTag via={c.connect_via} />
            </div>
            <p className="mt-0.5 truncate text-[11px] text-zinc-500">{c.category}</p>
          </div>
        </div>
        {c.connected && (
          <span className="shrink-0">
            <Badge value="Connected" tone="green" />
          </span>
        )}
      </div>

      {/* Blurb */}
      <p className="text-[13px] text-zinc-400">{c.blurb}</p>

      {/* What it unlocks — the plain-English payoff */}
      <div className="flex items-start gap-2 rounded-xl border border-accent/15 bg-accent/[0.04] px-3 py-2.5">
        <Sparkles size={14} className="mt-0.5 shrink-0 text-accent-soft" aria-hidden="true" />
        <p className="text-[12.5px] leading-relaxed text-zinc-300">{c.unlocks}</p>
      </div>

      {/* Scopes / permissions */}
      {c.scopes.length > 0 && (
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
          <p className="text-[11px] text-zinc-500">
            {c.connect_via === "mcp"
              ? `${c.tools_loaded} tool${c.tools_loaded === 1 ? "" : "s"} loaded`
              : c.connect_via === "memory"
                ? "Grounds chat & agents — toggle it on in the chat + menu"
                : c.account
                  ? `Signed in as ${c.account}`
                  : "Ready to use"}
          </p>
          {c.connect_via === "mcp" && (c.tool_names?.length ?? 0) > 0 && (
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

      {/* Spacer pushes actions to the bottom for even card heights */}
      <div className="flex-1" />

      {/* Action row */}
      {c.connected ? (
        <div className="flex items-center gap-2">
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
          <ConfirmButton
            onConfirm={disconnect}
            label="Disconnect"
            title={`Disconnect ${c.name}`}
            className="py-1.5"
          />
        </div>
      ) : c.connect_via === "memory" ? (
        // Memory sources are added on the Memory page, not connected here.
        <Link href="/memory" className="btn-ghost w-full py-1.5 text-center text-xs">
          <Brain size={14} /> Manage on the Memory page
        </Link>
      ) : (
        <div className="space-y-2.5">
          {!open ? (
            <button
              type="button"
              onClick={toggleForm}
              disabled={busy}
              className="btn-accent w-full py-1.5 text-xs"
            >
              {busy && c.connect_via === "oauth" ? (
                <LoaderInline label="Starting…" />
              ) : (
                <>
                  <Plug size={14} /> Connect
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
                c.fields.map((f, i) => (
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
            <ErrorNote>{testResult.error ?? "Test failed."}</ErrorNote>
          )
        ) : testResult.ok ? (
          <SuccessNote>Memory source is live and searchable.</SuccessNote>
        ) : (
          <ErrorNote>{testResult.error ?? "Test failed."}</ErrorNote>
        ))}
    </div>
  );
}

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

  const total = connectors.length;
  const connectedCount = connectors.filter((c) => c.connected).length;
  const toolsLive = useMemo(
    () =>
      connectors
        .filter((c) => c.connected)
        .reduce((n, c) => n + (c.tools_loaded || 0), 0),
    [connectors],
  );

  const q = search.trim().toLowerCase();
  const visible = useMemo(
    () =>
      connectors.filter((c) => {
        const inCat = activeCat === "All" || c.category === activeCat;
        const inSearch =
          !q ||
          c.name.toLowerCase().includes(q) ||
          c.blurb.toLowerCase().includes(q) ||
          c.unlocks.toLowerCase().includes(q);
        return inCat && inSearch;
      }),
    [connectors, activeCat, q],
  );

  // Group visible connectors by category, honoring the returned category order.
  // Any category not listed falls to the end (defensive).
  const grouped = useMemo(() => {
    const order = categories.length
      ? categories
      : Array.from(new Set(connectors.map((c) => c.category)));
    const seen = new Set(order);
    const extra = Array.from(new Set(visible.map((c) => c.category))).filter(
      (cat) => !seen.has(cat),
    );
    return [...order, ...extra]
      .map((cat) => ({ cat, items: visible.filter((c) => c.category === cat) }))
      .filter((g) => g.items.length > 0);
  }, [categories, connectors, visible]);

  const chips = ["All", ...categories];

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Marketplace"
          subtitle="Connect Iron Jarvis to your apps — one tap. Tokens are stored encrypted; nothing is sent anywhere but the service you connect."
          actions={
            /* Users arrive from the chat "+" menu (Marketplace has no nav
               entry) — give them the door back so a collapsed sidebar never
               strands them here. */
            <Link
              href="/chat"
              className="btn-accent px-3 py-1.5 text-[13px]"
              title="Back to the conversation — new connectors appear in the + menu"
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

      {/* Stat strip — the page's pulse at a glance */}
      {data && !offline && (
        <Reveal>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat
              label="Connected"
              value={connectedCount}
              icon={<CheckCircle2 size={15} />}
              accent={connectedCount > 0}
            />
            <Stat label="Available" value={total} icon={<Store size={15} />} />
            <Stat label="Tools live" value={toolsLive} icon={<Zap size={15} />} />
            <Stat
              label="Categories"
              value={categories.length}
              icon={<Layers size={15} />}
            />
          </div>
        </Reveal>
      )}

      {/* Search + category filter */}
      <Reveal>
        <div className="space-y-3">
          <div className="relative">
            <Search
              size={15}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500"
              aria-hidden="true"
            />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search connectors…"
              aria-label="Search connectors"
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
      ) : offline ? null : visible.length === 0 ? (
        <Reveal>
          <Empty icon={<Search size={24} />}>
            {q || activeCat !== "All"
              ? "No connectors match your search. Try a different term or category."
              : "No connectors available."}
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
    </PageShell>
  );
}
