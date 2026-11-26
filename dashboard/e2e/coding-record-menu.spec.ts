import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * Next leaps 28-a (R2.17) — the record for this folder, in the model menu, in
 * a real browser at desktop width and on a 390 × 844 phone.
 *
 * The scratch daemon's OWN record (`GET /build/scoreboard`, the real route)
 * is seeded with pieces of work straight into its fresh database (the
 * record's own `scoreboard.note`), so the line under each model is the
 * daemon's own words. Only what would need a real coding tool is stubbed in
 * the browser: Build › Code's tools and its one session (`/build/app/…`), and
 * the model list (`/models`) so the menu's rows are the ones on record.
 *
 * At each size: the line is under its model's name, inside the menu's panel
 * and the window, overlaps nothing in its row, nothing pans sideways, and a
 * model with nothing on record has no line. `IJ_RECORD_SHOTS=<folder>` also
 * saves a screenshot of each open menu.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
const HERE = __dirname;
const DB = join(HERE, ".home", ".ironjarvis", "ironjarvis.db");
const SHOTS = process.env.IJ_RECORD_SHOTS || "";

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "*",
  "access-control-allow-methods": "GET, POST, PATCH, PUT, DELETE, OPTIONS",
};

async function json(route: Route, body: unknown): Promise<void> {
  if (route.request().method() === "OPTIONS") {
    await route.fulfill({ status: 204, headers: CORS });
    return;
  }
  await route.fulfill({ status: 200, headers: { ...CORS, "content-type": "application/json" }, body: JSON.stringify(body) });
}

/** `[verdict, checked]` for each piece: kept, thrown, then not decided. */
function pieces(kept: number, thrown: number, undecided: number, passed: number, failed: number): [string, string][] {
  const verdicts = [...Array(kept).fill("kept"), ...Array(thrown).fill("thrown"), ...Array(undecided).fill("")];
  const checks = [...Array(passed).fill("passed"), ...Array(failed).fill("failed")];
  return verdicts.map((v, i) => [v, checks[i] ?? ""]);
}

const SEED_PY = `
import json, sys
from sqlmodel import create_engine
from iron_jarvis.harness import scoreboard as SB
db, groups = sys.argv[1], json.loads(sys.argv[2])
eng = create_engine("sqlite:///" + db.replace("\\\\", "/"))
SB.CodingOutcome.__table__.create(bind=eng, checkfirst=True)
n = 0
for g in groups:
    for verdict, checked in g["pieces"]:
        n += 1
        sid = g["tag"] + str(n)
        # (once per ref: a worker that starts again seeds nothing twice)
        SB.note(eng, session_id=sid, ref=sid + ":t1", folder=g["folder"], surface=g["surface"],
                tool=g["tool"], provider=g.get("provider", ""), model=g.get("model", ""), checked=checked,
                verdict=verdict, how={"kept": "committed", "thrown": "discarded"}.get(verdict, ""))
print(n)
`;

function seed(groups: unknown[]): void {
  execFileSync("uv", ["run", "--no-sync", "python", "-c", SEED_PY, DB, JSON.stringify(groups)], {
    cwd: resolve(HERE, "..", ".."),
    stdio: "pipe",
  });
}

const LONG_LINE = "In this folder: kept 7 of 9, passed its checks 8 of 9; 3 not decided yet";
const SHORT_LINE = "In this folder: kept 3 of 4 (too few to judge)";
const ONE_LINE = "In this folder: kept 1 of 1 (too few to judge)";

/* --------------------------------------------------------------- Build › Code */

