/**
 * Calm UI redesign S6 — the surface manifest, the Everything page and the
 * palette's coverage (T2, the unit half; the browser half is e2e/t2).
 *
 * T2: every surface is reachable within two clicks from home, and from the
 * palette. Two clicks = the sidebar's Everything item, then the entry; the
 * sidebar half is pinned by the sidebar tests (S7). Here: every route the
 * dashboard serves is in the manifest, every surface outside the sidebar is
 * an entry on Everything, every surface and every setting is a palette row.
 */

import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/everything",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api", () => ({
  get: async () => ({}),
  post: async () => ({}),
}));

import EverythingPage from "@/app/everything/page";
import {
  MAX_PINS,
  PIN_KEY,
  SIDEBAR_HREFS,
  SURFACES,
  readPins,
  surfaceFor,
  togglePin,
  ungrouped,
} from "@/lib/surfaces";
import { settingRows } from "@/components/CommandPalette";

/** Routes the app serves: every app/<dir>/page.tsx (static segments only). */
function routes(): string[] {
  const root = join(__dirname, "..", "app");
  const out: string[] = [];
  const walk = (dir: string, prefix: string) => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      if (!statSync(p).isDirectory()) continue;
      if (name.startsWith("[") || name.startsWith("_") || name.startsWith("(")) continue;
      const route = `${prefix}/${name}`;
      try {
        if (statSync(join(p, "page.tsx")).isFile()) out.push(route);
      } catch {
        /* no page here */
      }
      walk(p, route);
    }
  };
  walk(root, "");
  return out;
}

/** Old addresses that are only doors into a manifest surface. */
const REDIRECT_ONLY = new Set(["/integrations", "/ltm", "/lessons"]);

beforeEach(() => {
  window.localStorage.clear();
});
afterEach(() => cleanup());

describe("the surface manifest", () => {
  it("knows every route the dashboard serves (an unlisted page is unfindable)", () => {
    const missing = routes().filter((r) => !REDIRECT_ONLY.has(r) && !surfaceFor(r));
    expect(missing).toEqual([]);
    expect(ungrouped()).toEqual([]);
  });

  it("the sidebar has at most four items, and Build is one of them", () => {
    expect(SIDEBAR_HREFS.length).toBeLessThanOrEqual(4);
    expect(SIDEBAR_HREFS).toContain("/terminals");
    for (const h of SIDEBAR_HREFS) expect(surfaceFor(h)).toBeTruthy();
  });
});

describe("Everything", () => {
  it("lists every surface that is not in the sidebar or home — each one click from this page", () => {
    render(<EverythingPage />);
    const grid = screen.getByTestId("everything-grid");
    const hrefs = within(grid)
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"));
    const expected = SURFACES.filter((s) => s.group !== "primary").map((s) => s.href);
    expect(new Set(hrefs)).toEqual(new Set(expected));
    expect(expected).toContain("/marketplace");
    expect(expected.length).toBeGreaterThan(25);
  });

  it("the filter narrows by name, alias or line", () => {
    render(<EverythingPage />);
    fireEvent.change(screen.getByTestId("everything-filter"), { target: { value: "cron" } });
    const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).toContain("/schedules");
    expect(hrefs).not.toContain("/usage");
  });

  it("pins up to three entries, refuses a fourth and says so", () => {
    render(<EverythingPage />);
    for (const label of ["Usage", "Local fleet", "Connections"]) {
      fireEvent.click(screen.getByLabelText(`Pin ${label} to the sidebar`));
    }
    expect(readPins()).toEqual(["/usage", "/fleet", "/connections"]);
    fireEvent.click(screen.getByLabelText("Pin Skills to the sidebar"));
    expect(readPins()).toHaveLength(MAX_PINS);
    expect(screen.getByRole("status").textContent).toContain("You can pin 3");
    fireEvent.click(screen.getByLabelText("Unpin Usage from the sidebar"));
    expect(readPins()).toEqual(["/fleet", "/connections"]);
  });

  it("a stored pin that is not a surface is ignored", () => {
    window.localStorage.setItem(PIN_KEY, JSON.stringify(["/nope", "/usage", 7]));
    expect(readPins()).toEqual(["/usage"]);
    expect(togglePin("/usage")).toBe(true);
    expect(readPins()).toEqual([]);
  });
});

describe("the palette covers every surface and every setting", () => {
  it("every surface is a palette row (rendered and searched by its name)", async () => {
    const { CommandPalette } = await import("@/components/CommandPalette");
    render(<CommandPalette />);
    await act(async () => {
      window.dispatchEvent(new Event("ij:open-palette"));
    });
    const box = screen.getByRole("combobox");
    for (const s of SURFACES) {
      await act(async () => {
        fireEvent.change(box, { target: { value: s.label } });
      });
      const rows = screen.queryAllByRole("option");
      const found = rows.some((r) => r.getAttribute("data-href") === s.href);
      expect(found, `palette has no row for ${s.label} (${s.href})`).toBe(true);
    }
  });

  it("each setting is a row that opens it, plus a do-it-in-chat row that only types", () => {
    const rows = settingRows([
      { key: "default_model", label: "Default model", help: "Which model answers", aliases: ["model"] },
      { key: "permissions.{tool}", label: "Tool permission", pattern: true },
    ]);
    expect(rows.map((r) => r.id)).toEqual(["setting:default_model", "setting-chat:default_model"]);
    expect(rows[0]).toMatchObject({ kind: "setting", href: "/settings?focus=default_model" });
    expect(rows[1].href).toBe(`/chat?ask=${encodeURIComponent("Change my default model setting to ")}`);
    expect(rows[0].aliases).toContain("default model");
  });
});
