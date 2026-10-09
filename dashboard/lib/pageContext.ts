/**
 * "Ask Jarvis about this page" (v1.325.0) — the pure half.
 *
 * THE NEED: the user is standing on a page (Usage, a session, Settings) and
 * wants to ask about what is IN FRONT OF THEM — "what does this mean", "why is
 * this red". Chat cannot see another page, so the palette captures the page's
 * readable text at the moment of the press, hands it to the chat page through
 * sessionStorage, and the chat page sends it with that turn as `page_context`
 * (the daemon fences it as untrusted data, like a web page's text).
 *
 * WHAT IS CAPTURED, and what never is:
 *  - the page's own content: `#main-content` (MainContent, inside the layout's
 *    `<main>` scroll root), else `<main>`, else the body;
 *  - NEVER what the user is typing: input / textarea / select values and
 *    contenteditable regions are skipped whole (a half-written client email in
 *    a box is not "the page");
 *  - never the app's own chrome or overlays: anything marked
 *    `data-no-page-context`, dialogs (the palette itself, modals), `nav`,
 *    hidden / aria-hidden / inert parts, scripts and styles;
 *  - never a Build terminal's screen (`.xterm`): CLI output can hold keys;
 *  - at most `PAGE_CONTEXT_MAX_CHARS`, and a cut says so (`CUT_MARKER`).
 *
 * jsdom has no `innerText`, so this walks the DOM itself: text nodes in
 * document order, a line break around block elements, " | " between table
 * cells. The same walk runs in Chromium, so a test proves what ships.
 */

export interface PageContext {
  title: string;
  path: string;
  text: string;
}

/** The daemon's own bound on `page_context.text` (it truncates past this). */
export const PAGE_CONTEXT_MAX_CHARS = 12_000;
/** Bounds the daemon applies to the other two fields. */
export const PAGE_TITLE_MAX = 200;
export const PAGE_PATH_MAX = 300;
/** Appended when the page held more than the cap — the model is told it is
 *  reading part of the page, never left to assume it saw all of it. */
export const CUT_MARKER = "\n…(cut)";

/** sessionStorage key the palette writes and the chat page takes. */
export const PAGE_CONTEXT_KEY = "ij:page-context";
/** A stash older than this is stale — the user went somewhere else first. */
export const PAGE_CONTEXT_TTL_MS = 5 * 60 * 1000;
/** Where the palette sends the user; the chat page reads `?about=page`. */
export const ABOUT_PAGE_PARAM = "about";
export const ABOUT_PAGE_VALUE = "page";
export const ABOUT_PAGE_HREF = `/chat?${ABOUT_PAGE_PARAM}=${ABOUT_PAGE_VALUE}`;

/** The chat surface — `/` renders the chat page since v1.321.0. Kept equal to
 *  `components/AppSidebar.CHAT_PATHS` (pinned by page-context-v1325); not
 *  imported from there because the palette must not pull the sidebar in. */
export const CHAT_SURFACE_PATHS: readonly string[] = ["/", "/chat"];

/** True on the chat page — "about this page" is not offered there. */
export function isChatSurface(pathname: string): boolean {
  const p = (pathname || "/").replace(/\/+$/, "") || "/";
  return CHAT_SURFACE_PATHS.includes(p);
}

/** Skipped WHOLE — the element and everything inside it. */
const SKIP_SELECTOR = [
  "[data-no-page-context]",
  "[role='dialog']",
  "[role='alertdialog']",
  "[aria-modal='true']",
  "dialog",
  "nav",
  "[aria-hidden='true']",
  "[hidden]",
  "[inert]",
  "[contenteditable='']",
  "[contenteditable='true']",
  "[contenteditable='plaintext-only']",
  // A Build terminal's screen: CLI output can print tokens and keys, and the
  // pane has its own chat that already carries the pane (v1.280.0).
  ".xterm",
].join(",");

