"use client";

import { useState } from "react";
import {
  Webhook as WebhookIcon,
  ArrowDownLeft,
  ArrowUpRight,
  Plus,
  X,
} from "lucide-react";
import { useApi } from "@/lib/useApi";
import { API_BASE, post, ApiError } from "@/lib/api";
import type { Webhook } from "@/lib/types";
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
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

/** Webhook records may carry event types as a JSON string or an array. */
function eventTypes(w: Webhook): string[] {
  const raw = w.event_types_json ?? (w as Record<string, unknown>).event_types;
  if (Array.isArray(raw)) return raw.map(String);
  if (typeof raw === "string" && raw.trim()) {
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed.map(String);
    } catch {
      return raw.split(",").map((s) => s.trim()).filter(Boolean);
    }
  }
  return [];
}

type Direction = "inbound" | "outbound";

export default function WebhooksPage() {
  const { data, error, loading, reload } = useApi<{ webhooks: Webhook[] }>("/webhooks");
  const offline = error && error.status === 0;
  const webhooks = data?.webhooks ?? [];

  // Add form
  const [open, setOpen] = useState(false);
  const [slug, setSlug] = useState("");
  const [direction, setDirection] = useState<Direction>("inbound");
  const [targetUrl, setTargetUrl] = useState("");
  const [events, setEvents] = useState("");
  const [secretName, setSecretName] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!slug.trim()) return;
    if (direction === "outbound" && !targetUrl.trim()) {
      setFormError("Outbound webhooks need a target URL.");
      return;
    }
    setBusy(true);
    setFormError(null);
    setOk(null);
    const event_types = events
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    try {
      await post("/webhooks", {
        slug: slug.trim(),
        direction,
        target_url: direction === "outbound" ? targetUrl.trim() : "",
        event_types,
        secret_name: secretName.trim(),
      });
      setOk(
        direction === "inbound"
          ? `Inbound webhook ready: POST ${API_BASE}/webhooks/${slug.trim()}`
          : `Outbound webhook "${slug.trim()}" registered.`,
      );
      setSlug("");
      setTargetUrl("");
      setEvents("");
      setSecretName("");
      setDirection("inbound");
      reload();
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
          title="Webhooks"
          subtitle="Inbound and outbound webhook registrations."
          actions={
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="btn-accent"
            >
              <Plus size={14} /> Add webhook
            </button>
          }
        />
      </Reveal>
      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      {open && (
        <Reveal>
          <Card title="Add webhook" icon={<Plus size={15} />}>
            <form onSubmit={submit} className="space-y-3.5">
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                    Slug
                  </label>
                  <input
                    value={slug}
                    onChange={(e) => setSlug(e.target.value)}
                    placeholder="github-push"
                    className="field font-mono"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                    Direction
                  </label>
                  <select
                    value={direction}
                    onChange={(e) => setDirection(e.target.value as Direction)}
                    className="field"
                  >
                    <option value="inbound">Inbound (receive events)</option>
                    <option value="outbound">Outbound (send events)</option>
                  </select>
                </div>
              </div>

              {direction === "outbound" && (
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                    Target URL
                  </label>
                  <input
                    value={targetUrl}
                    onChange={(e) => setTargetUrl(e.target.value)}
                    placeholder="https://example.com/hook"
                    className="field font-mono"
                  />
                </div>
              )}

              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                    Event types
                  </label>
                  <input
                    value={events}
                    onChange={(e) => setEvents(e.target.value)}
                    placeholder="session.completed, workflow.completed"
                    className="field font-mono"
                  />
                  <div className="mt-1 text-[11px] text-zinc-600">
                    Comma-separated. Leave blank for all events.
                  </div>
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                    Secret name (optional)
                  </label>
                  <input
                    value={secretName}
                    onChange={(e) => setSecretName(e.target.value)}
                    placeholder="name of a stored secret"
                    className="field"
                  />
                </div>
              </div>

              {direction === "inbound" && slug.trim() && (
                <div className="text-[11px] text-zinc-500">
                  Trigger URL:{" "}
                  <code className="rounded bg-black/40 px-1.5 py-0.5 font-mono text-accent-soft">
                    POST {API_BASE}/webhooks/{slug.trim()}
                  </code>
                </div>
              )}

              <div className="flex items-center gap-2">
                <button
                  type="submit"
                  disabled={busy || !slug.trim()}
                  className="btn-accent"
                >
                  {busy ? <LoaderInline label="Adding…" /> : <><Plus size={14} /> Add webhook</>}
                </button>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 px-3 py-2 text-sm text-zinc-400 transition-colors hover:border-white/20 hover:text-zinc-200"
                >
                  <X size={14} /> Cancel
                </button>
              </div>
              {ok && <SuccessNote>{ok}</SuccessNote>}
              {formError && <ErrorNote>{formError}</ErrorNote>}
            </form>
          </Card>
        </Reveal>
      )}

      <Reveal>
        <Card>
          <div className="text-sm text-zinc-400">
            Inbound webhooks accept events at{" "}
            <code className="rounded bg-black/40 px-1.5 py-0.5 font-mono text-xs text-accent-soft">
              POST {API_BASE}/webhooks/&#123;slug&#125;
            </code>
            . Outbound webhooks POST registered events to their target URL.
          </div>
        </Card>
      </Reveal>

      <Reveal>
        <Card title={`Registrations${webhooks.length ? ` · ${webhooks.length}` : ""}`} icon={<WebhookIcon size={15} />}>
          {loading && !data ? (
            <SkeletonRows rows={4} />
          ) : webhooks.length === 0 ? (
            <Empty icon={<WebhookIcon size={24} />}>
              No webhooks registered — use “Add webhook” to create one.
            </Empty>
          ) : (
            <div className="-mx-1 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b hairline text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                    <th className="px-2 py-2.5 font-medium">Slug</th>
                    <th className="px-2 py-2.5 font-medium">Direction</th>
                    <th className="px-2 py-2.5 font-medium">Target / URL</th>
                    <th className="px-2 py-2.5 font-medium">Event types</th>
                    <th className="px-2 py-2.5 font-medium">Enabled</th>
                  </tr>
                </thead>
                <tbody>
                  {webhooks.map((w) => {
                    const inbound = (w.direction ?? "").toLowerCase() === "inbound";
                    const evs = eventTypes(w);
                    return (
                      <tr
                        key={w.slug}
                        className="border-b border-white/[0.04] align-top last:border-0 hover:bg-white/[0.02]"
                      >
                        <td className="px-2 py-2.5 font-mono text-zinc-100">{w.slug}</td>
                        <td className="px-2 py-2.5">
                          <span className="inline-flex items-center gap-1.5">
                            {inbound ? (
                              <ArrowDownLeft size={13} className="text-accent-soft" />
                            ) : (
                              <ArrowUpRight size={13} className="text-violet-300" />
                            )}
                            <Badge value={w.direction || "—"} tone={inbound ? "cyan" : "violet"} />
                          </span>
                        </td>
                        <td className="max-w-xs px-2 py-2.5">
                          {inbound ? (
                            <code className="font-mono text-[11px] text-zinc-400">
                              POST /webhooks/{w.slug}
                            </code>
                          ) : (
                            <span className="block truncate font-mono text-[11px] text-zinc-400" title={w.target_url ?? ""}>
                              {w.target_url || "—"}
                            </span>
                          )}
                        </td>
                        <td className="px-2 py-2.5">
                          {evs.length === 0 ? (
                            <span className="text-zinc-600">all</span>
                          ) : (
                            <div className="flex flex-wrap gap-1">
                              {evs.map((ev) => (
                                <span
                                  key={ev}
                                  className="rounded-md border border-white/10 bg-white/[0.03] px-1.5 py-0.5 font-mono text-[10px] text-zinc-300"
                                >
                                  {ev}
                                </span>
                              ))}
                            </div>
                          )}
                        </td>
                        <td className="px-2 py-2.5">
                          <Dot on={!!w.enabled} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </Reveal>
    </PageShell>
  );
}
