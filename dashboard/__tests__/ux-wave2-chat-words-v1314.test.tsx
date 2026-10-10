/**
 * v1.314.0 — UX wave 2, track T1 (chat words). Written BEFORE the fix.
 *
 * Findings (ux_wave2.json; the verifier's fix_adjustment wins):
 *  - composer-footer-jargon: the footer's model trigger names the DEFAULT
 *    model by its catalog label ("Claude Opus 4.8"), not the raw id in
 *    monospace; the approval select carries a VISIBLE label tied to it; the
 *    YOLO hint is plain words that still warn; share is a real target with a
 *    visible word. Same three modes, same wire values, same menu, same dialog.
 *  - receipt-raw-provider-ids: the receipt's "who" and the failover/mismatch
 *    warnings name providers through providerDisplay ("Claude Code"), the raw
 *    id stays reachable (title / expanded detail), the amber warnings stay
 *    visible without expanding, a custom endpoint id passes through unchanged.
 *  - approval-card-tool-ids: the card leads with what the action DOES
 *    ("save a file"); the exact tool id stays on the card; every answer, the
 *    count, the verbatim command and the arguments stay — and a long argument
 *    wraps instead of being cut off. An unknown tool falls back to its id.
 *  - empty-state-doors-push-starters-below-fold: on chat the three connect
 *    doors are a 3-up row from sm (opt-in `layout="row"`, so the wizard keeps
 *    its stacked doors) inside a wider card; doors still come first, the chips
 *    still prefill the composer and never send.
 *  - carry-model-menu-opaque: the title-bar model menu panel is opaque (or
 *    portaled out of the header's backdrop-filter so its own blur works);
 *    every row and the "Connect another account" link stay.
 *
 * Harness: lifted from chat-connect-doors-v1310 (ChatPage rendered for real,
 * transport hooks mocked at the usual seams; @/lib/daemon mocked so every
 * reader sees the same /health).
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
  return {
    FakeApiError,
    getResponses: {} as Record<string, unknown>,
    posts: [] as Array<{ path: string; body: unknown }>,
    health: null as Record<string, unknown> | null,
    refresh: vi.fn(),
    streamRuns: 0,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (p: string) => {
    const r = H.getResponses[p];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${p}`, 404);
    return r;
  },
  post: async (p: string, body?: unknown) => {
    H.posts.push({ path: p, body });
    return {};
  },
  put: async (p: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(p);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    epoch: 0,
    provided: true,
    health: H.health,
    refresh: H.refresh,
  }),
}));

vi.mock("@/lib/useChatStream", () => {
  class StreamError extends Error {
    status = 0;
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    StreamError,
    useChatStream: () => ({
      streaming: false,
      text: "",
      tools: [],
      approval: null,
      run: async () => {
        H.streamRuns += 1;
        return { reply: "" };
      },
      abort: () => {},
    }),
  };
});

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: false }),
}));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({
    text: "",
    tools: [],
    phase: null,
    active: false,
    start: () => {},
    stop: () => {},
  }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false,
    reason: null,
    engine: null,
    listening: false,
    processing: false,
    transcript: "",
    interim: "",
    error: null,
    start: () => {},
    stop: () => {},
    reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false,
    enabled: false,
    speaking: false,
    enable: () => {},
    disable: () => {},
    toggle: () => {},
    speak: () => {},
    resetStream: () => {},
    speakMore: () => {},
    cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => {
    const h = H.health as { default_provider?: string; providers?: Array<Record<string, unknown>> } | null;
    const byProvider: Record<string, boolean> = {};
    for (const p of h?.providers ?? []) byProvider[String(p.provider)] = Boolean(p.available);
    return {
      byProvider,
      signedOutByProvider: {},
      defaultProvider: h?.default_provider ?? "",
      loading: false,
      stale: false,
      refresh: () => {},
    };
  },
}));
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { ConnectDoors } from "@/components/onboarding/ConnectDoors";
import { TurnReceipt, routeWarning } from "@/components/chat/TurnReceipt";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import { ModelSwitcher } from "@/components/ModelSwitcher";

type Row = Record<string, unknown>;

const NOTHING_CONNECTED: Row[] = [
  { provider: "anthropic", available: false, class: "api", inherited_from: null },
  { provider: "openai", available: false, class: "api", inherited_from: null },
  { provider: "claude-cli", available: false, class: "cli", installed: false, signed_in: null, sign_in_fix: "" },
  { provider: "codex-cli", available: false, class: "cli", installed: false, signed_in: null, sign_in_fix: "" },
  { provider: "ollama", available: false, class: "local", inherited_from: null },
];

const ANTHROPIC_READY: Row[] = NOTHING_CONNECTED.map((r) =>
  r.provider === "anthropic" ? { ...r, available: true } : r,
);

const RAW_MODEL = "claude-opus-4-8";
const MODEL_LABEL = "Claude Opus 4.8";
const CATALOG_WITH_LABEL = {
  models: [
    { provider: "anthropic", model: RAW_MODEL, label: MODEL_LABEL, name: "Anthropic", kind: "api", available: true },
    { provider: "anthropic", model: "claude-sonnet-5", name: "Anthropic", kind: "api", available: true },
  ],
};

function setHealth(providers: Row[], defaultProvider: string, defaultModel = RAW_MODEL) {
  H.health = {
    status: "ok",
    version: "1.314.0",
    default_provider: defaultProvider,
    default_model: defaultModel,
    providers,
  };
  H.getResponses["/onboarding"] = {
    version: "1.314.0",
    first_run: false,
    doctor: { ok: true, checks: [] },
    checklist: [],
    next_step: null,
    model: {
      default_provider: defaultProvider,
      default_model: defaultModel,
      is_mock: defaultProvider === "mock",
      usable: providers
        .filter((p) => p.available)
        .map((p) => ({ provider: String(p.provider), label: String(p.provider) })),
    },
  };
}

function resetApi() {
  for (const k of Object.keys(H.getResponses)) delete H.getResponses[k];
  H.getResponses["/models"] = { models: [] };
  H.getResponses["/chat/personas"] = { personas: [] };
  H.getResponses["/chat/threads"] = { threads: [] };
  H.getResponses["/settings"] = { settings: {} };
  H.getResponses["/projects"] = { projects: [] };
  H.getResponses["/agents/mentionable"] = { agents: [] };
  H.getResponses["/skills"] = { skills: [] };
  H.getResponses["/workflows"] = { workflows: [] };
  H.getResponses["/tools"] = { tools: [] };
  H.getResponses["/undo?session_id=chat"] = { actions: [] };
  H.getResponses["/chat/approvals/pending"] = { approvals: [] };
  H.getResponses["/routing"] = { enabled: false, routing_model: "", connected: [], suggested: null, tiers: {} };
}

/** Is `raw` still reachable inside `root` — as words, or in a title? */
function reachable(root: Element, raw: string): boolean {
  if ((root.textContent ?? "").includes(raw)) return true;
  if ((root.getAttribute("title") ?? "").includes(raw)) return true;
  return Array.from(root.querySelectorAll("[title]")).some((el) =>
    (el.getAttribute("title") ?? "").includes(raw),
  );
}

