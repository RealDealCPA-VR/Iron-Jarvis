// Starter workflow catalog (v1.170.0 P7). Five REAL, runnable workflows the
// user can load onto the canvas with one click — the module's empty state used
// to be a blank graph and a prompt box, which teaches nothing about what a
// workflow can be (ask gates, notify steps, tool steps, verified outputs).
//
// Loading a starter dispatches the existing `ij:load-workflow` event — the
// SAME path the Load dropdown, the build-with-chat panel, and the terminal
// handoff use — so the canvas rebuilds its graph and NOTHING is saved until
// the user presses Save (suggest-don't-act, the repo doctrine).

// This module also hosts the workflows PAGE's pure helpers (isLiveRun,
// runBadgeTone, stepKindHint): a Next page file may export nothing beyond its
// default (the generated .next/types check rejects extra exports at build
// time), so the testable logic lives here where the test file can import it.

import {
  WORKFLOW_RUN_TERMINAL,
  type WorkflowRun,
  type WorkflowStep,
} from "@/lib/types";
import type { Tone } from "@/components/ui";
import type { StepExpect } from "@/components/workflow/agents";

/** Contract 8 (v1.170.0): deterministic post-step checks. ONE declaration —
 *  components/workflow/agents.ts (P6) owns the shape; re-exported here so the
 *  catalog and its consumers speak the same type instead of keeping a second
 *  copy that could drift (the exact WorkflowStep×5 disease this wave cures). */
export type { StepExpect } from "@/components/workflow/agents";

/** A starter step is the ONE shared step shape plus the optional `expect`
 *  block — the engine ignores unknown fields on old builds, so carrying it is
 *  additive. */
export type StarterStep = WorkflowStep & { expect?: StepExpect };

export interface StarterWorkflow {
  /** Def name the canvas Name field is prefilled with (user can rename). */
  name: string;
  /** Human title shown on the template card. */
  title: string;
  /** Saved-def description (travels with the load event). */
  description: string;
  /** One-line card blurb: why you'd reach for this one. */
  blurb: string;
  steps: StarterStep[];
}

/** The catalog. Every starter includes at least one ask or notify step so the
 *  human-in-the-loop kinds are discoverable, and exactly one carries `expect`
 *  so verified steps have a visible example. */
export const STARTERS: StarterWorkflow[] = [
  {
    name: "client-intake-triage",
    title: "Client intake triage",
    description:
      "Scan the client documents in this run's workspace (pin the client's project so it scans their folder), classify them, flag what's missing, and write an intake summary — with a human check before anything is written.",
    blurb: "Pin the client's project, run it: classified document list + gaps out — you approve the triage before the summary is written.",
    steps: [
      {
        // `.` = the run's workspace (the pinned project's folder when pinned,
        // a fresh temp dir otherwise). A subfolder like "uploads" does not
        // exist in either, so with on_failure halt it would kill the run at
        // step 1 on every default install — a starter must run out of the box.
        name: "List new client files",
        kind: "tool",
        tool: "list_folder",
        args: { path: "." },
        on_failure: "halt",
      },
      {
        name: "Triage the intake",
        kind: "agent",
        agent: "planner",
        task:
          "Review the client files listed in {{List new client files}}. Classify each document (W-2, 1099, K-1, prior-year return, ID, other), note anything missing for a complete return, and produce a short triage summary.",
      },
      {
        name: "Anything to correct?",
        kind: "ask",
        message:
          "Review the triage above — anything to add or correct before the intake summary is written? (Answer 'no' to continue as-is.)",
      },
      {
        name: "Write intake summary",
        kind: "agent",
        agent: "builder",
        task:
          "Using the triage in {{Triage the intake}} and my note in {{Anything to correct?}}, write intake-summary.md: documents received, what's missing, and the next steps.",
        expect: { files: ["intake-summary.md"] },
      },
      {
        name: "Tell me it's ready",
        kind: "notify",
        message: "Client intake triage finished — intake-summary.md is ready.",
      },
    ],
  },
  {
    name: "month-end-close",
    title: "Month-end close checklist",
    description:
      "Gather the month's statements, build a reconciliation checklist, pause for your approval of adjustments, then write the close memo.",
    blurb: "The close, as a repeatable run: gather → checklist → you approve the adjustments → close memo.",
    steps: [
      {
        name: "Gather statements",
        kind: "agent",
        agent: "researcher",
        task:
          "List the bank and credit-card statements and reports available for the month being closed, and flag any that are missing.",
      },
      {
        name: "Reconciliation checklist",
        kind: "agent",
        agent: "planner",
        task:
          "Build the month-end close checklist from {{Gather statements}}: reconciliations, accruals, prepaids, fixed assets, payroll tie-out — with a status and any proposed adjusting entry for each.",
      },
      {
        name: "Approve adjustments",
        kind: "ask",
        message:
          "Here's the close checklist with proposed adjusting entries. Approve them, or list the changes you want.",
      },
      {
        name: "Close memo",
        kind: "agent",
        agent: "builder",
        task:
          "Write close-memo.md summarizing the close: what reconciled, the adjustments approved in {{Approve adjustments}}, and the open items.",
        on_failure: "retry",
      },
      {
        name: "Notify",
        kind: "notify",
        message: "Month-end close run finished — close-memo.md is drafted.",
      },
    ],
  },
  {
    name: "weekly-status-digest",
    title: "Weekly status digest",
    description:
      "Collect what changed this week, draft a done / in-progress / blocked digest, have a reviewer tighten it, then send it to your destinations.",
    blurb: "A Friday digest that writes itself: collect → draft → review → notify. Pairs well with a weekly schedule.",
    steps: [
      {
        name: "Collect the week",
        kind: "agent",
        agent: "researcher",
        task:
          "Summarize what changed this week in the active project: recent files, notes, and open tasks.",
      },
      {
        name: "Draft the digest",
        kind: "agent",
        agent: "builder",
        task:
          "Turn {{Collect the week}} into a short status digest with four sections: done, in progress, blocked, next week.",
      },
      {
        // No on_failure:"skip" here: render_template resolves {{Review}} to
        // the step's summary REGARDLESS of status, so a skipped-over failure
        // would ship its error text to the user labeled as their digest. The
        // default (halt) stops the run before the notify instead.
        name: "Review",
        kind: "agent",
        agent: "reviewer",
        task:
          "Review the digest in {{Draft the digest}} for accuracy and tone; produce the final text.",
      },
      {
        name: "Send it",
        kind: "notify",
        message: "Weekly digest:\n{{Review}}",
      },
    ],
  },
  {
    name: "folder-batch-report",
    title: "Folder batch-process + notify",
    description:
      "List every document in a folder, extract the key fields from each, save one combined report, and get notified when it lands.",
    blurb: "Point it at a folder of documents; get back one batch-report.md and a ping when it's done.",
    steps: [
      {
        name: "Scan the folder",
        kind: "tool",
        tool: "list_folder",
        args: { path: "." },
        on_failure: "halt",
      },
      {
        name: "Process each file",
        kind: "agent",
        agent: "builder",
        task:
          "For each document found in {{Scan the folder}}, read it and extract the key fields into one combined summary table. Note any file that failed to read.",
      },
      {
        name: "Save the report",
        kind: "agent",
        agent: "builder",
        task:
          "Write batch-report.md from {{Process each file}} — one row per file, plus a section for anything that could not be read.",
      },
      {
        name: "Notify",
        kind: "notify",
        message: "Folder batch finished — batch-report.md is ready.",
      },
    ],
  },
  {
    name: "research-then-review",
    title: "Research, draft, review",
    description:
      "Research a topic, draft a one-page brief, pause for your steer, then have a reviewer produce the final version.",
    blurb: "The classic three-agent chain with a human checkpoint: research → draft → your steer → reviewed final.",
    steps: [
      {
        name: "Research",
        kind: "agent",
        agent: "researcher",
        task:
          "Research this topic (EDIT ME before running): collect the key facts, numbers, and sources.",
      },
      {
        name: "Draft",
        kind: "agent",
        agent: "builder",
        task: "Write a clear one-page brief from {{Research}}.",
      },
      {
        name: "Your steer",
        kind: "ask",
        message:
          "The draft is ready (see the Draft step). What should the reviewer emphasize, cut, or fix?",
      },
      {
        name: "Review",
        kind: "agent",
        agent: "reviewer",
        task:
          "Review {{Draft}} following my instruction in {{Your steer}}; produce the improved final version.",
      },
    ],
  },
];

