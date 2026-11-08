"use client";

/**
 * ONE front door for connections (v1.455.0, readiness Wave H — the owner's
 * decision on item 35, accepted as recommended).
 *
 * Connections lived in five places with the catalog's own categories:
 * Settings › Connections (plans, keys, model servers, several accounts), the
 * Directory (apps), Tools (extensions), Model servers (/fleet) and
 * Notifications (where Jarvis messages you). A person could not tell where
 * "my connections" are. This landing sits at the top of Settings ›
 * Connections with three tabs:
 *
 *  - **Discover** — every kind of thing that can be connected, one row each,
 *    and where it is set up (the existing pages, opened in place).
 *  - **Connected** — what IS connected, in one list: apps and extensions
 *    (GET /connectors, the Directory's own reading — `lib/directoryHealth`),
 *    models (the app's one /health poll) and destinations (GET
 *    /comm/channels). Read when the tab opens, never polled.
 *  - **Import** — the tools the user's other programs already use (GET
 *    /mcp/import/sources and the Tools page's own `SourcesDoor`).
 *
 * Nothing here is a second copy: every row opens the page that owns it, and
 * the other places link back here (`CONNECTIONS_HOME_HREF`). The old
 * addresses still work (`lib/relocated`). Calm (the v1.321.0 style): text
 * tabs, quiet rows, one line per thing.
 */

import { useCallback, useEffect, useMemo, useState, type KeyboardEvent } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { get } from "@/lib/api";
import { useDaemon } from "@/lib/daemon";
import { providerDisplay } from "@/lib/onboarding";
import { cardHealth, type CardHealth } from "@/lib/directoryHealth";
import { discover, discoveryLine } from "@/lib/importDiscovery";
import type { ImportSource } from "@/lib/mcpImport";
import { LoadFailedLine } from "@/components/LoadFailedLine";

const SourcesDoor = dynamic(() => import("@/components/tools/AddPack").then((m) => m.SourcesDoor), {
  ssr: false,
});

/** Where the landing is (lib/connectionsHome): every other connection page
 *  links here. */
export { CONNECTIONS_HOME_HREF, CONNECTIONS_HOME_LABEL } from "@/lib/connectionsHome";

export type ConnectionsTab = "discover" | "connected" | "import";
export const CONNECTIONS_TABS: ReadonlyArray<{ id: ConnectionsTab; label: string }> = [
  { id: "discover", label: "Discover" },
  { id: "connected", label: "Connected" },
  { id: "import", label: "Import" },
];

/** The tab an address asks for (`?tab=connected`), else Discover. */
export function tabFromSearch(search: string | null | undefined): ConnectionsTab {
  try {
    const t = new URLSearchParams(search ?? "").get("tab") ?? "";
    return CONNECTIONS_TABS.some((x) => x.id === t) ? (t as ConnectionsTab) : "discover";
  } catch {
    return "discover";
  }
}

/** A place the landing opens: a Settings section (in place) or a page. */
interface Door {
  id: string;
  label: string;
  blurb: string;
  /** A Settings section id (`connections-apps`) — opened in place. */
  section?: string;
  /** Or a page of its own. */
  href?: string;
}

/** Every kind of connection, and where it is set up. */
export const DISCOVER_DOORS: readonly Door[] = [
  { id: "apps", label: "Apps", blurb: "Notion, GitHub, Slack, Google Drive and more.", section: "connections-apps" },
  {
    id: "models",
    label: "AI models and plans",
    blurb: "Your Claude or ChatGPT plan, an API key, or a model on this PC.",
    section: "connections-accounts",
  },
  {
    id: "model-servers",
    label: "Your own model servers",
    blurb: "A model running on another computer on your network.",
    href: "/connections?focus=endpoints",
  },
  {
    id: "extensions",
    label: "Extensions and your own tools",
    blurb: "Ready-made extensions, a tool by its config, or a web service of yours.",
    section: "permissions-tools",
  },
  {
    id: "destinations",
    label: "Where Jarvis messages you",
    blurb: "Telegram, Slack, Discord or email, for updates and replies.",
    section: "connections-notifications",
  },
  { id: "browser", label: "Your browser", blurb: "The browser add-on, so Jarvis can work on pages.", section: "connections-browser" },
  {
    id: "remote",
    label: "Another computer",
    blurb: "An agent, or another Iron Jarvis, on another computer.",
    href: "/agents?view=team",
  },
];

