/**
 * v1.353.0 integrate — Wave 3's lanes joined: ONE place for each kind of
 * thing, in the same words, and the chat's fix doors are the doors the
 * other lanes built.
 *
 *  (a) APPS: the empty chat's apps line (3A, HomeReadiness), the Overview
 *      checklist's optional "Connect the apps you use" row (3A + integrate
 *      STEP_LINK) and the chat's "Connect Google Drive in Settings" fix door
 *      (3B, from the REAL daemon's doors — the contract fixture) all land on
 *      ONE section: Settings › Connections › Apps & tools, the Directory
 *      (3C). The relocated alias /marketplace and the door's own address name
 *      the same section.
 *  (b) BROWSER: the chat's "Pair your browser" door (3B) and the Directory's
 *      "Your browser" card (3C) open the same address, /computeruse, which
 *      lands on Settings › Connections › Browser.
 *  (c) WORDS: the bell's website-action row and the chat-ask row end the
 *      same way ("… waiting for your OK"); the Browser queue card answers
 *      with lib/approvalWords (pinned in browser-approval-copy-v13530).
 *
 * The real components (OnboardingWelcome, HomeReadiness.AppsLine) render;
 * only the data hooks are faked. The Python half (tests/
 * test_integrate_wave3_v13530.py) pins the daemon's side of each seam.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const hooks = vi.hoisted(() => ({ responses: {} as Record<string, unknown> }));

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
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "layout", "whileHover", "whileTap", "whileInView", "viewport"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  return {
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
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
import { AppsLine } from "@/components/chat/HomeReadiness";
import { RELOCATED } from "@/lib/relocated";
import { settingsRowLabel } from "@/lib/settingsRows";
import type { Onboarding, OnboardingStep } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (...p: string[]) => readFileSync(join(ROOT, ...p), "utf8").replace(/\r\n/g, "\n");
const FIX_DOORS = JSON.parse(read("__tests__", "fixtures", "fix-doors-v13530.json")) as Record<
  string,
  { href: string; label: string }[]
>;

/** The Settings section an address lands on: a relocated alias's own
 *  section, or the `section` of a /settings address. */
function sectionOf(href: string): string {
  const url = new URL(href, "http://ij.test");
  if (url.pathname === "/settings") return url.searchParams.get("section") ?? "";
  return RELOCATED[url.pathname]?.base === "/settings" ? (RELOCATED[url.pathname].extra.section ?? "") : "";
}

/** The SettingsHome entry for a section id: its label and the page it renders. */
function settingsEntry(section: string): { label: string; page: string } {
  const src = read("components", "settings", "SettingsHome.tsx");
  const at = src.indexOf(`id: "${section}"`);
  expect(at, `SettingsHome has no section ${section}`).toBeGreaterThan(-1);
  const entry = src.slice(at, src.indexOf("Comp:", at) + 120);
  return {
    label: /label: "([^"]+)"/.exec(entry)?.[1] ?? "",
    page: /pages\/(\w+)"/.exec(entry)?.[1] ?? "",
  };
}

function step(key: string, over: Partial<OnboardingStep> = {}): OnboardingStep {
  return { key, title: `Step ${key}`, detail: `Detail for ${key}`, done: false, action: "", ...over };
}

beforeEach(() => {
  localStorage.removeItem("ij_onboarding_dismissed");
});
afterEach(() => {
  cleanup();
  hooks.responses = {};
});

describe("(a) one place for the apps a new user connects — the Directory", () => {
  it("the Overview checklist's optional apps row opens the Directory, in the chat line's words", async () => {
    const apps = step("connect_apps", {
      title: "Connect the apps you use (optional)",
      optional: true,
      href: "/marketplace",
    });
    const checklist = [step("connect_ai"), step("first_session"), apps];
    hooks.responses["/onboarding"] = {
      version: "1.353.0",
      first_run: true,
      doctor: { ok: true, checks: [] },
      checklist,
      next_step: checklist[1],
    } satisfies Onboarding;
    render(<OnboardingWelcome />);
    await screen.findByText("Welcome to Iron Jarvis");
    const row = screen.getByText("Connect the apps you use (optional)").closest("li");
    const link = row!.querySelector("a")!;
    // Not the generic "/sessions · Get started" fallback any more.
    expect(link.getAttribute("href")).toBe("/marketplace");
    expect(link.textContent).toContain("Open the Directory");

    // The chat home's line: the same place, the same words.
    cleanup();
    render(<AppsLine names={["GitHub", "Notion"]} />);
    const line = screen.getByTestId("home-apps");
    const lineLink = line.querySelector("a")!;
    expect(lineLink.getAttribute("href")).toBe(link.getAttribute("href"));
    expect(lineLink.textContent).toBe("Connect the apps you use");
    expect(apps.title.startsWith(lineLink.textContent!)).toBe(true);
  });

  it("the chat's Drive fix door (the real daemon's) lands on that same section — the Directory page", () => {
    const [drive] = FIX_DOORS.google_drive;
    expect(drive.label).toBe("Connect Google Drive in Settings");
    expect(sectionOf(drive.href)).toBe("connections-apps");
    expect(sectionOf("/marketplace")).toBe("connections-apps");
    const entry = settingsEntry("connections-apps");
    expect(entry.page).toBe("DirectoryPage");
    // v1.354.0 (G67, updated on purpose): the row has no name of its own any
    // more; it is named like the page it opens — "Directory", the word
    // Everything, the checklist's press and the page's heading all use.
    expect(entry.label).toBe("");
    expect(settingsRowLabel("connections-apps")).toBe("Directory");
    // The page that section renders calls itself the Directory — the word
    // the checklist row's press uses.
    expect(read("components", "settings", "pages", "DirectoryPage.tsx")).toMatch(/title="Directory"/);
  });
});

describe("(b) one place for the browser", () => {
  it("the chat's Pair your browser door and the Directory's browser card open the same address", () => {
    const [pair] = FIX_DOORS.unpaired_browser;
    expect(pair).toEqual({ href: "/computeruse", label: "Pair your browser" });
    expect(sectionOf(pair.href)).toBe("connections-browser");
    expect(settingsEntry("connections-browser").page).toBe("BrowserPage");
    // The Directory's link card (connectors/catalog.py LINK_CARDS) — the
    // Python half pins the daemon value equal to this door's href.
    const catalog = read("..", "src", "iron_jarvis", "connectors", "catalog.py");
    const card = catalog.slice(catalog.indexOf('id="browser_addon"'));
    expect(/href="([^"]+)"/.exec(card)?.[1]).toBe(pair.href);
  });
});

describe("(c) the same words for 'it needs your OK'", () => {
  it("the bell's website-action row ends like the chat-ask row: 'waiting for your OK'", () => {
    const bell = read("components", "NotificationBell.tsx");
    expect(bell).toContain("A chat is waiting for your OK");
    expect(bell).toContain('"A website action is waiting for your OK"');
    // No user-facing string still says "computer-use approval" (comments may).
    expect(bell).not.toMatch(/["`][^"`\n]*computer-use approval/);
  });
});
