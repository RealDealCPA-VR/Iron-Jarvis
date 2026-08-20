/**
 * v1.192.0 — three Creative-page defects, all of them about the app telling
 * the truth about what a click does:
 *
 *  - #20 the lightbox published to a PUBLIC CDN on a single click, while the
 *    tile popover in the same file asked first. The Share button was the worst
 *    case: it read as "open share options" and uploaded before any destination
 *    had been chosen. Both lightbox paths now go through the same
 *    `confirmPublish()` gate, and the button names its outcome.
 *  - #21 the one-click relaunch of a dead session called the SETUP form's
 *    start(), which is guarded on `chosenDir` — a value that comes from one
 *    mount-time /fs/list that never retries. A folder that no longer lists (a
 *    renamed/disconnected drive) made the button do nothing at all: no
 *    spinner, no error. It now relaunches from the SESSION's own recorded
 *    engine + folder, and a failed launch is rendered in the live phase.
 *  - #40 two surfaces still printed the raw "(code 4294967295)" that v1.191.0
 *    existed to eliminate — and with no turns on screen the ErrorNote is the
 *    only explanation the user gets.
 *
 * These are wiring defects, so they are tested through the real page: a
 * source-pin would not have caught "the click is swallowed".
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    FakeApiError,
    gets: [] as string[],
    posts: [] as { path: string; body?: unknown }[],
    getResponses: {} as Record<string, unknown>,
    postResponses: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    api.gets.push(path);
    const r = api.getResponses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    }
    if (r instanceof api.FakeApiError) return Promise.reject(r);
    return Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    const r = api.postResponses[path];
    if (r instanceof api.FakeApiError) return Promise.reject(r);
    return Promise.resolve(r ?? {});
  },
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path === null ? null : (api.getResponses[path] ?? null),
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));

vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));

// Neighbors this file does not test: the arrival animation and the mic.
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (prev: string, chunk: string) => prev + chunk,
}));

import CreativePage from "@/app/creative/page";

const ITEMS_PATH = "/creative/items?limit=500";
const STUDIO_KEY = "ironjarvis.creative.studio";
const VIEW_KEY = "ironjarvis.creative.view";

function publishPosts() {
  return api.posts.filter((p) => p.path === "/creative/publish");
}

beforeEach(() => {
  window.localStorage.clear();
  api.gets = [];
  api.posts = [];
  api.getResponses = {};
  api.postResponses = {};
  window.HTMLElement.prototype.scrollTo = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/* ------------------------------------------------- #20 publishing consent */

describe("publishing asks first, on every surface", () => {
  const ITEM = {
    name: "sunset",
    version: 1,
    media: "image" as const,
    kind: "image",
    filename: "sunset.png",
    size: 1234,
    session_id: null,
    created_at: "2026-08-20T10:00:00",
    url: "/creative/file/sunset",
  };

  async function openLightbox() {
    api.getResponses[ITEMS_PATH] = { items: [ITEM], count: 1 };
    render(<CreativePage />);
    const label = await screen.findByText("sunset.png");
    const tile = label.closest('[role="button"]');
    expect(tile).not.toBeNull();
    fireEvent.click(tile as Element);
    return await screen.findByRole("dialog");
  }

  it("the lightbox Share button NAMES the upload and does not publish when declined", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    await openLightbox();

    // The label says what the click does — "Share" alone reads as a menu.
    const shareBtn = screen.getByRole("button", { name: /Publish & share/i });
    fireEvent.click(shareBtn);

    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1));
    expect(confirmSpy.mock.calls[0][0]).toMatch(/public/i);
    // The whole point: declining uploads NOTHING, and no share row appears.
    expect(publishPosts()).toHaveLength(0);
    expect(screen.queryByText(/The link is public/i)).toBeNull();
  });

  it("the lightbox Share button publishes once accepted, then opens the destinations", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    api.postResponses["/creative/publish"] = { url: "https://cdn.test/sunset.png" };
    await openLightbox();

    fireEvent.click(screen.getByRole("button", { name: /Publish & share/i }));

    // Wait on the END of the handler (the destinations row), not on the POST.
    await waitFor(() => expect(screen.getByText(/The link is public/i)).toBeInTheDocument());
    expect(publishPosts()).toEqual([
      { path: "/creative/publish", body: { name: "sunset" } },
    ]);
  });

  it('the lightbox "Get public URL" button asks too', async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    await openLightbox();

    fireEvent.click(screen.getByRole("button", { name: /Get public URL/i }));

    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1));
    expect(publishPosts()).toHaveLength(0);
  });

  it("the tile popover still asks — the two surfaces share ONE gate", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    api.getResponses[ITEMS_PATH] = { items: [ITEM], count: 1 };
    render(<CreativePage />);

    fireEvent.click(await screen.findByRole("button", { name: "Share this item" }));

    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1));
    expect(publishPosts()).toHaveLength(0);
  });
});

/* ------------------------- #21 relaunch + #40 honest whole-terminal death */

