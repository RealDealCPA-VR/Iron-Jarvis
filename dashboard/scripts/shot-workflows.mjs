// Real-browser screenshot proof for the n8n-style Workflows editor. Launches
// Edge (or Chrome) via puppeteer-core, loads /workflows, waits for network idle
// + a REAL sleep so React Flow has painted the node graph + animated edges, then
// captures a full-page PNG at deviceScaleFactor 2. Optionally clicks "Run
// workflow" and captures the result strip.

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

async function capture(page, file) {
  const out = resolve(PROOF_DIR, file);
  await page.screenshot({ path: out, fullPage: true });
  const { size } = statSync(out);
  console.log(`   saved ${file} (${size} bytes)`);
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

    const url = `${BASE}/workflows`;
    console.log(`-> navigating ${url}`);
    await page.goto(url, { waitUntil: "networkidle0", timeout: 60000 });
    await sleep(3000); // real settle so React Flow paints nodes + animated edges
    await capture(page, "feat-workflows-n8n.png");

    // Optional: click "Run workflow" and capture the result strip.
    try {
      const clicked = await page.evaluate(() => {
        const btn = [...document.querySelectorAll("button")].find((b) =>
          /run workflow/i.test(b.textContent || ""),
        );
        if (btn) {
          btn.click();
          return true;
        }
        return false;
      });
      if (clicked) {
        console.log("-> clicked Run workflow");
        await sleep(4000);
        await capture(page, "feat-workflows-n8n-run.png");
      }
    } catch (err) {
      console.log(`   (run capture skipped: ${err.message})`);
    }
  } finally {
    await browser.close();
  }
  console.log("done.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
