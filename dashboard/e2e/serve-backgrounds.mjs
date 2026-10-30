// v1.410.0: stands in for GitHub's raw file host during the end-to-end run.
// Serves ONLY the repository's backgrounds/<id>/index.html files, on loopback,
// so the scratch daemon (IRONJARVIS_BACKGROUNDS_BASE, loopback-only by design)
// downloads the real files byte for byte without touching the internet.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "backgrounds");
const port = Number(process.env.IJ_E2E_BG_PORT) || 8809;

createServer(async (req, res) => {
  const m = /^\/backgrounds\/([a-z0-9-]+)\/index\.html$/.exec(req.url || "");
  if (req.url === "/" || req.url === "/health") {
    res.writeHead(200).end("ok");
    return;
  }
  if (!m) {
    res.writeHead(404).end();
    return;
  }
  try {
    const body = await readFile(join(root, m[1], "index.html"));
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" }).end(body);
  } catch {
    res.writeHead(404).end();
  }
}).listen(port, "127.0.0.1", () => console.log(`e2e: backgrounds served on ${port}`));