/** Card summary line, e.g. "5 steps · asks you · notifies you · verified output". */
export function starterKindSummary(s: StarterWorkflow): string {
  const n = s.steps.length;
  const parts = [`${n} step${n === 1 ? "" : "s"}`];
  if (s.steps.some((st) => st.kind === "ask")) parts.push("asks you");
  if (s.steps.some((st) => st.kind === "notify")) parts.push("notifies you");
  if (s.steps.some((st) => st.expect)) parts.push("verified output");
  return parts.join(" · ");
}

/* ---- Workflows-page pure helpers (v1.170.0 P7) --------------------------- */

/** Non-terminal statuses (running / waiting / resuming) mean the record can
 *  still change under us — the run history keeps itself fresh while any
 *  exist. Absent/blank status is NOT live: a malformed row must never make
 *  the page poll forever. */
export function isLiveRun(r: Pick<WorkflowRun, "status">): boolean {
  const s = (r.status ?? "").toLowerCase();
  return s !== "" && !WORKFLOW_RUN_TERMINAL.has(s);
}

/** Explicit tones for the run statuses the shared STATUS_TONE map renders
 *  slate (invisible): `resuming` is live work (cyan, like running), `waiting`
 *  and `interrupted` need the user (amber). Everything else keeps the shared
 *  default — completed/failed/cancelled must NOT change. */
export function runBadgeTone(status: string | null | undefined): Tone | undefined {
  const s = (status ?? "").toLowerCase();
  if (s === "resuming") return "cyan";
  if (s === "waiting" || s === "interrupted") return "amber";
  return undefined;
}

/** Compact honesty hint for a non-agent step row ("what IS this step"). */
export function stepKindHint(d: Partial<WorkflowStep>): string | null {
  const kind = (d.kind ?? "agent").toLowerCase();
  if (kind === "tool") return d.tool ? `tool: ${d.tool}` : "tool";
  if (kind === "ask") return "asks you";
  if (kind === "notify") return "notify";
  return null;
}

/** The `ij:load-workflow` event detail for a starter — DEEP-copied so canvas
 *  edits can never mutate the shared catalog, and shaped like the terminal
 *  handoff (`steps` array; the canvas stringifies it itself). */
export function starterLoadDetail(s: StarterWorkflow): {
  name: string;
  description: string;
  steps: StarterStep[];
} {
  return JSON.parse(
    JSON.stringify({ name: s.name, description: s.description, steps: s.steps }),
  ) as { name: string; description: string; steps: StarterStep[] };
}
