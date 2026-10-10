/**
 * v1.329.0 — calm chat wave 5, G2: one model name, one way of describing
 * steps, plain receipt copy.
 *
 * The re-audit after wave 4 found:
 *  - the composer chip said "Opus 4.8" while the reply above it said
 *    "answered by claude-opus-4-8" (answeredModelName never used the
 *    friendly name);
 *  - the work line said "Read sales-harbor-st.xlsx" while the expanded
 *    receipt under the same reply listed monospace "read_file · 0.4 s";
 *  - dash asides on the folded row and the detail ("mock answer — no real
 *    model ran", "answered by X — failover from Y", "— requested …"), and
 *    the warnings in literal amber instead of the tone token;
 *  - the reasoning chip's tooltip had a dash aside;
 *  - the approval card drew a bare "—" for a call with no arguments.
 *
 * Page harness: the work-line-v1326 one (ChatPage rendered for real,
 * transport and stream mocked).
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
    offline = false;
    partial = "";
  }
  return { FakeApiError, FakeStreamError, getResponses: {} as Record<string, unknown> };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => "",
  get: async (p: string) => {
    const r = H.getResponses[p];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${p}`, 404);
    return r;
  },
  post: async () => ({}),
  put: async () => ({ id: "t1", title: "t" }),
  del: async () => ({}),
}));
vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    thinking: "",
    tools: [],
    mcpAsks: [],
    approval: null,
    phase: null,
    run: async () => ({ reply: "" }),
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false, reason: null, engine: null, listening: false, processing: false,
    transcript: "", interim: "", error: null, start: () => {}, stop: () => {}, reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: true, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
    readAloud: () => {}, readingKey: null,
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", async () => await vi.importActual("react-markdown"));

import ChatPage from "@/app/chat/page";
import { WorkLine } from "@/components/chat/WorkLine";
import { TurnReceipt, adaptedLabel, routeWarning } from "@/components/chat/TurnReceipt";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { answeredModelName } from "@/lib/answeredModel";
import { stepPhrase, stepPhraseText } from "@/lib/stepWords";
import { reasoningTitle } from "@/lib/reasoningChip";

const read = (rel: string) =>
  readFileSync(path.join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");

const LITERAL_HUE = /\b(?:text|bg|border)-(?:rose|amber|emerald|red|green|yellow)-\d{2,3}\b/;
const DASH = "—";

const AT = "2026-10-09T08:20:00";
const ROUTE = { requested: "", provider: "claude-cli", model: "claude-opus-4-8", reason: "default" };
const STEPS = [
  { name: "read_file", ok: true, ms: 400, target: "sales-harbor-st.xlsx" },
  { name: "web_search", ok: true, ms: 900, target: "pier 9 opening hours" },
  { name: "excel_query", ok: true, ms: 1200 },
  { name: "read_file", ok: false, ms: 300 },
];

beforeEach(() => {
  H.getResponses = {
    // A catalog row WITHOUT a label: the name must come from the id.
    "/models": { models: [{ provider: "claude-cli", model: "claude-opus-4-8", available: true }] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/chat/threads/t1": {
      id: "t1",
      title: "Harbor",
      messages: [
        { role: "user", content: "How did the harbor shop do?", at: AT },
        { role: "assistant", content: "Harbor is up 4 percent.", at: AT, route: ROUTE, toolsUsed: ["read_file"] },
      ],
    },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState({}, "", "/chat");
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/* ============================================================ one name */

