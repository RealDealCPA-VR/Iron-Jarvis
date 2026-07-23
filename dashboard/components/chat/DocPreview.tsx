"use client";

// Embedded document preview (v1.89.0) — lives in the chat's right rail, so a
// generated Word/Excel/PDF appears NEXT TO the conversation (the chat column
// shifts over; nothing floats). Spreadsheets render as real sheet tabs + rows
// (engine-read via GET /documents/preview), PDFs embed natively (iframe over
// GET /documents/file), everything else shows extracted text. "Open in Word/
// Excel/…" launches the OS-associated app through POST /documents/open — an
// explicit, user-initiated click.

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ExternalLink,
  FileText,
  Loader2,
  RefreshCw,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { get, post, ApiError, API_BASE, ijToken } from "@/lib/api";
import { ErrorNote, LoaderInline } from "@/components/ui";

interface PreviewData {
  kind: "sheet" | "pdf" | "html" | "markdown" | "text";
  name: string;
  path: string;
  suffix: string;
  sheets?: string[];
  sheet?: string;
  rows?: string[][];
  content?: string;
  /** Word-faithful docx→HTML (rendered on a page inside a SANDBOXED frame). */
  html?: string;
  truncated?: boolean;
}

/** Word-like page styling for the docx HTML preview. Rendered inside a fully
 *  sandboxed iframe (no scripts, no navigation) so untrusted document HTML
 *  can never execute — it can only look like a document. */
const PAGE_CSS = `
  html,body{margin:0;padding:0;background:#50545a;}
  .page{max-width:816px;margin:24px auto;background:#fff;color:#141414;
    padding:76px 88px;font-family:'Calibri','Segoe UI',Arial,sans-serif;
    font-size:11pt;line-height:1.55;box-shadow:0 2px 14px rgba(0,0,0,.5);
    min-height:900px;box-sizing:border-box;}
  h1{font-size:19pt;font-weight:600;margin:0 0 12px;}
  h2{font-size:14.5pt;font-weight:600;margin:16px 0 8px;}
  h3{font-size:12.5pt;font-weight:600;margin:14px 0 6px;}
  p{margin:0 0 10px;}
  table{border-collapse:collapse;margin:10px 0;}
  td,th{border:1px solid #b9b9b9;padding:4px 9px;font-size:10.5pt;}
  ul,ol{margin:0 0 10px;padding-left:26px;}
  li{margin:2px 0;}
  a{color:#0563c1;}
  img{max-width:100%;}
  strong{font-weight:700;} em{font-style:italic;}
`;

/** Word-page look for client-rendered markdown (the docx fallback + .md). */
const MD_PAGE_CLASS =
  "mx-auto my-5 min-h-[40rem] max-w-[816px] bg-white px-14 py-12 " +
  "font-[Calibri,'Segoe_UI',Arial,sans-serif] text-[11pt] leading-[1.55] " +
  "text-zinc-900 shadow-xl " +
  "[&_h1]:mb-3 [&_h1]:text-[19pt] [&_h1]:font-semibold " +
  "[&_h2]:mb-2 [&_h2]:mt-4 [&_h2]:text-[14.5pt] [&_h2]:font-semibold " +
  "[&_h3]:mb-1.5 [&_h3]:mt-3 [&_h3]:text-[12.5pt] [&_h3]:font-semibold " +
  "[&_p]:mb-2.5 [&_ul]:mb-2.5 [&_ul]:list-disc [&_ul]:pl-6 " +
  "[&_ol]:mb-2.5 [&_ol]:list-decimal [&_ol]:pl-6 " +
  "[&_table]:my-2.5 [&_table]:border-collapse " +
  "[&_td]:border [&_td]:border-zinc-400 [&_td]:px-2 [&_td]:py-1 " +
  "[&_th]:border [&_th]:border-zinc-400 [&_th]:px-2 [&_th]:py-1 " +
  "[&_a]:text-blue-700 [&_a]:underline";

/** Suffix → the native app the Open button names (mirrors the daemon map). */
const APP_LABEL: Record<string, string> = {
  ".docx": "Word",
  ".doc": "Word",
  ".xlsx": "Excel",
  ".xlsm": "Excel",
  ".csv": "Excel",
  ".pptx": "PowerPoint",
  ".pdf": "PDF viewer",
  ".html": "browser",
};

export function appLabelFor(path: string): string {
  const dot = path.lastIndexOf(".");
  const suffix = dot >= 0 ? path.slice(dot).toLowerCase() : "";
  return APP_LABEL[suffix] ?? "default app";
}

