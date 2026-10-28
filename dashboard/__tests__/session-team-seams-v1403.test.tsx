/**
 * v1.403.0 integrate — the coding-session team's SEAM. The screen was built
 * against stand-ins of the contract; this file decodes what the REAL daemon
 * answered (recorded by tests/test_session_team_seams_v1403.py into
 * fixtures/session-team-seams-v1403.json) with the screen's own decoders, and
 * checks nothing the screen shows is dropped on the way.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import seams from "./fixtures/session-team-seams-v1403.json";
import { decodeReplyTeam, decodeSessionTeam, decodeTeamNames, leadDidLines } from "@/lib/sessionTeam";
import { decodeFinish } from "@/lib/buildSessions";
import { finishWords } from "@/lib/stepWords";
import { TeamReplyLine } from "@/components/harness/HarnessTeam";

describe("the real daemon's team answers decode on the screen", () => {
  it("keeps the session view's team: lead, workers, reviewer, states", () => {
    const team = decodeSessionTeam(seams.view.team);
    expect(team).not.toBeNull();
    expect(team!.workers.map((w) => w.name)).toEqual(["qwen3-8b"]);
    expect(team!.reviewer?.name).toBe("gpt-6-astra");
    expect(team!.workers[0].state).toBe("unavailable");
    expect(decodeTeamNames(seams.view.setup.team)).toEqual({ workers: ["qwen3-8b"], reviewer: "gpt-6-astra" });
  });

  it("uses the names from-model returned in the session's setup", () => {
    expect(seams.view.setup.team.workers).toContain(seams.from_model_worker.name);
    expect(seams.view.setup.team.reviewer).toBe(seams.from_model_reviewer.name);
    expect(seams.empty_folder).toEqual({ team: null });
  });

  it("keeps a team reply's record and says the lead's own work in plain words", () => {
    const t = decodeReplyTeam(seams.reply_team);
    expect(t).not.toBeNull();
    expect(t!.pieces).toBe(1);
    expect(leadDidLines(t!)).toEqual(["The team lead did this itself: Qwen3 8B is at its limit."]);
  });

  it("names models on the reply line the way the menu does, never by id", () => {
    render(<TeamReplyLine raw={seams.reply_team} />);
    const line = screen.getByTestId("harness-team-members").textContent ?? "";
    expect(line).toContain("lead Opus 5.5");
    expect(line).not.toContain("claude-opus-5-5");
  });

  it("reads two reviews from the finish record", () => {
    const f = decodeFinish(seams.finish);
    expect(f?.review?.rounds).toBe(2);
    expect(finishWords(f!)).toMatch(/twice/);
  });
});
