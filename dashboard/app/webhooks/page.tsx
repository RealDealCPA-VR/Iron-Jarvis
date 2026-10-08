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
import { API_BASE, post, del, ApiError } from "@/lib/api";
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
  ConfirmButton,
  Code,
} from "@/components/ui";
import Link from "next/link";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { CopyIconButton } from "@/components/Markdown";

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

/** v1.314.0: "only this computer" is TRUE only for a loopback daemon —
 *  API_BASE can point at a deployed daemon (NEXT_PUBLIC_IJ_API), so the note
 *  is decided from the host, never assumed. */
function isLoopbackBase(base: string): boolean {
  try {
    const host = new URL(base).hostname.replace(/^\[|\]$/g, "").toLowerCase();
    return host === "127.0.0.1" || host === "localhost" || host === "::1";
  } catch {
    return false;
  }
}

/** v1.314.0 (review fix): the empty state's examples must be TRUE for this
 *  install. A loopback daemon (the default: it binds 127.0.0.1 and
 *  daemon/auth.py refuses a non-loopback Host) cannot be reached by GitHub's
 *  servers or a hosted form, so those senders are offered only when the
 *  address is NOT loopback. An inbound webhook also never starts a task by
 *  itself (routes/comm.py only publishes webhook.received and fires bound
 *  reflexes), so the inbound example names the reflex. Outbound posts to any
 *  URL, so the Zapier example is true everywhere. */
function webhookExamples(loopback: boolean): string[] {
  const inbound = loopback
    ? ["A script or app on this PC tells Iron Jarvis something happened (a reflex decides what to do)"]
    : [
        "A web form tells Iron Jarvis when someone fills it in (a reflex decides what to do)",
        "GitHub tells Iron Jarvis when code is pushed",
      ];
  return [...inbound, "Iron Jarvis tells Zapier when a task finishes"];
}

/** Quick-add chips for a sending webhook: real event names (core/events.py)
 *  that APPEND to the free-text box, which stays visible (v1.314.0). */
const EVENT_CHIPS: { event: string; label: string }[] = [
  { event: "session.completed", label: "A task finishes" },
  { event: "workflow.completed", label: "A workflow finishes" },
];

