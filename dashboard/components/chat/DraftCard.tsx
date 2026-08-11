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
 * CLASS AND STYLE ATTRIBUTES ARE STRIPPED on the way out. The card renders in
 * the app's dark theme (`text-zinc-100`, accent borders); pasting those into an
 * email would either mean nothing (Tailwind classes don't travel) or, worse,
 * carry light-on-white text into the composer. What survives is semantic —
 * `<p>`, `<strong>`, `<ul>`, `<a href>` — which inherits the composer's own
 * styling and is exactly what a hand-written email looks like.
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
    if (el !== clone && el.tagName === "BUTTON") el.remove();
  }
  return clone.innerHTML.trim();
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
    const heading = subject ? `<p><strong>Subject:</strong> ${escapeHtml(subject)}</p>` : "";
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
