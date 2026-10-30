import { expect, test, type Frame, type Page } from "@playwright/test";

/**
 * v1.410.0 — an animated background in a real browser (Edge), against the
 * scratch daemon, which downloads the REAL files from a loopback stand-in for
 * GitHub (e2e/serve-backgrounds.mjs).
 *
 *  - picking a background in Settings › Appearance downloads that one file and
 *    it shows behind the app; it is still there after a reload;
 *  - it is SEALED OFF: the frame cannot read the window's storage or DOM, and
 *    cannot reach the daemon or anything else (the CSP refuses the request);
 *  - the view is served with the no-network CSP;
 *  - Delete removes it and the plain theme comes back;
 *  - at 1280 × 800 and 390 × 844 it covers the window and nothing pans.
 *
 * Screenshots go to IJ_BACKGROUND_OUT when set.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
const OUT = process.env.IJ_BACKGROUND_OUT || "";

async function backgroundFrame(page: Page): Promise<Frame> {
  await expect(page.getByTestId("app-background")).toHaveAttribute("data-shown", "yes", { timeout: 30_000 });
  const frame = page.frames().find((f) => f.url().includes("/backgrounds/") && f.url().includes("/view"));
  expect(frame, "the background frame").toBeTruthy();
  return frame!;
}

async function makeThemeWith(page: Page, id: string) {
  await page.goto("/settings#appearance");
  await page.getByTestId("theme-maker-new").click();
  await page.getByTestId(`background-choice-${id}`).click();
  await expect(page.getByTestId(`background-choice-${id}`)).toHaveAttribute("aria-checked", "true", {
    timeout: 30_000,
  });
  await expect(page.getByTestId("theme-maker-readability")).toContainText("over the background too");
  await page.getByRole("button", { name: /Save and use this theme/ }).click();
}

for (const size of [
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
]) {
  test(`a picked background shows sealed off, survives a reload, and Delete removes it — ${size.width}px`, async ({
    page,
    request,
  }) => {
    await page.setViewportSize(size);
    // Start clean: nothing downloaded.
    for (const id of ["aurora", "waves"]) await request.delete(`${DAEMON}/backgrounds/${id}`);
    const before = await (await request.get(`${DAEMON}/backgrounds`)).json();
    expect(before.total_bytes).toBe(0);

    await makeThemeWith(page, "waves");
    const frame = await backgroundFrame(page);

    // Only that one file was downloaded.
    const after = await (await request.get(`${DAEMON}/backgrounds`)).json();
    const kept = after.backgrounds.filter((b: { state: string }) => b.state !== "absent");
    expect(kept.map((b: { id: string }) => b.id)).toEqual(["waves"]);

    // It runs, and it is sealed off.
    await expect.poll(() => frame.evaluate(() => document.documentElement.dataset.state)).toBe("running");
    const probe = await frame.evaluate(async (daemon) => {
      const out: Record<string, string> = {};
      try {
        localStorage.getItem("x");
        out.storage = "readable";
      } catch {
        out.storage = "blocked";
      }
      try {
        void window.parent.document.title;
        out.parent = "readable";
      } catch {
        out.parent = "blocked";
      }
      try {
        await fetch(`${daemon}/health`);
        out.network = "reached";
      } catch {
        out.network = "blocked";
      }
      out.origin = String(window.origin);
      return out;
    }, DAEMON);
    expect(probe).toEqual({ storage: "blocked", parent: "blocked", network: "blocked", origin: "null" });

    // The served file carries the no-network CSP with the sandbox directive.
    const view = await request.get(`${DAEMON}/backgrounds/waves/view`);
    const csp = view.headers()["content-security-policy"];
    expect(csp).toContain("default-src 'none'");
    expect(csp).toContain("sandbox allow-scripts");

    // It covers the window, takes no clicks, and nothing pans sideways.
    const box = await page.getByTestId("app-background").boundingBox();
    expect(box!.width).toBeGreaterThanOrEqual(size.width - 1);
    expect(box!.height).toBeGreaterThanOrEqual(size.height - 1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    const pe = await page.getByTestId("app-background").evaluate((el) => getComputedStyle(el).pointerEvents);
    expect(pe).toBe("none");
    if (OUT) await page.screenshot({ path: `${OUT}/background-${size.width}.png` });

    // A reload keeps it (the choice is saved with the palette; the file stays).
    await page.reload();
    await backgroundFrame(page);

    // Delete frees the space and the plain theme comes back.
    await page.goto("/settings#appearance");
    const row = page.getByTestId("downloaded-background-waves");
    await row.getByRole("button", { name: /Delete/ }).click();
    await row.getByRole("button").first().click();
    await expect(page.getByTestId("app-background")).toHaveCount(0, { timeout: 20_000 });
    const gone = await (await request.get(`${DAEMON}/backgrounds`)).json();
    expect(gone.total_bytes).toBe(0);
    await page.reload();
    await page.waitForTimeout(1500);
    await expect(page.getByTestId("app-background")).toHaveCount(0);
  });
}
