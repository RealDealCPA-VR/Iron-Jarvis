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

/** A retired word: what it matches, and the canon word that replaces it. */
export type Retired = { word: string; re: RegExp; use: string };

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
];

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
      const text = ts.isJsxText(node) ? node.getText(sf) : (node as ts.LiteralLikeNode).text;
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
    for (const r of RETIRED) {
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
