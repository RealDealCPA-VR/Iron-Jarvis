"use client";

import { useEffect, useRef, useState } from "react";
import {
  Search,
  Megaphone,
  Send,
  Radio,
  Plus,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  FileCode2,
  Info,
  Pencil,
  Lightbulb,
} from "lucide-react";
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
import { timeAgo } from "@/lib/format";
import { PageShell, Reveal } from "@/components/motion";
import { ChooserTiles } from "@/components/ChooserTiles";
import { useFocusRef } from "@/lib/useFocusRef";

/** A configured outbound channel. Shape changed from string[] → object list. */
interface ChannelInfo {
  name: string;
  type: string;
  /** No config row — mock/console/this-pc; ready by definition. */
  builtin?: boolean;
  /** The LAST REAL test result, persisted: green provably means "delivered". */
  last_test_ok?: boolean | null;
  last_test_at?: string | null;
  events?: string[];
  /** Two-way (v1.136.0): the destination also LISTENS for messages. */
  inbound_enabled?: boolean;
  /** Full chat from this destination — replies mirror to the desktop Chat page. */
  chat_enabled?: boolean;
  /** How many sender ids the fail-closed allowlist holds (0 = nobody). */
  allowed_senders_count?: number;
}

/** A field the add-form must collect for a given channel type. */
interface ChannelField {
  key: string;
  label: string;
  secret: boolean;
  help?: string;
  /** Optional server hint; "bool" renders as a real toggle (v1.136.0). */
  type?: string;
}

interface ChannelType {
  type: string;
  fields: ChannelField[];
  /** One-paste app manifest (JSON) for types that support it (slack); null otherwise. */
  manifest?: string | null;
  /** Human instructions for where to paste the manifest. */
  manifest_help?: string | null;
}

interface ChannelResult {
  ok?: boolean;
  detail?: string;
  [k: string]: unknown;
}

