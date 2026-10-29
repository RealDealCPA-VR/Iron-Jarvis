import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type APIRequestContext, type Page, type Route } from "@playwright/test";

/**
 * v1.405.0 — a coding session's Changes pane as ONE SCROLLING REVIEW, in a real
 * browser against the scratch daemon (a real session on a real folder).
 *
 * What the session changed (`POST /chat/changes`) and the editor this PC has
 * (`GET /editor/options`) are STUBBED in the browser, and `POST /editor/open`
 * is STUBBED too and only recorded: no editor is ever launched by this file.
 *
 *  - every file's diff is on screen in one scroll, with no press;
 *  - "Open at line N" asks to open the file at the line of the hunk's first
 *    CHANGE in the new file (v1.405.0 review: not the context line above
 *    it), naming the editor it opens;
 *  - nothing inside the pane is wider than the pane, and the page never pans
 *    sideways — at 1280 × 800 (the panes beside the conversation) and
 *    390 × 844 (stacked under it);
 *  - Wide view opens the same review side by side (1280).
 *
 * Screenshots go to IJ_REVIEW_OUT when set.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
const OUT = process.env.IJ_REVIEW_OUT || "";
const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "*",
  "access-control-allow-methods": "GET, POST, PATCH, PUT, DELETE, OPTIONS",
};

let work = "";

test.beforeAll(() => {
  work = mkdtempSync(join(tmpdir(), "ij-review-pane-"));
  mkdirSync(join(work, "src"), { recursive: true });
  writeFileSync(join(work, "src", "cart.py"), "a = 1\n");
});

test.afterAll(() => {
  try {
    if (work) rmSync(work, { recursive: true, force: true });
  } catch {
    /* the OS clears temp */
  }
});

async function stub(route: Route, body: unknown): Promise<void> {
  if (route.request().method() === "OPTIONS") {
    await route.fulfill({ status: 204, headers: CORS });
    return;
  }
  await route.fulfill({ status: 200, headers: { ...CORS, "content-type": "application/json" }, body: JSON.stringify(body) });
}

async function shot(page: Page, name: string): Promise<void> {
  if (!OUT) return;
  mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: join(OUT, `${name}.png`) });
}

function diffOf(n: number, start: number, longLine: boolean): string {
  const lines = [`@@ -${start},3 +${start},4 @@`, " context line"];
  for (let i = 0; i < n; i++) lines.push(`-old value ${i}`, `+new value ${i}`);
  if (longLine) lines.push(`+${"a_very_long_identifier_without_any_spaces_".repeat(8)}`);
  lines.push(" tail");
  return `--- a/x\n+++ b/x\n${lines.join("\n")}\n`;
}

function changes(): unknown[] {
  const deep = "src/components/very/deeply/nested/folder/structure/that/goes/on/CartSummaryPanel.tsx";
  return [
    { rel: "src/cart.py", status: "modified", added: 4, removed: 3, diff: diffOf(3, 3, false) },
    { rel: deep, status: "modified", added: 7, removed: 6, diff: diffOf(6, 120, true) },
    { rel: "src/new_module.py", status: "created", added: 3, removed: 0, diff: "--- /dev/null\n+++ b/n\n@@ -0,0 +1,3 @@\n+one\n+two\n+three\n" },
    { rel: "old.py", status: "deleted", added: 0, removed: 1, diff: "--- a/old.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-bye\n" },
    { rel: "README.md", status: "modified", added: 2, removed: 2, diff: diffOf(2, 10, false) },
  ].map((c) => ({
    ...c,
    path: join(work, ...c.rel.split("/")),
    name: c.rel.split("/").pop(),
    changed_since: c.rel === "README.md",
  }));
}

async function open(page: Page, request: APIRequestContext): Promise<{ opened: unknown[] }> {
  const a = await request.post(`${DAEMON}/build/sessions`, { data: { folder: work, branch: false } });
  expect(a.ok(), await a.text()).toBe(true);
  const view = (await a.json()) as Record<string, unknown> & { id: string };
  const now = new Date().toISOString();
  const withMessages = {
    ...view,
    messages: [
      { role: "user", content: "Tidy the cart", at: now },
      {
        role: "assistant",
        content: "Done.",
        at: now,
        steps: [
          {
            name: "read_file",
            ok: true,
            ms: 4,
            target: "cart.py",
            detail: { input: '{"path": "src/cart.py", "offset": 2}', output: "a = 1" },
          },
        ],
        timing: { startedAt: Date.now() - 4000, firstTokenAt: null, endedAt: Date.now() },
        helpers: [
          {
            id: "ij-finish-review",
            session: "s-review",
            agent: "reviewer",
            task: "Review the change",
            state: "done",
            steps: 1,
            startedAt: Date.now() - 3000,
            endedAt: Date.now() - 1000,
            result: "Changes needed: src/cart.py:1 drops the tax; see https://example.com/a.py:9 for why.\nVERDICT: changes needed",
            work: [],
            role: "review",
          },
        ],
      },
    ],
  };
  const opened: unknown[] = [];
  await page.route(`${DAEMON}/build/sessions/${view.id}`, (route) =>
    route.request().method() === "GET" ? stub(route, withMessages) : route.fallback(),
  );
  await page.route(`${DAEMON}/chat/changes`, (route) =>
    stub(route, { changes: changes(), files: 5, added: 16, removed: 12, truncated_files: false }),
  );
  await page.route(`${DAEMON}/editor/options`, (route) =>
    stub(route, {
      editors: [
        { id: "vscode", label: "VS Code", installed: true },
        { id: "cursor", label: "Cursor", installed: false },
      ],
      preferred: "vscode",
      preferred_label: "VS Code",
      setting: "auto",
      known: true,
      note: "",
    }),
  );
  // NEVER the real route: the scratch daemon would launch an editor.
  await page.route(`${DAEMON}/editor/open`, async (route) => {
    if (route.request().method() === "POST") opened.push(route.request().postDataJSON());
    await stub(route, { ok: true, editor: "vscode", label: "VS Code", line: 1 });
  });
  await page.goto(`/terminals?session=${view.id}`);
  await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
  return { opened };
}

