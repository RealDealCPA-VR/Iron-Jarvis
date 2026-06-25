// Real-browser screenshot proof for Workflows editor Load/Save. Launches Edge
// (or Chrome) via puppeteer-core, loads /workflows, waits for network idle + a
// REAL sleep so React Flow paints, then drives the new Load ▾ dropdown:
//   open dropdown -> pick the first saved/agent-authored workflow (rebuilds the
//   node graph on the canvas) -> re-open the dropdown -> capture a full-page PNG
// at deviceScaleFactor 2, showing the loaded graph + the Load list together.

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

async function clickByText(page, re) {
  const src = re.source;
  const flags = re.flags;
  return page.evaluate(
    (s, f) => {
      const rx = new RegExp(s, f);
      const btn = [...document.querySelectorAll("button")].find((b) =>
        rx.test((b.textContent || "").trim()),
      );
      if (btn) {
        btn.click();
        return true;
      }
      return false;
    },
    src,
    flags,
  );
}

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

    // Open the Load dropdown (also triggers a list refresh).
    if (await clickByText(page, /^Load$/i)) {
      console.log("-> opened Load dropdown");
      await sleep(1200);
    } else {
      console.log("   (Load button not found)");
    }

    // Pick the first saved/agent-authored workflow -> rebuilds the graph.
    const loaded = await page.evaluate(() => {
      // The dropdown items list step counts like "3 steps"; pick the first one.
      const items = [...document.querySelectorAll("button")].filter((b) =>
        /\bstep(s)?\b/i.test((b.querySelector("span span:last-child")?.textContent || "")),
      );
      if (items[0]) {
        items[0].click();
        return (items[0].textContent || "").trim().slice(0, 60);
      }
      return null;
    });
    if (loaded) {
      console.log(`-> loaded workflow: ${loaded}`);
      await sleep(2600); // fitView animation + repaint
    } else {
      console.log("   (no saved workflows in list to load)");
    }

    // Re-open the Load dropdown so the screenshot shows graph + list together.
    if (await clickByText(page, /^Load$/i)) await sleep(1200);

    await capture(page, "wf-loadsave.png");
  } finally {
    await browser.close();
  }
  console.log("done.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
