const puppeteer = require("puppeteer-core");
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath: process.env.CHROME_PATH, headless: "new",
    args: ["--no-sandbox","--disable-dev-shm-usage"] });
  const p = await b.newPage();
  await p.setViewport({ width: 1440, height: 1000, deviceScaleFactor: 2 });
  const errs = []; p.on("pageerror", e => errs.push(e.message));
  const reqs = [];
  p.on("request", r => { if (r.method() === "PATCH") reqs.push(r.url() + " " + (r.postData()||"")); });

  await p.goto("http://127.0.0.1:8798/connections", { waitUntil: "networkidle0", timeout: 60000 });
  await sleep(3000);

  // open the rename on the my-vllm row specifically
  const opened = await p.evaluate(() => {
    const b = [...document.querySelectorAll("button")]
      .find(x => (x.title||"").includes("click to rename") && x.textContent.includes("my-vllm"));
    if (!b) return false;
    b.scrollIntoView({ block: "center" }); b.click(); return true;
  });
  await sleep(500);
  await p.screenshot({ path: process.argv[2] });

  // React controlled input: set via the native setter, then fire `input`
  const typed = await p.evaluate(() => {
    const el = document.querySelector('input[aria-label="Endpoint name"]');
    if (!el) return false;
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setter.call(el, "Spark GB10 cluster");
    el.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  });
  await sleep(200);
  await p.keyboard.press("Enter");
  await sleep(2000);
  await p.screenshot({ path: process.argv[3] });

  const labels = await p.evaluate(() =>
    [...document.querySelectorAll("button")]
      .filter(x => (x.title||"").includes("click to rename"))
      .map(x => x.textContent.trim()));
  console.log(JSON.stringify({ opened, typed, labels, patchRequests: reqs, errors: errs.slice(0,2) }, null, 1));
  await b.close();
})();