/** POST /comm/channels/{name}/test — sends a REAL message through the channel. */
interface ChannelTestResult {
  name: string;
  ok: boolean;
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

/** A short, actionable fix for a failed channel test, from the daemon's detail. */
function tipFor(detail?: string): string {
  const d = (detail || "").toLowerCase();
  if (d.includes("delivery method") || d.includes("webhook_url") || d.includes("token_secret"))
    return "add an Incoming Webhook URL, or a bot token + a channel, then re-test.";
  if (d.includes("did not resolve") || d.includes("token secret"))
    return "the saved token is missing — re-enter it via Edit.";
  if (d.includes("chat_id")) return "add your Chat ID via Edit.";
  if (d.includes("host") || d.includes("from_addr") || d.includes("to_addr"))
    return "fill the SMTP host and addresses via Edit.";
  return "open Edit and re-check the channel's details.";
}

/** Tile metadata per addable type (v1.118.0): what you have, what it asks
 * for, honest effort — easiest first. The built-in "This PC" is not here
 * because it needs nothing; the list card shows it pre-connected instead. */
const DEST_META: Record<
  string,
  { label: string; blurb: string; needs: string; effort: "quickest" | "easy" | "needs a token" | "technical"; order: number }
> = {
  telegram: {
    label: "Telegram",
    blurb: "Alerts on your phone, via a bot you own.",
    needs: "a bot token from @BotFather — your chat is detected automatically",
    effort: "quickest",
    order: 1,
  },
  slack: {
    label: "Slack",
    blurb: "Messages into a channel your team watches.",
    needs: "one Incoming Webhook URL (two-way setup is optional, under Advanced)",
    effort: "easy",
    order: 2,
  },
  discord: {
    label: "Discord",
    blurb: "Messages into a server channel.",
    needs: "one webhook URL (channel settings → Integrations → Webhooks)",
    effort: "easy",
    order: 3,
  },
  email: {
    label: "Email",
    blurb: "Plain email to any address you choose.",
    needs: "SMTP details (host, from, to)",
    effort: "technical",
    order: 4,
  },
};

/** Which fields form the EASY path per type; everything else is Advanced.
 * null = no split (email is all-primary — it is honest about being technical). */
const PRIMARY_KEYS: Record<string, string[] | null> = {
  slack: ["webhook_url"],
  discord: ["webhook_url"],
  telegram: ["token", "chat_id"],
  email: null,
};

/** Friendly names for the per-destination event routing checkboxes (N5). */
const EVENT_LABELS: Array<{ key: string; label: string }> = [
  { key: "review.requested", label: "A review needs you" },
  { key: "approval.requested", label: "An agent asks permission to use a tool" },
  { key: "session.completed", label: "A task finished" },
  { key: "workflow.completed", label: "A workflow finished" },
  { key: "provider.failed", label: "A model failed" },
  { key: "provider.failover", label: "A model failed over" },
  { key: "autonomy.executed", label: "Autonomy acted on its own" },
  { key: "skill.proposal_created", label: "Skill suggestions" },
  // Goal news (v1.209.0) — the three DECISIONS a standing goal can announce.
  // Routine iteration heartbeats are deliberately not offered: they are not in
  // DEFAULT_ALERT_EVENTS either (comm/notifier.py), so a checkbox for them
  // would promise a message the server never sends.
  { key: "goal.satisfied", label: "A goal was achieved" },
  { key: "goal.tripped", label: "A goal hit its safety breaker" },
  { key: "goal.iteration_refused", label: "A goal run was held back (budget or state)" },
];

/** What the user READS on the telegram two-way fields (v1.136.0). The server's
 * own labels stay wire-honest ("true/false"); the screen speaks plainly. The
 * chat toggle's exact wording is the vocabulary decision from the messaging
 * plan: two-way is a per-destination upgrade, not a new noun. */
const TELEGRAM_FIELD_COPY: Record<string, { label?: string; help?: string }> = {
  chat_enabled: {
    label: "Chat with Iron Jarvis from this destination",
    help:
      "Message your bot and talk to the full Iron Jarvis. Replies land on your phone AND in a shared conversation on the desktop Chat page. Needs the allowed senders list — it fails closed, so an empty list allows nobody.",
  },
  inbound_enabled: {
    label: "Listen for incoming messages",
    help:
      "Let Iron Jarvis read messages sent to this bot — required for commands and for chat.",
  },
  allowed_senders: {
    label: "Allowed senders",
    help:
      "Comma-separated Telegram user ids allowed to talk to Iron Jarvis. Fails closed — empty allows nobody. In a private chat with your bot, the detected chat ID above is usually also your user id.",
  },
};

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
  // Non-null when the add form is reconfiguring an EXISTING channel (Edit). The
  // add endpoint replaces on the same name, so Edit = re-submit through it.
  const [editing, setEditing] = useState<string | null>(null);
  // Advanced fields collapsed by default (v1.118.0): Slack used to present
  // NINE fields as equals; the easy path is one webhook URL.
  const [advOpen, setAdvOpen] = useState(false);
  // Per-destination event routing (N5). Full set = "everything" = omitted on
  // the wire, which is the pre-v1.118 behaviour for every existing channel.
  const [addEvents, setAddEvents] = useState<string[]>(EVENT_LABELS.map((e) => e.key));
  // Telegram chat-id auto-detect (N3): poll getUpdates while the user goes and
  // messages their bot — the classic "what is my chat id" wall, removed.
  const [detecting, setDetecting] = useState(false);
  const [detectErr, setDetectErr] = useState<string | null>(null);
  const [detectChats, setDetectChats] = useState<Array<{ id: number; label: string }>>([]);
  const detectTimerRef = useRef<number | null>(null);

  function stopDetect() {
    if (detectTimerRef.current) window.clearInterval(detectTimerRef.current);
    detectTimerRef.current = null;
    setDetecting(false);
  }
  useEffect(() => () => stopDetect(), []); // never leak the poller

