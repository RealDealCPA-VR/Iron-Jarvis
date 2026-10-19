/**
 * v1.353.0 (wave 3, lane 3A) — "a clean first five minutes", on the chat home.
 *
 *  G34 Ollama: one answer, one press — a running Ollama (/health `detected`)
 *      is offered by the doors as "Use Ollama (on this PC) for answers" even
 *      before its address is saved (the daemon's press saves it).
 *  G35 Choosing a model is confirmed on the home, in the words pressed — the
 *      "Done" note named the promoted API row with raw ids and vanished with
 *      the doors; the home now keeps "Done — <label> answers you now." The
 *      heading no longer says "on this PC" (a cloud key is ready too) and the
 *      key door's success line says the next step while replies are a demo.
 *  G36 The empty chat shows the machine check ("This PC: 5 of 6 helpers
 *      ready — … · Details") from ONE read of GET /onboarding, and once a real
 *      model answers, "Connect the apps you use" leading to the Directory.
 *  G57 The model menu, the Settings rows and the compaction card use names,
 *      not provider ids.
 *
 * The chat page is rendered for real (the chat-connect-doors-v1310 harness);
 * ConnectDoors, SettingRow and CompactionCard are the real components.
 */

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
    gets: [] as string[],
    posts: [] as Array<{ path: string; body: unknown }>,
    postResponses: {} as Record<string, unknown>,
    health: null as Record<string, unknown> | null,
    onRefresh: null as null | (() => void),
    refresh: vi.fn(),
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.gets.push(path);
    const r = H.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body?: unknown) => {
    H.posts.push({ path, body });
    if (path in H.postResponses) return H.postResponses[path];
    if (path === "/onboarding/use-model") {
      return { promoted: { provider: "anthropic", model: "claude-opus-4-8" }, reason: "" };
    }
    return {};
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
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
      run: async () => ({ reply: "" }),
      abort: () => {},
    }),
  };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
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
import { chosenNote } from "@/components/onboarding/AnswerPress";
import { appNames, decodeHomeReadiness, machineText, showMachineLine } from "@/components/chat/HomeReadiness";
import { SettingRow, type SchemaSetting } from "@/components/settings/SettingRow";
import { CompactionCard } from "@/components/chat/CompactionCard";
import { answerCandidates, modelGroupName, providerDisplay, providerHeading } from "@/lib/onboarding";
import type { Health } from "@/lib/types";

type Row = Record<string, unknown>;

const NOTHING_CONNECTED: Row[] = [
  { provider: "anthropic", available: false, class: "api", inherited_from: null },
  { provider: "openai", available: false, class: "api", inherited_from: null },
  { provider: "claude-cli", available: false, class: "cli", installed: false, signed_in: null, sign_in_fix: "" },
  { provider: "codex-cli", available: false, class: "cli", installed: false, signed_in: null, sign_in_fix: "" },
  { provider: "ollama", available: false, class: "local", inherited_from: null, detected: false },
];
const with_ = (over: Record<string, Row>): Row[] =>
  NOTHING_CONNECTED.map((r) => (over[String(r.provider)] ? { ...r, ...over[String(r.provider)] } : r));
const CLAUDE_SIGNED_IN = with_({
  "claude-cli": { available: true, installed: true, signed_in: true },
  anthropic: { available: true, inherited_from: "claude-cli" },
});

/** The end-user machine checks as GET /onboarding sends them (+ a dev row
 *  an older daemon might still send — never counted). */
const CHECKS = [
  { name: "browser", ok: true, level: "recommended", label: "A web browser for browsing tasks" },
  { name: "pdf scan routing", ok: true, level: "recommended", label: "Spotting scanned pages inside PDFs" },
  { name: "local_ocr", ok: true, level: "recommended", label: "Reading scanned pages on this PC" },
  { name: "old Office files", ok: false, level: "recommended", label: "Reading old .doc and .xls files" },
  { name: "guide_docs", ok: true, level: "required", label: "The built-in help guides" },
  { name: "browser_addon", ok: true, level: "recommended", label: "The browser add-on" },
  { name: "node", ok: false, level: "recommended", label: "Node" },
];

