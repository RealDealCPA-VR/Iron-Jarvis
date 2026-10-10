/**
 * v1.329.0 — calm chat wave 7, I1: the rest of the mission and workflow copy
 * is plain, with no dash asides.
 *
 * Wave 6 (H4) cleaned AgentCards and MissionOutput. The final audit found the
 * same em-dash asides in the files around them: the mission door's four
 * default-model warnings, its older-daemon line and its intro sentence; the
 * team panel's "You don't have to choose — just say what you need."; the
 * "Can't reach the daemon right now — …" line; the project team panel's
 * empty state; the "— give a project's own team an objective" caption; the
 * mission headline ("Finished — something needs you", "Queued — …"); and
 * two workflow starter cards. They are plain sentences now, and the literal
 * hues and half-pixel sizes in those files are tone tokens / whole pixels.
 *
 * The guard at the bottom reads every STRING LITERAL and JSX TEXT node in
 * these files through the TypeScript parser (so comments never count) and
 * fails if a spaced em or en dash comes back. Wave 8 (J5) widened it to the
 * whole workflow editor (components/workflow) and the notification bell.
 */

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import ts from "typescript";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const H = vi.hoisted(() => ({ worlds: null as unknown }));

vi.mock("@/lib/useApi", () => ({
  useApi: () => ({ data: null, loading: false, error: null, reload: () => {} }),
  usePolledApi: () => ({ data: H.worlds, loading: false, error: null, reload: () => {} }),
}));

import { preflightProblem } from "@/components/agents/mission/MissionComposer";
import { ProjectTeamPanel } from "@/components/agents/mission/ProjectTeamPanel";
import { ProjectTeams } from "@/components/agents/mission/ProjectTeams";
import { LiveActivity } from "@/components/agents/mission/LiveActivity";
import { missionHeadline, type MissionView } from "@/lib/mission";
import { STARTERS } from "@/components/workflow/starters";

const ROOT = path.join(__dirname, "..");
const readSrc = (rel: string) => readFileSync(path.join(ROOT, rel), "utf8").replace(/\r\n/g, "\n");

afterEach(() => {
  cleanup();
  H.worlds = null;
});

/* ------------------------------------------------ 1. the mission door --- */

type Health = Parameters<typeof preflightProblem>[0];

function health(row: Record<string, unknown>): Health {
  return { default_provider: "fleet-custom", providers: [{ provider: "fleet-custom", ...row }] } as unknown as Health;
}

describe("the mission door's default-model warnings are plain sentences", () => {
  it("not signed in: says it will fail, in sentences", () => {
    const p = preflightProblem(health({ available: false, installed: true, signed_in: false }));
    expect(p?.text).toBe(
      "Your default model, fleet-custom, is installed but not signed in. A task started now will fail. Sign it in on Connections first.",
    );
  });

  it("unreachable: says it will fail, in sentences", () => {
    const p = preflightProblem(health({ available: false }));
    expect(p?.text).toBe(
      "Your default model, fleet-custom, isn't reachable right now. A task started now will fail. Bring it back, or choose another default on Connections.",
    );
  });

  it("paused by the breaker: says the wait, in sentences", () => {
    const p = preflightProblem(health({ available: true, circuit: { open: true, retry_in_s: 30 } }));
    expect(p?.text).toBe(
      "Your default model, fleet-custom, failed repeatedly and is paused for 30 s. A task started now is refused. Wait, or choose another default on Connections.",
    );
  });

  it("a stale check: says it could not check, never 'will fail'", () => {
    const p = preflightProblem(health({ available: false }), true);
    expect(p?.text).toBe(
      "Iron Jarvis could not check your default model, fleet-custom. The last check couldn't reach the daemon, and it looked unavailable before that. If a task fails, check it on Connections.",
    );
    expect(p?.text).not.toMatch(/will fail/i);
  });

  it("control: a reachable default draws no warning", () => {
    expect(preflightProblem(health({ available: true }))).toBeNull();
  });
});

/* ---------------------------------------------- 2. the mission headline --- */

