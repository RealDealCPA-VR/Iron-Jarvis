/**
 * v1.348.0 polish wave A, lane L3 — code colours in a fenced block.
 *
 * Pinned through the REAL component (components/chat/CodeBlock.tsx) and the
 * REAL helpers (lib/highlight.ts) running the REAL Shiki core with its
 * JavaScript regex engine. `highlightCode` is wrapped in a spy that calls
 * straight through, so a test can see WHETHER the component asked for colours
 * without replacing what it gets back; only the failure test makes it fail.
 *
 * Shiki state (the loaded engine and a small memo of finished results) is
 * module state and lives for the whole file, so every test colours its OWN
 * text (`sample(tag)`): a memo hit from an earlier test would show colours at
 * once and hide the plain-first step a test is about to assert.
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/highlight", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/highlight")>();
  return { ...real, highlightCode: vi.fn(real.highlightCode) };
});

import { CodeBlock } from "@/components/chat/CodeBlock";
import { fenceLang } from "@/components/chat/DraftCard";
import { nodeText } from "@/components/Markdown";
import {
  codeLangId,
  highlightCode,
  MAX_HIGHLIGHT_CHARS,
  MAX_HIGHLIGHT_LINES,
  tooLargeToHighlight,
  type CodeScheme,
} from "@/lib/highlight";

const highlightSpy = vi.mocked(highlightCode);

/** The first wait loads Shiki and a grammar for real; give it room on a slow
 *  runner, but it still names the exact thing it waits for. */
const LOAD = { timeout: 8_000 };

const TS = ["const answer: number = 42;", "// the answer", "export function ask(q: string) {", "  return q + answer;", "}"].join("\n");
const sample = (tag: string) => `${TS}\n// ${tag}`;

const block = () => screen.getByTestId("code-block");
const codeEl = () => block().querySelector("pre > code") as HTMLElement;
const colouredSpans = () => [...codeEl().querySelectorAll("span")].filter((s) => (s as HTMLElement).style.color !== "");
const spansWithText = (text: string) =>
  [...codeEl().querySelectorAll("span")].filter((s) => s.textContent === text) as HTMLElement[];

/** A colour as the DOM writes it back (jsdom turns `#ff7b72` into rgb()). */
function asDomColour(c: string): string {
  const probe = document.createElement("span");
  probe.style.color = c;
  return probe.style.color;
}

async function themeColourOf(code: string, scheme: CodeScheme, text: string): Promise<string> {
  const r = await highlightCode(code, "ts", scheme);
  const tok = r?.lines.flat().find((t) => t.content === text);
  if (!tok?.color) throw new Error(`no coloured "${text}" token in the ${scheme} result`);
  return asDomColour(tok.color);
}

afterEach(() => {
  cleanup();
  document.documentElement.removeAttribute("data-scheme");
  highlightSpy.mockClear();
});

