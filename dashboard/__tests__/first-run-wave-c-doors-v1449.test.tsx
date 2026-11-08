/**
 * v1.449.0 — readiness Wave C (first run and connections), the connect doors.
 *
 *  ITEM 20 A Claude Pro / ChatGPT Plus subscriber WITHOUT the command-line
 *          tool gets "Install Claude Code for me" / "Install Codex for me":
 *          a Build terminal running the vendor's own installer, asked for by
 *          the daemon's job NAME (never a command from the page), never
 *          silently; then "I've installed it — check now" (the rescan); the
 *          terminal is remembered for the visit so no second one opens.
 *  ITEM 21 The Ollama door reads the row's `installed` / `running` /
 *          `detected`: step 2 greyed until Ollama is on this PC, "running but
 *          has no model yet" said in words, step 3 folded into the ONE "Use
 *          Ollama for answers" press (the use-model press — the daemon saves
 *          this PC's address as part of it), Gemini's free key named as the
 *          quick alternative.
 *  ITEM 25 The Anthropic and OpenAI tabs say where a key comes from and
 *          that it is billed per use; the model-servers tab reads the
 *          `?check=1` answer, so a failed check says "Nothing was saved".
 *
 * ConnectDoors is the real component; only the transport and the daemon
 * hook are mocked (the first-ten-minutes-connect-v1443 harness).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
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
    posts: [] as { path: string; body: unknown }[],
    puts: [] as { path: string; body: unknown }[],
    postResponses: {} as Record<string, unknown>,
    /** POST /terminals/launch: a pane id, or an error to throw. */
    launch: { id: "pane-7" } as unknown,
    health: null as Record<string, unknown> | null,
    refresh: vi.fn(),
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: vi.fn(async () => ({})),
  post: vi.fn(async (path: string, body?: unknown) => {
    H.posts.push({ path, body });
    if (path in H.postResponses) {
      const r = H.postResponses[path];
      if (r instanceof Error) throw r;
      return r;
    }
    if (path === "/onboarding/use-model") {
      const provider = (body as { provider?: string })?.provider ?? "";
      return { promoted: { provider, model: "llama3.2" }, reason: "" };
    }
    if (path === "/terminals/launch") {
      if (H.launch instanceof Error) throw H.launch;
      return H.launch;
    }
    if (path === "/providers/rescan") return { detected: [] };
    return {};
  }),
  put: vi.fn(async (path: string, body?: unknown) => {
    H.puts.push({ path, body });
    return {};
  }),
  patch: vi.fn(async () => ({})),
  del: vi.fn(async () => ({})),
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

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import { ConnectDoors, DOORS_VISIT_KEY } from "@/components/onboarding/ConnectDoors";
import { INSTALLED_CHECK_LABEL } from "@/lib/cliInstall";
import { cacheSet } from "@/lib/apiCache";

type Row = Record<string, unknown>;

const NOTHING: Row[] = [
  { provider: "mock", available: true },
  { provider: "anthropic", available: false, class: "api", inherited_from: null },
  { provider: "openai", available: false, class: "api", inherited_from: null },
  { provider: "claude-cli", available: false, class: "cli", installed: false, signed_in: null, sign_in_fix: "" },
  { provider: "codex-cli", available: false, class: "cli", installed: false, signed_in: null, sign_in_fix: "" },
  { provider: "ollama", available: false, class: "local", detected: false, installed: false, running: false },
];

function health(over: Record<string, Row> = {}, defaultProvider = "mock") {
  return {
    default_provider: defaultProvider,
    default_model: defaultProvider === "mock" ? "mock" : "x",
    providers: NOTHING.map((r) => (over[String(r.provider)] ? { ...r, ...over[String(r.provider)] } : r)),
  };
}

beforeEach(() => {
  H.posts.length = 0;
  H.puts.length = 0;
  H.postResponses = {};
  H.launch = { id: "pane-7" };
  H.health = health();
  H.refresh.mockReset();
  cacheSet(DOORS_VISIT_KEY, { door: null, panes: {} });
});
afterEach(cleanup);

const openSub = () => fireEvent.click(screen.getByRole("button", { name: /I already pay/ }));
const openLocal = () => fireEvent.click(screen.getByRole("button", { name: /Free & private/ }));
const openKey = () => fireEvent.click(screen.getByRole("button", { name: /I have an API key/ }));
const launches = () => H.posts.filter((p) => p.path === "/terminals/launch");

/* ============================================================ ITEM 20 === */

