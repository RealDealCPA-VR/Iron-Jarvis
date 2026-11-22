import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type APIRequestContext, type Page, type Route } from "@playwright/test";

/**
 * v1.550.0 — the Timeline tab's GRAPH of the models, in a real browser against
 * the scratch daemon (a real session on a real folder). The session's saved
 * reply is STUBBED in the browser: a lead that handed two pieces out at once
 * (one on a cloud model, one on a local one), took both reports up, then
 * handed a third piece out and took that report up too.
 *
 *  - the tab opens on the graph: four model nodes and the lead's two returns,
 *    every node inside the pane, the page never pans sideways — at 1440 × 900
 *    and 390 × 844;
 *  - pointer on a node shows its numbers; on an edge, the task handed over;
 *  - a press on a helper opens only its steps; "All models" brings the graph
 *    back.
 *
 * Screenshots go to IJ_GRAPH_OUT when set.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
const OUT = process.env.IJ_GRAPH_OUT || "";
const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "*",
  "access-control-allow-methods": "GET, POST, PATCH, PUT, DELETE, OPTIONS",
};

let work = "";

test.beforeAll(() => {
  work = mkdtempSync(join(tmpdir(), "ij-timeline-graph-"));
  writeFileSync(join(work, "app.py"), "a = 1\n");
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

const usage = (i: number, o: number, c: number) => ({ input_tokens: i, output_tokens: o, cost_usd: c });

function reply(now: number): Record<string, unknown> {
  const call = (start: number, ms: number) => ({ start, ms, provider: "anthropic", model: "claude-opus-4-1", usage: usage(9000, 600, 0.04) });
  return {
    role: "assistant",
    content: "Done: the cart keeps its tax, the tests pass.",
    at: new Date(now).toISOString(),
    turnId: "t-graph",
    route: { provider: "anthropic", model: "claude-opus-4-1" },
    usage: usage(45_000, 3_000, 0.2),
    timing: { startedAt: now - 900_000, firstTokenAt: now - 899_000, endedAt: now, ms: 900_000 },
    calls: [call(0, 9_000), call(9_500, 8_000), call(400_000, 12_000), call(560_000, 9_000), call(880_000, 20_000)],
    steps: [
      { name: "read_file", ok: true, start: 18_000, ms: 40, target: "app.py" },
      { name: "delegate", ok: true, start: 20_000, ms: 370_000, helper: true },
      { name: "delegate", ok: true, start: 20_100, ms: 375_000, helper: true },
      { name: "delegate", ok: true, start: 420_000, ms: 450_000, helper: true },
    ],
    helpers: [
      {
        id: "h-cloud",
        session: "s-a",
        agent: "code-worker",
        task: "Make cart.total() keep the tax on discounted items. Only touch src/cart.py.",
        result: "cart.total() now applies the tax after the discount; src/cart.py changed (12 lines).",
        state: "done",
        start: 20_050,
        end: 390_000,
        provider: "openai",
        model: "gpt-5",
        reason: "cloud: good record for coding",
        usage: usage(30_000, 2_000, 0.11),
        steps: 6,
        work: [
          { name: "read_file", ok: true, start: 21_000, ms: 30, target: "src/cart.py" },
          { name: "edit_file", ok: true, start: 200_000, ms: 50, target: "src/cart.py" },
        ],
      },
      {
        id: "h-local",
        session: "s-b",
        agent: "test-worker",
        task: "Write a test that a 10% discount on a taxed item keeps the tax.",
        result: "tests/test_cart.py: one new test, passes.",
        state: "done",
        start: 20_150,
        end: 395_000,
        provider: "ollama",
        model: "glm-4.6",
        usage: usage(22_000, 1_500, 0),
        steps: 4,
        work: [{ name: "write_file", ok: true, start: 100_000, ms: 20, target: "tests/test_cart.py" }],
      },
      {
        id: "h-review",
        session: "s-c",
        agent: "reviewer",
        task: "Review the change to src/cart.py and its new test.",
        result: "Looks right. VERDICT: approve",
        state: "done",
        start: 420_050,
        end: 870_000,
        provider: "openai",
        model: "gpt-5",
        usage: usage(12_000, 800, 0.05),
        steps: 2,
        work: [{ name: "read_file", ok: true, start: 421_000, ms: 30, target: "src/cart.py" }],
      },
    ],
  };
}

async function open(page: Page, request: APIRequestContext): Promise<void> {
  const a = await request.post(`${DAEMON}/build/sessions`, { data: { folder: work, branch: false } });
  expect(a.ok(), await a.text()).toBe(true);
  const view = (await a.json()) as Record<string, unknown> & { id: string; setup?: Record<string, unknown> };
  const now = Date.now();
  const withMessages = {
    ...view,
    setup: { ...(view.setup ?? {}), provider: "anthropic", model: "claude-opus-4-1", reasoning: "high" },
    messages: [{ role: "user", content: "Keep the tax on discounted items", at: new Date(now - 900_000).toISOString(), turnId: "t-graph" }, reply(now)],
  };
  await page.route(`${DAEMON}/build/sessions/${view.id}`, (route) =>
    route.request().method() === "GET" ? stub(route, withMessages) : route.fallback(),
  );
  await page.goto(`/terminals?session=${view.id}`);
  await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
  await page.getByTestId("harness-pane-tab-timeline").click();
  await expect(page.getByTestId("timeline-graph")).toBeVisible();
}

async function pan(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

/** Every node that reaches past the graph's own box (sideways). */
async function outside(page: Page): Promise<string[]> {
  return page.getByTestId("timeline-graph").evaluate((root) => {
    const box = root.getBoundingClientRect();
    const bad: string[] = [];
    if (root.scrollWidth > root.clientWidth + 1) bad.push(`the graph scrolls sideways (${root.scrollWidth} > ${root.clientWidth})`);
    for (const el of Array.from(root.querySelectorAll('[data-testid="graph-node"]'))) {
      const r = el.getBoundingClientRect();
      if (r.right > box.right + 1 || r.left < box.left - 1) bad.push(`${el.getAttribute("data-key")} outside`);
    }
    return bad;
  });
}