/** Never read: their text is code, values the user is typing, or a list of
 *  every option a select could hold. */
const SKIP_TAGS = new Set([
  "SCRIPT",
  "STYLE",
  "NOSCRIPT",
  "TEMPLATE",
  "INPUT",
  "TEXTAREA",
  "SELECT",
  "OPTION",
  "SVG",
  "CANVAS",
  "IFRAME",
  "OBJECT",
  "VIDEO",
  "AUDIO",
]);

/** Elements that start and end a line, so words from two blocks never weld. */
const BLOCK_TAGS = new Set([
  "ADDRESS", "ARTICLE", "ASIDE", "BLOCKQUOTE", "BR", "DD", "DETAILS", "DIV",
  "DL", "DT", "FIELDSET", "FIGCAPTION", "FIGURE", "FOOTER", "FORM", "H1", "H2",
  "H3", "H4", "H5", "H6", "HEADER", "HR", "LI", "MAIN", "OL", "P", "PRE",
  "SECTION", "SUMMARY", "TABLE", "TBODY", "TFOOT", "THEAD", "TR", "UL",
]);
const CELL_TAGS = new Set(["TD", "TH"]);

/** The walk stops collecting well past the cap, so a huge page (a 20k-row
 *  table) costs a bounded walk; the cut marker still tells the truth. */
const RAW_BUDGET = PAGE_CONTEXT_MAX_CHARS * 4;

function isHiddenByStyle(el: Element, win: Window | null): boolean {
  if (!win || typeof win.getComputedStyle !== "function") return false;
  try {
    const cs = win.getComputedStyle(el);
    return cs.display === "none" || cs.visibility === "hidden";
  } catch {
    return false;
  }
}

function skipElement(el: Element, win: Window | null): boolean {
  if (SKIP_TAGS.has(el.tagName.toUpperCase())) return true;
  try {
    if (el.matches(SKIP_SELECTOR)) return true;
  } catch {
    /* a selector the engine refuses must not cost the whole capture */
  }
  return isHiddenByStyle(el, win);
}

/** Collapse runs of spaces inside a line, trim each line, and keep ONE line
 *  break between blocks (the cap is spent on words, not on blank lines). */
export function collapseWhitespace(raw: string): string {
  return raw
    .replace(/[^\S\n]+/g, " ")
    .split("\n")
    .map((line) => line.trim())
    .join("\n")
    .replace(/\n{2,}/g, "\n")
    .trim();
}

/** Cut `text` to `max` characters INCLUDING the marker, never splitting a
 *  surrogate pair. Text within the cap comes back untouched unless `cut`
 *  says it was already shortened. */
export function capText(text: string, max = PAGE_CONTEXT_MAX_CHARS, cut = false): string {
  if (!cut && text.length <= max) return text;
  let head = text.slice(0, Math.max(0, max - CUT_MARKER.length));
  if (/[\uD800-\uDBFF]$/.test(head)) head = head.slice(0, -1);
  return head.trimEnd() + CUT_MARKER;
}

/** The page's readable text, in document order. Exported for tests. */
export function readableText(root: Element, win: Window | null = null): { text: string; overflow: boolean } {
  const out: string[] = [];
  let size = 0;
  let overflow = false;
  const push = (s: string) => {
    out.push(s);
    size += s.length;
    if (size > RAW_BUDGET) overflow = true;
  };
  const walk = (node: Node) => {
    if (overflow) return;
    if (node.nodeType === 3) {
      push((node as Text).data);
      return;
    }
    if (node.nodeType !== 1) return;
    const el = node as Element;
    if (skipElement(el, win)) return;
    const tag = el.tagName.toUpperCase();
    if (tag === "IMG") {
      const alt = (el.getAttribute("alt") || "").trim();
      if (alt) push(` ${alt} `);
      return;
    }
    const block = BLOCK_TAGS.has(tag);
    const cell = CELL_TAGS.has(tag);
    if (block) push("\n");
    if (cell && el.previousElementSibling && CELL_TAGS.has(el.previousElementSibling.tagName.toUpperCase())) {
      push(" | ");
    }
    for (let c = el.firstChild; c; c = c.nextSibling) walk(c);
    if (block) push("\n");
    else if (cell) push(" ");
  };
  for (let c = root.firstChild; c; c = c.nextSibling) walk(c);
  return { text: collapseWhitespace(out.join("")), overflow };
}

