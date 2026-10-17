/**
 * v1.348.0 polish wave A, lane L3 — the LAZY BOUNDARY of code colours.
 *
 * Shiki must cost the chat nothing until a code block it can colour is on
 * screen: no module of it is imported at load, an unknown language or an
 * oversized block never imports it at all, and a block below the fold waits
 * until it scrolls near. Proven by watching the real import of `shiki/core`
 * and `shiki/engine/javascript` (each wrapper counts, then hands back the real
 * module) — its own file, because module state (an engine already loaded)
 * would make the "not yet" half of this test vacuous anywhere else.
 *
 * Plus source pins for what a behaviour test cannot see: no static Shiki
 * import on the chat path, each chunk behind a literal import(), no
 * WebAssembly engine, no colour written in either source, no injected HTML,
 * and the version pinned exactly.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const loads = vi.hoisted(() => ({ core: 0, engine: 0 }));

vi.mock("shiki/core", async (importOriginal) => {
  loads.core += 1;
  return await importOriginal();
});
vi.mock("shiki/engine/javascript", async (importOriginal) => {
  loads.engine += 1;
  return await importOriginal();
});

import { CodeBlock } from "@/components/chat/CodeBlock";

const DASH = path.join(__dirname, "..");
const read = (rel: string) => readFileSync(path.join(DASH, rel), "utf8").replace(/\r\n/g, "\n");
/** Source without its comments, so prose about Shiki is not mistaken for code. */
const code = (rel: string) =>
  read(rel)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:"'`])\/\/.*$/gm, "$1");

/** An IntersectionObserver the test drives: nothing is on screen until
 *  `enter()` says so. */
class FakeObserver {
  static all: FakeObserver[] = [];
  targets: Element[] = [];
  constructor(private cb: IntersectionObserverCallback) {
    FakeObserver.all.push(this);
  }
  observe(el: Element) {
    this.targets.push(el);
  }
  unobserve() {}
  disconnect() {
    this.targets = [];
  }
  takeRecords() {
    return [];
  }
  enter() {
    this.cb(
      this.targets.map((target) => ({ target, isIntersecting: true }) as IntersectionObserverEntry),
      this as unknown as IntersectionObserver,
    );
  }
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  FakeObserver.all = [];
});

describe("code colours load only when a block that needs them is on screen", () => {
  it("nothing loads for an unknown language, an oversized block, or a block still off screen; then it does", async () => {
    vi.stubGlobal("IntersectionObserver", FakeObserver);
    const tall = Array.from({ length: 2_001 }, (_, i) => `const x${i} = ${i};`).join("\n");
    render(
      <div>
        <div data-testid="unknown">
          <CodeBlock code={"++++[>++<-]"} lang="brainfuck" />
        </div>
        <div data-testid="tall">
          <CodeBlock code={tall} lang="ts" />
        </div>
        <div data-testid="below-fold">
          <CodeBlock code={"const lazy: boolean = true;"} lang="ts" />
        </div>
      </div>,
    );
    const inside = (id: string) => screen.getByTestId(id).querySelector('[data-testid="code-block"]') as HTMLElement;
    await act(async () => {});
    // Only the block that could be coloured watches for the screen.
    expect(FakeObserver.all).toHaveLength(1);
    expect(FakeObserver.all[0].targets).toEqual([inside("below-fold")]);
    // A real wait: an eager load would have started on the first tick.
    await act(() => new Promise((r) => setTimeout(r, 60)));
    expect(loads).toEqual({ core: 0, engine: 0 });
    for (const id of ["unknown", "tall", "below-fold"]) expect(inside(id).dataset.highlighted, id).toBe("false");

    act(() => FakeObserver.all[0].enter());
    await waitFor(() => expect(inside("below-fold").dataset.highlighted).toBe("true"), { timeout: 8_000 });
    expect(loads).toEqual({ core: 1, engine: 1 });
    expect(inside("unknown").dataset.highlighted).toBe("false");
    expect(inside("tall").dataset.highlighted).toBe("false");
  });

  it("the engine is loaded once and shared by every block", async () => {
    render(
      <div>
        <CodeBlock code={"SELECT 1;"} lang="sql" />
        <CodeBlock code={"print('two')"} lang="python" />
      </div>,
    );
    await waitFor(
      () => {
        const blocks = screen.getAllByTestId("code-block");
        expect(blocks.map((b) => b.dataset.highlighted)).toEqual(["true", "true"]);
      },
      { timeout: 8_000 },
    );
    expect(loads).toEqual({ core: 1, engine: 1 });
  });
});

describe("source pins for the lazy boundary", () => {
  it("CodeBlock never imports Shiki; lib/highlight imports only its TYPES statically", () => {
    expect(code("components/chat/CodeBlock.tsx")).not.toMatch(/shiki/);
    const lib = code("lib/highlight.ts");
    const staticShiki = [...lib.matchAll(/^import\s[^;]*?from\s+["']shiki[^"']*["']/gm)].map((m) => m[0]);
    // Anti-vacuity: the type import is there and is seen by the pattern.
    expect(staticShiki.length).toBe(1);
    for (const s of staticShiki) expect(s, s).toMatch(/^import type\s/);
    expect(lib).not.toMatch(/require\(\s*["']shiki/);
  });

  it("every Shiki chunk sits behind a literal import(): the core, the JS engine, 18 grammars, 2 themes", () => {
    const lib = code("lib/highlight.ts");
    expect(lib).not.toMatch(/import\(\s*`/); // a template would bundle every grammar Shiki ships
    const chunks = [...lib.matchAll(/import\(\s*"(shiki\/[^"]+)"\s*\)/g)].map((m) => m[1]);
    expect(chunks).toContain("shiki/core");
    expect(chunks).toContain("shiki/engine/javascript");
    const grammars = chunks.filter((c) => c.startsWith("shiki/langs/")).map((c) => c.slice(12).replace(/\.mjs$/, ""));
    expect(grammars.sort()).toEqual(
      [
        "bash",
        "csharp",
        "css",
        "diff",
        "go",
        "html",
        "java",
        "javascript",
        "json",
        "jsx",
        "markdown",
        "powershell",
        "python",
        "rust",
        "sql",
        "tsx",
        "typescript",
        "yaml",
      ].sort(),
    );
    expect(chunks.filter((c) => c.startsWith("shiki/themes/")).sort()).toEqual([
      "shiki/themes/github-dark-default.mjs",
      "shiki/themes/github-light-default.mjs",
    ]);
    // No WebAssembly engine, no bundle that drags in every language.
    expect(lib).not.toMatch(/shiki\/wasm|engine\/oniguruma|onig\.wasm|shiki\/bundle|from\s+["']shiki["']/);
  });

  it("no colour is written in either source, and tokens are never injected as HTML", () => {
    for (const rel of ["components/chat/CodeBlock.tsx", "lib/highlight.ts"]) {
      const src = code(rel);
      expect(src.match(/#[0-9a-fA-F]{3,8}\b/g), rel).toBeNull();
      expect(src.match(/\b(?:rgba?|hsla?)\(/g), rel).toBeNull();
      expect(src, rel).not.toMatch(/dangerouslySetInnerHTML|innerHTML|codeToHtml/);
    }
    expect(code("lib/highlight.ts")).toMatch(/codeToTokens\(/);
  });

  it("shiki is pinned to one exact version in package.json", () => {
    const pkg = JSON.parse(read("package.json")) as { dependencies: Record<string, string> };
    expect(pkg.dependencies.shiki).toMatch(/^\d+\.\d+\.\d+$/);
  });
});
