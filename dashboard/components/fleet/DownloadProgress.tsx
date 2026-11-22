"use client";

/**
 * One model download's progress, with Cancel while it runs (next leaps 43-a).
 * The SAME row on Model servers › Models and on the card under a chat reply
 * that started a download (components/chat/ConfigCards), so the two never
 * say different things. The bar is a real `progressbar`; words, not only the
 * bar, say how far it is. One quiet button (Cancel) while it runs; nothing to
 * press once it ended.
 */

import { useState } from "react";
import { CheckCircle2, Loader2, X } from "lucide-react";
import { cancelDownload, progressWords, type Download } from "@/lib/modelLibrary";

export function DownloadProgress({
  download,
  onChange,
  testId = "model-download",
}: {
  download: Download;
  /** The row as the daemon answered the Cancel press. */
  onChange?: (next: Download) => void;
  testId?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const running = download.state === "downloading";

  async function cancel() {
    setBusy(true);
    setErr("");
    try {
      const next = await cancelDownload(download.id);
      if (next) onChange?.(next);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid={testId} data-state={download.state} className="min-w-0 space-y-1.5">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <span className="flex min-w-0 items-center gap-1.5 text-xs text-zinc-300">
          {running ? (
            <Loader2 size={12} className="shrink-0 animate-spin text-zinc-500" aria-hidden />
          ) : download.state === "done" ? (
            <CheckCircle2 size={12} className="shrink-0 text-tone-success" aria-hidden />
          ) : null}
          <span className="min-w-0 break-words font-medium">{download.name}</span>
        </span>
        {running && (
          <button
            type="button"
            data-testid={`${testId}-cancel`}
            onClick={() => void cancel()}
            disabled={busy}
            className="inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 disabled:opacity-50"
          >
            <X size={11} aria-hidden /> Cancel
          </button>
        )}
      </div>
      {running && (
        <div
          role="progressbar"
          aria-label={`Downloading ${download.name}`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={download.percent ?? undefined}
          className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]"
        >
          <div
            className="h-full rounded-full bg-accent/70 transition-[width] duration-500"
            style={{ width: `${download.percent ?? 0}%` }}
          />
        </div>
      )}
      <p
        data-testid={`${testId}-words`}
        className={`break-words text-[11px] leading-relaxed ${
          download.state === "failed" ? "text-tone-danger" : "text-zinc-500"
        }`}
      >
        {progressWords(download)}
      </p>
      {err && (
        <p role="alert" className="break-words text-[11px] text-tone-danger">
          {err}
        </p>
      )}
    </div>
  );
}

export default DownloadProgress;