  function startDetect() {
    const token = (addValues["token"] ?? "").trim();
    if (!token || detecting) return;
    setDetectErr(null);
    setDetectChats([]);
    setDetecting(true);
    const poll = async () => {
      try {
        const r = await post<{ chats: Array<{ id: number; label: string }> }>(
          "/comm/telegram/detect-chat",
          { token },
        );
        if (r.chats.length === 1) {
          // One chat answered — that's you. Fill and stop.
          setAddValues((v) => ({ ...v, chat_id: String(r.chats[0].id) }));
          stopDetect();
        } else if (r.chats.length > 1) {
          setDetectChats(r.chats); // several chats — let the user pick
          stopDetect();
        }
      } catch (err) {
        // A bad token is an answer, not a retry loop.
        setDetectErr(err instanceof ApiError ? err.message : String(err));
        stopDetect();
      }
    };
    void poll();
    detectTimerRef.current = window.setInterval(() => void poll(), 2500);
    // Watching an unmessaged bot forever helps nobody — give up after 90s.
    window.setTimeout(() => stopDetect(), 90_000);
  }

  // ?focus=add (the global search's deep link) opens the form directly.
  const addFocusRef = useFocusRef<HTMLDivElement>("add");
  useEffect(() => {
    try {
      if (new URLSearchParams(window.location.search).get("focus") === "add")
        setShowAdd(true);
    } catch {
      /* malformed URL — ignore */
    }
  }, []);

  /* --- One-paste app manifest (slack) --------------------------------------- */
  // Open by default — the one-paste manifest is the EASIEST setup path and was
  // getting missed when tucked behind a collapsed toggle.
  const [manifestOpen, setManifestOpen] = useState(true);
  const [manifestCopied, setManifestCopied] = useState(false);

  /* --- Delete channel ------------------------------------------------------ */
  const [listError, setListError] = useState<string | null>(null);

  /* --- Per-channel test (real delivery) ------------------------------------ */
  const [testBusy, setTestBusy] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{
    name: string;
    ok: boolean;
    detail?: string;
  } | null>(null);

  async function testChannel(name: string) {
    setTestBusy(name);
    setTestResult(null);
    setListError(null);
    try {
      const res = await post<ChannelTestResult>(
        `/comm/channels/${encodeURIComponent(name)}/test`,
      );
      setTestResult({ name, ok: res.ok, detail: res.detail });
    } catch (err) {
      // Honest failure: surface the daemon's detail instead of pretending.
      setTestResult({
        name,
        ok: false,
        detail: err instanceof ApiError ? err.message : String(err),
      });
    } finally {
      setTestBusy(null);
    }
  }

  const selectedType = channelTypes.find((t) => t.type === addType);

