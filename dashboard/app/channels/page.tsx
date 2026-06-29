"use client";

import { useState } from "react";
import { Megaphone, Send, Radio } from "lucide-react";
import { get, post, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import {
  Card,
  Badge,
  Dot,
  OfflineHint,
  Empty,
  SkeletonRows,
  ErrorNote,
  LoaderInline,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

interface ChannelResult {
  ok?: boolean;
  detail?: string;
  [k: string]: unknown;
}

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
  const { data, error, loading } = useApi<{ channels: string[] }>("/comm/channels");
  const offline = error && error.status === 0;
  const channels = data?.channels ?? [];

  const [message, setMessage] = useState("");
  const [channel, setChannel] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [results, setResults] = useState<ReturnType<typeof normalize> | null>(null);

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

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Channels"
          subtitle="Outbound notification channels. Send a test message to one or all configured channels."
        />
      </Reveal>
      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <Reveal>
        <div className="grid gap-6 lg:grid-cols-2">
          <Card title={`Configured channels${channels.length ? ` · ${channels.length}` : ""}`} icon={<Radio size={15} />}>
            {loading && !data ? (
              <SkeletonRows rows={3} />
            ) : channels.length === 0 ? (
              <Empty icon={<Megaphone size={22} />}>
                No channels configured. Channels (Slack / Telegram / Discord) are
                set up in the <span className="font-mono text-zinc-400">[comm]</span>{" "}
                section of <span className="font-mono text-zinc-400">.ironjarvis/config.toml</span>,
                then take effect on the next daemon restart.
              </Empty>
            ) : (
              <ul className="space-y-2">
                {channels.map((c) => (
                  <li
                    key={c}
                    className="flex items-center gap-2.5 rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5"
                  >
                    <Dot on />
                    <span className="font-mono text-sm text-zinc-200">{c}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Send test message" icon={<Send size={15} />}>
            <form onSubmit={send} className="space-y-3.5">
              <div>
                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-500">
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
                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                  Channel
                </label>
                <select value={channel} onChange={(e) => setChannel(e.target.value)} className="field">
                  <option value="">All channels</option>
                  {channels.map((c) => (
                    <option key={c} value={c}>
                      {c}
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
                <div className="text-[11px] uppercase tracking-[0.1em] text-zinc-500">Result</div>
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
