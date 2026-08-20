import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * /you tells you when it could NOT load your profile (finding 19).
 *
 * `useApi` captures a failed GET instead of throwing, and the page only ever
 * read `loaded.data`. So a failed `GET /profile` (daemon restarting = status 0,
 * or a 5xx) produced the worst possible face: the spinner cleared, the EMPTY
 * form rendered over whatever is really saved, and — because the `dirty` memo
 * opens with `if (!preview) return false` and Save is `disabled={!dirty}` —
 * Save could NEVER be clicked no matter what was typed. A fully interactive
 * form whose only action is silently dead, and everything typed lost on
 * navigation.
 *
 * What these tests pin:
 *  - a status-0 load failure renders the house OfflineHint (the global
 *    DaemonBanner is transient and explains nothing about the dead Save; a
 *    network error never fires the global 5xx banner at all);
 *  - a 5xx load failure renders an ErrorNote naming the failure;
 *  - in BOTH cases the editable form is not on screen, so nothing the user
 *    types can be swallowed and no blank profile is shown over a real one;
 *  - Try again re-fetches and the real profile takes over;
 *  - the happy path is untouched: the form renders and typing arms Save.
 */

const hooks = vi.hoisted(() => ({
  calls: [] as string[],
  /** When non-null, GET /profile rejects with this status. */
  failStatus: null as number | null,
}));

class FakeApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

const PROFILE = {
  profile: {
    enabled: true,
    about: "I run a small CPA firm.",
    tone: "",
    writing_style: "",
    formatting: "",
    formatting_rules: "",
    reading_level: "",
    response_length: "",
    accessibility: "",
    language: "",
    enforce_language: true,
    voice_card: "",
    voice_source: "",
  },
  preview: "About you: I run a small CPA firm.",
  preview_chars: 33,
  preview_limit: 4000,
};

const OPTIONS = {
  tone: [{ key: "plain", label: "Plain" }],
  writing_style: [],
  formatting: [],
  reading_level: [],
  response_length: [],
  accessibility: [{ key: "dyslexia", label: "Dyslexia-friendly" }],
  language: [{ code: "en", label: "English" }],
};

vi.mock("@/lib/api", () => ({
  ApiError: FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => "",
  setIjToken: () => {},
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  wsUrl: (p: string) => `ws://127.0.0.1:8787${p}`,
  sseUrl: (p: string) => `http://127.0.0.1:8787${p}`,
  get: (path: string) => {
    hooks.calls.push(path);
    if (path === "/profile/options") return Promise.resolve(OPTIONS);
    if (path === "/profile") {
      if (hooks.failStatus !== null) {
        return Promise.reject(
          new FakeApiError(
            hooks.failStatus === 0 ? "network error" : "profile store unavailable",
            hooks.failStatus,
          ),
        );
      }
      return Promise.resolve(PROFILE);
    }
    return Promise.resolve({});
  },
  post: () => Promise.resolve(PROFILE),
  put: () => Promise.resolve(PROFILE),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
  api: () => Promise.resolve({}),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/you",
}));

// Imported AFTER the mocks so the page binds to the fakes. The whole PAGE is
// mounted, which is what makes "the hint is WIRED IN" testable at all.
const YouPage = (await import("@/app/you/page")).default;

const ABOUT_PLACEHOLDER = /I run a small CPA firm\./;

beforeEach(() => {
  hooks.calls = [];
  hooks.failStatus = null;
});

afterEach(() => cleanup());

describe("/you when the profile cannot be loaded", () => {
  it("renders the offline hint and NO editable form on a status-0 failure", async () => {
    hooks.failStatus = 0;
    render(<YouPage />);

    await waitFor(() => {
      expect(screen.getByText(/Daemon offline or unreachable\./)).toBeInTheDocument();
    });

    // The form is gone: nothing to type into, so nothing to lose — and no
    // blank profile shown over the one the daemon still holds.
    expect(screen.queryByPlaceholderText(ABOUT_PLACEHOLDER)).toBeNull();
    expect(screen.queryByRole("button", { name: /^Save/ })).toBeNull();
    // ...and the page does not claim an empty profile either.
    expect(screen.queryByText(/an empty profile adds nothing to the prompt/)).toBeNull();
  });

  it("names a 5xx failure in an error note and still hides the form", async () => {
    hooks.failStatus = 503;
    render(<YouPage />);

    await waitFor(() => {
      expect(screen.getByText(/profile store unavailable/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/Daemon offline or unreachable\./)).toBeNull();
    expect(screen.queryByPlaceholderText(ABOUT_PLACEHOLDER)).toBeNull();
    expect(screen.queryByRole("button", { name: /^Save/ })).toBeNull();
  });

  it("Try again re-fetches and the real profile takes over", async () => {
    hooks.failStatus = 0;
    render(<YouPage />);

    await waitFor(() => {
      expect(screen.getByText(/Daemon offline or unreachable\./)).toBeInTheDocument();
    });

    hooks.failStatus = null;
    fireEvent.click(screen.getByRole("button", { name: /Try again/ }));

    // Wait for the THING BEING ASSERTED: the saved value on screen.
    await waitFor(() => {
      expect(screen.getByDisplayValue("I run a small CPA firm.")).toBeInTheDocument();
    });
    expect(screen.queryByText(/Daemon offline or unreachable\./)).toBeNull();
  });
});

describe("/you when the profile loads", () => {
  it("renders the form and typing arms Save", async () => {
    render(<YouPage />);

    const about = await screen.findByPlaceholderText(ABOUT_PLACEHOLDER);
    const save = screen.getByRole("button", { name: /^Save/ }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    fireEvent.change(about, { target: { value: "I run a small CPA firm. And I ski." } });

    await waitFor(() => {
      expect(
        (screen.getByRole("button", { name: /^Save/ }) as HTMLButtonElement).disabled,
      ).toBe(false);
    });
  });
});
