/**
 * v1.450.0 review: the Sessions page's ⋯ menu that holds the clear-all press.
 *
 *  - It opens from the keyboard (its ⋯ is a real <summary>), Escape closes it
 *    and puts the caret back on ⋯ (a bare <details> ignores Escape).
 *  - The wipe still takes TWO presses: the first only arms it, nothing is
 *    posted; the second posts /sessions/clear.
 *  - The two words the lane added in this area carry no spaced dash (the
 *    app's plain-words rule): the title bar's Home link and a Launch row for
 *    a program that is not on this PC.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const hooks = vi.hoisted(() => ({ posts: [] as { path: string; body: unknown }[] }));

vi.mock("@/lib/useApi", () => {
  const read = () => ({ data: null, error: null, loading: false, reload: () => {} });
  return { useApi: read, usePolledApi: read };
});

vi.mock("@/lib/api", () => ({
  API_BASE: "http://test",
  ApiError: class extends Error {},
  ijToken: () => "",
  get: () => new Promise(() => {}),
  post: (path: string, body?: unknown) => {
    hooks.posts.push({ path, body });
    return Promise.resolve({ cleared: 0 });
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, back: () => {}, prefetch: () => {} }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/sessions",
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

import SessionsPage from "@/app/sessions/page";

const ROOT = join(__dirname, "..");
const src = (rel: string) => readFileSync(join(ROOT, rel), "utf8").replace(/\r\n/g, "\n");

afterEach(() => {
  cleanup();
  hooks.posts = [];
});

describe("the Sessions ⋯ menu", () => {
  it("opens, closes on Escape with the caret back on ⋯, and still asks twice", async () => {
    render(<SessionsPage />);
    const menu = (await screen.findByTestId("sessions-more")) as HTMLDetailsElement;
    const summary = within(menu).getByLabelText("More actions");
    expect(summary.tagName).toBe("SUMMARY");
    expect(menu.open).toBe(false);

    menu.open = true;
    const press = within(menu).getByRole("button", { name: /Clear finished, failed and stopped/ });
    press.focus();
    fireEvent.keyDown(press, { key: "Escape" });
    expect(menu.open).toBe(false);
    expect(document.activeElement).toBe(summary);

    menu.open = true;
    await act(async () => {
      fireEvent.click(within(menu).getByRole("button", { name: /Clear finished, failed and stopped/ }));
    });
    expect(hooks.posts).toEqual([]);
    await act(async () => {
      fireEvent.click(within(menu).getByRole("button", { name: /Delete them all\?/ }));
    });
    expect(hooks.posts.map((p) => p.path)).toEqual(["/sessions/clear"]);
  });
});

describe("no spaced dash in the words this lane added", () => {
  it.each([
    ["components/TitleBar.tsx", 'aria-label="Iron Jarvis: Home"', "Iron Jarvis — Home"],
    ["components/terminal/TerminalPane.tsx", "where this PC can find it. Get it here.", "not on this PC — get it"],
  ])("%s", (file, now, gone) => {
    const text = src(file);
    expect(text).toContain(now);
    expect(text).not.toContain(gone);
  });
});
