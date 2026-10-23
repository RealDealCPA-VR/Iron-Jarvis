/**
 * v1.378.0 review (IJ harness part 2, Phase 7): ONE table for a step's safe
 * target, read by both of its implementations.
 *
 * The chat page works a step's target out here (lib/workTarget stepTarget)
 * and saves it with the step; a Build session's reply line is written by the
 * DAEMON, whose twin is src/iron_jarvis/harness/step_target.py. Without one
 * shared answer the live row ("Ran python") and the saved row ("Ran · shell")
 * read differently — the walkthrough's gap 3. This test holds the TypeScript
 * side to the fixture (and writes it with IJ_REGEN_STEP_TARGET=1);
 * tests/test_step_target_v1378.py holds the Python side to the same file.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { KIND_OF_TOOLS, stepTarget } from "@/lib/workTarget";

const FIXTURE = join(__dirname, "fixtures", "step-target-v1378.json");

type Case = { name: string; args: unknown; target: string | null };

const INPUTS: Array<Omit<Case, "target">> = [
  { name: "read_file", args: { path: "C:\\Users\\me\\proj\\cart.py" } },
  { name: "read_file", args: { file_path: "/home/a/b/notes.txt", numbered: true } },
  { name: "read_file", args: { path: "C:" } },
  { name: "read_file", args: { paths: ["a/b.txt", "c\\d.txt", "e.txt"] } },
  { name: "read_file", args: { path: "C:\\keys\\sk-ant-abcdefghijklmnopqrstuvwxyz.txt" } },
  { name: "read_file", args: null },
  { name: "read_document", args: { path: "D:/clients/k-1 2025.pdf" } },
  { name: "extract_pdf", args: { source: "return.pdf" } },
  { name: "view_image", args: { path: "shot.png" } },
  { name: "excel_read", args: { path: "sales.xlsx" } },
  { name: "excel_profile", args: { file: "sales.xlsx" } },
  { name: "image_info", args: { path: "x/y/logo.svg" } },
  { name: "pdf_form_fields", args: { path: "w9.pdf" } },
  { name: "write_file", args: { content: "print(1)\n", path: "out/report.md" } },
  { name: "write_document", args: { output: "C:/x/sales.xlsx", source: "a.csv" } },
  { name: "convert_document", args: { source: "a.docx", target: "a.pdf" } },
  { name: "image_convert", args: { path: "a.png", out_dir: "C:\\out" } },
  { name: "image_resize", args: { path: "big.jpg" } },
  { name: "pdf_split", args: { path: "all.pdf" } },
  { name: "redact_pii", args: { path: "k1.pdf" } },
  { name: "edit_file", args: { new: "b", old: "a", path: "src/app.ts" } },
  { name: "multi_edit", args: { edits: [{ new: "b", old: "a" }], path: "a\\b.py" } },
  { name: "notebook_edit", args: { path: "nb.ipynb" } },
  { name: "docx_edit", args: { path: "letter.docx" } },
  { name: "excel_edit", args: { path: "book.xlsx" } },
  { name: "excel_apply_spec", args: { path: "book.xlsx" } },
  { name: "rename_file", args: { src: "old.txt", dst: "new.txt" } },
  { name: "pdf_arrange", args: { path: "a.pdf" } },
  { name: "pdf_form_fill", args: { path: "form.pdf" } },
  { name: "list_files", args: {} },
  { name: "list_files", args: { path: "." } },
  { name: "list_folder", args: { folder: "C:\\Users\\me\\Documents\\" } },
  { name: "glob", args: { pattern: "**/*.py" } },
  { name: "grep", args: { path: "src", pattern: "TODO" } },
  { name: "grep", args: { pattern: "C:\\Users\\me\\notes.txt foo" } },
  { name: "grep", args: { pattern: "password=hunter2" } },
  { name: "grep", args: { pattern: "a".repeat(80) } },
  { name: "grep", args: { pattern: "line1\nline2\tx" } },
  { name: "grep", args: { pattern: "abcdefABCDEF1234567890abcdefABCDEF12" } },
  { name: "grep", args: { pattern: "😀".repeat(40) } },
  { name: "grep", args: { pattern: "   " } },
  { name: "web_search", args: { query: "pier 9 hours https://www.example.com/x?y=1" } },
  { name: "web_fetch", args: { url: "https://www.Example.com/path?token=abc" } },
  { name: "web_fetch", args: { url: "ftp://x.com/a" } },
  { name: "web_fetch", args: { url: "http://[::1]:8080/x" } },
  { name: "shell", args: { command: "python -m pytest -q" } },
  { name: "shell", args: { command: '"C:\\Python\\python.exe" -c 1' } },
  { name: "shell", args: { command: 'cd "C:\\x" && python -m pytest' } },
  { name: "shell", args: { command: "FOO=1 make" } },
  { name: "shell", args: { command: "npm.cmd test" } },
  { name: "shell", args: { command: "curl -H 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz123' x" } },
  { name: "process_start", args: { command: "node server.js", until_exit: false } },
  { name: "run_code", args: { code: "x", language: "Python" } },
  { name: "run_code", args: { language: "c#" } },
  { name: "run_code", args: { language: "not a language!" } },
  { name: "repl", args: { code: "x = 1" } },
  { name: "rest_call", args: { method: "GET", path: "/x", service: "crm (svc_123)" } },
  { name: "page_open", args: { url: "http://localhost:5173/cart?token=1" } },
  { name: "page_open", args: { url: "localhost:8821" } },
  { name: "page_open", args: { url: "http://localhost:80/" } },
  { name: "page_open", args: { url: "http://[::1]:5173/a" } },
  { name: "page_screenshot", args: { url: "https://example.com/" } },
  { name: "page_click", args: { target: "Add to cart" } },
  { name: "page_type", args: { css: "#email", text: "secret" } },
  { name: "page_read", args: {} },
  { name: "browser_click", args: { target: "Buy" } },
  { name: "mcp__gmail__search_messages", args: { query: "invoice" } },
  { name: "unknown_tool", args: { path: "x.txt" } },
];

function computed(): Case[] {
  return INPUTS.map((c) => ({
    ...c,
    target: stepTarget(c.name, c.args as Record<string, unknown> | null),
  }));
}

describe("the shared step-target table", () => {
  it("matches lib/workTarget, case by case (IJ_REGEN_STEP_TARGET=1 writes it)", () => {
    const now = { kinds: [...KIND_OF_TOOLS].sort(), cases: computed() };
    if (process.env.IJ_REGEN_STEP_TARGET === "1") {
      writeFileSync(FIXTURE, `${JSON.stringify(now, null, 2)}\n`, "utf8");
    }
    const saved = JSON.parse(readFileSync(FIXTURE, "utf8").replace(/\r\n/g, "\n"));
    expect(saved).toEqual(now);
  });

  it("covers every built-in the table words", () => {
    const named = new Set(INPUTS.map((c) => c.name));
    expect(KIND_OF_TOOLS.filter((t) => !named.has(t))).toEqual([]);
  });
});
