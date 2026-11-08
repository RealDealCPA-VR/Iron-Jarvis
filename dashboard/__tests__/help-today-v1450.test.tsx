/**
 * v1.450.0 (readiness item 41 / ease item 73): Help and the Updates card
 * describe TODAY's app.
 *
 *  1. The Help page leads with the five places, in the sidebar's own order
 *     (lib/helpPlaces == lib/surfaces SIDEBAR_HREFS): a sixth sidebar place,
 *     or one taken away, turns this red until Help says so too.
 *  2. Its Maintenance link opens System › Maintenance, not the top of Settings.
 *  3. The Updates card shows "What changed in vX.Y.Z" from the daemon's
 *     GET /updates/changelog (the notes moved out of the Handbook), says when
 *     the version had no notes of its own, and draws nothing when there is
 *     nothing to show.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

const hooks = vi.hoisted(() => ({
  responses: {} as Record<string, unknown>,
  asked: [] as (string | null)[],
}));

vi.mock("@/lib/useApi", () => {
  const read = (path: string | null) => {
    hooks.asked.push(path);
    return { data: path ? (hooks.responses[path] ?? null) : null, error: null, loading: false, reload: () => {} };
  };
  return { useApi: read, usePolledApi: read };
});

vi.mock("@/lib/api", () => ({
  API_BASE: "http://test",
  ApiError: class extends Error {},
  ijToken: () => "",
  get: () => new Promise(() => {}),
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const strip = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) {
      if (!["initial", "animate", "exit", "transition", "variants", "layout", "whileHover", "whileTap", "whileInView", "viewport"].includes(k)) rest[k] = v;
    }
    return createElement(tag, rest);
  };
  const motion = new Proxy({} as Record<string, unknown>, { get: (_t, tag) => strip(String(tag)) });
  return {
    m: motion,
    motion,
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
  };
});

import HelpPage from "@/app/help/page";
import { WhatChanged, CHANGELOG_GUIDE_HREF } from "@/components/settings/pages/UpdatesPage";
import { HELP_PLACES } from "@/lib/helpPlaces";
import { SIDEBAR_HREFS } from "@/lib/surfaces";

afterEach(() => {
  cleanup();
  hooks.responses = {};
  hooks.asked = [];
});

describe("Help leads with the five places", () => {
  it("holds exactly the sidebar's places, in its order", () => {
    expect(HELP_PLACES.map((p) => p.href)).toEqual([...SIDEBAR_HREFS]);
    expect(HELP_PLACES.map((p) => p.title)).toEqual(["Chat", "Build", "Projects", "Everything", "Settings"]);
    expect(HELP_PLACES[0].desc).toMatch(/Chat \| Code/);
  });

  it("draws them as links on the Help page", () => {
    render(<HelpPage />);
    const list = screen.getByTestId("help-places");
    const hrefs = within(list).getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual([...SIDEBAR_HREFS]);
  });

  it("opens Settings on System › Maintenance from the troubleshooting entry", () => {
    render(<HelpPage />);
    const link = screen.getByRole("link", { name: "Settings › System › Maintenance" });
    expect(link.getAttribute("href")).toBe("/settings?section=system-maintenance");
  });
});

describe("the Updates card says what changed", () => {
  it("shows the installed version's notes", () => {
    hooks.responses["/updates/changelog?version=1.449.0"] = {
      version: "v1.449.0",
      found: true,
      latest: "v1.449.0",
      title: "What changed in v1.449.0",
      text: "**Did it work?** Every place gives the same verdict.",
    };
    render(<WhatChanged version="1.449.0" />);
    expect(screen.getByTestId("what-changed-title").textContent).toBe("What changed in v1.449.0");
    expect(screen.getByTestId("what-changed").textContent).toMatch(/Every place gives the same verdict/);
    expect(screen.queryByTestId("what-changed-nearest")).toBeNull();
    expect(screen.getByRole("link", { name: /Every version/ }).getAttribute("href")).toBe(CHANGELOG_GUIDE_HREF);
    expect(CHANGELOG_GUIDE_HREF).toBe("/help?guide=changelog");
  });

  it("says when the version brought no notes of its own", () => {
    hooks.responses["/updates/changelog?version=1.449.1"] = {
      version: "v1.449.1",
      found: false,
      latest: "v1.449.0",
      nearest: "v1.449.0",
      title: "What changed in v1.449.0",
      text: "The newest notes.",
    };
    render(<WhatChanged version="1.449.1" />);
    expect(screen.getByTestId("what-changed-title").textContent).toBe("What changed in v1.449.0");
    expect(screen.getByTestId("what-changed-nearest").textContent).toMatch(/v1\.449\.1 brought no notes of its own/);
  });

  it("draws nothing with no version, no answer, or nothing to show", () => {
    const { container, rerender } = render(<WhatChanged version={null} />);
    expect(container.textContent).toBe("");
    expect(hooks.asked).toContain(null); // no version: nothing asked
    rerender(<WhatChanged version="1.0.0" />);
    expect(container.textContent).toBe("");
    hooks.responses["/updates/changelog?version=1.0.0"] = { version: "v1.0.0", found: false, latest: "v1.449.0", title: "", text: "" };
    rerender(<WhatChanged version="1.0.0" />);
    expect(container.textContent).toBe("");
  });
});

/* ---------------------------------------------- ease item 73: the wordings */

