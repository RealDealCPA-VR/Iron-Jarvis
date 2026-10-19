/**
 * v1.354.0 (product-readiness G20): the run page's words, on their own.
 *
 * - runTitle: the first non-blank line, plain text, clipped; the whole task
 *   only when the title dropped something.
 * - runModelWords: a model server by the model menu's name; mock and the
 *   built-in providers exactly as before (the v1.314.0 pins).
 * - liveEventWords: never JSON; a tool in the chat's step words.
 * - findContinuation: follows ONLY the run the restart Continue started.
 * - phaseLabel: ONE table, shared by the chat page and the run page.
 * - lib/eventWords is the Time-travel feed's table, word for word (the feed
 *   re-exports it once the integrator moves it; until then this pin keeps
 *   the two copies from drifting).
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
  askedText,
  CHAT_RECAP_SEP,
  findContinuation,
  liveEventWords,
  MISSION_MEMBER_ORIGIN,
  offersRestartContinue,
  RESTART_SUMMARY,
  runModelWords,
  runTitle,
  RUN_TITLE_FALLBACK,
  RUN_TITLE_MAX,
  shownSummary,
} from "@/components/sessions/runPageWords";
import { PHASE_LABEL, phaseLabel } from "@/lib/stepWords";
import { EVENT_WORDS, humanSummary } from "@/lib/eventWords";
import * as feed from "@/components/TimeTravelFeed";
import { CONTINUE_AFTER_RESTART } from "@/components/InterruptedJobs";
import { interruptedLabel } from "@/components/sessions/SessionStatusBadge";
import type { IJEvent } from "@/lib/types";

const ROOT = path.resolve(__dirname, "..");
const src = (rel: string) => readFileSync(path.join(ROOT, rel), "utf8").replace(/\r\n/g, "\n");

const ev = (type: string, payload: Record<string, unknown> = {}): IJEvent =>
  ({ id: "e", type, ts: "2026-10-18T09:00:00Z", session_id: "s", payload }) as unknown as IJEvent;

describe("runTitle", () => {
  it("the first non-blank line, as plain text", () => {
    expect(runTitle("\n\n  ## Summarise the **lease**  \nthen email it")).toEqual({
      title: "Summarise the lease",
      full: "Summarise the lease then email it",
    });
  });
  it("a one-line task needs no hint", () => {
    expect(runTitle("Rename the scans")).toEqual({ title: "Rename the scans", full: null });
  });
  it("a long line is clipped with an ellipsis, and the whole is the hint", () => {
    const long = "word ".repeat(40).trim();
    const t = runTitle(long);
    expect(t.title.length).toBeLessThanOrEqual(RUN_TITLE_MAX);
    expect(t.title.endsWith("…")).toBe(true);
    expect(t.full).toBe(long);
  });
  it("nothing asked reads 'Run'", () => {
    expect(runTitle("")).toEqual({ title: RUN_TITLE_FALLBACK, full: null });
    expect(runTitle(null)).toEqual({ title: "Run", full: null });
  });
});

describe("runModelWords", () => {
  const catalog = [
    { provider: "fleet-a1b2", model: "llama-3-70b", name: "DGX Spark" },
    { provider: "fleet-custom", model: "qwen", name: "Office box" },
    { provider: "ollama", model: "llama3.2", name: "llama3.2" },
  ];
  it("a fleet node by its name", () => {
    expect(runModelWords("fleet-a1b2", "llama-3-70b", catalog)).toBe("DGX Spark · llama-3-70b");
  });
  it("the config-seeded endpoint by its fleet twin's name", () => {
    expect(runModelWords("custom", "qwen", catalog)).toBe("Office box · qwen");
  });
  it("the demo model and the built-in providers read exactly as before", () => {
    expect(runModelWords("mock", "mock-1", catalog)).toBe("Demo model (scripted)");
    expect(runModelWords("openai", "gpt-x", catalog)).toBe("OpenAI · gpt-x");
    // A detected Ollama row names its MODEL in `name`; that is never a place.
    expect(runModelWords("ollama", "qwen3", catalog)).not.toContain("llama3.2");
  });
  it("no catalog yet: the provider's own word, never a crash", () => {
    expect(runModelWords("fleet-a1b2", "m", null)).toBe("fleet-a1b2 · m");
  });
});

describe("liveEventWords — never the payload as JSON", () => {
  it("a tool step is the chat's step phrase; a failure says so", () => {
    expect(liveEventWords(ev("tool.executed", { tool: "read_file", ok: true }))).toEqual({
      label: "Read · read_file",
      detail: "",
    });
    expect(liveEventWords(ev("tool.executed", { tool: "read_file", ok: false })).detail).toBe("failed");
  });
  it("a state change in a sentence; a finished session in the one status table's words", () => {
    expect(liveEventWords(ev("agent.state_changed", { from: "running", to: "completed" })).detail).toBe(
      "Agent went from running to completed",
    );
    expect(liveEventWords(ev("session.completed", { status: "cancelled" }))).toEqual({
      label: "Session finished",
      detail: "Stopped",
    });
  });
  it("a routed model names the provider in words", () => {
    expect(liveEventWords(ev("provider.routed", { provider: "openai", model: "gpt-x" }))).toEqual({
      label: "Picked a model for this turn",
      detail: "OpenAI · gpt-x",
    });
  });
  it("an event nobody has words for is its own name, and its payload never shows", () => {
    const w = liveEventWords(ev("brand.new_thing", { secret: "shh", n: 2 }));
    expect(w).toEqual({ label: "brand.new_thing", detail: "" });
  });
  it("a plan step keeps the chat's progress words (stepLabel)", () => {
    expect(liveEventWords(ev("plan.step_completed", { run_id: "r9", index: 0, ok: true })).label).toBe("Step 1 done");
  });
});

describe("findContinuation", () => {
  const run = { id: "s1", workspace: "C:/w", since: Date.parse("2026-10-18T10:00:00Z") };
  const row = (over: Record<string, unknown>) => ({
    id: "s2",
    task: `${CONTINUE_AFTER_RESTART}\n\n[Continuing an earlier session.]`,
    workspace_path: "C:/w",
    created_at: "2026-10-18T10:00:01",
    ...over,
  });
  it("follows the run the restart Continue just started in this run's folder", () => {
    expect(findContinuation([row({})], run)).toBe("s2");
  });
  it("never this run, another folder, a different ask, or an older run", () => {
    expect(findContinuation([row({ id: "s1" })], run)).toBeNull();
    expect(findContinuation([row({ workspace_path: "C:/other" })], run)).toBeNull();
    expect(findContinuation([row({ task: "a typed follow-up" })], run)).toBeNull();
    expect(findContinuation([row({ created_at: "2026-10-18T09:00:00" })], run)).toBeNull();
    expect(findContinuation(null, run)).toBeNull();
    expect(findContinuation([row({})], { ...run, workspace: "" })).toBeNull();
  });
});

describe("one phase table", () => {
  it("phaseLabel says the words; an unknown phase reads as itself", () => {
    expect(phaseLabel("planning")).toBe("Planning the work…");
    expect(phaseLabel("running")).toBe("Working…");
    expect(phaseLabel("brand_new")).toBe("brand_new");
    expect(Object.keys(PHASE_LABEL).sort()).toEqual(["assembling", "planning", "running", "verifying"]);
  });
  it("the chat page and the run page both read it — neither keeps its own table", () => {
    const chat = src("app/chat/page.tsx");
    expect(chat).toContain('import { phaseLabel } from "@/lib/stepWords";');
    expect(chat).not.toContain("const PHASE_LABEL");
    expect(chat).toContain("phaseLabel(runStream.phase.phase)");
    const page = src("app/sessions/[id]/page.tsx");
    expect(page).toContain("phaseLabel(runStream.phase.phase)");
  });
});

describe("lib/eventWords is the Time-travel feed's table", () => {
  it("word for word", () => {
    expect(EVENT_WORDS).toEqual(feed.EVENT_WORDS);
  });
  it("the same sentence for every summary shape", () => {
    for (const s of [
      "provider.routed brain",
      "provider.routed",
      "agent running→completed",
      "openai/gpt-x · 12+40 tok",
      "mock/mock-1 · 1+2 tok",
      "weird.new_kind x",
      "",
      "plain words",
    ]) {
      expect(humanSummary(s), s).toBe(feed.humanSummary(s));
    }
  });
});

describe("the Interrupted label", () => {
  it("a failed run a restart tagged reads 'Interrupted'; nothing else does", () => {
    expect(interruptedLabel({ status: "failed", interrupted_at: "2026-10-18T09:00:00" })).toBe("Interrupted");
    expect(interruptedLabel({ status: "failed", interrupted_at: null })).toBeNull();
    expect(interruptedLabel({ status: "failed" })).toBeNull();
    expect(interruptedLabel({ status: "completed", interrupted_at: "2026-10-18T09:00:00" })).toBeNull();
  });
});

/* ---- v1.354.0 review ------------------------------------------------------ */
describe("askedText — what the user asked, without the wrapping", () => {
  it("drops the chat's recap; a '---' inside the recap is not the cut", () => {
    const task =
      "Conversation so far:\nUser: hi\nAssistant: a\n\n---\n\nb\n" + CHAT_RECAP_SEP + "Rename the scans\nAttached file: C:/x.pdf";
    expect(askedText(task)).toBe("Rename the scans\nAttached file: C:/x.pdf");
    expect(runTitle(task).title).toBe("Rename the scans");
  });
  it("drops a picked skill's directive line", () => {
    expect(runTitle('Use the "tax-prep" skill for this — load it with skill_load first.\n\nFile the 1040').title).toBe(
      "File the 1040",
    );
  });
  it("drops the continue door's model-facing recap from the whole-task hint", () => {
    const t = runTitle("make it shorter\n\n[Continuing an earlier session. Original task: 'x'. Prior result: y]");
    expect(t).toEqual({ title: "make it shorter", full: null });
  });
  it("an unwrapped task is untouched (a task that merely mentions the words stays)", () => {
    expect(askedText("Summarise\n\n---\n\nthe notes")).toBe("Summarise\n\n---\n\nthe notes");
    expect(runTitle("Explain 'Conversation so far:' in the docs").title).toBe("Explain 'Conversation so far:' in the docs");
  });
  it("the wrapping it strips is exactly what the chat page and the continue door write", () => {
    const chat = src("app/chat/page.tsx");
    // The needles are SOURCE text: a backslash and an "n", never a newline.
    expect(chat).toContain("return `Conversation so far:\\n${lines.join(\"\\n\")}`;");
    expect(chat).toContain("const openingTask = recap ? `${recap}\\n\\n---\\n\\n${task}` : task;");
    expect(chat).toContain('`Use the "${activeSkill}" skill for this — load it with skill_load first.\\n\\n`');
    expect(CHAT_RECAP_SEP).toBe("\n\n---\n\n");
    const orch = readFileSync(path.join(ROOT, "..", "src", "iron_jarvis", "agents", "orchestrator.py"), "utf8").replace(
      /\r\n/g,
      "\n",
    );
    expect(orch).toContain('f"{message}\\n\\n[Continuing an earlier session. Original task: "');
    expect(orch).toContain(`s.summary = "${RESTART_SUMMARY}"`);
  });
});

