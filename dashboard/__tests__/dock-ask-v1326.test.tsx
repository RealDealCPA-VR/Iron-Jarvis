/**
 * Calm chat W1-6 (v1.326.0) — a question for you takes the composer's place.
 *
 * The pure half (lib/dockAsk) and the card (components/chat/DockAsk):
 *  - one list of open questions, in answer order; an ended app question is a
 *    record, not a question; the one on screen stays first while it is open;
 *  - "1 of N" when several wait;
 *  - Enter presses the main answer and Esc the "no", only from inside the
 *    card or with nothing focused; never from a field (Enter or Esc) or a
 *    button (Enter), never with another field on the page focused, never in
 *    the first moment after a question appears;
 *  - a key presses the card's OWN button, so it runs what a click runs;
 *  - the card takes the caret from the (hidden) composer, and gives it back.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const H = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("@/lib/api", async (orig) => {
  const real = await orig<typeof import("@/lib/api")>();
  return { ...real, API_BASE: "http://localhost", ijToken: () => "", post: H.post };
});

import { DockAsk } from "@/components/chat/DockAsk";
import {
  collectDockAsks,
  dockAskKeyHint,
  dockKeyAction,
  withHeadFirst,
  type DockAskItem,
} from "@/lib/dockAsk";
import type { McpElicitationAsk, McpSamplingAsk } from "@/lib/mcpInteract";
import type { PendingApproval } from "@/lib/useChatStream";

const APPROVAL: PendingApproval = { id: "apr_1", callId: "c1", tool: "shell", args: { command: "git status" } };
const APPROVAL_2: PendingApproval = { id: "apr_2", callId: "c2", tool: "write_file", args: { path: "a.txt" } };
const ELICIT: McpElicitationAsk = {
  kind: "elicitation",
  id: "e1",
  callId: "c9",
  pack: "Weather",
  message: "Which city?",
  fields: [{ name: "city", type: "string", title: "City", description: "", required: true }],
};
const SAMPLE: McpSamplingAsk = {
  kind: "sampling",
  id: "s1",
  callId: "c8",
  pack: "Notes",
  system: "",
  messages: [{ role: "user", text: "Sum this up" }],
  more: 0,
  maxTokens: null,
  model: "anthropic",
  modelId: "claude-x",
};

const asApproval = (a: PendingApproval): DockAskItem => ({ kind: "approval", id: a.id, approval: a });
const asElicit = (a: McpElicitationAsk): DockAskItem => ({ kind: "elicitation", id: a.id, ask: a });
const asSample = (a: McpSamplingAsk): DockAskItem => ({ kind: "sampling", id: a.id, ask: a });

function renderDock(
  asks: DockAskItem[],
  over: Partial<Parameters<typeof DockAsk>[0]> = {},
) {
  const props = {
    onAnswerElicitation: vi.fn(async () => ({ ok: true }) as const),
    onDecideSampling: vi.fn(async () => true),
    onConversation: vi.fn(),
    armDelayMs: 0,
    ...over,
  };
  const view = render(<DockAsk asks={asks} {...props} />);
  return { ...view, props, rerenderAsks: (next: DockAskItem[]) => view.rerender(<DockAsk asks={next} {...props} />) };
}

const posted = (path: string) => H.post.mock.calls.filter((c) => c[0] === path).map((c) => c[1]);

beforeEach(() => {
  H.post.mockReset();
  H.post.mockResolvedValue({});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  (document.activeElement as HTMLElement | null)?.blur?.();
});

/* ---------------------------------------------------------------- the list */

describe("lib/dockAsk — one list of open questions", () => {
  it("orders the chat approval, then open app questions, then the run's asks; ended ones are left out", () => {
    const list = collectDockAsks({
      approval: APPROVAL,
      mcpAsks: [ELICIT, { ...SAMPLE, outcome: "approved" }, { ...SAMPLE, id: "s2" }],
      sessionApprovals: [APPROVAL_2],
    });
    expect(list.map((x) => `${x.kind}:${x.id}`)).toEqual([
      "approval:apr_1",
      "elicitation:e1",
      "sampling:s2",
      "approval:apr_2",
    ]);
  });

  it("is empty with nothing waiting, and never lists one id twice", () => {
    expect(collectDockAsks({ approval: null, mcpAsks: null, sessionApprovals: null })).toEqual([]);
    expect(collectDockAsks({ approval: APPROVAL, sessionApprovals: [APPROVAL] })).toHaveLength(1);
  });

  it("keeps the question on screen first while it is still open", () => {
    const list = [asApproval(APPROVAL), asElicit(ELICIT)];
    expect(withHeadFirst(list, "e1").map((x) => x.id)).toEqual(["e1", "apr_1"]);
    expect(withHeadFirst(list, "gone").map((x) => x.id)).toEqual(["apr_1", "e1"]);
    expect(withHeadFirst(list, null).map((x) => x.id)).toEqual(["apr_1", "e1"]);
  });

  it("says the keys in plain words", () => {
    expect(dockAskKeyHint("approval")).toBe("Enter to allow · Esc to decline");
    expect(dockAskKeyHint("elicitation")).toBe("Enter to send · Esc to decline");
  });
});

