import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * v1.418.0 (C4.6, C4.8) — Build › Code's keys, queue and history in a real
 * browser. Build › Code's own routes (`/build/app/…`) are STUBBED (no Claude
 * Code is started); a running reply is a turn stream that stays open.
 *
 *   - Esc stops a running reply (the Stop press);
 *   - Shift+Tab steps the mode ask → edits → plan → auto → ask, never into
 *     Full access;
 *   - Enter while it works QUEUES the message (a list under the box, × per
 *     message);
 *   - the `/` menu opens on `/` at the start of a later line, with hints;
 *   - "N earlier steps" + Load earlier puts the first message back on top.
 *
 * At 1280 × 800 and on a 390 × 844 phone; nothing pans sideways.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "*",
  "access-control-allow-methods": "GET, POST, PATCH, PUT, DELETE, OPTIONS",
};

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  if (route.request().method() === "OPTIONS") {
    await route.fulfill({ status: 204, headers: CORS });
    return;
  }
  await route.fulfill({ status, headers: { ...CORS, "content-type": "application/json" }, body: JSON.stringify(body) });
}

const MODES = [
  { id: "ask", label: "Ask first", dangerous: false },
  { id: "edits", label: "Auto edits", dangerous: false },
  { id: "plan", label: "Plan", dangerous: false },
  { id: "auto", label: "Auto", dangerous: false },
  { id: "full", label: "Full access", dangerous: true },
];

const TOOLS = {
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      modes: MODES,
      hidden_modes: [],
      models: [{ id: "", label: "Claude Code's default" }],
      efforts: ["low", "high"],
      accounts: [],
      commands: [],
    },
  ],
};

function session(mode = "ask") {
  return {
    id: "k1",
    harness: "claude-code",
    folder: "C:\\work\\shop",
    title: "Keys",
    account: "",
    model: "",
    effort: "",
    mode,
    harness_session_id: "",
    created_at: "2026-10-30T09:00:00Z",
    updated_at: "2026-10-30T09:00:00Z",
    archived_at: null,
    last_error: "",
  };
}

const COMMANDS = [
  { name: "review", description: "Review the change", argument_hint: "<pr number>" },
  { name: "release", description: "Cut a release" },
];

type Seen = { posts: { path: string; body: unknown }[]; patches: unknown[] };

async function stub(page: Page, opts: { running: boolean; view?: Record<string, unknown> }): Promise<Seen> {
  const seen: Seen = { posts: [], patches: [] };
  let mode = "ask";
  const view = () => ({
    session: session(mode),
    events: opts.running ? [{ type: "user", at: "x", text: "Count to thirty", turn: "turn_1" }] : [],
    running: opts.running ? "turn_1" : false,
    pending: [],
    commands: COMMANDS,
    ...(opts.view ?? {}),
  });
  await page.route(`${DAEMON}/build/app/harnesses`, (r) => json(r, TOOLS));
  await page.route(
    (url) => url.href.startsWith(`${DAEMON}/build/app/sessions`) && !url.pathname.includes("/sessions/"),
    (r) => json(r, { sessions: [session(mode)], work_root: "" }),
  );
  await page.route(
    (url) => url.pathname === "/build/app/sessions/k1",
    async (r) => {
      if (r.request().method() === "PATCH") {
        const body = r.request().postDataJSON() as { mode?: string };
        seen.patches.push(body);
        if (body.mode) mode = body.mode;
        await json(r, { session: session(mode), applied: { mode: "now" } });
        return;
      }
      await json(r, view());
    },
  );
  await page.route(`${DAEMON}/build/app/sessions/k1/turns/turn_1/stream`, async (r) => {
    if (r.request().method() === "OPTIONS") {
      await r.fulfill({ status: 204, headers: CORS });
      return;
    }
    // A reply that keeps running: the stream stays open.
  });
  await page.route(
    (url) => url.pathname.startsWith("/build/app/sessions/k1/") && !url.pathname.includes("/turns/"),
    async (r) => {
      const path = new URL(r.request().url()).pathname;
      if (r.request().method() === "POST") seen.posts.push({ path, body: r.request().postDataJSON() });
      if (path.endsWith("/queue")) {
        const body = r.request().postDataJSON() as { text: string };
        await json(r, { ok: true, id: "q1", queued: [{ id: "q1", text: body.text, files: [] }], queue_held: false });
        return;
      }
      if (path.endsWith("/queue/q1/remove")) {
        await json(r, { queued: [], queue_held: false });
        return;
      }
      if (path.endsWith("/events")) {
        await json(r, { events: [{ type: "user", at: "a", text: "The very first message" }], older: null, earlier: 0 });
        return;
      }
      await json(r, { ok: true, running: true, commands: COMMANDS });
    },
  );
  return seen;
}

