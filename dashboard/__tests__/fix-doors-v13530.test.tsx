/**
 * v1.353.0 lane 3B (G41) — A REFUSAL LEAVES A BUTTON TO THE FIX.
 *
 * The daemon now adds a door when a call failed because something is not
 * SET UP: "Connect Google Drive in Settings" for an app that signs in through
 * the browser, "Pair your browser" for an unpaired browser. The doors ride
 * the reply's existing `doors` field, so the chat draws them with the SAME
 * DoorsStrip as every other door — no new card, no new look.
 *
 * The doors here are READ from the contract fixture the Python suite writes
 * from the REAL daemon's done frame
 * (tests/test_chat_guide_media_doors_v13530.py, IJ_REGEN_FIX_DOORS=1), so a
 * daemon that changes the words or the address turns this file red too.
 * And each address must land somewhere real: the Settings section the
 * Directory lives in, and the Browser page's redirect.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { DoorsStrip } from "@/components/chat/DoorsStrip";
import { RELOCATED } from "@/lib/relocated";

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

afterEach(() => {
  cleanup();
});

/** CRLF-normalised at the reader (this checkout is autocrlf). */
function read(rel: string): string {
  return readFileSync(join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");
}

type Door = { href: string; label: string };
const FIXTURE = JSON.parse(read("__tests__/fixtures/fix-doors-v13530.json")) as {
  google_drive: Door[];
  unpaired_browser: Door[];
};

describe("fix doors under a reply (G41)", () => {
  it("a Drive refusal shows a 'Connect Google Drive in Settings' button to the Directory", () => {
    render(<DoorsStrip doors={FIXTURE.google_drive} />);
    const link = screen.getByRole("link", { name: "Connect Google Drive in Settings" });
    expect(link).toHaveAttribute("href", "/settings?section=connections-apps");
  });

  it("an unpaired-browser refusal shows 'Pair your browser' to the Browser page", () => {
    render(<DoorsStrip doors={FIXTURE.unpaired_browser} />);
    const link = screen.getByRole("link", { name: "Pair your browser" });
    expect(link).toHaveAttribute("href", "/computeruse");
  });

  it("each address opens a real place: the Directory section, the Browser page", () => {
    // /settings?section=connections-apps is the Directory's own section id.
    expect(read("components/settings/SettingsHome.tsx")).toContain('id: "connections-apps"');
    // /computeruse redirects to Settings › Connections › Browser.
    expect(RELOCATED["/computeruse"]).toEqual({
      base: "/settings",
      extra: { section: "connections-browser" },
    });
  });

  it("a fix door looks like every other door (same strip, same pill)", () => {
    render(
      <DoorsStrip
        doors={[{ href: "/workflows", label: "Open the canvas" }, ...FIXTURE.unpaired_browser]}
      />,
    );
    const [plain, fix] = screen.getAllByRole("link");
    expect(fix.className).toBe(plain.className);
  });
});