describe("lib/dockAsk — which key does what", () => {
  const key = (k: string, over: Partial<KeyboardEvent> = {}) =>
    ({ key: k, shiftKey: false, ctrlKey: false, altKey: false, metaKey: false, repeat: false, isComposing: false, ...over }) as KeyboardEvent;
  const el = (html: string) => {
    const d = document.createElement("div");
    d.innerHTML = html;
    return d.firstElementChild as Element;
  };

  it("Enter is the main answer and Esc the no, from the card itself or the page", () => {
    expect(dockKeyAction(key("Enter"), el("<div></div>"), true)).toBe("primary");
    expect(dockKeyAction(key("Escape"), el("<div></div>"), true)).toBe("decline");
    expect(dockKeyAction(key("Enter"), null, false)).toBe("primary");
  });

  it("never takes Enter from a field or a button, nor Esc from a field", () => {
    expect(dockKeyAction(key("Enter"), el('<input type="text">'), true)).toBeNull();
    expect(dockKeyAction(key("Enter"), el("<button>x</button>"), true)).toBeNull();
    expect(dockKeyAction(key("Enter"), el("<summary>x</summary>"), true)).toBeNull();
    expect(dockKeyAction(key("Escape"), el('<input type="text">'), true)).toBeNull();
    expect(dockKeyAction(key("Escape"), el("<select></select>"), true)).toBeNull();
    // A button has no typing to protect: Esc there still says no.
    expect(dockKeyAction(key("Escape"), el("<button>x</button>"), true)).toBe("decline");
  });

  it("ignores held keys, modifiers, IME and every other key", () => {
    expect(dockKeyAction(key("Enter", { repeat: true }), null, false)).toBeNull();
    expect(dockKeyAction(key("Enter", { shiftKey: true }), null, false)).toBeNull();
    expect(dockKeyAction(key("Enter", { ctrlKey: true }), null, false)).toBeNull();
    expect(dockKeyAction(key("Enter", { isComposing: true }), null, false)).toBeNull();
    expect(dockKeyAction(key("a"), null, false)).toBeNull();
  });
});

/* ---------------------------------------------------------------- the card */

