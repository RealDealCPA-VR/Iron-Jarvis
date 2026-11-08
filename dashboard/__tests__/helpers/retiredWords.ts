/**
 * The vocabulary scanner (v1.357.0, product readiness G76): ONE reader for
 * "no retired word in anything a user reads".
 *
 * VOCABULARY.md is the canon (one name per concept). Before this reader the
 * canon was enforced by exact-string pins, so a variant ("Connected packs",
 * "Open the pane", "the offline mock") slipped past every one of them. This
 * walks every STRING LITERAL, template piece and JSX TEXT node of a source
 * file through the TypeScript parser (comments are not nodes, so they never
 * count) and keeps the ones a user can read. What it skips, and why:
 *
 * - module specifiers and literal TYPES (code, never rendered);
 * - property NAMES (`{"job:mission": …}` is a key) and index keys (`m["mock"]`);
 * - the operands of a comparison or a `case` (`provider === "mock"` matches
 *   an id, it does not show one);
 * - the first argument of a matcher (`s.includes("…")`, `startsWith`, …) and
 *   every argument of a `console.*` call;
 * - JSX attributes nobody reads (className, id, key, href, data-*, …);
 * - search ALIASES (`aliases: [...]` in lib/nav.ts and the palette): the
 *   canon's own rule keeps a retired word searchable forever;
 * - a token with no space and no capital ("mock", "pane-rail", "/missions",
 *   "ij.pane.thread.") — an id, a test id, a route or a key — UNLESS it is
 *   rendered as it stands (a JSX child, a branch of a ternary in one, a piece
 *   of a sentence template, one side of an `n === 1 ? … : …` plural choice
 *   wherever it is kept).
 *
 * A visible attribute (title, aria-label, placeholder, alt, label) is read
 * whatever its shape (a ternary, a fallback, a template), and a hidden one
 * (className, data-*, href, …) is skipped whatever its shape. A single
 * lower-case word kept in a variable or an object VALUE and rendered later
 * (`{ mcp: "pack" }` then `{LABEL[k]}`) cannot be told from an id and is NOT
 * read: write it capitalised or in a sentence. What is left is prose; a
 * retired word in prose is a hit,
 * unless the caller's allowlist names that file and that text with a reason.
 */

import { readdirSync } from "node:fs";
import path from "node:path";
import ts from "typescript";

import { DASHBOARD_ROOT, readSrc } from "./dashGuard";

/** A retired word: what it matches, and the canon word that replaces it.
 *  `scope` (Wave D item 33) limits a row to the files it applies to — a word
 *  that is retired on SOME surfaces and the thing's own name on others. */
export type Retired = { word: string; re: RegExp; use: string; scope?: (file: string) => boolean };

/** VOCABULARY.md's retired words (case-insensitive, whole words). */
export const RETIRED: readonly Retired[] = [
  { word: "pack", re: /\bpacks?\b/i, use: "extension (an MCP server); Pack is reserved for the staff bundle" },
  // v1.360.0 integrate (lane 10C note C): VOCABULARY.md retired "plug-in"
  // (v1.216.0) but no row held it, so none of the three scans could see it.
  { word: "plug-in", re: /\bplug-ins?\b/i, use: "extension" },
  { word: "connector", re: /\bconnectors?\b/i, use: "connection" },
  { word: "memory source", re: /\bmemory[ -]sources?\b/i, use: "memory base" },
  { word: "brain", re: /\bbrains?\b/i, use: "memory base, or Iron Jarvis" },
  { word: "mission", re: /\bmissions?\b/i, use: "objective" },
  { word: "daemon", re: /\bdaemons?\b/i, use: "Iron Jarvis" },
  { word: "mock", re: /\bmock(?:s|ed)?\b/i, use: "the demo model" },
  { word: "simulated", re: /\bsimulated\b/i, use: "the demo model (a scripted demo, not real answers)" },
  { word: "offline demo", re: /\boffline (?:demo|model)s?\b/i, use: "the demo model" },
  { word: "pane", re: /\bpanes?\b/i, use: "terminal" },
  { word: "job", re: /\bjobs?\b/i, use: "task (one attempt at it is a run)" },
  { word: "assignment", re: /\bassignments?\b/i, use: "task (a queued task)" },
  // Harness gaps (fix 4): Build's coding-agent view is "Coding sessions"
  // (dashboard/lib/buildWords); "IJ harness" / "harness" were the engine
  // room's words for it and for the coding tool in a terminal.
  { word: "harness", re: /\bharness(?:es)?\b/i, use: "Coding sessions (Build's view), a coding session, or the coding tool" },
  // v1.409.0: Build's GUI for the real Claude Code / Codex is the "Code
  // view" (its switch reads Code | Terminal); "App view" was the plan's
  // working name and is never shown.
  { word: "app view", re: /\bapp view\b/i, use: "Code view (Build's Code | Terminal switch)" },
  // v1.420.0 (fix plan C5.2): Build's loading line said "Attaching to
  // sessions…" — the engine room's word for re-opening the terminals.
  { word: "attaching to sessions", re: /\battaching to sessions\b/i, use: "Opening your terminals…" },
];

