// A fresh, EMPTY daemon home for every end-to-end run (brief T3: "with an
// empty config directory"). Lives under e2e/.home (git-ignored).
import { mkdirSync, rmSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const home = join(dirname(fileURLToPath(import.meta.url)), ".home");
rmSync(home, { recursive: true, force: true });
mkdirSync(home, { recursive: true });
console.log(`e2e: fresh home at ${home}`);
