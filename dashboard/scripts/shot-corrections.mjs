// Real-browser screenshot proof for the five UX corrections. Launches Edge (or
// Chrome) via puppeteer-core, loads each corrected page on the running
// dashboard, waits for network idle + a REAL 3s sleep (Edge's virtual-time
// budget does not wait for framer-motion/client fetch), and captures full-page
// PNGs at deviceScaleFactor 2.

import { existsSync, mkdirSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROOF_DIR = resolve(__dirname, "..", "proof");
const BASE = process.env.SHOT_URL || "http://localhost:3000";

const CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
];

function findBrowser() {
  for (const p of CANDIDATES) if (existsSync(p)) return p;
  throw new Error("No Edge/Chrome executable found in known locations.");
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const PAGES = [
  ["/filesearch", "fix-filesearch.png"],
  ["/schedules", "fix-schedules.png"],
  ["/agents", "fix-agents.png"],
  // Click "Add webhook" so the create form is visible in the proof.
  ["/webhooks", "fix-webhooks.png", "Add webhook"],
  ["/ltm", "fix-ltm.png"],
];

// Click the first <button> whose text contains `label` (used to reveal forms).
async function clickButton(page, label) {
  const clicked = await page.evaluate((text) => {
    const btn = [...document.querySelectorAll("button")].find((b) =>
      (b.textContent || "").includes(text),
    );
    if (btn) {
      btn.click();
      return true;
    }
    return false;
  }, label);
  if (clicked) await sleep(800);
  return clicked;
}

async function shoot(page, path, file, clickLabel) {
  const url = `${BASE}${path}`;
  console.log(`-> navigating ${url}`);
  await page.goto(url, { waitUntil: "networkidle0", timeout: 60000 });
  await sleep(3000); // real wait for fetch + framer-motion
  if (clickLabel) await clickButton(page, clickLabel);
  const out = resolve(PROOF_DIR, file);
  await page.screenshot({ path: out, fullPage: true });
  const { size } = statSync(out);
  console.log(`   saved ${out} (${size} bytes)`);
  return size;
}

async function main() {
  mkdirSync(PROOF_DIR, { recursive: true });
  const executablePath = findBrowser();
  console.log(`using browser: ${executablePath}`);

  const browser = await puppeteer.launch({
    executablePath,
    headless: "new",
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 2 });
    for (const [path, file, clickLabel] of PAGES)
      await shoot(page, path, file, clickLabel);
  } finally {
    await browser.close();
  }
  console.log("done.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
