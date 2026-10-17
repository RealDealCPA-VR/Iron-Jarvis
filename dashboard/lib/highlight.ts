/**
 * CODE COLOURS FOR A FENCED BLOCK (v1.348.0, polish wave A, lane L3).
 *
 * The idea is borrowed from 21st.dev's agent-elements (MIT; item #7, which
 * uses Shiki); the code is ours. `components/chat/CodeBlock.tsx` draws the
 * same calm panel a code block always had and asks this file for colours.
 *
 * THE LAZY BOUNDARY. Nothing in this file imports Shiki at module load: every
 * Shiki module — the core, the regex engine, each language grammar, each
 * theme — is reached through a literal `import()` below, so the bundler puts
 * each one in its own chunk and the /chat route's first load does not grow.
 * The first chunk is fetched only when a code block with a language we colour
 * reaches the screen (CodeBlock watches for that); a chat with no code never
 * downloads any of it. Keep it that way: a static `import ... from "shiki..."`
 * here (a type-only import is erased and is fine) would pull the engine into
 * the chat page itself. `code-highlight-lazy-v1348.test.tsx` pins it.
 *
 * The fine-grained core with the JavaScript regex engine — no WebAssembly —
 * and a fixed list of common languages, each loaded the first time a block
 * needs it. Anything else stays plain text. Tokens come back as data
 * (`codeToTokens`), never as an HTML string, so nothing here is ever written
 * into the page as markup.
 *
 * Colours come from the theme DATA at runtime: a GitHub light theme when the
 * app is painted light (`<html data-scheme="light">`), the dark one
 * otherwise. A token in the theme's plain text colour carries NO colour, so it
 * keeps the panel's own text colour and plain text reads exactly as before.
 */

import type { HighlighterCore, LanguageInput, ThemedToken, ThemeInput } from "shiki/core";

export type CodeScheme = "light" | "dark";

/** A block longer than this stays plain (tokenising it would stall the page). */
export const MAX_HIGHLIGHT_LINES = 2_000;
/** ...and so does one bigger than this many characters (~100 KB). */
export const MAX_HIGHLIGHT_CHARS = 100_000;
/** A single line longer than this is left uncoloured inside a coloured block
 *  (a minified bundle or a one-line JSON dump). */
export const MAX_HIGHLIGHT_LINE_CHARS = 5_000;

/** One run of text in a highlighted line. `color` is absent when the theme
 *  paints it in its plain text colour (the panel's own colour shows then). */
export interface CodeToken {
  content: string;
  color?: string;
  italic?: boolean;
}

export interface HighlightedCode {
  /** The exact text that was highlighted (CodeBlock compares it to what it
   *  is showing now: a streaming block keeps its coloured start). */
  code: string;
  /** The grammar it was read with (one of the ids in `codeLangId`). */
  lang: string;
  scheme: CodeScheme;
  lines: CodeToken[][];
  /** How long the tokenising itself took, in ms (loading excluded). */
  ms: number;
}

/* Every grammar we colour, each behind its OWN literal import() so the
   bundler makes one small chunk per language and fetches only the ones a
   reply actually uses. A template string here would make the bundler emit a
   chunk for every grammar Shiki ships. */
const GRAMMARS: Record<string, LanguageInput> = {
  typescript: () => import("shiki/langs/typescript.mjs"),
  tsx: () => import("shiki/langs/tsx.mjs"),
  javascript: () => import("shiki/langs/javascript.mjs"),
  jsx: () => import("shiki/langs/jsx.mjs"),
  json: () => import("shiki/langs/json.mjs"),
  python: () => import("shiki/langs/python.mjs"),
  bash: () => import("shiki/langs/bash.mjs"),
  powershell: () => import("shiki/langs/powershell.mjs"),
  html: () => import("shiki/langs/html.mjs"),
  css: () => import("shiki/langs/css.mjs"),
  sql: () => import("shiki/langs/sql.mjs"),
  yaml: () => import("shiki/langs/yaml.mjs"),
  markdown: () => import("shiki/langs/markdown.mjs"),
  diff: () => import("shiki/langs/diff.mjs"),
  go: () => import("shiki/langs/go.mjs"),
  rust: () => import("shiki/langs/rust.mjs"),
  java: () => import("shiki/langs/java.mjs"),
  csharp: () => import("shiki/langs/csharp.mjs"),
};