describe("a reply names its model the way the composer chip does", () => {
  it("the catalog label wins; else the name the id spells; nothing invented", () => {
    const rows = [
      { provider: "claude-cli", model: "claude-opus-5-5", label: "Opus 5.5" },
      { provider: "claude-cli", model: "claude-opus-4-8" },
    ];
    expect(answeredModelName({ provider: "claude-cli", model: "claude-opus-5-5" }, rows)).toBe("Opus 5.5");
    expect(answeredModelName({ provider: "claude-cli", model: "claude-opus-4-8" }, rows)).toBe("Opus 4.8");
    // No catalog row at all (an older chat, a model since removed).
    expect(answeredModelName({ provider: "claude-cli", model: "claude-sonnet-4-5-20250929" }, [])).toBe("Sonnet 4.5");
    // CONTROL: an id it cannot read stays exactly as it is.
    expect(answeredModelName({ provider: "ollama", model: "qwen3:30b" }, rows)).toBe("qwen3:30b");
    expect(answeredModelName({ provider: "claude-cli", model: "" }, rows)).toBeUndefined();
    expect(answeredModelName(undefined, rows)).toBeUndefined();
  });

  it("on the chat page: 'answered by Opus 4.8', the raw id kept on the line's title", async () => {
    window.history.replaceState({}, "", "/chat?thread=t1");
    render(<ChatPage />);
    await screen.findByText("Harbor is up 4 percent.");
    const row = screen.getAllByTestId("reply-actions").at(-1)!;
    await waitFor(() =>
      expect(within(row).getByTestId("turn-answered-by").textContent).toBe("answered by Opus 4.8"),
    );
    expect(within(row).getByTestId("turn-answered-by").getAttribute("title")).toContain("claude-opus-4-8");
    expect(within(row).getByTestId("turn-receipt").textContent).not.toContain("claude-opus-4-8");
  });

  it("the page reads the shared rule, not a copy of it (source pin)", () => {
    const page = read("app/chat/page.tsx");
    const at = page.indexOf("const answeredModelName = useCallback(");
    expect(at).toBeGreaterThan(0);
    expect(page.slice(at, at + 300)).toContain("answeredModelNameFor(route, models)");
  });

  it("an adapted turn names its model by the same rule", () => {
    expect(adaptedLabel({ model: "claude-haiku-4-5", changes: ["tool_cap:3"] })).toBe(
      "adapted to Haiku 4.5: capped at 3 tools for this local model",
    );
    expect(adaptedLabel({ model: "qwen-3b", changes: ["tool_cap:4"] })).toBe(
      "adapted to qwen-3b: capped at 4 tools for this local model",
    );
  });
});

/* ===================================================== one way of saying it */

