/**
 * v1.198.0 — the Help page finally describes the product the user is holding.
 *
 * WHAT THESE TESTS GUARD:
 *  - the SUBSYSTEMS grid leads with the FOUR HERO surfaces (Chat, Projects,
 *    Build, Creative) — the exact pages the default Simple-mode nav leads
 *    with, which the grid used to omit entirely;
 *  - the core-loop CTA sends a new user to /chat ("ask your first question"),
 *    not /sessions — the product thesis is ONE chat surface that escalates
 *    itself, and Sessions is the advanced watch-it-work lane (kept as a quiet
 *    secondary mention);
 *  - the Guides card renders the daemon's GET /helpdocs catalog, and clicking
 *    a guide fetches GET /helpdocs/{slug} and renders its markdown in-page —
 *    packaged users have no repo to browse, so this is the only road to the
 *    Handbook;
 *  - a missing doc shows the daemon's honest 404 message, never a blank panel;
 *  - the "If something looks wrong" card carries the README's real remedies
 *    (daemon-offline, SmartScreen) so troubleshooting exists in-app at all.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const hooks = vi.hoisted(() => {
  /** Same shape as lib/api's ApiError — the page does `instanceof` checks
   *  against the MOCKED module's export, so one class serves both sides. */
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    responses: {} as Record<string, unknown>, // useApi(path) -> data
    docs: {} as Record<string, unknown>, // get(path) -> resolved body
    gets: [] as string[], // every get() path, in order
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
  get: (path: string) => {
    hooks.gets.push(path);
    const body = hooks.docs[path];
    if (body !== undefined) return Promise.resolve(body);
    // The daemon's honest miss: unknown slug / doc deleted from the install.
    return Promise.reject(
      new hooks.MockApiError(`help doc is missing from this install`, 404),
    );
  },
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

import HelpPage from "@/app/help/page";

/* ------------------------------------------------------------------ fixtures */

const CATALOG = {
  docs: [
    {
      slug: "handbook",
      title: "The Handbook",
      description: "Every surface, the trust model, and troubleshooting.",
    },
    {
      slug: "recommended-settings",
      title: "Recommended Settings",
      description: "A tuned daily-driver profile.",
    },
    {
      slug: "local-models",
      title: "Local Models by RAM Tier",
      description: "What to run at each RAM size.",
    },
  ],
};

function renderHelp() {
  hooks.responses["/helpdocs"] = CATALOG;
  return render(<HelpPage />);
}

/** The grid card <a> whose blurb starts with `blurb`. */
function heroCard(blurb: RegExp): HTMLAnchorElement {
  const link = screen.getByText(blurb).closest("a");
  expect(link).not.toBeNull();
  return link as HTMLAnchorElement;
}

afterEach(() => {
  cleanup();
  hooks.responses = {};
  hooks.docs = {};
  hooks.gets = [];
});

/* --------------------------------------------------------------------- tests */

describe("Help page heroes + core loop (v1.198.0)", () => {
  it("the SUBSYSTEMS grid leads with all four hero surfaces, correctly linked", () => {
    renderHelp();

    expect(heroCard(/One surface for everything/)).toHaveAttribute("href", "/chat");
    expect(heroCard(/The context spine/)).toHaveAttribute("href", "/projects");
    expect(heroCard(/Live terminals side by side/)).toHaveAttribute("href", "/terminals");
    expect(heroCard(/Generate images, video, music and speech/)).toHaveAttribute(
      "href",
      "/creative",
    );

    // Titles ride on the same cards (Build is the nav's name for /terminals).
    expect(within(heroCard(/Live terminals side by side/)).getByText("Build")).toBeInTheDocument();
    expect(
      within(heroCard(/The context spine/)).getByText("Projects"),
    ).toBeInTheDocument();
  });

  it("the core-loop CTA points a new user at Chat, not Sessions", () => {
    renderHelp();

    const cta = screen.getByText(/Ready to try it\?/).closest("p");
    expect(cta).not.toBeNull();
    const link = within(cta as HTMLElement).getByRole("link");
    expect(link).toHaveAttribute("href", "/chat");
    expect(cta!.textContent).toContain("ask your first question");
    // The old copy sent first-timers to the advanced lane.
    expect(cta!.textContent).not.toContain("Sessions");

    // Sessions survives as the quiet secondary mention — watching runs in
    // detail is real, it's just not step one.
    const secondary = screen.getByText(/shows every run in detail/).closest("p");
    const sessionsLink = within(secondary as HTMLElement).getByRole("link");
    expect(sessionsLink).toHaveAttribute("href", "/sessions");

    // The chat-first steps replaced the sessions-first ones.
    expect(screen.getByText("Ask in Chat")).toBeInTheDocument();
    expect(screen.getByText("It escalates itself")).toBeInTheDocument();
    expect(screen.getByText("Review & approve")).toBeInTheDocument();
    expect(screen.queryByText("Start a session")).not.toBeInTheDocument();
  });
});

