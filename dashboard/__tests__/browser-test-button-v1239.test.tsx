/**
 * The Test readout and the add-on folder (v1.239.0, Browser Ship 5).
 *
 * Ship 5 adds no capability to this card. It asks whether the two things the card
 * SAYS are true, because both were written before the thing they describe existed:
 *
 *  - THE FOLDER. Ship 1 could only name `extensions/chrome`, a path that exists in
 *    a source checkout and nowhere else, and it closed with "the installer ships
 *    the add-on inside the app from v1.239.0" — a sentence that becomes an
 *    instruction to go and clone a repository the moment a user reads it ON
 *    v1.239.0. The installer now carries the built add-on, so the packaged folder
 *    is named FIRST, it is copyable (the next thing that happens to it is a paste
 *    into Chrome's file picker), and the copy REPORTS ITS OWN FAILURE rather than
 *    doing nothing quietly — the v1.161.0 DraftCard rule: never claim a copy that
 *    did not happen.
 *
 *    AND IT IS AN ABSOLUTE PATH, not a folder name. The first cut of this ship
 *    printed the bare string `browser-addon` and told the user that "the setup
 *    checks on the Overview print the full path" — a page that renders nothing
 *    while the doctor is healthy, and `browser_addon` is RECOMMENDED so a missing
 *    add-on does not make it unhealthy either. Two shipped guides said the card
 *    "names the exact folder on this machine". `GET /browser/status` now carries
 *    `addon_dir`, resolved by the daemon's own `doctor.browser_addon_dir()`, and
 *    the cases below drive the card off THAT FIELD in both of its states: a path
 *    is printed and copied verbatim, and an empty one produces the honest block
 *    rather than a name Chrome's file picker cannot resolve. The route half — that
 *    a real daemon puts a real folder in that field — is driven over the real app
 *    in `tests/test_browser_addon_path_v1239.py`; neither half is evidence alone.
 *
 *  - THE READOUT. `POST /browser/test` is the one button a worried user presses,
 *    so what it prints is a diagnosis. The failure half must carry as much as the
 *    success half: the daemon's own sentence AND the elapsed figure, because a
 *    refusal that came back in 0 ms and one that spent fifteen seconds waiting are
 *    different faults wearing the same words. So is the daemon's BROWSER_* code:
 *    it is RENDERED, in its own chip, because two refusals can carry identical
 *    prose and the code is the half a user can paste into a bug report.
 *
 * NOTHING HERE ASSERTS A DURATION. Every elapsed figure asserted below is the
 * number the mocked daemon returned, so the assertion measures no hardware — and
 * one case deliberately returns an absurd value to prove the card prints what it
 * is given rather than gating on it. A threshold on this number would be a test of
 * the CI runner (the v1.239.0 house rule, and the reason `round_trip_ms` is
 * documented as reported and never asserted).
 *
 * AND TEST DOES NOT MUTATE (D25, "Do not make Test mutate the page"). The card's
 * half of that is asserted here by counting every write the component makes: one
 * POST to /browser/test and nothing else, no PUT, no PATCH, no DELETE. The
 * daemon's half — that the method it sends is in READ_METHODS — is pinned in
 * Python, where the frame is built.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
    posts: [] as { path: string; body: unknown }[],
    puts: [] as { path: string; body: unknown }[],
    patches: [] as string[],
    dels: [] as string[],
    status: null as Record<string, unknown> | null,
    test: {} as Record<string, unknown>,
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
    if (path === "/browser/status") {
      if (api.status === null) throw new api.FakeApiError("Not Found", 404);
      return api.status;
    }
    return {};
  },
  post: async (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    if (path === "/browser/test") return api.test;
    return {};
  },
  put: async (path: string, body?: unknown) => {
    api.puts.push({ path, body });
    return {};
  },
  patch: async (path: string) => {
    api.patches.push(path);
    return {};
  },
  del: async (path: string) => {
    api.dels.push(path);
    return {};
  },
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));

const { YourBrowserCard } = await import("@/components/browser/YourBrowserCard");

/* -------------------------------------------------------------------------- */
/*  Fixtures                                                                    */
/* -------------------------------------------------------------------------- */

type Status = Record<string, unknown>;

/** The absolute folder a real daemon reports in `addon_dir`. Shaped like the
 *  packaged answer (`<install>/resources/browser-addon`) because that is the
 *  reader this card was rewritten for, and written out here rather than built
 *  from the folder name so that a card which quietly fell back to printing the
 *  NAME could not satisfy these assertions. */
