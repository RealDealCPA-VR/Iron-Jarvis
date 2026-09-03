/**
 * v1.226.0 — three mutations that failed SILENTLY now say so, and the
 * dictation socket tears down on a server-side close. Source pins with
 * anchors unique to the post-change files (the render harnesses for these
 * 2000-line pages cost more than they prove here):
 *
 *  - F-F-5 app/artifacts/page.tsx remove(): try/catch -> ErrorNote (it was
 *    the one truly uncaught mutation — an unhandled rejection and nothing on
 *    screen).
 *  - F-F-5 components/memory/Lessons.tsx forget(): reload() in the catch too
 *    (a 404 = already gone) and a non-0 error is surfaced.
 *  - F-F-8 app/tools/page.tsx: a failed auto-approve PATCH keeps the dialog
 *    open with "Connected, but auto-approve could not be saved: …" instead of
 *    closing as success.
 *  - F-D-6 lib/useDictation.ts: ws.onclose tears down + errors when still
 *    wanted (mic used to stay on, "listening" forever).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const read = (rel: string) => readFileSync(join(process.cwd(), rel), "utf8");

describe("artifacts remove() surfaces its failure (F-F-5)", () => {
  const src = read("app/artifacts/page.tsx");
  it("wraps the DELETE and renders the error", () => {
    const fn = src.slice(src.indexOf("async function remove(id: string)"));
    const body = fn.slice(0, fn.indexOf("list.reload();"));
    expect(body).toContain("await del(`/code-artifacts/${encodeURIComponent(id)}`);");
    expect(body).toMatch(/try \{\s*await del\(/);
    expect(body).toContain("setRemoveError(e instanceof ApiError ? e.message : String(e));");
    expect(src).toContain("<ErrorNote>Could not delete this script: {removeError}</ErrorNote>");
  });
});

describe("Lessons forget() reloads on failure and surfaces non-0 (F-F-5)", () => {
  const src = read("components/memory/Lessons.tsx");
  it("the catch calls reload() and sets the error for a real daemon failure", () => {
    const fn = src.slice(src.indexOf("async function forget(id: string)"));
    const catchBlock = fn.slice(fn.indexOf("} catch (err) {"), fn.indexOf("} finally {"));
    expect(catchBlock).toContain("reload();");
    expect(catchBlock).toContain("if (!(err instanceof ApiError && err.status === 0))");
    expect(catchBlock).toContain("setForgetError(err instanceof ApiError ? err.message : String(err));");
    expect(src).toContain("<ErrorNote>Could not forget that lesson: {forgetError}</ErrorNote>");
  });
});

describe("tools: auto-approve grant failure keeps the dialog open (F-F-8)", () => {
  const src = read("app/tools/page.tsx");
  it("sets the pending error and returns before setPending(null)", () => {
    const at = src.indexOf("Connected, but auto-approve could not be saved:");
    expect(at).toBeGreaterThan(-1);
    const after = src.slice(at, at + 400);
    // The early return comes BEFORE the dialog would be closed.
    expect(after.indexOf("return;")).toBeGreaterThan(-1);
    expect(after.indexOf("return;")).toBeLessThan(after.indexOf("setPending(null);"));
    // The catch no longer swallows silently.
    expect(src).not.toContain("/* connected either way; the panel shows the real state */");
  });
});

describe("dictation: server-side close tears down (F-D-6)", () => {
  const src = read("lib/useDictation.ts");
  it("ws.onclose errors + tears down while still wanted, and ignores our own close", () => {
    const at = src.indexOf("ws.onclose = () => {");
    expect(at).toBeGreaterThan(-1);
    const handler = src.slice(at, at + 300);
    expect(handler).toContain("if (wsRef.current !== ws) return;");
    expect(handler).toContain('setError("Voice stream closed");');
    expect(handler).toMatch(/if \(wantRef\.current\) \{[\s\S]*teardown\(\);/);
  });
});
