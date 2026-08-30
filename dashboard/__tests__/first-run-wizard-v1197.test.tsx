import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * The first-run wizard's three doors + the /chat finale (v1.197.0).
 *
 * WHAT THESE TESTS GUARD:
 *  - the wizard shows exactly when onboarding says first_run AND no choice is
 *    stored, and its not-yet-connected step 1 opens with three PLAIN-LANGUAGE
 *    doors instead of leading with "CLIs" and "API keys" — vocabulary the old
 *    copy assumed a first-runner already knew;
 *  - the key door keeps the pre-v1.197.0 mechanics byte-for-byte: submitting a
 *    key POSTs /connections/anthropic/key then /connections/anthropic/test —
 *    the doors are presentation, not a new connection path;
 *  - the Ollama door links OUT to ollama.com/download (new tab), and Gemini is
 *    a LINK to /connections, never a key form — the google ConnectionSpec is
 *    OAuth-only and 400s a posted key, so a Gemini key field could only ever
 *    produce an error a new user cannot interpret;
 *  - the Ollama door has a real MECHANISM, not a promise: "Connect to Ollama
 *    on this PC" PUTs the default local URL to /settings (rescan cannot
 *    detect Ollama — availability is gated on ollama_base_url being
 *    configured), and the door carries no rescan claim;
 *  - the wizard is LATCHED open: first_run flips false the moment the wizard
 *    succeeds at its own steps, and every success path reloads /onboarding —
 *    the mocked reload flips the fixture exactly as production would, so a
 *    show condition that re-evaluated first_run mid-flow fails here;
 *  - finishing lands on /chat, the product's hero surface: "Start using Iron
 *    Jarvis" stores the choice AND router.push("/chat"), instead of
 *    dead-ending on whatever page happened to sit under the modal.
 */

const hooks = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: unknown }>,
  puts: [] as Array<{ path: string; body: unknown }>,
  routerPush: vi.fn(),
}));

// The real hooks KEEP the last data when `path` goes null — state persists,
// only the fetching stops (lib/useApi.ts). The wizard leans on that: the
// moment a run completes it STOPS polling `/sessions/{id}`, and the completed
// panel keeps rendering off the retained detail. A mock that nulls data with
// the path unmounts "Start using Iron Jarvis" one commit after it appears.
vi.mock("@/lib/useApi", async () => {
  const { useRef } = await import("react");
  const useStub = (path: string | null) => {
    const last = useRef<unknown>(null);
    if (path !== null) last.current = hooks.api[path] ?? null;
    return {
      data: last.current ?? null,
      error: null,
      loading: false,
      // PRODUCTION-SHAPED reload, not a no-op (v1.197.0 review): the wizard
      // calls refreshAll() → reloadOnboarding() on every success, and in the
      // real daemon a connected provider / first session row flips first_run
      // FALSE on that very reload. A `reload: () => {}` stub let a wizard
      // whose show condition re-evaluated first_run pass every test while
      // unmounting mid-flow in production — the latch below is what these
      // tests now genuinely exercise.
      reload: () => {
        if (path === "/onboarding") {
          hooks.api["/onboarding"] = {
            ...(hooks.api["/onboarding"] as Record<string, unknown>),
            first_run: false,
          };
        }
      },
    };
  };
  return { useApi: useStub, usePolledApi: useStub };
});

vi.mock("@/lib/api", () => ({
  API_BASE: "http://test",
  ijToken: () => "tok-1",
  get: () => Promise.resolve({}),
  put: (path: string, body?: unknown) => {
    hooks.puts.push({ path, body });
    return Promise.resolve({});
  },
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
  post: (path: string, body?: unknown) => {
    hooks.posts.push({ path, body });
    if (path === "/sessions") return Promise.resolve({ id: "s-1", status: "active" });
    if (path.endsWith("/test")) return Promise.resolve({ ok: true, detail: "model replied" });
    return Promise.resolve({});
  },
}));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    health: hooks.api["health"] ?? null,
    refresh: () => {},
  }),
}));