/** A retired word read on the DASHBOARD only (see RETIRED_ON_SCREEN). */
function onScreen(word: string, re: RegExp, use: string): Retired {
  return { word, re, use };
}

/**
 * v1.420.0 (fix plan C5.2): engine-room words retired from what the
 * DASHBOARD shows, but not yet from what the daemon answers. RETIRED above is
 * also read by the two Python scans of the daemon's sentences
 * (tests/test_vocabulary_beyond_dashboard_v13580.py parses the `{ word: …,
 * re: …, use: … }` rows of this file), and those scans keep PENDING empty;
 * the daemon still says these words in files other lanes reword this wave
 * (harness/branches.py, routes/build*.py, routes/ci_watch.py, routes/undo.py
 * — see the v1.420.0 hand-off). Written as `onScreen(…)` calls ON PURPOSE so
 * the Python reader does not parse them: when the daemon's last sentence is
 * reworded, move the entry up into RETIRED as a plain row.
 */
export const RETIRED_ON_SCREEN: readonly Retired[] = [
  // A Build session's branch lives in a git worktree beside the repository
  // (v1.375.0); a person reads "its own copy of the folder".
  onScreen("worktree", /\bworktrees?\b/i, "its own copy of the folder"),
  // The undo journal's word for a write it cannot reverse.
  onScreen("no safe inverse", /\bno safe inverse\b/i, "can't be undone"),
  // Connections headings a person read as engine-room words (v1.420.0, lane P).
  onScreen("Subscription & local providers", /subscription & local providers/i, "the tools' own headings (Claude Code, Codex, …)"),
  onScreen("Rescan local CLIs", /rescan local clis/i, "Look again"),
  onScreen("No OAuth client configured", /no oauth client configured/i, "the sign-in set-up sentence"),
  onScreen("Open Channels", /\bopen channels\b/i, "Open Notifications"),
  // v1.455.0 (Wave E, item 3): an MCP server is an "extension" (VOCABULARY.md
  // row 22); v1.448.0's chat door called it a "tool server", a second name.
  // Dashboard-only because a user's own words ("connect this tool server")
  // are still read by the daemon's matchers.
  onScreen("tool server", /\btool[- ]servers?\b/i, "extension"),
  // v1.455.0 (Wave E, item 4): the page is Rules since v1.452.0; "reflex"
  // stayed in the run notices. Dashboard-only: the daemon's ids, event types
  // and its own 404s ("no such reflex rule") still carry the word.
  onScreen("reflex", /\breflex(?:es)?\b/i, "rule (the page is Rules)"),
  // v1.455.0 (Wave E, item 6): a coding session is never a "Build session"
  // on screen (VOCABULARY.md row 25); the daemon's own 404s still say it.
  onScreen("Build session", /\bbuild sessions?\b/i, "coding session"),
];

/** A retired word read on SOME dashboard surfaces only (see RETIRED_ON_SURFACES). */
function onSurfaces(word: string, re: RegExp, use: string, scope: (file: string) => boolean): Retired {
  return { word, re, use, scope };
}

/** Wave D item 33: the chat and coding surfaces — components/chat,
 *  components/harness, components/buildCode, lib/buildApp*, lib/buildWords,
 *  and the two account-word modules those screens read (lib/chatAccount,
 *  lib/paneAccounts). Dashboard-relative paths with forward slashes. */