const ADDON_DIR = "C:\\Users\\dana\\AppData\\Local\\Programs\\Iron Jarvis\\resources\\browser-addon";

const base: Status = {
  connected: false,
  access: "interactive",
  host_permission: false,
  extension_id: "",
  expected_extension_id: "lgihfomaieifpnemakmpadmggjnoojmm",
  addon_dir: ADDON_DIR,
  active_tab: null,
  pending_pairing: null,
  paired: false,
  last_error: null,
};

const connected: Status = {
  ...base,
  connected: true,
  paired: true,
  host_permission: true,
  active_tab: { id: 7, title: "Example Domain", url: "https://example.com/" },
};

/** The folder names the card must print. Written out rather than imported from
 *  the component, so a rename shows up here as a failing assertion instead of
 *  travelling silently into the test that is supposed to catch it.
 *
 *  `PACKAGED_FOLDER` is the name the card may only ever say in PROSE, in the
 *  fallback sentence. If it is ever what the copy button hands over again, the
 *  "not a bare name" assertions below go red. */
const PACKAGED_FOLDER = "browser-addon";
const CHECKOUT_FOLDER = "extensions/chrome";

/** Install a clipboard, or none at all. jsdom ships no `navigator.clipboard`, so
 *  the "nothing can copy" case is the DEFAULT here and has to be created for the
 *  happy path — the reverse of the browser. */
function setClipboard(writeText: ((text: string) => Promise<void>) | null) {
  if (writeText === null) {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    return;
  }
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
}

beforeEach(() => {
  api.posts.length = 0;
  api.puts.length = 0;
  api.patches.length = 0;
  api.dels.length = 0;
  api.status = { ...base };
  api.test = {
    ok: true,
    detail: "Round-trip OK — active tab received.",
    round_trip_ms: 42,
    active_tab: { id: 7, title: "Example Domain", url: "https://example.com/" },
  };
  setClipboard(null);
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
});
afterEach(() => cleanup());

const postPaths = () => api.posts.map((p) => p.path);

/* -------------------------------------------------------------------------- */
/*  The add-on folder                                                           */
/* -------------------------------------------------------------------------- */

