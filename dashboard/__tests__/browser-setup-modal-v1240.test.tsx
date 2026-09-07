/**
 * The guided browser setup window (v1.240.0, Sidebar Ship 1).
 *
 * The user asked for "an HTML modal popup that cleanly and beautifuly explains
 * the bare minimum steps the user needs to take". "Bare minimum" is the load
 * bearing half of that sentence, and it is the half a regression eats first —
 * a wizard grows steps. So this file pins the shape of the thing rather than
 * its prose:
 *
 *  - IT REACHES NOTHING IT WAS NOT ASKED TO (F1). Opening the dialog arms a
 *    time-boxed window and NOTHING ELSE. It used to post
 *    `{access: "read_only"}`, which the daemon persists to config.toml through
 *    `PUT /settings`, so a user on Off who opened this dialog to READ what
 *    setup involves left with the browser capability switched on for good and
 *    Close did not revert it. The access level is now a visible control showing
 *    the value the daemon holds, written only on a click. Pinned three ways:
 *    the arm body carries no `access`, mount + close write no settings at all,
 *    and the selection follows the status rather than a constant.
 *
 *  - PAIRING IS A STEP, AND IT IS THE MAIN PATH (F2). Auto-pair is gone — it
 *    minted a real credential to any local process that forged an `Origin`
 *    header — so every user lands on "waiting to pair" every time. The dialog
 *    used to count `pending_pairing` as loaded, jump to "a new tab opens with
 *    one button", and leave the only action that mattered on the card BEHIND
 *    the full-screen modal. The Pair button, the identity beside it and the
 *    sentence saying why the press cannot be automated are asserted here, and
 *    they are reached by DRIVING THE CARD into that state rather than by
 *    constructing a modal around a fixture.
 *
 *  - NO AGENT-FACING STRING REACHES THE USER (F3). `/browser/request-host-permission`
 *    answers 409 with "…Ask the user to open the Browser page in Iron Jarvis
 *    and pair their browser, then retry." — copy written for a tool, telling
 *    the user who is standing on that page to go to that page. The dialog maps
 *    failures to human sentences, and the raw text is asserted ABSENT.
 *
 *  - A LAPSED WINDOW IS CALM, AND THE DIALOG STAYS USABLE (F4). Developer mode
 *    plus a file picker takes a first-timer longer than two minutes.
 *
 *  - IT NAMES THE REAL FOLDER, AND COPIES THAT EXACT STRING. Acceptance row 4
 *    of BROWSER-SIDEBAR-PLAN §7. The path is `status.addon_dir`, resolved by
 *    the daemon on THIS machine; what lands on the clipboard is asserted to be
 *    that same string, because a Copy button that copies something else is
 *    worse than no Copy button.
 *
 *  - IT ADVANCES ITSELF. No Next button anywhere: the steps are a function of
 *    the polled status, so the test moves the status and asserts the step moved.
 *
 *  - IT LEAVES THE CARD'S SUBTREE. `.card-surface` carries a `backdrop-filter`,
 *    which makes it the containing block for `fixed` descendants (v1.214.0), so
 *    the dialog must be a child of `document.body`.
 *
 *  - VOCABULARY. Nothing a user reads calls the add-on an "extension"; the one
 *    permitted literal is `chrome://extensions`, Chrome's name for its own
 *    page, which the modal quotes as an address.
 *
 * Every `waitFor` here reads the LAST thing its handler sets — the countdown
 * (written after the arm returned), the copied confirmation, the rendered
 * error, the re-enabled button — never the first observable side effect.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    puts: [] as { path: string; body: unknown }[],
    status: null as Record<string, unknown> | null,
    /** What POST /browser/setup/arm answers. */
    arm: { armed: true, expires_in_s: 120, addon_dir: "" } as Record<string, unknown>,
    /** Set to a message to make the arm fail. */
    armError: null as string | null,
    /** Per-path POST failures: path -> [message, status]. */
    postErrors: {} as Record<string, [string, number]>,
    /** Set to make PUT /settings fail. */
    putError: null as [string, number] | null,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  sseUrl: (p: string) => p,
  wsUrl: (p: string) => p,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: async (path: string) => {
    api.gets.push(path);
    if (path === "/browser/status") {
      if (api.status === null) throw new api.FakeApiError("Not Found", 404);
      return api.status;
    }
    return {};
  },
  post: async (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    const failure = api.postErrors[path];
    if (failure) throw new api.FakeApiError(failure[0], failure[1]);
    if (path === "/browser/setup/arm") {
      if (api.armError) throw new api.FakeApiError(api.armError, 404);
      return api.arm;
    }
    return {};
  },
  put: async (path: string, body?: unknown) => {
    api.puts.push({ path, body });
    if (api.putError) throw new api.FakeApiError(api.putError[0], api.putError[1]);
    return {};
  },
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/computeruse",
}));

