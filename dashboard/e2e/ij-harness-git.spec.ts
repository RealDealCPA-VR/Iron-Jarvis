import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";

/**
 * IJ harness part 2, Phase 4 (v1.375.0) — a Build session on its own git
 * branch, in a real browser against the scratch daemon (port 8807, a FRESH
 * home whose model is the demo model), with REAL git: a scratch repository
 * and a local bare remote in the OS's temp folder (never a real remote).
 *
 *  1. New session in the repository: the switch "Work on a new branch" shows
 *     (on); Start makes `ij/<name>` in `<repo>.ij-worktrees/<name>`; the
 *     header names the branch and its base; the main checkout is untouched;
 *  2. the demo model's scripted step writes RESULT.md — in the WORKTREE;
 *  3. Changes → Branch: the line and the file; Commit (the demo model drafts
 *     nothing and says so; the user's own words are committed); Push (the
 *     bare remote gets the branch); Merge into main (two presses; the main
 *     checkout gets RESULT.md); Remove the worktree (two presses: the folder
 *     is gone, the branch stays, the session is archived);
 *  4. 390 px: the Branch view never pans sideways.
 */

test.describe.configure({ mode: "serial" });

let root = "";
let repo = "";
let bare = "";

function git(args: string[], cwd: string): string {
  return execFileSync("git", args, { cwd, encoding: "utf-8", env: { ...process.env, GIT_TERMINAL_PROMPT: "0" } }).trim();
}

test.beforeAll(() => {
  root = mkdtempSync(join(tmpdir(), "ij-git-e2e-"));
  repo = join(root, "shop");
  bare = join(root, "remote.git");
  execFileSync("git", ["init", "-q", "-b", "main", repo]);
  git(["config", "user.email", "dev@example.com"], repo);
  git(["config", "user.name", "Dev"], repo);
  git(["config", "core.autocrlf", "false"], repo);
  writeFileSync(join(repo, "app.py"), "x = 1\n");
  git(["add", "-A"], repo);
  git(["commit", "-q", "-m", "first"], repo);
  execFileSync("git", ["init", "-q", "--bare", "-b", "main", bare]);
  git(["remote", "add", "origin", bare], repo);
  git(["push", "-q", "origin", "main"], repo);
});

test.afterAll(() => {
  try {
    if (root) rmSync(root, { recursive: true, force: true });
  } catch {
    /* the OS clears its temp folder */
  }
});

async function openHarness(page: Page) {
  await page.goto("/chat"); // v1.406.0: Chat › Code
  const tab = page.getByTestId("chat-view-code");
  await expect(tab).toBeVisible({ timeout: 90_000 });
  await tab.click();
  await expect(page.getByTestId("ij-harness-view")).toBeVisible({ timeout: 60_000 });
}

async function noSidewaysPan(page: Page): Promise<void> {
  const pan = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(pan).toBeLessThanOrEqual(0);
}

test("a session on its own branch: commit, push, merge, remove the worktree", async ({ page }) => {
  const mainBefore = git(["rev-parse", "main"], repo);
  await openHarness(page);
  await page.getByTestId("harness-new-session").click();
  await page.getByTestId("harness-new-folder").fill(repo);
  const sw = page.getByTestId("harness-new-branch-switch");
  await expect(sw).toBeVisible({ timeout: 20_000 });
  await expect(sw).toBeChecked();
  await page.getByTestId("harness-new-start").click();
  await expect(page.getByTestId("harness-session")).toBeVisible();
  const branchLine = page.getByTestId("harness-branch");
  await expect(branchLine).toContainText(/ij\/session-[0-9a-f]{4} from main/);
  const branch = ((await branchLine.textContent()) ?? "").match(/ij\/session-[0-9a-f]{4}/)?.[0] ?? "";
  const wt = join(`${repo}.ij-worktrees`, branch.slice(3));
  expect(existsSync(join(wt, "app.py"))).toBe(true);
  // The main checkout: same branch, same commit, nothing changed.
  expect(git(["branch", "--show-current"], repo)).toBe("main");
  expect(git(["status", "--porcelain"], repo)).toBe("");

  // The demo model writes RESULT.md — in the worktree, never the checkout.
  const box = page.getByTestId("harness-input");
  await box.fill("Write a hello file");
  await box.press("Enter");
  await expect(page.getByTestId("harness-reply").last()).toContainText("saved RESULT.md", { timeout: 90_000 });
  expect(existsSync(join(wt, "RESULT.md"))).toBe(true);
  expect(existsSync(join(repo, "RESULT.md"))).toBe(false);

  // Changes → Branch.
  await page.getByTestId("harness-pane-tab-changes").click();
  await page.getByTestId("changes-scope-branch").click();
  await expect(page.getByTestId("branch-status")).toContainText(`0 commits on ${branch} · 1 file not committed`);
  await expect(page.getByTestId("pane-branch").getByText("RESULT.md")).toBeVisible();

  // Commit: the demo model drafts nothing and says so; the user's words go in.
  await page.getByTestId("branch-commit").click();
  await expect(page.getByTestId("branch-draft-note")).toContainText("demo model");
  await page.getByTestId("branch-commit-message").fill("Add the result file");
  await page.getByTestId("branch-commit-go").click();
  await expect(page.getByTestId("branch-note")).toContainText("Committed");
  expect(git(["log", "-1", "--format=%s", branch], repo)).toBe("Add the result file");

  // Push: the bare remote has the branch.
  await expect(page.getByTestId("branch-push")).toBeEnabled({ timeout: 20_000 });
  await page.getByTestId("branch-push").click();
  await expect(page.getByTestId("branch-note")).toContainText(`Pushed ${branch} to origin.`);
  expect(git(["rev-parse", `refs/heads/${branch}`], bare)).toBe(git(["rev-parse", branch], repo));

  // 390 px while the Branch view shows: nothing pans sideways.
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByTestId("pane-branch")).toBeVisible();
  await noSidewaysPan(page);
  if (process.env.IJ_E2E_SHOTS) await page.screenshot({ path: join(process.env.IJ_E2E_SHOTS, "ij-git-390.png"), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });

  // Merge into main: two presses; the main checkout gets the file.
  await page.getByRole("button", { name: "Merge into main" }).click();
  expect(git(["rev-parse", "main"], repo)).toBe(mainBefore);
  await page.getByRole("button", { name: "Confirm merge" }).click();
  await expect(page.getByTestId("branch-note")).toContainText("Merged into main");
  expect(readFileSync(join(repo, "RESULT.md"), "utf-8").length).toBeGreaterThan(0);
  expect(git(["branch", "--show-current"], repo)).toBe("main");
  if (process.env.IJ_E2E_SHOTS) await page.screenshot({ path: join(process.env.IJ_E2E_SHOTS, "ij-git-merged.png") });

  // Remove the worktree: two presses; the folder is gone, the branch stays.
  const offer = page.getByTestId("branch-merged");
  await offer.getByRole("button", { name: "Remove the worktree" }).click();
  await offer.getByRole("button", { name: "Confirm remove" }).click();
  await expect(page.getByTestId("harness-no-session")).toBeVisible({ timeout: 30_000 });
  await expect.poll(() => existsSync(wt), { timeout: 20_000 }).toBe(false);
  expect(git(["rev-parse", "--verify", `refs/heads/${branch}`], repo)).not.toBe("");
});
