/**
 * Wave 3 (SPEED), v1.311.0 — the chat route stops downloading four panels
 * nobody has opened: the document preview, the workspace files tree (both the
 * panel and its directory tree), and the e-mail composer behind a draft card.
 *
 * THE v1.258.0 RULE DECIDES HOW THIS IS PINNED: "a component is not its own
 * chunk — before deferring one, check what else its module exports as a
 * VALUE." `DraftCard` imports `draftHeaders` (a value) from
 * `EmailComposeDialog.tsx` and calls it during render, so wrapping the dialog
 * in `next/dynamic` alone would move ZERO bytes. So the strongest pin here is
 * not "the page says dynamic(...)" but REACHABILITY: walking every STATIC,
 * non-type import from the chat route's entry files must never reach these
 * modules. A value helper left behind, or any other static importer on the
 * route, keeps the module resident and fails the walk — whichever file it is
 * in. Bytes are the coordinator's A/B on `.next/app-build-manifest.json`; this
 * file pins the import graph that decides them.
 *
 * Source pins, CRLF-normalised at the reader (the v1.232.1 rule), comments
 * stripped (a comment NAMING a module must not satisfy or fail a pin).
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = process.cwd();
const LF = String.fromCharCode(10);
const CRLF = String.fromCharCode(13) + LF;

function read(abs: string): string {
  return readFileSync(abs, "utf-8").split(CRLF).join(LF);
}

/** Source with block and line comments stripped (URLs in strings survive). */
function code(abs: string): string {
  return read(abs)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:"'`\\])\/\/.*$/gm, "$1");
}

const EXTS = [".tsx", ".ts", "/index.tsx", "/index.ts"];

/** Resolve an import specifier to a repo file, or null for a package. */
function resolveSpec(fromFile: string, spec: string): string | null {
  let base: string;
  if (spec.startsWith("@/")) base = resolve(ROOT, spec.slice(2));
  else if (spec.startsWith(".")) base = resolve(dirname(fromFile), spec);
  else return null;
  if (/\.(tsx?|jsx?)$/.test(base) && existsSync(base)) return base;
  for (const e of EXTS) if (existsSync(base + e)) return base + e;
  return null;
}

/** True when an import/export clause carries no runtime value. */
function typeOnly(clause: string): boolean {
  const c = clause.trim();
  if (c.startsWith("type ")) return true;
  const m = /^\{([\s\S]*)\}$/.exec(c);
  if (!m) return false; // a default or namespace import is a value
  const names = m[1].split(",").map((s) => s.trim()).filter(Boolean);
  return names.length > 0 && names.every((n) => n.startsWith("type "));
}

/** Specifiers a file pulls in at RUN time (static, non-type). Dynamic
 *  `import("...")` is deliberately not followed: that is what defers. */