const { BrowserSetupModal } = await import("@/components/browser/BrowserSetupModal");
const { YourBrowserCard } = await import("@/components/browser/YourBrowserCard");

/* -------------------------------------------------------------------------- */

type Status = Record<string, unknown>;

const base: Status = {
  connected: false,
  access: "read_only",
  host_permission: false,
  extension_id: "lgihfomaieifpnemakmpadmggjnoojmm",
  expected_extension_id: "lgihfomaieifpnemakmpadmggjnoojmm",
  active_tab: null,
  pending_pairing: null,
  paired: false,
  addon_dir: "C:\\Users\\vr\\AppData\\Local\\Programs\\Iron Jarvis\\resources\\browser-addon",
  last_error: null,
};

const REAL_DIR = base.addon_dir as string;

/** The one status per card state, matching browser-card-v1235's fixtures. */
const CARD_STATES: Record<string, Status> = {
  off: { ...base, access: "off" },
  not_connected: { ...base },
  waiting: {
    ...base,
    pending_pairing: {
      request_id: "pair_abc123",
      first_seen_at: "2026-09-06T12:00:00Z",
      extension_id: "lgihfomaieifpnemakmpadmggjnoojmm",
    },
  },
  paired_down: { ...base, paired: true },
  connected: {
    ...base,
    connected: true,
    paired: true,
    host_permission: true,
    active_tab: { id: 1, title: "Example", url: "https://example.com/" },
  },
};

/** Connected, paired, and blind — step 4's own state. */
const NO_SITE_ACCESS: Status = { ...base, connected: true, paired: true, host_permission: false };

let copiedText: string[] = [];

beforeEach(() => {
  api.gets.length = 0;
  api.posts.length = 0;
  api.puts.length = 0;
  api.status = { ...base };
  api.arm = { armed: true, expires_in_s: 120, addon_dir: "" };
  api.armError = null;
  api.postErrors = {};
  api.putError = null;
  copiedText = [];
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: async (t: string) => {
        copiedText.push(t);
      },
    },
  });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const postPaths = () => api.posts.map((p) => p.path);
const modalText = () => screen.getByTestId("browser-setup-modal").textContent ?? "";

/** Mount the modal and wait until the arm has landed — the countdown is the
 *  signal `arm()` writes LAST, so it is the only honest "ready" here. */
async function openModal(status: Status, onClose = () => {}) {
  const view = render(<BrowserSetupModal status={status as never} onClose={onClose} />);
  await screen.findByTestId("browser-setup-countdown");
  return view;
}

/** Open the dialog THE WAY A USER DOES: the real card, its real poll, its real
 *  button. Nothing here constructs the modal around a fixture, so the status
 *  the dialog reads is the one the card fetched. */
async function openFromCard(status: Status, ready = "browser-setup-countdown") {
  api.status = status;
  const view = render(<YourBrowserCard />);
  fireEvent.click(await screen.findByTestId("browser-setup-open"));
  // `ready` is the signal `arm()` writes LAST: the countdown for a window that
  // opened, the lapsed panel for one that came back with no time left.
  await screen.findByTestId(ready);
  return view;
}

/** Let the card's 5 s poll pick up a new status, with the dialog still open. */
async function pollWith(status: Status) {
  api.status = status;
  await act(async () => {
    vi.advanceTimersByTime(5000);
  });
}

/* -------------------------------------------------------------------------- */
/*  Reachability from the card                                                 */
/* -------------------------------------------------------------------------- */