export function isChatOrCodingSurface(file: string): boolean {
  return (
    file.startsWith("components/chat/") ||
    file.startsWith("components/harness/") ||
    file.startsWith("components/buildCode/") ||
    /^lib\/buildApp[^/]*\.tsx?$/.test(file) ||
    /^lib\/buildWords\.tsx?$/.test(file) ||
    /^lib\/chatAccount\.tsx?$/.test(file) ||
    /^lib\/paneAccounts\.tsx?$/.test(file)
  );
}

/**
 * Wave D item 33 (VOCABULARY.md row 29): words retired on the chat and
 * coding surfaces ONLY. "Iron-Proxy" is the account manager's own name — it
 * stays on its Connections card (components/connections/IronProxyCard.tsx,
 * "Several accounts (Iron-Proxy)") and in its own errors, and those files are
 * OUTSIDE this row's scope by construction, so the card needs no ALLOW entry.
 * On a chat or coding screen a person reads "your accounts" (the Several
 * accounts card) or, when the program itself is meant, "the account
 * manager". Written as `onSurfaces(…)` calls ON PURPOSE: the two Python
 * scans parse only the plain `{ word: …, re: …, use: … }` rows, and the
 * daemon's own Iron-Proxy sentences (its card, its account refusals) are
 * the program's name where the canon keeps it.
 */
export const RETIRED_ON_SURFACES: readonly Retired[] = [
  onSurfaces(
    "Iron-Proxy",
    /\biron[- ]?proxy\b/i,
    "your accounts (the Several accounts card), or the account manager when the program itself is meant",
    isChatOrCodingSurface,
  ),
];

/** v1.455.0 (Wave E, item 1): the surfaces where the user's own model server
 *  is named — the model menu and receipts (providerDisplay in lib/onboarding),
 *  the chat, Connections and its cards, the Model servers page, the first-run
 *  doors, the nav and the palette. There it is a "model server", never an
 *  "endpoint" or a "node". Advanced places that use the word for something
 *  else (a memory base's search address, a remote agent) are outside it. */
export function isModelServerSurface(file: string): boolean {
  return (
    file.startsWith("components/chat/") ||
    file === "app/chat/page.tsx" ||
    file === "app/fleet/page.tsx" ||
    file === "components/settings/pages/ConnectionsPage.tsx" ||
    file === "components/connections/EnvelopeCard.tsx" ||
    file === "components/connections/EndpointModelPicker.tsx" ||
    file === "components/onboarding/ConnectDoors.tsx" ||
    file === "components/CommandPalette.tsx" ||
    file === "lib/onboarding.ts" ||
    file === "lib/modelServers.ts" ||
    file === "lib/nav.ts"
  );
}

/** v1.455.0 (Wave E, item 1): the model-server words, scoped (see above). */
export const RETIRED_ON_MODEL_SERVER_SURFACES: readonly Retired[] = [
  onSurfaces("endpoint", /\bendpoints?\b/i, "model server", isModelServerSurface),
  onSurfaces("node", /\bnodes?\b(?!\.js)/i, "model server", isModelServerSurface),
];

/** Every retired row, in one list (a scoped row applies only to its files). */
export const ALL_RETIRED: readonly Retired[] = [
  ...RETIRED,
  ...RETIRED_ON_SCREEN,
  ...RETIRED_ON_SURFACES,
  ...RETIRED_ON_MODEL_SERVER_SURFACES,
];

/** Wave D item 33: the HTML entities JSX text may spell a character with
 *  (`&amp;`, `&rsquo;`, `&#39;`, `&#x27;`). The scanner reads JSX text as
 *  written, so "See subscription &amp; local providers" never matched the
 *  retired "subscription & local providers". Decoded before any row is tried. */
const NAMED_ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
  lsquo: "‘",
  rsquo: "’",
  ldquo: "“",
  rdquo: "”",
  hellip: "…",
  mdash: "—",
  ndash: "–",
  middot: "·",
  times: "×",
  rarr: "→",
  larr: "←",
  bull: "•",
  copy: "©",
  reg: "®",
  trade: "™",
};

