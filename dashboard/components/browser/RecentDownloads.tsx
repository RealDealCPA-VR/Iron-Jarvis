"use client";

/**
 * v1.444.0: what your browser downloaded, as Iron Jarvis noticed it.
 *
 * The add-on reports each finished download with its real path; the daemon
 * keeps a bounded list (GET /browser/downloads, `browser/downloads.py`). In
 * chat, Jarvis can list these (`downloads_list`) and rename, move or copy one
 * (`download_manage`, which asks first). This card shows the newest few so the
 * user can see that Jarvis knows where a file went.
 *
 * Read ONCE when the card opens (never polled), and nothing is drawn when the
 * daemon has no such route (an older daemon) or the list is empty. "Show in
 * folder" opens the file's FOLDER (POST /documents/open); a downloaded file is
 * never opened from here — it could be a program.
 */

import { useState } from "react";
import { FolderOpen } from "lucide-react";

import { ApiError, post } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { useApi } from "@/lib/useApi";

export interface DownloadRow {
  id: string;
  name: string;
  path: string;
  site?: string;
  bytes?: number | null;
  at?: string;
  exists?: boolean;
}

interface DownloadsAnswer {
  downloads?: DownloadRow[];
  total?: number;
}

/** "2.1 MB", "640 KB", "12 bytes" — "" when the size is not known. */
export function sizeWords(n: number | null | undefined): string {
  if (typeof n !== "number" || n < 0) return "";
  if (n < 1024) return `${n} bytes`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

/** The site's host only ("irs.gov"); "" for anything unreadable. */
export function siteHost(site: string | undefined): string {
  if (!site) return "";
  try {
    return new URL(site).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

/** The folder a Windows or POSIX path sits in. */
export function folderOf(path: string): string {
  const i = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
  return i > 0 ? path.slice(0, i) : path;
}

export function RecentDownloads() {
  const { data, error } = useApi<DownloadsAnswer>("/browser/downloads?limit=5");
  const [note, setNote] = useState<string | null>(null);
  if (error || !data) return null;
  const rows = Array.isArray(data.downloads) ? data.downloads : [];
  if (!rows.length) return null;
  const total = typeof data.total === "number" ? data.total : rows.length;

  async function showFolder(row: DownloadRow) {
    setNote(null);
    try {
      await post("/documents/open", { path: folderOf(row.path) });
    } catch (e) {
      setNote(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <div data-testid="browser-recent-downloads" className="mt-3">
      <p className="text-[12px] font-medium text-zinc-300">Recent downloads</p>
      <p className="mt-0.5 text-[11px] text-zinc-500">
        Jarvis knows where these went. Ask it in chat to rename, move or copy one; it asks
        before it changes anything.
      </p>
      <ul className="mt-1.5 space-y-1">
        {rows.map((row) => {
          const bits = [sizeWords(row.bytes), siteHost(row.site) && `from ${siteHost(row.site)}`, row.at && timeAgo(row.at)]
            .filter(Boolean)
            .join(" · ");
          return (
            <li
              key={row.id}
              data-testid={`browser-download-${row.id}`}
              className="flex items-center gap-2 text-[12px]"
            >
              <span className="min-w-0 flex-1 truncate" title={row.path}>
                <span className="text-zinc-200">{row.name}</span>
                {bits && <span className="text-zinc-500"> · {bits}</span>}
                {row.exists === false && (
                  <span className="text-tone-warn"> · no longer there</span>
                )}
              </span>
              {row.exists !== false && (
                <button
                  type="button"
                  data-testid={`browser-download-folder-${row.id}`}
                  onClick={() => void showFolder(row)}
                  title={`Show ${folderOf(row.path)}`}
                  className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap text-[11px] text-accent-soft hover:text-accent"
                >
                  <FolderOpen size={12} /> Show in folder
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {total > rows.length && (
        <p className="mt-1 text-[11px] text-zinc-500">
          {total - rows.length} more. Ask Jarvis &ldquo;what did I download?&rdquo; to see them.
        </p>
      )}
      {note && (
        <p role="alert" className="mt-1 text-[11px] text-tone-warn">
          {note}
        </p>
      )}
    </div>
  );
}
