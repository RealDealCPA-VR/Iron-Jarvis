/**
 * v1.280.0 — the Build page prunes the per-pane storage of panes that no
 * longer exist.
 *
 * `ij.pane.view.<id>`, `ij.pane.thread.<id>` and the entries of the canvas
 * rect map `ij_term_layout` were written per pane and never removed, so they
 * grew for the life of an install. Pane ids survive a daemon restart, so the
 * live list the daemon answers is the truth: anything not in it is gone.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  LAYOUT_KEY,
  PANE_THREAD_PREFIX,
  PANE_VIEW_PREFIX,
  prunePaneStorage,
} from "@/components/terminal/paneKeys";

/** Source read with line ends normalised (CI checks out CRLF). */
function src(rel: string): string {
  return readFileSync(join(process.cwd(), rel), "utf8").replace(/\r\n/g, "\n");
}

describe("the Build page calls the prune after a successful pane load (v1.280.0)", () => {
  it("prunes against the live ids, right where the hosts are retained, and its keys are the writers' keys", () => {
    const page = src("app/terminals/page.tsx");
    const retain = page.indexOf("retainPaneHosts(alive.map((t) => t.id));");
    const prune = page.indexOf("prunePaneStorage(alive.map((t) => t.id));");
    expect(retain).toBeGreaterThan(-1);
    expect(prune).toBeGreaterThan(retain);
    // The prune reads the live list the same `get("/terminals")` answered —
    // no other call site, so a failed fetch can never prune.
    expect(page.split("prunePaneStorage(").length - 1).toBe(1);
    expect(page).toContain("const paneViewKey = (id: string) => `${PANE_VIEW_PREFIX}${id}`;");
    const core = src("components/terminal/paneChatCore.ts");
    expect(core).toContain(`export const PANE_THREAD_PREFIX = "${PANE_THREAD_PREFIX}";`);
  });
});

function fakeStorage(init: Record<string, string>): Storage {
  const m = new Map(Object.entries(init));
  return {
    get length() {
      return m.size;
    },
    key: (i: number) => Array.from(m.keys())[i] ?? null,
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
    clear: () => m.clear(),
  } as Storage;
}

describe("prunePaneStorage (v1.280.0)", () => {
  it("removes the view, thread and rect of every pane not in the live list, and keeps the rest", () => {
    const s = fakeStorage({
      [`${PANE_VIEW_PREFIX}live1`]: "chat",
      [`${PANE_VIEW_PREFIX}dead1`]: "terminal",
      [`${PANE_THREAD_PREFIX}live1`]: "t1",
      [`${PANE_THREAD_PREFIX}dead2`]: "t2",
      [LAYOUT_KEY]: JSON.stringify({ live1: { x: 1 }, dead1: { x: 2 }, dead3: { x: 3 } }),
      ij_term_tree_collapsed: "1",
      "ij.build.shape": "rail",
    });
    const report = prunePaneStorage(["live1", "live2"], s);
    expect(report).toEqual({ keys: 2, rects: 2 });
    expect(s.getItem(`${PANE_VIEW_PREFIX}live1`)).toBe("chat");
    expect(s.getItem(`${PANE_THREAD_PREFIX}live1`)).toBe("t1");
    expect(s.getItem(`${PANE_VIEW_PREFIX}dead1`)).toBeNull();
    expect(s.getItem(`${PANE_THREAD_PREFIX}dead2`)).toBeNull();
    expect(JSON.parse(s.getItem(LAYOUT_KEY)!)).toEqual({ live1: { x: 1 } });
    // Keys that are not per-pane are untouched.
    expect(s.getItem("ij_term_tree_collapsed")).toBe("1");
    expect(s.getItem("ij.build.shape")).toBe("rail");
  });

  it("with nothing dead, writes nothing", () => {
    const layout = JSON.stringify({ a: { x: 1 } });
    const s = fakeStorage({ [`${PANE_VIEW_PREFIX}a`]: "chat", [LAYOUT_KEY]: layout });
    let writes = 0;
    const original = s.setItem;
    s.setItem = (k, v) => {
      writes += 1;
      original(k, v);
    };
    expect(prunePaneStorage(["a"], s)).toEqual({ keys: 0, rects: 0 });
    expect(writes).toBe(0);
    expect(s.getItem(LAYOUT_KEY)).toBe(layout);
  });

  it("an empty live list prunes everything per-pane — the caller only passes one after a 200", () => {
    const s = fakeStorage({ [`${PANE_VIEW_PREFIX}a`]: "chat", [LAYOUT_KEY]: JSON.stringify({ a: {} }) });
    expect(prunePaneStorage([], s)).toEqual({ keys: 1, rects: 1 });
    expect(JSON.parse(s.getItem(LAYOUT_KEY)!)).toEqual({});
  });

  it("a corrupt rect map or a refusing store prunes nothing and never throws", () => {
    const s = fakeStorage({ [LAYOUT_KEY]: "{not json", [`${PANE_VIEW_PREFIX}dead`]: "chat" });
    // The keys are pruned before the map is read; the bad map is left alone.
    expect(prunePaneStorage(["x"], s)).toEqual({ keys: 1, rects: 0 });
    expect(s.getItem(LAYOUT_KEY)).toBe("{not json");
    const refusing = {
      get length(): number {
        throw new Error("SecurityError");
      },
    } as unknown as Storage;
    expect(prunePaneStorage(["x"], refusing)).toEqual({ keys: 0, rects: 0 });
    expect(prunePaneStorage(["x"], null)).toEqual({ keys: 0, rects: 0 });
  });
});
