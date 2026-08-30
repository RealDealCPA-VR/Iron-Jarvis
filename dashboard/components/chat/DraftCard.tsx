"use client";

/**
 * A drafted email/message, boxed and copyable as RICH TEXT (v1.161.0).
 *
 * THE POINT IS THE CLIPBOARD FLAVOUR, not the box. Copying a draft as
 * `text/plain` and pasting it into Gmail or Outlook loses every bit of
 * structure — bold becomes literal asterisks, lists become hyphens, links
 * become bare URLs — so the user reformats by hand, which is the work they
 * asked the assistant to do. Writing `text/html` alongside the plain text lets
 * the composer take the formatted version, and the paste arrives looking like
 * what was on screen.
 *
 * THE HTML IS TAKEN FROM THE RENDERED DOM, not generated a second time from the
 * markdown. Two renderers drift: the day someone adds a table override to the
 * chat markdown, a separately-written serializer keeps emitting the old shape
 * and the paste stops matching the screen. Reading `innerHTML` off the node the
 * user is looking at makes that class of bug impossible.
 *
 * THE APP'S PRESENTATION IS STRIPPED AND THE DESTINATION'S IS SUPPLIED. The
 * card renders in the app's dark theme (`text-zinc-100`, accent borders), and
 * pasting that into an email would either mean nothing (Tailwind classes don't
 * travel) or carry light-on-white text into the composer — so `class` and
 * `style` come off.
 *
 * Stripping ALONE was shipped in v1.161.0 and was not enough: the result was
 * semantically perfect and visually flat. Outlook renders through WORD's
 * engine, which gives a bare `<p>` a ZERO margin — the blank lines you see in a
 * browser come from the BROWSER's default stylesheet, and a stylesheet never
 * crosses a clipboard. The user pasted a draft and had to put the spacing back
 * by hand. So the strip is followed by `EMAIL_STYLES`: inline margins, in
 * POINTS, which is the one form Word honours. Colour and font are still left
 * out on purpose, so the text adopts the composer's own theme.
 *
 * `hardenLineBreaks` closes the other half: a single newline is a SPACE in
 * markdown, which is right for prose and wrong for a signature block.
 *
 * DEGRADED COPIES SAY SO. Not every surface can write two flavours: an older
 * browser, a locked-down webview, or a clipboard permission refusal leaves only
 * `writeText`. The button then reports "Copied as text" rather than "Copied",
 * because a user told "Copied" who then pastes and finds the formatting gone
 * has been misled about the one thing this component exists to do.
 */