async function pan(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

/** Every element inside the pane that reaches past the pane's own box. */
async function overflowing(page: Page): Promise<string[]> {
  return page.getByTestId("pane-changes").evaluate((root) => {
    const box = root.getBoundingClientRect();
    const bad: string[] = [];
    if (root.scrollWidth > root.clientWidth + 1) bad.push(`pane-changes scrolls sideways (${root.scrollWidth} > ${root.clientWidth})`);
    for (const el of Array.from(root.querySelectorAll("*"))) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      if (r.right > box.right + 1 || r.left < box.left - 1) {
        const id = el.getAttribute("data-testid") || el.tagName.toLowerCase();
        bad.push(`${id}: ${Math.round(r.left)}..${Math.round(r.right)} outside ${Math.round(box.left)}..${Math.round(box.right)}`);
      }
    }
    return bad;
  });
}

for (const { width, height } of [
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
]) {
  test(`at ${width}×${height}: every changed file's diff is in one scroll, inside the pane`, async ({ page, request }) => {
    await page.setViewportSize({ width, height });
    const { opened } = await open(page, request);
    const tab = page.getByTestId("harness-pane-tab-changes");
    await tab.scrollIntoViewIfNeeded();
    await tab.click();
    await expect(page.getByTestId("changes-review")).toBeVisible();
    await expect(page.getByTestId("review-summary")).toHaveText("5 files changed +16 −12 · 0 of 5 reviewed");
    // Every file's rows, with no press.
    for (const i of [0, 1, 2, 3, 4]) await expect(page.getByTestId(`review-rows-${i}`)).toBeAttached();
    await expect(page.getByTestId("review-changed-since-4")).toHaveText("changed since");
    // A deleted file opens nothing; a hunk opens at its first CHANGE (the
    // hunk starts at 120 with one context line, so the change is at 121).
    await expect(page.getByTestId("review-open-3")).toHaveCount(0);
    const hunk = page.getByTestId("review-hunk-open-1").first();
    await hunk.scrollIntoViewIfNeeded();
    await expect(hunk).toHaveText("Open at line 121");
    await hunk.click();
    await expect.poll(() => opened.length).toBe(1);
    expect(opened[0]).toEqual({ path: join(work, "src", "components", "very", "deeply", "nested", "folder", "structure", "that", "goes", "on", "CartSummaryPanel.tsx"), line: 121, editor: "vscode" });

    // The page shows several files at once in the pane's scroll.
    await page.getByTestId("review-file-0").scrollIntoViewIfNeeded();
    // The top line is ONE line, and a short path is shown whole in its
    // header (a squeezed flex item once hid both: the first cut).
    const summaryBox = await page.getByTestId("review-summary").boundingBox();
    expect(summaryBox?.height ?? 99).toBeLessThanOrEqual(24);
    const path0 = page.getByTestId("review-path-0");
    await expect(path0).toHaveText("src/cart.py");
    expect(await path0.evaluate((el) => el.scrollWidth <= el.clientWidth && el.clientWidth > 40)).toBe(true);
    expect(await overflowing(page)).toEqual([]);
    expect(await pan(page)).toBeLessThanOrEqual(0);
    await shot(page, `review-pane-${width}`);

    // Reviewed: the file folds, its header stays, the count moves.
    await page.getByTestId("review-mark-0").check();
    await expect(page.getByTestId("review-summary")).toHaveText("5 files changed +16 −12 · 1 of 5 reviewed");
    await expect(page.getByTestId("review-rows-0")).toHaveCount(0);
    expect(await overflowing(page)).toEqual([]);

    if (width >= 1280) {
      await page.getByTestId("review-wide-open").click();
      const dlg = page.getByTestId("review-wide");
      await expect(dlg.getByRole("dialog")).toBeVisible();
      await expect(dlg.getByTestId("diff-split").first()).toBeVisible();
      await expect(dlg.getByTestId("review-wide-summary")).toContainText("1 of 5 reviewed");
      await shot(page, `review-pane-${width}-wide`);
      await dlg.getByRole("button", { name: "Close" }).click();
      await expect(dlg).toHaveCount(0);
    }
  });
}

test("at 1280×800: a step and a reviewer's finding open in the editor at their line", async ({ page, request }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  const { opened } = await open(page, request);
  // The step: its press shows when the pointer rests on its line.
  await page.getByTestId("work-line").getByRole("button").first().click();
  const row = page.getByTestId("work-row-with-open").first();
  await row.hover();
  const step = row.getByTestId("step-open-in-editor");
  await expect(step).toHaveText("Open in VS Code");
  await expect(step).toBeVisible();
  await step.click();
  await expect.poll(() => opened.length).toBe(1);
  expect(opened[0]).toEqual({ path: join(work, "src", "cart.py"), line: 2, editor: "vscode" });
  // The finding: the place it names opens; the web address stays text.
  await page.getByTestId("agent-run-toggle").first().click();
  const refs = page.getByTestId("editor-ref");
  await expect(refs).toHaveCount(1);
  await expect(refs.first()).toHaveText("src/cart.py:1");
  await refs.first().click();
  await expect.poll(() => opened.length).toBe(2);
  expect(opened[1]).toEqual({ path: join(work, "src", "cart.py"), line: 1, editor: "vscode" });
  expect(await pan(page)).toBeLessThanOrEqual(0);
  await shot(page, "review-pane-1280-step-and-finding");
});
