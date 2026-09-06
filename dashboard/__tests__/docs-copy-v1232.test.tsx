/**
 * v1.232.0 (audit Wave 6, 6B) — the pages say what the Handbook says.
 *
 *  - Documents: convert / split / merge / batch have no control on the page
 *    (they are chat jobs), so the read card's empty state says so and offers
 *    the four as chips that open Chat with the request typed in (`/chat?ask=`).
 *  - Memory: imports live on the Long-term tab, which Simple mode's nav hides
 *    behind /memory; every OTHER tab carries the one-line door to it, and the
 *    Long-term tab itself does not (the import card is already on screen).
 *  - Help: the "Daemon offline" entry says the same thing as the Handbook and
 *    README — tray restarts, two misses, Settings → Maintenance.
 *
 * Whole pages are mounted (not the components alone) so the assertions prove
 * the copy is WIRED IN, not merely written.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

const hooks = vi.hoisted(() => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    responses: {} as Record<string, unknown>,
    search: "",
    MockApiError,
  };
});

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

vi.mock("@/lib/api", () => ({
  API_BASE: "http://test",
  ApiError: hooks.MockApiError,
  ijToken: () => "",
  setIjToken: () => {},
  onUnauthorizedChange: () => () => {},
  wsUrl: (p: string) => `ws://test${p}`,
  sseUrl: (p: string) => `http://test${p}`,
  get: () => Promise.resolve({}),
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
  api: () => Promise.resolve({}),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(hooks.search),
  usePathname: () => "/memory",
}));

vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (p: string, c: string) => p + c,
}));

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

const DocumentsPage = (await import("@/app/documents/page")).default;
const MemoryPage = (await import("@/app/memory/page")).default;
const HelpPage = (await import("@/app/help/page")).default;

beforeEach(() => {
  hooks.search = "";
  hooks.responses = { "/documents/live": { docs: [] }, "/helpdocs": { docs: [] } };
});
afterEach(cleanup);

describe("Documents empty state (v1.232.0)", () => {
  it("says convert/split/merge/batch are done from Chat and offers the four as chips", () => {
    render(<DocumentsPage />);
    expect(
      screen.getByText(/Convert, split, merge and batch are done from Chat/),
    ).toBeInTheDocument();
    for (const label of ["Convert", "Split", "Merge", "Batch"]) {
      const chip = screen.getByRole("link", { name: label });
      const href = chip.getAttribute("href") ?? "";
      expect(href.startsWith("/chat?ask=")).toBe(true);
      // The chip carries a real request, not an empty composer.
      expect(decodeURIComponent(href.slice("/chat?ask=".length)).length).toBeGreaterThan(10);
    }
  });
});

describe("Memory import door (v1.232.0)", () => {
  it("every non-long-term tab links to the Long-term tab where imports live", () => {
    hooks.search = "scope=working";
    render(<MemoryPage />);
    const link = screen.getByTestId("memory-import-link");
    expect(link).toHaveTextContent("Import from ChatGPT/Claude/Takeout → Long-term memory");
    expect(link).toHaveAttribute("href", "/memory?scope=longterm");
  });

  it("the Long-term tab itself does not repeat the door", () => {
    hooks.search = "scope=longterm";
    render(<MemoryPage />);
    expect(screen.queryByTestId("memory-import-link")).toBeNull();
  });
});

describe("Help 'Daemon offline' entry (v1.232.0)", () => {
  it("says the same thing as the Handbook: tray restarts, two misses, Maintenance", () => {
    render(<HelpPage />);
    const row = screen.getByText(/“Daemon offline” in the dashboard/).closest("li");
    expect(row).not.toBeNull();
    const text = row!.textContent ?? "";
    expect(text).toMatch(/two missed polls/);
    expect(text).toMatch(/quit from the tray and relaunch/i);
    expect(text).toMatch(/Restart Iron Jarvis/);
    expect(text).toMatch(/Copy diagnostics/);
    expect(text).toMatch(/Open logs folder/);
    expect(within(row as HTMLElement).getByRole("link", { name: /Settings → Maintenance/ })).toHaveAttribute(
      "href",
      "/settings",
    );
  });
});