export function decodeEntities(text: string): string {
  if (!text.includes("&")) return text;
  return text.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (whole, body: string) => {
    if (body[0] === "#") {
      const code = body[1] === "x" || body[1] === "X" ? parseInt(body.slice(2), 16) : parseInt(body.slice(1), 10);
      return Number.isFinite(code) && code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : whole;
    }
    const named = NAMED_ENTITIES[body.toLowerCase()];
    return named ?? whole;
  });
}

/** The folders whose files a user reads. */
export const SCANNED_DIRS = ["app", "components", "lib"] as const;

/** JSX attributes a user reads, whatever their shape. */
const VISIBLE_ATTRS = new Set([
  "title",
  "aria-label",
  "aria-description",
  "aria-valuetext",
  "aria-roledescription",
  "placeholder",
  "alt",
  "label",
]);

/** JSX attributes nobody reads. */
const HIDDEN_ATTRS = new Set([
  "className",
  "class",
  "id",
  "key",
  "href",
  "src",
  "to",
  "type",
  "name",
  "role",
  "htmlFor",
  "rel",
  "target",
  "method",
  "value",
  "defaultValue",
  "variant",
  "size",
  "tone",
  "icon",
  "kind",
  "as",
  "mode",
  "side",
  "align",
  "autoComplete",
  "inputMode",
  "pattern",
  "accept",
  "lang",
  "dir",
  "form",
  "testId",
  "aria-controls",
  "aria-labelledby",
  "aria-describedby",
  "aria-owns",
  "aria-live",
]);

/** Methods whose FIRST argument is a matcher, never shown. */
const MATCHERS = new Set([
  "includes",
  "startsWith",
  "endsWith",
  "indexOf",
  "lastIndexOf",
  "split",
  "match",
  "matchAll",
  "replace",
  "replaceAll",
  "search",
  "getItem",
  "setItem",
  "removeItem",
  "querySelector",
  "querySelectorAll",
  "getElementById",
  "closest",
  "getAttribute",
  "setAttribute",
  "hasAttribute",
  "addEventListener",
  "removeEventListener",
]);

/** Properties whose array holds search aliases. */
const ALIAS_KEYS = new Set(["aliases", "keywords"]);

export type Hit = { file: string; line: number; word: string; text: string };

const parse = (rel: string, src: string) =>
  ts.createSourceFile(rel, src, ts.ScriptTarget.Latest, true, rel.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);

const isTextNode = (node: ts.Node) =>
  ts.isStringLiteral(node) ||
  ts.isNoSubstitutionTemplateLiteral(node) ||
  ts.isTemplateHead(node) ||
  ts.isTemplateMiddle(node) ||
  ts.isTemplateTail(node) ||
  ts.isJsxText(node);

/** A token with no whitespace and no capital: an id, key, route or test id. */
const looksLikeCode = (text: string) => !/\s/.test(text) && !/[A-Z]/.test(text);

/** A template whose fixed pieces read as a sentence (a space or a capital). */
function proseTemplate(t: ts.TemplateExpression): boolean {
  const pieces = [t.head.text, ...t.templateSpans.map((s) => s.literal.text)];
  return pieces.some((p) => /\s/.test(p) || /[A-Z]/.test(p));
}

/** A comparison with the number 1 (`n === 1`, `count !== 1`): the condition
 *  of a singular/plural choice, which is always words. */
function comparesWithOne(e: ts.Expression): boolean {
  let x: ts.Expression = e;
  while (ts.isParenthesizedExpression(x)) x = x.expression;
  if (!ts.isBinaryExpression(x)) return false;
  const op = x.operatorToken.kind;
  if (
    op !== ts.SyntaxKind.EqualsEqualsEqualsToken &&
    op !== ts.SyntaxKind.ExclamationEqualsEqualsToken &&
    op !== ts.SyntaxKind.EqualsEqualsToken &&
    op !== ts.SyntaxKind.ExclamationEqualsToken
  )
    return false;
  const one = (s: ts.Expression) => ts.isNumericLiteral(s) && s.text === "1";
  return one(x.left) || one(x.right);
}

/** The JSX attribute a literal is the VALUE of, climbing through the shapes
 *  an attribute value takes (`{…}`, parentheses, a ternary branch, a
 *  fallback, a template piece, `+`): `title={x ? "Close pane" : "Open"}` is
 *  the title's text, `className={\`pane ${x}\`}` is a class. Null when the
 *  literal sits inside a call or anything else first. (v1.357.0 review: the
 *  attribute used to be read only when the literal was its DIRECT value, so a
 *  one-word title in a ternary was skipped as code and a class template read
 *  as prose.) */
