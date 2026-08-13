"use client";

// Embedded document preview (v1.89.0) — lives in the chat's right rail, so a
// generated Word/Excel/PDF appears NEXT TO the conversation (the chat column
// shifts over; nothing floats). Spreadsheets render as real sheet tabs + rows
// (engine-read via GET /documents/preview), PDFs embed natively (iframe over
// GET /documents/file), everything else shows extracted text. "Open in Word/
// Excel/…" launches the OS-associated app through POST /documents/open — an
// explicit, user-initiated click.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  Check,
  Copy,
  Download,
  ExternalLink,
  FileDiff,
  FileText,
  FolderOpen,
  Loader2,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { get, post, ApiError, API_BASE, ijToken } from "@/lib/api";
import { ErrorNote, LoaderInline } from "@/components/ui";
import { FilePickerModal } from "@/components/FilePickerModal";

/** A common save destination offered by GET /documents/places. */
interface SavePlace {
  key: string;
  label: string;
  path: string;
}

export interface PreviewData {
  kind: "sheet" | "pdf" | "html" | "markdown" | "text" | "image";
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
  /** Truncation honesty (v1.166.0): the REAL extent of the file, so a clipped
   *  preview can say "first 80 of 4,112 rows" instead of looking complete. */
  total_rows?: number;
  total_cols?: number;
  total_chars?: number;
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

// --- "Changes since you last previewed" (v1.166.0, A7) -----------------------
// A tool rewrites report.md while its preview sits open; Refresh shows the new
// text and the old one is simply GONE — the user cannot tell what the model
// changed. So the panel keeps an in-memory snapshot of the last payload it
// showed (per path, per sheet for workbooks) and, when a re-preview differs,
// offers a line diff. In-memory ONLY, module-level so closing and reopening
// the panel still counts as "last previewed"; a page reload forgets, by design
// — this is a courtesy view, not a version store.

/** One line of the diff view. `same` lines are kept for reading context. */
export interface DiffLine {
  kind: "same" | "added" | "removed";
  text: string;
}

/** Serialize a preview payload into comparable lines: text/markdown split on
 *  newlines, sheets one TAB-joined line per row (cell edits then read as a
 *  changed line). Kinds with no stable text form (pdf/html/image) return null
 *  — no snapshot, no diff, no false "unchanged" claim. */
export function snapshotLines(d: PreviewData): string[] | null {
  if (d.kind === "sheet") return (d.rows ?? []).map((r) => r.join("\t"));
  if (d.kind === "text" || d.kind === "markdown")
    return (d.content ?? "").split("\n");
  return null;
}

/** Classic LCS line diff: unchanged lines interleaved with removed (prev-only)
 *  and added (next-only) lines, in document order. Previews are capped
 *  server-side (80 rows / 20k chars), so the quadratic table stays tiny; past
 *  the guard it degrades to remove-all/add-all — coarser, never wrong. */
export function diffLines(prev: string[], next: string[]): DiffLine[] {
  const MAX = 1500;
  if (prev.length > MAX || next.length > MAX) {
    return [
      ...prev.map((text) => ({ kind: "removed" as const, text })),
      ...next.map((text) => ({ kind: "added" as const, text })),
    ];
  }
  const m = prev.length;
  const n = next.length;
  // lcs[i][j] = LCS length of prev[i:] vs next[j:]
  const lcs: Uint32Array[] = Array.from(
    { length: m + 1 },
    () => new Uint32Array(n + 1),
  );
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      lcs[i][j] =
        prev[i] === next[j]
          ? lcs[i + 1][j + 1] + 1
          : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (prev[i] === next[j]) {
      out.push({ kind: "same", text: prev[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ kind: "removed", text: prev[i] });
      i++;
    } else {
      out.push({ kind: "added", text: next[j] });
      j++;
    }
  }
  while (i < m) out.push({ kind: "removed", text: prev[i++] });
  while (j < n) out.push({ kind: "added", text: next[j++] });
  return out;
}

/** Last-viewed payloads, keyed by path (+ sheet name for workbooks — switching
 *  tabs is not a "change"). Module scope on purpose; see the block comment. */
const lastViewedLines = new Map<string, string[]>();

function snapshotKey(path: string, d: PreviewData): string {
  // NUL separator — legal in neither a file path nor a sheet name, so
  // "C:\a b" + "" can never collide with "C:\a" + "b".
  return `${path}\u0000${d.kind === "sheet" ? (d.sheet ?? "") : ""}`;
}

// --- Redaction receipt: "Compare to original" (v1.168.0) ---------------------
// The redaction engine writes its output as `<stem>.redacted<suffix>` beside
// the source (documents/redact.py callers: the tool, the scan/apply routes,
// the skill — pinned server-side in tests/test_redaction_receipt_v1168.py).
// So a preview of `organizer.redacted.txt` can OFFER the original: both files
// go through GET /documents/read (the same extract_text on both sides — the
// preview payload carries no text at all for pdf/docx, the formats tax
// documents actually arrive in), the existing diffLines machinery renders
// what changed, and the removed-PII counts come from the placeholder tokens
// the engine itself wrote into the redacted copy — counted by re-reading the
// written file, never repeated from a tool's claim, and net of any markers
// the original already carried (see markerDelta).

/** The `label` style's in-document tags (mirrors _LABELS in
 *  documents/redact.py — the backend test pins this exact vocabulary). */
const REDACTION_LABELS = [
  "SSN", "ITIN", "EIN", "EMAIL", "PHONE", "CARD", "ACCOUNT", "DOB",
  "ADDRESS", "IP", "REDACTED",
] as const;

/** GET /documents/read clips text at this many chars (pinned server-side) —
 *  a payload AT the cap means the comparison window may be short. */
const READ_CAP = 20_000;

/** The original this file is a redacted copy of, per the engine's naming
 *  convention — or null when the name doesn't say so. `report.redacted.pdf`
 *  → `report.pdf`, `notes.redacted` → `notes`, anything else → null. */
export function redactionSourcePath(path: string): string | null {
  const parts = /^(.*[\\/])?([^\\/]+)$/.exec(path);
  if (!parts) return null;
  const m = /^(.+)\.redacted(\.[^.]+)?$/i.exec(parts[2]);
  if (!m) return null;
  return (parts[1] ?? "") + m[1] + (m[2] ?? "");
}

export interface RedactionMarkers {
  /** Label-style tag → occurrences (`[SSN]` twice → { SSN: 2 }). */
  categories: Record<string, number>;
  /** Runs of █ — the `black` style's same-length blocks, one run per value. */
  blocks: number;
}

/** Count the redaction markers the engine wrote into *text*. Only the exact
 *  `[SSN]`-style tags the engine emits count — `[NOPE]` is document text. */
export function redactionMarkers(text: string): RedactionMarkers {
  const categories: Record<string, number> = {};
  const rx = new RegExp(`\\[(${REDACTION_LABELS.join("|")})\\]`, "g");
  for (const m of text.matchAll(rx))
    categories[m[1]] = (categories[m[1]] ?? 0) + 1;
  return { categories, blocks: (text.match(/█+/g) ?? []).length };
}

/** Markers the redacted copy carries BEYOND the original's, clamped at 0.
 *  The original can legitimately contain markers — re-redacting an already-
 *  redacted copy (`x.redacted.redacted.txt`, the engine's own convention
 *  applied twice), or a document quoting a literal `[SSN]` — and counting
 *  those would claim removals THIS pass never made, contradicting the diff
 *  right below the badges. */
export function markerDelta(
  red: RedactionMarkers,
  orig: RedactionMarkers,
): RedactionMarkers {
  const categories: Record<string, number> = {};
  for (const [label, n] of Object.entries(red.categories)) {
    const left = n - (orig.categories[label] ?? 0);
    if (left > 0) categories[label] = left;
  }
  return { categories, blocks: Math.max(0, red.blocks - orig.blocks) };
}

/** The header line's honest count — empty string when nothing was found
 *  (`remove` style leaves no marker, so the diff alone tells that story). */
export function markerSummary(m: RedactionMarkers): string {
  const parts = Object.entries(m.categories).map(([l, n]) => `${n} × ${l}`);
  if (m.blocks > 0) parts.push(`${m.blocks} × blacked-out (█)`);
  if (parts.length === 0) return "";
  return `Removed: ${parts.join(", ")} — counted by re-reading the redacted file itself.`;
}

/** Render a diff line's text with the engine's markers highlighted, so the
 *  eye lands on what replaced the PII. */
function withMarkersHighlighted(text: string): ReactNode[] {
  const rx = new RegExp(`\\[(?:${REDACTION_LABELS.join("|")})\\]|█+`, "g");
  const out: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const m of text.matchAll(rx)) {
    const at = m.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    out.push(
      <mark
        key={key++}
        data-testid="redaction-marker"
        className="rounded bg-amber-400/20 px-0.5 text-amber-200"
      >
        {m[0]}
      </mark>,
    );
    last = at + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

/** State of one comparison: both texts fetched, diffed, counted. */
interface CompareState {
  loading: boolean;
  error: string | null;
  /** Which file failed and why, in words that fit the ACTUAL failure —
   *  never "the original may have been deleted" when the redacted copy's
   *  read is the one that failed, or when the daemon's file policy refused. */
  errorHint: string | null;
  diff: DiffLine[] | null;
  markers: RedactionMarkers | null;
  /** Either side sat at the /documents/read clip — window honesty. */
  clipped: boolean;
}

export function DocPreview({
  path,
  onClose,
}: {
  path: string;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [data, setData] = useState<PreviewData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sheet, setSheet] = useState<string>("");
  const [opening, setOpening] = useState(false);
  const [openNote, setOpenNote] = useState<string | null>(null);
  const [places, setPlaces] = useState<SavePlace[]>([]);
  const [saving, setSaving] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [pickFolder, setPickFolder] = useState(false);
  const [imgError, setImgError] = useState(false);
  // The last-viewed lines this payload DIFFERS from (null = no change to show)
  // and whether the diff view is the one on screen. See the module block above.
  const [changedFrom, setChangedFrom] = useState<string[] | null>(null);
  const [showChanges, setShowChanges] = useState(false);
  // "Compare to original" (v1.168.0): fetched lazily on first toggle — text
  // extraction on a big PDF is real work, so it never runs unasked.
  const [compare, setCompare] = useState<CompareState | null>(null);
  const [showCompare, setShowCompare] = useState(false);

  // The original this file is a redacted copy of, by the engine's naming
  // convention (null for every non-redacted file — the row simply isn't there).
  const redactionSource = useMemo(() => redactionSourcePath(path), [path]);
  const redactionSourceName =
    redactionSource?.split(/[\\/]/).pop() ?? redactionSource ?? "";

  // In-flight guard for the comparison fetch. /documents/read can take
  // SECONDS (the scanned-PDF OCR fallback goes through the vision router), so
  // a Refresh, sheet switch, or path change that invalidates the comparison
  // mid-fetch must also make the in-flight result unlandable — otherwise the
  // old Promise resolves after the reset and the next toggle shows a stale
  // (or the PREVIOUS file's) diff and counts without refetching.
  const compareGen = useRef(0);

  /** Fetch BOTH extracted texts and diff them (original → redacted copy). */
  const loadCompare = useCallback(async () => {
    if (!redactionSource) return;
    const gen = ++compareGen.current; // a newer call invalidates this one too
    setCompare({
      loading: true,
      error: null,
      errorHint: null,
      diff: null,
      markers: null,
      clipped: false,
    });
    const read = (p: string) =>
      get<{ path: string; text: string }>(
        `/documents/read?path=${encodeURIComponent(p)}`,
      );
    // allSettled, not all: an error must say WHICH side failed — blaming the
    // original when the redacted copy's read is the broken one sends the user
    // hunting for the wrong file.
    const [orig, red] = await Promise.allSettled([
      read(redactionSource),
      read(path),
    ]);
    if (gen !== compareGen.current) return; // invalidated mid-flight — drop it
    if (orig.status === "rejected" || red.status === "rejected") {
      const failedOriginal = orig.status === "rejected";
      const reason: unknown = failedOriginal ? orig.reason : (red as PromiseRejectedResult).reason;
      const failedName = failedOriginal
        ? redactionSourceName
        : (path.split(/[\\/]/).pop() ?? path);
      const status = reason instanceof ApiError ? reason.status : null;
      // The hint fits the failure: 403 is the daemon's file policy saying no
      // (protected path / outside the allowlist) — "moved or deleted" would
      // be simply wrong there. Only a 400 "cannot read" earns that wording.
      const errorHint =
        status === 403
          ? `The daemon's file policy refused to read ${failedName} — a protected path, or one outside the configured allowlist.`
          : status === 400
            ? `The comparison needs both files on disk — ${failedName} may have been moved, renamed, or deleted.`
            : `Couldn't read ${failedName} — use Refresh to try again.`;
      setCompare({
        loading: false,
        error: reason instanceof Error ? reason.message : String(reason),
        errorHint,
        diff: null,
        markers: null,
        clipped: false,
      });
      return;
    }
    const origText = orig.value.text ?? "";
    const redText = red.value.text ?? "";
    setCompare({
      loading: false,
      error: null,
      errorHint: null,
      diff: diffLines(origText.split(/\r?\n/), redText.split(/\r?\n/)),
      // Only markers this pass ADDED count — the original may already carry
      // markers (a re-redacted copy, or a literal "[SSN]" in the document),
      // and badging those would claim removals the diff below denies.
      markers: markerDelta(redactionMarkers(redText), redactionMarkers(origText)),
      clipped: origText.length >= READ_CAP || redText.length >= READ_CAP,
    });
  }, [path, redactionSource, redactionSourceName]);

  function toggleCompare() {
    setShowChanges(false); // one diff view at a time
    setShowCompare((v) => {
      const next = !v;
      // Refetch on first open AND when reopening onto a cached error — the
      // failure may have been transient (daemon hiccup, file restored), and
      // re-showing a stale error with no way to retry dead-ends the user.
      if (next && (compare === null || compare.error)) void loadCompare();
      return next;
    });
  }

  useEffect(() => {
    let alive = true;
    get<{ places: SavePlace[] }>("/documents/places")
      .then((r) => alive && setPlaces(r.places ?? []))
      .catch(() => alive && setPlaces([])); // no places → the picker still works
    return () => {
      alive = false;
    };
  }, []);

  /** Copy the previewed file into *folder*, retrying once to overwrite. */
  async function saveTo(folder: string, overwrite = false) {
    setSaving(folder);
    setSaveError(null);
    setSavedNote(null);
    setPickFolder(false);
    try {
      const r = await post<{ path: string; folder: string }>(
        "/documents/save-copy",
        { source: path, dest_dir: folder, overwrite },
      );
      setSavedNote(`Saved to ${r.path}`);
    } catch (err) {
      // 409 = same name already there. Ask rather than silently replacing
      // someone's file — the whole point of this row is no surprises.
      if (err instanceof ApiError && err.status === 409 && !overwrite) {
        setSaving(null);
        if (
          window.confirm(
            `${name} is already in that folder. Replace it?`,
          )
        )
          return saveTo(folder, true);
        return;
      }
      setSaveError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(null);
    }
  }

  const load = useCallback(
    async (wantSheet: string) => {
      setLoading(true);
      setError(null);
      // A fresh load is a fresh attempt for the <img> too: without this,
      // one transient failure (the agent still writing the PNG) left Refresh
      // refetching the JSON while the panel stayed stuck on the error text.
      setImgError(false);
      try {
        const q = wantSheet ? `&sheet=${encodeURIComponent(wantSheet)}` : "";
        const d = await get<PreviewData>(
          `/documents/preview?path=${encodeURIComponent(path)}${q}`,
        );
        setData(d);
        setSheet(d.sheet ?? "");
        // Snapshot bookkeeping (v1.166.0): compare against the last payload
        // this panel SHOWED for the same path(+sheet), then that comparison
        // base becomes component state and the store moves to the new payload
        // — "since you last previewed" always means the previous viewing.
        const lines = snapshotLines(d);
        if (lines) {
          const key = snapshotKey(path, d);
          const prior = lastViewedLines.get(key);
          setChangedFrom(
            prior &&
              (prior.length !== lines.length ||
                prior.some((l, idx) => l !== lines[idx]))
              ? prior
              : null,
          );
          lastViewedLines.set(key, lines);
        } else {
          setChangedFrom(null);
        }
        setShowChanges(false); // a fresh payload always lands on the current view
        // A fresh payload also invalidates a fetched comparison — the file on
        // disk may have changed, and a stale diff would claim otherwise. The
        // generation bump makes any IN-FLIGHT comparison unlandable too.
        compareGen.current += 1;
        setCompare(null);
        setShowCompare(false);
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
        setData(null);
      } finally {
        setLoading(false);
      }
    },
    [path],
  );

  // A new path resets the panel (and the sheet selection) entirely. The
  // generation bump here is belt-and-braces with load()'s: a comparison
  // started under the OLD path must never land under the new one — that
  // would show file A's removal receipt under "a redacted copy of B".
  useEffect(() => {
    compareGen.current += 1;
    setSheet("");
    setOpenNote(null);
    setImgError(false);
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
  // The diff on demand: last-viewed lines vs the payload on screen (v1.166.0).
  const diff = useMemo(
    () =>
      showChanges && changedFrom && data
        ? diffLines(changedFrom, snapshotLines(data) ?? [])
        : null,
    [showChanges, changedFrom, data],
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
          {/* Download + copy-path (v1.153.2). Previewing a file you cannot
              take away is half an answer, and "where exactly is it?" was the
              question that surfaced this: a path you can copy beats a path you
              have to transcribe from a sentence. */}
          <a
            // &download=1 forces Content-Disposition: attachment (v1.166.0) —
            // pdf/images now render INLINE by default so the iframe/<img>
            // previews work, and this anchor must stay a real download.
            href={`${fileUrl}&download=1`}
            download={name}
            title={`Download ${name}`}
            aria-label={`Download ${name}`}
            className="grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-accent-soft"
          >
            <Download size={13} />
          </a>
          <button
            type="button"
            onClick={() => {
              void navigator.clipboard?.writeText(path);
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1400);
            }}
            title={`Copy the full path\n${path}`}
            aria-label="Copy the full file path"
            className="grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-accent-soft"
          >
            {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} />}
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
      {/* Where to keep it (v1.107.0). Chat's tools run inside a confined
          workspace, so anything they produce lands in the uploads scratch dir
          by construction — right for confinement, useless as a place to find a
          finished file. Buttons for the folders people actually use, plus a
          picker for anywhere else. */}
      <div className="flex shrink-0 flex-wrap items-center gap-1.5 px-1">
        <span className="text-[11px] text-zinc-500">Save a copy to</span>
        {places.map((pl) => (
          <button
            key={pl.key}
            type="button"
            onClick={() => void saveTo(pl.path)}
            disabled={saving !== null}
            title={pl.path}
            className="inline-flex items-center gap-1 rounded-md border border-white/[0.08] px-2 py-0.5 text-[11px] text-zinc-300 transition-colors hover:bg-white/[0.06] disabled:opacity-50"
          >
            {saving === pl.path ? (
              <Loader2 size={11} className="animate-spin" />
            ) : (
              <Download size={11} />
            )}
            {pl.label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setPickFolder(true)}
          disabled={saving !== null}
          className="inline-flex items-center gap-1 rounded-md border border-white/[0.08] px-2 py-0.5 text-[11px] text-zinc-300 transition-colors hover:bg-white/[0.06] disabled:opacity-50"
        >
          <FolderOpen size={11} /> Choose folder…
        </button>
      </div>
      {savedNote && (
        <p className="shrink-0 px-1 text-[11px] text-emerald-300/90">{savedNote}</p>
      )}
      {saveError && (
        <p className="shrink-0 px-1 text-[11px] text-rose-300/90">{saveError}</p>
      )}
      {openNote && (
        <p className="shrink-0 px-1 text-[11px] text-emerald-300/90">{openNote}</p>
      )}
      {/* The file changed between viewings (v1.166.0): offer the diff. The row
          only exists when a re-preview genuinely differed from the snapshot —
          an always-present toggle would mostly show "no changes". */}
      {changedFrom && !loading && data && (
        <div className="flex shrink-0 items-center gap-2 px-1">
          <button
            type="button"
            onClick={() => {
              setShowCompare(false); // one diff view at a time
              setShowChanges((v) => !v);
            }}
            className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] transition-colors ${
              showChanges
                ? "border-accent/40 bg-accent/[0.1] text-accent-soft"
                : "border-white/[0.08] text-zinc-300 hover:bg-white/[0.06]"
            }`}
          >
            <FileDiff size={11} /> {showChanges ? "Current" : "Changes"}
          </button>
          <span className="text-[10.5px] text-zinc-500">
            this file changed since you last previewed it
          </span>
        </div>
      )}
      {/* Redaction receipt (v1.168.0): this file's NAME says it is a redacted
          copy (`<stem>.redacted<suffix>`, the engine's one convention), so
          offer the original side-by-side — what was removed, category by
          category, counted from the redacted file's own text. */}
      {redactionSource && !loading && data && (
        <div className="flex shrink-0 items-center gap-2 px-1">
          <button
            type="button"
            onClick={toggleCompare}
            className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] transition-colors ${
              showCompare
                ? "border-accent/40 bg-accent/[0.1] text-accent-soft"
                : "border-white/[0.08] text-zinc-300 hover:bg-white/[0.06]"
            }`}
          >
            <ShieldCheck size={11} />{" "}
            {showCompare ? "Hide comparison" : "Compare to original"}
          </button>
          <span className="text-[10.5px] text-zinc-500">
            a redacted copy of {redactionSourceName}
          </span>
        </div>
      )}
      {error && <ErrorNote>{error}</ErrorNote>}
      <FilePickerModal
        open={pickFolder}
        onClose={() => setPickFolder(false)}
        pickFolders
        onPick={(folder) => void saveTo(folder)}
        title={`Where should ${name} go?`}
      />

      {/* Body */}
      <div className="min-h-0 flex-1 overflow-auto rounded-xl border border-white/[0.06] bg-ink-850/40">
        {loading ? (
          <div className="p-3">
            <LoaderInline label="Loading preview…" />
          </div>
        ) : !data ? null : showCompare ? (
          // The redaction receipt: original vs redacted copy, one unified
          // line diff (the same view idiom as "Changes"), removed PII lines
          // red, the engine's replacement markers highlighted amber.
          <div className="p-3">
            {!compare || compare.loading ? (
              <LoaderInline label="Reading both files…" />
            ) : compare.error ? (
              <div className="text-[11.5px]">
                <p className="text-rose-300/90">
                  Couldn&apos;t compare: {compare.error}
                </p>
                {compare.errorHint && (
                  // Computed at fetch time: names the file that actually
                  // failed, with wording that fits the failure (moved/deleted
                  // only for a 400 "cannot read"; policy refusal for a 403).
                  <p className="pt-1 text-zinc-400">{compare.errorHint}</p>
                )}
              </div>
            ) : (
              <>
                {(Object.keys(compare.markers?.categories ?? {}).length > 0 ||
                  (compare.markers?.blocks ?? 0) > 0) && (
                  <div className="flex flex-wrap items-center gap-1.5 pb-2">
                    {Object.entries(compare.markers?.categories ?? {}).map(
                      ([label, n]) => (
                        <span
                          key={label}
                          data-testid="redaction-badge"
                          className="inline-flex items-center gap-1 rounded-md border border-amber-400/30 bg-amber-400/[0.08] px-1.5 py-0.5 text-[10px] font-medium text-amber-300"
                        >
                          {label} × {n}
                        </span>
                      ),
                    )}
                    {(compare.markers?.blocks ?? 0) > 0 && (
                      <span
                        data-testid="redaction-badge"
                        className="inline-flex items-center gap-1 rounded-md border border-amber-400/30 bg-amber-400/[0.08] px-1.5 py-0.5 text-[10px] font-medium text-amber-300"
                      >
                        █ × {compare.markers!.blocks}
                      </span>
                    )}
                  </div>
                )}
                <p className="pb-2 text-[10.5px] text-zinc-500">
                  {compare.markers && markerSummary(compare.markers)
                    ? markerSummary(compare.markers)
                    : "No new redaction markers in this file's extracted text — the diff below shows what differs from the original."}{" "}
                  Red lines are the original&apos;s text; green lines are the
                  redacted copy.
                  {compare.clipped &&
                    // Window honesty: /documents/read clips at 20k chars, so
                    // a payload AT the cap means later PII is out of view.
                    ` Compared over the first ${READ_CAP.toLocaleString()} extracted characters of each file only.`}
                </p>
                <div className="whitespace-pre-wrap break-words font-mono text-[11.5px] leading-relaxed">
                  {(compare.diff ?? []).map((l, i) => (
                    <div
                      key={i}
                      data-testid={`cmp-${l.kind}`}
                      className={
                        l.kind === "added"
                          ? "bg-emerald-500/[0.08] text-emerald-300"
                          : l.kind === "removed"
                            ? "bg-rose-500/[0.08] text-rose-300/90"
                            : "text-zinc-500"
                      }
                    >
                      {l.kind === "added" ? "+ " : l.kind === "removed" ? "− " : "  "}
                      {l.kind === "added" ? withMarkersHighlighted(l.text) : l.text}
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        ) : diff ? (
          // The line diff, "since you last previewed": green added, red
          // removed, unchanged lines dimmed for reading context.
          <div className="p-3">
            <p className="pb-2 text-[10.5px] text-zinc-500">
              Changes since you last previewed — added lines green, removed red.
              {data.truncated &&
                // The diff compares CLIPPED payloads (the server sends only
                // the first 80 rows / 20k chars) — say so, or an edit past
                // the window silently reads as "no change".
                ` Compared over the clipped preview only — changes past the first ${
                  data.kind === "sheet"
                    ? `${(data.rows ?? []).length} rows`
                    : `${(data.content ?? "").length.toLocaleString()} characters`
                } are not shown.`}
            </p>
            <div className="whitespace-pre-wrap break-words font-mono text-[11.5px] leading-relaxed">
              {diff.map((l, i) => (
                <div
                  key={i}
                  data-testid={`diff-${l.kind}`}
                  className={
                    l.kind === "added"
                      ? "bg-emerald-500/[0.08] text-emerald-300"
                      : l.kind === "removed"
                        ? "bg-rose-500/[0.08] text-rose-300/90"
                        : "text-zinc-500"
                  }
                >
                  {(l.kind === "added" ? "+ " : l.kind === "removed" ? "− " : "  ") +
                    l.text}
                </div>
              ))}
            </div>
          </div>
        ) : data.kind === "image" ? (
          // Pixels straight off GET /documents/file (inline since v1.166.0) on
          // a checkered backdrop so transparency reads as transparency.
          <div
            className="grid h-full place-items-center overflow-auto bg-[#3b3e44] p-3"
            style={{
              backgroundImage:
                "linear-gradient(45deg, rgba(255,255,255,0.05) 25%, transparent 25%, transparent 75%, rgba(255,255,255,0.05) 75%), linear-gradient(45deg, rgba(255,255,255,0.05) 25%, transparent 25%, transparent 75%, rgba(255,255,255,0.05) 75%)",
              backgroundSize: "16px 16px",
              backgroundPosition: "0 0, 8px 8px",
            }}
          >
            {imgError ? (
              <p className="max-w-[24rem] text-center text-[11.5px] text-zinc-400">
                Couldn&apos;t load this image — it may have moved or been
                deleted. Try Download or Open in {appLabelFor(path)}.
              </p>
            ) : (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={fileUrl}
                alt={name}
                onError={() => setImgError(true)}
                className="max-h-full max-w-full rounded-md object-contain"
              />
            )}
          </div>
        ) : data.kind === "pdf" ? (
          <iframe
            src={fileUrl}
            title={`Preview of ${name}`}
            className="h-full w-full border-0"
          />
        ) : data.kind === "html" ? (
          // Word-faithful page — sandbox="" blocks scripts/forms/navigation.
          <div className="flex h-full min-h-0 flex-col">
            <iframe
              sandbox=""
              srcDoc={docSrcDoc}
              title={`Preview of ${name}`}
              className="min-h-0 w-full flex-1 border-0"
            />
            {data.truncated && (
              <p className="shrink-0 py-1 text-center text-[10.5px] text-zinc-400">
                {typeof data.total_chars === "number"
                  ? `Preview clipped — ${data.total_chars.toLocaleString()} characters total; open the file for everything.`
                  : "Preview clipped — open the file for everything."}
              </p>
            )}
          </div>
        ) : data.kind === "markdown" ? (
          <div className="h-full overflow-auto bg-[#50545a] px-3">
            <div className={MD_PAGE_CLASS}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {data.content ?? ""}
              </ReactMarkdown>
            </div>
            {data.truncated && (
              <p className="pb-3 text-center text-[10.5px] text-zinc-300">
                {typeof data.total_chars === "number"
                  ? `Preview clipped — showing ${(data.content ?? "").length.toLocaleString()} of ${data.total_chars.toLocaleString()} characters; open the file for everything.`
                  : "Preview clipped — open the file for everything."}
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
                  {/* Truncation honesty (v1.166.0): name the REAL row count
                      when the daemon reports one — a bare "first 80" still
                      reads as "almost everything" on a 40,000-row sheet. */}
                  {typeof data.total_rows === "number"
                    ? `Showing the first ${(data.rows ?? []).length} of ${data.total_rows.toLocaleString()} rows — open in Excel for the full sheet.`
                    : "Showing the first 80 rows — open in Excel for the full sheet."}
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
                {typeof data.total_chars === "number"
                  ? `Preview clipped — showing ${(data.content ?? "").length.toLocaleString()} of ${data.total_chars.toLocaleString()} characters; open the file for everything.`
                  : "Preview clipped — open the file for everything."}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
