/**
 * v1.355.0 lane 5D — G65: unattended work warns that asks wait five minutes.
 *
 * A schedule, a goal and a workflow run with nobody watching: a step that
 * needs approval waits 5 minutes (bell and desktop notice) and then that step
 * is skipped. The create forms never said so, even for the template that
 * moves files. Now the ONE sentence (lib/unattendedWords, rendered by
 * components/UnattendedAskNote) sits under each form, quiet grey, no box.
 *
 * The REAL pages render (the ux-wave2-automation-v1314 and workflows-calm-v1329
 * harnesses: transport mocked, React Flow stubbed). The sentence's minutes are
 * pinned to the daemon's own clock by tests/test_model_doors_v13550.py.
 *
 * Control: an EVENT schedule only emits an event, so no step can ask: the
 * sentence goes away there instead of saying something untrue.
 */

import type { ReactNode } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    responses: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://127.0.0.1:8797",
  ijToken: () => "",
  wsUrl: (p: string) => p,
  sseUrl: (p: string) => p,
  flattenDetail: (d: unknown) => String(d),
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  api: async () => ({}),
  get: async (path: string) => api.responses[path] ?? {},
  post: async () => ({ ok: true }),
  put: async () => ({}),
  patch: async () => ({}),
  del: async () => ({}),
}));

vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title, actions }: { title: string; actions?: ReactNode }) => (
    <div>
      <h1>{title}</h1>
      {actions}
    </div>
  ),
}));
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (t: string, c: string) => (t ? `${t} ${c}` : c),
}));
vi.mock("@xyflow/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@xyflow/react")>();
  const { createElement } = await import("react");
  return {
    ...actual,
    ReactFlow: ({ children }: { children?: ReactNode }) =>
      createElement("div", { "data-testid": "rf-canvas" }, children),
    ReactFlowProvider: ({ children }: { children?: ReactNode }) => createElement("div", null, children),
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    useReactFlow: () => ({ fitView: async () => false }),
  };
});

import SchedulesPage from "@/app/schedules/page";
import AutonomyPage from "@/app/autonomy/page";
import WorkflowsPage from "@/app/workflows/page";
import { UNATTENDED_ASK_NOTE } from "@/lib/unattendedWords";

const SENTENCE =
  "If a step needs your OK it waits 5 minutes (bell and desktop notice); if nobody answers, that step is skipped.";

beforeAll(() => {
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {};
  }
});
beforeEach(() => {
  window.scrollTo = vi.fn();
  window.HTMLElement.prototype.scrollTo = vi.fn();
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  localStorage.clear();
  api.responses = {};
});
afterEach(cleanup);

function seedSchedules() {
  api.responses["/schedules"] = { schedules: [] };
  api.responses["/workflows"] = { workflows: [] };
  api.responses["/projects"] = { projects: [] };
  api.responses["/comm/channels"] = { channels: [] };
  api.responses["/agents"] = { builtin: ["builder", "researcher"], dynamic: [] };
}

function seedAutonomy() {
  api.responses["/autonomy"] = {
    enabled: false,
    level: "suggest",
    dry_run: false,
    kill_switch: false,
    tick_seconds: 900,
    max_actions_per_day: 20,
    max_tokens_per_day: 200000,
    used_actions_24h: 0,
    used_tokens_24h: 0,
    active_goals: 0,
    pending_proposals: 0,
  };
  api.responses["/autonomy/goals"] = { goals: [] };
  api.responses["/proposals?status=pending"] = { proposals: [] };
  api.responses["/autonomy/briefing"] = { text: "", active_goals: 0, recent_actions: 0, pending_proposals: 0, pushed: null };
  api.responses["/goals"] = { goals: [] };
  api.responses["/goals/digest?hours=24"] = { digest: { goals: [], since: "2026-10-06T00:00:00Z" } };
  api.responses["/grants?live=1"] = { grants: [] };
}

it("the one sentence says what the forms promise", () => {
  expect(UNATTENDED_ASK_NOTE).toBe(SENTENCE);
});

describe("Schedules: Add schedule", () => {
  it("shows the sentence in the add form, under its button", async () => {
    seedSchedules();
    render(<SchedulesPage />);
    const task = (await screen.findByLabelText("Task text")) as HTMLTextAreaElement;
    const form = task.closest("form") as HTMLFormElement;
    const note = within(form).getByTestId("unattended-ask-note");
    expect(note.textContent).toBe(SENTENCE);
    const add = within(form).getByRole("button", { name: /Add schedule/ });
    expect(add.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Quiet grey words, no box (the calm look).
    expect(note.className).toMatch(/text-zinc-500/);
    expect(note.className).not.toMatch(/border|bg-|shadow/);
  });

  it("control: an event schedule asks nothing, so the sentence goes away", async () => {
    seedSchedules();
    render(<SchedulesPage />);
    await screen.findByLabelText("Task text");
    expect(screen.getByTestId("unattended-ask-note")).toBeTruthy();
    const kinds = screen.getByRole("radiogroup", { name: "Schedule kind" });
    const radios = within(kinds).getAllByRole("radio");
    const event = radios.find((r) => /event/i.test(r.textContent ?? "") || /event/i.test(r.getAttribute("aria-label") ?? ""));
    expect(event, "an event kind tile").toBeTruthy();
    fireEvent.click(event!);
    await waitFor(() => expect(screen.queryByTestId("unattended-ask-note")).toBeNull());
  });
});

describe("Autonomy: New goal", () => {
  it("shows the sentence under Add goal", async () => {
    seedAutonomy();
    render(<AutonomyPage />);
    const add = await screen.findByRole("button", { name: /Add goal/ });
    const form = add.closest("form") as HTMLFormElement;
    const note = within(form).getByTestId("unattended-ask-note");
    expect(note.textContent).toBe(SENTENCE);
    expect(add.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("Workflows: the editor", () => {
  it("shows the sentence right under the editor where a workflow is made and saved", async () => {
    api.responses["/workflows"] = { workflows: [] };
    api.responses["/workflows/runs?limit=50"] = { runs: [] };
    render(<WorkflowsPage />);
    const editor = await screen.findByTestId("workflow-editor");
    const notes = screen.getAllByTestId("unattended-ask-note");
    expect(notes).toHaveLength(1);
    expect(notes[0].textContent).toBe(SENTENCE);
    expect(editor.compareDocumentPosition(notes[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