function enclosingAttribute(node: ts.Node): ts.JsxAttribute | null {
  let cur: ts.Node = node;
  for (;;) {
    const p: ts.Node | undefined = cur.parent;
    if (!p) return null;
    if (ts.isJsxAttribute(p)) return p;
    if (
      ts.isJsxExpression(p) ||
      ts.isParenthesizedExpression(p) ||
      ts.isAsExpression(p) ||
      ts.isTemplateExpression(p) ||
      ts.isTemplateSpan(p)
    ) {
      cur = p;
      continue;
    }
    if (ts.isConditionalExpression(p) && (p.whenTrue === cur || p.whenFalse === cur)) {
      cur = p;
      continue;
    }
    if (ts.isBinaryExpression(p)) {
      const op = p.operatorToken.kind;
      if (
        op === ts.SyntaxKind.PlusToken ||
        ((op === ts.SyntaxKind.BarBarToken ||
          op === ts.SyntaxKind.QuestionQuestionToken ||
          op === ts.SyntaxKind.AmpersandAmpersandToken) &&
          p.right === cur)
      ) {
        cur = p;
        continue;
      }
    }
    return null;
  }
}

/** Is a code-shaped literal RENDERED as it stands: a JSX child, a ternary
 *  branch / fallback inside one, a piece of a sentence template, glued to a
 *  sentence with `+`, or one side of a singular/plural choice
 *  (`n === 1 ? "pane" : "panes"`, wherever it is kept)? */
function renderedAsItStands(node: ts.Node): boolean {
  let cur: ts.Node = node;
  for (;;) {
    const p: ts.Node | undefined = cur.parent;
    if (!p) return false;
    if (ts.isParenthesizedExpression(p) || ts.isAsExpression(p)) {
      cur = p;
      continue;
    }
    if (ts.isConditionalExpression(p) && (p.whenTrue === cur || p.whenFalse === cur)) {
      if (comparesWithOne(p.condition)) return true;
      cur = p;
      continue;
    }
    if (ts.isBinaryExpression(p)) {
      const op = p.operatorToken.kind;
      if (
        (op === ts.SyntaxKind.BarBarToken ||
          op === ts.SyntaxKind.QuestionQuestionToken ||
          op === ts.SyntaxKind.AmpersandAmpersandToken) &&
        p.right === cur
      ) {
        cur = p;
        continue;
      }
      if (op === ts.SyntaxKind.PlusToken) {
        const other = p.left === cur ? p.right : p.left;
        if ((ts.isStringLiteral(other) || ts.isNoSubstitutionTemplateLiteral(other)) && !looksLikeCode(other.text))
          return true;
        cur = p;
        continue;
      }
      return false;
    }
    if (ts.isJsxExpression(p)) return !!p.parent && (ts.isJsxElement(p.parent) || ts.isJsxFragment(p.parent));
    if (ts.isTemplateSpan(p)) return proseTemplate(p.parent as ts.TemplateExpression);
    return false;
  }
}

/** The name of the call a node is an argument of, and its index. */
function callOf(node: ts.Node): { name: string; owner: string; index: number } | null {
  const p = node.parent;
  if (!p || !ts.isCallExpression(p)) return null;
  const index = p.arguments.indexOf(node as ts.Expression);
  if (index < 0) return null;
  const callee = p.expression;
  if (ts.isPropertyAccessExpression(callee)) {
    return { name: callee.name.text, owner: callee.expression.getText(), index };
  }
  if (ts.isIdentifier(callee)) return { name: callee.text, owner: "", index };
  return null;
}

