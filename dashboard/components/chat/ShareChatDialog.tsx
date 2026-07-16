"use client";

// Share a saved chat thread. Two honest renderings from the daemon
// (POST /chat/threads/{id}/share): FULL — the verbatim transcript — or
// COMPACTED — a faithful digest via the one-shot LLM path. The preview IS
// the exact text you copy; downloads add a .md file or a self-contained
// .html page. Nothing is published anywhere: sharing = your clipboard,
// your files, your apps.

import { useCallback, useEffect, useState } from "react";
import {
  AlignLeft,
  Check,
  Copy,
  Download,
  FileCode2,
  Loader2,
  Mail,
  MessageCircle,
  RefreshCw,
  Share2,
  Sparkles,
  X,
} from "lucide-react";
import { ApiError, post } from "@/lib/api";

type ShareMode = "full" | "compact";

interface ShareResult {
  content: string;
  mode: string;
  format: string;
  title: string;
  messages: number;
  /** Which provider produced a compact digest (transparency). */
  provider?: string;
}

/** mailto: URLs break around ~2k chars; keep a margin. Longer texts share
 *  via Copy/Download instead of a silently truncated email. */
const MAILTO_MAX = 1800;
/** wa.me tolerates long texts but browsers cap URL length — stay sane. */
const WHATSAPP_MAX = 6000;

function slugify(title: string): string {
  const s = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
  return s || "chat";
}