// Voice never matters to these tests; an inert, supported=false dictation keeps
// step 2 rendering without a mic in jsdom.
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false,
    reason: "no mic in tests",
    listening: false,
    processing: false,
    transcript: "",
    interim: "",
    error: null,
    start: () => {},
    stop: () => {},
    reset: () => {},
  }),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: hooks.routerPush }),
}));

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial",
    "animate",
    "exit",
    "transition",
    "variants",
    "whileHover",
  ]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  // CACHED per tag: handing back a fresh component function on every access
  // gives React a new component IDENTITY each render, which remounts the whole
  // subtree — a node found by findBy* can be detached before the click lands.
  const cache = new Map<string, unknown>();
  return {
    AnimatePresence: ({ children }: { children?: unknown }) =>
      createElement(Fragment, null, children as never),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

import { FirstRunWizard } from "@/components/FirstRunWizard";

const ONBOARDING = {
  version: "1.197.0",
  first_run: true,
  doctor: { ok: true, checks: [] },
  checklist: [
    {
      key: "connect_ai",
      title: "Connect a model",
      detail: "",
      done: false,
      action: "",
    },
    {
      key: "set_up_voice",
      title: "Set up voice (optional)",
      detail: "",
      done: false,
      action: "",
      optional: true,
    },
    {
      key: "first_session",
      // The REAL current backend title — the wizard reconciles its headings
      // onto the checklist, so the fixture must not pin an older era's copy.
      title: "Give it your first task",
      detail: "",
      done: false,
      action: "",
    },
  ],
  next_step: null,
};

const DOOR_LABELS = [
  "I already pay for Claude or ChatGPT",
  "Free & private on this PC",
  "I have an API key",
];

const door = (label: string) => screen.getByRole("button", { name: new RegExp(label) });

beforeEach(() => {
  hooks.api = {
    "/onboarding": ONBOARDING,
    "/voice/status": { available: false, backend: null, hint: "" },
    // No providers available → step 1 renders the not-connected branch.
    health: { status: "ok", version: "1.197.0", providers: [] },
  };
  hooks.posts = [];
  hooks.puts = [];
  hooks.routerPush = vi.fn();
  window.localStorage.clear();
});
afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

/* ------------------------------------------------------------ the doors --- */

describe("step 1 — three plain-language doors", () => {
  it("shows on a first run with no stored choice, doors first", async () => {
    render(<FirstRunWizard />);
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    for (const label of DOOR_LABELS) expect(door(label)).toBeTruthy();
    // Nothing is pre-picked: the mechanics stay hidden until the user says
    // which person they are — no key input, no download link yet.
    expect(screen.queryByPlaceholderText("sk-ant-…")).toBeNull();
    expect(screen.queryByText("Download Ollama")).toBeNull();
  });

  it("never shows once a choice is stored", async () => {
    window.localStorage.setItem("ij_first_run_choice", "done");
    render(<FirstRunWizard />);
    // The effect that reads storage must run before "nothing" is proven.
    await waitFor(() =>
      expect(window.localStorage.getItem("ij_first_run_choice")).toBe("done"),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

/* --------------------------------------------------- the key door's wiring --- */

describe("the key door — pre-v1.197.0 mechanics, untouched", () => {
  it("saves then tests the key: POST /connections/anthropic/key → /test", async () => {
    render(<FirstRunWizard />);
    fireEvent.click(door("I have an API key"));
    fireEvent.change(screen.getByPlaceholderText("sk-ant-…"), {
      target: { value: "sk-ant-test-123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    // The success note is set AFTER both awaited posts — waiting on it waits
    // on the real end of the handler, not the first observable side effect.
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Connected"));
    expect(hooks.posts.map((p) => p.path)).toEqual([
      "/connections/anthropic/key",
      "/connections/anthropic/test",
    ]);
    expect(hooks.posts[0].body).toEqual({ key: "sk-ant-test-123" });
    // THE LATCH, exercised for real: the connect's refreshAll() reloaded
    // /onboarding, which — as in production, where a connected provider ends
    // first_run — flips the fixture. WAITED FOR, not asserted on the next line
    // (v1.214.1): the note above renders when the handler's last `await`
    // resolves, and `refreshAll()` lands in a separate commit after it, so
    // this was a race that a loaded runner loses. Waiting is also the truer
    // shape of the claim — the guarantee is "the wizard is still standing
    // AFTER the flip", which cannot be checked before the flip has happened.
    await waitFor(() =>
      expect((hooks.api["/onboarding"] as { first_run: boolean }).first_run).toBe(
        false,
      ),
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});

/* -------------------------------------------------------- the two pointers --- */

describe("the honest pointers", () => {
  it("the Ollama door links out to ollama.com/download in a new tab", async () => {
    render(<FirstRunWizard />);
    fireEvent.click(door("Free & private on this PC"));
    const a = (await screen.findByText("Download Ollama")) as HTMLAnchorElement;
    expect(a.getAttribute("href")).toBe("https://ollama.com/download");
    expect(a.getAttribute("target")).toBe("_blank");
    expect(a.getAttribute("rel")).toBe("noreferrer");
    // The pull snippet and the honest capability line ride along.
    expect(screen.getByText("ollama pull llama3.2")).toBeTruthy();
    expect(screen.getByText(/less capable than a frontier one/)).toBeTruthy();
  });

  it("the Ollama door CONNECTS — configures the URL instead of promising a rescan", async () => {
    // ADVERSARIAL REVIEW (v1.197.0). "Rescan now" only enumerates CLIs, and
    // ollama availability is gated on ollama_base_url being CONFIGURED — so a
    // door made of download → pull → rescan stayed red forever while its copy
    // promised "turns green on its own". The honest mechanism is a Connect
    // button that saves the default local URL (the daemon normalizes it and
    // live-reconfigures the manager; the health poll then proves real
    // reachability).
    render(<FirstRunWizard />);
    fireEvent.click(door("Free & private on this PC"));
    // No rescan claim in THIS door — it would be a lie here.
    expect(screen.queryByRole("button", { name: /Rescan now/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Connect to Ollama on this PC" }));
    // The re-check promise only appears AFTER connecting, when it is true —
    // waiting on it waits on the real end of the handler.
    await waitFor(() =>
      expect(screen.getByText(/turns green on its own once Ollama is reachable/)).toBeTruthy(),
    );
    expect(hooks.puts).toEqual([
      { path: "/settings", body: { values: { ollama_base_url: "http://localhost:11434" } } },
    ]);
    // ...and if it stays grey the copy says what to check, honestly.
    expect(screen.getByText(/check that Ollama is actually running/)).toBeTruthy();
    // The subscription door is where the rescan claim IS true (claude/codex
    // presence is a live per-poll /health check) — it keeps the button.
    fireEvent.click(door("I already pay for Claude or ChatGPT"));
    expect(screen.getByRole("button", { name: /Rescan now/ })).toBeTruthy();
  });

  it("Gemini is a LINK to /connections, never a key form", async () => {
    // The google ConnectionSpec is OAuth-only: POST /connections/google/key
    // 400s. A Gemini entry in the key form could only ever manufacture an
    // error, so the wizard points at the Connections page instead.
    render(<FirstRunWizard />);
    fireEvent.click(door("I have an API key"));
    const link = (await screen.findByText("Connections page")) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/connections");
    // ...and no door ever grew a google key option.
    expect(screen.queryByRole("button", { name: /google|gemini/i })).toBeNull();
  });
});

/* ------------------------------------------------------------- the finale --- */

describe("the finale — finishing lands on /chat", () => {
  it("survives its own success and lands on /chat", async () => {
    // A completed run is already waiting at the polled endpoint, so reaching
    // step 3 and starting a task lands directly on the celebration panel.
    hooks.api["/sessions/s-1"] = {
      session: { status: "completed", summary: "All good" },
      transcript: { tools: [] },
    };
    render(<FirstRunWizard />);
    fireEvent.click(screen.getByRole("button", { name: /First task/ }));
    // The step heading is the RECONCILED checklist title, not hardcoded copy.
    expect(screen.getByText("Give it your first task")).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", { name: "Summarize what you can do for me in 5 bullets" }),
    );
    const start = await screen.findByRole("button", { name: /Start using Iron Jarvis/ });
    // THE LATCH IS LOAD-BEARING HERE (v1.197.0 review): completing the run
    // called refreshAll() → reloadOnboarding(), and — as in production, where
    // a session row ends first_run — the fixture has ALREADY flipped by the
    // time the celebration renders. A wizard whose show condition re-read
    // first_run unmounted right now, making this button unreachable; the old
    // no-op reload stub could never see that.
    // SEEN RED TWICE on a loaded runner before this was awaited (v1.214.1):
    // the celebration renders as soon as the run reports completed, and the
    // `refreshAll()` that flips the fixture lands in a later commit. Same
    // correction as its sibling above, and the same reason it is the better
    // shape: the button has to survive the flip, so the flip has to have
    // happened before "still reachable" means anything.
    await waitFor(() =>
      expect((hooks.api["/onboarding"] as { first_run: boolean }).first_run).toBe(
        false,
      ),
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /Start using Iron Jarvis/ }),
    ).toBeTruthy();
    fireEvent.click(start);
    await waitFor(() => {
      expect(window.localStorage.getItem("ij_first_run_choice")).toBe("done");
      expect(hooks.routerPush).toHaveBeenCalledWith("/chat");
    });
    // The modal is gone — the user is standing in /chat, not under an overlay.
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
