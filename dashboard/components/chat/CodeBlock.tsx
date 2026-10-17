"use client";

/**
 * A FENCED CODE BLOCK, NOW IN COLOUR (v1.348.0, polish wave A, lane L3).
 *
 * The idea is ported from 21st.dev's agent-elements (MIT; item #7); the code
 * is ours. A drop-in for the code-block branch of `components/Markdown.tsx`
 * (`MarkdownPre`, after the draft and chart fences are routed elsewhere): the
 * SAME panel, the same classes and the same hover copy button as before —
 * plus syntax colours. Nothing new is boxed and nothing moves.
 *
 *   - Plain text shows at once. The colours replace it when they are ready;
 *     if anything goes wrong the plain text simply stays (no message: the
 *     code is still there to read and copy).
 *   - Shiki is loaded lazily by `lib/highlight.ts`, and only once a block with
 *     a language we colour is on (or about to come on) screen. A block whose
 *     language we do not colour, or one past the size cap (2,000 lines /
 *     100 KB), never loads anything.
 *   - Colours follow the app's light or dark scheme, and a scheme switch
 *     recolours every block.
 *   - While a reply streams, the coloured start of the block stays coloured
 *     and the newly arrived tail shows as plain text until the next pass
 *     (at most one pass every few hundred ms).
 *   - The copy button copies the raw text, exactly as before.
 *
 * Tokens are React spans with an inline colour taken from the theme data —
 * never an HTML string, and never a colour written in this source.
 */

import { memo, useEffect, useRef, useState, useSyncExternalStore, type ReactNode, type RefObject } from "react";
import { Check, Copy } from "lucide-react";
import {
  codeLangId,
  highlightCode,
  peekHighlight,
  readCodeScheme,
  subscribeCodeScheme,
  tooLargeToHighlight,
  type CodeScheme,
  type CodeToken,
  type HighlightedCode,
} from "@/lib/highlight";

/** The shortest gap between two highlight passes over one block while its
 *  text keeps changing (a streaming reply). A slow pass widens the gap. */
const MIN_PASS_GAP_MS = 250;

/** Start loading a little before a block scrolls into view. */
const VIEW_MARGIN = "200px 0px";

const serverScheme = (): CodeScheme => "dark";

/** True once the element has been on screen (stays true). Without an
 *  IntersectionObserver (old engines, jsdom) everything counts as on screen. */
function useSeenOnScreen(ref: RefObject<HTMLElement | null>, enabled: boolean): boolean {
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (seen || !enabled) return;
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      setSeen(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setSeen(true);
          io.disconnect();
        }
      },
      { rootMargin: VIEW_MARGIN },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [ref, seen, enabled]);
  return seen;
}

/** The copy button, as it always looked (the check uses the success tone so
 *  it re-inks with the theme). Copies the raw text. */
function CodeCopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );
  function copy() {
    try {
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
    } catch {
      /* no clipboard API at all — same */
    }
  }
  return (
    <>
      <button
        type="button"
        onClick={copy}
        title="Copy code"
        aria-label="Copy code"
        className="absolute right-2 top-2 z-10 grid h-6 w-6 place-items-center rounded-md border border-white/10 bg-white/[0.06] text-zinc-400 opacity-0 transition-opacity hover:text-zinc-100 focus-visible:opacity-100 group-hover/code:opacity-100 motion-reduce:transition-none"
      >
        {copied ? <Check size={12} aria-hidden className="text-tone-success" /> : <Copy size={12} aria-hidden />}
      </button>
      {/* Said, not shown: a screen reader hears that the copy worked. A bare
          live region, not role="status" (that role is how the chat's own
          progress notes are found). */}
      <span className="sr-only" aria-live="polite">
        {copied ? "Copied" : ""}
      </span>
    </>
  );
}

