/**
 * v1.330.0 (calm chat wave 10, L3): ONE no-dash guard.
 *
 * chat-notices-v1329 and mission-copy-v1329 each carried a copy of the same
 * TypeScript-parser walker ("no spaced em or en dash in user-visible strings
 * and JSX text"). The walker lives in __tests__/helpers/dashGuard.ts now and
 * both import it. This file pins that: neither keeps a walker of its own,
 * each still names every file it read before the move (the union does not
 * shrink), each keeps its allowlist, and the shared reader still catches and
 * skips exactly what the two copies did.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { asides, copyPieces } from "./helpers/dashGuard";

const HERE = __dirname;
const readTest = (name: string) => readFileSync(path.join(HERE, name), "utf8").replace(/\r\n/g, "\n");

const GUARDS = ["chat-notices-v1329.test.tsx", "mission-copy-v1329.test.tsx"];

describe("one shared no-dash guard", () => {
  it.each(GUARDS)("%s imports the shared reader and keeps no walker of its own", (name) => {
    const src = readTest(name);
    expect(src).toMatch(/import \{[^}]*\basides\b[^}]*\} from "\.\/helpers\/dashGuard";/);
    for (const own of [
      "createSourceFile",
      "forEachChild",
      "function copyPieces",
      "function jsxDashAsides",
      "function jsxRendered",
      "function asides",
      'from "typescript"',
    ]) {
      expect(src, `${name} still has ${own}`).not.toContain(own);
    }
  });

  it("the union of files the two guards read did not shrink", () => {
    // What each guard read before the move (v1.329.0). The mission guard
    // reads two folders whole; the rest are named.
    const chat = readTest("chat-notices-v1329.test.tsx");
    for (const rel of [
      "components/chat/stepLabel.ts",
      "components/chat/PreflightNote.tsx",
      "components/chat/RetryTurnButton.tsx",
      "app/chat/page.tsx",
    ])
      expect(chat, rel).toContain(`"${rel}"`);
    const mission = readTest("mission-copy-v1329.test.tsx");
    expect(mission).toContain('const MISSION_DIR = "components/agents/mission";');
    expect(mission).toContain('const WORKFLOW_DIR = "components/workflow";');
    expect(mission).toMatch(/readdirSync\(path\.join\(ROOT, MISSION_DIR\)\)/);
    expect(mission).toMatch(/readdirSync\(path\.join\(ROOT, WORKFLOW_DIR\)\)/);
    for (const rel of [
      "lib/mission.ts",
      "components/workflow/starters.ts",
      "lib/agentWorlds.ts",
      "app/agents/page.tsx",
      "app/workflows/page.tsx",
      "components/NotificationBell.tsx",
    ])
      expect(mission, rel).toContain(`"${rel}"`);
    // The copy list is still the mission files + the workflow editor + the
    // Workflows page + the bell, and the copy test still runs over it.
    expect(mission).toContain("...WORKFLOW_FILES.filter((f) => !FILES.includes(f)),");
    expect(mission).toContain("expect(COPY_FILES.flatMap((rel) => asides(rel))).toEqual([]);");
  });

  it("the chat guard keeps its model-facing allowlist, applied to the chat page only", () => {
    const chat = readTest("chat-notices-v1329.test.tsx");
    for (const key of ["agentReplyLabel", "skillTask", "doItTask"]) expect(chat).toContain(`${key}:`);
    expect(chat).toContain('asides(rel, undefined, rel === "app/chat/page.tsx" ? allow : [])');
  });

  it("the shared reader catches what both copies caught, and skips comments and placeholders", () => {
    const probe = [
      "// a comment — fine",
      "/* block — fine */",
      'const a = "one — two";',
      "const b = `x ${1} — y`;",
      "const c = <p>{/* jsx comment — fine */}left — right</p>;",
      'const d = <span>— a caption</span>;',
      'const e = "—";',
      'const f = <p>run <b>x</b>{" "}',
      "  —{\" \"}",
      '  {"the run is waiting."}</p>;',
      'const g = <p>left {"—"} right</p>;',
      "const h = <p>{a} — </p>;",
      'const i = <span>{a ?? "—"}</span>;',
      "const j = <td>—</td>;",
      "const k = <td>\n  —\n</td>;",
      "const l = <p>{a}—{b}</p>;",
      'const m = " – ";',
      "const n = <td> — </td>;",
    ].join("\n");
    expect(asides("probe.tsx", probe).map((s) => s.split(":")[1]).sort((x, y) => Number(x) - Number(y))).toEqual([
      "3", "4", "5", "6", "9", "11", "12", "19",
    ]);
    // An allowed fragment is skipped only where it appears.
    expect(asides("probe.tsx", 'const a = "one — two";\nconst b = "three — four";', ["one — two"])).toEqual([
      "probe.tsx:2: three — four",
    ]);
    // A .ts file is read too (no JSX), and comments never count.
    expect(asides("probe.ts", '// x — y\nconst s = "fine";\nconst t = "bad — one";')).toEqual(["probe.ts:3: bad — one"]);
  });

  it("the shared reader reads real copy from a covered file (anti-vacuity)", () => {
    const loading = copyPieces("app/agents/page.tsx").map((p) => p.text).join("\n");
    expect(loading).toContain("Loading…");
  });
});