const BC_FOLDER = "C:\\Users\\me\\Projects\\a-folder-with-a-rather-long-name\\shop";
const TOOLS = {
  harnesses: [
    {
      id: "codex",
      label: "Codex",
      installed: true,
      signed_in: true,
      modes: [{ id: "ask", label: "Ask first", dangerous: false }],
      hidden_modes: [],
      models: [
        { id: "", label: "Codex's default" },
        { id: "gpt-6-astra", label: "GPT-6 Astra with a rather long model name for a phone" },
        { id: "gpt-6-mini", label: "GPT-6 Mini" },
      ],
      efforts: ["low", "medium", "high"],
      accounts: [],
    },
  ],
};
const SESSION = {
  id: "app_record",
  harness: "codex",
  folder: BC_FOLDER,
  title: "Fix the off-by-one in the cart and make every test pass again",
  account: "",
  model: "gpt-6-astra",
  effort: "",
  mode: "ask",
  harness_session_id: "",
  created_at: "2026-11-26T09:00:00Z",
  updated_at: "2026-11-26T09:00:00Z",
  archived_at: null,
  last_error: "",
  capabilities: { files: false, shell: false, browser: false, extensions: false, memory: false },
};

async function stubBuildCode(page: Page): Promise<void> {
  await page.route(`${DAEMON}/build/app/harnesses`, (r) => json(r, TOOLS));
  await page.route(
    (url) => url.href.startsWith(`${DAEMON}/build/app/sessions`),
    (r) => {
      const path = new URL(r.request().url()).pathname;
      if (path === "/build/app/sessions") return json(r, { sessions: [SESSION] });
      if (path.endsWith("/commands")) return json(r, { commands: [] });
      if (path.endsWith("/changes")) return json(r, { source: "git", changes: [], files: 0, by_tool: 0 });
      return json(r, { session: SESSION, events: [], running: null, pending: [] });
    },
  );
}

/* --------------------------------------------------------------- Chat › Code */

const MODELS = {
  models: [
    { provider: "anthropic", model: "claude-haiku-4-5", kind: "api", available: true, inherited_from: null },
    {
      provider: "anthropic",
      model: "claude-opus-4-8",
      kind: "api",
      available: true,
      inherited_from: null,
      label: "Opus 4.8 with a long name",
    },
    { provider: "anthropic", model: "claude-sonnet-4-6", kind: "api", available: true, inherited_from: null },
  ],
};

let chatFolder = "";
let chatSession = "";

test.beforeAll(async ({ request }) => {
  chatFolder = mkdtempSync(join(tmpdir(), "ij-record-"));
  const made = await request.post(`${DAEMON}/build/sessions`, {
    data: { folder: chatFolder, branch: false, provider: "anthropic", model: "claude-haiku-4-5" },
  });
  expect(made.status()).toBe(201);
  chatSession = ((await made.json()) as { id: string }).id;
  seed([
    { tag: "bcd", folder: BC_FOLDER, surface: "tool", tool: "codex", model: "", pieces: pieces(7, 2, 3, 8, 1) },
    { tag: "bca", folder: BC_FOLDER, surface: "tool", tool: "codex", model: "gpt-6-astra", pieces: pieces(3, 1, 0, 0, 0) },
    {
      tag: "cch",
      folder: chatFolder,
      surface: "code",
      tool: "jarvis",
      provider: "anthropic",
      model: "claude-haiku-4-5",
      pieces: pieces(7, 2, 3, 8, 1),
    },
    {
      tag: "cco",
      folder: chatFolder,
      surface: "code",
      tool: "jarvis",
      provider: "anthropic",
      model: "claude-opus-4-8",
      pieces: pieces(1, 0, 0, 0, 0),
    },
  ]);
  // The daemon's own route answers the seeded record.
  const rec = await request.get(`${DAEMON}/build/scoreboard?folder=${encodeURIComponent(BC_FOLDER)}`);
  const body = (await rec.json()) as { rows: { model: string; words: string }[] };
  expect(body.rows.map((r) => [r.model, r.words])).toEqual([
    ["", LONG_LINE],
    ["gpt-6-astra", SHORT_LINE],
  ]);
});

/* ------------------------------------------------------------------ checks */