describe("the guided window is reachable from the card (v1.240.0)", () => {
  for (const state of ["off", "not_connected", "paired_down"]) {
    it(`offers "Set up my browser" in the ${state} state`, async () => {
      api.status = CARD_STATES[state];
      render(<YourBrowserCard />);
      // WAIT FOR THE CARD TO BE IN THIS STATE FIRST. The first paint has no
      // status yet and `browserCardState(null)` is "off", so a bare
      // findByTestId resolves against the off card and would pass for every
      // fixture — a mutation removing paired_down from the condition left this
      // green until the badge was pinned. Assert the real thing.
      const badge = await screen.findByTestId("browser-badge");
      await waitFor(() => expect(badge.dataset.state).toBe(state));
      expect(screen.getByTestId("browser-setup-open")).toBeTruthy();
    });
  }

  for (const state of ["waiting", "connected"]) {
    it(`does not offer it in the ${state} state`, async () => {
      api.status = CARD_STATES[state];
      render(<YourBrowserCard />);
      // Wait for the card to have RESOLVED into this state before asserting an
      // absence — an absence read during the first paint proves nothing.
      const badge = await screen.findByTestId("browser-badge");
      await waitFor(() => expect(badge.dataset.state).toBe(state));
      expect(screen.queryByTestId("browser-setup-open")).toBeNull();
    });
  }

  it("pressing it opens the dialog and arms a window", async () => {
    await openFromCard(CARD_STATES.not_connected);
    expect(postPaths()).toContain("/browser/setup/arm");
  });
});

/* -------------------------------------------------------------------------- */
/*  F1 — the dialog switches nothing on                                        */
/* -------------------------------------------------------------------------- */

describe("opening the dialog changes no setting (F1)", () => {
  it("arms with no access value at all", async () => {
    await openFromCard(CARD_STATES.off);
    const armed = api.posts.find((p) => p.path === "/browser/setup/arm");
    expect(armed).toBeTruthy();
    // THE WHOLE FINDING IN ONE LINE. `{access: "read_only"}` here is a capability
    // the user never chose, persisted to config.toml by the route's own
    // `PUT /settings` call and NOT reverted by Close.
    expect(armed?.body).toEqual({});
    expect(Object.keys((armed?.body ?? {}) as object)).not.toContain("access");
  });

  it("opening and closing writes no settings — Off stays Off", async () => {
    await openFromCard(CARD_STATES.off);
    // The dialog reports the daemon's value, and the daemon's value is Off.
    expect(screen.getByTestId("browser-setup-access").dataset.selected).toBe("off");
    fireEvent.click(screen.getByTestId("browser-setup-close"));
    await waitFor(() => expect(postPaths()).toContain("/browser/setup/disarm"));
    expect(api.puts).toEqual([]);
    expect(api.status?.access).toBe("off");
  });

  it("shows the level the daemon holds, not a hardcoded one", async () => {
    await openModal({ ...base, access: "interactive" });
    expect(screen.getByTestId("browser-setup-access").dataset.selected).toBe("interactive");
    expect(
      screen.getByTestId("browser-setup-access-interactive").getAttribute("aria-pressed"),
    ).toBe("true");
    expect(screen.getByTestId("browser-setup-access-read_only").getAttribute("aria-pressed")).toBe(
      "false",
    );
    expect(screen.queryByTestId("browser-setup-access-off-note")).toBeNull();
  });

  it("says out loud when the capability is Off, instead of quietly fixing it", async () => {
    await openModal(CARD_STATES.off);
    const note = screen.getByTestId("browser-setup-access-off-note");
    expect(note.textContent).toContain("currently Off");
    expect(api.puts).toEqual([]);
  });

  it("writes the level only on a click, through PUT /settings", async () => {
    await openFromCard(CARD_STATES.off);
    expect(api.puts).toEqual([]);
    fireEvent.click(screen.getByTestId("browser-setup-access-read_only"));
    await waitFor(() =>
      expect(api.puts).toEqual([{ path: "/settings", body: { values: { browser_access: "read_only" } } }]),
    );
  });

  it("a refused level is reported in the dialog's own words", async () => {
    api.putError = ["browser_access: not a valid value", 400];
    await openModal(CARD_STATES.off);
    fireEvent.click(screen.getByTestId("browser-setup-access-interactive"));
    await waitFor(() =>
      expect(modalText()).toContain("would not accept that access level"),
    );
    expect(modalText()).not.toContain("not a valid value");
  });
});

/* -------------------------------------------------------------------------- */
/*  Arming and disarming                                                       */
/* -------------------------------------------------------------------------- */