function staticValueImports(abs: string): string[] {
  const src = code(abs);
  const out: string[] = [];
  const re = /(?:^|[\n;])\s*(?:import|export)\s+([^'"`;]*?)\s*from\s*["']([^"']+)["']/g;
  for (const m of src.matchAll(re)) {
    if (!typeOnly(m[1])) out.push(m[2]);
  }
  for (const m of src.matchAll(/(?:^|[\n;])\s*import\s*["']([^"']+)["']/g)) out.push(m[1]);
  return out;
}

/** Every repo module reachable from `roots` through static value imports,
 *  with the importer that first reached it (for a readable failure). */
function reach(roots: string[]): Map<string, string> {
  const seen = new Map<string, string>();
  const queue = roots.map((r) => resolve(ROOT, r));
  for (const r of queue) seen.set(r, "(root)");
  while (queue.length) {
    const file = queue.shift()!;
    for (const spec of staticValueImports(file)) {
      const hit = resolveSpec(file, spec);
      if (hit && !seen.has(hit)) {
        seen.set(hit, relative(ROOT, file));
        queue.push(hit);
      }
    }
  }
  return seen;
}

const ENTRY = ["app/chat/page.tsx"];
const DEFERRED_MODULES = [
  "components/chat/DocPreview.tsx",
  "components/terminal/FilesPanel.tsx",
  "components/terminal/DirectoryTree.tsx",
  "components/chat/EmailComposeDialog.tsx",
];

const PAGE = code(resolve(ROOT, "app/chat/page.tsx"));
const DRAFT = code(resolve(ROOT, "components/chat/DraftCard.tsx"));

describe("the chat route's static import graph no longer reaches the four panels", () => {
  const graph = reach(ENTRY);

  it("ANTI-VACUITY: the walker sees the route (modules the page renders statically ARE reached)", () => {
    // If the walker silently found nothing, every "not reached" below would
    // pass for free. These are on the route today and stay there.
    for (const m of [
      "components/chat/DraftCard.tsx", // rendered for every ```email fence
      "components/chat/CompactionCard.tsx", // v1.258.0's must-stay-static control
      "components/chat/WorkflowDraftCard.tsx",
      "lib/useChatStream.ts",
    ]) {
      expect(graph.has(resolve(ROOT, m)), `${m} should be reached`).toBe(true);
    }
    // ...and a module the page already defers is NOT reached (dynamic imports
    // are not followed), proving the walker can tell the difference.
    expect(graph.has(resolve(ROOT, "components/project/ProjectSurfaces.tsx"))).toBe(false);
  });

  for (const mod of DEFERRED_MODULES) {
    it(`${mod} is off the route (no static value import reaches it)`, () => {
      const via = graph.get(resolve(ROOT, mod));
      // Today: reached — DocPreview/FilesPanel/DirectoryTree from the page,
      // EmailComposeDialog from DraftCard (component AND the draftHeaders value).
      expect(via, `${mod} is still statically reached via ${via}`).toBeUndefined();
    });
  }

  it("draftHeaders, wherever it now lives, does not drag the dialog back in (red today: it comes from the dialog's own module)", () => {
    // The fix_adjustment: move the value helpers out of EmailComposeDialog.tsx
    // (e.g. a small module) and keep ComposeMode/ComposeResult as `import type`.
    // The module DraftCard takes draftHeaders from must not itself value-import
    // the dialog's module.
    const m = /import\s*\{[^}]*\bdraftHeaders\b[^}]*\}\s*from\s*["']([^"']+)["']/.exec(DRAFT);
    expect(m, "DraftCard must import draftHeaders by name").not.toBeNull();
    const file = resolveSpec(resolve(ROOT, "components/chat/DraftCard.tsx"), m![1]);
    expect(file, `cannot resolve ${m![1]}`).not.toBeNull();
    const sub = reach([relative(ROOT, file!)]);
    expect(sub.has(resolve(ROOT, "components/chat/EmailComposeDialog.tsx"))).toBe(false);
  });
});

describe("each panel is deferred FROM ITS OWN module, the house idiom", () => {
  const OWN: Array<[string, string, string]> = [
    // [component, module specifier, source that must declare it]
    ["DocPreview", "@/components/chat/DocPreview", PAGE],
    ["FilesPanel", "@/components/terminal/FilesPanel", PAGE],
    ["DirectoryTree", "@/components/terminal/DirectoryTree", PAGE],
    ["EmailComposeDialog", "@/components/chat/EmailComposeDialog", DRAFT],
  ];
  for (const [name, spec, src] of OWN) {
    it(`${name} = dynamic(() => import("${spec}"))`, () => {
      const at = src.indexOf(`const ${name} = dynamic(`);
      expect(at, `${name} must be declared with dynamic()`).toBeGreaterThan(-1);
      expect(src.slice(at, at + 400)).toContain(`import("${spec}")`);
    });
  }

  it("DraftCard imports next/dynamic", () => {
    expect(DRAFT).toContain('import dynamic from "next/dynamic"');
  });
});

describe("CONTROLS (already true): the panels still render, and the draft still reads its headers", () => {
  it("the page still renders all three panels behind their gates", () => {
    expect(PAGE).toContain("<DocPreview");
    expect(PAGE).toContain("<FilesPanel");
    expect(PAGE).toContain("<DirectoryTree");
  });

  it("DraftCard still renders the dialog and still computes draftHeaders", () => {
    expect(DRAFT).toContain("<EmailComposeDialog");
    expect(DRAFT).toMatch(/draftHeaders\(/);
  });

});