/** How far anything laid out reaches past the window's right edge. */
async function widest(page: Page): Promise<number> {
  return page.evaluate(() => {
    let worst = 0;
    const w = window.innerWidth;
    for (const el of Array.from(document.querySelectorAll<HTMLElement>("body *"))) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (getComputedStyle(el).position === "fixed") continue;
      worst = Math.max(worst, r.right - w);
    }
    return Math.max(worst, document.documentElement.scrollWidth - w);
  });
}

/** The record's line against its row, its panel and the window: every box
 *  the line is drawn in, and whether it overlaps anything else in its row. */
async function placed(page: Page, lineSel: string, panelSel: string) {
  return page.evaluate(
    ([ls, ps]) => {
      const line = document.querySelector(ls) as HTMLElement | null;
      const panel = document.querySelector(ps) as HTMLElement | null;
      if (!line || !panel) return null;
      const r = line.getBoundingClientRect();
      const p = panel.getBoundingClientRect();
      const row = line.closest("button") as HTMLElement | null;
      const overlaps: string[] = [];
      if (row) {
        for (const other of Array.from(row.querySelectorAll<HTMLElement>("*"))) {
          if (other === line || other.contains(line) || line.contains(other)) continue;
          if (other.children.length > 0) continue;
          const o = other.getBoundingClientRect();
          if (o.width === 0 || o.height === 0) continue;
          const x = Math.min(r.right, o.right) - Math.max(r.left, o.left);
          const y = Math.min(r.bottom, o.bottom) - Math.max(r.top, o.top);
          if (x > 0.5 && y > 0.5) overlaps.push(`${other.tagName}:${(other.textContent || "").slice(0, 30)}`);
        }
      }
      const name = line.previousElementSibling?.getBoundingClientRect() ?? null;
      return {
        inWindow: r.left >= -0.5 && r.right <= window.innerWidth + 0.5,
        inPanel: r.left >= p.left - 0.5 && r.right <= p.right + 0.5,
        underName: name ? r.top >= name.bottom - 0.5 : false,
        // The words wrap inside the row, never cut off sideways.
        clipped: line.scrollWidth > line.clientWidth + 1,
        lines: Math.round(r.height / parseFloat(getComputedStyle(line).lineHeight || "16")),
        overlaps,
        text: line.textContent,
      };
    },
    [lineSel, panelSel] as const,
  );
}

/** The page's own errors and console errors, as they come. */
function said(page: Page): string[] {
  const out: string[] = [];
  page.on("pageerror", (e) => out.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") out.push(`console: ${m.text().slice(0, 300)}`);
  });
  return out;
}

async function shot(page: Page, name: string): Promise<void> {
  if (SHOTS) await page.screenshot({ path: join(SHOTS, `${name}.png`) });
}