function classes(el: Element): string[] {
  return (el.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);
}

beforeEach(() => {
  resetApi();
  H.posts = [];
  H.streamRuns = 0;
  H.refresh = vi.fn();
  setHealth(ANTHROPIC_READY, "anthropic");
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/* ======================================================= composer footer */

describe("composer footer speaks plainly (composer-footer-jargon)", () => {
  it("the model trigger names the DEFAULT model by its catalog label, not the raw id, and not in monospace", async () => {
    H.getResponses["/models"] = CATALOG_WITH_LABEL;
    render(<ChatPage />);
    // The trigger's own title stays "Switch model" (four suites find it by it).
    const trigger = await screen.findByTitle("Switch model");
    await waitFor(() => expect(trigger.textContent).toContain(MODEL_LABEL));
    expect(trigger.textContent).toMatch(/default/i);
    expect(trigger.textContent).not.toContain(RAW_MODEL);
    expect(trigger.textContent).not.toContain("default ·");
    expect(classes(trigger)).not.toContain("font-mono");
    expect(trigger.querySelectorAll(".font-mono")).toHaveLength(0);
    // Anti-vacuity: the exact id the default resolves to is still reachable.
    expect(reachable(trigger, RAW_MODEL)).toBe(true);
  });

  // v1.329.0 (calm chat F7): this pin moved on purpose. With no catalog
  // label the chip no longer prints the raw id; it says the name the id
  // spells ("Opus 4.8"), derived only from the id (nothing invented), and
  // the raw id stays reachable on the inner title.
  it("CONTROL: a default with no catalog label reads as the name its id spells (nothing invented)", async () => {
    H.getResponses["/models"] = { models: [] };
    render(<ChatPage />);
    const trigger = await screen.findByTitle("Switch model");
    await waitFor(() => expect(trigger.textContent).toContain("Default: Opus 4.8"));
    expect(trigger.textContent).not.toContain(RAW_MODEL);
    expect(reachable(trigger, RAW_MODEL)).toBe(true);
    expect(trigger.textContent).not.toContain(MODEL_LABEL);
  });

  it("HONESTY: a demo-model default says 'Demo model (scripted)', never a real model's name", async () => {
    // v1.314.0 (fix round): the catalog HAS a labelled row for the configured
    // model id, but the provider answering is the scripted demo — naming
    // "Claude Opus 4.8" here would claim a model that is not answering.
    H.getResponses["/models"] = CATALOG_WITH_LABEL;
    setHealth(NOTHING_CONNECTED, "mock");
    render(<ChatPage />);
    const trigger = await screen.findByTitle("Switch model");
    await waitFor(() => expect(trigger.textContent).toContain("Demo model (scripted)"));
    expect(trigger.textContent).not.toContain(MODEL_LABEL);
    expect(trigger.textContent).not.toContain(RAW_MODEL);
    // The record of what is configured stays reachable on the inner title.
    expect(reachable(trigger, `mock · ${RAW_MODEL}`)).toBe(true);
  });

  // v1.327.0: the composer's permission chip replaced the approval select (it
  // kept the id). The contract these two tests guarded stays, read off the
  // chip: a VISIBLE name for the current level, the same three modes and wire
  // values in the same order, plain words that still warn, and amber for the
  // no-ask level. The words are now the chip's (lib/permissionLevels.ts).
  it("the permission chip shows its level in VISIBLE words; the three modes and their wire values stay", async () => {
    const { container } = render(<ChatPage />);
    await screen.findByTitle("Switch model");
    const chip = container.querySelector("#chat-approval-mode") as HTMLButtonElement;
    expect(chip).toBeTruthy();
    expect(chip.getAttribute("data-mode")).toBe("approve_for_me");
    // The level's name is real text on the chip (not sr-only) and in its name.
    const words = within(chip).getByText("Ask when risky");
    expect(classes(words)).not.toContain("sr-only");
    expect(chip.getAttribute("aria-label")).toBe("Permissions: Ask when risky");
    fireEvent.click(chip);
    const items = within(screen.getByTestId("permission-menu")).getAllByTestId("permission-item");
    expect(items.map((i) => i.getAttribute("data-mode"))).toEqual(["always_ask", "approve_for_me", "yolo"]);
    expect(items.map((i) => i.querySelector("span.block")?.textContent)).toEqual([
      "Ask first",
      "Ask when risky",
      "Don't ask",
    ]);
  });

  it("the no-ask level is plain words that still warn (no 'YOLO'), and the amber tone stays", async () => {
    const { container } = render(<ChatPage />);
    await screen.findByTitle("Switch model");
    const chip = container.querySelector("#chat-approval-mode") as HTMLButtonElement;
    fireEvent.click(chip);
    const yolo = within(screen.getByTestId("permission-menu"))
      .getAllByTestId("permission-item")
      .find((i) => i.getAttribute("data-mode") === "yolo") as HTMLElement;
    expect(yolo.textContent).not.toMatch(/YOLO/);
    expect(yolo.textContent).toMatch(/without asking/i);
    expect(yolo.textContent).toMatch(/care/i); // a warning tone, per the verifier
    fireEvent.click(yolo);
    await waitFor(() => expect(chip.getAttribute("data-mode")).toBe("yolo"));
    expect(chip.title).not.toMatch(/YOLO/);
    expect(chip.title).toMatch(/without asking/i);
    // CONTROL: the dangerous position still LOOKS dangerous (a tone token).
    expect(classes(chip)).toContain("text-tone-warn");
  });

  it("share is a real target with a visible word; its name, dialog gate and explanation stay", async () => {
    render(<ChatPage />);
    await screen.findByTitle("Switch model");
    const share = screen.getByRole("button", { name: "Share this chat" });
    expect(share.textContent).toMatch(/Share/);
    expect(share.className).toMatch(/(^|\s)(h-7|min-h-7|h-8|min-h-8|h-\[28px\]|min-h-\[28px\]|btn-ghost)(\s|$)/);
    // CONTROL: no thread yet → disabled, and the title says why.
    expect((share as HTMLButtonElement).disabled).toBe(true);
    expect(share.getAttribute("title")).toMatch(/shared after its first reply/);
  });
});

/* =========================================================== empty state */

const FIRST_CHIP = "What can you do?";
const DOOR_SUB = /I already pay/;

describe("chat empty state: starters reach the fold (empty-state-doors-push-starters-below-fold)", () => {
  it("on chat the three doors are a 3-up row from sm, inside a card wider than max-w-md", async () => {
    setHealth(NOTHING_CONNECTED, "mock");
    render(<ChatPage />);
    const sub = await screen.findByRole("button", { name: DOOR_SUB });
    const grid = sub.parentElement as HTMLElement;
    expect(within(grid).getByRole("button", { name: /Free & private/ })).toBeTruthy();
    expect(within(grid).getByRole("button", { name: /API key/ })).toBeTruthy();
    expect(classes(grid)).toContain("sm:grid-cols-3");
    const heading = screen.getByText("Connect a model for real answers");
    const card = heading.parentElement as HTMLElement;
    expect(card.contains(sub)).toBe(true);
    expect(classes(card)).not.toContain("max-w-md");
    expect(card.className).toMatch(/(^|\s)max-w-(xl|2xl|3xl)(\s|$)/);
  });

  it("ConnectDoors lays out a row only when asked (layout=\"row\"); the wizard's default stays stacked", async () => {
    setHealth(NOTHING_CONNECTED, "mock");
    const { unmount } = render(<ConnectDoors />);
    const stacked = (await screen.findByRole("button", { name: DOOR_SUB })).parentElement as HTMLElement;
    expect(classes(stacked)).toContain("grid");
    expect(classes(stacked)).not.toContain("sm:grid-cols-3");
    unmount();
    // eslint-disable-next-line @typescript-eslint/ban-ts-comment
    // @ts-ignore — the opt-in prop the fix adds (v1.314.0)
    render(<ConnectDoors layout="row" />);
    const row = (await screen.findByRole("button", { name: DOOR_SUB })).parentElement as HTMLElement;
    expect(classes(row)).toContain("sm:grid-cols-3");
    expect(within(row).getAllByRole("button")).toHaveLength(3);
  });

  it("CONTROL: doors still come first; a starter chip prefills the composer and never sends", async () => {
    setHealth(NOTHING_CONNECTED, "mock");
    render(<ChatPage />);
    const chip = await screen.findByRole("button", { name: FIRST_CHIP });
    const sub = await screen.findByRole("button", { name: DOOR_SUB });
    expect(sub.compareDocumentPosition(chip) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(chip);
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe(FIRST_CHIP));
    expect(H.streamRuns).toBe(0);
    expect(H.posts.filter((p) => p.path.startsWith("/chat"))).toEqual([]);
  });
});

/* =========================================================== turn receipt */

describe("turn receipt names who answered in plain words (receipt-raw-provider-ids)", () => {
  it("the collapsed 'who' reads 'Claude Code'; the raw id stays in a title", () => {
    render(<TurnReceipt route={{ provider: "claude-cli", model: "claude-fable-5", reason: "default" }} />);
    const toggle = screen.getByRole("button", { expanded: false });
    expect(toggle.textContent).toContain("Claude Code");
    expect(toggle.textContent).not.toContain("claude-cli");
    expect(reachable(toggle, "claude-cli")).toBe(true);
    // Quiet path: no warning.
    expect(screen.queryByText(/answered by/)).toBeNull();
  });

  it("failover and mismatch warnings name both sides through providerDisplay", () => {
    expect(
      routeWarning({ requested: "", provider: "codex-cli", reason: "failover", from: "claude-cli", why: "timeout" }),
    ).toBe("answered by Codex (Claude Code didn't respond in time)");
    // A custom endpoint id has no friendly name: it passes through unchanged.
    expect(
      routeWarning({ requested: "", provider: "claude-cli", reason: "failover", from: "fleet-rtx6000ada", why: "http 500" }),
    ).toBe("answered by Claude Code (fleet-rtx6000ada returned HTTP 500)");
    expect(routeWarning({ requested: "", provider: "claude-cli", reason: "failover", from: "ollama" })).toBe(
      "answered by Claude Code (Ollama was not available)",
    );
    expect(routeWarning({ requested: "codex-cli", provider: "claude-cli", reason: "explicit" })).toBe(
      "answered by Claude Code (you asked for Codex)",
    );
  });

  it("the failover chip stays amber and visible WITHOUT expanding; the expanded detail keeps the raw ids", () => {
    render(
      <TurnReceipt
        route={{
          requested: "",
          provider: "claude-cli",
          model: "claude-fable-5",
          reason: "failover",
          from: "codex-cli",
          why: "http 500",
        }}
      />,
    );
    // v1.329.0 (W6 H1): the chip names the model (as a normal row does); the
    // provider stays in its title and in the expanded detail (asserted below).
    const chip = screen.getByText(/answered by claude-fable-5 \(Codex returned HTTP 500\)/);
    expect(chip.getAttribute("title")).toContain("Served by Claude Code.");
    expect(chip.className).toMatch(/text-tone-warn/);
    const toggle = screen.getByRole("button", { expanded: false });
    expect(toggle.textContent).not.toContain("claude-cli");
    fireEvent.click(toggle);
    const panelId = toggle.getAttribute("aria-controls");
    const panel = panelId ? document.getElementById(panelId) : null;
    expect(panel).not.toBeNull();
    expect(panel!.textContent).toContain("Claude Code");
    // A record of what ran: the exact ids are still reachable in the detail.
    expect(reachable(panel!, "claude-cli")).toBe(true);
    expect(reachable(panel!, "codex-cli")).toBe(true);
    expect(panel!.textContent).toContain("claude-fable-5");
  });

  it("CONTROL: the demo-model warning still outranks everything, amber, without expanding", () => {
    render(<TurnReceipt route={{ provider: "mock", reason: "failover", from: "ollama", why: "http 500" }} />);
    const chip = screen.getByText(/No real model ran/);
    expect(chip.className).toMatch(/text-tone-warn/);
    expect(screen.getByRole("button", { expanded: false }).textContent).toMatch(/No real model ran/);
  });
});

/* ========================================================== approval card */

const LONG_PATH =
  "C:/Users/Someone/Documents/Clients/2026/Northwind Traders Holdings LLC/Quarterly Reports/Q3/Reconciliation working papers - final.xlsx";

function leadOf(re: RegExp): HTMLElement {
  return screen.getByText(re) as HTMLElement;
}

describe("approval card says the action in words (approval-card-tool-ids)", () => {
  it("write_file leads with 'save a file'; the exact tool id and every answer stay", () => {
    render(
      <ApprovalCard approval={{ id: "apr_w", callId: "c1", tool: "write_file", args: { path: LONG_PATH } }} />,
    );
    const card = screen.getByTestId("chat-approval-card");
    const lead = leadOf(/Your call/);
    expect(lead.textContent).toMatch(/save a file/i);
    // The record: the exact tool id is still on the card, and the aria-label keeps it.
    expect(reachable(card, "write_file")).toBe(true);
    expect(card.getAttribute("aria-label")).toBe("Approve write_file?");
    // Every consent answer is still there.
    expect(screen.getByRole("button", { name: "Allow once" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Allow for this conversation" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Deny" })).toBeTruthy();
    expect(screen.getByTestId("approval-waiting")).toBeTruthy();
  });

  it("a long argument wraps in full instead of being cut off", () => {
    render(
      <ApprovalCard approval={{ id: "apr_w", callId: "c1", tool: "write_file", args: { path: LONG_PATH } }} />,
    );
    const line = screen.getByText((_, el) => !!el && el.tagName === "P" && (el.textContent ?? "").includes(LONG_PATH));
    expect(line.textContent).toContain(LONG_PATH);
    expect(classes(line)).not.toContain("truncate");
  });

  // v1.314.0 fix round: the batch case uses a BUILT-IN id. `rename_real_file`
  // is an agent-authored custom tool (tools/dynamic.py) whose argv can be any
  // program, so the card must not describe it in the app's own words.
  it("a batch leads with 'rename or move a file'; count, examples (wrapped), and both batch answers stay", () => {
    render(
      <ApprovalCard
        approval={{
          id: "apr_b",
          callId: "c1",
          tool: "rename_file",
          args: { source: "1.pdf" },
          count: 8,
          examples: [{ source: LONG_PATH }, { source: "2.pdf" }, { source: "3.pdf" }],
          timeoutS: 0,
        }}
      />,
    );
    const card = screen.getByTestId("chat-approval-card");
    expect(leadOf(/One answer/).textContent).toMatch(/rename or move a file/i);
    expect(screen.getByTestId("approval-count").textContent).toBe("× 8");
    expect(reachable(card, "rename_file")).toBe(true);
    expect(card.getAttribute("aria-label")).toBe("Approve rename_file × 8?");
    const ex = screen.getByText((_, el) => !!el && el.tagName === "P" && (el.textContent ?? "").includes(LONG_PATH));
    expect(classes(ex)).not.toContain("truncate");
    expect(screen.getByText("…and 5 more like these")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Allow these 8" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Deny all 8" })).toBeTruthy();
  });

  it("shell leads with 'run a command' and still shows the verbatim command", () => {
    render(
      <ApprovalCard approval={{ id: "apr_s", callId: "c1", tool: "shell", args: { command: "git status --short" } }} />,
    );
    expect(leadOf(/Your call/).textContent).toMatch(/run a command/i);
    const pre = screen.getByText("git status --short");
    expect(pre.tagName).toBe("PRE");
    expect(reachable(screen.getByTestId("chat-approval-card"), "shell")).toBe(true);
  });

  it("a browser action leads with 'act in a browser tab'; its tab answer and note stay", () => {
    render(
      <ApprovalCard approval={{ id: "apr_c", callId: "c1", tool: "browser_click", args: { selector: "#pay" } }} />,
    );
    expect(leadOf(/Your call/).textContent).toMatch(/browser tab/i);
    expect(screen.getByTestId("approval-tab")).toBeTruthy();
    expect(screen.getByTestId("approval-tab-note")).toBeTruthy();
    expect(reachable(screen.getByTestId("chat-approval-card"), "browser_click")).toBe(true);
  });

  it("CONTROL: an unknown tool falls back to 'run <its id>' — no invented words", () => {
    render(<ApprovalCard approval={{ id: "apr_u", callId: "c1", tool: "frobnicate_widget", args: {} }} />);
    const lead = leadOf(/Your call/);
    expect(lead.textContent).toMatch(/run/);
    expect(lead.textContent).toContain("frobnicate_widget");
  });

  it("the words live in one map, lib/toolWords.ts (unknown → null)", async () => {
    // A computed specifier: a literal one fails the whole FILE at import
    // analysis while the module does not exist yet (only this test goes red).
    const spec = ["@/lib", "toolWords"].join("/");
    const mod = (await import(/* @vite-ignore */ spec)) as { toolWords: (t: string) => string | null };
    expect(mod.toolWords("write_file")).toMatch(/save a file/i);
    expect(mod.toolWords("rename_file")).toMatch(/rename or move a file/i);
    // An agent-authored custom tool is never described in the app's words.
    expect(mod.toolWords("rename_real_file")).toBeNull();
    expect(mod.toolWords("shell")).toMatch(/run a command/i);
    expect(mod.toolWords("browser_navigate")).toMatch(/browser tab/i);
    // v1.314.0 review: an agent-made custom tool may take a browser_* name
    // (tools/dynamic.py) and run any command — never the browser's words.
    expect(mod.toolWords("browser_backup")).toBeNull();
    expect(mod.toolWords("frobnicate_widget")).toBeNull();
  });
});