function view(status: string, outcome: string | null = null): MissionView {
  return {
    session: {
      id: "s1",
      task: "t",
      objective: "t",
      status,
      outcome,
      project_id: null,
      created_at: null,
      finished_at: null,
      interrupted: false,
      continued_as: null,
      provider: "",
      model: "",
      route_note: "",
    },
    coordinator: { name: "Jarvis", status: "done", waiting_on: null, steps: 1 },
    members: [],
    progress: { done: 0, total: 0 },
    activity: [],
    deliverable: { text: "", documents: [], worklist: null },
  } as unknown as MissionView;
}

describe("the mission headline has no dash aside", () => {
  it("finished with something waiting on the user", () => {
    expect(missionHeadline(view("completed", "needs_you"))).toBe("Finished, but something needs you");
  });
  it("queued", () => {
    expect(missionHeadline(view("queued"))).toBe("Queued, waiting for a free slot");
  });
  it("control: a plain finish is one word", () => {
    expect(missionHeadline(view("completed", "completed"))).toBe("Finished");
  });
});

/* ------------------------------------------- 3. the project team panels --- */

describe("the project team panels say it plainly, in tone tokens", () => {
  it("no team yet: two plain sentences", () => {
    render(<ProjectTeamPanel projectId="p1" projectName="Acme" team={[]} onSaved={() => {}} />);
    const text = screen.getByTestId("mission-project-team").textContent ?? "";
    expect(text).toContain("No team picked yet. Jarvis chooses from all your agents.");
    expect(text).not.toContain("—");
  });

  it("the projects list caption is a sentence, and 'waiting on you' is the warn tone", () => {
    H.worlds = {
      worlds: [
        {
          project: { id: "p1", name: "Acme books" },
          team: [],
          thread_id: null,
          counts: { waiting: 2, running: 0, queued: 0, done_7d: 0 },
        },
      ],
    };
    render(<ProjectTeams onOpen={() => {}} />);
    const header = screen.getByTestId("mission-projects").querySelector("header")!;
    expect(header.textContent).toBe("Your projectsGive a project's own team an objective.");
    // The count line under the name says it too; the chip is the pill.
    const chip = screen.getAllByText("2 waiting on you").find((el) => el.className.includes("rounded-full"))!;
    expect(chip).toBeTruthy();
    expect(chip.className).toContain("text-tone-warn");
    expect(chip.className).not.toMatch(/amber/);
  });

  it("the live activity dots are tone tokens", () => {
    render(
      <LiveActivity
        running={false}
        lines={[
          { at: null, text: "Researcher read k1.pdf", tone: "ok" },
          { at: null, text: "Writer failed", tone: "warn" },
          { at: null, text: "Asked you", tone: "ask" },
        ] as never}
      />,
    );
    const log = screen.getByTestId("mission-activity");
    expect(log.querySelector(".bg-tone-success")).not.toBeNull();
    expect(log.querySelector(".bg-tone-danger")).not.toBeNull();
    expect(log.querySelector(".bg-tone-warn")).not.toBeNull();
  });
});

/* ------------------------------------------------- 4. workflow starters --- */

describe("the workflow starter cards read as sentences", () => {
  it("no description, blurb, ask or notify text carries a dash aside", () => {
    for (const s of STARTERS) {
      expect(s.description, s.name).not.toMatch(/\s[—–]\s/);
      expect(s.blurb, s.name).not.toMatch(/\s[—–]\s/);
      for (const step of s.steps) {
        for (const v of [step.task, step.message]) {
          if (typeof v === "string") expect(v, `${s.name} / ${step.name}`).not.toMatch(/\s[—–]\s/);
        }
      }
    }
    const intake = STARTERS.find((s) => s.name === "client-intake-triage")!;
    expect(intake.blurb).toBe(
      "Pin the client's project and run it. You get a classified document list and the gaps, and you approve the triage before the summary is written.",
    );
  });
});

/* ------------------------------------------------ 5. the source guards --- */

/** Every string literal, template piece and JSX text node in a source file,
 *  with its line. Comments are not nodes, so they never count. */