for (const size of [
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
]) {
  test(`${size.width} px — Build › Code's picker: the folder's record under each model, inside the window`, async ({
    page,
  }) => {
    await page.setViewportSize(size);
    const problems = said(page);
    await stubBuildCode(page);
    await page.goto(`/terminals?view=code&code=${SESSION.id}`);
    const chip = page.getByTestId("code-model-chip");
    await expect(chip).toBeVisible({ timeout: 90_000 });
    await chip.scrollIntoViewIfNeeded();
    const panel = page.getByTestId("code-pick-tool-menu");
    for (let i = 0; i < 5 && (await panel.count()) === 0; i++) {
      await chip.click();
      await panel.waitFor({ state: "visible", timeout: 4_000 }).catch(() => {});
    }
    await expect(panel).toBeVisible();
    const astra = page.getByTestId("code-pick-model-record-gpt-6-astra");
    await expect(astra).toHaveText(SHORT_LINE, { timeout: 30_000 });
    await expect(page.getByTestId("code-pick-model-record-default")).toHaveText(LONG_LINE);
    await expect(page.getByTestId("code-pick-model-record-gpt-6-mini")).toHaveCount(0);
    await page.waitForTimeout(300);
    for (const id of ["default", "gpt-6-astra"]) {
      const got = await placed(page, `[data-testid="code-pick-model-record-${id}"]`, '[data-testid="code-pick-tool-menu"]');
      expect(got, id).not.toBeNull();
      expect.soft(got!.inWindow, `${id}: inside the window`).toBe(true);
      expect.soft(got!.inPanel, `${id}: inside the panel`).toBe(true);
      expect.soft(got!.underName, `${id}: under the model's name`).toBe(true);
      expect.soft(got!.clipped, `${id}: wraps, never cut off`).toBe(false);
      expect.soft(got!.overlaps, `${id}: overlaps nothing in its row`).toEqual([]);
    }
    expect(await widest(page)).toBeLessThanOrEqual(1);
    await shot(page, `build-code-picker-${size.width}`);
    // Nothing the record draws raises a page error (the stubs' own gaps may).
    expect.soft(problems.filter((p) => /record|scoreboard/i.test(p))).toEqual([]);
    if (problems.length) console.log(`build-code-picker-${size.width} page problems: ${problems.join(" | ")}`);
  });

  test(`${size.width} px — Chat › Code's model menu: the folder's record under the model, inside the window`, async ({
    page,
  }) => {
    await page.setViewportSize(size);
    const problems = said(page);
    await page.route(`${DAEMON}/models`, (r) => json(r, MODELS));
    await page.goto(`/chat?view=code&session=${chatSession}`);
    await expect(page.getByTestId("harness-session")).toBeVisible({ timeout: 90_000 });
    // The session's own chip (the chat page's composer, hidden behind it, has one too).
    const chip = page.getByTestId("harness-composer-toolbar").getByTitle("Switch model");
    await expect(chip).toBeVisible({ timeout: 90_000 });
    await chip.scrollIntoViewIfNeeded();
    const menu = page.getByTestId("model-menu");
    for (let i = 0; i < 5 && (await menu.count()) === 0; i++) {
      await chip.click();
      await menu.waitFor({ state: "visible", timeout: 4_000 }).catch(() => {});
    }
    await expect(menu).toBeVisible();
    await menu.getByRole("button", { name: /^Anthropic/ }).click();
    const sub = page.getByTestId("model-sub");
    await expect(sub).toBeVisible();
    const lines = sub.getByTestId("model-record");
    await expect(lines).toHaveCount(2, { timeout: 30_000 });
    await expect(lines.nth(0)).toHaveText(LONG_LINE);
    await expect(lines.nth(1)).toHaveText(ONE_LINE);
    await page.waitForTimeout(300);
    // Each line gets a name of its own to be measured by (the test's, not the page's).
    const marked = await page.evaluate(() => {
      const all = Array.from(document.querySelectorAll('[data-testid="model-sub"] [data-testid="model-record"]'));
      all.forEach((el, j) => el.setAttribute("data-record-n", String(j)));
      return all.length;
    });
    expect(marked).toBe(2);
    for (const n of [0, 1]) {
      const at = await placed(page, `[data-record-n="${n}"]`, '[data-testid="model-sub"]');
      expect(at, `line ${n}`).not.toBeNull();
      expect.soft(at!.inWindow, `line ${n}: inside the window`).toBe(true);
      expect.soft(at!.inPanel, `line ${n}: inside the panel`).toBe(true);
      expect.soft(at!.underName, `line ${n}: under the model's name`).toBe(true);
      expect.soft(at!.clipped, `line ${n}: wraps, never cut off`).toBe(false);
      expect.soft(at!.overlaps, `line ${n}: overlaps nothing in its row`).toEqual([]);
    }
    expect(await widest(page)).toBeLessThanOrEqual(1);
    await shot(page, `chat-code-menu-${size.width}`);
    expect.soft(problems.filter((p) => /record|scoreboard/i.test(p))).toEqual([]);
    if (problems.length) console.log(`chat-code-menu-${size.width} page problems: ${problems.join(" | ")}`);
  });
}