function setHealth(providers: Row[], defaultProvider: string) {
  H.health = {
    status: "ok",
    version: "1.353.0",
    default_provider: defaultProvider,
    default_model: "claude-opus-4-8",
    providers,
  };
}

function setOnboarding(over: Record<string, unknown> = {}, appsDone = false) {
  H.getResponses["/onboarding"] = {
    version: "1.353.0",
    first_run: true,
    doctor: { ok: true, checks: CHECKS },
    checklist: [
      { key: "connect_ai", done: false, optional: false },
      { key: "set_up_voice", done: false, optional: true },
      { key: "connect_apps", done: appsDone, optional: true, href: "/marketplace" },
    ],
    next_step: { key: "connect_ai" },
    model: { default_provider: "mock", default_model: "", is_mock: true, usable: [] },
    ...over,
  };
}

function resetApi() {
  for (const k of Object.keys(H.getResponses)) delete H.getResponses[k];
  for (const k of Object.keys(H.postResponses)) delete H.postResponses[k];
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
}

const FIRST_CHIP = "What can you do?";

beforeEach(() => {
  resetApi();
  H.gets = [];
  H.posts = [];
  H.onRefresh = null;
  H.refresh = vi.fn(() => H.onRefresh?.());
  setHealth(NOTHING_CONNECTED, "mock");
  setOnboarding();
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/* ================================================================ G35 === */

describe("G35 — the choice is confirmed on the home, in the words pressed", () => {
  it("'Use Claude (your Claude Code sign-in)' → the home keeps saying it after the doors are gone", async () => {
    setHealth(CLAUDE_SIGNED_IN, "mock");
    // The press refreshes /health; the daemon now answers with the new default.
    H.onRefresh = () => setHealth(CLAUDE_SIGNED_IN, "anthropic");
    render(<ChatPage />);
    await screen.findByRole("button", { name: FIRST_CHIP });
    expect(screen.queryByTestId("home-chosen")).toBeNull(); // nothing claimed before the press
    fireEvent.click(
      await screen.findByRole("button", { name: "Use Claude (your Claude Code sign-in) for answers" }),
    );
    const done = await screen.findByTestId("home-chosen");
    expect(done.textContent).toBe("Done — Claude (your Claude Code sign-in) answers you now. Ask anything below.");
    expect(done.getAttribute("role")).toBe("status");
    // The doors are gone (a real model answers) — the line stays.
    await waitFor(() => expect(screen.queryByRole("button", { name: /I already pay/ })).toBeNull());
    expect(screen.getByTestId("home-chosen").textContent).toMatch(/^Done — Claude \(your Claude Code sign-in\)/);
    expect(H.posts.filter((p) => p.path === "/onboarding/use-model")).toHaveLength(1);
  });

  it("a press the daemon declines ('you already chose X') claims nothing on the home", async () => {
    setHealth(CLAUDE_SIGNED_IN, "mock");
    H.postResponses["/onboarding/use-model"] = { promoted: null, reason: "You already chose Codex." };
    render(<ChatPage />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Use Claude (your Claude Code sign-in) for answers" }),
    );
    await screen.findByText("You already chose Codex.");
    expect(screen.queryByTestId("home-chosen")).toBeNull();
  });

  it("the press note itself uses the pressed label (no raw ids)", () => {
    expect(chosenNote("Claude (your Claude Code sign-in)")).toBe(
      "Done — Claude (your Claude Code sign-in) answers you now.",
    );
  });
});

/* ================================================================ G36 === */

describe("G36 — the empty chat's machine check and connect-your-apps line", () => {
  it("a brand-new user sees how many helpers are ready, the first missing one, and Details", async () => {
    render(<ChatPage />);
    const line = await screen.findByTestId("home-machine");
    // 6 end-user rows (the dev 'node' row never counts), 1 missing.
    expect(line.textContent).toBe("This PC: 5 of 6 helpers ready — Reading old .doc and .xls files · Details");
    expect(within(line).getByRole("link", { name: "Details" }).getAttribute("href")).toBe("/everything#status");
    // Replies are still the demo: no apps line yet.
    expect(screen.queryByTestId("home-apps")).toBeNull();
    // ONE read, never polled.
    expect(H.gets.filter((p) => p === "/onboarding")).toHaveLength(1);
  });

  it("once a real model answers, 'Connect the apps you use' leads to the Directory", async () => {
    setHealth(CLAUDE_SIGNED_IN, "anthropic");
    setOnboarding({
      first_run: false,
      next_step: { key: "first_session" },
      model: { default_provider: "anthropic", default_model: "claude-opus-4-8", is_mock: false, usable: [] },
    });
    render(<ChatPage />);
    const apps = await screen.findByTestId("home-apps");
    const link = within(apps).getByRole("link", { name: "Connect the apps you use" });
    expect(link.getAttribute("href")).toBe("/marketplace");
    expect(apps.textContent).toBe("Connect the apps you use — GitHub, Notion, Slack…");
  });

  it("no apps line while /health is still unknown (not knowing is not 'a real model answers')", async () => {
    H.health = null;
    render(<ChatPage />);
    await screen.findByTestId("home-machine"); // the /onboarding read has landed
    expect(screen.queryByTestId("home-apps")).toBeNull();
  });

  it("CONTROL: an app already connected — no apps line; a settled PC — no machine line", async () => {
    setHealth(CLAUDE_SIGNED_IN, "anthropic");
    setOnboarding(
      {
        first_run: false,
        next_step: null,
        doctor: { ok: true, checks: CHECKS.map((c) => ({ ...c, ok: true })) },
        model: { default_provider: "anthropic", default_model: "x", is_mock: false, usable: [] },
      },
      true,
    );
    render(<ChatPage />);
    await screen.findByRole("button", { name: FIRST_CHIP });
    await waitFor(() => expect(H.gets).toContain("/onboarding"));
    // Give the read a moment to land, then nothing may show.
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("home-apps")).toBeNull();
    expect(screen.queryByTestId("home-machine")).toBeNull();
  });

  it("decoding: counted the OnboardingWelcome way, never from doctor.ok; junk is nothing", () => {
    const h = decodeHomeReadiness({ doctor: { ok: true, checks: CHECKS }, checklist: [], next_step: null })!;
    expect([h.ready, h.total, h.firstMissing]).toEqual([5, 6, "Reading old .doc and .xls files"]);
    expect(h.requiredMissing).toBe(false); // only a recommended extra is missing
    expect(showMachineLine(h)).toBe(false); // a settled PC is not nagged by an extra
    const required = decodeHomeReadiness({
      doctor: { ok: false, checks: [{ name: "guide_docs", ok: false, level: "required", label: "Guides" }] },
    })!;
    expect(showMachineLine(required)).toBe(true);
    expect(machineText(required)).toBe("This PC: 0 of 1 helper ready — Guides");
    expect(decodeHomeReadiness(null)).toBeNull();
    expect(decodeHomeReadiness("nope")).toBeNull();
    expect(appNames(["Gmail", "Drive"])).toBe("Gmail, Drive…");
  });
});

/* ======================================================== G34 + doors === */

describe("G34 / G35 / G33 — the connect doors", () => {
  it("a running Ollama is ONE press: 'Use Ollama (on this PC) for answers' posts the press", async () => {
    setHealth(with_({ ollama: { detected: true } }), "mock");
    render(<ConnectDoors />);
    expect(screen.getByText("Ready — choose what answers you")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Use Ollama (on this PC) for answers" }));
    await waitFor(() =>
      expect(H.posts.find((p) => p.path === "/onboarding/use-model")?.body).toEqual({ provider: "ollama" }),
    );
    // Where the words go is said before the press.
    expect(document.body.textContent).toMatch(/With Ollama \(on this PC\), what you type stays on this PC\./);
  });

  it("CONTROL: Ollama not detected — no press for it", () => {
    render(<ConnectDoors />);
    expect(screen.queryByRole("button", { name: /Use Ollama/ })).toBeNull();
  });

  it("answerCandidates: a detected Ollama is a candidate once, a connected one is not doubled", () => {
    const h = (rows: Row[]) => ({ default_provider: "mock", providers: rows }) as unknown as Health;
    expect(answerCandidates(h(with_({ ollama: { detected: true } }))).map((c) => c.provider)).toEqual(["ollama"]);
    expect(
      answerCandidates(h(with_({ ollama: { detected: true, available: true } }))).map((c) => c.provider),
    ).toEqual(["ollama"]);
    expect(answerCandidates(h(NOTHING_CONNECTED))).toEqual([]);
  });

  it("the key door's success line says the next step while replies are a demo", async () => {
    H.postResponses["/connections/anthropic/test"] = { ok: true, detail: "Key works." };
    render(<ConnectDoors />);
    fireEvent.click(screen.getByRole("button", { name: /I have an API key/ }));
    fireEvent.change(screen.getByPlaceholderText("sk-ant-…"), { target: { value: "sk-ant-test" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect((await screen.findByRole("status")).textContent).toBe(
      "Connected — Key works. Choose it above to use it for answers.",
    );
  });

  it("a signed-out Claude Code door says press Sign in — never the terminal sentence", () => {
    setHealth(
      with_({
        "claude-cli": {
          installed: true,
          signed_in: false,
          sign_in_fix: "Claude Code isn't signed in. Open a terminal, run `claude`, then `/login`.",
        },
      }),
      "mock",
    );
    render(<ConnectDoors />);
    fireEvent.click(screen.getByRole("button", { name: /I already pay/ }));
    expect(screen.getByTestId("signed-out-line-claude").textContent).toBe(
      "Not signed in yet — press Sign in below. A window opens; type /login there and finish in your browser.",
    );
    expect(document.body.textContent).not.toMatch(/terminal/i);
    expect(screen.getByRole("button", { name: "Sign in to Claude Code" })).toBeTruthy();
  });
});

/* ================================================================ G57 === */

describe("G57 — names, not provider ids", () => {
  it("the composer's model menu lists Claude Code, Codex, OpenCode and Your model server", async () => {
    H.getResponses["/models"] = {
      models: [
        { provider: "anthropic", model: "claude-opus-4-8", kind: "api", available: true, inherited_from: "claude-cli" },
        { provider: "claude-cli", model: "subscription", kind: "cli", available: true },
        { provider: "codex-cli", model: "gpt-5.5", kind: "cli", available: true },
        { provider: "custom", model: "default", kind: "local", available: true },
        { provider: "opencode-cli", model: "lan/qwen3", kind: "api", available: true },
        { provider: "fleet-dgx", model: "glm-4.6", name: "DGX Spark", kind: "local", available: true },
        // A detected Ollama row names its MODEL in `name` — not a group name.
        { provider: "ollama", model: "llama3.2:latest", name: "llama3.2:latest", source: "cli", kind: "local", available: false },
      ],
    };
    setHealth(CLAUDE_SIGNED_IN, "anthropic");
    render(<ChatPage />);
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    fireEvent.click(await screen.findByTitle("Switch model"));
    const menu = await screen.findByTestId("model-menu");
    await waitFor(() => expect(within(menu).getAllByTestId("model-kind-badge").length).toBe(7));
    const names = within(menu)
      .getAllByTestId("model-kind-badge")
      .map((b) => b.closest("button")!.querySelector("span")!.textContent);
    expect(new Set(names)).toEqual(
      new Set(["Anthropic", "Claude Code", "Codex", "Your model server", "OpenCode", "DGX Spark", "Ollama"]),
    );
    expect(menu.textContent).not.toMatch(/\bclaude-cli\b|\bcodex-cli\b|\bopencode-cli\b|\bcustom\b|\bfleet-dgx\b/);
  });

  it("the helpers: OpenCode by name, a heading for the user's own server, an endpoint by its label", () => {
    expect(providerDisplay("opencode-cli")).toBe("OpenCode");
    expect(providerHeading("custom")).toBe("Your model server");
    expect(providerHeading("claude-cli")).toBe("Claude Code");
    expect(providerHeading("fleet-x")).toBe("");
    expect(modelGroupName({ provider: "fleet-x", model: "m", name: "Box" })).toBe("Box");
    expect(modelGroupName({ provider: "ollama", model: "q", name: "q" })).toBe("Ollama");
    expect(modelGroupName({ provider: "fleet-y", model: "m" })).toBe("fleet-y");
  });

  it("Settings › Models: the default provider is chosen by name, the value stays the id", () => {
    const def: SchemaSetting = { key: "default_provider", label: "Default provider", group: "models", section: "", type: "string" };
    const onChange = vi.fn();
    render(
      <SettingRow
        def={def}
        value="claude-cli"
        changed={false}
        onChange={onChange}
        providerOptions={["auto", "claude-cli", "custom", "opencode-cli", "mock"]}
      />,
    );
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => [o.value, o.textContent]);
    expect(options).toEqual([
      ["auto", "Auto"],
      ["claude-cli", "Claude Code"],
      ["custom", "Your model server"],
      ["opencode-cli", "OpenCode"],
      ["mock", "Demo model (scripted)"],
    ]);
    fireEvent.change(select, { target: { value: "custom" } });
    expect(onChange).toHaveBeenCalledWith("custom");
  });

  it("Settings › Models: the default model is picked from a list (its name), 'Other…' still takes an id", async () => {
    H.getResponses["/models"] = {
      models: [
        { provider: "claude-cli", model: "claude-opus-5-5", label: "Opus 5.5", available: true },
        { provider: "claude-cli", model: "claude-sonnet-5", label: "Sonnet 5", available: true },
        { provider: "openai", model: "gpt-5.5", available: true },
      ],
    };
    const def: SchemaSetting = { key: "default_model", label: "Default model", group: "models", section: "", type: "string" };
    const onChange = vi.fn();
    render(<SettingRow def={def} value="" changed={false} onChange={onChange} modelProvider="claude-cli" />);
    const select = (await screen.findByRole("combobox")) as HTMLSelectElement;
    await waitFor(() => expect(Array.from(select.options).map((o) => o.textContent)).toContain("Opus 5.5"));
    const texts = Array.from(select.options).map((o) => o.textContent);
    expect(texts).toEqual(["Not set", "Opus 5.5", "Sonnet 5", "Other… (type a model id)"]);
    expect(screen.queryByRole("textbox")).toBeNull(); // no free-text box by default
    fireEvent.change(select, { target: { value: "claude-opus-5-5" } });
    expect(onChange).toHaveBeenLastCalledWith("claude-opus-5-5");
    fireEvent.change(select, { target: { value: "__other_model__" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Default model id" }), { target: { value: "my-model" } });
    expect(onChange).toHaveBeenLastCalledWith("my-model");
  });

  it("the compaction card says who wrote it by name", () => {
    render(
      <CompactionCard
        info={{ found: true, summary: "s", covers: 2, stripped: 0, stripped_claims: [], provider: "claude-cli", model: "claude-opus-5-5" }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText(/written by Claude Code · claude-opus-5-5/)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/written by claude-cli/);
  });
});