const HEALTH_WORDS: Record<Exclude<CardHealth, "none">, string> = {
  working: "Working",
  signin: "Sign-in to finish",
  attention: "Needs attention",
  off: "Off",
};

interface ConnectorRow {
  id: string;
  name: string;
  connect_via?: string | null;
  connected?: boolean | null;
  status?: string | null;
  enabled?: boolean | null;
}
interface ChannelRow {
  name: string;
  type?: string | null;
  builtin?: boolean | null;
}

/** One line of the Connected tab. */
export interface ConnectedRow {
  key: string;
  name: string;
  kind: "App" | "Model" | "Destination";
  state: string;
  warn: boolean;
  door: Door;
}

const DOOR = (id: string) => DISCOVER_DOORS.find((d) => d.id === id)!;

/** The Connected tab's rows from the three readings (pure; the tests feed it). */
export function connectedRows(
  connectors: readonly ConnectorRow[],
  providers: ReadonlyArray<{ provider: string; available?: boolean | null }>,
  channels: readonly ChannelRow[],
): ConnectedRow[] {
  const out: ConnectedRow[] = [];
  for (const p of providers) {
    if (!p?.provider || p.provider === "mock" || p.provider === "auto" || p.available !== true) continue;
    out.push({
      key: `model:${p.provider}`,
      name: providerDisplay(p.provider),
      kind: "Model",
      state: "Ready",
      warn: false,
      door: DOOR("models"),
    });
  }
  for (const c of connectors) {
    const h = cardHealth(c);
    if (h === "none") continue;
    out.push({
      key: `app:${c.id}`,
      name: c.name || c.id,
      kind: "App",
      state: HEALTH_WORDS[h],
      warn: h === "attention" || h === "signin",
      door: DOOR("apps"),
    });
  }
  for (const ch of channels) {
    if (!ch?.name || ch.builtin) continue;
    out.push({
      key: `dest:${ch.name}`,
      name: ch.name,
      kind: "Destination",
      state: ch.type ? String(ch.type) : "Set up",
      warn: false,
      door: DOOR("destinations"),
    });
  }
  return out;
}

const TAB_BASE =
  "rounded-md px-2 py-1 text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50";

