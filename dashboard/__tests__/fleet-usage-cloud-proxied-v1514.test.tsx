/**
 * v1.514.0 (second review F9): tokens one of the user's own model servers
 * passed on to a cloud service are counted as CLOUD by the daemon, and their
 * cost is "not known" (no price), never "$0" and never avoided spend. The
 * Model servers page says so beside the cloud figure; with none, the line is
 * unchanged.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const S = vi.hoisted(() => ({ responses: {} as Record<string, unknown> }));

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {
    status = 0;
  },
  API_BASE: "http://api.test",
  ijToken: () => null,
  get: (path: string) => Promise.resolve(S.responses[path] ?? {}),
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

function apiState(path: string | null) {
  return { data: path ? (S.responses[path] ?? null) : null, error: null, loading: false, reload: () => {} };
}
vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => apiState(path),
  usePolledApi: (path: string | null) => apiState(path),
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, unauthorized: false, requestError: false, checking: false, health: null, refresh: () => {} }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, prefetch: () => {}, back: () => {} }),
  usePathname: () => "/fleet",
  useSearchParams: () => new URLSearchParams(""),
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("@/components/BrandGlyph", async (orig) => ({
  ...((await orig()) as object),
  ProviderMark: () => null,
}));

import FleetPage from "@/app/fleet/page";

afterEach(() => {
  cleanup();
  S.responses = {};
});

const BASE = {
  days: 30,
  local_tokens: 1_000_000,
  cloud_tokens: 2_000_000,
  cloud_cost_usd: 0,
  est_avoided_usd: 5,
  comparison_provider: "anthropic",
  comparison_model: "claude-opus-4-8",
  basis: "estimate: what the local tokens would have cost on anthropic:claude-opus-4-8 at list price",
  by_node: [],
};

describe("the Model servers page's cloud line (F9)", () => {
  it("says the cost is not known for tokens a model server sent on to a cloud service", () => {
    S.responses["/fleet"] = { nodes: [] };
    S.responses["/fleet/usage"] = { ...BASE, cloud_cost_unknown_tokens: 2_000_000 };
    render(<FleetPage />);
    const line = screen.getByTestId("fleet-cloud-cost-unknown");
    expect(line.textContent).toMatch(/cost not known for 2,000,000 tok/);
    expect(line.getAttribute("title")).toMatch(/passed these on to a cloud service/);
  });

  it("is unchanged when no tokens went that way", () => {
    S.responses["/fleet"] = { nodes: [] };
    S.responses["/fleet/usage"] = { ...BASE, cloud_cost_unknown_tokens: 0 };
    render(<FleetPage />);
    expect(screen.queryByTestId("fleet-cloud-cost-unknown")).toBeNull();
  });
});