describe("a dead studio session explains itself and can be relaunched", () => {
  const DEST = "D:\\Renders";
  const TAIL_PROBE = "/creative/studio/t-1/tail?chars=1";
  const TAIL_POLL = "/creative/studio/t-1/tail?chars=4000";

  function seedStoredSession(extra: Record<string, unknown> = {}) {
    window.localStorage.setItem(VIEW_KEY, "create");
    window.localStorage.setItem(
      STUDIO_KEY,
      JSON.stringify({
        cli: "claude", // the SETUP form's current pick — deliberately NOT the
        dir: DEST, //     session's engine, so a relaunch that reads the form
        autopilot: true, //   launches the wrong thing and this test sees it.
        session: {
          terminal_id: "t-1",
          dest: DEST,
          cli_label: "Codex",
          cli_id: "codex",
          skill: "Auto",
          skill_value: "",
          autopilot: true,
          command: "codex",
          sent_first: true,
          baseline: [],
          messages: ["make a teaser"],
          started_at: Date.now() - 60_000,
          ...extra,
        },
      }),
    );
  }

  function seedFetches() {
    api.getResponses[ITEMS_PATH] = { items: [], count: 0 };
    api.getResponses["/terminals/ai-clis"] = {
      clis: [
        { id: "codex", label: "Codex", installed: true, command: "codex" },
        { id: "claude", label: "Claude Code", installed: true, command: "claude" },
      ],
    };
    api.getResponses["/skills"] = { skills: [] };
    // THE TRIGGER: the destination no longer lists (renamed / disconnected
    // drive). The one mount-time listing fails and never retries, so
    // `chosenDir` is null forever — the state that used to swallow the click.
    api.getResponses[`/fs/list?path=${encodeURIComponent(DEST)}`] = new api.FakeApiError(
      "not a directory",
      400,
    );
    api.getResponses[`/creative/studio-media?path=${encodeURIComponent(DEST)}`] = {
      files: [],
      truncated: false,
    };
    api.getResponses[TAIL_PROBE] = { alive: true, exit_code: null, tail: "", mode: null, automode: false };
    // Killed FROM OUTSIDE — the restart-to-update case, Windows' unsigned -1.
    api.getResponses[TAIL_POLL] = {
      alive: false,
      exit_code: 4294967295,
      tail: "",
      mode: null,
      automode: false,
    };
    api.getResponses["/creative/studio/t-2/tail?chars=4000"] = {
      alive: true,
      exit_code: null,
      tail: "",
      mode: null,
      automode: false,
      ready: true,
    };
  }

  /** Resume the stored session and let the tail poll report the death. */
  async function reachDeadSession() {
    render(<CreativePage />);
    fireEvent.click(await screen.findByRole("button", { name: /Resume session/i }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /Start a new session — same engine, same folder/i }),
      ).toBeInTheDocument(),
    );
  }

  it("says what happened in words — the raw 4294967295 never reaches the screen", async () => {
    seedStoredSession();
    seedFetches();
    const { container } = render(<CreativePage />);
    fireEvent.click(await screen.findByRole("button", { name: /Resume session/i }));

    // The compact status footer is the LAST of the three surfaces to render
    // the death, and it is the one that used to be nothing but the number.
    await waitFor(() =>
      expect(screen.getByText("was closed from outside (code -1)")).toBeInTheDocument(),
    );
    // The ErrorNote (the ONLY message when no brief was ever sent) too.
    expect(container.textContent).toContain("This terminal was closed from outside (code -1)");
    expect(container.textContent).not.toContain("4294967295");
    expect(container.textContent).not.toContain("Terminal exited (code");
  });

  it("relaunches THIS session's engine and folder, even though the folder never listed", async () => {
    seedStoredSession();
    seedFetches();
    api.postResponses["/creative/studio/start"] = {
      terminal_id: "t-2",
      command: "codex",
      cwd: DEST,
      autopilot: true,
      cli: "codex",
    };
    await reachDeadSession();

    // Proof the defect's precondition holds: the listing really did fail.
    expect(api.gets).toContain(`/fs/list?path=${encodeURIComponent(DEST)}`);

    fireEvent.click(
      screen.getByRole("button", { name: /Start a new session — same engine, same folder/i }),
    );

    await waitFor(() => {
      const start = api.posts.filter((p) => p.path === "/creative/studio/start");
      expect(start).toHaveLength(1);
      // The SESSION's engine (codex), not the setup form's pick (claude), and
      // the session's folder, which no /fs/list ever confirmed.
      expect(start[0].body).toMatchObject({ cli: "codex", cwd: DEST, autopilot: true });
    });
  });

  it("a failed relaunch is REPORTED where the button is, not in the hidden setup form", async () => {
    seedStoredSession();
    seedFetches();
    api.postResponses["/creative/studio/start"] = new api.FakeApiError(
      "codex is not installed",
      424,
    );
    await reachDeadSession();

    fireEvent.click(
      screen.getByRole("button", { name: /Start a new session — same engine, same folder/i }),
    );

    await waitFor(() =>
      expect(screen.getByText(/codex is not installed/i)).toBeInTheDocument(),
    );
    // Still the live phase — the error is visible next to the dead session.
    expect(
      screen.getByRole("button", { name: /Start a new session — same engine, same folder/i }),
    ).toBeInTheDocument();
  });

  it("refuses honestly when a legacy session records no resolvable engine", async () => {
    // Pre-v1.192.0 records carry no cli_id, and this one's label matches no
    // installed engine — launching the form's current pick instead would be
    // exactly the wrong-engine bug, so it says so and launches nothing.
    seedStoredSession({ cli_id: undefined, cli_label: "Gemini CLI" });
    seedFetches();
    await reachDeadSession();

    fireEvent.click(
      screen.getByRole("button", { name: /Start a new session — same engine, same folder/i }),
    );

    await waitFor(() =>
      expect(screen.getByText(/isn.t available here/i)).toBeInTheDocument(),
    );
    expect(api.posts.filter((p) => p.path === "/creative/studio/start")).toHaveLength(0);
  });
});
