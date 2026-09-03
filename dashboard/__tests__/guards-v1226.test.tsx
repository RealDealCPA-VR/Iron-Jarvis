/**
 * v1.226.0 (F-F-6) — one unguarded field in one card must not take down the
 * whole Overview. `/metrics` -> `{}` crashed HealthCard on
 * `m.sessions_evaluated.toLocaleString()`; `/onboarding` -> `{}` crashed
 * OnboardingWelcome on `data.checklist.map`. Both now render with a
 * partial/empty payload.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";

const hooks = vi.hoisted(() => ({ onboarding: {} as unknown }));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path === "/onboarding" ? hooks.onboarding : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const strip = (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) {
      if (!["initial", "animate", "exit", "transition", "variants", "layout"].includes(k)) rest[k] = v;
    }
    return rest;
  };
  return {
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => (props: Record<string, unknown>) => createElement(String(tag), strip(props)),
    }),
  };
});

import { HealthCard, type OverviewMetrics } from "@/components/overview/HealthCard";
import { OnboardingWelcome } from "@/components/OnboardingWelcome";

afterEach(() => cleanup());

describe("HealthCard with a partial /metrics payload (v1.226.0)", () => {
  it("renders {} without throwing and shows zero counters", () => {
    const { container } = render(
      <HealthCard metrics={{} as unknown as OverviewMetrics} loading={false} />,
    );
    const txt = container.textContent || "";
    expect(txt).toContain("Sessions evaluated");
    expect(txt).toContain("0 events");
    expect(txt).toContain("0 tool calls");
  });

  it("still formats a full payload", () => {
    const { container } = render(
      <HealthCard
        metrics={{
          sessions_evaluated: 1234,
          avg_completion: 0.5,
          avg_tool_success_rate: 0.75,
          avg_latency_s: 2,
          total_tool_invocations: 1,
          event_count: 5,
        } as unknown as OverviewMetrics}
        loading={false}
      />,
    );
    const txt = container.textContent || "";
    expect(txt).toContain("1,234");
    expect(txt).toContain("1 tool call");
  });
});

describe("OnboardingWelcome with a partial /onboarding payload (v1.226.0)", () => {
  it("renders {} (no checklist field) without throwing", () => {
    hooks.onboarding = {};
    localStorage.removeItem("ij_onboarding_dismissed");
    expect(() => render(<OnboardingWelcome />)).not.toThrow();
  });
});