async function noPan(page: Page): Promise<void> {
  const pan = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(pan).toBeLessThanOrEqual(0);
}

for (const size of [
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
]) {
  test(`${size.width} px — Esc stops a running reply; Enter queues; × takes it off`, async ({ page }) => {
    await page.setViewportSize(size);
    const seen = await stub(page, { running: true });
    await page.goto("/terminals?view=code&code=k1");
    const box = page.getByTestId("code-box");
    await expect(box).toBeVisible({ timeout: 90_000 });
    await expect(page.getByTestId("code-working")).toBeVisible();
    await box.fill("then add a test");
    await box.press("Enter");
    await expect(page.getByTestId("code-queued")).toContainText("then add a test");
    expect(seen.posts.some((p) => p.path.endsWith("/queue"))).toBe(true);
    expect(seen.posts.some((p) => p.path.endsWith("/steer"))).toBe(false);
    await noPan(page);
    await page.getByTestId("code-queued-remove-q1").click();
    await expect(page.getByTestId("code-queued")).toHaveCount(0);
    await box.press("Escape");
    await expect.poll(() => seen.posts.some((p) => p.path.endsWith("/interrupt"))).toBe(true);
  });

  test(`${size.width} px — Shift+Tab steps the mode and never into Full access`, async ({ page }) => {
    await page.setViewportSize(size);
    const seen = await stub(page, { running: false });
    await page.goto("/terminals?view=code&code=k1");
    const box = page.getByTestId("code-box");
    await expect(box).toBeVisible({ timeout: 90_000 });
    for (const want of ["edits", "plan", "auto", "ask", "edits"]) {
      await box.press("Shift+Tab");
      await expect.poll(() => (seen.patches.at(-1) as { mode?: string } | undefined)?.mode).toBe(want);
    }
    expect(seen.patches.some((p) => (p as { mode?: string }).mode === "full")).toBe(false);
    await expect(box).toBeFocused();
  });

  test(`${size.width} px — the / menu opens on a later line, with its hint`, async ({ page }) => {
    await page.setViewportSize(size);
    await stub(page, { running: false });
    await page.goto("/terminals?view=code&code=k1");
    const box = page.getByTestId("code-box");
    await expect(box).toBeVisible({ timeout: 90_000 });
    await box.fill("fix the login page\n/re");
    const menu = page.getByTestId("code-slash-menu");
    await expect(menu).toBeVisible();
    await expect(menu.getByTestId("code-command-hint")).toHaveText("<pr number>");
    const r = await menu.boundingBox();
    expect(r && r.x >= 0 && r.x + r.width <= size.width).toBeTruthy();
    await page.getByTestId("code-command-review").click();
    await expect(box).toHaveValue("fix the login page\n/review ");
  });

  test(`${size.width} px — N earlier steps, and Load earlier brings the first message back`, async ({ page }) => {
    await page.setViewportSize(size);
    await stub(page, {
      running: false,
      view: { events: [{ type: "text_done", at: "b", item: "m9", text: "The newest answer." }], events_total: 2, older: "s1" },
    });
    await page.goto("/terminals?view=code&code=k1");
    await expect(page.getByTestId("code-earlier-count")).toHaveText("1 earlier step", { timeout: 90_000 });
    await page.getByTestId("code-load-earlier").click();
    await expect(page.getByText("The very first message")).toBeVisible();
    await expect(page.getByTestId("code-earlier")).toHaveCount(0);
    await noPan(page);
  });
}