export default function WebhooksPage() {
  const { data, error, loading, reload } = useApi<{ webhooks: Webhook[] }>("/webhooks");
  const offline = error && error.status === 0;
  const webhooks = data?.webhooks ?? [];
  const loopback = isLoopbackBase(API_BASE);

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
  const [listError, setListError] = useState<string | null>(null);

  // v1.292.0 (platform-06): an outbound webhook with NO event types would
  // match nothing (the server delivers only listed types) -- the form used to
  // say "Leave blank for all events". Same sentence the daemon answers with.
  const EMPTY_EVENTS_HINT =
    "Pick at least one event type for an outbound webhook, for example session.completed. With none listed, nothing would ever be sent.";

  async function remove(slugToRemove: string) {
    setListError(null);
    try {
      await del(`/webhooks/${encodeURIComponent(slugToRemove)}`);
      reload();
    } catch (err) {
      setListError(err instanceof ApiError ? err.message : String(err));
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!slug.trim()) return;
    if (direction === "outbound" && !targetUrl.trim()) {
      setFormError("Outbound webhooks need a target URL.");
      return;
    }
    const event_types = events
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (direction === "outbound" && event_types.length === 0) {
      setFormError(EMPTY_EVENTS_HINT);
      return;
    }
    setBusy(true);
    setFormError(null);
    setOk(null);
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
          ? `Ready. Other apps can now send events to ${API_BASE}/webhooks/${slug.trim()}`
          : `Webhook "${slug.trim()}" added. Iron Jarvis will tell that app when those events happen.`,
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
          subtitle="Let other apps tell Iron Jarvis something happened (a reflex decides what to do), or let Iron Jarvis tell other apps when work finishes."
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
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Name (used in the address)
                  </label>
                  <input
                    value={slug}
                    onChange={(e) => setSlug(e.target.value)}
                    placeholder="github-push"
                    className="field font-mono"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Direction
                  </label>
                  <select
                    aria-label="Direction"
                    value={direction}
                    onChange={(e) => setDirection(e.target.value as Direction)}
                    className="field"
                  >
                    {/* Values stay inbound/outbound — only the words moved (v1.314.0). */}
                    <option value="inbound">Receive — another app tells Iron Jarvis something happened</option>
                    <option value="outbound">Send — tell another app when something finishes</option>
                  </select>
                </div>
              </div>

              {direction === "outbound" && (
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
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
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Event types
                  </label>
                  <input
                    value={events}
                    onChange={(e) => setEvents(e.target.value)}
                    placeholder="session.completed, workflow.completed"
                    className="field font-mono"
                  />
                  {direction === "outbound" && (
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      {EVENT_CHIPS.map((c) => {
                        const listed = events
                          .split(",")
                          .map((x) => x.trim())
                          .includes(c.event);
                        return (
                          <button
                            key={c.event}
                            type="button"
                            disabled={listed}
                            title={c.event}
                            onClick={() =>
                              setEvents((v) => {
                                const t = v.trim().replace(/,$/, "");
                                return t ? `${t}, ${c.event}` : c.event;
                              })
                            }
                            className="rounded-full border border-white/10 px-2 py-0.5 text-[11px] text-zinc-300 transition-colors hover:border-accent/40 hover:text-accent-soft disabled:opacity-40"
                          >
                            + {c.label}
                          </button>
                        );
                      })}
                    </div>
                  )}
                  <div className="mt-1 text-[11px] text-zinc-600">
                    {direction === "outbound"
                      ? "Comma-separated. A sending webhook only sends the event types listed here, so pick at least one."
                      : "Comma-separated. Not needed when another app sends events here."}
                  </div>
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
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
                  The other app sends to:{" "}
                  {/* <Code> (v1.313.0): a theme-true chip, not a literal black one. */}
                  <Code className="text-accent-soft">
                    {API_BASE}/webhooks/{slug.trim()}
                  </Code>
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
        {/* v1.314.0: lead with what a webhook is FOR; the address and the
            HTTP detail follow, and most people are pointed to Reflexes. */}
        <Card>
          <div className="space-y-2 text-sm text-zinc-400">
            {/* The empty state below teaches the same idea, so the lead
                sentence shows only once there is a list to read. */}
            {webhooks.length > 0 && (
              <p>
                {loopback
                  ? "A webhook is an address other apps on this computer can call to tell Iron Jarvis something happened"
                  : "A webhook is an address other apps (a form, Zapier, GitHub) can call to tell Iron Jarvis something happened"}{" "}
                — or one Iron Jarvis calls to tell another app when work finishes.
              </p>
            )}
            <p className="text-[12px] text-zinc-500">
              Other apps send events (as a POST request) to{" "}
              <Code className="text-accent-soft">{API_BASE}/webhooks/&lt;name&gt;</Code>.
              {loopback && " Only programs on this computer can reach this address."}
            </p>
            <p className="text-[12px] text-zinc-500">
              To decide what happens when one arrives,{" "}
              <Link href="/reflex" className="text-accent-soft underline-offset-2 hover:underline">
                add a reflex →
              </Link>
            </p>
          </div>
        </Card>
      </Reveal>

      <Reveal>
        <Card title={`Your webhooks${webhooks.length ? ` · ${webhooks.length}` : ""}`} icon={<WebhookIcon size={15} />}>
          {listError && <ErrorNote>{listError}</ErrorNote>}
          {loading && !data ? (
            <SkeletonRows rows={4} />
          ) : webhooks.length === 0 ? (
            // v1.314.0: teach + open the page's OWN form (setOpen(true), never
            // a toggle). The label must not read "Add webhook": that name
            // belongs to the header button (webhooks-manage-v1292).
            <Empty
              icon={<WebhookIcon size={24} />}
              title="Connect another app"
              examples={webhookExamples(loopback)}
              action={{ label: "Add your first webhook", onClick: () => setOpen(true) }}
              secondary={{ label: "Most people want Reflexes instead →", href: "/reflex" }}
            >
              A webhook lets another app tell Iron Jarvis something happened (a reflex decides what
              to do about it), or lets Iron Jarvis tell another app when work finishes.
            </Empty>
          ) : (
            <div className="-mx-1 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b hairline text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    <th className="px-2 py-2.5 font-medium">Name</th>
                    <th className="px-2 py-2.5 font-medium">Direction</th>
                    <th className="px-2 py-2.5 font-medium">Address</th>
                    <th className="px-2 py-2.5 font-medium">Event types</th>
                    <th className="px-2 py-2.5 font-medium">Enabled</th>
                    <th className="px-2 py-2.5 font-medium"></th>
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
                            <span className="inline-flex max-w-full items-center gap-1">
                              <code
                                className="truncate font-mono text-[11px] text-zinc-400"
                                title={`POST ${API_BASE}/webhooks/${w.slug}`}
                              >
                                {API_BASE}/webhooks/{w.slug}
                              </code>
                              <CopyIconButton
                                text={`${API_BASE}/webhooks/${w.slug}`}
                                title="Copy address"
                              />
                            </span>
                          ) : (
                            <span className="block truncate font-mono text-[11px] text-zinc-400" title={w.target_url ?? ""}>
                              {w.target_url || "—"}
                            </span>
                          )}
                        </td>
                        <td className="px-2 py-2.5">
                          {evs.length === 0 ? (
                            // An outbound row with no types was saved by an
                            // older build; it sends nothing -- say so, never "all".
                            <span
                              className={inbound ? "text-zinc-600" : "text-amber-300/80"}
                              title={inbound ? undefined : "Remove it and add it again with at least one event type."}
                            >
                              {inbound ? "—" : "none — nothing is sent"}
                            </span>
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
                        <td className="px-2 py-2.5 text-right">
                          <ConfirmButton
                            label="Remove"
                            onConfirm={() => remove(w.slug)}
                            title={`Remove webhook ${w.slug}`}
                          />
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