function clip(s: string, max: number): string {
  const t = s.replace(/\s+/g, " ").trim();
  return t.length > max ? `${t.slice(0, max - 1)}…` : t;
}

/** The scroll root's content: MainContent's `#main-content`, else `<main>`. */
function contentRoot(doc: Document): Element | null {
  return doc.getElementById("main-content") || doc.querySelector("main") || doc.body;
}

/**
 * Capture the page the user is looking at. Never throws: a page that cannot
 * be read comes back with empty text (the daemon injects nothing for it).
 */
export function capturePageContext(doc: Document = document): PageContext {
  let path = "";
  let title = "";
  let text = "";
  try {
    const win = doc.defaultView;
    path = clip(win?.location?.pathname || "", PAGE_PATH_MAX);
    const root = contentRoot(doc);
    if (root) {
      // The page's own name first: document.title can carry the bell's
      // "(3)" count prefix, the h1 is what the user reads on screen.
      const h1 = root.querySelector("h1") || doc.querySelector("h1");
      title = clip(h1?.textContent || "", PAGE_TITLE_MAX);
      const { text: body, overflow } = readableText(root, win);
      // A walk that stopped early is a cut even when what it kept is short
      // (a page that is mostly whitespace collapses below the cap).
      text = capText(body, PAGE_CONTEXT_MAX_CHARS, overflow);
    }
    if (!title) {
      const named = doc.documentElement?.getAttribute("data-ij-title") || doc.title || "";
      title = clip(named, PAGE_TITLE_MAX);
    }
  } catch {
    /* a page that cannot be read is an empty capture, never a throw */
  }
  return { title, path, text };
}

interface Stashed {
  ctx: PageContext;
  at: number;
}

function storage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.sessionStorage : null;
  } catch {
    return null;
  }
}

/** Hand a capture to the chat page. False when the browser refused storage
 *  (the chat page then simply finds nothing to take). */
export function stashPageContext(ctx: PageContext, now: number = Date.now()): boolean {
  const s = storage();
  if (!s) return false;
  try {
    s.setItem(PAGE_CONTEXT_KEY, JSON.stringify({ ctx, at: now } satisfies Stashed));
    return true;
  } catch {
    return false;
  }
}

/** Read AND remove the stash (one landing, one use). Stale (older than
 *  `PAGE_CONTEXT_TTL_MS`), malformed or absent → null. The fields are
 *  re-bounded here: sessionStorage is writable by anything on the page. */
export function takePageContext(now: number = Date.now()): PageContext | null {
  const s = storage();
  if (!s) return null;
  let raw: string | null = null;
  try {
    raw = s.getItem(PAGE_CONTEXT_KEY);
    s.removeItem(PAGE_CONTEXT_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<Stashed> | null;
    const at = typeof parsed?.at === "number" ? parsed.at : NaN;
    if (!Number.isFinite(at) || now - at > PAGE_CONTEXT_TTL_MS || at - now > 60_000) return null;
    const ctx = parsed?.ctx as Partial<PageContext> | undefined;
    if (!ctx || typeof ctx !== "object") return null;
    const str = (v: unknown) => (typeof v === "string" ? v : "");
    return {
      title: clip(str(ctx.title), PAGE_TITLE_MAX),
      path: clip(str(ctx.path), PAGE_PATH_MAX),
      text: capText(str(ctx.text)),
    };
  } catch {
    return null;
  }
}