describe("the setup window is armed on open and given back on close", () => {
  it("closing posts disarm and tells the card", async () => {
    const onClose = vi.fn();
    await openModal(base, onClose);
    fireEvent.click(screen.getByTestId("browser-setup-close"));
    await waitFor(() => expect(postPaths()).toContain("/browser/setup/disarm"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Escape also disarms — the window must not outlive the dialog", async () => {
    const onClose = vi.fn();
    await openModal(base, onClose);
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(postPaths()).toContain("/browser/setup/disarm"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("a daemon without the route says so in human words, not in HTTP's", async () => {
    api.armError = "Not Found";
    render(<BrowserSetupModal status={base as never} onClose={() => {}} />);
    expect(await screen.findByText(/older than the guided setup/)).toBeTruthy();
    expect(modalText()).not.toContain("Not Found");
    // And it does not claim an open window — but the steps are still there.
    expect(screen.queryByTestId("browser-setup-window")).toBeNull();
    expect(screen.getByTestId("browser-setup-lapsed")).toBeTruthy();
    expect(screen.getByTestId("browser-setup-open-1")).toBeTruthy();
  });
});

/* -------------------------------------------------------------------------- */
/*  The folder                                                                 */
/* -------------------------------------------------------------------------- */

describe("step 1 names the real folder for this machine (acceptance row 4)", () => {
  it("prints status.addon_dir verbatim", async () => {
    await openModal(base);
    expect(screen.getByTestId("browser-setup-folder-value").textContent).toBe(REAL_DIR);
  });

  it("copies that exact string, and says so", async () => {
    await openModal(base);
    fireEvent.click(screen.getByTestId("browser-setup-folder-copy"));
    // The collapsed step is written after the clipboard call resolved — it is
    // the LAST thing the handler produces, and it is the confirmation the user
    // is left looking at once the folder makes way for step 2.
    await waitFor(() =>
      expect(screen.getByTestId("browser-setup-done-1").textContent).toContain(
        "copied to your clipboard",
      ),
    );
    expect(copiedText).toEqual([REAL_DIR]);
  });

  it("a clipboard that refuses keeps the folder on screen and says nothing took it", async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    await openModal(base);
    fireEvent.click(screen.getByTestId("browser-setup-folder-copy"));
    await waitFor(() =>
      expect(screen.getByTestId("browser-setup-folder-copied").textContent).toContain(
        "Nothing here can copy",
      ),
    );
    // The step must NOT advance: the path is not on any clipboard, so taking it
    // off the screen would strand the user.
    expect(screen.getByTestId("browser-setup-step-1").dataset.done).toBe("false");
    expect(screen.getByTestId("browser-setup-folder-value").textContent).toBe(REAL_DIR);
  });

  it("copying advances step 1 without touching the daemon", async () => {
    await openModal(base);
    expect(screen.getByTestId("browser-setup-step-1").dataset.done).toBe("false");
    fireEvent.click(screen.getByTestId("browser-setup-folder-copy"));
    await waitFor(() =>
      expect(screen.getByTestId("browser-setup-step-1").dataset.done).toBe("true"),
    );
    expect(screen.getByTestId("browser-setup-open-2")).toBeTruthy();
    expect(postPaths()).toEqual(["/browser/setup/arm"]);
    expect(api.puts).toEqual([]);
  });

  it("an unresolved folder is said out loud, not printed blank", async () => {
    await openModal({ ...base, addon_dir: "" });
    expect(screen.queryByTestId("browser-setup-folder-value")).toBeNull();
    const miss = screen.getByTestId("browser-setup-folder-missing");
    expect(miss.textContent).toContain("could not find the add-on folder");
  });

  it("falls back to the folder the arm reported when status has none", async () => {
    api.arm = { armed: true, expires_in_s: 120, addon_dir: "/opt/iron-jarvis/browser-addon" };
    await openModal({ ...base, addon_dir: "" });
    expect(screen.getByTestId("browser-setup-folder-value").textContent).toBe(
      "/opt/iron-jarvis/browser-addon",
    );
  });
});

/* -------------------------------------------------------------------------- */
/*  F2 — pairing is a step of its own, and it is the main path                 */
/* -------------------------------------------------------------------------- */

describe("a browser waiting to pair is offered Pair, right here (F2)", () => {
  it("the dialog follows the card into the waiting state and shows step 3", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await openFromCard(CARD_STATES.not_connected);
    await pollWith(CARD_STATES.waiting);

    // The add-on has reached the daemon, so step 2 is done — but NOTHING is
    // paired, and the old dialog called this "loaded" and skipped to the tab
    // that never opened.
    const open3 = await screen.findByTestId("browser-setup-open-3");
    expect(open3).toBeTruthy();
    expect(screen.getByTestId("browser-setup-step-2").dataset.done).toBe("true");
    expect(screen.getByTestId("browser-setup-step-3").dataset.done).toBe("false");
    expect(screen.getByTestId("browser-setup-step-3").dataset.active).toBe("true");
    expect(screen.queryByTestId("browser-setup-open-4")).toBeNull();
    // The precise lie that was on screen before.
    expect(modalText()).not.toContain("A new tab opens");
  });

  it("Pair posts the pending request id, from inside the dialog", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await openFromCard(CARD_STATES.not_connected);
    await pollWith(CARD_STATES.waiting);

    const button = await screen.findByTestId("browser-setup-pair");
    fireEvent.click(button);
    // The re-enabled button is written in `finally`, LAST — so waiting on it
    // means the whole handler ran, not just that a fetch was recorded.
    await waitFor(() => {
      expect(api.posts.find((p) => p.path === "/browser/pair")?.body).toEqual({
        request_id: "pair_abc123",
      });
      expect(button.hasAttribute("disabled")).toBe(false);
    });
  });

  it("shows the identity being approved, exactly as the card does", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await openFromCard(CARD_STATES.not_connected);
    await pollWith(CARD_STATES.waiting);

    const identity = await screen.findByTestId("browser-setup-pair-identity");
    expect(identity.textContent).toContain("Identified as the Iron Jarvis browser add-on");
    expect(identity.textContent).toContain("lgihfomaieifpnemakmpadmggjnoojmm");
  });

  it("names an unrecognised caller as one, and does not dress it up", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await openFromCard(CARD_STATES.not_connected);
    await pollWith({
      ...base,
      pending_pairing: {
        request_id: "pair_evil",
        first_seen_at: "2026-09-06T12:00:00Z",
        extension_id: "aaaabbbbccccddddeeeeffffgggghhhh",
      },
    });
    const identity = await screen.findByTestId("browser-setup-pair-identity");
    expect(identity.textContent).toContain("Unrecognised caller");
    expect(identity.textContent).toContain("aaaabbbbccccddddeeeeffffgggghhhh");
  });

  it("says plainly why this press cannot be automated", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await openFromCard(CARD_STATES.not_connected);
    await pollWith(CARD_STATES.waiting);

    const why = await screen.findByTestId("browser-setup-why-pair");
    const text = why.textContent ?? "";
    // The honest reason, in user language: a name over a local socket is text
    // any program can type, so a person has to confirm it is theirs.
    expect(text).toContain("Anything running on this computer");
    expect(text).toMatch(/just text it typed/);
    expect(text).toContain("that is mine");
    // Not jargon.
    expect(text.toLowerCase()).not.toContain("origin header");
    expect(text.toLowerCase()).not.toContain("websocket");
  });

  it("a pairing that no longer exists is explained, not dumped", async () => {
    api.postErrors["/browser/pair"] = ["no such pending pairing request", 404];
    await openModal(CARD_STATES.waiting);
    fireEvent.click(screen.getByTestId("browser-setup-pair"));
    await waitFor(() => expect(modalText()).toContain("no longer waiting"));
    expect(modalText()).not.toContain("no such pending pairing request");
  });

  it("once paired, step 3 collapses to a check and step 4 opens", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await openFromCard(CARD_STATES.not_connected);
    await pollWith(CARD_STATES.waiting);
    await screen.findByTestId("browser-setup-open-3");
    await pollWith(NO_SITE_ACCESS);

    await waitFor(() =>
      expect(screen.getByTestId("browser-setup-step-3").dataset.done).toBe("true"),
    );
    expect(screen.getByTestId("browser-setup-done-3").textContent).toContain("Paired");
    expect(screen.queryByTestId("browser-setup-open-3")).toBeNull();
    expect(screen.getByTestId("browser-setup-open-4")).toBeTruthy();
  });
});