/* ======================================================= model menu panel */

describe("title-bar model menu is opaque (carry-model-menu-opaque)", () => {
  it("the open panel has an opaque background (or is portaled so its own blur works); rows and the connect link stay", async () => {
    H.getResponses["/models"] = CATALOG_WITH_LABEL;
    render(<ModelSwitcher />);
    const trigger = await screen.findByRole("button", { name: /switch the active model/i });
    const root = trigger.parentElement as HTMLElement;
    fireEvent.click(trigger);
    const heading = await screen.findByText("Active model");
    let panel: HTMLElement | null = heading;
    while (panel && panel.parentElement !== root && panel.parentElement !== document.body) {
      panel = panel.parentElement;
    }
    expect(panel).not.toBeNull();
    const bg = classes(panel!).filter((c) => /^bg-/.test(c));
    const portaled = panel!.parentElement === document.body;
    const opaque = bg.length > 0 && bg.every((c) => !c.includes("/"));
    if (!portaled) {
      expect(opaque).toBe(true);
      expect(classes(panel!).some((c) => c.startsWith("backdrop-blur"))).toBe(false);
    } else {
      expect(opaque || classes(panel!).some((c) => c.startsWith("backdrop-blur"))).toBe(true);
    }
    // Everything the panel offered is still in it.
    expect(within(panel!).getByTestId("ij-model-list")).toBeTruthy();
    expect(within(panel!).getByText("All models")).toBeTruthy();
    expect(within(panel!).getByText(/Connect another account/)).toBeTruthy();
  });
});

/* ======================================================== source anchors */

describe("source anchors", () => {
  const src = (rel: string) => readFileSync(path.join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");

  it("the chat page passes the opt-in row layout to its ConnectDoors (the wizard does not)", () => {
    expect(src("app/chat/page.tsx")).toMatch(/<ConnectDoors[^>]*layout="row"/);
    expect(src("components/FirstRunWizard.tsx")).not.toMatch(/<ConnectDoors[^>]*layout="row"/);
  });

  it("TurnReceipt and the chat page read provider names through providerDisplay", () => {
    expect(src("components/chat/TurnReceipt.tsx")).toMatch(/import\s*\{[^}]*\bproviderDisplay\b[^}]*\}\s*from\s*"@\/lib\/onboarding"/);
  });
});
