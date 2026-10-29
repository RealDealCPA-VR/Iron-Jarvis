import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type APIRequestContext, type Page, type Route } from "@playwright/test";

/**
 * v1.404.0 — a coding session's SESSION tab in a real browser, against the
 * scratch daemon (a real session, its real folder). The conversation and the
 * background programs are STUBBED in the browser (a session's messages are
 * written only by turns; programs only by its tools): the stub adds a message
 * that carried a REAL picture in a temp folder and a file with a long name,
 * and a program with a long command line.
 *
 *  - the tab opens on its icon and shows the session;
 *  - the picture LOADS through the daemon's media route (a real thumbnail);
 *  - nothing in the tab is wider than the pane (a long file name and a long
 *    command line are cut, never pushed past the edge), and the page never
 *    pans sideways — at 1280 × 800 (the panes beside the conversation) and
 *    390 × 844 (stacked under it).
 *
 * Screenshots go to IJ_SESSION_RAIL_OUT when set.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
const OUT = process.env.IJ_SESSION_RAIL_OUT || "";
const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "*",
  "access-control-allow-methods": "GET, POST, PATCH, PUT, DELETE, OPTIONS",
};
// A 2 × 2 PNG (red, green / blue, white).
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP4z8DA8J+BkYHh////DAAe9gT9Ce00PgAAAABJRU5ErkJggg==",
  "base64",
);

let work = "";
let picture = "";
let longFile = "";

test.beforeAll(() => {
  work = mkdtempSync(join(tmpdir(), "ij-session-rail-"));
  mkdirSync(join(work, "uploads"), { recursive: true });
  picture = join(work, "uploads", "cart-mockup.png");
  writeFileSync(picture, PNG);
  longFile = join(work, "uploads", `${"quarterly-requirements-and-acceptance-notes-".repeat(3)}final.pdf`);
  writeFileSync(longFile, "%PDF-1.4\n");
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

async function newSession(request: APIRequestContext): Promise<{ id: string; view: Record<string, unknown> }> {
  const a = await request.post(`${DAEMON}/build/sessions`, { data: { folder: work, branch: false } });
  expect(a.ok(), await a.text()).toBe(true);
  const view = (await a.json()) as Record<string, unknown> & { id: string };
  return { id: view.id, view };
}

async function open(page: Page, request: APIRequestContext): Promise<void> {
  const { id, view } = await newSession(request);
  const now = new Date().toISOString();
  const withMessages = {
    ...view,
    messages: [
      {
        role: "user",
        content: "Build the cart page to match this mock-up and the notes",
        at: now,
        attachmentPaths: [picture, longFile],
        filePaths: ["src/components/very/deeply/nested/folder/structure/CartSummaryPanel.tsx"],
        skill: "review",
      },
      {
        role: "assistant",
        content: "Done.",
        at: now,
        context: { window: 1_000_000, used: 300_000, percent: 38, level: "ok" },
        folderRules: ["CLAUDE.md"],
      },
    ],
  };
  await page.route(`${DAEMON}/build/sessions/${id}`, (route) =>
    route.request().method() === "GET" ? stub(route, withMessages) : route.fallback(),
  );
  await page.route(`${DAEMON}/build/sessions/${id}/programs`, (route) =>
    stub(route, {
      programs: [
        {
          id: "p1",
          command: `npm run dev -- --port 5173 ${"--some-very-long-flag=with-a-long-value ".repeat(4)}`.trim(),
          running: true,
          stopped: false,
          exit_code: null,
          ran_s: 185,
          started_s: 185,
        },
      ],
    }),
  );
  await page.goto(`/terminals?session=${id}`);
  await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
}

async function pan(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

/** Every element inside the tab that reaches past the tab's own box. */
async function overflowing(page: Page): Promise<string[]> {
  return page.getByTestId("pane-session").evaluate((root) => {
    const box = root.getBoundingClientRect();
    const bad: string[] = [];
    if (root.scrollWidth > root.clientWidth + 1) bad.push(`pane-session scrolls sideways (${root.scrollWidth} > ${root.clientWidth})`);
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
  test(`at ${width}×${height}: the Session tab shows the session and its last attachment, inside the pane`, async ({
    page,
    request,
  }) => {
    await page.setViewportSize({ width, height });
    await open(page, request);
    const tab = page.getByTestId("harness-pane-tab-session");
    await tab.scrollIntoViewIfNeeded();
    await tab.click();
    const pane = page.getByTestId("pane-session");
    await expect(pane).toBeVisible();
    await expect(page.getByTestId("session-rail-folder")).toBeVisible();
    await expect(page.getByTestId("session-rail-context-percent")).toHaveText("38% used");
    await expect(page.getByTestId("session-rail-rules")).toHaveText("Follows CLAUDE.md");

    // The picture really loads through the daemon's media route.
    const thumb = page.getByTestId("session-rail-attachment-thumb");
    await expect(thumb).toHaveAttribute("alt", "cart-mockup.png");
    await expect
      .poll(() => thumb.evaluate((img) => (img as HTMLImageElement).complete && (img as HTMLImageElement).naturalWidth), {
        timeout: 15_000,
      })
      .toBe(2);
    await expect(page.getByTestId("session-rail-attachment-file")).toContainText("quarterly-requirements");
    await expect(page.getByTestId("session-rail-program")).toContainText("Running · 3 min");

    await page.getByTestId("session-rail-background").scrollIntoViewIfNeeded();
    expect(await overflowing(page)).toEqual([]);
    expect(await pan(page)).toBeLessThanOrEqual(0);
    await pane.evaluate((el) => el.closest("[role=tabpanel]")?.scrollTo(0, 0));
    await shot(page, `session-rail-${width}`);
    await page.getByTestId("session-rail-background").scrollIntoViewIfNeeded();
    await shot(page, `session-rail-${width}-below`);
  });
}
