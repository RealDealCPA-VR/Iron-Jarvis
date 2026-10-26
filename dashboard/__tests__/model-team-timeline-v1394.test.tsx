/**
 * v1.394.0 (the long-jobs plan's Phase 5): the Timeline and the helper rows
 * of a coding session that used its MODEL TEAM.
 *
 * Fed with a REAL recorded reply (fixtures/model-team-timeline-v1394.json,
 * written by tests/test_model_team_live_v1394.py through the real Build route,
 * the real router and the fake Iron-Proxy): a session on Claude that handed a
 * reading task to a local member (qwen3:8b on this PC) and a coding task to a
 * Codex member. The plan's "done when": the Timeline shows THREE models — the
 * reply's own on its bar, each helper's on its row next to the member's name
 * — and each helper row says the model it ran on.
 */
import React from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { HarnessTimeline } from "@/components/harness/HarnessTimeline";
import { SavedHelperRows } from "@/components/harness/HarnessHelpers";
import { workerModelName } from "@/lib/buildHelpers";
import { decodeMessage, type BuildMessage, type BuildSessionView } from "@/lib/buildSessions";

const FIXTURE = JSON.parse(
  readFileSync(path.join(__dirname, "fixtures", "model-team-timeline-v1394.json"), "utf8").replace(/\r\n/g, "\n"),
) as { question: Record<string, unknown>; line: Record<string, unknown> };

const LINE = decodeMessage(FIXTURE.line) as BuildMessage;
const QUESTION = decodeMessage(FIXTURE.question) as BuildMessage;

function viewOf(messages: BuildMessage[]): BuildSessionView {
  return {
    id: "s1",
    messages,
    setup: { provider: "claude-cli", model: "claude-sonnet-4-5" },
  } as unknown as BuildSessionView;
}

afterEach(() => cleanup());

describe("a coding session that used its model team", () => {
  it("the Timeline names three models: the reply's, and each member's next to its name", () => {
    render(
      <div style={{ height: 600 }}>
        <HarnessTimeline view={viewOf([QUESTION, LINE])} live={null} models={[]} />
      </div>,
    );
    const labels = screen
      .getAllByTestId("timeline-row")
      .map((r) => r.querySelector("span[title]")?.getAttribute("title") ?? "");
    const local = workerModelName("ollama", "qwen3:8b", []);
    const codex = workerModelName("codex-cli", "subscription", []);
    const claude = workerModelName("claude-cli", "claude-sonnet-4-5", []);
    expect(labels).toContain(claude);
    expect(labels).toContain(`Your local-reader agent · ${local}`);
    expect(labels).toContain(`Your codex-coder agent · ${codex}`);
    expect(new Set([claude, local, codex]).size).toBe(3);
  });

  it("each saved helper row says the model it ran on", () => {
    render(<SavedHelperRows raw={(FIXTURE.line as { helpers: unknown }).helpers} />);
    const rows = screen.getAllByTestId("helper-row");
    expect(rows).toHaveLength(2);
    const said = screen.getAllByTestId("helper-model").map((n) => n.textContent);
    expect(said).toEqual([
      `On ${workerModelName("ollama", "qwen3:8b", [])}`,
      `On ${workerModelName("codex-cli", "subscription", [])}`,
    ]);
  });
});
