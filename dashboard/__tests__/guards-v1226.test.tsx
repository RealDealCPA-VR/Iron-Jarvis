/**
 * v1.226.0 (F-F-6) — one unguarded field in one card must not take down the
 * whole Overview. `/onboarding` -> `{}` crashed OnboardingWelcome on
 * `data.checklist.map`; it now renders with a partial/empty payload.
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
    // v1.250.0 (S-08): components animate with framer's slim `m.*` under the
    // layout's LazyMotion, so this mock must export `m` too — otherwise every
    // mocked surface throws "No \"m\" export is defined on the mock".
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => (props: Record<string, unknown>) => createElement(String(tag), strip(props)),
    }),
  };
});

import { OnboardingWelcome } from "@/components/OnboardingWelcome";

afterEach(() => cleanup());

// The HealthCard half of this file left with HealthCard itself (v1.439.0,
// Wave 6: the card had not been on any screen since v1.334.0).

describe("OnboardingWelcome with a partial /onboarding payload (v1.226.0)", () => {
  it("renders {} (no checklist field) without throwing", () => {
    hooks.onboarding = {};
    localStorage.removeItem("ij_onboarding_dismissed");
    expect(() => render(<OnboardingWelcome />)).not.toThrow();
  });
});
