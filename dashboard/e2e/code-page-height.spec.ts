import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * v1.420.2 (a USER BUG, with two screenshots): in Chat › Code the window's own
 * scrollbar scrolled the whole Code view away into about ten screens of
 * nothing. Measured on a copy of the user's session: ONE screen-reader-only
 * label (Tailwind `sr-only` is position:absolute — a live plan's step words)
 * at the end of a long conversation made the page 9,094 px tall in a 950 px
 * window. The transcript was not a positioning box, so the label was placed
 * by the whole view: the transcript did not clip it, and it stretched the
 * page to the conversation's full length.
 *
 * Two checks, in a real browser (layout cannot be measured in jsdom):
 *  1. THE RULE: no absolutely positioned element inside a scroll box is
 *     positioned by a box outside it (offsetParent outside the scroller),
 *     anywhere on the page, in both Code views and their empty states.
 *  2. THE SYMPTOM: a long conversation ending in such a label never makes the
 *     page taller than the window, whichever way the conversation is
 *     scrolled.
 * Chat › Code runs on the scratch daemon's demo model (port 8807, a fresh
 * home); Build › Code's own routes are stubbed, as in build-app-view.spec.ts.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;

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

/** Every absolutely positioned element inside a scroll box whose positioning
 *  box is OUTSIDE that scroll box: it is neither clipped nor scrolled by it,
 *  and it adds its own place to the page's height. */
async function escapingBoxes(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    const scrollers = Array.from(document.querySelectorAll<HTMLElement>("main *")).filter((el) =>
      /(auto|scroll)/.test(getComputedStyle(el).overflowY),
    );
    for (const sc of scrollers) {
      for (const el of Array.from(sc.querySelectorAll<HTMLElement>("*"))) {
        if (getComputedStyle(el).position !== "absolute") continue;
        // Not drawn at all (inside a `hidden` view, e.g. the Chat kept behind
        // Code): it takes no room anywhere.
        if (el.getClientRects().length === 0) continue;
        const cb = el.offsetParent;
        if (cb && (cb === sc || sc.contains(cb))) continue;
        const name = (n: Element | null) =>
          n ? `${n.tagName.toLowerCase()}${n.getAttribute("data-testid") ? `[${n.getAttribute("data-testid")}]` : ""}` : "none";
        out.push(`${name(el)} "${(el.textContent || "").slice(0, 30)}" in ${name(sc)}, placed by ${name(cb)}`);
      }
    }
    return out;
  });
}

/** The page's own scroller: how far past the window it reaches. */
async function pageOverflow(page: Page): Promise<number> {
  return page.evaluate(() => {
    const main = document.querySelector("main") as HTMLElement;
    return main.scrollHeight - main.clientHeight;
  });
}

/** A long conversation ending in a screen-reader-only label: 6,000 px of
 *  rows, then one `sr-only` span, at the end of the transcript. */
async function lengthen(page: Page, transcript: string): Promise<void> {
  await page.getByTestId(transcript).evaluate((el) => {
    const tall = document.createElement("div");
    tall.style.height = "6000px";
    const label = document.createElement("span");
    label.className = "sr-only";
    label.textContent = "Done: ";
    el.appendChild(tall);
    el.appendChild(label);
  });
}

async function bothEnds(page: Page, transcript: string): Promise<number[]> {
  const out: number[] = [];
  for (const end of ["bottom", "top"] as const) {
    await page.getByTestId(transcript).evaluate((el, e) => {
      el.scrollTop = e === "top" ? 0 : el.scrollHeight;
    }, end);
    await page.waitForTimeout(150);
    out.push(await pageOverflow(page));
  }
  return out;
}

