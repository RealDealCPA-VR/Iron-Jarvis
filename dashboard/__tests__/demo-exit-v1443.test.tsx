/**
 * v1.445.0 — the demo model is no longer a dead end (ease-of-use review
 * item 2). One user-visible behaviour per test, each red on the v1.442.1 code:
 *
 *  1. The strip under the title bar never calls the demo model (or a saved web
 *     sign-in) "a model is connected": a fresh install reads "no AI model is
 *     connected" and offers "Connect a model".
 *  2. When ONE press can choose a real model (a signed-in Claude Code…), the
 *     strip's button IS that press, "Use Claude Code for answers" — the
 *     default everywhere (POST /onboarding/use-model), not one chat's pick.
 *  3. "Connect a model" shows the connect choices where the user is (the
 *     chat's doors) and only goes to Connections when no screen shows them.
 *  4. The model menu says WHY a row is greyed in visible words, and ends with
 *     a "Connect a model…" row a keyboard reaches.
 *  5. After the first message the chat keeps a "Connect a model" press under
 *     the box for as long as the demo is the default.
 *  6. The downgrade banner stays quiet for a chat turn the demo answered (the
 *     strip, the reply and its receipt already say it).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { IJEvent, ModelOption } from "@/lib/types";

const H = vi.hoisted(() => ({
  health: null as Record<string, unknown> | null,
  refresh: vi.fn(),
  posts: [] as { path: string; body: unknown }[],
  answer: { promoted: { provider: "anthropic", model: "claude-opus-4-8" }, reason: "" } as unknown,
  events: [] as IJEvent[],
}));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: H.health, refresh: H.refresh }),
}));
vi.mock("@/lib/api", () => ({
  API_BASE: "",
  ijToken: () => "",
  get: async () => ({}),
  post: async (path: string, body: unknown) => {
    H.posts.push({ path, body });
    return H.answer;
  },
  put: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: H.events, connected: true }) }));
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    onClick,
    ...rest
  }: {
    href: string;
    children?: React.ReactNode;
    onClick?: (e: React.MouseEvent<HTMLAnchorElement>) => void;
  }) => (
    <a href={href} onClick={onClick} {...rest}>
      {children}
    </a>
  ),
}));
// The real doors are another lane's; a stub proves they are what opens.
vi.mock("@/components/onboarding/ConnectDoors", () => ({
  ConnectDoors: () => <div data-testid="connect-doors-stub">the connect doors</div>,
}));
vi.mock("next/dynamic", () => ({
  default: () =>
    function Doors() {
      return <div data-testid="connect-doors-stub">the connect doors</div>;
    },
}));

import { SimulatedBanner } from "@/components/SimulatedBanner";
import { ModelMenu } from "@/components/chat/ModelMenu";
import { DemoConnectChoice, OTHER_WAYS_TO_CONNECT } from "@/components/chat/DemoConnectChoice";
import { ProviderDowngradeBanner, downgradeNotice } from "@/components/ProviderDowngradeBanner";
import {
  CONNECT_A_MODEL_ROW,
  NOT_CONNECTED_YET,
  OPEN_CONNECT_EVENT,
  PRESSABLE,
  openConnect,
  pressableCandidates,
  realModelRows,
} from "@/lib/demoExit";

const read = (rel: string) => readFileSync(join(process.cwd(), rel), "utf-8").replace(/\r\n/g, "\n");

/** /health on a fresh install, as the daemon really sends it (11 provider
 *  rows, the Browser vault's 5) — one web sign-in saved — plus a demo row. */
const FRESH = [
  ...["anthropic", "google", "openai", "openrouter", "xai"].map((p) => ({ provider: p, available: false, class: "api" })),
  ...["claude-cli", "codex-cli", "grok-cli"].map((p) => ({ provider: p, available: false, class: "cli" })),
  ...["custom", "ollama", "opencode-cli"].map((p) => ({ provider: p, available: false, class: "local" })),
  { provider: "claude", available: true, class: "browser" },
  { provider: "mock", available: true, class: "mock" },
];
const withRow = (row: Record<string, unknown>) =>
  FRESH.map((r) => (r.provider === row.provider ? { ...r, ...row } : r));
const demoHealth = (providers: unknown[]) => ({ status: "ok", default_provider: "mock", default_model: "", providers });