describe("the receipt says the steps in the work line's words", () => {
  it("the same phrase above and below, no monospace ids", () => {
    render(
      <>
        <div data-testid="above">
          <WorkLine steps={STEPS} />
        </div>
        <div data-testid="below">
          <TurnReceipt route={ROUTE} steps={STEPS} toolsUsed={["read_file", "web_search", "excel_query"]} />
        </div>
      </>,
    );
    const above = screen.getByTestId("above");
    fireEvent.click(within(above).getByRole("button", { expanded: false }));
    const below = screen.getByTestId("below");
    fireEvent.click(within(below).getByRole("button", { expanded: false }));

    const receiptRows = within(below).getAllByTestId("turn-step").map((r) => r.textContent);
    expect(receiptRows).toEqual([
      "Read sales-harbor-st.xlsx · 0.4 s",
      "Searched the web for pier 9 opening hours · 0.9 s",
      "Ran excel_query · 1.2 s",
      "Read · read_file · 0.3 s · failed",
    ]);
    // Each receipt row opens with exactly the words the work line's row says.
    const workRows = within(above).getAllByTestId("work-row").map((r) => r.textContent ?? "");
    STEPS.forEach((st, i) => {
      const phrase = stepPhraseText(stepPhrase(st.name, st.target ?? null));
      expect(workRows[i].startsWith(phrase.replace(" · ", ""))).toBe(true);
      expect(receiptRows[i]!.startsWith(phrase)).toBe(true);
    });
    // Not a second vocabulary: no code chips, no monospace in the step list.
    const detail = within(below).getByTestId("turn-receipt-detail");
    for (const r of within(detail).getAllByTestId("turn-step")) {
      expect(r.tagName).not.toBe("CODE");
      expect(r.className).not.toContain("font-mono");
    }
    // The raw id stays on the row's hover.
    expect(within(detail).getAllByTestId("turn-step")[0].getAttribute("title")).toContain("(read_file)");
    // The failed one still reads as a failure.
    expect(within(detail).getAllByTestId("turn-step")[3].className).toContain("text-tone-danger");
  });

  it("a reply with only tool names (no stored steps) uses the same words", () => {
    render(<TurnReceipt route={ROUTE} toolsUsed={["web_search", "excel_query"]} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getAllByTestId("turn-tool").map((r) => r.textContent)).toEqual([
      "Searched the web · web_search",
      "Ran excel_query",
    ]);
  });

  it("a saved target that looks like a key is dropped on the way out", () => {
    render(
      <TurnReceipt
        route={ROUTE}
        steps={[{ name: "web_search", ok: true, ms: 100, target: "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" } as never]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByTestId("turn-step").textContent).toBe("Searched the web · web_search · 0.1 s");
  });
});

/* ============================================================ plain copy */

const WARN_ROUTES = [
  { provider: "mock", reason: "mock" },
  { requested: "", provider: "claude-cli", reason: "failover", from: "fleet-rtx6000ada", why: "http 500" },
  { requested: "", provider: "claude-cli", reason: "failover", from: "ollama" },
  { requested: "codex-cli", provider: "claude-cli", reason: "failover" },
  { requested: "", provider: "claude-cli", reason: "failover" },
  { requested: "codex-cli", provider: "claude-cli", reason: "explicit" },
];

describe("the receipt speaks in plain sentences, warnings in the warning token", () => {
  it("every warning is plain words with no dash aside", () => {
    expect(WARN_ROUTES.map((r) => routeWarning(r))).toEqual([
      "Mock answer. No real model ran.",
      "answered by Claude Code (fleet-rtx6000ada returned HTTP 500)",
      "answered by Claude Code (Ollama was not available)",
      "answered by Claude Code (Codex was not available)",
      "answered by Claude Code (as a fallback)",
      "answered by Claude Code (you asked for Codex)",
    ]);
  });

  it.each(WARN_ROUTES.map((r) => [r.provider + "/" + r.reason + "/" + (r as { from?: string }).from, r]))(
    "%s: the folded row and the detail carry no dash and no literal hue",
    (_label, route) => {
      render(
        <TurnReceipt
          inline
          modelName="Opus 4.8"
          route={{ ...(route as object), model: "claude-opus-4-8" } as never}
          deniedTools={["shell"]}
          trust="low"
          trustReason="the message came from your phone"
          trustNote="low trust: 4 tools kept away"
        />,
      );
      const toggle = screen.getByTestId("turn-receipt");
      expect(toggle.textContent).not.toContain(DASH);
      fireEvent.click(toggle);
      const detail = screen.getByTestId("turn-receipt-detail");
      expect(detail.textContent).not.toContain(DASH);
      for (const el of [toggle, detail, ...Array.from(toggle.querySelectorAll("*")), ...Array.from(detail.querySelectorAll("*"))]) {
        expect(el.getAttribute("class") ?? "").not.toMatch(LITERAL_HUE);
        expect(el.getAttribute("title") ?? "").not.toContain(DASH);
      }
      // Anti-vacuity: the warnings are still drawn in the warning tone.
      // v1.329.0 (W6 H1): a substitute is named by its MODEL, as a normal
      // row is (the name the caller gives), never by its provider.
      const words = routeWarning({ ...(route as object), model: "claude-opus-4-8" } as never, "Opus 4.8")!;
      if ((route as { provider: string }).provider !== "mock") expect(words).toMatch(/^answered by Opus 4\.8 \(/);
      const warn = within(toggle).getByText(words);
      expect(warn.className).toContain("text-tone-warn");
      expect(within(toggle).getByText("1 blocked").className).toContain("text-tone-warn");
      expect(within(toggle).getByTestId("turn-trust").className).toContain("text-tone-warn");
      expect(within(toggle).getByTestId("turn-trust").textContent).toBe(
        "low trust: the message came from your phone. 4 tools kept away",
      );
    },
  );

  it("the detail says who was asked for and who failed with a dot, not a dash", () => {
    render(
      <TurnReceipt
        route={{ requested: "codex-cli", provider: "claude-cli", model: "claude-opus-4-8", reason: "failover", from: "ollama", why: "timeout" }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByText("· you asked for Codex").getAttribute("title")).toBe("codex-cli");
    expect(screen.getByText("· Ollama didn't respond in time").getAttribute("title")).toBe("ollama");
  });
});

/* ======================================================= reasoning + ask */

describe("the reasoning chip and the ask card", () => {
  it("the reasoning tooltip is plain sentences and says what 'Reasoning' means (source pin)", () => {
    // v1.330.0: the tooltip is computed from the model's documented default
    // (lib/reasoningChip.reasoningTitle), so the pin reads the select's title
    // EXPRESSION and the words it produces for the unknown case, where the
    // chip still says "Reasoning".
    const page = read("app/chat/page.tsx");
    const at = page.indexOf('aria-label="Reasoning level"');
    expect(at).toBeGreaterThan(0);
    const select = page.slice(at, at + 900);
    expect(select).toMatch(/title=\{reasoningTitle\(reasoningDefaultFor\(choice\)\)\}/);
    const title = reasoningTitle("");
    expect(title).toBe(
      "How hard the model thinks before answering. Higher is slower and costs more. With nothing picked the model decides.",
    );
    for (const def of ["", "low", "medium", "high", "off", "auto"]) {
      expect(reasoningTitle(def)).not.toContain(DASH);
    }
  });

  it("a call with no arguments says '(empty)', never a bare dash", () => {
    const { unmount } = render(
      <ApprovalCard
        approval={{ id: "a1", callId: "c1", tool: "list_files", count: 2, examples: [{}, { path: "b" }] }}
        docked
      />,
    );
    const card = screen.getByTestId("chat-approval-card");
    expect(card.textContent).toContain("(empty)");
    expect(card.textContent).toContain("path: b");
    expect(card.textContent).not.toContain(DASH);
    unmount();
    render(<ApprovalCard approval={{ id: "a2", callId: "c2", tool: "write_file", args: { path: "" } }} docked />);
    expect(screen.getByTestId("chat-approval-card").textContent).toContain("path: (empty)");
  });
});