for (const size of [
  { width: 1913, height: 990 },
  { width: 1280, height: 800 },
]) {
  test.describe(`${size.width} × ${size.height}`, () => {
    test.use({ viewport: size });

    test("Chat › Code: the page never scrolls past the view, empty or with a long conversation", async ({ page }) => {
      const work = mkdtempSync(join(tmpdir(), "ij-page-height-"));
      try {
        await page.goto("/chat");
        await page.getByTestId("chat-view-code").click({ timeout: 90_000 });
        await expect(page.getByTestId("ij-harness-view")).toBeVisible({ timeout: 60_000 });
        expect(await escapingBoxes(page)).toEqual([]);
        expect(await pageOverflow(page)).toBeLessThanOrEqual(1);

        // A session on the demo model (the start composer: folder, words, Enter).
        await page.getByTestId("harness-new-session").click();
        const box = page.getByTestId("harness-new-box");
        await expect(box).toBeFocused({ timeout: 30_000 });
        await page.getByTestId("harness-new-folder-choose").click();
        const typed = page.getByTestId("harness-new-folder-tree").getByLabel("Go to root path");
        await typed.fill(work);
        await typed.press("Enter");
        await page.getByTestId("harness-new-folder-tree-use").click();
        await box.fill("Write a hello file");
        await box.press("Enter");
        await expect(page.getByTestId("harness-reply").last()).toContainText("saved RESULT.md", { timeout: 90_000 });

        expect(await escapingBoxes(page)).toEqual([]);
        await lengthen(page, "harness-transcript");
        // The rule again, now that the transcript holds an absolute label.
        expect(await escapingBoxes(page)).toEqual([]);
        expect(await bothEnds(page, "harness-transcript")).toEqual([0, 0]);
        // Every side pane too.
        for (const tab of ["plan", "timeline", "changes", "session", "files"]) {
          const t = page.getByTestId(`harness-pane-tab-${tab}`);
          if (!(await t.count())) continue;
          await t.click();
          await page.waitForTimeout(400);
          expect({ tab, escaping: await escapingBoxes(page) }).toEqual({ tab, escaping: [] });
          expect({ tab, overflow: await pageOverflow(page) }).toEqual({ tab, overflow: 0 });
        }
      } finally {
        try {
          rmSync(work, { recursive: true, force: true });
        } catch {
          /* a shell may still hold the folder for a moment; it is the OS's temp */
        }
      }
    });

    test("Build › Code: the page never scrolls past the view with a long conversation", async ({ page }) => {
      const session = {
        id: "h1",
        harness: "claude-code",
        folder: "C:\\work\\shop",
        title: "Fix the off-by-one in cart.py",
        account: "",
        model: "",
        effort: "",
        mode: "ask",
        harness_session_id: "5f0c2a1e",
        created_at: "2026-10-29T10:00:00Z",
        updated_at: "2026-10-29T10:00:00Z",
        archived_at: null,
        last_error: "",
      };
      await page.route(`${DAEMON}/build/app/harnesses`, (r) =>
        json(r, {
          harnesses: [
            {
              id: "claude-code",
              label: "Claude Code",
              installed: true,
              signed_in: true,
              modes: [{ id: "ask", label: "Ask first", dangerous: false }],
              hidden_modes: [],
              models: [{ id: "", label: "Claude Code's default" }],
              efforts: [],
              accounts: [],
            },
          ],
        }),
      );
      await page.route(
        (url) => url.href.startsWith(`${DAEMON}/build/app/sessions`) && !url.pathname.includes("/sessions/"),
        (r) => json(r, { sessions: [session] }),
      );
      await page.route(`${DAEMON}/build/app/sessions/h1`, (r) =>
        json(r, {
          session,
          events: [
            { type: "user", at: "2026-10-29T10:00:01Z", text: "Fix the off-by-one in cart.py" },
            { type: "tool", at: "2026-10-29T10:00:03Z", item: "tu1", kind: "read", name: "Read", title: "Read cart.py", status: "done", output: "def total(items): ..." },
            { type: "done", stop: "completed" },
          ],
          running: null,
          turn_id: null,
          pending: [],
          commands: [],
        }),
      );
      await page.goto("/terminals?view=code&code=h1");
      await expect(page.getByTestId("code-transcript")).toBeVisible({ timeout: 90_000 });
      expect(await escapingBoxes(page)).toEqual([]);
      await lengthen(page, "code-transcript");
      expect(await escapingBoxes(page)).toEqual([]);
      expect(await bothEnds(page, "code-transcript")).toEqual([0, 0]);
    });
  });
}
