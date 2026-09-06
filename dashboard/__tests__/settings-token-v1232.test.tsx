/**
 * v1.232.0 (audit Wave 6, task 6A) — the Settings "Daemon access token" box.
 *
 * The packaged desktop app ALWAYS seeds this token (desktop/preload.js writes
 * localStorage["ij_token"] before the bundle runs), so "Local installs
 * usually need no token — leave this empty" was wrong for every daily-driver
 * user, and Clear was a lever that 401'd the app. Inside the desktop shell the
 * card is read-only and says who set it; the paste box + Clear + "leave this
 * empty" belong to a browser WITHOUT the bridge.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const tok = vi.hoisted(() => ({ value: "" }));
vi.mock("@/lib/api", () => ({
  ijToken: () => tok.value,
  setIjToken: () => {},
}));

import { DaemonTokenCard } from "@/components/settings/DaemonTokenCard";

const win = window as unknown as { ironjarvis?: { isDesktop?: boolean } };

beforeEach(() => {
  delete win.ironjarvis;
  tok.value = "";
});
afterEach(cleanup);

describe("DaemonTokenCard (v1.232.0)", () => {
  it("inside the desktop app with a seeded token: read-only, 'Set by the desktop app', no Clear", () => {
    win.ironjarvis = { isDesktop: true };
    tok.value = "deadbeef";
    render(<DaemonTokenCard />);
    expect(screen.getByTestId("token-seeded")).toHaveTextContent(/Set by the desktop app/);
    expect(screen.queryByRole("button", { name: /clear/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /save token/i })).toBeNull();
    expect(screen.queryByText(/leave this empty/i)).toBeNull();
    const box = screen.getByLabelText(/Daemon access token \(set by the desktop app\)/);
    expect(box).toHaveAttribute("readonly");
  });

  it("a browser without the bridge keeps the paste box, Save, Clear and the 'leave this empty' line", () => {
    tok.value = "";
    render(<DaemonTokenCard />);
    expect(screen.getByRole("button", { name: /clear/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save token/i })).toBeInTheDocument();
    expect(screen.getByText(/leave this\s+empty/i)).toBeInTheDocument();
    expect(screen.queryByText(/Set by the desktop app/)).toBeNull();
  });

  it("the desktop shell with NO token (auth disabled) still shows the paste box — nothing was seeded", () => {
    win.ironjarvis = { isDesktop: true };
    tok.value = "";
    render(<DaemonTokenCard />);
    expect(screen.getByRole("button", { name: /save token/i })).toBeInTheDocument();
    expect(screen.queryByText(/Set by the desktop app/)).toBeNull();
  });
});

describe("Settings page wiring", () => {
  it("mounts the card and no longer carries its own token box", () => {
    const src = readFileSync(join(process.cwd(), "app", "settings", "page.tsx"), "utf8");
    expect(src).toContain('import { DaemonTokenCard } from "@/components/settings/DaemonTokenCard";');
    expect(src).toContain("<DaemonTokenCard />");
    expect(src).not.toContain("Local installs usually need no token");
    expect(src).not.toContain("setIjToken");
  });
});
