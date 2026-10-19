/**
 * v1.358.0 lane 8D (acceptance 6, the doors half) — LM Studio and llama.cpp
 * running on this PC are offered in ONE press where a detected Ollama is:
 * the chat's empty state and the first-run wizard (both render the REAL
 * ConnectDoors) and the composer's suggestion chip (the REAL
 * ModelSuggestChip). All three read /health; the `custom` row carries
 * `detected_servers` (daemon/app.py _visible_providers ←
 * providers/local_servers_seen.health_field — pinned from the Python side in
 * tests/test_local_servers_doors_v13580.py). The press posts the server's
 * KEY to POST /onboarding/use-model, which saves the model-server slot and
 * chooses it (pinned in tests/test_connections_honest_v13580.py).
 *
 * Only the transport seams are mocked (lib/api, lib/daemon). Every waitFor
 * waits for the thing it asserts.
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
    health: null as Record<string, unknown> | null,
    refresh: vi.fn(),
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async () => ({}),
  post: async (path: string, body?: unknown) => {
    H.posts.push({ path, body });
    if (path === "/onboarding/use-model") {
      return { promoted: { provider: "custom", model: "qwen2.5-7b-instruct" }, reason: "" };
    }
    return {};
  },
  put: async () => ({}),
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

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import { ConnectDoors } from "@/components/onboarding/ConnectDoors";
import { ModelSuggestChip } from "@/components/chat/ModelSuggestChip";

type Row = Record<string, unknown>;

/** /health as a fresh install with LM Studio running sends it: nothing
 *  connected, the demo answering, the `custom` row naming what one press
 *  connects (local_servers_seen.health_field's `{key, label, base_url}`). */
const LMSTUDIO = { key: "lmstudio", label: "LM Studio (on this PC)", base_url: "http://localhost:1234/v1" };
const LLAMACPP = { key: "llamacpp", label: "llama.cpp (on this PC)", base_url: "http://localhost:8080/v1" };

function setHealth(detected: unknown[] | undefined) {
  const custom: Row = { provider: "custom", available: false, class: "local", inherited_from: null };
  if (detected !== undefined) custom.detected_servers = detected;
  H.health = {
    status: "ok",
    version: "1.358.0",
    default_provider: "mock",
    default_model: "",
    providers: [
      { provider: "anthropic", available: false, class: "api", inherited_from: null },
      { provider: "claude-cli", available: false, class: "cli", installed: false, signed_in: null, sign_in_fix: "" },
      { provider: "codex-cli", available: false, class: "cli", installed: false, signed_in: null, sign_in_fix: "" },
      { provider: "ollama", available: false, class: "local", inherited_from: null, detected: false },
      custom,
    ],
  };
}

beforeEach(() => {
  H.posts = [];
  H.refresh.mockReset();
  setHealth([LMSTUDIO]);
});
afterEach(cleanup);

describe("(6) the connect doors offer a model server found on this PC", () => {
  it("a running LM Studio is ONE press: 'Use LM Studio (on this PC) for answers' posts its key", async () => {
    render(<ConnectDoors />);
    expect(screen.getByText("Ready — choose what answers you")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Use LM Studio (on this PC) for answers" }));
    await waitFor(() =>
      expect(H.posts.find((p) => p.path === "/onboarding/use-model")?.body).toEqual({ provider: "lmstudio" }),
    );
    // Where the words go is said before the press, in the Ollama door's words.
    expect(document.body.textContent).toMatch(
      /With LM Studio \(on this PC\), what you type stays on this PC\./,
    );
    // Never the "connected elsewhere — choose it on the Connections page" line:
    // it is NOT connected yet, the press connects it.
    expect(document.body.textContent).not.toMatch(/connected too|is connected/);
  });

  it("llama.cpp gets its own press in its own words, beside LM Studio", () => {
    setHealth([LMSTUDIO, LLAMACPP]);
    render(<ConnectDoors />);
    expect(screen.getByRole("button", { name: "Use LM Studio (on this PC) for answers" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Use llama.cpp (on this PC) for answers" })).toBeTruthy();
  });

  it("CONTROL: nothing found (or an older daemon with no field) — no press for either", () => {
    setHealth([]);
    const { unmount } = render(<ConnectDoors />);
    expect(screen.queryByRole("button", { name: /Use LM Studio|Use llama\.cpp/ })).toBeNull();
    unmount();
    setHealth(undefined);
    render(<ConnectDoors />);
    expect(screen.queryByRole("button", { name: /Use LM Studio|Use llama\.cpp/ })).toBeNull();
  });

  it("the suggestion chip offers it in one tap and posts the same press", async () => {
    render(<ModelSuggestChip />);
    fireEvent.click(screen.getByRole("button", { name: /Use LM Studio \(on this PC\) for answers/ }));
    await waitFor(() =>
      expect(H.posts.find((p) => p.path === "/onboarding/use-model")?.body).toEqual({ provider: "lmstudio" }),
    );
    await screen.findByText("Done — LM Studio (on this PC) now answers.");
  });
});
