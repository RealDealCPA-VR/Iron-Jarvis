/**
 * v1.420.0 — ONE vocabulary for the two Code views (fix plan C5.1, C5.2,
 * C1.8, C6.7; decisions D3 and D6).
 *
 *  - Chat › Code and Build › Code both keep the name "Code"; a row that must
 *    tell them apart names the TOOL: "Coding session (Jarvis)" for Chat ›
 *    Code, "Coding session (Claude Code)" / "(Codex)" / "(Pi)" /
 *    "(OpenCode)" / "(Grok)" for Build › Code (Running now).
 *  - Build says what it holds now (terminals AND the Code view) and is found
 *    by "code", "claude code" and "codex".
 *  - The engine-room words leave the screen: "worktree" ("its own copy of
 *    the folder"), "Iron-Proxy" on the coding surfaces ("your accounts"),
 *    "no safe inverse" ("can't be undone"), "Attaching to sessions…"
 *    ("Opening your terminals…"); a stopped run has its own grey lane.
 *  - The approval levels are named by what they do, the same in Settings
 *    (settings/schema.py) and the chip; "Don't ask" says what still asks.
 *  - Local only says "this PC or your own network" (egress lets a LAN or a
 *    tailnet host through).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { BUILD_BLURB, CODING_SESSION_JARVIS, codingSessionWith } from "@/lib/buildWords";
import { buildAppReplyItems, buildReplyItems } from "@/lib/runningNow";
import { NAV_ENTRIES } from "@/lib/nav";
import { PERMISSION_LEVELS, PERMISSION_MODES, permissionLevel, readsLevelLine } from "@/lib/permissionLevels";
import {
  CLOUD_ASK_BODY_END,
  LOCAL_ONLY_MENU_ROW,
  LOCAL_ONLY_TITLE_OFF,
  LOCAL_ONLY_TITLE_ON,
  cloudAskBody,
} from "@/lib/localOnly";
import { LANES, laneFor } from "@/lib/kanban";
import { branchLine } from "@/lib/buildGit";
import { accountBlockedReason, accountStatusLine } from "@/lib/buildAccounts";
import { cannotUndoTitle } from "@/components/chat/TurnReceipt";
import { RETIRED, RETIRED_ON_SCREEN, readableCopy, retiredHits } from "./helpers/retiredWords";
import type { SessionView } from "@/lib/types";

const DASH = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(DASH, rel), "utf-8").replace(/\r\n/g, "\n");
const NOW = Date.parse("2026-10-30T12:00:00Z");

/* --------------------------------------------- C5.1 two views, told apart */

describe("the two Code views are told apart by the tool (C5.1, D6)", () => {
  it("a Chat › Code session reads 'Coding session (Jarvis)' in Running now", () => {
    expect(CODING_SESSION_JARVIS).toBe("Coding session (Jarvis)");
    const [item] = buildReplyItems(
      [{ turn_id: "t1", thread_id: "b1", surface: "build", started_at: "2026-10-30T11:48:00Z", title: "Fix it" }],
      NOW,
    );
    expect(item.who).toBe("Coding session (Jarvis)");
    expect(item.line).toBe("Coding session (Jarvis) · 12 min");
  });

  it.each([
    ["claude-code", "Claude Code"],
    ["codex", "Codex"],
    ["pi", "Pi"],
    ["opencode", "OpenCode"],
    ["grok", "Grok"],
  ])("a Build › Code session on %s reads 'Coding session (%s)'", (harness, tool) => {
    const [item] = buildAppReplyItems(
      [{ turn_id: "t1", thread_id: "a1", surface: "build_app", harness, started_at: "2026-10-30T11:58:00Z", waiting: true }],
      NOW,
    );
    expect(item.who).toBe(`Coding session (${tool})`);
    expect(item.line).toBe(`Coding session (${tool}) · 2 min · Waiting on you`);
    expect(codingSessionWith(tool)).toBe(`Coding session (${tool})`);
  });

  it("an unknown tool is not guessed at", () => {
    const [item] = buildAppReplyItems([{ turn_id: "t1", thread_id: "a1", surface: "build_app", harness: "" }], NOW);
    expect(item.who).toBe("Coding session");
  });

  it("Build says it holds the terminals AND the Code view, and is found by the coding tools' names", () => {
    expect(BUILD_BLURB).toMatch(/^Terminals for your own coding tools — Claude Code, Codex or any command line/);
    expect(BUILD_BLURB).toMatch(/Code view/);
    const build = NAV_ENTRIES.find((e) => e.href === "/terminals")!;
    for (const a of ["code", "claude code", "codex"]) expect(build.aliases).toContain(a);
    const chat = NAV_ENTRIES.find((e) => e.href === "/chat")!;
    expect(chat.aliases).toContain("code");
  });

  it("VOCABULARY.md has a row for each Code view", () => {
    const canon = read("../VOCABULARY.md");
    expect(canon).toContain("| **Chat › Code**: ");
    expect(canon).toContain("| **Build › Code**: ");
    expect(canon).toContain("**coding session (Jarvis)**");
    expect(canon).toContain("**coding session (Claude Code)**");
  });
});

/* ------------------------------------------- C5.2 engine-room words retired */

const MY_FILES = [
  "lib/buildWords.ts",
  "lib/runningNow.ts",
  "lib/nav.ts",
  "lib/surfaces.ts",
  "lib/permissionLevels.ts",
  "lib/localOnly.ts",
  "lib/buildAccounts.ts",
  "lib/buildGit.ts",
  "lib/kanban.ts",
  "components/harness/HarnessBranch.tsx",
  "components/harness/HarnessAccounts.tsx",
  "components/harness/HarnessStartFolder.tsx",
  "components/buildCode/CodePanes.tsx",
  "components/chat/TurnReceipt.tsx",
  "app/terminals/page.tsx",
];

