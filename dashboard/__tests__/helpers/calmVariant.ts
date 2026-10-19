/**
 * The calm-variant reader (v1.330.0, calm chat wave 11): ONE way to ask "does
 * every <Badge> and <ConfirmButton> in this file say variant="calm"?".
 *
 * Wave 10 (L2) wrote this walk inside connections-fleet-calm-v1329 for the
 * Connections page. The other calm pages (Build, Workflows, the Agents page's
 * Your team tab and project lists) use it from here. It reads the file
 * through the TypeScript parser, so a comment or a string that mentions a
 * Badge never counts, and a JSX spread (which could hide the variant) is
 * reported as such.
 *
 * v1.356.0 (G80, updated on purpose): calm is the DEFAULT look of Badge and
 * ConfirmButton now, so a use is calm unless it says `variant="bordered"`
 * (the old pill, kept only where it is deliberate) or picks its variant with
 * an expression the reader cannot prove. A bare `<Badge value=… />` is calm.
 */

import ts from "typescript";

import { readSrc } from "./dashGuard";

export const CALM_ELEMENTS = ["Badge", "ConfirmButton"] as const;

export type CalmUse = {
  rel: string;
  line: number;
  tag: (typeof CALM_ELEMENTS)[number];
  calm: boolean;
  spread: boolean;
  /** The literal `label` / `value` prop when it is a plain string, else "". */
  label: string;
};

/** Every Badge / ConfirmButton element in a source file, with its variant. */
export function calmUses(rel: string, src = readSrc(rel)): CalmUse[] {
  const file = ts.createSourceFile(rel, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: CalmUse[] = [];
  const literal = (props: ts.NodeArray<ts.JsxAttributeLike>, name: string) => {
    const attr = props.find((p): p is ts.JsxAttribute => ts.isJsxAttribute(p) && p.name.getText(file) === name);
    const v = attr?.initializer;
    return v && ts.isStringLiteral(v) ? v.text : "";
  };
  // Absent = the default (calm since v1.356.0); a string literal is calm
  // unless it is "bordered"; an expression cannot be proven calm.
  const calmVariant = (props: ts.NodeArray<ts.JsxAttributeLike>) => {
    const attr = props.find((p): p is ts.JsxAttribute => ts.isJsxAttribute(p) && p.name.getText(file) === "variant");
    if (!attr) return true;
    const v = attr.initializer;
    return !!v && ts.isStringLiteral(v) && v.text !== "bordered";
  };
  const visit = (n: ts.Node) => {
    if (ts.isJsxSelfClosingElement(n) || ts.isJsxOpeningElement(n)) {
      const tag = n.tagName.getText(file).replace(/^ui\./, "");
      if ((CALM_ELEMENTS as readonly string[]).includes(tag)) {
        const props = n.attributes.properties;
        out.push({
          rel,
          line: file.getLineAndCharacterOfPosition(n.getStart(file)).line + 1,
          tag: tag as CalmUse["tag"],
          calm: calmVariant(props),
          spread: props.some((p) => ts.isJsxSpreadAttribute(p)),
          label: literal(props, "label") || literal(props, "value"),
        });
      }
    }
    ts.forEachChild(n, visit);
  };
  visit(file);
  return out;
}

/** A use the page keeps in the bordered look on purpose (`variant="bordered"`
 *  since v1.356.0): the file, the element, its literal label, and WHY. */
export type KeptDefault = { rel: string; tag: CalmUse["tag"]; label: string; why: string };

/** The uses that are not calm (or hide their variant behind a spread), as
 *  `rel:line <Tag> ...`, minus the ones `kept` names. */
export function uncalm(rel: string, src?: string, kept: KeptDefault[] = []): string[] {
  return calmUses(rel, src)
    .filter((u) => !u.calm || u.spread)
    .filter((u) => u.spread || !kept.some((k) => k.rel === rel && k.tag === u.tag && k.label === u.label))
    .map((u) => `${rel}:${u.line} <${u.tag}> ${u.spread ? "has a spread" : "is not the calm look"}`);
}
