/**
 * v1.516.0 (second review F13, decision E8): the Model servers page says a
 * failure in ONE plain sentence, and keeps what the program itself said
 * ("ConnectError: …") one press down, under Details. Before, the page printed
 * the daemon's `RuntimeError: …` / `ConnectError: …` as the message.
 *
 * Two places on the page: the note when the whole page could not be read
 * (`GET /fleet` -> `error` + `error_detail`) and the note under a model's
 * Check press (`POST /fleet/nodes/{id}/verify` -> the same two keys).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

/* ------------------------------------------------------------------ mocks */
const hooks = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return { FakeApiError, responses: {} as Record<string, unknown> };
});

vi.mock("@/lib/api", () => ({
  ApiError: hooks.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  sseUrl: (p: string) => p,
  wsUrl: (p: string) => p,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  get: vi.fn((p: string) => Promise.resolve(hooks.responses[p] ?? {})),
  post: vi.fn(() => Promise.resolve({ ok: true })),
  put: vi.fn(() => Promise.resolve({})),
  patch: vi.fn(() => Promise.resolve({})),
  del: vi.fn(() => Promise.resolve({})),
}));
vi.mock("@/lib/useApi", () => {
  const read = (p: string | null) => ({
    data: p ? (hooks.responses[p] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  });
  return { useApi: read, usePolledApi: read };
});
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    health: { default_provider: "mock", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {}, prefetch: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/connections/RestHookups", () => ({ RestHookups: () => null }));
vi.mock("@/components/connections/IronProxyCard", () => ({ IronProxyCard: () => null }));
vi.mock("@/components/connections/EnvelopeCard", () => ({
  EnvelopeRowControls: () => null,
  MeasuredEndpoints: () => null,
}));
vi.mock("@/components/BrandGlyph", () => ({ ProviderMark: () => null, BrandGlyph: () => null }));

import FleetPage from "@/app/fleet/page";
import { post } from "@/lib/api";
import { checkFailedWords } from "@/lib/fleet";
import { DETAILS_WORDS } from "@/lib/failureActions";

const PAGE_SENTENCE = "Your model servers could not be read just now. Press Refresh to try again.";
const RAW = "RuntimeError: sampler exploded";

const node = {
  id: "gw",
  label: "gw",
  base_url: "http://gw.test:8000",
  kind: "litellm",
  source: "user",
  enabled: true,
  routable: true,
  default_model: "glm",
  api_key_name: "",
  tool_use: true,
  vision: false,
  protocol: "openai",
};
const row = (extra: Record<string, unknown> = {}) => ({
  node,
  status: "online",
  evidence: "direct",
  latency_ms: 14,
  error: "",
  metrics_supported: true,
  metrics_reason: "",
  metrics: { requests_running: 0, requests_waiting: 0 },
  rates: null,
  models: [],
  children: [],
  sampled_at: 512.5,
  checked: true,
  ...extra,
});

function seed(fleet: Record<string, unknown>) {
  hooks.responses["/fleet"] = { nodes: [], sampling: { active: true, interval: 2 }, code_route: {}, ...fleet };
  hooks.responses["/fleet/usage"] = {};
}

beforeEach(() => {
  hooks.responses = {};
  vi.mocked(post).mockClear();
});
afterEach(() => cleanup());

describe("the page could not be read", () => {
  it("says one plain sentence, with the program's words folded under Details", () => {
    seed({ error: PAGE_SENTENCE, error_detail: RAW });
    render(<FleetPage />);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(PAGE_SENTENCE);
    expect(alert.textContent).not.toContain("RuntimeError");
    const details = screen.getByTestId("fleet-error-details");
    expect(details.tagName).toBe("DETAILS");
    expect((details as HTMLDetailsElement).open).toBe(false);
    expect(details.querySelector("summary")?.textContent).toBe(DETAILS_WORDS);
    expect(details.textContent).toContain(RAW);
  });

  it("no Details when the daemon sent none", () => {
    seed({ error: PAGE_SENTENCE, error_detail: "" });
    render(<FleetPage />);
    expect(screen.getByRole("alert").textContent).toContain(PAGE_SENTENCE);
    expect(screen.queryByTestId("fleet-error-details")).toBeNull();
  });

  it("nothing at all when nothing failed", () => {
    seed({ error: "", error_detail: "" });
    render(<FleetPage />);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByTestId("fleet-error-details")).toBeNull();
  });
});

describe("a model's Check that did not work", () => {
  const BORROWED = { tool_use: true, tool_use_from: "node", vision: false, vision_from: "node", checked_model: "glm" };

  it("says why in a sentence; the program's words wait under Details", async () => {
    seed({ nodes: [row({ models: [{ id: "qwen", ...BORROWED }] })] });
    vi.mocked(post).mockResolvedValueOnce({
      tool_use: null,
      error: "The model server could not be reached.",
      error_detail: "ConnectError: [Errno 111] Connection refused",
      answered: false,
    } as never);
    render(<FleetPage />);
    fireEvent.click(screen.getByTestId("fleet-model-check"));
    const note = await screen.findByTestId("fleet-model-check-note");
    expect(note.textContent).toBe("Couldn't check qwen. The model server could not be reached.");
    expect(note.textContent).not.toContain("ConnectError");
    const details = await screen.findByTestId("fleet-model-check-details");
    expect((details as HTMLDetailsElement).open).toBe(false);
    expect(details.textContent).toContain(DETAILS_WORDS);
    expect(details.textContent).toContain("ConnectError: [Errno 111] Connection refused");
  });

  it("a check that worked shows no Details", async () => {
    seed({ nodes: [row({ models: [{ id: "qwen", ...BORROWED }] })] });
    vi.mocked(post).mockResolvedValueOnce({ tool_use: true, error: "", error_detail: "" } as never);
    render(<FleetPage />);
    fireEvent.click(screen.getByTestId("fleet-model-check"));
    expect(await screen.findByTestId("fleet-model-check-note")).toHaveTextContent("Checked.");
    expect(screen.queryByTestId("fleet-model-check-details")).toBeNull();
  });

  it("the words follow a full stop, never a colon", () => {
    expect(checkFailedWords("qwen", "The model server did not answer in time.")).toBe(
      "Couldn't check qwen. The model server did not answer in time.",
    );
    expect(checkFailedWords("qwen", "")).toBe("Couldn't check qwen.");
    expect(checkFailedWords("qwen", null)).toBe("Couldn't check qwen.");
  });
});
