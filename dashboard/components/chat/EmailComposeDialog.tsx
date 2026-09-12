"use client";

// Save a chat draft to your mailbox's Drafts folder, or send it (C-06).
//
// SUGGEST, DON'T ACT. The draft card's two buttons both open THIS dialog, and
// nothing reaches the mail server until a person presses its primary button.
// "Save to Drafts" is the default and sends nothing — the email lands in the
// Drafts folder for review in Outlook. "Send now" is a deliberate switch.
// Attachments are offered only from this conversation's files (the Files
// rail), unticked unless the draft itself names the file.

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Loader2, Mail, Paperclip, X } from "lucide-react";
import { get, post } from "@/lib/api";

export type ComposeMode = "draft" | "send";

export interface ComposeResult {
  ok: boolean;
  mode: ComposeMode;
  channel?: string;
  detail?: string;
  folder?: string | null;
  refused?: string[];
  attachments?: string[];
}

interface ChannelRow {
  name?: string;
  type?: string;
}

/** "a@x.com, Ann <b@y.com>; c@z.com" → one string per address. */
export function splitAddresses(raw: string): string[] {
  return raw
    .split(/[,;]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * The draft's own "To:" / "Cc:" header lines, when the model wrote them at the
 * top of the body (only the first few lines count — a "To:" further down is
 * part of the message). `body` is the text with those header lines removed.
 */
export function draftHeaders(text: string): { to: string[]; cc: string[]; body: string } {
  const lines = text.split(/\r?\n/);
  const to: string[] = [];
  const cc: string[] = [];
  const keep: string[] = [];
  let scanned = 0;
  let inHeader = true;
  for (const line of lines) {
    if (inHeader && scanned < 6) {
      const m = /^\s*(to|cc)\s*:\s*(.+?)\s*$/i.exec(line);
      if (m) {
        (m[1].toLowerCase() === "to" ? to : cc).push(...splitAddresses(m[2]));
        scanned += 1;
        continue;
      }
      if (line.trim()) {
        scanned += 1;
        inHeader = false;
      }
    }
    keep.push(line);
  }
  return { to, cc, body: keep.join("\n").replace(/^\s*\n/, "") };
}

function basename(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

function folderOf(path: string): string {
  const i = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
  return i > 0 ? path.slice(0, i) : "";
}

export function EmailComposeDialog({
  mode: initialMode,
  subject: initialSubject,
  to: initialTo,
  cc: initialCc,
  files,
  bodyText,
  getBody,
  onClose,
  onDone,
}: {
  mode: ComposeMode;
  subject?: string;
  to: string[];
  cc: string[];
  /** This conversation's files (the Files rail's list). */
  files: readonly string[];
  /** The draft's plain text — used only to pre-tick files it names. */
  bodyText: string;
  /** The message as sent: the card's cleaned HTML + plain text. */
  getBody: () => { html: string; text: string };
  onClose: () => void;
  onDone: (result: ComposeResult) => void;
}) {
  const [mode, setMode] = useState<ComposeMode>(initialMode);
  const [to, setTo] = useState(initialTo.join(", "));
  const [cc, setCc] = useState(initialCc.join(", "));
  const [subject, setSubject] = useState(initialSubject ?? "");
  const [picked, setPicked] = useState<Set<string>>(() => {
    const lower = bodyText.toLowerCase();
    return new Set(files.filter((f) => lower.includes(basename(f).toLowerCase())));
  });
  // undefined = still checking; null = no email account; "" = could not check.
  const [account, setAccount] = useState<string | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const firstField = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let alive = true;
    get<{ channels?: ChannelRow[] }>("/comm/channels")
      .then((d) => {
        if (!alive) return;
        const row = (d?.channels ?? []).find(
          (c) => String(c?.type ?? "").toLowerCase() === "email",
        );
        setAccount(row ? String(row.name ?? "email") : null);
      })
      .catch(() => {
        // Could not check — let the daemon answer on submit instead of
        // claiming there is no account.
        if (alive) setAccount("");
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (account !== undefined && account !== null) firstField.current?.focus();
  }, [account]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const toList = splitAddresses(to);
  const canSubmit = !busy && (mode === "draft" || toList.length > 0);

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    const { html, text } = getBody();
    try {
      const res = await post<ComposeResult>("/comm/email/compose", {
        mode,
        to: toList,
        cc: splitAddresses(cc),
        subject,
        html,
        text,
        attachments: files.filter((f) => picked.has(f)),
      });
      onDone(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const title = mode === "draft" ? "Save to Drafts" : "Send email";
  const input =
    "w-full rounded-lg border border-white/10 bg-ink-900/60 px-2.5 py-1.5 text-[13px] text-zinc-100 outline-none transition-colors placeholder:text-zinc-600 focus:border-accent/40";
  const label = "text-[10px] font-semibold uppercase tracking-wider text-zinc-500";

  const dialog = (
    <div
      role="presentation"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm"
      onClick={() => !busy && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        data-testid="email-compose-dialog"
        className="w-full max-w-lg overflow-hidden rounded-2xl border border-white/10 bg-ink-850/95 shadow-card-hover backdrop-blur-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-white/[0.06] px-4 py-3">
          <Mail size={14} className="text-accent-soft/80" aria-hidden="true" />
          <span className="flex-1 text-sm font-medium text-zinc-100">{title}</span>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label="Close"
            className="grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
          >
            <X size={13} />
          </button>
        </div>

        {account === undefined ? (
          <div className="flex items-center gap-2 px-4 py-6 text-[13px] text-zinc-400">
            <Loader2 size={14} className="animate-spin text-accent-soft" />
            Checking your email account…
          </div>
        ) : account === null ? (
          <div data-testid="compose-no-account" className="space-y-3 px-4 py-4">
            <p className="text-[13px] leading-relaxed text-zinc-200">
              No email account is connected yet.
            </p>
            <p className="text-[12.5px] leading-relaxed text-zinc-400">
              Add your email in Channels — its SMTP server to send, and its IMAP
              server to save drafts — then use this button again. Until then,
              Copy still puts the draft on your clipboard with its formatting.
            </p>
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={onClose}
                className="rounded-lg border border-white/10 px-3 py-1.5 text-[12px] text-zinc-300 transition-colors hover:bg-white/[0.05]"
              >
                Not now
              </button>
              <Link
                href="/channels"
                onClick={onClose}
                className="rounded-lg border border-accent/25 bg-accent/[0.1] px-3 py-1.5 text-[12px] text-accent-soft transition-colors hover:bg-accent/[0.18]"
              >
                Open Channels
              </Link>
            </div>
          </div>
        ) : (
          <div className="space-y-3 px-4 py-4">
            <div
              role="radiogroup"
              aria-label="What to do with this email"
              className="inline-flex rounded-lg border border-white/10 p-0.5"
            >
              {(["draft", "send"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  role="radio"
                  aria-checked={mode === m}
                  data-testid={`compose-mode-${m}`}
                  onClick={() => setMode(m)}
                  className={`rounded-md px-2.5 py-1 text-[12px] transition-colors ${
                    mode === m
                      ? "bg-accent/[0.14] text-accent-soft"
                      : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  {m === "draft" ? "Save to Drafts" : "Send now"}
                </button>
              ))}
            </div>

            <label className="block space-y-1">
              <span className={label}>To</span>
              <input
                ref={firstField}
                id="compose-to"
                value={to}
                onChange={(e) => setTo(e.target.value)}
                placeholder={mode === "draft" ? "optional for a draft" : "name@example.com"}
                className={input}
              />
            </label>
            <label className="block space-y-1">
              <span className={label}>Cc</span>
              <input
                id="compose-cc"
                value={cc}
                onChange={(e) => setCc(e.target.value)}
                className={input}
              />
            </label>
            <label className="block space-y-1">
              <span className={label}>Subject</span>
              <input
                id="compose-subject"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                className={input}
              />
            </label>

            <div className="space-y-1">
              <span className={label}>Attach from this conversation</span>
              {files.length === 0 ? (
                <p className="text-[12px] text-zinc-500">No files in this conversation yet.</p>
              ) : (
                <div className="max-h-36 space-y-0.5 overflow-y-auto rounded-lg border border-white/[0.06] p-1">
                  {files.map((f, i) => (
                    <label
                      key={f}
                      className="flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-1 hover:bg-white/[0.03]"
                    >
                      <input
                        id={`compose-file-${i}`}
                        type="checkbox"
                        checked={picked.has(f)}
                        onChange={(e) =>
                          setPicked((prev) => {
                            const next = new Set(prev);
                            if (e.target.checked) next.add(f);
                            else next.delete(f);
                            return next;
                          })
                        }
                        className="accent-cyan-400"
                      />
                      <Paperclip size={11} className="shrink-0 text-zinc-500" aria-hidden="true" />
                      <span className="min-w-0 flex-1 truncate text-[12.5px] text-zinc-200">
                        {basename(f)}
                      </span>
                      <span className="max-w-[40%] truncate text-[10.5px] text-zinc-600">
                        {folderOf(f)}
                      </span>
                    </label>
                  ))}
                </div>
              )}
            </div>

            <p data-testid="compose-note" className="text-[11.5px] leading-relaxed text-zinc-500">
              {mode === "draft"
                ? "Goes to your mailbox's Drafts folder — nothing is sent. Open it in Outlook to review and send."
                : `Sends now from your email account${account ? ` (${account})` : ""}. This can't be undone.`}
            </p>
            {error && (
              <p role="alert" className="text-[12px] leading-relaxed text-rose-300">
                {error}
              </p>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={onClose}
                disabled={busy}
                className="rounded-lg border border-white/10 px-3 py-1.5 text-[12px] text-zinc-300 transition-colors hover:bg-white/[0.05]"
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid="compose-submit"
                onClick={() => void submit()}
                disabled={!canSubmit}
                className="inline-flex items-center gap-1.5 rounded-lg border border-accent/25 bg-accent/[0.1] px-3 py-1.5 text-[12px] text-accent-soft transition-colors hover:bg-accent/[0.18] disabled:opacity-40"
              >
                {busy && <Loader2 size={12} className="animate-spin" />}
                {mode === "draft" ? "Save to Drafts" : "Send"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );

  return typeof document === "undefined" ? null : createPortal(dialog, document.body);
}
