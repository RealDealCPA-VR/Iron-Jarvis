/**
 * v1.443.0 — the first ten minutes: the connect doors work the first time.
 *
 *  FIX 2 A pasted key is TESTED before it is stored (`?check=1`) and chosen
 *        for answers only after the test passed — a wrong key changes
 *        nothing and its reason stays on screen; a good one ends in "Done —
 *        <Provider> answers you now". The key's shape picks the right tab
 *        (an OpenAI key pasted into the Anthropic tab, the usual slip).
 *  FIX 3 The signed-out Claude Code / Codex row has "I've signed in — check
 *        now" (POST /providers/rescan re-probes the sign-in); the false
 *        "re-checks every few seconds" is gone; the door picked and the
 *        Sign in pane opened are kept for the visit, so coming back from the
 *        Build terminal never offers a second terminal.
 *
 * ConnectDoors is the real component; only the transport and the daemon
 * hook are mocked.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
    postResponses: {} as Record<string, unknown>,
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
    if (path in H.postResponses) return H.postResponses[path];
    if (path === "/onboarding/use-model") {
      const provider = (body as { provider?: string })?.provider ?? "";
      return { promoted: { provider, model: "m" }, reason: "" };
    }
    if (path === "/terminals/launch") return { id: "pane-7" };
    return {};
  }),
  put: vi.fn(async () => ({})),
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

import { ConnectDoors, CHECK_NOW_LABEL } from "@/components/onboarding/ConnectDoors";
import { keyShapeProvider } from "@/lib/onboarding";

function demoHealth(rows: Record<string, unknown>[] = []) {
  return {
    default_provider: "mock",
    default_model: "mock",
    providers: [{ provider: "mock", available: true }, ...rows],
  };
}

beforeEach(() => {
  H.posts.length = 0;
  H.postResponses = {};
  H.health = demoHealth();
  H.refresh.mockReset();
});
afterEach(cleanup);

/* ================================================================ FIX 2 === */

describe("the key's shape", () => {
  it.each([
    ["sk-ant-api03-abc", "anthropic"],
    ["sk-proj-abc", "openai"],
    ["sk-abc123", "openai"],
    ["sk-or-v1-abc", null],
    ["AIzaSyAbc", "google"],
    ["AQ.Ab8RN6", "google"],
    ["  sk-ant-x  ", "anthropic"],
    ["hello", null],
    ["", null],
  ])("%s reads as %s", (key, want) => {
    expect(keyShapeProvider(key)).toBe(want);
  });
});

function openKeyDoor() {
  fireEvent.click(screen.getByRole("button", { name: /I have an API key/ }));
}