describe("Guides (v1.198.0)", () => {
  it("renders a card per doc from GET /helpdocs", () => {
    renderHelp();

    expect(screen.getByText("The Handbook")).toBeInTheDocument();
    expect(screen.getByText("Recommended Settings")).toBeInTheDocument();
    expect(screen.getByText("Local Models by RAM Tier")).toBeInTheDocument();
    expect(
      screen.getByText("Every surface, the trust model, and troubleshooting."),
    ).toBeInTheDocument();
  });

  it("clicking a guide fetches /helpdocs/handbook and renders its markdown", async () => {
    hooks.docs["/helpdocs/handbook"] = {
      slug: "handbook",
      title: "The Handbook",
      markdown: "# Inside the Handbook\n\nEverything, honestly told.",
    };
    renderHelp();

    fireEvent.click(screen.getByText("The Handbook").closest("button") as HTMLElement);

    await waitFor(() => expect(hooks.gets).toContain("/helpdocs/handbook"));
    // The markdown really renders — a heading element, not the raw `#` text.
    const heading = await screen.findByRole("heading", { name: "Inside the Handbook" });
    expect(heading).toBeInTheDocument();
    expect(screen.getByText("Everything, honestly told.")).toBeInTheDocument();
  });

  it("a missing doc shows the daemon's 404 message, never a blank panel", async () => {
    // No hooks.docs entry => the mocked get() rejects like the daemon's 404.
    renderHelp();

    fireEvent.click(
      screen.getByText("Recommended Settings").closest("button") as HTMLElement,
    );

    await waitFor(() =>
      expect(hooks.gets).toContain("/helpdocs/recommended-settings"),
    );
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("missing from this install");
  });
});

describe("Troubleshooting card (v1.198.0)", () => {
  it("lists the daemon-offline and SmartScreen entries with the README's remedies", () => {
    renderHelp();

    expect(screen.getByText("If something looks wrong")).toBeInTheDocument();

    // Daemon offline: quit from the tray and relaunch (the app supervises).
    const daemonRow = screen
      .getByText(/“Daemon offline” in the dashboard/)
      .closest("li");
    expect(daemonRow).not.toBeNull();
    expect(daemonRow!.textContent).toMatch(/Quit from the tray and relaunch/i);
    expect(daemonRow!.textContent).toMatch(/supervises and restarts its daemon/i);

    // SmartScreen: unsigned yet; More info -> Run anyway, once per download.
    const ssRow = screen.getByText("Windows SmartScreen on install").closest("li");
    expect(ssRow).not.toBeNull();
    expect(ssRow!.textContent).toContain("More info");
    expect(ssRow!.textContent).toContain("Run anyway");
    expect(ssRow!.textContent).toMatch(/once per download/i);

    // The other README claims ride along: port conflict, data home.
    expect(screen.getByText(/“Port 8787 already in use”/)).toBeInTheDocument();
    expect(screen.getByText(/%APPDATA%\\Iron Jarvis/)).toBeInTheDocument();
  });

  it("'Something else?' points at the Overview's System health (Advanced), not Settings", () => {
    // The System health panel renders on the OVERVIEW behind the Advanced nav
    // toggle (app/page.tsx `{advanced && ...}`); Settings has no such card.
    // This entry once mirrored a stale README sentence linking /settings.
    renderHelp();

    const row = screen.getByText("Something else?").closest("li");
    expect(row).not.toBeNull();
    const link = within(row as HTMLElement).getByRole("link");
    expect(link).toHaveAttribute("href", "/");
    expect(link.getAttribute("href")).not.toContain("/settings");
    expect(row!.textContent).toMatch(/System health card on the Overview/);
    // The Advanced gate is real — the copy must tell the user how to see it.
    expect(row!.textContent).toContain("Advanced");
    expect(row!.textContent).not.toContain("Settings");
  });
});