for (const size of [
  { name: "wide", width: 1440, height: 900 },
  { name: "phone", width: 390, height: 844 },
]) {
  test(`the graph of the models (${size.name})`, async ({ page, request }) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    await open(page, request);
    const nodes = page.getByTestId("graph-node");
    await expect(nodes).toHaveCount(6);
    const keys = await nodes.evaluateAll((els) => els.map((e) => e.getAttribute("data-key")));
    expect(keys).toEqual(["lead", "helper:h-cloud", "helper:h-local", "back:0", "helper:h-review", "back:1"]);
    await expect(page.getByTestId("graph-edge")).toHaveCount(6);
    // The two helpers that ran together sit side by side, even in the panel
    // at its narrowest.
    const cloud = (await nodes.nth(1).boundingBox())!;
    const local = (await nodes.nth(2).boundingBox())!;
    expect(Math.abs(cloud.y - local.y)).toBeLessThanOrEqual(1);
    expect(cloud.x + cloud.width).toBeLessThan(local.x);
    expect(await outside(page)).toEqual([]);
    expect(await pan(page)).toBeLessThanOrEqual(0);
    await shot(page, `graph-${size.name}`);

    // Pointer on a node: its numbers.
    await nodes.nth(2).hover();
    await expect(page.getByTestId("graph-card")).toContainText("Started");
    await shot(page, `graph-${size.name}-node-card`);

    // Pointer on an edge: the task handed over.
    await page.getByTestId("graph-edge-hit").first().focus();
    await expect(page.getByTestId("graph-edge-words")).toContainText("Make cart.total()");
    await shot(page, `graph-${size.name}-edge-card`);

    // A press on a helper: only its steps.
    await nodes.nth(1).click();
    await expect(page.getByTestId("timeline-focus")).toBeVisible();
    const kinds = await page.getByTestId("timeline-row").evaluateAll((els) => els.map((e) => e.getAttribute("data-kind")));
    expect(kinds).toEqual(["helper", "step", "step"]);
    await shot(page, `graph-${size.name}-helper-steps`);
    await page.getByTestId("timeline-focus-back").click();
    await expect(page.getByTestId("timeline-graph")).toBeVisible();
  });
}