/* -------------------------------------------------------------------------- */
/*  Self-advancing steps                                                       */
/* -------------------------------------------------------------------------- */

describe("the modal advances itself off the polled status", () => {
  it("a connected add-on finishes steps 1-3 and opens step 4", async () => {
    const { rerender } = await openModal(base);
    expect(screen.getByTestId("browser-setup-open-1")).toBeTruthy();

    rerender(<BrowserSetupModal status={NO_SITE_ACCESS as never} onClose={() => {}} />);
    await waitFor(() =>
      expect(screen.getByTestId("browser-setup-step-2").dataset.done).toBe("true"),
    );
    expect(screen.getByTestId("browser-setup-step-1").dataset.done).toBe("true");
    expect(screen.getByTestId("browser-setup-step-3").dataset.done).toBe("true");
    expect(screen.getByTestId("browser-setup-step-4").dataset.done).toBe("false");
    // Exactly one open instruction, and the finished ones have collapsed.
    expect(screen.queryByTestId("browser-setup-open-1")).toBeNull();
    expect(screen.queryByTestId("browser-setup-open-2")).toBeNull();
    expect(screen.queryByTestId("browser-setup-open-3")).toBeNull();
    expect(screen.getByTestId("browser-setup-open-4")).toBeTruthy();
    expect(screen.getByTestId("browser-setup-done-1")).toBeTruthy();
  });

  it("a pending pairing counts as loaded, and only as loaded", async () => {
    await openModal(CARD_STATES.waiting);
    expect(screen.getByTestId("browser-setup-step-2").dataset.done).toBe("true");
    expect(screen.getByTestId("browser-setup-step-3").dataset.done).toBe("false");
  });

  it("a paired browser that is not running says so instead of asking a dead socket", async () => {
    await openModal(CARD_STATES.paired_down);
    expect(screen.getByTestId("browser-setup-open-4")).toBeTruthy();
    expect(screen.getByTestId("browser-setup-not-running").textContent).toContain(
      "not running right now",
    );
    expect(screen.queryByTestId("browser-setup-reopen-grant")).toBeNull();
  });

  it("step 4 can reopen the grant tab, and says it asked", async () => {
    await openModal(NO_SITE_ACCESS);
    fireEvent.click(screen.getByTestId("browser-setup-reopen-grant"));
    await waitFor(() => expect(screen.getByTestId("browser-setup-reopened")).toBeTruthy());
    expect(postPaths()).toContain("/browser/request-host-permission");
  });

  it("shows a success state and closes itself once site access lands", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const onClose = vi.fn();
    const { rerender } = render(
      <BrowserSetupModal status={base as never} onClose={onClose} />,
    );
    await screen.findByTestId("browser-setup-countdown");

    rerender(
      <BrowserSetupModal
        status={{ ...base, connected: true, paired: true, host_permission: true } as never}
        onClose={onClose}
      />,
    );
    expect(await screen.findByTestId("browser-setup-success")).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(2000);
    });
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(postPaths()).toContain("/browser/setup/disarm");
  });
});