describe("the card names the add-on folder a user can actually reach (v1.239.0)", () => {
  it("prints the ABSOLUTE folder the daemon reported, not the folder's name", async () => {
    // The S1 of this ship: a file picker cannot resolve "browser-addon". What the
    // card shows is whatever `addon_dir` carried, character for character.
    render(<YourBrowserCard />);
    const block = await screen.findByTestId("browser-addon-folder");

    expect(screen.getByTestId("browser-addon-packaged-value").textContent).toBe(ADDON_DIR);
    expect(screen.getByTestId("browser-addon-packaged-value").textContent).not.toBe(
      PACKAGED_FOLDER,
    );
    expect(block.textContent).toContain("Iron Jarvis install");
    // The retired fallback: a page that prints nothing while the doctor is happy.
    expect(block.textContent).not.toContain("Overview");
    // And it must not send a packaged user to a build they cannot run: the build
    // step is still there, scoped to a checkout, which the next case pins.
    expect(block.textContent).not.toContain("pnpm");
  });

  it("prints whatever path it is handed — nothing here is built from a constant", async () => {
    // A card that assembled the path itself would pass the case above on a
    // machine-shaped fixture and be wrong on every other install. This drives a
    // second, differently-shaped daemon answer through the same field.
    const posix = "/opt/Iron Jarvis/resources/browser-addon";
    api.status = { ...base, addon_dir: posix };
    render(<YourBrowserCard />);
    await screen.findByTestId("browser-addon-folder");
    expect(screen.getByTestId("browser-addon-packaged-value").textContent).toBe(posix);
  });

  it("says it could not find the folder when the daemon resolved none", async () => {
    // The honest fallback. No path, no folder name standing in for one, and the
    // remedy is the doctor's own — never a page that does not print the path.
    api.status = { ...base, addon_dir: "" };
    render(<YourBrowserCard />);
    const block = await screen.findByTestId("browser-addon-folder");

    const said = await screen.findByTestId("browser-addon-unresolved");
    expect(said.textContent).toContain("could not find");
    expect(said.textContent?.toLowerCase()).toContain("reinstall");
    expect(block.textContent).not.toContain("Overview");
    // Nothing offers a path to copy, because there is none to copy.
    expect(screen.queryByTestId("browser-addon-packaged-value")).toBeNull();
    expect(screen.queryByTestId("browser-addon-packaged-copy")).toBeNull();
    // The checkout line survives: it is the remaining way in.
    expect(screen.getByTestId("browser-addon-checkout-value").textContent).toBe(CHECKOUT_FOLDER);
  });

  it("treats a daemon that sends no field at all the same way", async () => {
    // A daemon older than v1.239.0 answers without `addon_dir`. That is "I do not
    // know", not "here is a folder name".
    const { addon_dir: _drop, ...older } = base as Record<string, unknown>;
    api.status = older;
    render(<YourBrowserCard />);
    await screen.findByTestId("browser-addon-folder");
    expect(await screen.findByTestId("browser-addon-unresolved")).toBeTruthy();
    expect(screen.queryByTestId("browser-addon-packaged-value")).toBeNull();
  });

  it("still names the checkout folder, for the reader who has one", async () => {
    render(<YourBrowserCard />);
    await screen.findByTestId("browser-addon-folder");
    expect(screen.getByTestId("browser-addon-checkout-value").textContent).toBe(CHECKOUT_FOLDER);
  });

  it("copies the absolute path — the thing that gets pasted into Chrome", async () => {
    const written: string[] = [];
    setClipboard(async (text: string) => {
      written.push(text);
    });
    render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-addon-packaged-copy"));

    await screen.findByTestId("browser-addon-packaged-copied");
    expect(written).toEqual([ADDON_DIR]);
    expect(screen.getByTestId("browser-addon-packaged-copied").textContent).toContain("Copied");
  });

  it("prefers the desktop bridge when the app is running inside Electron", async () => {
    // navigator.clipboard is permission-gated in Electron, which is why the
    // bridge exists at all. A Copy that skipped it would be a Copy that works in
    // a browser tab and silently fails in the app the user actually runs.
    const viaBridge: string[] = [];
    (window as unknown as { ironjarvis?: unknown }).ironjarvis = {
      clipboardWriteText: async (text: string) => {
        viaBridge.push(text);
      },
    };
    setClipboard(async () => {
      throw new Error("the browser clipboard must not have been reached");
    });
    render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-addon-checkout-copy"));

    await screen.findByTestId("browser-addon-checkout-copied");
    expect(viaBridge).toEqual([CHECKOUT_FOLDER]);
  });

  it("says so when nothing can copy, and leaves the name readable", async () => {
    // The DraftCard rule (v1.161.0): a degraded copy SAYS it degraded. A button
    // that reports success it did not have is worse than one that reports none.
    render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-addon-packaged-copy"));

    const said = await screen.findByTestId("browser-addon-packaged-copied");
    expect(said.textContent).not.toContain("Copied");
    expect(said.textContent?.toLowerCase()).toContain("copy");
    expect(screen.getByTestId("browser-addon-packaged-value").textContent).toBe(ADDON_DIR);
  });

  it("is shown to a browser that is paired but not running, too", async () => {
    // "Paired — not running" is where a user lands when the add-on was removed or
    // the browser was reinstalled. Naming the folder only in the never-connected
    // state would hide it from exactly the person reloading it.
    api.status = { ...base, paired: true };
    render(<YourBrowserCard />);
    expect(await screen.findByTestId("browser-addon-folder")).toBeTruthy();
  });

  it("is not repeated at a browser that is already connected", async () => {
    api.status = connected;
    render(<YourBrowserCard />);
    await screen.findByTestId("browser-test");
    expect(screen.queryByTestId("browser-addon-folder")).toBeNull();
  });
});

/* -------------------------------------------------------------------------- */
/*  The Test readout                                                            */
/* -------------------------------------------------------------------------- */