  async function copyManifest() {
    const manifest = selectedType?.manifest;
    if (!manifest) return;
    try {
      await navigator.clipboard.writeText(manifest);
      setManifestCopied(true);
      window.setTimeout(() => setManifestCopied(false), 2000);
    } catch {
      /* clipboard blocked — the YAML is still selectable in the <pre> */
    }
  }

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
      const config: Record<string, string | string[]> = {};
      selectedType?.fields.forEach((f) => {
        config[f.key] = addValues[f.key] ?? "";
      });
      // Full set = everything = omit (the server treats absent as all).
      if (addEvents.length && addEvents.length < EVENT_LABELS.length)
        config["events"] = addEvents;
      await post("/comm/channels", { name, type: addType, config });
      // ADDING IS TESTING (v1.118.0): a destination that only fails later, the
      // night it mattered, is the exact trap this flow exists to remove. Send
      // the real test now; green means delivered, red keeps the form open with
      // the actionable fix.
      let tested: { ok: boolean; detail?: string };
      try {
        tested = await post<ChannelTestResult>(
          `/comm/channels/${encodeURIComponent(name)}/test`,
        );
      } catch (err) {
        tested = {
          ok: false,
          detail: err instanceof ApiError ? err.message : String(err),
        };
      }
      reload();
      if (!tested.ok) {
        setAddError(
          `Saved, but the test message did not deliver — ${tested.detail ?? "no detail"}. ` +
            `Fix: ${tipFor(tested.detail)}`,
        );
        return; // form stays open; the row already shows its red state
      }
      setAddSuccess(
        `“${name}” ${editing ? "updated" : "added"} — test message delivered ✓`,
      );
      setAddName("");
      setAddType("");
      setAddValues({});
      setAddEvents(EVENT_LABELS.map((e) => e.key));
      setAdvOpen(false);
      setShowAdd(false);
      setEditing(null);
    } catch (err) {
      // Keep the form open so the user can fix a bad name/type/config — the
      // daemon's detail carries the actionable tip (e.g. Slack needs a webhook).
      setAddError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setAddBusy(false);
    }
  }

  /** Open the form to RECONFIGURE an existing channel (name + type locked). */
  function openEdit(c: ChannelInfo) {
    setEditing(c.name);
    setAddType(c.type);
    setAddName(c.name);
    // Seed the two-way TOGGLES from the row's live state (v1.136.0): the add
    // endpoint replaces config wholesale and drops blank fields, so an edit
    // that opened with the toggles blank would silently switch two-way OFF —
    // and a toggle that reads as the setting must BE the setting (v1.127.0).
    // Older daemons omit these GET fields; then this seeds nothing (status quo).
    const seed: Record<string, string> = {};
    if (typeof c.inbound_enabled === "boolean")
      seed.inbound_enabled = String(c.inbound_enabled);
    if (typeof c.chat_enabled === "boolean")
      seed.chat_enabled = String(c.chat_enabled);
    setAddValues(seed);
    setShowAdd(true);
    setAddError(null);
    setAddSuccess(null);
    setManifestOpen(c.type === "slack");
    setManifestCopied(false);
  }
  function openEditByName(name: string) {
    const c = channels.find((x) => x.name === name);
    if (c) openEdit(c);
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
          title="Notifications"
          subtitle="Where Iron Jarvis sends alerts. Add a destination, then send a test message to one or all of them."
          actions={
            <button
              type="button"
              onClick={() => {
                // Always open a FRESH add (never inherit an in-progress edit).
                if (editing) {
                  setEditing(null);
                  setAddType("");
                  setAddName("");
                  setAddValues({});
                  setShowAdd(true);
                } else {
                  setShowAdd((v) => !v);
                }
                setAddError(null);
                setAddSuccess(null);
              }}
              className={showAdd && !editing ? "btn-ghost" : "btn-accent"}
            >
              <Plus size={14} /> Add destination
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
          <div ref={addFocusRef}>
          <Card
            title={editing ? `Edit “${editing}”` : "Add a destination"}
            icon={editing ? <Pencil size={15} /> : <Plus size={15} />}
          >
            <form onSubmit={addChannel} className="space-y-3.5">
              {editing && (
                <div className="rounded-xl border border-accent/20 bg-accent/[0.05] px-3.5 py-2.5 text-[12px] leading-relaxed text-zinc-300">
                  Re-enter the details to update{" "}
                  <span className="font-mono text-accent-soft">{editing}</span>. For
                  your security, saved secrets aren&apos;t shown — re-paste the ones
                  you&apos;re setting (Slack: a webhook URL, or a bot token + a
                  channel).
                  {selectedType?.fields.some((f) => f.key === "allowed_senders") && (
                    <>
                      {" "}
                      The allowed senders list starts blank here too — re-enter
                      it under Advanced, or saving clears it (it fails closed:
                      empty allows nobody).
                    </>
                  )}
                </div>
              )}
              {editing ? (
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Type
                  </label>
                  <Badge value={DEST_META[addType]?.label ?? addType} tone="cyan" />
                </div>
              ) : (
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Where should alerts go?
                  </label>
                  <ChooserTiles
                    ariaLabel="Destination type"
                    value={addType}
                    onChange={(key: string) => {
                      setAddType(key);
                      setAddValues({});
                      setAdvOpen(false);
                      stopDetect();
                      setDetectErr(null);
                      setDetectChats([]);
                      setManifestOpen(false);
                      setManifestCopied(false);
                    }}
                    options={channelTypes
                      .filter((t) => DEST_META[t.type])
                      .sort((a, b) => DEST_META[a.type].order - DEST_META[b.type].order)
                      .map((t) => ({
                        key: t.type,
                        ...DEST_META[t.type],
                        icon: <Megaphone size={15} />,
                      }))}
                  />
                </div>
              )}

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
                  disabled={!!editing}
                  className="field font-mono text-sm disabled:opacity-60"
                />
                <p className="mt-1 text-[11px] leading-relaxed text-zinc-500">
                  A short name you&apos;ll use to send to it, e.g. team-alerts.
                </p>
              </div>

              {(() => {
                if (!selectedType) return null;
                const primaryKeys = PRIMARY_KEYS[addType] ?? null;
                const primary = primaryKeys
                  ? selectedType.fields.filter((f) => primaryKeys.includes(f.key))
                  : selectedType.fields;
                const advanced = primaryKeys
                  ? selectedType.fields.filter((f) => !primaryKeys.includes(f.key))
                  : [];
                const renderField = (f: ChannelField) => {
                  const copy =
                    addType === "telegram" ? TELEGRAM_FIELD_COPY[f.key] : undefined;
                  const label = copy?.label ?? f.label;
                  const help = copy?.help ?? f.help;
                  // Boolean settings render as REAL toggles (v1.136.0) — a
                  // control that reads as a setting must BE the setting
                  // (v1.127.0 lesson). The form state still stores the wire's
                  // "true"/"false" strings, so POST semantics are unchanged.
                  const isBool =
                    f.type === "bool" ||
                    f.key === "inbound_enabled" ||
                    f.key === "chat_enabled";
                  if (isBool) {
                    const on =
                      (addValues[f.key] ?? "").trim().toLowerCase() === "true";
                    return (
                      <label
                        key={f.key}
                        className={`flex cursor-pointer items-start gap-2.5 rounded-xl border p-2.5 transition-colors ${
                          on
                            ? "border-accent/25 bg-accent/[0.05]"
                            : "border-white/[0.06] hover:bg-white/[0.03]"
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={on}
                          onChange={() =>
                            setAddValues((v) => ({
                              ...v,
                              [f.key]: on ? "false" : "true",
                            }))
                          }
                          aria-label={label}
                          className="mt-0.5 accent-accent"
                        />
                        <span className="min-w-0">
                          <span className="block text-[12.5px] text-zinc-200">
                            {label}
                          </span>
                          {help && (
                            <span className="block text-[11px] leading-snug text-zinc-500">
                              {help}
                            </span>
                          )}
                        </span>
                      </label>
                    );
                  }
                  return (
                  <div key={f.key}>
                    <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                      {label}
                    </label>
                    <input
                      type={f.secret ? "password" : "text"}
                      value={addValues[f.key] ?? ""}
                      onChange={(e) =>
                        setAddValues((v) => ({ ...v, [f.key]: e.target.value }))
                      }
                      aria-label={label}
                      autoComplete="off"
                      className={`field text-sm ${f.secret ? "font-mono" : ""}`}
                    />
                    {help && (
                      <p className="mt-1 text-[11px] leading-relaxed text-zinc-500">{help}</p>
                    )}
                    {addType === "telegram" && f.key === "chat_id" && (
                      <div className="mt-2 space-y-2">
                        {detecting ? (
                          <p className="flex items-center gap-2 text-[12px] text-accent-soft">
                            <Radio size={13} className="animate-pulse" />
                            Now send your bot any message — watching…
                            <button
                              type="button"
                              onClick={stopDetect}
                              className="text-zinc-500 transition-colors hover:text-zinc-300"
                            >
                              stop
                            </button>
                          </p>
                        ) : (
                          <button
                            type="button"
                            onClick={startDetect}
                            disabled={!(addValues["token"] ?? "").trim()}
                            className="btn-ghost px-2.5 py-1.5 text-[12px] disabled:opacity-40"
                            title="Reads your bot's recent messages once to find your chat"
                          >
                            <Search size={13} /> Detect my chat ID
                          </button>
                        )}
                        {detectChats.length > 1 && (
                          <div className="flex flex-wrap gap-1.5">
                            {detectChats.map((c) => (
                              <button
                                key={c.id}
                                type="button"
                                onClick={() => {
                                  setAddValues((v) => ({ ...v, chat_id: String(c.id) }));
                                  setDetectChats([]);
                                }}
                                className="rounded-full border border-accent/30 bg-accent/[0.08] px-2.5 py-1 text-[11.5px] text-accent-soft transition-colors hover:bg-accent/[0.14]"
                              >
                                {c.label}
                              </button>
                            ))}
                          </div>
                        )}
                        {detectErr && (
                          <p className="text-[11.5px] text-rose-300">{detectErr}</p>
                        )}
                      </div>
                    )}
                  </div>
                  );
                };
                return (
                  <>
                    {primary.map(renderField)}
                    <div className="rounded-xl border border-white/[0.05] bg-white/[0.02]">
                      <button
                        type="button"
                        onClick={() => setAdvOpen((v) => !v)}
                        aria-expanded={advOpen}
                        className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-[11px] uppercase tracking-[0.1em] text-zinc-400 transition-colors hover:text-accent-soft"
                      >
                        {advOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                        Advanced
                        {advanced.length > 0 ? ` · two-way & extras (${advanced.length})` : ""}
                        {" · which alerts"}
                      </button>
                      {advOpen && (
                        <div className="space-y-3.5 border-t hairline px-3 py-3">
                          {advanced.map(renderField)}
                          <div>
                            <p className="mb-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                              Send this destination
                            </p>
                            <div className="grid gap-1.5 sm:grid-cols-2">
                              {EVENT_LABELS.map((ev) => {
                                const on = addEvents.includes(ev.key);
                                return (
                                  <label
                                    key={ev.key}
                                    className={`flex cursor-pointer items-center gap-2 rounded-lg border p-2 text-[12px] transition-colors ${
                                      on
                                        ? "border-accent/25 bg-accent/[0.05] text-zinc-200"
                                        : "border-white/[0.06] text-zinc-500 hover:bg-white/[0.03]"
                                    }`}
                                  >
                                    <input
                                      type="checkbox"
                                      checked={on}
                                      onChange={() =>
                                        setAddEvents((prev) =>
                                          prev.includes(ev.key)
                                            ? prev.filter((k) => k !== ev.key)
                                            : [...prev, ev.key],
                                        )
                                      }
                                      className="accent-accent"
                                    />
                                    {ev.label}
                                  </label>
                                );
                              })}
                            </div>
                            <p className="mt-1 text-[11px] text-zinc-600">
                              Everything ticked = all alerts (the default).
                            </p>
                          </div>
                        </div>
                      )}
                    </div>
                  </>
                );
              })()}

              {/* One-paste app setup: only for types that ship a manifest (slack). */}
              {selectedType?.manifest && (
                <div className="rounded-xl border border-white/[0.06] bg-white/[0.02]">
                  <button
                    type="button"
                    onClick={() => setManifestOpen((v) => !v)}
                    aria-expanded={manifestOpen}
                    className="flex w-full items-center justify-between gap-2 px-3.5 py-2.5 text-left text-xs font-medium text-zinc-300 transition-colors hover:text-accent-soft"
                  >
                    <span className="inline-flex items-center gap-2">
                      <FileCode2 size={13} className="text-accent-soft/80" />
                      One-paste Slack app setup
                    </span>
                    {manifestOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  </button>
                  {manifestOpen && (
                    <div className="space-y-2 border-t hairline px-3.5 pb-3.5 pt-2.5">
                      {selectedType.manifest_help && (
                        <p className="text-[11px] leading-relaxed text-zinc-500">
                          {selectedType.manifest_help}
                        </p>
                      )}
                      <div className="relative">
                        <pre className="max-h-72 overflow-auto rounded-xl border border-white/[0.06] bg-ink-900/80 px-3.5 py-3 font-mono text-[11px] leading-relaxed text-zinc-300">
                          {selectedType.manifest}
                        </pre>
                        <button
                          type="button"
                          onClick={copyManifest}
                          title="Copy the manifest YAML to your clipboard"
                          className="absolute right-2 top-2 inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-ink-950/90 px-2 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft"
                        >
                          {manifestCopied ? (
                            <>
                              <Check size={12} className="text-emerald-300" /> Copied
                            </>
                          ) : (
                            <>
                              <Copy size={12} /> Copy
                            </>
                          )}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}

              <div className="flex items-center gap-2">
                <button
                  type="submit"
                  disabled={addBusy || !addName.trim() || !addType}
                  className="btn-accent"
                >
                  {addBusy ? (
                    <LoaderInline label={editing ? "Saving…" : "Adding…"} />
                  ) : editing ? (
                    <>
                      <Pencil size={14} /> Save changes
                    </>
                  ) : (
                    <>
                      <Plus size={14} /> Add destination
                    </>
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowAdd(false);
                    setEditing(null);
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
          </div>
        </Reveal>
      )}

      {/* First-run walkthrough (v1.136.0, the LongTerm idiom): shown until one
          chat-enabled destination exists, then it gets out of the way for good
          — an onboarding panel that never leaves becomes furniture. Gated on a
          loaded list so it never flashes over an offline or still-loading page. */}
      {data && !channels.some((c) => c.chat_enabled) && (
        <Reveal>
          <div className="rounded-2xl border border-accent/15 bg-accent/[0.04] p-4">
            <div className="flex items-start gap-2.5">
              <Info size={15} className="mt-0.5 shrink-0 text-accent-soft" />
              <div className="min-w-0 space-y-3">
                <div>
                  <h3 className="text-[13px] font-semibold text-zinc-100">
                    Chat with Iron Jarvis from your phone
                  </h3>
                  <p className="mt-1 text-[12px] leading-relaxed text-zinc-400">
                    A destination doesn&apos;t have to be one-way. Turn on chat
                    and your Telegram bot becomes a real conversation with Iron
                    Jarvis — the same one you see on the desktop Chat page.
                  </p>
                </div>
                <ol className="space-y-2">
                  {[
                    {
                      t: "Add a Telegram destination",
                      d: "Create a bot with @BotFather, paste its token here, and use Detect my chat ID.",
                    },
                    {
                      t: "Turn on “Chat with Iron Jarvis from this destination”",
                      d: "It's under Advanced. Add your Telegram user id to the allowed senders list — it fails closed, so an empty list allows nobody.",
                    },
                    {
                      t: "Message your bot",
                      d: "Replies come back on your phone, and the conversation appears in Chat on this desktop too.",
                    },
                  ].map((step, i) => (
                    <li key={step.t} className="flex gap-2.5">
                      <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border border-accent/30 bg-accent/[0.08] text-[11px] font-semibold text-accent-soft">
                        {i + 1}
                      </span>
                      <span className="min-w-0">
                        <span className="block text-[12.5px] text-zinc-200">
                          {step.t}
                        </span>
                        <span className="block text-[11.5px] leading-snug text-zinc-500">
                          {step.d}
                        </span>
                      </span>
                    </li>
                  ))}
                </ol>
              </div>
            </div>
          </div>
        </Reveal>
      )}

      <Reveal>
        <div className="grid gap-6 lg:grid-cols-2">
          <Card title={`Destinations${channels.length ? ` · ${channels.length}` : ""}`} icon={<Radio size={15} />}>
            {loading && !data ? (
              <SkeletonRows rows={3} />
            ) : channels.length === 0 ? (
              <Empty icon={<Megaphone size={22} />}>
                No destinations configured yet. Click{" "}
                <span className="font-medium text-accent-soft">Add destination</span> to connect
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
                      {/* v1.118.0: the dot is a TEST RESULT, not decoration —
                          green = last real send delivered; rose = it failed;
                          zinc = never tested. Built-ins are ready by nature. */}
                      <span
                        className={`h-2 w-2 shrink-0 rounded-full ${
                          c.builtin || c.last_test_ok
                            ? "bg-emerald-400"
                            : c.last_test_ok === false
                              ? "bg-rose-400"
                              : "bg-zinc-600"
                        }`}
                      />
                      <div className="min-w-0">
                        <span className="flex items-center gap-2">
                          <span className="truncate font-mono text-sm text-zinc-200">
                            {c.name === "this-pc" ? "This PC" : c.name}
                          </span>
                          {c.type && <Badge value={c.type} tone="cyan" />}
                          {/* Two-way (v1.136.0): this destination also
                              LISTENS — with chat on, it's a full conversation
                              mirrored to the desktop Chat page. */}
                          {(c.chat_enabled || c.inbound_enabled) && (
                            <Badge value="two-way" tone="violet" />
                          )}
                        </span>
                        <span className="block text-[11px] text-zinc-500">
                          {c.name === "this-pc"
                            ? "Pops a notification on this device — no setup (desktop app)"
                            : c.builtin
                              ? "Built-in — always available"
                              : c.last_test_ok
                                ? `Working — tested ${c.last_test_at ? timeAgo(c.last_test_at) : "earlier"}`
                                : c.last_test_ok === false
                                  ? "Failing — open Test for the fix"
                                  : "Untested — hit Test once"}
                          {(c.events?.length ?? 0) > 0 &&
                            ` · ${c.events!.length} alert kind${c.events!.length === 1 ? "" : "s"}`}
                          {c.chat_enabled && " · chat on"}
                        </span>
                      </div>
                    </div>
                    {!(c.builtin ?? BUILTIN.has(c.name)) && (
                      <div className="flex shrink-0 items-center gap-1.5">
                        <button
                          type="button"
                          onClick={() => openEdit(c)}
                          title={`Edit ${c.name}`}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-xs font-medium text-zinc-300 transition-colors hover:border-accent/40 hover:text-accent-soft"
                        >
                          <Pencil size={13} /> Edit
                        </button>
                        <button
                          type="button"
                          onClick={() => testChannel(c.name)}
                          disabled={testBusy !== null}
                          title={`Send a real test message through ${c.name}`}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/[0.08] px-2.5 py-1 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14] disabled:opacity-50"
                        >
                          {testBusy === c.name ? (
                            <LoaderInline label="Testing…" />
                          ) : (
                            <>
                              <Send size={13} /> Test
                            </>
                          )}
                        </button>
                        <ConfirmButton
                          onConfirm={() => deleteChannel(c.name)}
                          title={`Delete channel ${c.name}`}
                        />
                      </div>
                    )}
                    {(c.builtin ?? BUILTIN.has(c.name)) && c.name === "this-pc" && (
                      <button
                        type="button"
                        onClick={() => testChannel(c.name)}
                        disabled={testBusy !== null}
                        title="Pop a test notification on this device right now"
                        className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/[0.08] px-2.5 py-1 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14] disabled:opacity-50"
                      >
                        {testBusy === c.name ? (
                          <LoaderInline label="Testing…" />
                        ) : (
                          <>
                            <Send size={13} /> Test
                          </>
                        )}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {testResult && (
              <div className="mt-3 space-y-2">
                {testResult.ok ? (
                  <SuccessNote>
                    Test message delivered — check {testResult.name}.
                  </SuccessNote>
                ) : (
                  <>
                    <ErrorNote>
                      Test to {testResult.name} failed
                      {testResult.detail ? ` — ${testResult.detail}` : "."}
                    </ErrorNote>
                    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-accent/15 bg-accent/[0.04] px-3 py-2 text-[12px] text-zinc-300">
                      <Lightbulb size={13} className="shrink-0 text-accent-soft" />
                      <span className="min-w-0 flex-1">
                        Fix it fast: {tipFor(testResult.detail)}
                      </span>
                      <button
                        type="button"
                        onClick={() => openEditByName(testResult.name)}
                        className="inline-flex items-center gap-1 rounded-lg border border-accent/30 bg-accent/[0.08] px-2 py-1 text-[11px] font-medium text-accent-soft transition-colors hover:bg-accent/[0.14]"
                      >
                        <Pencil size={12} /> Edit {testResult.name}
                      </button>
                    </div>
                  </>
                )}
              </div>
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
