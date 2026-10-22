/**
 * The questions a Build session asks the user (v1.369.0, IJ harness plan
 * Phase 6), each drawn in the composer's place by the chat's own DockAsk:
 *
 *   - the model's own question (`ask_user`: Claude Code's AskUserQuestion,
 *     Codex's request_user_input) — lettered choices the letter keys pick,
 *     a typed answer of the user's own, several picks where allowed, Enter
 *     sends and Esc skips (the card's OWN buttons, so a key runs a click);
 *   - the plan door (`exit_plan_mode`) — once-only by its NAME (a listing row
 *     carries no flags): Allow once and Deny only, never "Always" or a wider
 *     scope, and the plan it approves is on the card (the call's own plan, or
 *     the reply above when the call carries none);
 *   - a Build session's wider answer says "this session", not "this chat".
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, API_BASE: "http://localhost", ijToken: () => "", post: H.post };
});

import { DockAsk } from "@/components/chat/DockAsk";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { cardAnswer, questionAnswer } from "@/components/chat/AskUserCard";
import { collectDockAsks, dockAskKeyHint, type DockAskItem } from "@/lib/dockAsk";
import { decodeAskUser, type AskUserAsk } from "@/lib/buildSessions";

const ONE = decodeAskUser({
  id: "ask_1",
  call_id: "c1",
  questions: [{ id: "db", question: "Which database?", options: [{ label: "SQLite" }, { label: "Postgres" }] }],
}) as AskUserAsk;

const TWO = decodeAskUser({
  id: "ask_2",
  questions: [
    { id: "db", question: "Which database?", options: [{ label: "SQLite" }, { label: "Postgres" }] },
    { id: "extras", question: "Which extras?", multi: true, options: [{ label: "Auth" }, { label: "Billing" }] },
  ],
}) as AskUserAsk;

const asQuestion = (a: AskUserAsk): DockAskItem => ({ kind: "question", id: a.id, ask: a });

function renderDock(asks: DockAskItem[], over: Partial<Parameters<typeof DockAsk>[0]> = {}) {
  const props = {
    onAnswerElicitation: vi.fn(async () => ({ ok: true }) as const),
    onDecideSampling: vi.fn(async () => true),
    onAnswerQuestion: vi.fn(async () => null),
    armDelayMs: 0,
    ...over,
  };
  render(<DockAsk asks={asks} {...props} />);
  return props;
}

beforeEach(() => {
  H.post.mockReset();
  H.post.mockResolvedValue({});
});
afterEach(() => {
  cleanup();
  (document.activeElement as HTMLElement | null)?.blur?.();
});

/* ------------------------------------------------------- the model's question */

