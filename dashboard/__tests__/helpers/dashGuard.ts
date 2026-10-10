/**
 * The no-dash guard (v1.330.0, calm chat wave 10): ONE reader for "no spaced
 * em or en dash in any user-visible string or JSX text".
 *
 * chat-notices-v1329 and mission-copy-v1329 each carried a copy of this
 * walker. They import it from here now and keep their own file lists and
 * allowlists. It reads every STRING LITERAL, template piece and JSX TEXT node
 * through the TypeScript parser, so comments never count.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import ts from "typescript";

/** The dashboard folder (this file sits in __tests__/helpers). */
export const DASHBOARD_ROOT = path.join(__dirname, "..", "..");

/** A dashboard source file as text, CRLF folded to LF (CI checks out CRLF). */
export const readSrc = (rel: string) =>
  readFileSync(path.join(DASHBOARD_ROOT, rel), "utf8").replace(/\r\n/g, "\n");

export type CopyPiece = { line: number; text: string; jsx: boolean };

const parse = (rel: string, src: string) =>
  ts.createSourceFile(rel, src, ts.ScriptTarget.Latest, true, rel.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);

/** Every string literal, template piece and JSX text node in a source file,
 *  with its line. Comments are not nodes, so they never count. */
export function copyPieces(rel: string, src = readSrc(rel)): CopyPiece[] {
  const file = parse(rel, src);
  const out: CopyPiece[] = [];
  const visit = (node: ts.Node) => {
    if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node) ||
      ts.isJsxText(node)
    ) {
      const text = ts.isJsxText(node) ? node.getText(file) : (node as ts.LiteralLikeNode).text;
      out.push({ line: file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1, text, jsx: ts.isJsxText(node) });
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return out;
}

/** A dash used as an aside: an em or en dash with a space (or the edge of
 *  the text) on both sides. A lone dash placeholder ("—") is not an aside. */
export const ASIDE = /(^|\s)[—–](\s|$)/;
export const DASH_ONLY = /^[—–]$/;

/** What a JSX text node renders, by React's whitespace rule: a run of
 *  whitespace that holds a line break is dropped at the start and end of a
 *  line; whitespace on one line is kept. */
export function jsxRendered(raw: string): string {
  const lines = raw.split(/\r\n|\n|\r/);
  if (lines.length === 1) return raw;
  return lines
    .map((l, i) => {
      let s = l;
      if (i !== 0) s = s.replace(/^[ \t]+/, "");
      if (i !== lines.length - 1) s = s.replace(/[ \t]+$/, "");
      return s;
    })
    .filter((s) => s.length > 0)
    .join(" ");
}

/** A JSX child that is only a dash, read together with its siblings. On its
 *  own it looks like a placeholder, but `{" "}\n—{" "}` renders "x — y": a
 *  space next to it (from a `{" "}` sibling, or whitespace the node keeps) makes
 *  it an aside. A dash that is the element's whole text stays a placeholder. */
export function jsxDashAsides(rel: string, src = readSrc(rel)): string[] {
  const file = parse(rel, src);
  const out: string[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isJsxElement(node) || ts.isJsxFragment(node)) {
      let joined = "";
      const parts: Array<{ start: number; text: string; node: ts.Node }> = [];
      for (const child of node.children) {
        let text: string;
        if (ts.isJsxText(child)) text = jsxRendered(child.getText(file));
        else if (ts.isJsxExpression(child) && !child.expression) text = ""; // {/* a comment */}
        else if (
          ts.isJsxExpression(child) &&
          child.expression &&
          (ts.isStringLiteral(child.expression) || ts.isNoSubstitutionTemplateLiteral(child.expression))
        )
          text = child.expression.text;
        else text = "\u0001"; // an element or a computed value: never a space
        parts.push({ start: joined.length, text, node: child });
        joined += text;
      }
      if (!DASH_ONLY.test(joined.trim())) {
        for (const p of parts) {
          const dash = p.text.trim();
          if (!DASH_ONLY.test(dash)) continue; // longer copy is the per-piece check's job
          const at = p.start + p.text.indexOf(dash);
          const spaced = (c: string | undefined) => c !== undefined && /\s/.test(c);
          if (spaced(joined[at - 1]) || spaced(joined[at + 1])) {
            const line = file.getLineAndCharacterOfPosition(p.node.getStart(file)).line + 1;
            out.push(`${rel}:${line}: ${joined.replace(/\u0001/g, "{…}").trim().slice(0, 80)}`);
          }
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return out;
}

/** Every dash aside in a file, as `rel:line: text`. `allow` lists fragments
 *  (the caller's own allowlist): a string piece holding one is skipped, only
 *  where it appears. A string that is exactly a dash ("—") is a placeholder;
 *  " — " is not. A JSX text node that trims to a dash is judged with its
 *  siblings by jsxDashAsides (its raw text carries the source's indentation). */
export function asides(rel: string, src?: string, allow: string[] = []): string[] {
  const text = src ?? readSrc(rel);
  const placeholder = (p: CopyPiece) => (p.jsx ? DASH_ONLY.test(p.text.trim()) : DASH_ONLY.test(p.text));
  return [
    ...copyPieces(rel, text)
      .filter((p) => !placeholder(p) && ASIDE.test(p.text))
      .filter((p) => !allow.some((a) => p.text.includes(a)))
      .map((p) => `${rel}:${p.line}: ${p.text.trim().slice(0, 80)}`),
    ...jsxDashAsides(rel, text),
  ];
}