const src = (rel: string) => readFileSync(join(process.cwd(), rel), "utf8").replace(/\r\n/g, "\n");

describe("ease item 73's one-line wordings", () => {
  // [file, the new words, the old words that must be gone]
  const ROWS: [string, string, string][] = [
    ["app/self-dev/page.tsx", '"/settings?focus=self_dev_enabled"', "Open Settings → Advanced"],
    ["app/autonomy/page.tsx", "Goals Jarvis works toward on its own, and the switch that stops it all.", "Motivation Layer —"],
    ["lib/nav.ts", "Watch a folder and get a suggestion when files change.", "Always-on watchers"],
    ["components/TitleBar.tsx", 'aria-label="Iron Jarvis: Home"', "Iron Jarvis — Overview"],
    ["app/documents/page.tsx", "Hide personal details (SSNs, phone numbers…)", '<Card title="Redact PII"'],
    ["lib/buildApp.ts", "These accounts are marked as at their limit.", "Iron-Proxy has these marked"],
    ["components/buildCode/CodePanes.tsx", "Its own tool servers", "Its own MCP servers"],
    ["components/terminal/TerminalPane.tsx", "Starts without Iron Jarvis's memory and browser", 'no Jarvis capabilities"'],
    ["components/terminal/TerminalPane.tsx", "where this PC can find it. Get it here.", "isn't on your PATH"],
    ["components/chat/HomeReadiness.tsx", "Type your question in the box.", "Ask anything below.\n"],
    ["app/chat/page.tsx", "A run card can't be kept in a messaging conversation.", "server-owned, so the run card"],
    ["app/chat/page.tsx", "Files are added to this chat for Jarvis to read", "uploaded and grounded into this chat"],
    ["lib/chatControlWords.ts", "You can turn on up to ${max} tools per chat. Turn one off first.", "tool slots are in use"],
  ];
  it.each(ROWS)("%s says it plainly", (file, now, gone) => {
    const text = src(file);
    expect(text).toContain(now);
    expect(text).not.toContain(gone);
  });

  it("the Sessions page keeps its clear-all press in a ⋯ menu, not as the page's button", () => {
    const text = src("app/sessions/page.tsx");
    const menu = text.indexOf('data-testid="sessions-more"');
    const press = text.indexOf('label="Clear finished, failed and stopped"');
    expect(menu).toBeGreaterThan(-1);
    expect(press).toBeGreaterThan(menu);
    expect(text.slice(menu, press)).toContain('aria-label="More actions"');
  });
});
