const puppeteer = require("puppeteer-core");
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath: process.env.CHROME_PATH, headless: "new",
    args: ["--no-sandbox","--disable-dev-shm-usage"] });
  const p = await b.newPage();
  await p.setViewport({ width: 1440, height: 1000, deviceScaleFactor: 2 });
  const errs = []; p.on("pageerror", e => errs.push(e.message));
  await p.goto("http://127.0.0.1:8798/connections", { waitUntil: "networkidle0", timeout: 60000 });
  await sleep(3000);

  // find the rename control by its title text
  const found = await p.evaluate(() => {
    const b = [...document.querySelectorAll("button")]
      .find(x => (x.title || "").includes("click to rename"));
    if (!b) return { found: false };
    b.scrollIntoView({ block: "center" });
    b.click();
    return { found: true, was: b.textContent.trim() };
  });
  await sleep(400);
  await p.screenshot({ path: process.argv[2] });

  // type a new name and commit with Enter
  let renamed = null;
  if (found.found) {
    await p.keyboard.down("Control"); await p.keyboard.press("KeyA"); await p.keyboard.up("Control");
    await p.type('input[aria-label="Endpoint name"]', "Spark GB10 cluster");
    await p.keyboard.press("Enter");
    await sleep(1800);
    renamed = await p.evaluate(() => {
      const b = [...document.querySelectorAll("button")]
        .find(x => (x.title || "").includes("click to rename"));
      return b ? b.textContent.trim() : null;
    });
    await p.screenshot({ path: process.argv[3] });
  }
  console.log(JSON.stringify({ ...found, renamedTo: renamed, errors: errs.slice(0,3) }, null, 1));
  await b.close();
})();