describe("a run a restart cut off — the offer and the summary", () => {
  const cut = { status: "failed", interrupted_at: "2026-10-18T09:00:00", origin: null };
  it("Continue is offered exactly where the bell offers it", () => {
    expect(offersRestartContinue(cut, [{ parent_id: null }])).toBe(true);
    expect(offersRestartContinue({ ...cut, origin: "job:mission" }, [])).toBe(true);
    expect(offersRestartContinue({ ...cut, origin: MISSION_MEMBER_ORIGIN }, [])).toBe(false);
    expect(offersRestartContinue(cut, [{ parent_id: null }, { parent_id: "run_p" }])).toBe(false);
    expect(offersRestartContinue({ ...cut, interrupted_at: null }, [])).toBe(false);
    expect(offersRestartContinue({ ...cut, status: "completed" }, [])).toBe(false);
  });
  it("the stock summary is hidden under the line, and in words once answered", () => {
    expect(shownSummary(RESTART_SUMMARY, true)).toBeNull();
    expect(shownSummary(RESTART_SUMMARY, false)).toBe("Stopped when Iron Jarvis restarted.");
    expect(shownSummary("Wrote summary.md", true)).toBe("Wrote summary.md");
    expect(shownSummary("", false)).toBeNull();
    expect(shownSummary(null, false)).toBeNull();
  });
});