describe("Test reports what the daemon said, success and failure alike (v1.239.0)", () => {
  it("renders the success detail, the tab and the elapsed figure", async () => {
    api.status = connected;
    render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-test"));

    const readout = await screen.findByTestId("browser-test-result");
    expect(readout.dataset.ok).toBe("true");
    expect(screen.getByTestId("browser-test-detail").textContent).toBe(
      "Round-trip OK — active tab received.",
    );
    expect(readout.textContent).toContain("Example Domain");
    expect(readout.textContent).toContain("https://example.com/");
    // The number the mock returned. Nothing here times anything.
    expect(screen.getByTestId("browser-test-elapsed").textContent).toBe("42 ms");
  });

  it("renders the failure detail AND its elapsed figure, as an alert", async () => {
    api.status = connected;
    api.test = {
      ok: false,
      detail: "ACTION_TIMEOUT: your browser did not answer in time.",
      round_trip_ms: 15000,
      active_tab: null,
    };
    render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-test"));

    const readout = await screen.findByTestId("browser-test-result");
    expect(readout.dataset.ok).toBe("false");
    expect(screen.getByTestId("browser-test-detail").textContent).toContain("ACTION_TIMEOUT");
    // The elapsed figure is the whole diagnosis here: this failure waited out the
    // bound, and an instant refusal wears the same sentence with a different
    // number. Dropping it from the failure half loses the difference.
    expect(screen.getByTestId("browser-test-elapsed").textContent).toBe("15000 ms");
    expect(readout.querySelector('[role="alert"]')).not.toBeNull();
  });

  it("prints whatever figure it is given — nothing is gated on the number", async () => {
    // The pin for "displayed, never asserted": if any threshold, clamp or
    // "too slow" branch is ever added, the card stops printing what the daemon
    // measured and this goes red.
    api.status = connected;
    api.test = { ok: true, detail: "Round-trip OK.", round_trip_ms: 987654, active_tab: null };
    render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-test"));

    await screen.findByTestId("browser-test-result");
    expect(screen.getByTestId("browser-test-elapsed").textContent).toBe("987654 ms");
  });

  it("shows the BROWSER_* code, so two refusals with one sentence differ", async () => {
    // `BrowserTestResult.code` was added in this ship with a comment saying the
    // card could tell three failures apart by it. Nothing read it. These two
    // answers carry IDENTICAL prose, so the chip is the only thing between them.
    api.status = connected;
    api.test = {
      ok: false,
      detail: "Your browser did not answer.",
      code: "ACTION_TIMEOUT",
      round_trip_ms: 15000,
      active_tab: null,
    };
    const first = render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-test"));
    await screen.findByTestId("browser-test-result");
    expect(screen.getByTestId("browser-test-code").textContent).toBe("ACTION_TIMEOUT");
    first.unmount();
    cleanup();

    api.test = {
      ok: false,
      detail: "Your browser did not answer.",
      code: "BROWSER_NOT_CONNECTED",
      round_trip_ms: 15000,
      active_tab: null,
    };
    render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-test"));
    await screen.findByTestId("browser-test-result");
    expect(screen.getByTestId("browser-test-code").textContent).toBe("BROWSER_NOT_CONNECTED");
  });

  it("shows no chip when the daemon sent no code", async () => {
    // Success carries none, and neither does a daemon older than this ship. An
    // empty chip would be a label with nothing in it.
    api.status = connected;
    api.test = {
      ok: false,
      detail: "Something went wrong.",
      round_trip_ms: 5,
      active_tab: null,
    };
    const first = render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-test"));
    await screen.findByTestId("browser-test-result");
    expect(screen.queryByTestId("browser-test-code")).toBeNull();
    first.unmount();
    cleanup();

    // And an EMPTY code is the same thing, not an empty chip: that is the literal
    // shape the route's success body carries (`"code": ""`).
    api.test = { ok: false, detail: "Something went wrong.", code: "", round_trip_ms: 5, active_tab: null };
    render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-test"));
    await screen.findByTestId("browser-test-result");
    expect(screen.queryByTestId("browser-test-code")).toBeNull();
  });

  it("writes exactly one request, and it is the Test", async () => {
    api.status = connected;
    render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-test"));

    await screen.findByTestId("browser-test-result");
    expect(postPaths()).toEqual(["/browser/test"]);
    expect(api.puts).toEqual([]);
    expect(api.patches).toEqual([]);
    expect(api.dels).toEqual([]);
  });

  it("a second Test replaces the readout rather than stacking one", async () => {
    api.status = connected;
    render(<YourBrowserCard />);
    fireEvent.click(await screen.findByTestId("browser-test"));
    await screen.findByTestId("browser-test-result");

    api.test = { ok: false, detail: "PERMISSION_DENIED: site access.", round_trip_ms: 3, active_tab: null };
    fireEvent.click(screen.getByTestId("browser-test"));

    await waitFor(() =>
      expect(screen.getByTestId("browser-test-detail").textContent).toContain("PERMISSION_DENIED"),
    );
    expect(screen.getAllByTestId("browser-test-result")).toHaveLength(1);
  });
});