describe("DockAsk — the card in the composer's place", () => {
  it("draws the approval card (testid kept) with no count for one question, '1 of N' for more", () => {
    const { rerenderAsks } = renderDock([asApproval(APPROVAL)]);
    expect(screen.getByTestId("dock-ask").getAttribute("data-kind")).toBe("approval");
    expect(screen.getByTestId("chat-approval-card")).toBeTruthy();
    expect(screen.queryByTestId("dock-ask-count")).toBeNull();
    rerenderAsks([asApproval(APPROVAL), asElicit(ELICIT), asSample(SAMPLE)]);
    expect(screen.getByTestId("dock-ask-count").textContent).toBe("1 of 3");
    expect(screen.getAllByRole("alertdialog")).toHaveLength(1);
  });

  it("renders nothing when nothing is waiting", () => {
    renderDock([]);
    expect(screen.queryByTestId("dock-ask")).toBeNull();
  });

  it("Enter with nothing focused allows once; Esc with nothing focused denies", async () => {
    renderDock([asApproval(APPROVAL)]);
    (document.activeElement as HTMLElement | null)?.blur?.();
    fireEvent.keyDown(document.body, { key: "Enter" });
    await waitFor(() => expect(posted("/chat/approvals/apr_1")).toEqual([{ decision: "once" }]));
    cleanup();
    H.post.mockClear();
    renderDock([asApproval(APPROVAL_2)]);
    (document.activeElement as HTMLElement | null)?.blur?.();
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(posted("/chat/approvals/apr_2")).toEqual([{ decision: "deny" }]));
  });

  it("Enter inside the card (on the card itself) allows once", async () => {
    renderDock([asApproval(APPROVAL)]);
    fireEvent.keyDown(screen.getByTestId("chat-approval-card"), { key: "Enter" });
    await waitFor(() => expect(posted("/chat/approvals/apr_1")).toEqual([{ decision: "once" }]));
  });

  it("Enter on a focused button inside the card is that button's own, never Allow", async () => {
    renderDock([asApproval(APPROVAL)]);
    const conv = screen.getByRole("button", { name: /allow for this conversation/i });
    conv.focus();
    fireEvent.keyDown(conv, { key: "Enter" });
    await new Promise((r) => setTimeout(r, 20));
    expect(H.post).not.toHaveBeenCalled();
  });

  it("never answers while another field on the page has focus", async () => {
    render(<input aria-label="elsewhere" />);
    renderDock([asApproval(APPROVAL)]);
    const other = screen.getByLabelText("elsewhere");
    other.focus();
    fireEvent.keyDown(other, { key: "Enter" });
    fireEvent.keyDown(other, { key: "Escape" });
    await new Promise((r) => setTimeout(r, 20));
    expect(H.post).not.toHaveBeenCalled();
  });

  it("never answers while a dialog is open over the page", async () => {
    render(<div role="dialog" aria-modal="true" />);
    renderDock([asApproval(APPROVAL)]);
    (document.activeElement as HTMLElement | null)?.blur?.();
    fireEvent.keyDown(document.body, { key: "Enter" });
    await new Promise((r) => setTimeout(r, 20));
    expect(H.post).not.toHaveBeenCalled();
  });

  it("ignores Enter in the first moment after a question appears (a key typed for the composer)", async () => {
    let now = 1_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    renderDock([asApproval(APPROVAL)], { armDelayMs: 400 });
    (document.activeElement as HTMLElement | null)?.blur?.();
    now += 100;
    fireEvent.keyDown(document.body, { key: "Enter" });
    await new Promise((r) => setTimeout(r, 20));
    expect(H.post).not.toHaveBeenCalled();
    now += 400;
    fireEvent.keyDown(document.body, { key: "Enter" });
    await waitFor(() => expect(posted("/chat/approvals/apr_1")).toEqual([{ decision: "once" }]));
  });

  it("an app's question: Enter in its text field is left alone; Enter on the card sends through its checks", async () => {
    const { props } = renderDock([asElicit(ELICIT)]);
    const field = screen.getByLabelText(/City/);
    field.focus();
    fireEvent.keyDown(field, { key: "Enter" });
    fireEvent.keyDown(field, { key: "Escape" });
    await new Promise((r) => setTimeout(r, 20));
    expect(props.onAnswerElicitation).not.toHaveBeenCalled();
    // Enter on the card runs the card's own submit: the required field is
    // empty, so its check stops it — a key gets no shortcut past the rules.
    fireEvent.keyDown(screen.getByTestId("mcp-elicitation-card"), { key: "Enter" });
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(props.onAnswerElicitation).not.toHaveBeenCalled();
    fireEvent.change(field, { target: { value: "Lisbon" } });
    fireEvent.keyDown(screen.getByTestId("mcp-elicitation-card"), { key: "Enter" });
    await waitFor(() =>
      expect(props.onAnswerElicitation).toHaveBeenCalledWith("e1", "accept", { city: "Lisbon" }),
    );
  });

  it("an app's question: Esc on the card declines", async () => {
    const { props } = renderDock([asElicit(ELICIT)]);
    fireEvent.keyDown(screen.getByTestId("mcp-elicitation-card"), { key: "Escape" });
    await waitFor(() => expect(props.onAnswerElicitation).toHaveBeenCalledWith("e1", "decline", undefined));
  });

  it("an app asking for the model: Enter allows once, through the card's own button", async () => {
    const { props } = renderDock([asSample(SAMPLE)]);
    fireEvent.keyDown(screen.getByTestId("mcp-sampling-card"), { key: "Enter" });
    await waitFor(() => expect(props.onDecideSampling).toHaveBeenCalledWith("s1", "approve"));
  });

  it("the question on screen is not pushed aside by a newer one", () => {
    const { rerenderAsks } = renderDock([asElicit(ELICIT)]);
    fireEvent.change(screen.getByLabelText(/City/), { target: { value: "Por" } });
    // A new approval arrives and the page lists it FIRST.
    rerenderAsks([asApproval(APPROVAL), asElicit(ELICIT)]);
    expect(screen.getByTestId("dock-ask").getAttribute("data-kind")).toBe("elicitation");
    expect((screen.getByLabelText(/City/) as HTMLInputElement).value).toBe("Por");
    expect(screen.getByTestId("dock-ask-count").textContent).toBe("1 of 2");
    // Answered: the approval is next.
    rerenderAsks([asApproval(APPROVAL)]);
    expect(screen.getByTestId("dock-ask").getAttribute("data-kind")).toBe("approval");
  });

  it("takes the caret from the composer when it had it, and gives it back when the last question goes", () => {
    const returnFocus = vi.fn();
    render(
      <div data-testid="chat-composer">
        <textarea aria-label="Message" />
      </div>,
    );
    const box = screen.getByLabelText("Message");
    box.focus();
    const { rerenderAsks } = renderDock([asApproval(APPROVAL)], { returnFocus });
    expect(document.activeElement).toBe(screen.getByTestId("dock-ask"));
    expect(returnFocus).not.toHaveBeenCalled();
    rerenderAsks([]);
    expect(returnFocus).toHaveBeenCalledTimes(1);
  });

  it("leaves the caret where it is when another field had it", () => {
    render(<input aria-label="elsewhere" />);
    const other = screen.getByLabelText("elsewhere");
    other.focus();
    renderDock([asApproval(APPROVAL)]);
    expect(document.activeElement).toBe(other);
  });

  it("offers Stop while the composer (and its Stop) is hidden", () => {
    const onStop = vi.fn();
    renderDock([asApproval(APPROVAL)], { onStop });
    fireEvent.click(screen.getByTestId("dock-ask-stop"));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("'Allow for this conversation' still reaches the page's grant handler", async () => {
    const { props } = renderDock([asApproval(APPROVAL)]);
    fireEvent.click(screen.getByRole("button", { name: /allow for this conversation/i }));
    await waitFor(() => expect(props.onConversation).toHaveBeenCalledWith("shell"));
    await act(async () => {});
  });
});