describe("lib/highlight: which blocks get colours", () => {
  it("names a grammar for the fence languages a model writes, and none for the rest", () => {
    const cases: Record<string, string> = {
      ts: "typescript",
      TypeScript: "typescript",
      tsx: "tsx",
      js: "javascript",
      javascript: "javascript",
      jsx: "jsx",
      json: "json",
      py: "python",
      python: "python",
      bash: "bash",
      sh: "bash",
      shell: "bash",
      zsh: "bash",
      powershell: "powershell",
      ps1: "powershell",
      pwsh: "powershell",
      html: "html",
      css: "css",
      sql: "sql",
      yaml: "yaml",
      yml: "yaml",
      markdown: "markdown",
      md: "markdown",
      diff: "diff",
      patch: "diff",
      go: "go",
      rust: "rust",
      rs: "rust",
      java: "java",
      csharp: "csharp",
      cs: "csharp",
    };
    for (const [fence, id] of Object.entries(cases)) expect(codeLangId(fence), fence).toBe(id);
    for (const plain of ["", "text", "plaintext", "email", "chart", "brainfuck", "cobol", "constructor", "__proto__"]) {
      expect(codeLangId(plain), plain).toBeNull();
    }
    expect(codeLangId(undefined)).toBeNull();
    expect(codeLangId(null)).toBeNull();
  });

  it("the size cap: 2,000 lines and 100,000 characters still colour; one more of either does not", () => {
    expect(MAX_HIGHLIGHT_LINES).toBe(2_000);
    expect(MAX_HIGHLIGHT_CHARS).toBe(100_000);
    const lines = (n: number) => Array.from({ length: n }, (_, i) => `x${i}`).join("\n");
    expect(tooLargeToHighlight(lines(2_000))).toBe(false);
    expect(tooLargeToHighlight(lines(2_001))).toBe(true);
    expect(tooLargeToHighlight("a".repeat(100_000))).toBe(false);
    expect(tooLargeToHighlight("a".repeat(100_001))).toBe(true);
  });

  it("real Shiki: every colour comes from the theme's own data, plain runs carry none, the text is untouched", async () => {
    const code = sample("theme-data");
    const dark = await highlightCode(code, "ts", "dark");
    const light = await highlightCode(code, "ts", "light");
    for (const [r, file] of [
      [dark, "github-dark-default"],
      [light, "github-light-default"],
    ] as const) {
      expect(r, file).not.toBeNull();
      // The words are exactly the code (lines joined back with newlines).
      expect(r!.lines.map((l) => l.map((t) => t.content).join("")).join("\n")).toBe(code);
      const theme = (await import(`shiki/themes/${file}.mjs`)).default as {
        colors?: Record<string, unknown>;
        tokenColors?: Array<{ settings?: { foreground?: unknown } }>;
      };
      const palette = new Set(
        [
          ...Object.values(theme.colors ?? {}),
          ...(theme.tokenColors ?? []).map((t) => t.settings?.foreground ?? ""),
        ]
          .filter((c): c is string => typeof c === "string")
          .map((c) => c.toLowerCase()),
      );
      const tokens = r!.lines.flat();
      const coloured = tokens.filter((t) => t.color);
      // Anti-vacuity: a keyword and a comment are coloured, the spaces are not.
      expect(coloured.length, file).toBeGreaterThan(3);
      expect(tokens.some((t) => !t.color && t.content.trim() === ""), file).toBe(true);
      for (const t of coloured) expect(palette.has(t.color!.toLowerCase()), `${file}: ${t.color}`).toBe(true);
    }
    // Light and dark really are two palettes.
    const kw = (r: typeof dark) => r!.lines.flat().find((t) => t.content === "const")!.color;
    expect(kw(dark)).toBeTruthy();
    expect(kw(light)).toBeTruthy();
    expect(kw(dark)).not.toBe(kw(light));
  });

  it("an unknown language or an oversized block resolves to nothing", async () => {
    await expect(highlightCode("++++[>++<-]", "brainfuck", "dark")).resolves.toBeNull();
    await expect(highlightCode("a".repeat(100_001), "json", "dark")).resolves.toBeNull();
  });
});

