"use client";

/**
 * SETTINGS, IN SEVEN GROUPS, FROM THE ONE SCHEMA (calm UI redesign S10,
 * AUDIT §4.4, brief non-negotiable 3).
 *
 * Every row is drawn from `GET /settings/schema` — the same declaration the
 * chat's settings tools are generated from — and saved through the same
 * writer (`PUT /settings/values`: validated all-or-nothing, one ledger row,
 * Undo). So a setting is changeable here exactly when it is changeable in
 * chat, and a change made in either place shows in "Changed here or in chat".
 *
 * The groups also hold the pages that were their own routes before (AUDIT
 * Q4/Q8): Connections, the Directory, Notifications, Browser and Secrets under
 * Connections; You and Train under Memory & you; Tools and standing grants
 * under Permissions & ledger; Updates and maintenance under System. Each is the
 * page itself, embedded (its old address redirects here), mounted only when
 * its section is open.
 *
 * A daemon with no schema (older than the dashboard) gets the previous form.
 */

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState, type ComponentType } from "react";
import { ChevronDown, ChevronLeft, ChevronRight, Palette, RotateCcw, Search } from "lucide-react";
import { ApiError, get, post, put } from "@/lib/api";
import { useDaemon } from "@/lib/daemon";
import { getDeviceId } from "@/lib/device";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { Card, ErrorNote, LoaderInline, OfflineHint, SuccessNote } from "@/components/ui";
import { EmbeddedPage } from "@/components/EmbeddedPage";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import { ThemeMaker } from "@/components/ThemeMaker";
import { SettingRow, type SchemaSetting } from "@/components/settings/SettingRow";
import { LedgerPanel } from "@/components/settings/LedgerPanel";

const LegacySettingsForm = dynamic(() => import("@/components/settings/pages/LegacySettingsForm"), { ssr: false });

const page = (load: () => Promise<{ default: ComponentType }>) => dynamic(load, { ssr: false, loading: () => <LoaderInline /> });
const named = <K extends string>(load: () => Promise<Record<K, ComponentType>>, key: K) =>
  dynamic(() => load().then((m) => ({ default: m[key] })), { ssr: false, loading: () => <LoaderInline /> });

interface Group {
  id: string;
  label: string;
  description: string;
}
interface Sub {
  id: string;
  label: string;
  blurb: string;
  Comp: ComponentType;
}

/** The pages and panels each group holds, beside its settings rows. */
const SUBSECTIONS: Record<string, Sub[]> = {
  models: [
    {
      id: "models-local",
      label: "What your local models can do",
      blurb: "Each local model's measured skills, and which jobs it is trusted with.",
      Comp: named(() => import("@/components/settings/pages/LegacySettingsForm"), "LocalCapabilitiesCard"),
    },
  ],
  connections: [
    {
      id: "connections-accounts",
      label: "Accounts & keys",
      blurb: "Model providers, sign-ins, API keys, endpoints and Iron-Proxy accounts.",
      Comp: page(() => import("@/components/settings/pages/ConnectionsPage")),
    },
    {
      id: "connections-apps",
      label: "Apps & tools",
      blurb: "The Directory: connect Notion, GitHub, Slack and other apps.",
      Comp: page(() => import("@/components/settings/pages/DirectoryPage")),
    },
    {
      id: "connections-notifications",
      label: "Notifications",
      blurb: "Where Iron Jarvis messages you: Telegram, Slack, Discord and email.",
      Comp: page(() => import("@/components/settings/pages/NotificationsPage")),
    },
    {
      id: "connections-browser",
      label: "Browser",
      blurb: "The browser add-on and computer use.",
      Comp: page(() => import("@/components/settings/pages/BrowserPage")),
    },
    {
      id: "connections-secrets",
      label: "Keys & secrets",
      blurb: "The encrypted vault: values are write-only, never shown back.",
      Comp: page(() => import("@/components/settings/pages/SecretsPage")),
    },
  ],
  memory: [
    {
      id: "memory-profile",
      label: "Your profile",
      blurb: "Who you are and how you want answers written.",
      Comp: page(() => import("@/components/settings/pages/YouPage")),
    },
    {
      id: "memory-train",
      label: "Train on your files",
      blurb: "Your writing, notes, wiki and past conversations, in one place.",
      Comp: page(() => import("@/components/settings/pages/TrainPage")),
    },
  ],
  permissions: [
    {
      id: "permissions-tools",
      label: "Tools & permissions",
      blurb: "What each tool may do without asking, extensions and your own tools.",
      Comp: page(() => import("@/components/settings/pages/ToolsPage")),
    },
    {
      id: "permissions-grants",
      label: "Standing grants",
      blurb: "Approvals you gave that stay in place. You can revoke any of them.",
      Comp: named(() => import("@/components/StandingGrants"), "StandingGrants"),
    },
  ],
  system: [
    {
      id: "system-updates",
      label: "Updates",
      blurb: "What version you are on, what is new, and the restart-to-install switch.",
      Comp: page(() => import("@/components/settings/pages/UpdatesPage")),
    },
    {
      id: "system-maintenance",
      label: "Maintenance",
      blurb: "Back up now, restore, and restart the daemon.",
      Comp: named(() => import("@/components/settings/MaintenanceCard"), "MaintenanceCard"),
    },
    {
      id: "system-token",
      label: "Access token",
      blurb: "The key a browser outside the app uses to reach the daemon.",
      Comp: named(() => import("@/components/settings/DaemonTokenCard"), "DaemonTokenCard"),
    },
  ],
};