function copyPieces(rel: string, src = readSrc(rel)): Array<{ line: number; text: string; jsx: boolean }> {
  const file = ts.createSourceFile(rel, src, ts.ScriptTarget.Latest, true, rel.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const out: Array<{ line: number; text: string; jsx: boolean }> = [];
  const visit = (node: ts.Node) => {
    if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node) ||
      ts.isJsxText(node)
    ) {
      const text = ts.isJsxText(node) ? node.getText(file) : (node as ts.LiteralLikeNode).text;
      out.push({ line: file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1, text, jsx: ts.isJsxText(node) });
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return out;
}

/** A dash used as an aside: an em or en dash with a space (or the edge of
 *  the text) on both sides. A lone dash placeholder ("—") is not an aside. */
const ASIDE = /(^|\s)[—–](\s|$)/;
const DASH_ONLY = /^[—–]$/;

/** What a JSX text node renders, by React's whitespace rule: a run of
 *  whitespace that holds a line break is dropped at the start and end of a
 *  line; whitespace on one line is kept. */
function jsxRendered(raw: string): string {
  const lines = raw.split(/\r\n|\n|\r/);
  if (lines.length === 1) return raw;
  return lines
    .map((l, i) => {
      let s = l;
      if (i !== 0) s = s.replace(/^[ \t]+/, "");
      if (i !== lines.length - 1) s = s.replace(/[ \t]+$/, "");
      return s;
    })
    .filter((s) => s.length > 0)
    .join(" ");
}

/** A JSX child that is only a dash, read together with its siblings. On its
 *  own it looks like a placeholder, but `{" "}\n—{" "}` renders "x — y": a
 *  space next to it (from a `{" "}` sibling, or whitespace the node keeps) makes
 *  it an aside. A dash that is the element's whole text stays a placeholder. */
function jsxDashAsides(rel: string, src = readSrc(rel)): string[] {
  const file = ts.createSourceFile(rel, src, ts.ScriptTarget.Latest, true, rel.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const out: string[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isJsxElement(node) || ts.isJsxFragment(node)) {
      let joined = "";
      const parts: Array<{ start: number; text: string; node: ts.Node }> = [];
      for (const child of node.children) {
        let text: string;
        if (ts.isJsxText(child)) text = jsxRendered(child.getText(file));
        else if (ts.isJsxExpression(child) && !child.expression) text = ""; // {/* a comment */}
        else if (
          ts.isJsxExpression(child) &&
          child.expression &&
          (ts.isStringLiteral(child.expression) || ts.isNoSubstitutionTemplateLiteral(child.expression))
        )
          text = child.expression.text;
        else text = "\u0001"; // an element or a computed value: never a space
        parts.push({ start: joined.length, text, node: child });
        joined += text;
      }
      if (!DASH_ONLY.test(joined.trim())) {
        for (const p of parts) {
          const dash = p.text.trim();
          if (!DASH_ONLY.test(dash)) continue; // longer copy is the per-piece check's job
          const at = p.start + p.text.indexOf(dash);
          const spaced = (c: string | undefined) => c !== undefined && /\s/.test(c);
          if (spaced(joined[at - 1]) || spaced(joined[at + 1])) {
            const line = file.getLineAndCharacterOfPosition(p.node.getStart(file)).line + 1;
            out.push(`${rel}:${line}: ${joined.replace(/\u0001/g, "{…}").trim().slice(0, 80)}`);
          }
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return out;
}

function asides(rel: string, src?: string): string[] {
  const text = src ?? readSrc(rel);
  // A string that is exactly a dash ("—") is a placeholder; " — " is not. A
  // JSX text node that trims to a dash is judged with its siblings by
  // jsxDashAsides (its raw text carries the source's indentation).
  const placeholder = (p: { text: string; jsx: boolean }) =>
    p.jsx ? DASH_ONLY.test(p.text.trim()) : DASH_ONLY.test(p.text);
  return [
    ...copyPieces(rel, text)
      .filter((p) => !placeholder(p) && ASIDE.test(p.text))
      .map((p) => `${rel}:${p.line}: ${p.text.trim().slice(0, 80)}`),
    ...jsxDashAsides(rel, text),
  ];
}

const MISSION_DIR = "components/agents/mission";
const FILES = [
  ...readdirSync(path.join(ROOT, MISSION_DIR))
    .filter((f) => /\.tsx?$/.test(f))
    .map((f) => `${MISSION_DIR}/${f}`),
  "lib/mission.ts",
  "components/workflow/starters.ts",
  // v1.329.0 wave 8 (J4): the mission page's own route file and the worlds
  // helpers its sections read (countsLine, REMOTE_SEES, the waiting words).
  "lib/agentWorlds.ts",
  "app/agents/page.tsx",
];

/* Wave 8 (J5): the dash guard reaches the whole workflow editor and the
   notification bell too. Wave 9 (K3): it also reads the Workflows page itself
   (app/workflows/page.tsx), whose header, Build with chat intro and Templates
   line still carried dash asides on screen. */
const WORKFLOW_DIR = "components/workflow";
const WORKFLOW_FILES = readdirSync(path.join(ROOT, WORKFLOW_DIR))
  .filter((f) => /\.tsx?$/.test(f))
  .map((f) => `${WORKFLOW_DIR}/${f}`);
const COPY_FILES = [
  ...FILES,
  ...WORKFLOW_FILES.filter((f) => !FILES.includes(f)),
  "app/workflows/page.tsx",
  "components/NotificationBell.tsx",
];

/* Wave 9 (K3): the hue / half-pixel rule now reaches the whole workflow
   editor too: the Workflows page and every file in components/workflow,
   which moved onto tone tokens and whole pixels this wave. The bell is
   still read for copy only. */
const CALM_FILES = [
  ...FILES,
  ...WORKFLOW_FILES.filter((f) => !FILES.includes(f)),
  "app/workflows/page.tsx",
];

const HUES =
  "slate|gray|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose";
const PROPS =
  "text|bg|border|ring|from|via|to|fill|stroke|outline|decoration|divide|placeholder|shadow|caret|accent";
const LITERAL_HUE = new RegExp(
  `(?<![\\w-])(?:${PROPS})(?:-[trblxyse])?-(?:${HUES})-\\d{2,3}(?:\\/(?:\\d{1,3}|\\[[0-9.]+\\]))?`,
  "g",
);
const HALF_PIXEL = /text-\[\d+\.5px\]/g;

describe("the mission files, lib/mission and the workflow starters keep to the calm rules", () => {
  it("covers every file in the mission folder", () => {
    for (const f of [
      "MissionComposer.tsx",
      "MissionScreen.tsx",
      "ProjectTeamPanel.tsx",
      "ProjectTeams.tsx",
      "LiveActivity.tsx",
      "RoomTranscript.tsx",
      "AgentCards.tsx",
      "MissionOutput.tsx",
      "MissionRail.tsx",
      "ProjectTeams.tsx",
      "ProjectWork.tsx",
      "TeamScreen.tsx",
    ]) {
      expect(FILES).toContain(`${MISSION_DIR}/${f}`);
    }
    expect(FILES).toContain("lib/agentWorlds.ts");
    expect(FILES).toContain("app/agents/page.tsx");
  });

  it("no spaced em or en dash in any user-visible string or JSX text", () => {
    expect(COPY_FILES.flatMap((rel) => asides(rel))).toEqual([]);
  });

  it("the dash guard covers the workflow editor and the bell (wave 8)", () => {
    for (const f of [
      "NodeInspector.tsx",
      "SavedWorkflows.tsx",
      "StepNode.tsx",
      "TriggerInspector.tsx",
      "WorkflowCanvas.tsx",
      "agents.ts",
      "starters.ts",
    ]) {
      expect(COPY_FILES).toContain(`${WORKFLOW_DIR}/${f}`);
    }
    expect(COPY_FILES).toContain("components/NotificationBell.tsx");
    // Wave 9 (K3): the Workflows page itself is read too.
    expect(COPY_FILES).toContain("app/workflows/page.tsx");
    const page = copyPieces("app/workflows/page.tsx").map((p) => p.text).join("\n");
    expect(page).toContain(
      "Wire agents into a visual, multi-step workflow, then run it. Describe one below, or send a terminal session here with its → Workflow button.",
    );
    expect(page).toContain("No saved workflows yet. Start from one of these. Loading a");
    expect(page).toContain("Describe a process and the agent builds the steps into the editor above. For example,");
    // Anti-vacuity: the plain sentences this wave wrote are read as copy.
    const canvas = copyPieces(`${WORKFLOW_DIR}/WorkflowCanvas.tsx`).map((p) => p.text).join("\n");
    expect(canvas).toContain("An example to start from. Change the steps, or Load a saved one.");
    const bell = copyPieces("components/NotificationBell.tsx").map((p) => p.text).join("\n");
    expect(bell).toContain("Your objective finished, but something needs you");
    expect(bell).toContain("Your objective finished. Check what it did");
    expect(bell).toContain("The run is waiting for you.");
  });

  it("the guard reads copy and skips comments (anti-vacuity)", () => {
    // Real copy is read: the plain sentences this wave wrote are found.
    const composer = copyPieces(`${MISSION_DIR}/MissionComposer.tsx`).map((p) => p.text).join("\n");
    expect(composer).toContain("This daemon is older than the mission screen. Restart Iron Jarvis to update it.");
    const screenText = copyPieces(`${MISSION_DIR}/MissionScreen.tsx`).map((p) => p.text).join("\n");
    expect(screenText).toContain("Just say what you need.");
    // A dash aside in a string, a template, or JSX text is caught; the same
    // dash in a comment is not.
    const probe = [
      "// a comment — fine",
      "/* block — fine */",
      'const a = "one — two";',
      "const b = `x ${1} — y`;",
      "const c = <p>{/* jsx comment — fine */}left — right</p>;",
      'const d = <span>— a caption</span>;',
      'const e = "—";',
      // Wave 8 (J5): a dash on its own between {" "} pieces renders as an
      // aside ("It wants to run x — the run is waiting"), even across lines.
      'const f = <p>run <b>x</b>{" "}',
      "  —{\" \"}",
      '  {"the run is waiting."}</p>;',
      'const g = <p>left {"—"} right</p>;',
      "const h = <p>{a} — </p>;",
      'const i = <span>{a ?? "—"}</span>;',
      "const j = <td>—</td>;",
      "const k = <td>\n  —\n</td>;",
      "const l = <p>{a}—{b}</p>;",
      'const m = " — ";',
      "const n = <td> — </td>;",
    ].join("\n");
    expect(asides("probe.tsx", probe).map((s) => s.split(":")[1]).sort((x, y) => Number(x) - Number(y))).toEqual([
      // f's lone dash (line 9), g's {"—"} (11), h's trailing " — " (12) and
      // m's " — " string (19). i, j, k and n are placeholders (a dash that is
      // the cell's whole text), l is glued to its neighbours.
      "3", "4", "5", "6", "9", "11", "12", "19",
    ]);
  });

  it("no literal hue and no half-pixel size (the mission files and, since wave 9, the whole workflow editor)", () => {
    // Anti-vacuity: the workflow editor's files are in the list.
    for (const f of [
      "app/workflows/page.tsx",
      `${WORKFLOW_DIR}/WorkflowCanvas.tsx`,
      `${WORKFLOW_DIR}/NodeInspector.tsx`,
      `${WORKFLOW_DIR}/TriggerInspector.tsx`,
      `${WORKFLOW_DIR}/StepNode.tsx`,
      `${WORKFLOW_DIR}/TriggerNode.tsx`,
      `${WORKFLOW_DIR}/SavedWorkflows.tsx`,
      `${WORKFLOW_DIR}/calm.tsx`,
    ]) {
      expect(CALM_FILES).toContain(f);
    }
    for (const rel of CALM_FILES) {
      const src = readSrc(rel);
      expect([...src.matchAll(LITERAL_HUE)].map((m) => m[0]), rel).toEqual([]);
      expect([...src.matchAll(HALF_PIXEL)].map((m) => m[0]), rel).toEqual([]);
    }
  });
});