/** Why a literal is NOT something a user reads, or "" when it is. */
export function skipReason(node: ts.Node, sf: ts.SourceFile): string {
  if (ts.isJsxText(node)) return "";
  const text = (node as ts.LiteralLikeNode).text;
  const p = node.parent;
  if (!p) return "orphan";
  if (ts.isImportDeclaration(p) || ts.isExportDeclaration(p) || ts.isExternalModuleReference(p)) return "module";
  if (ts.isLiteralTypeNode(p)) return "type";
  if (
    (ts.isPropertyAssignment(p) ||
      ts.isPropertySignature(p) ||
      ts.isPropertyDeclaration(p) ||
      ts.isMethodDeclaration(p) ||
      ts.isEnumMember(p)) &&
    p.name === node
  )
    return "key";
  if (ts.isElementAccessExpression(p) && p.argumentExpression === node) return "index";
  if (ts.isBinaryExpression(p)) {
    const op = p.operatorToken.kind;
    if (
      op === ts.SyntaxKind.EqualsEqualsEqualsToken ||
      op === ts.SyntaxKind.ExclamationEqualsEqualsToken ||
      op === ts.SyntaxKind.EqualsEqualsToken ||
      op === ts.SyntaxKind.ExclamationEqualsToken ||
      op === ts.SyntaxKind.InKeyword
    )
      return "comparison";
  }
  if (ts.isCaseClause(p)) return "case";
  const call = callOf(node);
  if (call) {
    if (call.owner === "console") return "console";
    if (call.name === "require" || call.name === "import") return "module";
    if (MATCHERS.has(call.name) && call.index === 0) return "matcher";
  }
  if (ts.isImportTypeNode(p) || (ts.isCallExpression(p) && p.expression.kind === ts.SyntaxKind.ImportKeyword))
    return "module";
  // An alias list: `aliases: ["connect a pack", …]` (the canon keeps the old
  // word searchable on purpose).
  if (ts.isArrayLiteralExpression(p) && p.parent && ts.isPropertyAssignment(p.parent)) {
    const key = p.parent.name.getText(sf).replace(/["']/g, "");
    if (ALIAS_KEYS.has(key)) return "alias";
  }
  // A JSX attribute: its name decides, whatever shape its value takes.
  const attr = enclosingAttribute(node);
  if (attr) {
    const name = attr.name.getText(sf);
    if (VISIBLE_ATTRS.has(name)) return "";
    if (HIDDEN_ATTRS.has(name) || name.startsWith("data-")) return "attribute";
  }
  if (looksLikeCode(text) && !renderedAsItStands(node)) return "code";
  return "";
}

/** Every piece of user-readable copy in a source, with its line. */
export function readableCopy(rel: string, src = readSrc(rel)): Array<{ line: number; text: string }> {
  const sf = parse(rel, src);
  const out: Array<{ line: number; text: string }> = [];
  const visit = (node: ts.Node) => {
    if (isTextNode(node) && !skipReason(node, sf)) {
      // JSX text and a JSX attribute's plain string value are read with their
      // HTML entities decoded, as the browser shows them (Wave D item 33).
      const jsxSpelled = ts.isJsxText(node) || (ts.isStringLiteral(node) && !!node.parent && ts.isJsxAttribute(node.parent));
      const raw = ts.isJsxText(node) ? node.getText(sf) : (node as ts.LiteralLikeNode).text;
      const text = jsxSpelled ? decodeEntities(raw) : raw;
      if (text.trim()) out.push({ line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1, text });
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return out;
}

/** Every retired word in a source's readable copy. */
export function retiredHits(rel: string, src?: string): Hit[] {
  const out: Hit[] = [];
  for (const piece of readableCopy(rel, src)) {
    for (const r of ALL_RETIRED) {
      if (r.scope && !r.scope(rel)) continue;
      if (r.re.test(piece.text)) {
        out.push({ file: rel, line: piece.line, word: r.word, text: piece.text.replace(/\s+/g, " ").trim() });
      }
    }
  }
  return out;
}

/** Every .ts/.tsx file under the scanned folders, as dashboard-relative
 *  paths with forward slashes. */
export function scannedFiles(): string[] {
  const out: string[] = [];
  const walk = (rel: string) => {
    for (const e of readdirSync(path.join(DASHBOARD_ROOT, rel), { withFileTypes: true })) {
      const child = `${rel}/${e.name}`;
      if (e.isDirectory()) walk(child);
      else if (/\.tsx?$/.test(e.name) && !e.name.endsWith(".d.ts")) out.push(child);
    }
  };
  for (const d of SCANNED_DIRS) walk(d);
  return out.sort();
}
