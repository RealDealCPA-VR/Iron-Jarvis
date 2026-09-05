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
import { Check, Copy } from "lucide-react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { API_BASE, ijToken } from "@/lib/api";
import { DraftCard, draftFromFence } from "@/components/chat/DraftCard";

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
function MarkdownMedia({ src, alt }: { src?: string | Blob; alt?: string }) {
  const raw = typeof src === "string" ? src : "";
  if (!raw) return null;
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
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-white/10 bg-white/[0.05] px-2.5 py-1.5 text-left font-medium text-zinc-100">
      {children}
    </th>
  ),
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
    <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={MD_COMPONENTS}>
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