describe("FIX 2: test the key first, choose it only after a pass", () => {
  it("a wrong key is not saved, nothing is chosen, and the reason stays on screen", async () => {
    H.postResponses["/connections/anthropic/key?check=1"] = {
      provider: "anthropic",
      status: "not_saved",
      saved: false,
      test: {
        ok: false,
        kind: "refused_key",
        detail: "anthropic rejected the credential (401)",
        title: "Anthropic refused the key",
        fix: "Check the key, then connect it again.",
      },
    };
    const chosen = vi.fn();
    render(<ConnectDoors onChosen={chosen} />);
    openKeyDoor();
    const box = screen.getByPlaceholderText("sk-ant-…");
    fireEvent.change(box, { target: { value: "sk-ant-bad" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    const line = await screen.findByTestId("key-door-result");
    expect(line.textContent).toContain("Anthropic refused the key");
    expect(line.textContent).toContain("Nothing was saved, and what answers you didn't change.");
    // MUTATION (choose without the test): /onboarding/use-model posted -> red.
    expect(H.posts.map((p) => p.path)).toEqual(["/connections/anthropic/key?check=1"]);
    expect(chosen).not.toHaveBeenCalled();
    // The key stays in the box to fix or move to another tab.
    expect((screen.getByPlaceholderText("sk-ant-…") as HTMLInputElement).value).toBe("sk-ant-bad");
  });

  it("a good key is chosen for answers with the one press, and says it worked", async () => {
    H.postResponses["/connections/anthropic/key?check=1"] = {
      provider: "anthropic",
      status: "connected",
      saved: true,
      test: { ok: true, kind: "ok", detail: "ok", title: "Anthropic accepted the key", fix: "" },
    };
    const chosen = vi.fn();
    render(<ConnectDoors onChosen={chosen} />);
    openKeyDoor();
    fireEvent.change(screen.getByPlaceholderText("sk-ant-…"), { target: { value: "sk-ant-good" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(chosen).toHaveBeenCalledWith("Anthropic"));
    expect(await screen.findByText("Done — Anthropic answers you now.")).toBeTruthy();
    // MUTATION (the old plain key route): no `?check=1` -> red.
    expect(H.posts.map((p) => p.path)).toEqual(["/connections/anthropic/key?check=1", "/onboarding/use-model"]);
    expect(H.posts[1].body).toEqual({ provider: "anthropic" });
    expect(screen.getByTestId("key-door-result").textContent).not.toContain("Choose it above");
  });

  it("a good key while a real model already answers is not chosen over it", async () => {
    H.health = { default_provider: "openai", default_model: "x", providers: [{ provider: "openai", available: true }] };
    H.postResponses["/connections/anthropic/key?check=1"] = {
      saved: true,
      test: { ok: true, kind: "ok", detail: "ok", title: "Anthropic accepted the key" },
    };
    render(<ConnectDoors />);
    openKeyDoor();
    fireEvent.change(screen.getByPlaceholderText("sk-ant-…"), { target: { value: "sk-ant-good" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByTestId("key-door-result")).toBeTruthy();
    expect(H.posts.map((p) => p.path)).not.toContain("/onboarding/use-model");
  });

  it("an OpenAI key pasted into the Anthropic tab switches to OpenAI and says so", async () => {
    render(<ConnectDoors />);
    openKeyDoor();
    fireEvent.change(screen.getByPlaceholderText("sk-ant-…"), { target: { value: "sk-proj-abc" } });
    // MUTATION (no shape switch): the Anthropic placeholder stays -> red.
    expect(screen.getByPlaceholderText("sk-…")).toBeTruthy();
    expect(screen.getByTestId("key-door-shape").textContent).toBe(
      "This looks like an OpenAI key, so the OpenAI tab is picked.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(H.posts[0]?.path).toBe("/connections/openai/key?check=1"));
  });

  it("a tab picked by hand stands, and the mismatch offers a one-press switch", () => {
    render(<ConnectDoors />);
    openKeyDoor();
    fireEvent.change(screen.getByPlaceholderText("sk-ant-…"), { target: { value: "sk-proj-abc" } });
    fireEvent.click(screen.getByRole("button", { name: "Anthropic" }));
    expect(screen.getByPlaceholderText("sk-ant-…")).toBeTruthy();
    expect(screen.getByTestId("key-door-shape").textContent).toContain(
      "This looks like an OpenAI key, not an Anthropic one.",
    );
    fireEvent.click(screen.getByTestId("key-door-shape-switch"));
    expect(screen.getByPlaceholderText("sk-…")).toBeTruthy();
  });

  it("an OpenRouter key in the OpenAI tab is left alone", () => {
    render(<ConnectDoors />);
    openKeyDoor();
    fireEvent.click(screen.getByRole("button", { name: "OpenAI" }));
    fireEvent.change(screen.getByPlaceholderText("sk-…"), { target: { value: "sk-or-v1-abc" } });
    expect(screen.queryByTestId("key-door-shape")).toBeNull();
  });
});

/* ================================================================ FIX 3 === */

const SIGNED_OUT = {
  provider: "claude-cli",
  available: false,
  installed: true,
  signed_in: false,
  sign_in_fix: "Claude Code isn't signed in.",
};

describe("FIX 3: a signed-out row can be checked again now", () => {
  it("the check-now press re-probes through the rescan and re-reads /health", async () => {
    H.health = demoHealth([SIGNED_OUT]);
    H.postResponses["/providers/rescan"] = {
      detected: [{ provider: "claude-cli", signed_in: true }],
    };
    render(<ConnectDoors />);
    fireEvent.click(screen.getByRole("button", { name: /I already pay/ }));
    fireEvent.click(screen.getByRole("button", { name: CHECK_NOW_LABEL }));
    await waitFor(() => expect(H.refresh).toHaveBeenCalled());
    expect(H.posts.map((p) => p.path)).toEqual(["/providers/rescan"]);
    expect(screen.queryByTestId("check-now-note-claude")).toBeNull();
  });

  it("still signed out after the look: it says so in words", async () => {
    H.health = demoHealth([SIGNED_OUT]);
    H.postResponses["/providers/rescan"] = { detected: [{ provider: "claude-cli", signed_in: false }] };
    render(<ConnectDoors />);
    fireEvent.click(screen.getByRole("button", { name: /I already pay/ }));
    fireEvent.click(screen.getByTestId("check-now-claude"));
    expect((await screen.findByTestId("check-now-note-claude")).textContent).toBe(
      `Claude Code still says it isn't signed in. Finish the sign-in in the Build terminal, then press “${CHECK_NOW_LABEL}” again.`,
    );
  });

  it("never claims a re-check every few seconds", () => {
    H.health = demoHealth([{ provider: "claude-cli", available: false, installed: true, signed_in: null }]);
    render(<ConnectDoors />);
    fireEvent.click(screen.getByRole("button", { name: /I already pay/ }));
    expect(document.body.textContent).not.toMatch(/every few seconds/);
    expect(screen.getByTestId("recheck-words").textContent).toContain("about every two minutes");
  });

  it("the door and the Sign in pane are kept for the visit — no second terminal", async () => {
    H.health = demoHealth([SIGNED_OUT]);
    const first = render(<ConnectDoors />);
    fireEvent.click(screen.getByRole("button", { name: /I already pay/ }));
    fireEvent.click(screen.getByRole("button", { name: "Sign in to Claude Code" }));
    expect(await screen.findByTestId("pane-hint-claude")).toBeTruthy();
    first.unmount(); // the user went to the Build terminal

    render(<ConnectDoors />);
    // MUTATION (local state only): the door is closed and Sign in is back -> red.
    expect(screen.getByTestId("pane-hint-claude").textContent).toContain("Open the Build terminal");
    expect(screen.queryByRole("button", { name: "Sign in to Claude Code" })).toBeNull();
    expect(screen.getByRole("link", { name: "Open the Build terminal" }).getAttribute("href")).toBe(
      "/terminals?focus=pane-7",
    );
    // A pane closed since can be opened again on purpose.
    fireEvent.click(screen.getByTestId("sign-in-again-claude"));
    await waitFor(() =>
      expect(H.posts.filter((p) => p.path === "/terminals/launch")).toHaveLength(2),
    );
  });
});