import { Check, Copy, Mail } from "lucide-react";
import {
  Children,
  isValidElement,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

/**
 * Fence languages that mark a draft the USER will send, rather than code.
 *
 * MUST stay in sync with `DRAFT_BLOCK` in `daemon/chat_turn.py`, which is the
 * instruction telling the model which word to write. A fence the model never
 * emits renders nothing, and a word the dashboard does not accept renders a
 * grey code block — the failure is silent in both directions, so
 * `tests/test_draft_card_v1161.py` asserts the two sides name the same word.
 */
export const DRAFT_LANGS = new Set(["email", "draft", "message"]);

/**
 * The fence's language, read off remark's `language-xxx` class.
 *
 * react-markdown hands `<pre>` a single `<code className="language-email">`
 * child; the class is the ONLY place the fence's info string survives, so this
 * is the seam the whole feature hangs from.
 */
export function fenceLang(children: ReactNode): string {
  let lang = "";
  Children.forEach(children, (child) => {
    if (!isValidElement(child)) return;
    const cls = (child.props as { className?: string }).className || "";
    const found = /language-([\w-]+)/.exec(cls);
    if (found) lang = found[1].toLowerCase();
  });
  return lang;
}

/** Attributes that must never reach the clipboard — see the module docstring. */
const STRIPPED_ATTRS = ["class", "style", "data-testid"];

/**
 * Inline spacing applied to the CLIPBOARD copy, per tag (v1.163.0).
 *
 * WHY THE STRIPPED VERSION WASN'T ENOUGH. Removing every `class` and `style`
 * left semantically perfect HTML — `<p>`, `<ul>`, `<strong>` — that pasted into
 * Outlook with the paragraphs run together, so the spacing had to be redone by
 * hand. Outlook renders through WORD's engine, and Word gives a bare `<p>` a
 * ZERO margin; the blank lines you see in a browser come from the browser's own
 * default stylesheet, which never crosses the clipboard. Semantic structure and
 * VISIBLE structure are not the same thing once the receiving app supplies the
 * defaults.
 *
 * So presentation is not stripped and then abandoned — it is stripped and then
 * REPLACED with the one form Outlook honours: inline styles, in POINTS (Word's
 * unit). Colour and font are still left out on purpose so the text adopts the
 * composer's own theme instead of arriving as light-grey-on-white.
 *
 * The visible card is untouched: this is applied to a CLONE.
 */
const EMAIL_STYLES: Record<string, string> = {
  P: "margin:0 0 10pt 0;",
  UL: "margin:0 0 10pt 0; padding-left:24pt;",
  OL: "margin:0 0 10pt 0; padding-left:24pt;",
  LI: "margin:0 0 4pt 0;",
  BLOCKQUOTE: "margin:0 0 10pt 12pt; padding-left:9pt; border-left:1.5pt solid #cccccc;",
  H1: "margin:0 0 8pt 0; font-size:14pt; font-weight:bold;",
  H2: "margin:0 0 8pt 0; font-size:13pt; font-weight:bold;",
  H3: "margin:0 0 6pt 0; font-size:12pt; font-weight:bold;",
  PRE: "margin:0 0 10pt 0; font-family:Consolas,monospace; white-space:pre-wrap;",
  TABLE: "border-collapse:collapse; margin:0 0 10pt 0;",
  TH: "border:0.75pt solid #999999; padding:4pt 6pt; text-align:left;",
  TD: "border:0.75pt solid #999999; padding:4pt 6pt;",
  HR: "margin:0 0 10pt 0;",
};

type Bridge = {
  clipboardWriteHtml?: (html: string, text: string) => Promise<unknown>;
  clipboardWriteText?: (text: string) => Promise<unknown>;
};

function bridge(): Bridge | undefined {
  if (typeof window === "undefined") return undefined;
  return (window as unknown as { ironjarvis?: Bridge }).ironjarvis;
}

/**
 * Semantic HTML for `node`, with presentation stripped.
 *
 * Clones first: the visible card must not lose its styling because someone
 * pressed Copy.
 */
export function cleanHtml(node: HTMLElement): string {
  const clone = node.cloneNode(true) as HTMLElement;
  for (const el of [clone, ...Array.from(clone.querySelectorAll("*"))]) {
    for (const attr of STRIPPED_ATTRS) el.removeAttribute(attr);
    // Buttons and other affordances can be rendered inside the body by future
    // markdown overrides; they are chrome, never content.
    if (el !== clone && el.tagName === "BUTTON") {
      el.remove();
      continue;
    }
    // Re-apply the ONE kind of presentation the destination honours. Without
    // this a paste into Outlook loses every paragraph break — see EMAIL_STYLES.
    const style = el === clone ? undefined : EMAIL_STYLES[el.tagName];
    if (style) el.setAttribute("style", style);
  }
  return clone.innerHTML.trim();
}

/**
 * Turn the line breaks that MEAN something into hard ones (v1.215.1).
 *
 * Markdown collapses a single newline into a space. That is right for prose
 * and wrong for the two places an email actually uses one — a signature block
 * and an address — so v1.163.0 hardened every soft newline in a draft.
 *
 * WHICH BROKE THE OTHER HALF, and it took a look at the real clipboard payload
 * to see it. Models hard-wrap their output at around 80 columns, so a single
 * sentence arrives as two source lines; hardening every newline turned that
 * wrap into a `<br>` and the pasted email carried a line break through the
 * middle of a sentence. Captured from the shipping build:
 *
 *   <p>To finish your return before the <strong>October 15</strong>
 *   extended deadline, I need three<br>
 *   things from you:</p>
 *
 * A newline inside a paragraph is genuinely ambiguous, and neither blanket
 * answer is right. The signal that separates the two cases is LINE LENGTH: a
 * line that wrapped is long by construction (it wrapped because it ran out of
 * width), while the lines of a signature or an address are short because the
 * writer ended them. So the decision is made per BLOCK — a run of non-blank
 * lines — rather than per line:
 *
 *   contains a long line  →  it is wrapped prose; leave the newlines soft
 *   all lines are short   →  they were ended on purpose; harden them
 *
 * Structured blocks (lists, headings, quotes, tables) are skipped outright:
 * markdown already gives each of their lines its own line, so a hard break
 * would be at best redundant and at worst a stray `<br>` inside an `<li>`.
 *
 * Fenced code is skipped for the original reason — inside a fence a newline is
 * already literal, and padding those lines would corrupt the quoted content.
 */

/** A line at least this long is assumed to have WRAPPED rather than to have
 *  been ended deliberately. Model output wraps in the 72–100 column range and
 *  signature/address lines are rarely past 40, so the gap is wide and the
 *  exact number is not load-bearing. */
export const WRAP_HINT = 60;

/** Lines markdown already lays out itself — a hard break adds nothing. */
const STRUCTURED = /^\s*(#{1,6}\s|>|\||[-*+]\s|\d+[.)]\s)/;

export function hardenLineBreaks(markdown: string): string {
  const lines = markdown.split("\n");

  // Which lines are inside a fence (the fence markers themselves count, so a
  // block never straddles one).
  const fenced: boolean[] = [];
  let inFence = false;
  for (const line of lines) {
    if (/^\s*(```|~~~)/.test(line)) {
      fenced.push(true);
      inFence = !inFence;
    } else {
      fenced.push(inFence);
    }
  }

  const out = lines.slice();
  let i = 0;
  while (i < lines.length) {
    if (fenced[i] || !lines[i].trim()) {
      i += 1;
      continue;
    }
    let j = i;
    while (j < lines.length && lines[j].trim() && !fenced[j]) j += 1;
    const block = lines.slice(i, j);
    const structured = block.some((l) => STRUCTURED.test(l));
    const wrapped = block.some((l) => l.trim().length >= WRAP_HINT);
    if (!structured && !wrapped) {
      for (let k = i; k < j - 1; k += 1) {
        // A line that already ends in a hard break is left alone.
        if (/\s{2,}$/.test(lines[k])) continue;
        out[k] = lines[k] + "  ";
      }
    }
    i = j;
  }
  return out.join("\n");
}

/**
 * The PLAIN-TEXT flavour, actually in plain text (v1.215.1).
 *
 * `text/plain` was the fence's body verbatim — which is MARKDOWN. Anything
 * that took that flavour got `**October 1**` and `[the portal](https://…)`
 * literally, and the whole point of the two-flavour copy is that neither one
 * needs fixing by hand. Captured from the shipping build:
 *
 *   To finish your return before the **October 15** extended deadline…
 *
 * That flavour is not a rare path: it is what a plain-text composer takes, what
 * a `Ctrl+Shift+V` paste takes, and what the whole copy degrades to when the
 * rich write is unavailable (`copyRich` → "plain", which the button reports).
 *
 * Inline syntax is unwrapped; STRUCTURE is kept, because in plain text a "- "
 * bullet and a blank line between paragraphs are how structure survives at all.
 * Fenced content is left exactly as written — it is quoted material.
 */
export function plainFromMarkdown(markdown: string): string {
  const lines = markdown.split("\n");
  let inFence = false;
  return lines
    .map((line) => {
      if (/^\s*(```|~~~)/.test(line)) {
        inFence = !inFence;
        return null; // the fence markers are syntax, not content
      }
      if (inFence) return line;
      return (
        line
          // Markdown's own hard-break marker has done its job by now.
          .replace(/\s{2,}$/, "")
          // Headings and quote markers: the words, not the punctuation.
          .replace(/^(\s*)#{1,6}\s+/, "$1")
          .replace(/^(\s*)>\s?/, "$1")
          // Images first (they are links with a leading !), then links. The
          // URL is dropped rather than kept in parentheses: a signature line
          // reading "Valentino (https://…)" is not tidier than the name.
          .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
          .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
          // Emphasis. Paired markers only, so snake_case and a lone asterisk
          // survive untouched.
          .replace(/\*\*\*([^*\n]+)\*\*\*/g, "$1")
          .replace(/\*\*([^*\n]+)\*\*/g, "$1")
          .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1$2")
          .replace(/__([^_\n]+)__/g, "$1")
          .replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,;:!?]|$)/g, "$1$2")
          // Inline code.
          .replace(/`([^`\n]+)`/g, "$1")
      );
    })
    .filter((line): line is string => line !== null)
    .join("\n");
}

/** Whether both clipboard flavours are reachable in this environment. */
export function canCopyRich(): boolean {
  if (bridge()?.clipboardWriteHtml) return true;
  return (
    typeof navigator !== "undefined" &&
    !!navigator.clipboard?.write &&
    typeof ClipboardItem !== "undefined"
  );
}

/**
 * Put `html` + `text` on the clipboard. Resolves "rich" or "plain" — the caller
 * shows which, so a silent downgrade can never be reported as a full copy.
 */
export async function copyRich(html: string, text: string): Promise<"rich" | "plain"> {
  const ij = bridge();
  // Electron's native clipboard first: inside the desktop app the async
  // Clipboard API can be permission-gated, and this path never is. (Same
  // reasoning as the terminal's copy — see TerminalPane.)
  if (ij?.clipboardWriteHtml) {
    try {
      await ij.clipboardWriteHtml(html, text);
      return "rich";
    } catch {
      /* fall through to the browser path */
    }
  }
  if (navigator.clipboard?.write && typeof ClipboardItem !== "undefined") {
    try {
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/html": new Blob([html], { type: "text/html" }),
          "text/plain": new Blob([text], { type: "text/plain" }),
        }),
      ]);
      return "rich";
    } catch {
      /* fall through to plain text */
    }
  }
  if (ij?.clipboardWriteText) {
    await ij.clipboardWriteText(text);
    return "plain";
  }
  await navigator.clipboard.writeText(text);
  return "plain";
}

/**
 * Everything the chat renderer needs to turn a fence into a card, or `null`
 * when the fence is ordinary code.
 *
 * EXISTS BECAUSE THE WIRING KEPT BEING THE UNTESTED PART. The decision (is this
 * a draft?), the subject split and the line-break hardening used to live inline
 * in `chat/page.tsx`, with the test file holding its own copy of the same
 * sequence — so a mutation that deleted the real call site left every test
 * green. One function, used by both, closes that: a step dropped here fails
 * loudly instead of silently rendering a worse card.
 */
export function draftFromFence(
  children: ReactNode,
  rawText: string,
): { subject?: string; text: string; markdown: string } | null {
  if (!DRAFT_LANGS.has(fenceLang(children))) return null;
  const { subject, body } = splitSubject(rawText);
  //: `text` is the plain-text flavour (what a plain paste gets); `markdown` is
  //: what gets RENDERED, with soft newlines hardened so a signature block does
  //: not collapse onto one line.
  //: `text` is the PLAIN-TEXT flavour — markdown syntax unwrapped, structure
  //: kept — and `markdown` is what gets RENDERED, with the line breaks that
  //: mean something hardened. They are two different jobs on the same body and
  //: neither is the raw fence: see plainFromMarkdown and hardenLineBreaks.
  return {
    subject,
    text: plainFromMarkdown(body),
    markdown: hardenLineBreaks(body),
  };
}

export function DraftCard({
  subject,
  text,
  children,
}: {
  /** The `Subject:` line, when the draft carried one. */
  subject?: string;
  /** The draft as plain text — the fallback flavour, and the source of truth
   *  for what is copied when rich text is unavailable. */
  text: string;
  /** The rendered body. Rendered by the CALLER so this card cannot drift from
   *  the chat's own markdown styling. */
  children: ReactNode;
}) {
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const timer = useRef<number | null>(null);
  const [state, setState] = useState<"idle" | "rich" | "plain" | "failed">("idle");
  const [subjectCopied, setSubjectCopied] = useState(false);

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  const flash = useCallback((next: "rich" | "plain" | "failed") => {
    setState(next);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState("idle"), 2200);
  }, []);

  const copyAll = useCallback(async () => {
    const node = bodyRef.current;
    // The subject travels WITH the body: it is the first thing the user needs
    // in the composer, and a copy that silently drops it means retyping the one
    // line the assistant was asked to write.
    const heading = subject
      ? `<p style="${EMAIL_STYLES.P}"><strong>Subject:</strong> ${escapeHtml(subject)}</p>`
      : "";
    const html = `${heading}${node ? cleanHtml(node) : `<p>${escapeHtml(text)}</p>`}`;
    const plain = subject ? `Subject: ${subject}\n\n${text}` : text;
    try {
      flash(await copyRich(html, plain));
    } catch {
      flash("failed");
    }
  }, [flash, subject, text]);

  const copySubject = useCallback(async () => {
    if (!subject) return;
    try {
      const ij = bridge();
      if (ij?.clipboardWriteText) await ij.clipboardWriteText(subject);
      else await navigator.clipboard.writeText(subject);
      setSubjectCopied(true);
      window.setTimeout(() => setSubjectCopied(false), 1500);
    } catch {
      /* nothing useful to surface for a subject line */
    }
  }, [subject]);

  const label =
    state === "rich"
      ? "Copied"
      : state === "plain"
        ? "Copied as text"
        : state === "failed"
          ? "Copy failed"
          : "Copy";

  return (
    <div
      data-testid="draft-card"
      className="my-2.5 max-w-2xl overflow-hidden rounded-xl border border-accent/20 bg-ink-900/50"
    >
      <div className="flex items-center gap-2 border-b border-white/[0.06] bg-white/[0.02] px-3 py-2">
        <Mail size={12} className="shrink-0 text-accent-soft/80" />
        {subject ? (
          <>
            <span className="min-w-0 flex-1 truncate text-[12.5px] text-zinc-200">
              <span className="text-zinc-500">Subject: </span>
              {subject}
            </span>
            <button
              type="button"
              onClick={() => void copySubject()}
              title="Copy the subject on its own"
              aria-label="Copy subject"
              className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
            >
              {subjectCopied ? (
                <Check size={11} className="text-emerald-400" />
              ) : (
                <Copy size={11} />
              )}
            </button>
          </>
        ) : (
          <span className="flex-1 text-[12.5px] text-zinc-400">Draft</span>
        )}
      </div>

      <div
        ref={bodyRef}
        data-testid="draft-body"
        className="px-3.5 py-2.5 text-[13.5px] leading-relaxed text-zinc-200"
      >
        {children}
      </div>

      <div className="flex items-center justify-between gap-2 border-t border-white/[0.06] px-3 py-1.5">
        <span className="truncate text-[11px] text-zinc-500">
          {state === "plain"
            ? "formatting could not be copied here — paste as plain text"
            : "paste into your email — formatting is kept"}
        </span>
        <button
          type="button"
          onClick={() => void copyAll()}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-accent/25 bg-accent/[0.08] px-2.5 py-1 text-[11.5px] text-accent-soft transition-colors hover:bg-accent/[0.16]"
        >
          {state === "rich" || state === "plain" ? (
            <Check size={11} className="text-emerald-400" />
          ) : (
            <Copy size={11} />
          )}
          {label}
        </button>
      </div>
    </div>
  );
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/**
 * Split a draft into its `Subject:` line and the rest.
 *
 * Only a subject on the FIRST non-empty line counts. A "Subject:" appearing
 * mid-body is part of the message the user is writing (quoting a thread, for
 * instance), and promoting it to the header would silently delete a line from
 * the body.
 */
export function splitSubject(raw: string): { subject?: string; body: string } {
  const text = raw.replace(/^\s*\n/, "");
  const match = /^subject:[ \t]*(.+?)[ \t]*(?:\r?\n|$)/i.exec(text);
  if (!match) return { body: raw.trim() };
  const subject = match[1].trim();
  if (!subject) return { body: raw.trim() };
  return { subject, body: text.slice(match[0].length).replace(/^\s*\n/, "").trim() };
}
