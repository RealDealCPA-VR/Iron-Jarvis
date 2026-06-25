// Real-browser screenshot proof for the new feature pages. Launches Edge (or
// Chrome) via puppeteer-core, loads each running page, waits for network idle +
// a REAL sleep, and captures full-page PNGs at deviceScaleFactor 2.

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

const SHOTS = [
  ["/secrets", "feat-secrets.png"],
  ["/integrations", "feat-integrations.png"],
  ["/schedules", "feat-schedules.png"],
  ["/filesearch", "feat-filesearch.png"],
  ["/ltm", "feat-ltm.png"],
  ["/agents", "feat-agents.png"],
  ["/channels", "feat-channels.png"],
  ["/sessions", "feat-newsession.png"],
];

async function shoot(page, path, file) {
  const url = `${BASE}${path}`;
  console.log(`-> navigating ${url}`);
  await page.goto(url, { waitUntil: "networkidle0", timeout: 60000 });
  await sleep(3000);
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
    for (const [path, file] of SHOTS) {
      await shoot(page, path, file);
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