/* -------------------------------------------------------------------------- */
/*  F3 — no agent-facing string reaches the user                               */
/* -------------------------------------------------------------------------- */

describe("failures are told in the user's words, never the tool's (F3)", () => {
  /** The daemon's real 409 body for this route, verbatim. */
  const TOOL_REMEDY =
    "the browser add-on is not connected. Ask the user to open the Browser page in Iron Jarvis and pair their browser, then retry.";

  it("a 409 from the grant nudge is rewritten for the person reading it", async () => {
    api.postErrors["/browser/request-host-permission"] = [TOOL_REMEDY, 409];
    await openModal(NO_SITE_ACCESS);
    fireEvent.click(screen.getByTestId("browser-setup-reopen-grant"));
    await waitFor(() => expect(modalText()).toContain("not connected to Iron Jarvis right now"));

    const text = modalText();
    // THE FINDING: this told the user, who is standing on the Browser page, to
    // go to the Browser page — in a voice written for an agent.
    expect(text).not.toContain(TOOL_REMEDY);
    expect(text).not.toContain("Ask the user");
    expect(text).not.toContain("then retry");
    // And it did not claim it asked.
    expect(screen.queryByTestId("browser-setup-reopened")).toBeNull();
  });

  it("a daemon that is simply not answering says that, and nothing else", async () => {
    api.postErrors["/browser/request-host-permission"] = ["Failed to fetch", 0];
    await openModal(NO_SITE_ACCESS);
    fireEvent.click(screen.getByTestId("browser-setup-reopen-grant"));
    await waitFor(() => expect(modalText()).toContain("did not answer"));
    expect(modalText()).not.toContain("Failed to fetch");
  });

  it("the component holds no raw-error renderer at all", () => {
    const src = readFileSync(
      join(process.cwd(), "components", "browser", "BrowserSetupModal.tsx"),
      "utf8",
    ).replace(/\r\n/g, "\n");
    // A SOURCE PIN, because the failure is a shape rather than a string: an
    // `errText(e)` helper is one careless edit away from being rendered again,
    // and every path through this dialog goes through `humanFailure`.
    expect(src).not.toContain("function errText");
    expect(src).not.toMatch(/set(ArmError|Failure)\(String\(/);
    expect(src).toContain("function humanFailure");
  });
});

/* -------------------------------------------------------------------------- */
/*  The window, as a clock — and F4, a lapse that is not a failure             */
/* -------------------------------------------------------------------------- */

describe("the countdown is the security bound, shown", () => {
  it("renders the daemon's remaining window as m:ss", async () => {
    api.arm = { armed: true, expires_in_s: 125, addon_dir: "" };
    await openModal(base);
    expect(screen.getByTestId("browser-setup-countdown").textContent).toBe("2:05");
  });

  // THE ONE TEST THAT LETS THE CLOCK DO IT. Nothing here ever writes `left`:
  // the window opens with a full two minutes and closes because the component's
  // own interval counted it down to zero, one second at a time.
  it("stops at zero and offers Re-arm, which arms again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // LONG window, closed ONLY by the advance below. A short one lapses on REAL
    // elapsed time under `shouldAdvanceTime`, which passes alone and fails in a
    // contended full-suite run.
    api.arm = { armed: true, expires_in_s: 120, addon_dir: "" };
    render(<BrowserSetupModal status={base as never} onClose={() => {}} />);
    await screen.findByTestId("browser-setup-countdown");

    // ONE SECOND AT A TIME, never a single 121 s jump.
    //
    // The countdown's interval is created by a PASSIVE EFFECT, and React can
    // flush that effect AFTER the commit that painted the countdown — so the
    // node `findByTestId` just resolved on is not proof the interval exists
    // yet. One big `advanceTimersByTime(121_000)` can therefore land entirely
    // before the interval is registered; it is then created at fake-time
    // +121 s with nothing left to advance it, and the window hangs at 2:00 for
    // the rest of the test. That is this file's full-suite failure: under load
    // the flush lands late far more often. Stepping with an `act` boundary each
    // second reaches the interval whenever it lands, so what the test depends
    // on is the component's clock, not React's scheduling.
    const seen: string[] = [];
    for (let i = 0; i < 300; i++) {
      const shown = screen.queryByTestId("browser-setup-countdown");
      if (!shown) break;
      seen.push(shown.textContent ?? "");
      await act(async () => {
        vi.advanceTimersByTime(1000);
      });
    }
    // It COUNTED, rather than jumping from 2:00 to gone. Distinct values rather
    // than "the second frame differs from the first": the interval may not
    // exist yet on the first step or two (see above), and no assertion here may
    // turn on which step it starts on.
    expect(seen[0]).toBe("2:00");
    expect(new Set(seen).size).toBeGreaterThan(20);

    // Fake time has done its one job (shutting the window). Give the real clock
    // back BEFORE any findBy/waitFor: their polling runs on the installed timers,
    // and under fake ones the wait is decided by machine load, not by the app.
    vi.useRealTimers();
    const rearm = await screen.findByTestId("browser-setup-rearm");
    expect(screen.queryByTestId("browser-setup-countdown")).toBeNull();

    api.arm = { armed: true, expires_in_s: 120, addon_dir: "" };
    fireEvent.click(rearm);
    await screen.findByTestId("browser-setup-countdown");
    expect(postPaths().filter((p) => p === "/browser/setup/arm")).toHaveLength(2);
  });

  // NO CLOCK HERE. The behaviour under test is the LAPSED STATE, which the
  // test above already proves the countdown reaches on its own. A window the
  // daemon answers with no time left lands in exactly that state, so this one
  // is seeded there and runs entirely on real timers — nothing it asserts can
  // then turn on how fast the machine is.
  it("a lapsed window is calm, and leaves every step working (F4)", async () => {
    api.arm = { armed: true, expires_in_s: 0, addon_dir: "" };
    await openFromCard(CARD_STATES.not_connected, "browser-setup-lapsed");

    const lapsed = screen.getByTestId("browser-setup-lapsed");
    // NOT AN ALARM. Developer mode plus a file picker takes longer than two
    // minutes for a first-timer, and the amber "the setup window has closed"
    // panel read like a failed install.
    expect(lapsed.className).not.toContain("amber");
    expect(lapsed.textContent).toContain("every step below still works");
    expect(screen.queryByTestId("browser-setup-countdown")).toBeNull();

    // And it is TRUE: the folder is still there, and copying it still advances.
    expect(screen.getByTestId("browser-setup-folder-value").textContent).toBe(REAL_DIR);
    fireEvent.click(screen.getByTestId("browser-setup-folder-copy"));
    await waitFor(() =>
      expect(screen.getByTestId("browser-setup-step-1").dataset.done).toBe("true"),
    );
    expect(screen.getByTestId("browser-setup-open-2")).toBeTruthy();
  });

  // Same seeding as F4, and the fake clock is here for ONE job only: driving
  // the card's own 5 s poll from "not connected" to "waiting to pair". The
  // window is already shut before any of it, so nothing about Pair depends on
  // a countdown reaching zero within a deadline.
  it("Pair still works after the window lapsed — it never needed it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.arm = { armed: true, expires_in_s: 0, addon_dir: "" };
    await openFromCard(CARD_STATES.not_connected, "browser-setup-lapsed");
    await pollWith(CARD_STATES.waiting);
    // Handed back only HERE: `pollWith` above still has to advance the fake
    // clock, and everything below this line waits on the DOM instead.
    vi.useRealTimers();

    const button = await screen.findByTestId("browser-setup-pair");
    expect(screen.getByTestId("browser-setup-lapsed")).toBeTruthy();
    expect(button.hasAttribute("disabled")).toBe(false);
    fireEvent.click(button);
    await waitFor(() => {
      expect(api.posts.find((p) => p.path === "/browser/pair")?.body).toEqual({
        request_id: "pair_abc123",
      });
      expect(button.hasAttribute("disabled")).toBe(false);
    });
  });

  it("an armed:false answer is treated as no window at all", async () => {
    api.arm = { armed: false, expires_in_s: 120, addon_dir: "" };
    render(<BrowserSetupModal status={base as never} onClose={() => {}} />);
    expect(await screen.findByTestId("browser-setup-lapsed")).toBeTruthy();
    expect(screen.queryByTestId("browser-setup-countdown")).toBeNull();
  });
});