function downloadText(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function ShareChatDialog({
  threadId,
  title,
  onClose,
}: {
  threadId: string;
  title: string;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<ShareMode>("full");
  const [results, setResults] = useState<Partial<Record<ShareMode, ShareResult>>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [htmlBusy, setHtmlBusy] = useState(false);

  const load = useCallback(
    async (m: ShareMode, force = false) => {
      if (!force && results[m]) return;
      setLoading(true);
      setError(null);
      try {
        const r = await post<ShareResult>(`/chat/threads/${threadId}/share`, {
          mode: m,
        });
        setResults((prev) => ({ ...prev, [m]: r }));
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [threadId, results],
  );

  useEffect(() => {
    void load(mode);
  }, [mode, load]);

  // Escape closes (matches the other modals).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const cur = results[mode];
  const content = cur?.content ?? "";

  async function copy() {
    if (!content) return;
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("couldn't reach the clipboard — select the preview and copy manually");
    }
  }

  function downloadMd() {
    if (!content) return;
    const suffix = mode === "compact" ? "-compacted" : "";
    downloadText(`${slugify(title)}${suffix}.md`, content, "text/markdown");
  }

  async function downloadHtml() {
    setHtmlBusy(true);
    setError(null);
    try {
      const r = await post<ShareResult>(`/chat/threads/${threadId}/share`, {
        mode,
        format: "html",
      });
      const suffix = mode === "compact" ? "-compacted" : "";
      downloadText(`${slugify(title)}${suffix}.html`, r.content, "text/html");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setHtmlBusy(false);
    }
  }

  const enc = encodeURIComponent;
  const mailtoOk = content.length > 0 && content.length <= MAILTO_MAX;
  const whatsOk = content.length > 0 && content.length <= WHATSAPP_MAX;
  const tooLong = "too long for this channel — use Copy or a download instead";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Share ${title}`}
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[80vh] w-full max-w-[38rem] flex-col overflow-hidden rounded-2xl border border-white/10 bg-ink-850/95 shadow-card-hover backdrop-blur-xl"
      >
        <header className="flex shrink-0 items-center gap-2 border-b hairline px-4 py-3">
          <Share2 size={16} className="text-accent-soft/80" />
          <h2 className="min-w-0 truncate text-[13px] font-semibold tracking-wide text-zinc-200">
            Share “{title}”
          </h2>
          <button
            type="button"
            onClick={onClose}
            title="Close"
            className="ml-auto grid h-6 w-6 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
          >
            <X size={14} />
          </button>
        </header>

        {/* Mode toggle ------------------------------------------------------ */}
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b hairline px-4 py-2.5">
          <div
            role="group"
            aria-label="What to share"
            className="flex items-center overflow-hidden rounded-xl border border-white/10 bg-white/[0.02]"
          >
            <button
              type="button"
              onClick={() => setMode("full")}
              aria-pressed={mode === "full"}
              title="The complete conversation, word for word"
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-[13px] font-medium transition-colors ${
                mode === "full"
                  ? "bg-accent/15 text-accent-soft"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              <AlignLeft size={13} /> Full chat
            </button>
            <button
              type="button"
              onClick={() => setMode("compact")}
              aria-pressed={mode === "compact"}
              title="A short digest of the conversation, written by your model"
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-[13px] font-medium transition-colors ${
                mode === "compact"
                  ? "bg-accent/15 text-accent-soft"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              <Sparkles size={13} /> Compacted
            </button>
          </div>
          {cur && (
            <span className="text-[11px] text-zinc-500">
              {cur.messages} message{cur.messages === 1 ? "" : "s"}
              {mode === "compact" && cur.provider ? ` · digest by ${cur.provider}` : ""}
            </span>
          )}
          {mode === "compact" && cur && (
            <button
              type="button"
              onClick={() => void load("compact", true)}
              disabled={loading}
              title="Write the digest again"
              className="btn-ghost ml-auto px-2 py-1 text-[12px]"
            >
              <RefreshCw size={12} className={loading ? "animate-spin" : ""} /> Redo
            </button>
          )}
        </div>

        {/* Preview — exactly what Copy puts on the clipboard ---------------- */}
        <div className="min-h-[14rem] flex-1 overflow-y-auto px-4 py-3">
          {loading && !cur ? (
            <div className="flex h-full min-h-[12rem] items-center justify-center gap-2 text-[13px] text-zinc-500">
              <Loader2 size={15} className="animate-spin" />
              {mode === "compact" ? "compacting the conversation…" : "loading…"}
            </div>
          ) : error ? (
            <div className="rounded-xl border border-rose-500/30 bg-rose-500/[0.06] px-3 py-2.5 text-[12.5px] leading-relaxed text-rose-300">
              {error}
            </div>
          ) : (
            <pre className="whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed text-zinc-300">
              {content}
            </pre>
          )}
        </div>

        {/* Actions ----------------------------------------------------------- */}
        <footer className="shrink-0 border-t hairline px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void copy()}
              disabled={!content || loading}
              className="btn-accent py-1.5 text-[13px]"
            >
              {copied ? (
                <>
                  <Check size={14} /> Copied
                </>
              ) : (
                <>
                  <Copy size={14} /> Copy
                </>
              )}
            </button>
            <button
              type="button"
              onClick={downloadMd}
              disabled={!content || loading}
              title="Save as a markdown file"
              className="btn-ghost py-1.5 text-[13px]"
            >
              <Download size={14} /> .md
            </button>
            <button
              type="button"
              onClick={() => void downloadHtml()}
              disabled={!content || loading || htmlBusy}
              title="Save as a self-contained web page (opens in any browser)"
              className="btn-ghost py-1.5 text-[13px]"
            >
              {htmlBusy ? <Loader2 size={14} className="animate-spin" /> : <FileCode2 size={14} />}{" "}
              .html
            </button>
            <span className="mx-1 hidden h-4 w-px bg-white/10 sm:block" />
            <a
              href={mailtoOk ? `mailto:?subject=${enc(title)}&body=${enc(content)}` : undefined}
              aria-disabled={!mailtoOk}
              title={mailtoOk ? "Send in an email" : tooLong}
              className={`btn-ghost py-1.5 text-[13px] ${mailtoOk ? "" : "pointer-events-none opacity-40"}`}
            >
              <Mail size={14} /> Email
            </a>
            <a
              href={whatsOk ? `https://wa.me/?text=${enc(content)}` : undefined}
              target="_blank"
              rel="noopener noreferrer"
              aria-disabled={!whatsOk}
              title={whatsOk ? "Share on WhatsApp" : tooLong}
              className={`btn-ghost py-1.5 text-[13px] ${whatsOk ? "" : "pointer-events-none opacity-40"}`}
            >
              <MessageCircle size={14} /> WhatsApp
            </a>
          </div>
          {mode === "compact" && (
            <p className="mt-2 text-[11px] leading-relaxed text-zinc-500">
              The digest is written by your model from this conversation — give it a
              quick read before sending.
            </p>
          )}
        </footer>
      </div>
    </div>
  );
}
