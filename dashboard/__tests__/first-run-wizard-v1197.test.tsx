import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * The connect doors' MECHANICS (v1.197.0; the file kept its name).
 *
 * v1.439.0 (Wave 6, D6): the first-run wizard these tests were written
 * through was removed — it had not been mounted since the calm redesign
 * (v1.321.0). The doors it used are the shared `ConnectDoors`, which the chat's
 * empty state still shows while replies are the demo, so the pins on the
 * doors' own mechanics moved onto it; the wizard-only pins (its show rule, its
 * latch across a first_run flip, its "Start using Iron Jarvis" finale) left
 * with the wizard.
 *
 * WHAT THESE TESTS GUARD:
 *  - three PLAIN-LANGUAGE doors, nothing pre-picked (no key box, no download
 *    link) until the user says which person they are;
 *  - the key door keeps the pre-v1.197.0 mechanics: submitting a key POSTs
 *    /connections/anthropic/key then /connections/anthropic/test;
 *  - the Ollama door links OUT to ollama.com/download (new tab) and CONNECTS
 *    (PUTs the default local URL to /settings) instead of promising a rescan;
 *    Gemini takes an AI Studio key in the key form (v1.355.0, G46).
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
    // v1.250.0 (S-08): components animate with framer's slim `m.*` under the
    // layout's LazyMotion, so this mock must export `m` too — otherwise every
    // mocked surface throws "No \"m\" export is defined on the mock".
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
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

import { ConnectDoors } from "@/components/onboarding/ConnectDoors";

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

describe("three plain-language doors", () => {
  it("offers the three doors with nothing pre-picked", async () => {
    render(<ConnectDoors />);
    for (const label of DOOR_LABELS) expect(door(label)).toBeTruthy();
    // Nothing is pre-picked: the mechanics stay hidden until the user says
    // which person they are — no key input, no download link yet.
    expect(screen.queryByPlaceholderText("sk-ant-…")).toBeNull();
    expect(screen.queryByText("Download Ollama")).toBeNull();
  });
});

/* --------------------------------------------------- the key door's wiring --- */

describe("the key door — pre-v1.197.0 mechanics, untouched", () => {
  // v1.443.0, moved on purpose: the key is TESTED before it is saved
  // (`?check=1`), and only a key that passed is chosen (/onboarding/use-model).
  // This fake daemon answers like an older one, so the door also asks /test.
  it("tests the key before it is saved, then chooses it: key?check=1 → /test → use-model", async () => {
    render(<ConnectDoors />);
    fireEvent.click(door("I have an API key"));
    fireEvent.change(screen.getByPlaceholderText("sk-ant-…"), {
      target: { value: "sk-ant-test-123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    // Wait on the END of the handler (its result line), not the first post.
    await waitFor(() => expect(screen.getByTestId("key-door-result").textContent).toContain("Connected"));
    await waitFor(() =>
      expect(hooks.posts.map((p) => p.path)).toEqual([
        "/connections/anthropic/key?check=1",
        "/connections/anthropic/test",
        "/onboarding/use-model",
      ]),
    );
    expect(hooks.posts[0].body).toEqual({ key: "sk-ant-test-123" });
  });
});

/* -------------------------------------------------------- the two pointers --- */

describe("the honest pointers", () => {
  it("the Ollama door links out to ollama.com/download in a new tab", async () => {
    render(<ConnectDoors />);
    fireEvent.click(door("Free & private on this PC"));
    const a = (await screen.findByText("Download Ollama")) as HTMLAnchorElement;
    expect(a.getAttribute("href")).toBe("https://ollama.com/download");
    expect(a.getAttribute("target")).toBe("_blank");
    expect(a.getAttribute("rel")).toBe("noreferrer");
    // v1.355.0 (G73), changed on purpose: there is no pull snippet to copy
    // any more; step 2 is one "Download a starter model" press (a Build pane
    // runs the download — model-doors-v13550 pins the press). The honest
    // capability line still rides along.
    expect(screen.queryByText("ollama pull llama3.2")).toBeNull();
    expect(screen.getByRole("button", { name: /Download a starter model/ })).toBeTruthy();
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
    render(<ConnectDoors />);
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

  it("Gemini takes a key in the key form (v1.355.0, G46)", async () => {
    // Changed on purpose. Until v1.355.0 the google ConnectionSpec was
    // OAuth-only (a posted key 400'd), so this door only pointed at the
    // Connections page. The spec takes an AI Studio key now, so the key form
    // offers Google Gemini like the other providers — and the old pointer to
    // an account login is gone (model-doors-v13550 pins the post + test).
    render(<ConnectDoors />);
    fireEvent.click(door("I have an API key"));
    expect(await screen.findByRole("button", { name: "Google Gemini" })).toBeTruthy();
    expect(screen.queryByText(/connects with account login/)).toBeNull();
  });
});
