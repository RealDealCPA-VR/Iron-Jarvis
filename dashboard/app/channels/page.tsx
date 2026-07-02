"use client";

import { useState } from "react";
import { Megaphone, Send, Radio, Plus } from "lucide-react";
import { get, post, del, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import {
  Card,
  Badge,
  Dot,
  OfflineHint,
  Empty,
  SkeletonRows,
  ErrorNote,
  SuccessNote,
  LoaderInline,
  ConfirmButton,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

/** A configured outbound channel. Shape changed from string[] → object list. */
interface ChannelInfo {
  name: string;
  type: string;
}

/** A field the add-form must collect for a given channel type. */
interface ChannelField {
  key: string;
  label: string;
  secret: boolean;
  help?: string;
}

interface ChannelType {
  type: string;
  fields: ChannelField[];
}

interface ChannelResult {
  ok?: boolean;
  detail?: string;
  [k: string]: unknown;
}

/** Built-in channels have no config; deleting them is a server-side no-op. */
const BUILTIN = new Set(["mock", "console"]);

/** Normalize the loose /comm/notify response into per-channel rows. */
function normalize(res: unknown): { name: string; ok: boolean | null; detail: string }[] {
  if (!res || typeof res !== "object") return [];
  return Object.entries(res as Record<string, unknown>).map(([name, v]) => {
    if (v && typeof v === "object") {
      const r = v as ChannelResult;
      return {
        name,
        ok: typeof r.ok === "boolean" ? r.ok : null,
        detail: typeof r.detail === "string" ? r.detail : JSON.stringify(v),
      };
    }
    return { name, ok: null, detail: String(v) };
  });
}

export default function ChannelsPage() {
  const { data, error, loading, reload } = useApi<{ channels: ChannelInfo[] }>("/comm/channels");
  const { data: typesData } = useApi<{ types: ChannelType[] }>("/comm/channel-types");
  const offline = error && error.status === 0;
  const channels = data?.channels ?? [];
  const channelTypes = typesData?.types ?? [];

  /* --- Send test message --------------------------------------------------- */
  const [message, setMessage] = useState("");
  const [channel, setChannel] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [results, setResults] = useState<ReturnType<typeof normalize> | null>(null);

  /* --- Add channel --------------------------------------------------------- */
  const [showAdd, setShowAdd] = useState(false);
  const [addType, setAddType] = useState("");
  const [addName, setAddName] = useState("");
  const [addValues, setAddValues] = useState<Record<string, string>>({});
  const [addBusy, setAddBusy] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [addSuccess, setAddSuccess] = useState<string | null>(null);

  /* --- Delete channel ------------------------------------------------------ */
  const [listError, setListError] = useState<string | null>(null);

  const selectedType = channelTypes.find((t) => t.type === addType);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!message.trim()) return;
    setBusy(true);
    setFormError(null);
    setResults(null);
    try {
      const body: { message: string; channels?: string[] } = { message: message.trim() };
      if (channel) body.channels = [channel];
      const res = await post<unknown>("/comm/notify", body);
      setResults(normalize(res));
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function addChannel(e: React.FormEvent) {
    e.preventDefault();
    const name = addName.trim();
    if (!name || !addType) return;
    setAddBusy(true);
    setAddError(null);
    setAddSuccess(null);
    try {
      // All field values (secret + plain) go into `config` keyed by field.key;
      // the server routes secret fields to the encrypted vault.
      const config: Record<string, string> = {};
      selectedType?.fields.forEach((f) => {
        config[f.key] = addValues[f.key] ?? "";
      });
      await post("/comm/channels", { name, type: addType, config });
      setAddSuccess(`Channel “${name}” added.`);
      setAddName("");
      setAddType("");
      setAddValues({});
      setShowAdd(false);
      reload();
    } catch (err) {
      // Keep the form open so the user can fix a bad name/type.
      setAddError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setAddBusy(false);
    }
  }

  async function deleteChannel(name: string) {
    setListError(null);
    try {
      await del(`/comm/channels/${encodeURIComponent(name)}`);
      reload();
    } catch (err) {
      setListError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Channels"
          subtitle="Outbound notification channels. Add a channel, then send a test message to one or all of them."
          actions={
            <button
              type="button"
              onClick={() => {
                setShowAdd((v) => !v);
                setAddError(null);
                setAddSuccess(null);
              }}
              className={showAdd ? "btn-ghost" : "btn-accent"}
            >
              <Plus size={14} /> Add channel
            </button>
          }
        />
      </Reveal>
      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      {addSuccess && !showAdd && (
        <Reveal>
          <SuccessNote>{addSuccess}</SuccessNote>
        </Reveal>
      )}

      {showAdd && (
        <Reveal>
          <Card title="Add a channel" icon={<Plus size={15} />}>
            <form onSubmit={addChannel} className="space-y-3.5">
              <div>
                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  Type
                </label>
                <select
                  aria-label="Channel type"
                  value={addType}
                  onChange={(e) => {
                    setAddType(e.target.value);
                    setAddValues({});
                  }}
                  className="field"
                >
                  <option value="">Select a type…</option>
                  {channelTypes.map((t) => (
                    <option key={t.type} value={t.type}>
                      {t.type}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  Name
                </label>
                <input
                  type="text"
                  value={addName}
                  onChange={(e) => setAddName(e.target.value)}
                  placeholder="team-alerts"
                  aria-label="Channel name"
                  autoComplete="off"
                  className="field font-mono text-sm"
                />
                <p className="mt-1 text-[11px] leading-relaxed text-zinc-500">
                  A short name you&apos;ll use to send to it, e.g. team-alerts.
                </p>
              </div>

              {selectedType?.fields.map((f) => (
                <div key={f.key}>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    {f.label}
                  </label>
                  <input
                    type={f.secret ? "password" : "text"}
                    value={addValues[f.key] ?? ""}
                    onChange={(e) =>
                      setAddValues((v) => ({ ...v, [f.key]: e.target.value }))
                    }
                    aria-label={f.label}
                    autoComplete="off"
                    className={`field text-sm ${f.secret ? "font-mono" : ""}`}
                  />
                  {f.help && (
                    <p className="mt-1 text-[11px] leading-relaxed text-zinc-500">{f.help}</p>
                  )}
                </div>
              ))}

              <div className="flex items-center gap-2">
                <button
                  type="submit"
                  disabled={addBusy || !addName.trim() || !addType}
                  className="btn-accent"
                >
                  {addBusy ? <LoaderInline label="Adding…" /> : <><Plus size={14} /> Add channel</>}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowAdd(false);
                    setAddError(null);
                  }}
                  className="btn-ghost"
                >
                  Cancel
                </button>
              </div>
              {addError && <ErrorNote>{addError}</ErrorNote>}
            </form>
          </Card>
        </Reveal>
      )}

      <Reveal>
        <div className="grid gap-6 lg:grid-cols-2">
          <Card title={`Configured channels${channels.length ? ` · ${channels.length}` : ""}`} icon={<Radio size={15} />}>
            {loading && !data ? (
              <SkeletonRows rows={3} />
            ) : channels.length === 0 ? (
              <Empty icon={<Megaphone size={22} />}>
                No channels configured yet. Click{" "}
                <span className="font-medium text-accent-soft">Add channel</span> to connect
                Slack, Discord, Telegram, or email.
              </Empty>
            ) : (
              <ul className="space-y-2">
                {channels.map((c) => (
                  <li
                    key={c.name}
                    className="flex items-center justify-between gap-2.5 rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5"
                  >
                    <div className="flex min-w-0 items-center gap-2.5">
                      <Dot on />
                      <span className="truncate font-mono text-sm text-zinc-200">{c.name}</span>
                      {c.type && <Badge value={c.type} tone="cyan" />}
                    </div>
                    {!BUILTIN.has(c.name) && (
                      <ConfirmButton
                        onConfirm={() => deleteChannel(c.name)}
                        title={`Delete channel ${c.name}`}
                      />
                    )}
                  </li>
                ))}
              </ul>
            )}
            {listError && (
              <div className="mt-3">
                <ErrorNote>{listError}</ErrorNote>
              </div>
            )}
          </Card>

          <Card title="Send test message" icon={<Send size={15} />}>
            <form onSubmit={send} className="space-y-3.5">
              <div>
                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  Message
                </label>
                <textarea
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  rows={3}
                  placeholder="Hello from Iron Jarvis…"
                  className="field resize-y"
                />
              </div>
              <div>
                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  Channel
                </label>
                <select aria-label="Channel" value={channel} onChange={(e) => setChannel(e.target.value)} className="field">
                  <option value="">All channels</option>
                  {channels.map((c) => (
                    <option key={c.name} value={c.name}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </div>
              <button type="submit" disabled={busy || !message.trim()} className="btn-accent">
                {busy ? <LoaderInline label="Sending…" /> : <><Send size={14} /> Send</>}
              </button>
              {formError && <ErrorNote>{formError}</ErrorNote>}
            </form>

            {results && (
              <div className="mt-4 space-y-2">
                <div className="text-[11px] uppercase tracking-[0.1em] text-zinc-400">Result</div>
                {results.length === 0 ? (
                  <Empty>No channel responses.</Empty>
                ) : (
                  results.map((r) => (
                    <div
                      key={r.name}
                      className="flex items-start justify-between gap-3 rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5"
                    >
                      <div className="min-w-0">
                        <span className="font-mono text-sm text-zinc-200">{r.name}</span>
                        <div className="truncate text-xs text-zinc-500">{r.detail}</div>
                      </div>
                      {r.ok === null ? (
                        <Badge value="sent" tone="slate" />
                      ) : (
                        <Badge value={r.ok ? "ok" : "failed"} tone={r.ok ? "green" : "red"} />
                      )}
                    </div>
                  ))
                )}
              </div>
            )}
          </Card>
        </div>
      </Reveal>
    </PageShell>
  );
}
