/**
 * v1.436.0 (chat-first plan Wave 5, group J) — the review's dashboard pins.
 *
 * A door under a reply is DATA: a saved conversation is drawn again without
 * going back through `decodeDoors`, so DoorsStrip itself must never draw a
 * press it does not know, and a sign-in press must never start a terminal by
 * being drawn — only by the user's click.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

const hooks = vi.hoisted(() => ({ posts: [] as { path: string; body: unknown }[] }));

vi.mock("@/lib/api", () => ({
  API_BASE: "http://test",
  ApiError: class extends Error {},
  ijToken: () => "",
  get: () => Promise.resolve({}),
  post: (path: string, body: unknown) => {
    hooks.posts.push({ path, body });
    return Promise.resolve({ id: "pane-1" });
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, onClick, ...rest }: React.ComponentProps<"a">) => (
    <a
      href={href}
      {...rest}
      onClick={(e) => {
        onClick?.(e);
        e.preventDefault();
      }}
    >
      {children}
    </a>
  ),
}));

import { DoorsStrip } from "@/components/chat/DoorsStrip";

afterEach(() => {
  cleanup();
  hooks.posts = [];
  window.localStorage.clear();
});

describe("a sign-in door starts nothing until it is pressed", () => {
  it("is drawn as the press and POSTs nothing before the click", async () => {
    render(<DoorsStrip doors={[{ href: "/connections", label: "Sign in to Codex", press: "codex-cli" }]} />);
    await act(async () => {});
    expect(screen.getByRole("button", { name: "Sign in to Codex" })).toBeInTheDocument();
    expect(hooks.posts).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Sign in to Codex" }));
    await act(async () => {});
    expect(hooks.posts).toEqual([{ path: "/terminals/launch", body: { cli: "codex", account: "default" } }]);
  });
});

describe("DoorsStrip never draws a press it does not know (a saved door is data)", () => {
  it.each(["evil-cli", "claude; del /q *", "openai", ""])(
    "press %j on an undecoded door is its plain link and launches nothing",
    async (press) => {
      render(<DoorsStrip doors={[{ href: "/connections", label: "Open your accounts", press }]} />);
      await act(async () => {});
      expect(screen.queryByRole("button")).toBeNull();
      const link = screen.getByRole("link", { name: "Open your accounts" });
      expect(link).toHaveAttribute("href", "/connections");
      fireEvent.click(link);
      await act(async () => {});
      expect(hooks.posts).toEqual([]);
    },
  );
});