export function ConnectionsHome({ onGo }: { onGo: (group: string, section?: string) => void }) {
  const [tab, setTab] = useState<ConnectionsTab>("discover");
  useEffect(() => {
    // Read once, after mount (a static page: never useSearchParams).
    setTab(tabFromSearch(typeof window !== "undefined" ? window.location.search : ""));
  }, []);

  const open = useCallback(
    (door: Door) => {
      if (door.section) onGo(door.section.split("-")[0], door.section);
    },
    [onGo],
  );

  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const i = CONNECTIONS_TABS.findIndex((t) => t.id === tab);
    const next = CONNECTIONS_TABS[(i + (e.key === "ArrowRight" ? 1 : CONNECTIONS_TABS.length - 1)) % CONNECTIONS_TABS.length];
    setTab(next.id);
    document.getElementById(`connections-tab-${next.id}`)?.focus();
  }

  return (
    <section data-testid="connections-home" className="rounded-2xl border border-white/[0.06] bg-white/[0.015] p-3">
      <div role="tablist" aria-label="Your connections" onKeyDown={onKey} className="mb-2 flex gap-1">
        {CONNECTIONS_TABS.map((t) => (
          <button
            key={t.id}
            id={`connections-tab-${t.id}`}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            aria-controls={`connections-panel-${t.id}`}
            tabIndex={tab === t.id ? 0 : -1}
            data-testid={`connections-tab-${t.id}`}
            onClick={() => setTab(t.id)}
            className={`${TAB_BASE} ${tab === t.id ? "bg-white/[0.06] font-medium text-zinc-100" : "text-zinc-400 hover:text-zinc-200"}`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`connections-panel-${tab}`}
        aria-labelledby={`connections-tab-${tab}`}
        data-testid={`connections-panel-${tab}`}
      >
        {tab === "discover" && <DiscoverPanel open={open} />}
        {tab === "connected" && <ConnectedPanel open={open} />}
        {tab === "import" && <ImportPanel />}
      </div>
    </section>
  );
}

function DoorRow({ door, open }: { door: Door; open: (d: Door) => void }) {
  const body = (
    <>
      <span className="min-w-0 flex-1">
        <span className="block text-[13px] font-medium text-zinc-200">{door.label}</span>
        <span className="block text-[12px] text-zinc-500">{door.blurb}</span>
      </span>
      <ArrowRight size={13} className="shrink-0 text-zinc-600" aria-hidden />
    </>
  );
  const cls = "flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left transition-colors hover:bg-white/[0.03]";
  return door.href ? (
    <Link href={door.href} data-testid={`connections-door-${door.id}`} className={cls}>
      {body}
    </Link>
  ) : (
    <button type="button" data-testid={`connections-door-${door.id}`} onClick={() => open(door)} className={cls}>
      {body}
    </button>
  );
}

function DiscoverPanel({ open }: { open: (d: Door) => void }) {
  return (
    <div className="divide-y divide-white/[0.04]">
      {DISCOVER_DOORS.map((d) => (
        <DoorRow key={d.id} door={d} open={open} />
      ))}
    </div>
  );
}

function ConnectedPanel({ open }: { open: (d: Door) => void }) {
  const { health } = useDaemon();
  const [connectors, setConnectors] = useState<ConnectorRow[] | null>(null);
  const [channels, setChannels] = useState<ChannelRow[]>([]);
  const [failed, setFailed] = useState<unknown>(null);
  const [n, setN] = useState(0);

  useEffect(() => {
    let live = true;
    setFailed(null);
    void Promise.all([
      get<{ connectors?: ConnectorRow[] }>("/connectors"),
      get<{ channels?: ChannelRow[] }>("/comm/channels").catch(() => ({ channels: [] as ChannelRow[] })),
    ])
      .then(([c, ch]) => {
        if (!live) return;
        setConnectors(Array.isArray(c?.connectors) ? c.connectors : []);
        setChannels(Array.isArray(ch?.channels) ? ch.channels : []);
      })
      .catch((e: unknown) => {
        if (live) setFailed(e ?? new Error("failed"));
      });
    return () => {
      live = false;
    };
  }, [n]);

  const rows = useMemo(
    () => (connectors ? connectedRows(connectors, health?.providers ?? [], channels) : []),
    [connectors, health, channels],
  );

  if (failed) return <LoadFailedLine thing="Your connections" error={failed as never} onRetry={() => setN((x) => x + 1)} />;
  if (connectors === null) return <p className="px-2 py-2 text-[12px] text-zinc-500">Reading your connections…</p>;
  if (rows.length === 0) {
    return (
      <p data-testid="connections-none" className="px-2 py-2 text-[12px] text-zinc-500">
        Nothing is connected yet. Discover has every kind of thing you can connect.
      </p>
    );
  }
  return (
    <ul className="divide-y divide-white/[0.04]">
      {rows.map((r) => (
        <li key={r.key} data-testid={`connections-row-${r.key}`} className="flex items-center gap-3 px-2 py-2">
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13px] text-zinc-200">{r.name}</span>
            <span className="block text-[11px] text-zinc-500">{r.kind}</span>
          </span>
          <span className={`text-[12px] ${r.warn ? "text-tone-warn" : "text-zinc-400"}`}>{r.state}</span>
          <button
            type="button"
            onClick={() => open(r.door)}
            className="text-[12px] text-zinc-400 underline-offset-2 transition-colors hover:text-accent-soft hover:underline"
          >
            Manage
          </button>
        </li>
      ))}
    </ul>
  );
}

function ImportPanel() {
  const [sources, setSources] = useState<ImportSource[] | null>(null);
  const [failed, setFailed] = useState<unknown>(null);
  const [n, setN] = useState(0);

  useEffect(() => {
    let live = true;
    setFailed(null);
    get<{ sources?: ImportSource[] }>("/mcp/import/sources")
      .then((res) => {
        if (live) setSources(Array.isArray(res?.sources) ? res.sources : []);
      })
      .catch((e: unknown) => {
        if (live) setFailed(e ?? new Error("failed"));
      });
    return () => {
      live = false;
    };
  }, [n]);

  if (failed)
    return (
      <LoadFailedLine
        thing="The tools your other programs use"
        error={failed as never}
        onRetry={() => setN((x) => x + 1)}
      />
    );
  if (sources === null) return <p className="px-2 py-2 text-[12px] text-zinc-500">Looking in your other programs…</p>;
  const line = discoveryLine(discover(sources));
  return (
    <div className="space-y-2 px-2 py-1">
      <p data-testid="connections-import-line" className="text-[12px] text-zinc-400">
        {line || "Bring over the tools Claude Code, Codex, Cursor, VS Code and others already use."} Nothing is added
        until you tick it and press Add.
      </p>
      <SourcesDoor sources={sources} onAdded={() => setN((x) => x + 1)} />
    </div>
  );
}