function readSection(): { section: string; focus: string } {
  try {
    const p = new URLSearchParams(window.location.search);
    let section = p.get("section") || "";
    if (!section && window.location.hash === "#appearance") section = "appearance";
    return { section, focus: p.get("focus") || "" };
  } catch {
    return { section: "", focus: "" };
  }
}

function matches(def: SchemaSetting, q: string): boolean {
  const hay = [def.key, def.label, def.help ?? "", ...(def.aliases ?? [])].join(" ").toLowerCase();
  return q
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((w) => hay.includes(w));
}

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

export function SettingsHome() {
  const { online, checking, health, refresh } = useDaemon();
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [defs, setDefs] = useState<SchemaSetting[]>([]);
  const [legacy, setLegacy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [original, setOriginal] = useState<Record<string, unknown>>({});
  const [form, setForm] = useState<Record<string, unknown>>({});
  const [active, setActive] = useState("models");
  const [openSub, setOpenSub] = useState<string>("");
  const [focusKey, setFocusKey] = useState("");
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<{ action_id: string; count: number } | null>(null);
  const [error, setError] = useState("");
  const [ledgerKey, setLedgerKey] = useState(0);
  // AUDIT §8 (390 px): on a phone the groups are a drill-down list — the list
  // first, a group's page after a press, "All settings" back.
  const [phoneList, setPhoneList] = useState(true);
  const deviceId = useRef("");

  const load = useCallback(async () => {
    deviceId.current = getDeviceId();
    setLoadError("");
    try {
      const schema = await get<{ groups?: Group[]; settings?: SchemaSetting[] }>("/settings/schema");
      const values = await get<{ values?: Record<string, unknown> }>(
        `/settings/values?device_id=${encodeURIComponent(deviceId.current)}`,
      );
      const list = (schema.settings ?? []).filter((d) => !d.pattern);
      setGroups(schema.groups ?? []);
      setDefs(list);
      setOriginal(values.values ?? {});
      setForm(values.values ?? {});
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setLegacy(true);
      else setLoadError((e as { message?: string })?.message || "Couldn't read the settings.");
    }
  }, []);

  useEffect(() => {
    void load();
    const { section, focus } = readSection();
    if (focus) setFocusKey(focus);
    if (focus || section) setPhoneList(false);
    if (section) {
      const group = section.split("-")[0];
      setActive(group);
      if (section.includes("-")) setOpenSub(section);
    }
  }, [load]);

  // ?focus=<key>: open that setting's group and bring the row into view.
  useEffect(() => {
    if (!focusKey || defs.length === 0) return;
    const def = defs.find((d) => d.key === focusKey);
    if (!def) return;
    setActive(def.group);
    const t = window.setTimeout(() => {
      document.getElementById(`setting-${def.key}`)?.scrollIntoView?.({ block: "center" });
    }, 50);
    return () => window.clearTimeout(t);
  }, [focusKey, defs]);

  const changedKeys = useMemo(
    () => Object.keys(form).filter((k) => !same(form[k], original[k])),
    [form, original],
  );
  const providerOptions = useMemo(() => {
    const rows = (health?.providers ?? []) as { provider?: string; available?: boolean }[];
    return ["auto", ...rows.filter((r) => r.available && r.provider).map((r) => String(r.provider)), "mock"];
  }, [health]);

  async function save() {
    setSaving(true);
    setError("");
    setSaved(null);
    const values = Object.fromEntries(changedKeys.map((k) => [k, form[k]]));
    try {
      const res = await put<{ updated?: string[]; action_id?: string }>("/settings/values", {
        values,
        device_id: deviceId.current,
      });
      setOriginal((o) => ({ ...o, ...values }));
      setSaved({ action_id: res.action_id ?? "", count: (res.updated ?? []).length || changedKeys.length });
      setLedgerKey((k) => k + 1);
      refresh();
    } catch (e) {
      setError((e as { message?: string })?.message || "Couldn't save.");
    } finally {
      setSaving(false);
    }
  }

  async function undoSave() {
    if (!saved?.action_id) return;
    try {
      await post(`/undo/${encodeURIComponent(saved.action_id)}`, {});
      setSaved(null);
      setLedgerKey((k) => k + 1);
      await load();
      refresh();
    } catch (e) {
      setError((e as { message?: string })?.message || "Couldn't undo.");
    }
  }

  if (legacy) return <LegacySettingsForm />;

  const q = query.trim();
  const shown = q ? defs.filter((d) => matches(d, q)) : defs.filter((d) => d.group === active);
  const subsShown = q
    ? Object.values(SUBSECTIONS)
        .flat()
        .filter((s) => [s.label, s.blurb].join(" ").toLowerCase().includes(q.toLowerCase()))
    : SUBSECTIONS[active] ?? [];
  const sections = Array.from(new Set(shown.map((d) => (q ? groups?.find((g) => g.id === d.group)?.label ?? d.group : d.section))));
  const group = groups?.find((g) => g.id === active);

  return (
    <PageShell>
      <Reveal>
        <PageHeader title="Settings" subtitle="Everything you can set, in seven groups. Each can also be changed by asking in chat." />
      </Reveal>
      {!checking && !online && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}
      {loadError && (
        <Reveal>
          <ErrorNote>{loadError}</ErrorNote>
        </Reveal>
      )}
      <Reveal>
        <div className="relative isolate max-w-md">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 z-[1] -translate-y-1/2 text-zinc-500" aria-hidden />
          <label htmlFor="settings-search" className="sr-only">
            Search settings
          </label>
          <input
            id="settings-search"
            data-testid="settings-search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search settings…"
            className="field w-full pl-8"
          />
        </div>
      </Reveal>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 md:grid-cols-[13rem_minmax(0,1fr)]">
        {/* The seven groups. A phone gets a select (no sideways scroll). */}
        <nav aria-label="Settings groups" className="md:sticky md:top-2 md:self-start">
          {phoneList && !q && (
            <ul className="divide-y divide-white/[0.05] rounded-2xl border border-white/[0.06] md:hidden" data-testid="settings-groups-phone">
              {(groups ?? []).map((g) => (
                <li key={g.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setActive(g.id);
                      setPhoneList(false);
                    }}
                    className="flex w-full items-center gap-3 px-4 py-3 text-left"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block text-body-lg text-zinc-100">{g.label}</span>
                      <span className="block text-meta text-zinc-500">{g.description}</span>
                    </span>
                    <ChevronRight size={15} className="shrink-0 text-zinc-500" aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <ul className="hidden space-y-0.5 md:block" data-testid="settings-groups">
            {(groups ?? []).map((g) => (
              <li key={g.id}>
                <button
                  type="button"
                  aria-current={!q && g.id === active ? "page" : undefined}
                  onClick={() => {
                    setActive(g.id);
                    setQuery("");
                  }}
                  className={`w-full rounded-lg px-3 py-2 text-left text-[13px] ${
                    !q && g.id === active ? "bg-accent/[0.08] text-accent-soft" : "text-zinc-300 hover:bg-white/[0.04]"
                  }`}
                >
                  {g.label}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <div
          className={`min-w-0 max-w-[720px] space-y-6 ${phoneList && !q ? "hidden md:block" : ""}`}
          data-testid="settings-group"
          data-group={q ? "search" : active}
        >
          {!q && (
            <button
              type="button"
              onClick={() => setPhoneList(true)}
              className="inline-flex items-center gap-1 text-meta text-zinc-400 hover:text-zinc-200 md:hidden"
            >
              <ChevronLeft size={13} aria-hidden /> All settings
            </button>
          )}
          {!q && group && (
            <div>
              <h2 className="text-lg font-semibold text-zinc-50">{group.label}</h2>
              <p className="text-[13px] text-zinc-400">{group.description}</p>
            </div>
          )}
          {q && shown.length === 0 && subsShown.length === 0 && (
            <p className="text-[13px] text-zinc-400">Nothing matches “{q}”. Ctrl K also searches pages and chats.</p>
          )}
          {groups === null && !loadError ? (
            <LoaderInline label="Loading settings…" />
          ) : (
            sections.map((sec) => {
              const rows = shown.filter((d) => (q ? (groups?.find((g) => g.id === d.group)?.label ?? d.group) : d.section) === sec);
              return (
                <section key={sec || "_"} className="rounded-2xl border border-white/[0.06] bg-white/[0.015]">
                  {sec && <h3 className="px-3 pt-3 text-[12px] font-semibold uppercase tracking-wide text-zinc-500">{sec}</h3>}
                  <div className="divide-y divide-white/[0.04]">
                    {rows.map((d) => (
                      <SettingRow
                        key={d.key}
                        def={d}
                        value={form[d.key]}
                        changed={!same(form[d.key], original[d.key])}
                        highlight={d.key === focusKey}
                        providerOptions={d.key === "default_provider" ? providerOptions : undefined}
                        onChange={(v) => setForm((f) => ({ ...f, [d.key]: v }))}
                      />
                    ))}
                  </div>
                </section>
              );
            })
          )}

          {!q && active === "appearance" && (
            <div id="appearance" data-testid="settings-appearance" className="scroll-mt-20">
              <Card title="Theme" icon={<Palette size={15} />}>
                <p className="mb-3 text-[12px] leading-relaxed text-zinc-500">
                  Pick a theme, or make your own. It changes right away and is remembered on this device.
                </p>
                <ThemeSwitcher variant="drawer" />
                <ThemeMaker />
              </Card>
            </div>
          )}

          {subsShown.map((s) => {
            const open = openSub === s.id;
            const Comp = s.Comp;
            return (
              <section key={s.id} id={s.id} data-testid={`settings-sub-${s.id}`} className="scroll-mt-20 rounded-2xl border border-white/[0.06]">
                <button
                  type="button"
                  aria-expanded={open}
                  onClick={() => setOpenSub(open ? "" : s.id)}
                  className="flex w-full items-center gap-3 px-4 py-3 text-left"
                >
                  {open ? <ChevronDown size={15} className="text-zinc-400" /> : <ChevronRight size={15} className="text-zinc-400" />}
                  <span className="min-w-0 flex-1">
                    <span className="block text-[14px] font-medium text-zinc-100">{s.label}</span>
                    <span className="block text-[12px] text-zinc-500">{s.blurb}</span>
                  </span>
                </button>
                {open && (
                  <div className="border-t border-white/[0.06] p-3">
                    <EmbeddedPage>
                      <Comp />
                    </EmbeddedPage>
                  </div>
                )}
              </section>
            );
          })}

          {!q && active === "permissions" && <LedgerPanel refreshKey={ledgerKey} />}
        </div>
      </div>

      {(changedKeys.length > 0 || saved || error) && (
        <div
          data-testid="settings-save-bar"
          className="sticky bottom-3 z-30 flex flex-wrap items-center gap-3 rounded-2xl border border-white/10 bg-ink-900/95 px-4 py-3 shadow-xl backdrop-blur"
        >
          {changedKeys.length > 0 && (
            <>
              <span className="text-[13px] text-zinc-300">
                {changedKeys.length} unsaved change{changedKeys.length === 1 ? "" : "s"}
              </span>
              <button type="button" onClick={() => void save()} disabled={saving} className="btn-accent btn-sm">
                {saving ? <LoaderInline label="Saving…" /> : "Save changes"}
              </button>
              <button type="button" onClick={() => setForm(original)} className="btn-ghost btn-sm">
                Reset
              </button>
            </>
          )}
          {saved && changedKeys.length === 0 && (
            <span className="flex items-center gap-3">
              <SuccessNote>
                Saved {saved.count} setting{saved.count === 1 ? "" : "s"}.
              </SuccessNote>
              {saved.action_id && (
                <button type="button" onClick={() => void undoSave()} className="btn-ghost btn-sm inline-flex items-center gap-1">
                  <RotateCcw size={12} aria-hidden /> Undo
                </button>
              )}
            </span>
          )}
          {error && <ErrorNote>{error}</ErrorNote>}
        </div>
      )}
    </PageShell>
  );
}