describe("item 20 — Install Claude Code / Codex for me", () => {
  it("a not-installed row offers the press; it asks for the daemon's job by name and names the terminal it opened", async () => {
    render(<ConnectDoors />);
    openSub();
    const claude = screen.getByTestId("install-claude");
    expect(claude.textContent).toBe("Install Claude Code for me");
    expect(screen.getByTestId("install-codex").textContent).toBe("Install Codex for me");
    // Never a command on screen; the page says it never installs quietly.
    expect(document.body.textContent).not.toMatch(/\birm\b|\biex\b|npm install/);
    expect(document.body.textContent).toMatch(/nothing is installed quietly/);
    // The vendor's page stays as the way to read first.
    expect(screen.getAllByRole("link", { name: /read the vendor's install page/ })).toHaveLength(2);

    fireEvent.click(claude);
    const line = await screen.findByTestId("install-pane-claude");
    expect(launches()).toEqual([{ path: "/terminals/launch", body: { cli: "claude-code-install" } }]);
    expect(line.textContent).toMatch(/A Build terminal opened and is installing Claude Code/);
    expect(line.textContent).toMatch(/nothing runs hidden/);
    expect(within(line).getByRole("link", { name: "Open the Build terminal" }).getAttribute("href")).toBe(
      "/terminals?focus=pane-7",
    );
    // The press replaced itself: it cannot open a second terminal.
    expect(screen.queryByTestId("install-claude")).toBeNull();
    // Codex's press is untouched.
    expect(screen.getByTestId("install-codex")).toBeTruthy();
    expect(H.refresh).toHaveBeenCalled();
  });

  it("Codex's press asks for its own job", async () => {
    render(<ConnectDoors />);
    openSub();
    fireEvent.click(screen.getByTestId("install-codex"));
    await screen.findByTestId("install-pane-codex");
    expect(launches()).toEqual([{ path: "/terminals/launch", body: { cli: "codex-install" } }]);
  });

  it("'I've installed it — check now' re-scans; a tool still missing is said, one found says nothing", async () => {
    render(<ConnectDoors />);
    openSub();
    fireEvent.click(screen.getByTestId("install-claude"));
    await screen.findByTestId("install-pane-claude");
    // The rescan lists a subscription CLI only when its program is on the PC.
    H.postResponses["/providers/rescan"] = { detected: [] };
    fireEvent.click(screen.getByTestId("installed-check-claude"));
    const note = await screen.findByTestId("installed-check-note-claude");
    expect(note.textContent).toMatch(/Claude Code still isn't found on this PC/);
    expect(note.textContent).toContain(INSTALLED_CHECK_LABEL);
    expect(H.posts.filter((p) => p.path === "/providers/rescan")).toHaveLength(1);

    H.postResponses["/providers/rescan"] = { detected: [{ provider: "claude-cli", signed_in: false }] };
    fireEvent.click(screen.getByTestId("installed-check-claude"));
    await waitFor(() => expect(H.posts.filter((p) => p.path === "/providers/rescan")).toHaveLength(2));
    await waitFor(() => expect(screen.queryByTestId("installed-check-note-claude")).toBeNull());
  });

  it("a refusal is said in the daemon's own words and the press stays", async () => {
    H.launch = new H.FakeApiError("Too many terminals are open. Close one and try again.", 429);
    render(<ConnectDoors />);
    openSub();
    fireEvent.click(screen.getByTestId("install-claude"));
    const alert = await screen.findByText("Too many terminals are open. Close one and try again.");
    expect(alert.getAttribute("role")).toBe("alert");
    expect(screen.getByTestId("install-claude")).toBeTruthy();
    expect(screen.queryByTestId("install-pane-claude")).toBeNull();
  });

  it("the terminal is remembered for the visit: coming back from Build never opens a second one", async () => {
    const first = render(<ConnectDoors />);
    openSub();
    fireEvent.click(screen.getByTestId("install-claude"));
    await screen.findByTestId("install-pane-claude");
    first.unmount();
    render(<ConnectDoors />);
    // The door is still open and the row still points at the terminal.
    const line = await screen.findByTestId("install-pane-claude");
    expect(within(line).getByRole("link", { name: "Open the Build terminal" }).getAttribute("href")).toBe(
      "/terminals?focus=pane-7",
    );
    expect(screen.queryByTestId("install-claude")).toBeNull();
    expect(launches()).toHaveLength(1);
    // A closed terminal can be opened again, on purpose.
    expect(screen.getByTestId("install-again-claude")).toBeTruthy();
  });

  it("once the tool is found the row offers Sign in, not Install (the rescan path hands over)", () => {
    H.health = health({ "claude-cli": { installed: true, signed_in: false } });
    render(<ConnectDoors />);
    openSub();
    expect(screen.queryByTestId("install-claude")).toBeNull();
    expect(screen.getByRole("button", { name: "Sign in to Claude Code" })).toBeTruthy();
  });
});

/* ============================================================ ITEM 21 === */

describe("item 21 — the Ollama door tells three facts and ends in one press", () => {
  it("nothing on this PC: step 2 and step 3 are greyed and say why; Gemini's free key is the quick alternative", () => {
    render(<ConnectDoors />);
    openLocal();
    expect(screen.queryByTestId("ollama-on-pc")).toBeNull();
    const download = screen.getByTestId("starter-model-download") as HTMLButtonElement;
    expect(download.disabled).toBe(true);
    expect(screen.getByTestId("starter-model-wait").textContent).toMatch(/Install Ollama first \(step 1\)/);
    const use = screen.getByTestId("ollama-use") as HTMLButtonElement;
    expect(use.textContent).toBe("Use Ollama for answers");
    expect(use.disabled).toBe(true);
    expect(screen.getByTestId("ollama-use-wait").textContent).toMatch(/once Ollama is running with a model/);
    // The old second press is gone: step 3 IS the press.
    expect(screen.queryByRole("button", { name: "Connect to Ollama on this PC" })).toBeNull();
    const alt = screen.getByTestId("ollama-gemini-alternative");
    expect(alt.textContent).toMatch(/Prefer no download\? A Google Gemini key has a free tier/);
  });

  it("the Gemini alternative opens the key door on the Google tab", () => {
    render(<ConnectDoors />);
    openLocal();
    fireEvent.click(screen.getByTestId("ollama-gemini-switch"));
    expect(screen.getByPlaceholderText("AQ.… or AIza…")).toBeTruthy();
    expect(screen.getByTestId("key-door-free-tier")).toBeTruthy();
  });

  it("running but no model yet: step 1 is done, step 2 is live and the line says what to do, step 3 waits for the model", () => {
    H.health = health({ ollama: { installed: true, running: true, detected: false } });
    render(<ConnectDoors />);
    openLocal();
    expect(screen.getByTestId("ollama-on-pc").textContent).toMatch(/Ollama is on this PC/);
    expect((screen.getByTestId("starter-model-download") as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByTestId("starter-model-wait")).toBeNull();
    expect(screen.getByTestId("ollama-no-model").textContent).toBe(
      "Ollama is running but has no model yet — press Download a starter model.",
    );
    expect((screen.getByTestId("ollama-use") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId("ollama-use-wait").textContent).toMatch(/once a model is downloaded \(step 2\)/);
  });

  it("installed but not running: step 2 is live, no 'no model' line, step 3 waits", () => {
    H.health = health({ ollama: { installed: true, running: false, detected: false } });
    render(<ConnectDoors />);
    openLocal();
    expect((screen.getByTestId("starter-model-download") as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByTestId("ollama-no-model")).toBeNull();
    expect((screen.getByTestId("ollama-use") as HTMLButtonElement).disabled).toBe(true);
  });

  it("running with a model: the ONE press posts use-model and says the pressed words", async () => {
    H.health = health({ ollama: { installed: true, running: true, detected: true } });
    const chosen = vi.fn();
    render(<ConnectDoors onChosen={chosen} />);
    openLocal();
    expect(screen.queryByTestId("ollama-no-model")).toBeNull();
    const use = screen.getByTestId("ollama-use") as HTMLButtonElement;
    expect(use.disabled).toBe(false);
    fireEvent.click(use);
    await waitFor(() => expect(chosen).toHaveBeenCalledWith("Ollama (on this PC)"));
    expect(H.posts.filter((p) => p.path === "/onboarding/use-model").map((p) => p.body)).toEqual([
      { provider: "ollama" },
    ]);
    // No PUT from the page: the daemon saves the address as part of the press.
    expect(H.puts).toEqual([]);
  });

  it("the daemon's refusal (running, no model) is shown in its words, with no command", async () => {
    H.health = health({ ollama: { installed: true, running: true, detected: true } });
    H.postResponses["/onboarding/use-model"] = new H.FakeApiError(
      "Ollama (on this PC) is running, but it has no model yet. Press “Download a starter model” on the connect door, or add one in Ollama's own window, then try again.",
      409,
    );
    render(<ConnectDoors />);
    openLocal();
    fireEvent.click(screen.getByTestId("ollama-use"));
    const refusal = await screen.findByRole("alert");
    expect(refusal.textContent).toMatch(/has no model yet/);
    expect(refusal.textContent).toMatch(/Download a starter model/);
    expect(refusal.textContent).not.toMatch(/ollama pull/);
  });

  it("CONTROL: an older daemon (no running field) greys nothing", () => {
    H.health = health({ ollama: { installed: undefined, running: undefined, detected: false } });
    render(<ConnectDoors />);
    openLocal();
    expect((screen.getByTestId("starter-model-download") as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByTestId("ollama-use") as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByTestId("starter-model-wait")).toBeNull();
    expect(screen.queryByTestId("ollama-use-wait")).toBeNull();
  });

  it("CONTROL: with a real default already chosen the connect press stands (nothing to choose)", async () => {
    H.health = health({ ollama: { installed: true, running: true, detected: true } }, "anthropic");
    render(<ConnectDoors />);
    openLocal();
    expect(screen.queryByTestId("ollama-use")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Connect to Ollama on this PC" }));
    await waitFor(() => expect(H.puts).toHaveLength(1));
    expect(H.puts[0]).toEqual({ path: "/settings", body: { values: { ollama_base_url: "http://localhost:11434" } } });
  });
});

/* ============================================================ ITEM 25 === */

describe("item 25 — the key tabs say where a key comes from and how it is billed", () => {
  it("Anthropic: the Console link and 'per use'", () => {
    render(<ConnectDoors />);
    openKey();
    const line = screen.getByTestId("key-door-get-one");
    expect(within(line).getByRole("link", { name: "the Anthropic Console" }).getAttribute("href")).toBe(
      "https://console.anthropic.com/settings/keys",
    );
    expect(screen.getByTestId("key-door-billing").textContent).toBe(
      " Anthropic bills that account per use (pay as you go) — it is separate from a Claude plan.",
    );
  });

  it("OpenAI: the platform link and 'per use'", () => {
    render(<ConnectDoors />);
    openKey();
    fireEvent.click(screen.getByRole("button", { name: "OpenAI" }));
    const line = screen.getByTestId("key-door-get-one");
    expect(within(line).getByRole("link", { name: "OpenAI's platform" }).getAttribute("href")).toBe(
      "https://platform.openai.com/api-keys",
    );
    expect(screen.getByTestId("key-door-billing").textContent).toMatch(/OpenAI bills that account per use/);
  });

  it("CONTROL: Gemini keeps its free-tier line and has no billing sentence", () => {
    render(<ConnectDoors />);
    openKey();
    fireEvent.click(screen.getByRole("button", { name: "Google Gemini" }));
    expect(screen.getByTestId("key-door-free-tier")).toBeTruthy();
    expect(screen.queryByTestId("key-door-billing")).toBeNull();
  });

  it("the model-servers tab: a key that fails its check says 'Nothing was saved' and asks no Test", async () => {
    H.postResponses["/connections/custom/key?check=1"] = {
      saved: false,
      status: "not_saved",
      test: { ok: false, kind: "refused_key", title: "Your model server refused the key", fix: "Check it.", detail: "401" },
    };
    render(<ConnectDoors />);
    openKey();
    fireEvent.click(screen.getByRole("button", { name: /Model servers/ }));
    fireEvent.change(screen.getByLabelText(/address/i), { target: { value: "http://localhost:1234/v1" } });
    fireEvent.change(screen.getByPlaceholderText("sk-… (optional)"), { target: { value: "sk-wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    const result = await screen.findByTestId("key-door-result");
    expect(result.textContent).toMatch(/Your model server refused the key/);
    expect(result.textContent).toMatch(/Nothing was saved, and what answers you didn't change\./);
    expect(H.posts.map((p) => p.path)).toEqual(["/connections/custom/key?check=1"]);
    // The key stays in the box for a fix.
    expect((screen.getByPlaceholderText("sk-… (optional)") as HTMLInputElement).value).toBe("sk-wrong");
  });

  it("CONTROL: a saved model-server key goes on to the Test, as before", async () => {
    H.postResponses["/connections/custom/key?check=1"] = { saved: true, status: "connected" };
    H.postResponses["/connections/custom/test"] = { ok: true, detail: "custom reachable — credential accepted" };
    render(<ConnectDoors />);
    openKey();
    fireEvent.click(screen.getByRole("button", { name: /Model servers/ }));
    fireEvent.change(screen.getByLabelText(/address/i), { target: { value: "http://localhost:1234/v1" } });
    fireEvent.change(screen.getByPlaceholderText("sk-… (optional)"), { target: { value: "sk-good" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    await screen.findByTestId("key-door-result");
    expect(H.posts.map((p) => p.path).slice(0, 2)).toEqual([
      "/connections/custom/key?check=1",
      "/connections/custom/test",
    ]);
  });
});