/** What a model writes after the fence → the grammar we read it with. */
const ALIASES: Record<string, string> = {
  ts: "typescript",
  mts: "typescript",
  cts: "typescript",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  node: "javascript",
  jsonc: "json",
  json5: "json",
  py: "python",
  python3: "python",
  py3: "python",
  sh: "bash",
  shell: "bash",
  shellscript: "bash",
  zsh: "bash",
  ps1: "powershell",
  ps: "powershell",
  pwsh: "powershell",
  htm: "html",
  postgres: "sql",
  postgresql: "sql",
  mysql: "sql",
  sqlite: "sql",
  yml: "yaml",
  md: "markdown",
  patch: "diff",
  golang: "go",
  rs: "rust",
  cs: "csharp",
  "c#": "csharp",
};

const THEMES: Record<CodeScheme, { name: string; load: ThemeInput }> = {
  dark: { name: "github-dark-default", load: () => import("shiki/themes/github-dark-default.mjs") },
  light: { name: "github-light-default", load: () => import("shiki/themes/github-light-default.mjs") },
};

/** The grammar id for a fence's language, or null when we leave it plain. */
export function codeLangId(lang: string | null | undefined): string | null {
  const key = (lang || "").trim().toLowerCase();
  if (!key) return null;
  if (Object.prototype.hasOwnProperty.call(GRAMMARS, key)) return key;
  return Object.prototype.hasOwnProperty.call(ALIASES, key) ? ALIASES[key] : null;
}

/** Too long to colour: more than MAX_HIGHLIGHT_LINES lines or
 *  MAX_HIGHLIGHT_CHARS characters. Such a block always stays plain. */
export function tooLargeToHighlight(code: string): boolean {
  if (code.length > MAX_HIGHLIGHT_CHARS) return true;
  let lines = 1;
  for (let i = code.indexOf("\n"); i !== -1; i = code.indexOf("\n", i + 1)) {
    if (++lines > MAX_HIGHLIGHT_LINES) return true;
  }
  return false;
}

/* ---- The app's scheme, observed once for every block -------------------- */

/** The scheme the page is painted in: `<html data-scheme>`, dark unless it
 *  says light (the same attribute globals.css keys every light re-ink on). */
export function readCodeScheme(): CodeScheme {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.getAttribute("data-scheme") === "light" ? "light" : "dark";
}

const schemeListeners = new Set<() => void>();
let schemeObserver: MutationObserver | null = null;

/** Subscribe to scheme changes (for `useSyncExternalStore`). ONE observer on
 *  <html> serves every code block on the page, however many there are. */
export function subscribeCodeScheme(onChange: () => void): () => void {
  schemeListeners.add(onChange);
  if (!schemeObserver && typeof MutationObserver !== "undefined" && typeof document !== "undefined") {
    schemeObserver = new MutationObserver(() => {
      for (const fn of [...schemeListeners]) fn();
    });
    schemeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-scheme"] });
  }
  return () => {
    schemeListeners.delete(onChange);
    if (schemeListeners.size === 0 && schemeObserver) {
      schemeObserver.disconnect();
      schemeObserver = null;
    }
  };
}

/* ---- Results kept for a remount (switching chats and back) -------------- */

const MEMO_MAX_ENTRIES = 64;
const MEMO_MAX_CHARS = 400_000;
const memo = new Map<string, HighlightedCode>();
let memoChars = 0;

const memoKey = (code: string, lang: string, scheme: CodeScheme) => `${scheme}\u0000${lang}\u0000${code}`;

