// @vitest-environment jsdom
/**
 * 17-a (build-out plan R1.8, "Every new notice: its cap and its off switch"):
 * the night review's bell switch is a Notifications row, and its ONE control
 * on the Settings page is the Notifications card's "In the bell" switch
 * (components/settings/BellNotices). Settings › Connections no longer shows a
 * second "Notifications" heading beside the card (wave-c-one-control pins
 * that on the real Settings page); chat and search still reach the schema
 * row.
 *
 *  - the switch shows the saved value (not set reads on, the default);
 *  - a press saves THROUGH THE WRITER (PUT /settings/values), so it has Undo;
 *  - a failed save says so and the switch stays where it was;
 *  - a value that cannot be read draws no switch (never the wrong side);
 *  - its words are the schema row's own, word for word.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DASHBOARD_ROOT, readSrc } from "./helpers/dashGuard";

const H = vi.hoisted(() => ({
  values: {} as Record<string, unknown>,
  puts: [] as { path: string; body: unknown }[],
  putError: null as Error | null,
  getError: null as Error | null,
}));

vi.mock("@/lib/api", () => ({
  get: vi.fn(async (p: string) => {
    if (p !== "/settings/values") throw new Error(`unmocked GET ${p}`);
    if (H.getError) throw H.getError;
    return { device_id: "", values: { ...H.values } };
  }),
  put: vi.fn(async (p: string, body: unknown) => {
    H.puts.push({ path: p, body });
    if (H.putError) throw H.putError;
    return { updated: ["night_review_notice"], action_id: "act_1" };
  }),
}));

import { get } from "@/lib/api";
import { BELL_CARD_TITLE, BELL_SAVE_FAILED, BELL_SWITCHES, BellNotices } from "@/components/settings/BellNotices";

const LABEL = "Tell me in the bell what the night review found";

beforeEach(() => {
  H.values = { night_review_notice: true };
  H.puts = [];
  H.putError = null;
  H.getError = null;
  vi.mocked(get).mockClear();
});
afterEach(() => cleanup());

describe("the night review's bell switch (17-a)", () => {
  it("shows the saved value, and a press saves it through the one writer", async () => {
    render(<BellNotices />);
    const sw = await screen.findByRole("switch", { name: LABEL });
    expect(screen.getByText(BELL_CARD_TITLE)).toBeTruthy();
    expect(sw.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(sw);
    await waitFor(() => expect(sw.getAttribute("aria-checked")).toBe("false"));
    expect(H.puts).toEqual([{ path: "/settings/values", body: { values: { night_review_notice: false } } }]);
    fireEvent.click(sw);
    await waitFor(() => expect(sw.getAttribute("aria-checked")).toBe("true"));
    expect(H.puts[1]).toEqual({ path: "/settings/values", body: { values: { night_review_notice: true } } });
  });

  it("a switch saved off reads off, and one never set reads on", async () => {
    H.values = { night_review_notice: false };
    render(<BellNotices />);
    expect((await screen.findByRole("switch", { name: LABEL })).getAttribute("aria-checked")).toBe("false");
    cleanup();
    H.values = { night_review_notice: null };
    render(<BellNotices />);
    expect((await screen.findByRole("switch", { name: LABEL })).getAttribute("aria-checked")).toBe("true");
  });

  it("a failed save says so, and the switch stays where it was", async () => {
    H.putError = new Error("boom");
    render(<BellNotices />);
    const sw = await screen.findByRole("switch", { name: LABEL });
    fireEvent.click(sw);
    expect(await screen.findByText(BELL_SAVE_FAILED)).toBeTruthy();
    expect(sw.getAttribute("aria-checked")).toBe("true");
  });

  it("a value that cannot be read draws no switch", async () => {
    H.getError = new Error("offline");
    render(<BellNotices />);
    await waitFor(() => expect(vi.mocked(get)).toHaveBeenCalledWith("/settings/values"));
    await act(async () => {
      await vi.mocked(get).mock.results[0].value.catch(() => undefined);
    });
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByText(BELL_CARD_TITLE)).toBeNull();
  });

  it("its words are the schema row's own", () => {
    const schema = readFileSync(path.join(DASHBOARD_ROOT, "..", "src", "iron_jarvis", "settings", "schema.py"), "utf8")
      .replace(/\r\n/g, "\n");
    for (const sw of BELL_SWITCHES) {
      const start = schema.indexOf(`S("${sw.key}"`);
      expect(start, sw.key).toBeGreaterThan(-1);
      const block = schema.slice(start, schema.indexOf("tier=", start));
      const literals = [...block.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1]);
      // S(key, label, group, type, help...): the help is the literals after the type.
      expect(literals[1]).toBe(sw.label);
      expect(literals[2]).toBe("connections");
      expect(literals.slice(4).join("")).toBe(sw.hint);
    }
  });

  it("is the ONE control: Settings sends the row to the Notifications card, and the card draws it", () => {
    const home = readSrc("components/settings/SettingsHome.tsx");
    for (const sw of BELL_SWITCHES) {
      expect(home).toMatch(new RegExp(`\\b${sw.key}: "connections-notifications"`));
    }
    expect(home).toContain('id: "connections-notifications"');
    expect(readSrc("components/settings/pages/NotificationsPage.tsx")).toContain("<BellNotices />");
  });
});
