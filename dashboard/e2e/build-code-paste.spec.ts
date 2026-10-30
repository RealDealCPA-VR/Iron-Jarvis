import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * v1.413.0 — a pasted picture, in a real browser: Build › Code (the start
 * composer, then the session it makes) and Chat › Code (the composer-first
 * session). The user: "I cant copy and past images into the build modules
 * </> code. I suspect it is the same with the native harness."
 *
 * The paste is a REAL ClipboardEvent carrying a PNG (what Edge hands the page
 * for a screenshot); the upload goes to the scratch daemon's real
 * `POST /documents/upload`, and the thumbnail is read back from it. Build ›
 * Code's own routes (`/build/app/…`) are STUBBED (no Claude Code, Codex or Pi
 * is started); Chat › Code uses a real session of the scratch daemon. At
 * 1280 × 800 and on a 390 × 844 phone: the thumbnail shows (its picture
 * really loaded), sits inside the window, nothing pans, and the picture's path
 * rides the message.
 */

const DAEMON = `http://127.0.0.1:${process.env.IJ_E2E_DAEMON_PORT || 8807}`;
/** A 2 × 2 red PNG. */
const PNG_B64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP8z8DAwMDAxMDAwMDAAAANGgEB1jKHYgAAAABJRU5ErkJggg==";

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

const TOOLS = {
  harnesses: [
    {
      id: "claude-code",
      label: "Claude Code",
      installed: true,
      signed_in: true,
      modes: [
        { id: "ask", label: "Ask first", dangerous: false, about: "Asks before it changes a file or runs a command." },
        { id: "full", label: "Full access", dangerous: true, about: "Runs everything without asking." },
      ],
      hidden_modes: [],
      models: [{ id: "", label: "Claude Code's default" }],
      efforts: [],
      accounts: [],
    },
  ],
};

const SESSION = {
  id: "n1",
  harness: "claude-code",
  folder: "",
  no_folder: true,
  title: "",
  account: "",
  model: "",
  effort: "",
  mode: "ask",
  harness_session_id: "",
  created_at: "2026-10-30T09:00:00Z",
  updated_at: "2026-10-30T09:00:00Z",
  archived_at: null,
  last_error: "",
};

/** Paste the PNG into the element, as the browser hands a pasted screenshot. */
async function pastePicture(page: Page, testId: string): Promise<void> {
  await page.evaluate(
    ({ b64, id }) => {
      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
      const dt = new DataTransfer();
      dt.items.add(new File([bytes], "image.png", { type: "image/png" }));
      const el = document.querySelector(`[data-testid="${id}"]`) as HTMLElement;
      el.focus();
      el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
    },
    { b64: PNG_B64, id: testId },
  );
}

/** The thumbnail: shown, its picture loaded, inside the window. */
async function thumbOk(page: Page, sel: string): Promise<void> {
  const thumb = page.locator(sel).first();
  await expect(thumb).toBeVisible({ timeout: 30_000 });
  await expect.poll(() => thumb.evaluate((img) => (img as HTMLImageElement).naturalWidth), { timeout: 15_000 }).toBeGreaterThan(0);
  const box = await thumb.boundingBox();
  const width = page.viewportSize()!.width;
  expect(box && box.x >= 0 && box.x + box.width <= width).toBeTruthy();
  const pan = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(pan).toBeLessThanOrEqual(0);
}

for (const size of [
  { width: 1280, height: 800 },
  { width: 390, height: 844 },
]) {
  test(`${size.width} px — Build › Code: a pasted picture goes up, shows, and rides the first message`, async ({ page }) => {
    await page.setViewportSize(size);
    let turnBody: Record<string, unknown> | null = null;
    await page.route(`${DAEMON}/build/app/harnesses`, (r) => json(r, TOOLS));
    await page.route(
      (url) => url.href.startsWith(`${DAEMON}/build/app/sessions`) && !url.pathname.includes("/sessions/"),
      (r) => (r.request().method() === "POST" ? json(r, SESSION, 201) : json(r, { sessions: [], work_root: "" })),
    );
    await page.route(`${DAEMON}/build/app/sessions/n1?*`, (r) => json(r, { session: SESSION, events: [], running: false, pending: [] }));
    await page.route(`${DAEMON}/build/app/sessions/n1`, (r) => json(r, { session: SESSION, events: [], running: false, pending: [] }));
    await page.route(`${DAEMON}/build/app/sessions/n1/turn`, async (route) => {
      if (route.request().method() === "OPTIONS") {
        await route.fulfill({ status: 204, headers: CORS });
        return;
      }
      turnBody = route.request().postDataJSON() as Record<string, unknown>;
      const files = (turnBody.files as string[]) ?? [];
      const frames = [
        { type: "user", at: "x", text: turnBody.text, files, turn: "t1" },
        { type: "text_done", at: "x", item: "m1", text: "It is a small red square.", turn: "t1" },
        { type: "done", at: "x", stop: "completed", turn: "t1" },
      ];
      await route.fulfill({
        status: 200,
        headers: { ...CORS, "content-type": "text/event-stream" },
        body: frames.map((f) => `event: ${f.type}\ndata: ${JSON.stringify(f)}\n\n`).join(""),
      });
    });
    await page.goto("/terminals?view=code");
    const box = page.getByTestId("code-start-box");
    await expect(box).toBeVisible({ timeout: 90_000 });
    await pastePicture(page, "code-start-box");
    await thumbOk(page, '[data-testid="harness-attachment-thumb"]');
    await expect(page.getByTestId("harness-attachment")).toContainText("Pasted picture");
    await box.fill("What is in this picture?");
    await page.getByTestId("code-start-send").click();
    await expect.poll(() => turnBody, { timeout: 30_000 }).not.toBeNull();
    const sent = turnBody as unknown as { text: string; files: string[] };
    expect(sent.text).toBe("What is in this picture?");
    expect(sent.files).toHaveLength(1);
    expect(sent.files[0]).toMatch(/pasted-.*\.png$/);
    // The sent message shows its picture.
    await thumbOk(page, '[data-testid="code-user"] [data-testid="harness-sent-thumb"]');
  });

  test(`${size.width} px — Chat › Code: a pasted picture on the composer-first start goes up and shows`, async ({ page }) => {
    await page.setViewportSize(size);
    const made = await page.request.post(`${DAEMON}/build/sessions`, { data: { folder: "" } });
    expect(made.status()).toBe(201);
    const id = ((await made.json()) as { id: string }).id;
    await page.goto(`/chat?view=code&session=${id}`);
    const box = page.getByTestId("harness-input");
    await expect(box).toBeVisible({ timeout: 90_000 });
    await pastePicture(page, "harness-input");
    await thumbOk(page, '[data-testid="harness-attachment-thumb"]');
    await expect(page.getByTestId("harness-attachment")).toContainText("Pasted picture");
    await page.request.delete(`${DAEMON}/build/sessions/${id}`);
  });
}
