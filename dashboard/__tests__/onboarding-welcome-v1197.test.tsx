/**
 * v1.197.0 — the getting-started checklist points a brand-new user at the
 * places that actually complete each step.
 *
 * Two STEP_LINK entries were dead ends or off-thesis for a fresh install:
 *  - first_session sent users to /sessions ("New session"), but the product
 *    thesis is ONE chat surface that escalates itself — and the backend now
 *    counts a chat thread as the first task. The step must open Chat.
 *  - teach_style sent users to /memory?scope=lessons ("Review lessons"), a
 *    page that is EMPTY for a user with zero lessons. The signal is CREATED
 *    by asking Chat to remember a preference (the chat-armable
 *    remember_preference tool writes the LessonRecord), so the step points
 *    there. NOT "rate a reply": thumbs feedback exists only on session
 *    detail pages (SessionFeedback.tsx), so that CTA would name an
 *    affordance Chat does not have.
 *
 * Pinned with VALUES (href + CTA text), because a swapped or reverted entry
 * still renders a plausible-looking row:
 *  - first_session -> /chat "Open Chat";
 *  - teach_style   -> /chat "Teach it in Chat";
 *  - connect_ai stays /connections "Connect a model" (untouched by v1.197.0);
 *  - a DONE step still renders an "Open" link to its page — the regression
 *    guard for the existing "always clickable" behavior comment in the file
 *    (a done row once rendered NO control at all, which read as broken).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const hooks = vi.hoisted(() => ({
  responses: {} as Record<string, unknown>,
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({
      href,
      children,
      ...rest
    }: {
      href: string;
      children?: React.ReactNode;
    }) => createElement("a", { href, ...rest }, children),
  };
});

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial",
    "animate",
    "exit",
    "transition",
    "variants",
    "layout",
    "whileHover",
    "whileTap",
    "whileInView",
    "viewport",
  ]);
  const tagFor =
    (tag: string) => (props: Record<string, unknown>) => {
      const rest: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(props)) {
        if (!MOTION_ONLY.has(k)) rest[k] = v;
      }
      return createElement(tag, rest);
    };
  const cache = new Map<string, unknown>();
  return {
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

import { OnboardingWelcome } from "@/components/OnboardingWelcome";
import type { Onboarding, OnboardingStep } from "@/lib/types";

/* ------------------------------------------------------------------ fixtures */

function step(key: string, over: Partial<OnboardingStep> = {}): OnboardingStep {
  return {
    key,
    title: `Step ${key}`,
    detail: `Detail for ${key}`,
    done: false,
    action: "",
    ...over,
  };
}

/** All five steps not-done, next_step = first_session: the brand-new user. */
function freshInstall(over: Partial<Onboarding> = {}): Onboarding {
  const checklist = [
    step("connect_ai"),
    step("first_session"),
    step("work_with_document"),
    step("teach_style"),
    step("set_up_voice", { optional: true }),
  ];
  return {
    version: "1.197.0",
    first_run: true,
    doctor: { ok: true, checks: [] },
    checklist,
    next_step: checklist[1], // first_session
    ...over,
  };
}

/** The <a> a checklist row renders for the step titled `title`. */
function rowLink(title: string): HTMLAnchorElement {
  const row = screen.getByText(title).closest("li");
  expect(row).not.toBeNull();
  const link = row!.querySelector("a");
  expect(link).not.toBeNull();
  return link as HTMLAnchorElement;
}

beforeEach(() => {
  // Dismissed key unset: the full checklist panel must render, not the
  // collapsed "Finish setup" affordance.
  localStorage.removeItem("ij_onboarding_dismissed");
});

afterEach(() => {
  cleanup();
  hooks.responses = {};
});

/* --------------------------------------------------------------------- tests */

describe("OnboardingWelcome step links (v1.197.0)", () => {
  it("first_session points at Chat ('Open Chat'), not the Sessions list", async () => {
    hooks.responses["/onboarding"] = freshInstall();
    render(<OnboardingWelcome />);
    await screen.findByText("Welcome to Iron Jarvis");

    const link = rowLink("Step first_session");
    expect(link).toHaveAttribute("href", "/chat");
    expect(link.textContent).toContain("Open Chat");
    expect(link.textContent).not.toContain("New session");
  });

  it("teach_style points at Chat ('Teach it in Chat'), not the empty lessons page", async () => {
    hooks.responses["/onboarding"] = freshInstall();
    render(<OnboardingWelcome />);
    await screen.findByText("Welcome to Iron Jarvis");

    const link = rowLink("Step teach_style");
    expect(link).toHaveAttribute("href", "/chat");
    expect(link.textContent).toContain("Teach it in Chat");
    // "Rate a reply" named an affordance Chat does not have (thumbs live on
    // session detail only) — the CTA must not regress to it.
    expect(link.textContent).not.toContain("Rate a reply");
    // The dead end for a zero-lesson user must be gone entirely.
    expect(link.getAttribute("href")).not.toContain("/memory");
  });

  it("connect_ai still points at /connections ('Connect a model')", async () => {
    hooks.responses["/onboarding"] = freshInstall();
    render(<OnboardingWelcome />);
    await screen.findByText("Welcome to Iron Jarvis");

    const link = rowLink("Step connect_ai");
    expect(link).toHaveAttribute("href", "/connections");
    expect(link.textContent).toContain("Connect a model");
  });

  it("a DONE step still renders an 'Open' link to its page (always-clickable guard)", async () => {
    const data = freshInstall();
    data.checklist[0] = step("connect_ai", { done: true });
    hooks.responses["/onboarding"] = data;
    render(<OnboardingWelcome />);
    await screen.findByText("Welcome to Iron Jarvis");

    const link = rowLink("Step connect_ai");
    expect(link).toHaveAttribute("href", "/connections");
    // Done rows swap the CTA for "Open" but never lose the control.
    expect(link.textContent).toContain("Open");
    expect(link.textContent).not.toContain("Connect a model");
  });
});