describe("the model's own question", () => {
  it("joins the dock's list after the approvals, and only while open", () => {
    const list = collectDockAsks({ approval: null, questions: [ONE, { ...TWO, outcome: "answered" }] });
    expect(list.map((a) => a.kind + ":" + a.id)).toEqual(["question:ask_1"]);
    expect(dockAskKeyHint("question")).toBe("Enter to send · Esc to skip");
  });

  it("a letter picks a choice, Enter sends it as the answer", async () => {
    const props = renderDock([asQuestion(ONE)]);
    const card = screen.getByTestId("ask-user-card");
    expect(card).toHaveTextContent("Which database?");
    fireEvent.keyDown(card, { key: "b" });
    expect(screen.getByTestId("ask-user-choice-db-B")).toHaveAttribute("aria-checked", "true");
    fireEvent.keyDown(card, { key: "Enter" });
    await waitFor(() => expect(props.onAnswerQuestion).toHaveBeenCalledWith("ask_1", "Postgres"));
  });

  it("a typed answer wins over a picked one; Esc skips (null), never sends", async () => {
    const props = renderDock([asQuestion(ONE)]);
    fireEvent.click(screen.getByTestId("ask-user-choice-db-A"));
    fireEvent.change(screen.getByTestId("ask-user-text-db"), { target: { value: "DuckDB" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(props.onAnswerQuestion).toHaveBeenCalledWith("ask_1", "DuckDB"));
    cleanup();
    const again = renderDock([asQuestion(ONE)]);
    fireEvent.keyDown(screen.getByTestId("ask-user-card"), { key: "Escape" });
    await waitFor(() => expect(again.onAnswerQuestion).toHaveBeenCalledWith("ask_1", null));
  });

  it("sending nothing asks for an answer instead of sending an empty one", async () => {
    const props = renderDock([asQuestion(ONE)]);
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Pick an answer or type one first.");
    expect(props.onAnswerQuestion).not.toHaveBeenCalled();
  });

  it("several questions answer keyed by id, several picks as a list", async () => {
    const props = renderDock([asQuestion(TWO)]);
    fireEvent.click(screen.getByTestId("ask-user-choice-db-A"));
    fireEvent.click(screen.getByTestId("ask-user-choice-extras-A"));
    fireEvent.click(screen.getByTestId("ask-user-choice-extras-B"));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(props.onAnswerQuestion).toHaveBeenCalledWith("ask_2", { db: "SQLite", extras: ["Auth", "Billing"] }),
    );
  });

  it("a refusal from the daemon stays on the card in its words, and the buttons come back", async () => {
    renderDock([asQuestion(ONE)], { onAnswerQuestion: vi.fn(async () => "That question is no longer open.") });
    fireEvent.click(screen.getByTestId("ask-user-choice-db-A"));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That question is no longer open.");
    expect(screen.getByRole("button", { name: "Send" })).not.toBeDisabled();
  });

  it("the answer shapes", () => {
    const q = TWO.questions[1];
    expect(questionAnswer(q, [1], "")).toEqual(["Billing"]);
    expect(questionAnswer(q, [], "Search")).toEqual(["Search"]);
    expect(questionAnswer(q, [], "")).toBe("");
    expect(cardAnswer(TWO.questions, {}, {})).toBeNull();
    expect(cardAnswer(TWO.questions, { db: [1] }, {})).toEqual({ db: "Postgres" });
  });
});

/* ------------------------------------------------------------- the plan door */

describe("the plan door (exit_plan_mode)", () => {
  const PLAN = { id: "apr_p", callId: "cp", tool: "exit_plan_mode", args: {} };

  it("with no plan in the call, the reply above is the plan, and it is shown on the card", () => {
    render(<ApprovalCard approval={PLAN} docked scope="session" planText={"1. Add the page\n2. Test it"} />);
    expect(screen.getByTestId("approval-plan-text")).toHaveTextContent("Add the page");
    cleanup();
    render(<ApprovalCard approval={PLAN} docked scope="session" />);
    expect(screen.getByTestId("approval-plan-above")).toHaveTextContent("The plan is the reply above.");
  });

  it("the call's own plan wins over the reply above", () => {
    render(
      <ApprovalCard
        approval={{ ...PLAN, args: { plan: "Rename the folder" } }}
        docked
        scope="session"
        planText="the reply"
      />,
    );
    expect(screen.getByTestId("approval-plan-text")).toHaveTextContent("Rename the folder");
    expect(screen.getByTestId("approval-plan-text")).not.toHaveTextContent("the reply");
  });

  it("is once-only BY NAME: Allow once and Deny, nothing wider, even when the row says it could be", async () => {
    render(
      <ApprovalCard approval={{ ...PLAN, canAlways: true, onceOnly: false }} docked scope="session" planText="Plan" />,
    );
    const buttons = screen.getAllByRole("button").map((b) => b.textContent?.trim());
    expect(buttons).toEqual(["Allow once", "Deny"]);
    expect(screen.getByRole("alertdialog")).toHaveTextContent(
      "Nothing changes until you answer. “Allow once” approves this plan and the session starts working on it; Deny keeps it planning.",
    );
    // A plan is not an action: no undo line, and it is not a local-only card.
    expect(screen.queryByTestId("approval-undo")).toBeNull();
    expect(screen.queryByTestId("approval-local-only")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Allow once" }));
    await waitFor(() => expect(H.post).toHaveBeenCalledWith("/chat/approvals/apr_p", { decision: "once" }));
  });

  it("a Build session's ordinary card offers the wider answer as this SESSION", () => {
    render(
      <ApprovalCard approval={{ id: "a1", callId: "c1", tool: "shell", args: { command: "npm test" } }} docked scope="session" />,
    );
    expect(screen.getByRole("button", { name: "Allow for this session" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /this chat/ })).toBeNull();
  });
});