function renderTokens(lines: CodeToken[][]): ReactNode[] {
  const out: ReactNode[] = [];
  lines.forEach((line, i) => {
    if (i > 0) out.push("\n");
    line.forEach((t, j) => {
      if (!t.color && !t.italic) {
        out.push(t.content);
        return;
      }
      out.push(
        <span key={`${i}:${j}`} style={{ color: t.color, fontStyle: t.italic ? "italic" : undefined }}>
          {t.content}
        </span>,
      );
    });
  });
  return out;
}

/** The coloured lines of ONE finished pass, drawn once. A streaming reply
 *  re-renders its block every frame while only the plain tail changes; built
 *  inline, every frame rebuilt every token span (review, measured in jsdom: a
 *  coloured 1,000-line block cost ~65 ms per re-render against 0.5 ms plain).
 *  `lines` is the pass's own array, so it changes only when a pass lands. */
const ColouredLines = memo(function ColouredLines({ lines }: { lines: CodeToken[][] }) {
  return <>{renderTokens(lines)}</>;
});

export function CodeBlock({ code, lang }: { code: string; lang?: string | null }) {
  const langId = codeLangId(lang);
  const wanted = langId !== null && !tooLargeToHighlight(code);
  const scheme = useSyncExternalStore(subscribeCodeScheme, readCodeScheme, serverScheme);
  const boxRef = useRef<HTMLDivElement>(null);
  const seen = useSeenOnScreen(boxRef, wanted);

  const [result, setResult] = useState<HighlightedCode | null>(null);
  const mounted = useRef(true);
  const lastPassAt = useRef(0);
  const lastCost = useRef(0);
  const issued = useRef(0);
  const accepted = useRef(0);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // The last pass, if it was made for this scheme and language.
  const current = wanted && result && result.scheme === scheme && result.lang === langId ? result : null;
  const upToDate = current !== null && current.code === code;
  // A finished highlight of exactly this text (a remount — switching chats
  // and back) shows at once, before anything is loaded or observed.
  const remembered = wanted && !upToDate ? peekHighlight(code, lang, scheme) : null;

  useEffect(() => {
    if (!wanted || !seen || upToDate || remembered) return;
    // Throttle, don't debounce: a streaming block still gets a pass every
    // gap, and the last change always gets one. The first pass (or the
    // first after a scheme switch) goes at once.
    const gap = Math.max(MIN_PASS_GAP_MS, lastCost.current * 4);
    const wait = current ? Math.max(0, gap - (performance.now() - lastPassAt.current)) : 0;
    const timer = window.setTimeout(() => {
      lastPassAt.current = performance.now();
      const ticket = ++issued.current;
      highlightCode(code, lang, scheme).then(
        (next) => {
          // An older pass can still be useful (its text may be the start of
          // what is shown now), but it must never replace a newer one.
          if (!mounted.current || !next || ticket < accepted.current) return;
          accepted.current = ticket;
          lastCost.current = next.ms;
          setResult(next);
        },
        () => {
          /* a highlight that fails keeps the plain text, silently */
        },
      );
    }, wait);
    return () => window.clearTimeout(timer);
  }, [code, lang, scheme, wanted, seen, upToDate, remembered, current]);

  const shown = remembered ?? current;
  let body: ReactNode = code;
  let coloured = false;
  if (shown && code.startsWith(shown.code)) {
    // Up to date, or streaming: the start is coloured, the tail that arrived
    // since is plain (empty when up to date). One shape for both, so the
    // coloured part is never remounted when the tail empties.
    body = [<ColouredLines key="coloured" lines={shown.lines} />, code.slice(shown.code.length)];
    coloured = true;
  }

  return (
    <div
      ref={boxRef}
      className="group/code relative my-2"
      data-testid="code-block"
      data-lang={langId ?? undefined}
      data-highlighted={coloured ? "true" : "false"}
    >
      <CodeCopyButton text={code} />
      <pre className="overflow-x-auto rounded border border-white/[0.06] bg-ink-900/80 p-3 font-mono text-xs leading-relaxed text-zinc-200">
        <code className={lang ? `language-${lang}` : undefined}>{body}</code>
      </pre>
    </div>
  );
}