beforeEach(() => {
  H.health = null;
  H.refresh.mockClear();
  H.posts.length = 0;
  H.events = [];
  H.answer = { promoted: { provider: "anthropic", model: "claude-opus-4-8" }, reason: "" };
});
afterEach(() => cleanup());

/* ------------------------------------------------------------- 1 + 2 + 3 */

describe("the demo strip", () => {
  it("1. a fresh install is NOT 'a model is connected' — the demo row and a web sign-in never count", () => {
    H.health = demoHealth(FRESH);
    render(<SimulatedBanner />);
    const strip = screen.getByTestId("simulated-banner");
    expect(strip.textContent).toContain("no AI model is connected");
    expect(strip.textContent).not.toMatch(/connected but not chosen/);
    expect(within(strip).getByRole("link", { name: /Connect a model/ }).getAttribute("href")).toBe("/connections");
  });

  it("2. a signed-in Claude Code: the button IS the one press that sets the default everywhere", async () => {
    H.health = demoHealth(withRow({ provider: "claude-cli", available: true, signed_in: true }));
    const { rerender } = render(<SimulatedBanner />);
    const strip = screen.getByTestId("simulated-banner");
    expect(strip.textContent).toContain("Claude Code is ready to answer but not chosen yet");
    const press = within(strip).getByRole("button", { name: "Use Claude Code for answers" });
    // Not the old "Choose a model", which changed only the chat on screen.
    expect(within(strip).queryByRole("button", { name: /Choose a model/ })).toBeNull();
    await act(async () => {
      fireEvent.click(press);
    });
    expect(H.posts).toEqual([{ path: "/onboarding/use-model", body: { provider: "claude-cli" } }]);
    expect(H.refresh).toHaveBeenCalled();
    // The default changed: /health now says so, and the strip keeps the
    // press's own words for a moment instead of vanishing silently.
    H.health = { ...demoHealth(withRow({ provider: "claude-cli", available: true })), default_provider: "anthropic" };
    rerender(<SimulatedBanner />);
    const done = screen.getByTestId("simulated-banner");
    expect(done.getAttribute("data-state")).toBe("done");
    expect(done.textContent).toBe("Done — Claude Code answers you now.");
    expect(done.textContent).not.toMatch(/demo/i);
  });

  it("2b. the press's refusal is said in the strip, in the daemon's words", async () => {
    H.health = demoHealth(withRow({ provider: "claude-cli", available: true }));
    H.answer = { promoted: null, reason: "You already chose Anthropic for answers, so nothing changed." };
    render(<SimulatedBanner />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Use Claude Code for answers" }));
    });
    expect(screen.getByTestId("simulated-banner-note").textContent).toBe(
      "You already chose Anthropic for answers, so nothing changed.",
    );
  });

  it("2c. a model the one press cannot choose (Grok): the honest sentence and the model menu", () => {
    H.health = demoHealth(withRow({ provider: "grok-cli", available: true }));
    render(<SimulatedBanner />);
    const strip = screen.getByTestId("simulated-banner");
    expect(strip.textContent).toContain("a model is connected but not chosen yet");
    expect(within(strip).getByRole("button", { name: /Choose a model/ })).toBeTruthy();
  });

  it("3. 'Connect a model' opens the connect choices where the user is, and only leaves the page when none take it", () => {
    H.health = demoHealth(FRESH);
    render(<SimulatedBanner />);
    const link = screen.getByRole("link", { name: /Connect a model/ });
    // No screen shows the doors: the link goes to Connections.
    const leave = new MouseEvent("click", { bubbles: true, cancelable: true });
    link.dispatchEvent(leave);
    expect(leave.defaultPrevented).toBe(false);
    // The chat's doors are on screen: they take it and the page stays.
    const took = vi.fn((e: Event) => {
      (e as CustomEvent<{ handled: boolean }>).detail.handled = true;
    });
    window.addEventListener(OPEN_CONNECT_EVENT, took);
    try {
      const stay = new MouseEvent("click", { bubbles: true, cancelable: true });
      link.dispatchEvent(stay);
      expect(took).toHaveBeenCalledTimes(1);
      expect(stay.defaultPrevented).toBe(true);
    } finally {
      window.removeEventListener(OPEN_CONNECT_EVENT, took);
    }
  });

  it("CONTROL: a chosen default shows no strip", () => {
    H.health = { ...demoHealth(withRow({ provider: "claude-cli", available: true })), default_provider: "anthropic" };
    render(<SimulatedBanner />);
    expect(screen.queryByTestId("simulated-banner")).toBeNull();
  });
});

