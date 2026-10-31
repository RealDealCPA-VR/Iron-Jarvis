/**
 * v1.414.0 — a project's CODING SESSIONS (Chat › Code and Build › Code) on
 * its page and its world. The daemon finds them by folder and sends
 * `coding_sessions`; this side decodes them (whitelist), says each state in
 * words and opens each in ITS OWN view through that view's producer. Each
 * world list (Waiting on you, Completed) names a coding session as one.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

vi.mock("@/components/agents/world/WorldBoard", () => ({
  WorldBoard: () => <div data-testid="world-board-stub" />,
}));

import { ProjectCodingSessions } from "@/components/projects/ProjectCodingSessions";
import { ProjectWork } from "@/components/agents/mission/ProjectWork";
import { WaitingList } from "@/components/agents/world/WaitingList";
import { CompletedList } from "@/components/agents/world/CompletedList";
import { codingSessionHref, codingStateWords, decodeCodingSessions } from "@/lib/codingSessions";
import { buildSessionHref } from "@/lib/buildLinks";
import { buildCodeHref } from "@/lib/buildApp";
import { waitingKindLabel } from "@/lib/agentWorlds";
import { whereWord } from "@/lib/preferences";

afterEach(cleanup);

/** Two rows as the daemon sends them (projects/coding_sessions.public). */
const WIRE = [
  {
    kind: "coding_session",
    view: "chat_code",
    id: "chat_1",
    key: "coding:chat_code:chat_1",
    title: "Fix the invoice export",
    tool: "",
    state: "waiting",
    outcome: null,
    updated_at: "2026-10-30T10:00:00+00:00",
    link: "/chat?view=code&session=chat_1",
    local_only: false,
    folder: "C:\\acme\\src",
  },
  {
    kind: "coding_session",
    view: "build_code",
    id: "app_2",
    key: "coding:build_code:app_2",
    title: "Add the CSV test",
    tool: "Codex",
    state: "finished",
    outcome: "failed",
    updated_at: "2026-10-30T09:00:00+00:00",
    link: "/terminals?view=code&code=app_2",
    local_only: false,
    folder: "C:\\acme",
  },
];

describe("lib/codingSessions", () => {
  it("whitelists the wire: an unknown view or a row with no id dies; an unknown state reads idle", () => {
    const rows = decodeCodingSessions([
      ...WIRE,
      { view: "somewhere_else", id: "x" },
      { view: "chat_code", id: "" },
      { view: "chat_code", id: "c9", state: "exploded" },
      "nonsense",
      null,
    ]);
    expect(rows.map((r) => r.id)).toEqual(["chat_1", "app_2", "c9"]);
    expect(rows[2].state).toBe("idle");
    expect(decodeCodingSessions(undefined)).toEqual([]);
    expect(decodeCodingSessions({ not: "a list" })).toEqual([]);
  });

  it("opens each in its own view through that view's producer — the daemon's exact strings", () => {
    // Pinned on both sides: projects/coding_sessions.CHAT_CODE_HREF / BUILD_CODE_HREF
    // (tests/test_project_coding_sessions_v1414.py pins the same literals).
    expect(buildSessionHref("chat_1")).toBe("/chat?view=code&session=chat_1");
    expect(buildCodeHref("app_2")).toBe("/terminals?view=code&code=app_2");
    const [a, b] = decodeCodingSessions(WIRE);
    expect(codingSessionHref(a)).toBe(WIRE[0].link);
    expect(codingSessionHref(b)).toBe(WIRE[1].link);
  });

  it("says each state in words", () => {
    expect(codingStateWords({ state: "working", outcome: "" })).toBe("Working");
    expect(codingStateWords({ state: "waiting", outcome: "" })).toBe("Waiting for you");
    expect(codingStateWords({ state: "finished", outcome: "completed" })).toBe("Finished");
    expect(codingStateWords({ state: "finished", outcome: "failed" })).toBe("Failed");
    expect(codingStateWords({ state: "finished", outcome: "cancelled" })).toBe("Stopped");
    expect(codingStateWords({ state: "idle", outcome: "" })).toBe("No reply yet");
  });
});

describe("ProjectCodingSessions", () => {
  it("lists every coding session with its tool, state and an Open into its own view", () => {
    render(<ProjectCodingSessions items={WIRE} />);
    const list = screen.getByTestId("project-coding-sessions");
    expect(within(list).getByText("Coding sessions")).toBeTruthy();
    const a = screen.getByTestId("project-coding-session-chat_1");
    expect(a.getAttribute("href")).toBe("/chat?view=code&session=chat_1");
    expect(a.getAttribute("data-state")).toBe("waiting");
    expect(a.textContent).toContain("Fix the invoice export");
    expect(a.textContent).toContain("Waiting for you");
    const b = screen.getByTestId("project-coding-session-app_2");
    expect(b.getAttribute("href")).toBe("/terminals?view=code&code=app_2");
    expect(b.textContent).toContain("Codex · ");
    expect(b.textContent).toContain("Failed");
  });

  it("renders nothing at all for a project with none (or an older daemon)", () => {
    const { container } = render(<ProjectCodingSessions items={[]} />);
    expect(container.innerHTML).toBe("");
    const { container: c2 } = render(<ProjectCodingSessions items={undefined} />);
    expect(c2.innerHTML).toBe("");
  });
});

describe("the world's lists name a coding session as one", () => {
  it("Waiting on you: 'Coding session' and the daemon's link", () => {
    expect(waitingKindLabel("coding_session")).toBe("Coding session");
    render(
      <WaitingList
        projectId="p1"
        items={[
          {
            id: "chat_1",
            kind: "coding_session",
            title: "Fix the invoice export",
            agent: "",
            since: "2026-10-30T10:00:00+00:00",
            link: "/chat?view=code&session=chat_1",
            reason: "it is waiting for your answer",
          },
        ]}
      />,
    );
    const row = screen.getByTestId("world-waiting-item-chat_1");
    expect(row.textContent).toContain("Coding session");
    expect(row.getAttribute("href")).toBe("/chat?view=code&session=chat_1");
  });

  it("Completed: the row says it is a coding session, with its tool", () => {
    render(
      <CompletedList
        projectId="p1"
        items={[
          {
            id: "app_2",
            kind: "coding_session",
            title: "Add the CSV test",
            agent: "Codex",
            finished_at: "2026-10-30T09:00:00+00:00",
            outcome: "failed",
            link: "/terminals?view=code&code=app_2",
          },
          { id: "s1", kind: "session", title: "A run", agent: "builder", outcome: "completed", link: "/sessions/s1" },
        ]}
      />,
    );
    const coding = screen.getByTestId("world-completed-item-app_2");
    expect(coding.textContent).toContain("Coding session · Codex");
    expect(within(coding).getByText(/Open/).closest("a")?.getAttribute("href")).toBe(
      "/terminals?view=code&code=app_2",
    );
    expect(screen.getByTestId("world-completed-item-s1").textContent).not.toContain("Coding session");
  });

  it("the world's Board lists the project's coding sessions above its board", () => {
    render(<ProjectWork projectId="p1" waiting={[]} completed={[]} coding={WIRE} />);
    fireEvent.click(screen.getByTestId("project-tab-board"));
    const board = screen.getByTestId("mission-project-work");
    expect(within(board).getByTestId("project-coding-sessions")).toBeTruthy();
    expect(within(board).getByTestId("world-board-stub")).toBeTruthy();
  });
});

describe("a correction said in a coding session", () => {
  it("is quoted as said in a coding session", () => {
    expect(whereWord("code")).toBe("coding session");
  });
});