function remember(result: HighlightedCode): void {
  const key = memoKey(result.code, result.lang, result.scheme);
  const old = memo.get(key);
  if (old) {
    memo.delete(key);
    memoChars -= old.code.length;
  }
  memo.set(key, result);
  memoChars += result.code.length;
  for (const [k, v] of memo) {
    if (memo.size <= MEMO_MAX_ENTRIES && memoChars <= MEMO_MAX_CHARS) break;
    memo.delete(k);
    memoChars -= v.code.length;
  }
}

/** A finished highlight of exactly this code, if one is still remembered —
 *  so a block that remounts shows its colours at once instead of flashing
 *  plain. Synchronous and cheap; never loads anything. */
export function peekHighlight(code: string, lang: string | null | undefined, scheme: CodeScheme): HighlightedCode | null {
  const id = codeLangId(lang);
  if (!id) return null;
  return memo.get(memoKey(code, id, scheme)) ?? null;
}

/* ---- Loading (all of it lazy) ------------------------------------------- */

let core: Promise<HighlighterCore> | null = null;
const grammarsLoading = new Map<string, Promise<void>>();
const themesLoading = new Map<CodeScheme, Promise<void>>();

function loadCore(): Promise<HighlighterCore> {
  if (!core) {
    core = (async () => {
      const [{ createHighlighterCore }, { createJavaScriptRegexEngine }] = await Promise.all([
        import("shiki/core"),
        import("shiki/engine/javascript"),
      ]);
      // `forgiving`: a pattern the JavaScript engine cannot express is skipped
      // (that bit of a line stays plain) instead of failing the whole block.
      return createHighlighterCore({
        themes: [],
        langs: [],
        engine: createJavaScriptRegexEngine({ forgiving: true }),
      });
    })();
    // A failed load (a chunk that did not arrive) is forgotten, so a later
    // block can try again; this block simply stays plain.
    core.catch(() => {
      core = null;
    });
  }
  return core;
}

function once<K>(cache: Map<K, Promise<void>>, key: K, load: () => Promise<void>): Promise<void> {
  let p = cache.get(key);
  if (!p) {
    p = load();
    cache.set(key, p);
    p.catch(() => cache.delete(key));
  }
  return p;
}

/**
 * Colour `code` as `lang` in the given scheme. Resolves to null when the
 * language is not one we colour or the block is too large (nothing is loaded
 * then); rejects if Shiki could not load or tokenise — the caller keeps the
 * plain text.
 */
export async function highlightCode(
  code: string,
  lang: string | null | undefined,
  scheme: CodeScheme,
): Promise<HighlightedCode | null> {
  const id = codeLangId(lang);
  if (!id || tooLargeToHighlight(code)) return null;
  const known = memo.get(memoKey(code, id, scheme));
  if (known) return known;

  const hl = await loadCore();
  const theme = THEMES[scheme];
  await Promise.all([
    once(grammarsLoading, id, () => hl.loadLanguage(GRAMMARS[id])),
    once(themesLoading, scheme, () => hl.loadTheme(theme.load)),
  ]);

  const started = now();
  const result = hl.codeToTokens(code, {
    lang: id,
    theme: theme.name,
    tokenizeMaxLineLength: MAX_HIGHLIGHT_LINE_CHARS,
  });
  const plain = (result.fg || "").toLowerCase();
  const lines = result.tokens.map((line) => line.map((t) => toCodeToken(t, plain)));
  const out: HighlightedCode = { code, lang: id, scheme, lines, ms: now() - started };
  remember(out);
  return out;
}

function toCodeToken(t: ThemedToken, plain: string): CodeToken {
  const token: CodeToken = { content: t.content };
  const color = (t.color || "").toLowerCase();
  if (color && color !== plain) token.color = t.color;
  // FontStyle is a bit set: Italic = 1 (NotSet = -1, so test > 0 first).
  // Bold and underline are left out on purpose: the panel keeps one weight.
  if (typeof t.fontStyle === "number" && t.fontStyle > 0 && (t.fontStyle & 1) === 1) token.italic = true;
  return token;
}

function now(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}