export function DocPreview({
  path,
  onClose,
}: {
  path: string;
  onClose: () => void;
}) {
  const [data, setData] = useState<PreviewData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sheet, setSheet] = useState<string>("");
  const [opening, setOpening] = useState(false);
  const [openNote, setOpenNote] = useState<string | null>(null);

  const load = useCallback(
    async (wantSheet: string) => {
      setLoading(true);
      setError(null);
      try {
        const q = wantSheet ? `&sheet=${encodeURIComponent(wantSheet)}` : "";
        const d = await get<PreviewData>(
          `/documents/preview?path=${encodeURIComponent(path)}${q}`,
        );
        setData(d);
        setSheet(d.sheet ?? "");
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
        setData(null);
      } finally {
        setLoading(false);
      }
    },
    [path],
  );

  // A new path resets the panel (and the sheet selection) entirely.
  useEffect(() => {
    setSheet("");
    setOpenNote(null);
    void load("");
  }, [path, load]);

  async function openNative() {
    if (opening) return;
    setOpening(true);
    setOpenNote(null);
    try {
      const res = await post<{ ok: boolean; app: string }>("/documents/open", {
        path,
      });
      setOpenNote(`Opening in ${res.app}…`);
      window.setTimeout(() => setOpenNote(null), 3000);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setOpening(false);
    }
  }

  const name = data?.name ?? path.split(/[\\/]/).pop() ?? path;
  const tok = ijToken();
  const fileUrl = `${API_BASE}/documents/file?path=${encodeURIComponent(path)}${
    tok ? `&token=${encodeURIComponent(tok)}` : ""
  }`;
  // The Word-faithful page: server HTML wrapped in our page chrome, rendered
  // in a FULLY sandboxed frame (no scripts/forms/navigation can run).
  const docSrcDoc = useMemo(
    () =>
      data?.kind === "html"
        ? `<!doctype html><html><head><meta charset="utf-8"><style>${PAGE_CSS}</style></head><body><div class="page">${data.html ?? ""}</div></body></html>`
        : "",
    [data],
  );

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {/* Header: name + open-native + refresh + close */}
      <div className="flex shrink-0 items-center gap-2 rounded-xl border border-white/[0.06] bg-ink-850/60 px-3 py-2">
        <FileText size={13} className="shrink-0 text-accent-soft/80" />
        <span className="min-w-0 truncate text-[12px] text-zinc-200" title={path}>
          {name}
        </span>
        <div className="ml-auto flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={() => void openNative()}
            disabled={opening}
            title={`Open this file in ${appLabelFor(path)}`}
            className="inline-flex items-center gap-1 rounded-md border border-accent/30 bg-accent/[0.08] px-2 py-1 text-[11px] text-accent-soft transition-colors hover:bg-accent/[0.15] disabled:opacity-50"
          >
            {opening ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <ExternalLink size={12} />
            )}
            Open in {appLabelFor(path)}
          </button>
          <button
            type="button"
            onClick={() => void load(sheet)}
            title="Refresh the preview"
            aria-label="Refresh preview"
            className="grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
          >
            <RefreshCw size={13} />
          </button>
          <button
            type="button"
            onClick={onClose}
            title="Close the preview"
            aria-label="Close preview"
            className="grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
          >
            <X size={14} />
          </button>
        </div>
      </div>
      {openNote && (
        <p className="shrink-0 px-1 text-[11px] text-emerald-300/90">{openNote}</p>
      )}
      {error && <ErrorNote>{error}</ErrorNote>}

      {/* Body */}
      <div className="min-h-0 flex-1 overflow-auto rounded-xl border border-white/[0.06] bg-ink-850/40">
        {loading ? (
          <div className="p-3">
            <LoaderInline label="Loading preview…" />
          </div>
        ) : !data ? null : data.kind === "pdf" ? (
          <iframe
            src={fileUrl}
            title={`Preview of ${name}`}
            className="h-full w-full border-0"
          />
        ) : data.kind === "html" ? (
          // Word-faithful page — sandbox="" blocks scripts/forms/navigation.
          <iframe
            sandbox=""
            srcDoc={docSrcDoc}
            title={`Preview of ${name}`}
            className="h-full w-full border-0"
          />
        ) : data.kind === "markdown" ? (
          <div className="h-full overflow-auto bg-[#50545a] px-3">
            <div className={MD_PAGE_CLASS}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {data.content ?? ""}
              </ReactMarkdown>
            </div>
            {data.truncated && (
              <p className="pb-3 text-center text-[10.5px] text-zinc-300">
                Preview clipped — open the file for everything.
              </p>
            )}
          </div>
        ) : data.kind === "sheet" ? (
          <div className="flex h-full min-h-0 flex-col">
            {(data.sheets?.length ?? 0) > 1 && (
              <div className="flex shrink-0 flex-wrap gap-1 border-b hairline p-1.5">
                {data.sheets!.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => void load(s)}
                    className={`rounded-md border px-2 py-0.5 text-[10.5px] transition-colors ${
                      s === data.sheet
                        ? "border-accent/40 bg-accent/[0.1] text-accent-soft"
                        : "border-white/10 text-zinc-400 hover:text-zinc-200"
                    }`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}
            <div className="min-h-0 flex-1 overflow-auto p-1.5">
              <table className="min-w-full border-collapse text-[11px]">
                <tbody>
                  {(data.rows ?? []).map((row, ri) => (
                    <tr key={ri}>
                      {row.map((cell, ci) => (
                        <td
                          key={ci}
                          className={`max-w-[16rem] truncate border border-white/[0.05] px-1.5 py-0.5 ${
                            ri === 0
                              ? "bg-white/[0.04] font-medium text-zinc-200"
                              : "text-zinc-400"
                          }`}
                        >
                          {cell}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.truncated && (
                <p className="px-1.5 py-2 text-[10.5px] text-zinc-600">
                  Showing the first 80 rows — open in Excel for the full sheet.
                </p>
              )}
            </div>
          </div>
        ) : (
          <div className="p-3">
            <pre className="whitespace-pre-wrap break-words font-mono text-[11.5px] leading-relaxed text-zinc-300">
              {data.content ?? ""}
            </pre>
            {data.truncated && (
              <p className="pt-2 text-[10.5px] text-zinc-600">
                Preview clipped — open the file for everything.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