describe("lib/demoExit", () => {
  it("counts only a real model, and offers only what the one press can choose", () => {
    expect(realModelRows(demoHealth(FRESH) as never)).toEqual([]);
    const h = demoHealth([...FRESH, { provider: "grok-cli", available: true, class: "cli" }]);
    expect(realModelRows(h as never).map((r) => r.provider)).toEqual(["grok-cli"]);
    expect(pressableCandidates(h as never)).toEqual([]);
    const c = demoHealth(withRow({ provider: "claude-cli", available: true }));
    expect(pressableCandidates(c as never).map((x) => x.provider)).toEqual(["claude-cli"]);
  });

  it("PRESSABLE is the connect doors' one-press set (kept in step)", () => {
    const src = read("components/onboarding/ConnectDoors.tsx");
    const block = /const ONE_PRESS = new Set\(\[([\s\S]*?)\]\);/.exec(src)![1];
    const names = [...block.matchAll(/"([a-z-]+)"/g)].map((m) => m[1]).sort();
    const local = read("lib/onboarding.ts");
    const servers = /LOCAL_SERVER_PRESS_LABELS[^{]*\{([\s\S]*?)\};/.exec(local)![1];
    const serverNames = [...servers.matchAll(/^\s*([a-z]+):/gm)].map((m) => m[1]);
    expect([...PRESSABLE].sort()).toEqual([...names, ...serverNames].sort());
  });

  it("openConnect answers false with nobody listening", () => {
    expect(openConnect()).toBe(false);
  });
});

/* ------------------------------------------------------------------- 4 */

const CATALOG = [
  { provider: "claude-cli", model: "claude-opus-4-8", label: "Opus 4.8", available: true, kind: "cli" },
  { provider: "xai", model: "grok-4", available: false, kind: "api" },
] as unknown as ModelOption[];

function renderMenu(onConnect?: () => boolean) {
  const onOpenChange = vi.fn();
  render(
    <ModelMenu
      open
      onOpenChange={onOpenChange}
      choice=""
      models={CATALOG}
      trigger={{ text: "default model", raw: "", isDefault: true } as never}
      onPick={() => {}}
      onConnect={onConnect}
    />,
  );
  return { menu: screen.getByTestId("model-menu"), onOpenChange };
}

describe("the model menu", () => {
  it("4a. a greyed row says why on the screen, not only in a hover tooltip", () => {
    const { menu } = renderMenu(() => false);
    const grey = within(menu)
      .getAllByRole("button")
      .find((b) => (b as HTMLButtonElement).disabled && /xAI|xai/i.test(b.textContent ?? ""))!;
    expect(grey).toBeTruthy();
    expect(within(grey).getByTestId("model-row-reason").textContent).toBe(NOT_CONNECTED_YET);
    // The connected row carries no such line.
    const up = within(menu)
      .getAllByRole("button")
      .find((b) => !(b as HTMLButtonElement).disabled && /Claude Code/.test(b.textContent ?? ""))!;
    expect(within(up).queryByTestId("model-row-reason")).toBeNull();
  });

  it("4b. it ends with a 'Connect a model…' row a keyboard reaches, which closes the menu and opens the doors", () => {
    const onConnect = vi.fn(() => true);
    const { menu, onOpenChange } = renderMenu(onConnect);
    const row = within(menu).getByTestId("model-menu-connect");
    expect(row.textContent).toBe(CONNECT_A_MODEL_ROW);
    expect(row.tagName).toBe("A");
    expect(row.getAttribute("href")).toBe("/connections");
    // It is the menu's last control.
    const controls = menu.querySelectorAll("a[href], button, input");
    expect(controls[controls.length - 1]).toBe(row);
    row.focus();
    expect(document.activeElement).toBe(row);
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    row.dispatchEvent(click);
    expect(onConnect).toHaveBeenCalledTimes(1);
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(click.defaultPrevented).toBe(true);
  });

  it("CONTROL: without onConnect (the IJ harness's menu) there is no such row", () => {
    const { menu } = renderMenu();
    expect(within(menu).queryByTestId("model-menu-connect")).toBeNull();
  });
});

/* ------------------------------------------------------------------- 5 */

describe("after the first message: the connect choice under the box", () => {
  it("5a. nothing pressable: 'Connect a model' opens the doors right here", () => {
    H.health = demoHealth(FRESH);
    render(<DemoConnectChoice />);
    const open = screen.getByTestId("demo-connect-open");
    expect(open.textContent).toContain("Connect a model");
    expect(screen.queryByTestId("demo-connect-panel")).toBeNull();
    fireEvent.click(open);
    expect(within(screen.getByTestId("demo-connect-panel")).getByTestId("connect-doors-stub")).toBeTruthy();
    expect(open.getAttribute("aria-expanded")).toBe("true");
  });

  it("5b. a pressable model: the one press, and 'Other ways to connect' beside it", () => {
    H.health = demoHealth(withRow({ provider: "claude-cli", available: true }));
    render(<DemoConnectChoice />);
    expect(screen.getByRole("button", { name: /Use Claude Code for answers/ })).toBeTruthy();
    expect(screen.getByTestId("demo-connect-open").textContent).toContain(OTHER_WAYS_TO_CONNECT);
  });

  it("5c. the strip's and the menu's 'Connect a model' open it (openConnect takes it)", () => {
    H.health = demoHealth(FRESH);
    render(<DemoConnectChoice />);
    let took = false;
    act(() => {
      took = openConnect();
    });
    expect(took).toBe(true);
    expect(screen.getByTestId("demo-connect-panel")).toBeTruthy();
  });

  it("5d. a chat kept behind Chat › Code (under inert) never takes it, so the link leaves for Connections", () => {
    H.health = demoHealth(FRESH);
    render(
      <div inert={true as never}>
        <DemoConnectChoice />
      </div>,
    );
    let took = true;
    act(() => {
      took = openConnect();
    });
    expect(took).toBe(false);
    expect(screen.queryByTestId("demo-connect-panel")).toBeNull();
  });

  it("CONTROL: with a real default nothing is offered", () => {
    H.health = { ...demoHealth(withRow({ provider: "claude-cli", available: true })), default_provider: "anthropic" };
    render(<DemoConnectChoice />);
    expect(screen.queryByTestId("demo-connect")).toBeNull();
    expect(openConnect()).toBe(false);
  });

  it("the chat page draws it under the box after the first message, and wires the menu and the new chat's doors", () => {
    const src = read("app/chat/page.tsx");
    const meta = src.slice(src.indexOf('data-testid="composer-meta"'), src.indexOf('data-testid="chat-hero-foot"'));
    expect(meta).toMatch(/messages\.length > 0 && \(\s*<DemoConnectChoice/);
    expect(src).toMatch(/<ModelMenu[\s\S]{0,900}?onConnect=\{openConnect\}/);
    expect(src).toMatch(/<HeroConnectAnchor>[\s\S]{0,400}?<ConnectDoors layout="row"/);
  });

  it("it never subscribes to the composer (the page must not re-render per keystroke)", () => {
    const src = read("components/chat/DemoConnectChoice.tsx");
    expect(src).not.toMatch(/composerStore|useComposer/);
  });
});

/* ------------------------------------------------------------------- 6 */

describe("the downgrade banner", () => {
  const ev = (id: string, session_id: string | null): IJEvent => ({
    id,
    type: "provider.downgraded",
    session_id,
    ts: `2026-11-07T00:00:0${id}Z`,
    payload: { requested: "mock (default)", used: "mock", reason: "demo" },
  });

  it("6. a chat turn the demo answered: nothing (not a downgrade, already said in the chat)", () => {
    expect(downgradeNotice(ev("1", null))).toBeNull();
    expect(downgradeNotice(ev("2", "chat"))).toBeNull();
    H.events = [ev("1", null)];
    render(<ProviderDowngradeBanner />);
    expect(screen.queryByTestId("provider-downgrade")).toBeNull();
  });

  it("CONTROL: a RUN the demo answered still says so", () => {
    H.events = [ev("3", "sess_9")];
    render(<ProviderDowngradeBanner />);
    expect(screen.getByTestId("provider-downgrade").getAttribute("data-kind")).toBe("mock");
  });
});