/* -------------------------------------------------------------------------- */
/*  The portal, and the words                                                  */
/* -------------------------------------------------------------------------- */

describe("the dialog is attached to the page, not to the card", () => {
  it("renders through the shared Modal portal into document.body", async () => {
    api.status = CARD_STATES.not_connected;
    const { container } = render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-setup-open"));
    await screen.findByTestId("browser-setup-countdown");

    const overlay = screen.getByTestId("browser-setup-modal");
    expect(overlay.parentElement).toBe(document.body);
    expect(container.contains(overlay)).toBe(false);
    const dialog = document.querySelector('[role="dialog"][aria-modal="true"]');
    expect(dialog).toBeTruthy();
    expect(dialog?.getAttribute("aria-label")).toBe("Set up your browser");
  });

  it("uses the shared primitive rather than a hand-rolled overlay", () => {
    const src = readFileSync(
      join(process.cwd(), "components", "browser", "BrowserSetupModal.tsx"),
      "utf8",
    ).replace(/\r\n/g, "\n");
    expect(src).toContain('import { Modal } from "@/components/Modal";');
    expect(src).not.toContain("createPortal");
    expect(src).not.toContain("fixed inset-0");
  });
});

describe("nothing a user reads calls the add-on an extension", () => {
  const CHROME_PAGE = "chrome://extensions";
  const CHECKOUT_FOLDER = "extensions/chrome";

  const scrub = (t: string) =>
    t.split(CHROME_PAGE).join(" ").split(CHECKOUT_FOLDER).join(" ");

  it("across every step of the dialog, steps 2 and 3 included", async () => {
    // STEP 2 HAS TO BE DRIVEN TO, not merely listed. It is the step that quotes
    // `chrome://extensions`, so it is the one most likely to slip into calling
    // the add-on an extension — and a first cut of this test scanned three
    // statuses that all landed on step 1 or the last step, so a mutation
    // renaming it in step 2 stayed green. The copy is what advances step 1.
    // STEP 3 is here for the same reason: it prints a Chrome extension id.
    const cases: { status: Status; copyFirst?: boolean; open: string }[] = [
      { status: base, open: "browser-setup-open-1" },
      { status: { ...base, addon_dir: "" }, open: "browser-setup-open-1" },
      { status: base, copyFirst: true, open: "browser-setup-open-2" },
      { status: CARD_STATES.waiting, open: "browser-setup-open-3" },
      { status: NO_SITE_ACCESS, open: "browser-setup-open-4" },
      { status: CARD_STATES.paired_down, open: "browser-setup-open-4" },
    ];
    for (const c of cases) {
      const { unmount } = await openModal(c.status);
      if (c.copyFirst) fireEvent.click(screen.getByTestId("browser-setup-folder-copy"));
      await screen.findByTestId(c.open);
      const text = scrub(modalText());
      expect(text.toLowerCase(), `on ${c.open}`).not.toContain("extension");
      expect(text, `on ${c.open}`).toContain("add-on");
      unmount();
    }
  });

  it("and step 2 says plainly why those two clicks are the user's", async () => {
    await openModal(base);
    fireEvent.click(screen.getByTestId("browser-setup-folder-copy"));
    const why = await screen.findByTestId("browser-setup-why-manual");
    expect(why.textContent).toContain("cannot do these two clicks for you");
    expect(why.textContent).toContain("Web Store");
  });
});