describe("CodeBlock: the same panel, now in colour", () => {
  it("keeps the panel exactly as it was: classes, a <code> with the fence's language, the hover copy button", () => {
    render(<CodeBlock code={"brainfuck code"} lang="brainfuck" />);
    expect(block().className).toBe("group/code relative my-2");
    const pre = block().querySelector("pre")!;
    expect(pre.className).toBe(
      "overflow-x-auto rounded border border-white/[0.06] bg-ink-900/80 p-3 font-mono text-xs leading-relaxed text-zinc-200",
    );
    expect(codeEl().className).toBe("language-brainfuck");
    const copy = screen.getByRole("button", { name: "Copy code" });
    expect(copy.getAttribute("title")).toBe("Copy code");
    for (const cls of ["absolute", "right-2", "top-2", "opacity-0", "group-hover/code:opacity-100", "focus-visible:opacity-100"]) {
      expect(copy.classList.contains(cls), cls).toBe(true);
    }
    // The fade is a transition, and it stops for reduced motion.
    expect(copy.classList.contains("motion-reduce:transition-none")).toBe(true);
  });

  it("drops into react-markdown's fence the way Markdown's code branch hands it over (text + fence language)", async () => {
    // The exact call the integrator puts at the end of MarkdownPre, after the
    // draft and chart fences have been routed away.
    const md = [
      "Run this:",
      "",
      "```python",
      "print('hi')  # wiring",
      "```",
      "",
      "and an unlabelled one:",
      "",
      "```",
      "npm install",
      "```",
      "",
    ].join("\n");
    const { container } = render(
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          pre: ({ children }) => <CodeBlock code={nodeText(children).replace(/\n$/, "")} lang={fenceLang(children)} />,
        }}
      >
        {md}
      </ReactMarkdown>,
    );
    const [py, plain] = screen.getAllByTestId("code-block");
    expect(py.querySelector("pre > code")!.className).toBe("language-python");
    expect(py.querySelector("pre")!.textContent).toBe("print('hi')  # wiring");
    expect(plain.querySelector("pre > code")!.hasAttribute("class")).toBe(false);
    expect(plain.querySelector("pre")!.textContent).toBe("npm install");
    await waitFor(() => expect(py.dataset.highlighted).toBe("true"), LOAD);
    expect(py.querySelector("pre")!.textContent).toBe("print('hi')  # wiring");
    expect(plain.dataset.highlighted).toBe("false");
    expect(container.querySelectorAll("pre")).toHaveLength(2);
  });

  it("shows the plain text at once, then the same text in the theme's colours", async () => {
    const code = sample("plain-then-colour");
    render(<CodeBlock code={code} lang="ts" />);
    // First paint: plain, complete, nothing coloured yet.
    expect(block().dataset.highlighted).toBe("false");
    expect(codeEl().textContent).toBe(code);
    expect(colouredSpans()).toEqual([]);

    await waitFor(() => expect(block().dataset.highlighted).toBe("true"), LOAD);
    expect(colouredSpans().length).toBeGreaterThan(3);
    // Same words, same order — only colour was added.
    expect(codeEl().textContent).toBe(code);
    expect(codeEl().className).toBe("language-ts");
    expect(block().dataset.lang).toBe("typescript");
    // The keyword wears exactly the dark theme's keyword colour.
    expect(spansWithText("const")[0].style.color).toBe(await themeColourOf(code, "dark", "const"));
    // Tokens are spans, never injected HTML.
    expect(codeEl().innerHTML).not.toMatch(/<(?!\/?span\b)/);
  });

  it("switching the app to light re-colours the block from the light theme, and back", async () => {
    const code = sample("scheme-switch");
    render(<CodeBlock code={code} lang="ts" />);
    await waitFor(() => expect(block().dataset.highlighted).toBe("true"), LOAD);
    const dark = await themeColourOf(code, "dark", "const");
    expect(spansWithText("const")[0].style.color).toBe(dark);

    act(() => document.documentElement.setAttribute("data-scheme", "light"));
    await waitFor(() => {
      const now = spansWithText("const")[0]?.style.color;
      expect(now).toBeTruthy();
      expect(now).not.toBe(dark);
    });
    expect(highlightSpy).toHaveBeenCalledWith(code, "ts", "light");
    expect(spansWithText("const")[0].style.color).toBe(await themeColourOf(code, "light", "const"));
    expect(codeEl().textContent).toBe(code);

    act(() => document.documentElement.setAttribute("data-scheme", "dark"));
    await waitFor(() => expect(spansWithText("const")[0]?.style.color).toBe(dark));
  });

  it("the copy button copies the raw text (not the coloured markup) and says so to a screen reader", async () => {
    const code = sample("copy");
    const writeText = vi.fn(async (_: string) => {});
    const had = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    try {
      const { rerender } = render(<CodeBlock code={code} lang="ts" />);
      await waitFor(() => expect(block().dataset.highlighted).toBe("true"), LOAD);
      fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
      expect(writeText).toHaveBeenCalledWith(code);
      const live = block().querySelector('[aria-live="polite"]')!;
      await waitFor(() => expect(live.textContent).toBe("Copied"));
      expect(live.className).toBe("sr-only");
      // Mid-stream (review): the block has grown since its last pass. Copy
      // takes the text on screen now, never the last coloured pass's text.
      const grown = `${code}\nconst grown = 1;`;
      rerender(<CodeBlock code={grown} lang="ts" />);
      expect(codeEl().textContent).toBe(grown);
      fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
      expect(writeText).toHaveBeenLastCalledWith(grown);
    } finally {
      if (had) Object.defineProperty(navigator, "clipboard", had);
      else delete (navigator as { clipboard?: unknown }).clipboard;
    }
  });

  it("a block past the size cap stays plain and never asks for colours", async () => {
    const tallCode = Array.from({ length: 2_001 }, (_, i) => `x${i} = ${i}`).join("\n");
    const wideCode = `{"k": "${"a".repeat(100_001)}"}`;
    const small = "x = 1  # size-cap control";
    render(
      <div>
        <div data-testid="tall">
          <CodeBlock code={tallCode} lang="python" />
        </div>
        <div data-testid="wide">
          <CodeBlock code={wideCode} lang="json" />
        </div>
        <div data-testid="small">
          <CodeBlock code={small} lang="python" />
        </div>
      </div>,
    );
    const inside = (id: string) => screen.getByTestId(id).querySelector('[data-testid="code-block"]') as HTMLElement;
    // Anti-vacuity: a normal block beside them DOES get coloured.
    await waitFor(() => expect(inside("small").dataset.highlighted).toBe("true"), LOAD);
    for (const id of ["tall", "wide"]) {
      expect(inside(id).dataset.highlighted, id).toBe("false");
      expect(inside(id).querySelectorAll("code span").length, id).toBe(0);
    }
    expect(inside("tall").querySelector("code")!.textContent).toBe(tallCode);
    const asked = highlightSpy.mock.calls.map((c) => c[0]);
    expect(asked).toContain(small);
    expect(asked).not.toContain(tallCode);
    expect(asked).not.toContain(wideCode);
  });

  it("a language we do not colour, or no language at all, stays plain", async () => {
    render(
      <div>
        <div data-testid="unknown">
          <CodeBlock code={"++++[>++<-]"} lang="brainfuck" />
        </div>
        <div data-testid="none">
          <CodeBlock code={"just words"} />
        </div>
        <div data-testid="known">
          <CodeBlock code={sample("unknown-control")} lang="typescript" />
        </div>
      </div>,
    );
    const inside = (id: string) => screen.getByTestId(id).querySelector('[data-testid="code-block"]') as HTMLElement;
    await waitFor(() => expect(inside("known").dataset.highlighted).toBe("true"), LOAD);
    expect(inside("unknown").dataset.highlighted).toBe("false");
    expect(inside("unknown").querySelectorAll("code span").length).toBe(0);
    expect(inside("unknown").querySelector("code")!.textContent).toBe("++++[>++<-]");
    expect(inside("none").dataset.highlighted).toBe("false");
    expect(inside("none").querySelector("code")!.hasAttribute("class")).toBe(false);
    const asked = highlightSpy.mock.calls.map((c) => c[0]);
    expect(asked).not.toContain("++++[>++<-]");
    expect(asked).not.toContain("just words");
  });

  it("a highlight that fails keeps the plain text, silently", async () => {
    const code = sample("fails");
    highlightSpy.mockImplementationOnce(() => Promise.reject(new Error("engine down")));
    const errors = vi.spyOn(console, "error");
    try {
      render(<CodeBlock code={code} lang="ts" />);
      await waitFor(() => expect(highlightSpy).toHaveBeenCalledTimes(1));
      await expect(highlightSpy.mock.results[0].value).rejects.toThrow("engine down");
      await act(async () => {});
      expect(block().dataset.highlighted).toBe("false");
      expect(codeEl().textContent).toBe(code);
      expect(colouredSpans()).toEqual([]);
      expect(errors).not.toHaveBeenCalled();
    } finally {
      errors.mockRestore();
    }
  });

  it("while a reply streams, the coloured start stays coloured and the new tail shows at once", async () => {
    const first = sample("stream");
    const { rerender } = render(<CodeBlock code={first} lang="ts" />);
    await waitFor(() => expect(block().dataset.highlighted).toBe("true"), LOAD);
    const keywordColour = spansWithText("const")[0].style.color;
    expect(spansWithText("const")).toHaveLength(1);

    const next = `${first}\nconst more = 2;`;
    rerender(<CodeBlock code={next} lang="ts" />);
    // Immediately: every character is there, the start is still coloured.
    expect(codeEl().textContent).toBe(next);
    expect(block().dataset.highlighted).toBe("true");
    expect(spansWithText("const")).toHaveLength(1);
    // The next pass colours the tail too.
    await waitFor(() => expect(spansWithText("const")).toHaveLength(2));
    expect(spansWithText("const")[1].style.color).toBe(keywordColour);
    expect(codeEl().textContent).toBe(next);

    // A change that is NOT a continuation shows plain at once (never colours
    // painted over the wrong characters), then colours again.
    const other = sample("stream-replaced").replace("answer", "reply");
    rerender(<CodeBlock code={other} lang="ts" />);
    expect(codeEl().textContent).toBe(other);
    expect(block().dataset.highlighted).toBe("false");
    await waitFor(() => expect(block().dataset.highlighted).toBe("true"));
  });

  it("a re-render that only grows the plain tail does not redraw the coloured lines (review)", async () => {
    // A streaming reply re-renders its block every frame. Drawing a token
    // reads its text, so count every read of the pass the block shows: the
    // frames between two passes must read none (rebuilding every span each
    // frame cost ~65 ms per re-render for a 1,000-line block in jsdom).
    const real = highlightSpy.getMockImplementation()!;
    let reads = 0;
    highlightSpy.mockImplementationOnce(async (...args) => {
      const r = await real(...args);
      if (!r) return r;
      const lines = r.lines.map((line) =>
        line.map((t) =>
          Object.defineProperty({ ...t }, "content", {
            enumerable: true,
            get: () => {
              reads += 1;
              return t.content;
            },
          }),
        ),
      );
      return { ...r, lines };
    });
    let code = sample("memo");
    const { rerender } = render(<CodeBlock code={code} lang="ts" />);
    await waitFor(() => expect(block().dataset.highlighted).toBe("true"), LOAD);
    // Anti-vacuity: drawing the pass did read its tokens.
    expect(reads).toBeGreaterThan(0);
    reads = 0;
    for (let i = 0; i < 5; i++) {
      code += ` tail${i}`;
      act(() => rerender(<CodeBlock code={code} lang="ts" />));
      expect(codeEl().textContent).toBe(code);
      expect(block().dataset.highlighted).toBe("true");
    }
    expect(reads).toBe(0);
  });

  it("a fast-growing block is re-coloured at a steady pace, not on every change, and the last change always lands", async () => {
    let code = sample("throttle");
    const { rerender } = render(<CodeBlock code={code} lang="ts" />);
    await waitFor(() => expect(block().dataset.highlighted).toBe("true"), LOAD);
    highlightSpy.mockClear();
    const started = performance.now();
    for (let i = 0; i < 8; i++) {
      code += `\nconst t${i} = ${i};`;
      rerender(<CodeBlock code={code} lang="ts" />);
      // A real gap between changes, as tokens arrive: an unthrottled block
      // would get a pass in every one of them.
      await act(() => new Promise((r) => setTimeout(r, 20)));
    }
    const elapsed = performance.now() - started;
    // At most one pass per 250 ms window (measured, not assumed: a slow
    // runner that stretches the burst is allowed proportionally more).
    expect(highlightSpy.mock.calls.length).toBeLessThanOrEqual(Math.ceil(elapsed / 250) + 1);
    expect(highlightSpy.mock.calls.length).toBeLessThan(8);
    await waitFor(() => expect(spansWithText("const")).toHaveLength(9));
    expect(highlightSpy).toHaveBeenLastCalledWith(code, "ts", "dark");
  });

  it("an older pass that finishes late never replaces a newer one", async () => {
    const real = highlightSpy.getMockImplementation()!;
    const first = sample("late-pass");
    let release!: () => Promise<void>;
    highlightSpy.mockImplementationOnce(
      (...args) =>
        new Promise((resolve) => {
          release = async () => {
            resolve(await real(...args));
          };
        }),
    );
    const { rerender } = render(<CodeBlock code={first} lang="ts" />);
    await waitFor(() => expect(highlightSpy).toHaveBeenCalledTimes(1));
    // The reply grows while the first pass is still out.
    const next = `${first}\nconst later = 3;`;
    rerender(<CodeBlock code={next} lang="ts" />);
    await waitFor(() => expect(spansWithText("const")).toHaveLength(2), LOAD);
    // ...and grows again: its next pass waits out the throttle, so for a
    // moment the newest finished pass (two coloured keywords) is what shows.
    const third = `${next}\nconst third = 4;`;
    rerender(<CodeBlock code={third} lang="ts" />);
    expect(spansWithText("const").length).toBeGreaterThanOrEqual(2);
    // Now the first (older, shorter) pass lands: it must be ignored, not
    // wind the colours back to its one keyword.
    await act(async () => {
      await release();
    });
    expect(spansWithText("const").length).toBeGreaterThanOrEqual(2);
    expect(block().dataset.highlighted).toBe("true");
    expect(codeEl().textContent).toBe(third);
    await waitFor(() => expect(spansWithText("const")).toHaveLength(3));
  });

  it("a block that remounts (switching chats and back) shows its colours at once", async () => {
    const code = sample("remount");
    const first = render(<CodeBlock code={code} lang="ts" />);
    await waitFor(() => expect(block().dataset.highlighted).toBe("true"), LOAD);
    first.unmount();
    highlightSpy.mockClear();
    render(<CodeBlock code={code} lang="ts" />);
    expect(block().dataset.highlighted).toBe("true");
    expect(colouredSpans().length).toBeGreaterThan(3);
    expect(codeEl().textContent).toBe(code);
  });
});