describe("the engine-room words leave the screen (C5.2)", () => {
  it("the scan knows them", () => {
    const words = [...RETIRED, ...RETIRED_ON_SCREEN].map((r) => r.word);
    for (const w of ["worktree", "no safe inverse", "attaching to sessions"]) expect(words).toContain(w);
  });

  it("none of this lane's files says one", () => {
    const hits = MY_FILES.flatMap((f) => retiredHits(f)).map((h) => `${h.file}:${h.line} ${h.text.slice(0, 80)}`);
    expect(hits).toEqual([]);
  });

  it("the coding surfaces say 'your accounts', never Iron-Proxy", () => {
    for (const f of ["components/harness/HarnessAccounts.tsx", "lib/buildAccounts.ts", "components/buildCode/CodePanes.tsx"]) {
      const shown = retiredHits(f, read(f).replace(/Iron-Proxy/g, "IRONPROXY_PROBE pack"));
      // The probe word rides on every shown "Iron-Proxy": none may be left.
      expect(shown.filter((h) => h.text.includes("IRONPROXY_PROBE")), f).toEqual([]);
    }
    expect(accountStatusLine({ status: "missing" } as never).text).toBe("No longer in your accounts");
    expect(accountBlockedReason({ status: "missing" } as never)).toBe(
      "This account is no longer in your accounts. Choose another one.",
    );
  });

  it("a removed copy of the folder says so in plain words", () => {
    expect(branchLine({ branch: "ij/x", base: "main", removed: true } as never)).toBe(
      "ij/x from main · its own copy of the folder removed",
    );
  });

  it("an undo that cannot happen says 'can't be undone'", () => {
    expect(cannotUndoTitle("this action has no safe inverse")).toBe("This change can't be undone.");
    expect(cannotUndoTitle("already undone")).toBe("Can't undo: already undone");
    expect(cannotUndoTitle(undefined)).toBe("Can't undo: not undoable");
  });

  it("Build opens with 'Opening your terminals…'", () => {
    const shown = readableCopy("app/terminals/page.tsx").map((c) => c.text);
    expect(shown).toContain("Opening your terminals…");
    expect(shown.filter((t) => /attaching to sessions/i.test(t))).toEqual([]);
  });

  it("a stopped run has its own grey Stopped lane; Failed is real failures", () => {
    const view = (status: string) => ({ id: "s", status } as unknown as SessionView);
    expect(laneFor(view("cancelled"), false)).toBe("stopped");
    expect(laneFor(view("failed"), false)).toBe("failed");
    const stopped = LANES.find((l) => l.id === "stopped")!;
    expect(stopped.title).toBe("Stopped");
    expect(stopped.tone).toBe("slate");
    expect(LANES.find((l) => l.id === "failed")!.hint).not.toMatch(/stopped/i);
  });
});

/* ------------------------------------- C1.8 one posture vocabulary (D3) */

function schemaOptions(): Record<string, string> {
  const py = read("../src/iron_jarvis/settings/schema.py");
  const at = py.indexOf('S("device.approval_mode"');
  const block = py.slice(at, py.indexOf(")),", py.indexOf("options=", at)) + 3);
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/\("(\w+)", "([^"]+)"\)/g)) out[m[1]] = m[2];
  return out;
}

describe("the approval levels are named by what they do, the same everywhere (C1.8)", () => {
  it("the chip's names, strictest first", () => {
    expect(PERMISSION_MODES.map((m) => PERMISSION_LEVELS[m].label)).toEqual([
      "Ask before every action",
      "Ask before commands",
      "Accept edits",
      "Auto (its own safety check)",
      "Don't ask",
    ]);
  });

  it("Settings (settings/schema.py) names them exactly as the chip does", () => {
    const opts = schemaOptions();
    expect(Object.keys(opts)).toEqual([...PERMISSION_MODES]);
    for (const m of PERMISSION_MODES) expect(opts[m]).toBe(PERMISSION_LEVELS[m].label);
  });

  it("Accept edits says what differs from Ask before commands", () => {
    expect(PERMISSION_LEVELS.accept_edits.description).toMatch(/^Like Ask before commands/);
  });

  it("Don't ask names connected apps, commits and pushes", () => {
    const d = PERMISSION_LEVELS.yolo.description;
    expect(d).toMatch(/^Runs commands, edits files and uses connected apps without asking;/);
    expect(d).toMatch(/commits and pushes/);
    expect(d).toMatch(/still ask/);
    expect(PERMISSION_LEVELS.yolo.tone).toBe("warn");
  });

  it("the chip stays short and keeps reading what Jarvis may read", () => {
    expect(PERMISSION_LEVELS.auto.chip).toBe("Auto");
    const line = readsLevelLine({ limited: true, chosen: ["C:\\Work"] });
    expect(permissionLevel("approve_for_me", line).description).toContain(line);
    expect(permissionLevel("yolo", line).description).toContain(line);
  });
});

/* ------------------------------------------------ C6.7 Local only's words */

describe("Local only says 'this PC or your own network' (C6.7)", () => {
  it("the chip, the menu and the card", () => {
    expect(LOCAL_ONLY_TITLE_ON).toBe("Nothing in this chat leaves this PC or your own network without asking you.");
    expect(LOCAL_ONLY_TITLE_OFF).toBe("Keep this chat on this PC or your own network.");
    expect(LOCAL_ONLY_MENU_ROW).toBe("Keep this chat on this PC or your own network");
    expect(cloudAskBody("Opus 4.8")).toBe(`Send this message to Opus 4.8? ${CLOUD_ASK_BODY_END}`);
    expect(CLOUD_ASK_BODY_END).toBe("It will leave this PC and your own network.");
  });
});
