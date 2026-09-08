/**
 * The browser sidebar, EXECUTED (v1.242.0, Ship 3 — reachability finding R4).
 *
 * `tests/test_browser_sidepanel_v1242.py` pins the panel's SOURCE, and says so in
 * its own docstring. Nothing in this repository ever ran `dist/sidepanel.js`. That
 * is the repo's named failure mode written into the one file that can least afford
 * it: the panel's whole job is to render frames, its script is the only script the
 * surface has, and a runtime break in it — a null element read at load, a rename
 * between the HTML and the bundle, a listener that never registers — ships GREEN
 * past every source pin there is. The user then clicks the toolbar icon and gets a
 * blank panel with the error in a console nobody is watching.
 *
 * So this file loads the BUILT add-on — `extensions/chrome/dist/sidepanel.html` and
 * the `dist/sidepanel.js` beside it — executes the real bundle in jsdom against
 * that real HTML with a stubbed `chrome`, drives the states the daemon actually
 * produces, and asserts what RENDERS.
 *
 * WHY THE BUILT FILES AND NOT THE SOURCES. `dist/` is what Chrome loads and what
 * the installer bundles. A test that transpiled `src/sidepanel/sidepanel.ts` itself
 * would prove the TypeScript is fine while the folder the user points Chrome at
 * carries a stale or missing bundle — which is exactly the failure
 * `scripts/build.mjs` exists to catch, from the other end.
 *
 * IT MUST NOT SKIP. `dist/` is gitignored, so it can be absent — and a test that
 * quietly skips when its subject is missing is a test that cannot fail. Absent
 * files are a FAILURE naming the one command that fixes it. CI builds the add-on
 * before this suite runs (`.github/workflows/tests.yml`, job `dashboard`;
 * `release.yml`, job `suite`), which is an ordering this file depends on.
 *
 * There is no JS test runner in `extensions/chrome` and there must not be one
 * (that add-on ships to a browser and is built by esbuild alone), so the harness
 * is the dashboard's: vitest, jsdom, already here.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { waitFor } from "@testing-library/dom";
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

/** The BUILT add-on, relative to the dashboard package vitest runs in. */
const ADDON = resolve(process.cwd(), "..", "extensions", "chrome");
const PANEL_HTML = join(ADDON, "dist", "sidepanel.html");
const PANEL_JS = join(ADDON, "dist", "sidepanel.js");

/** The remedy, spelled out once: the runner must be told what to type. */
const BUILD_HINT =
  "the built browser add-on is missing. Run `node scripts/build.mjs` in " +
  "extensions/chrome (CI does this before the dashboard suite) — this test executes " +
  "dist/sidepanel.js and cannot be skipped, because a panel that no test ever ran is " +
  "a panel whose runtime breaks ship green.";

const absent = [PANEL_HTML, PANEL_JS].filter((p) => !existsSync(p));

/* -------------------------------------------------------------------------- */
/*  The chrome the panel talks to                                              */
/* -------------------------------------------------------------------------- */

type Message = Record<string, unknown>;

interface Harness {
  /** Every `chrome.runtime.sendMessage` the panel made, in order. */
  sent: Message[];
  /** What the worker answers a `{kind: "status"}` with. Mutate between drives. */
  status: Record<string, unknown>;
  /** What a `panel_action` gets back — `false` is "it never left the browser". */
  delivered: boolean;
  /** Push one `browser.panel_event` at the panel, the way the worker does. */
  emit: (event: string, payload?: Record<string, unknown>) => void;
  /** Tell the panel the status changed, the way `onStatusChange` does. */
  restatus: () => Promise<void>;
}

/** A status frame with every field the panel reads, so no test invents a shape. */
function bridgeStatus(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    state: "offline",
    paired: false,
    hostPermission: false,
    access: "",
    lastError: "",
    pendingCommands: 0,
    ...over,
  };
}

function el(id: string): HTMLElement {
  const node = document.getElementById(id);
  if (!node) throw new Error(`the built panel has no #${id} — the HTML and the bundle disagree`);
  return node;
}

function transcript(): string {
  return el("transcript").textContent ?? "";
}

function turns(): HTMLElement[] {
  return Array.from(el("transcript").querySelectorAll<HTMLElement>("p.turn"));
}

/**
 * Load the real HTML into this document, then EXECUTE the real bundle over it.
 *
 * The `<script src>` in the page is inert here (jsdom never runs a script inserted
 * through innerHTML), which is what makes the order controllable: the DOM is
 * complete before the bundle runs, exactly as it is in Chrome, and the bundle's
 * module-level `document.getElementById` calls find real nodes.
 */
function mountPanel(over: Record<string, unknown> = {}): Harness {
  const html = readFileSync(PANEL_HTML, "utf8").replace(/\r\n/g, "\n");

  const head = /<head>([\s\S]*?)<\/head>/.exec(html);
  const bodyOpen = /<body([^>]*)>/.exec(html);
  if (!head || !bodyOpen) {
    throw new Error("dist/sidepanel.html is not the page this test knows how to mount");
  }
  const bodyInner = html.slice(
    (bodyOpen.index ?? 0) + bodyOpen[0].length,
    html.lastIndexOf("</body>"),
  );

  document.head.innerHTML = head[1];
  document.body.innerHTML = bodyInner;
  // The <body> ATTRIBUTES carry the panel's whole display state machine
  // (`data-turn`, `data-access`), and innerHTML cannot bring them, so they are
  // applied from the real page rather than assumed.
  for (const [, name, value] of bodyOpen[1].matchAll(/([\w-]+)="([^"]*)"/g)) {
    document.body.setAttribute(name, value);
  }

  const harness: Harness = {
    sent: [],
    status: bridgeStatus(over),
    delivered: true,
    emit: () => {},
    restatus: async () => {},
  };

  let listener: ((message: unknown) => void) | null = null;

  const chromeStub = {
    runtime: {
      id: "lgihfomaieifpnemakmpadmggjnoojmm",
      getManifest: () => JSON.parse(readFileSync(join(ADDON, "manifest.json"), "utf8")),
      sendMessage: async (message: Message) => {
        harness.sent.push(message);
        if (message["kind"] === "status") return harness.status;
        if (message["kind"] === "panel_action") return { sent: harness.delivered };
        return {};
      },
      onMessage: {
        addListener: (fn: (message: unknown) => void) => {
          listener = fn;
        },
      },
    },
  };
  (globalThis as unknown as { chrome: unknown }).chrome = chromeStub;

  // THE ONE THING jsdom DOES NOT HAVE that the panel uses. `Element.scrollTo` is
  // real in every browser the manifest supports (the floor is Chrome 120) and
  // unimplemented in jsdom, so it is filled in rather than worked around — the
  // panel keeps the transcript pinned to the newest line and that call is not a
  // defect to be edited out of the product for a test's convenience.
  const proto = Element.prototype as unknown as { scrollTo?: () => void };
  if (typeof proto.scrollTo !== "function") {
    proto.scrollTo = () => {};
  }

  // THE REAL BUNDLE, run as Chrome runs it: one IIFE against the globals above.
  const source = readFileSync(PANEL_JS, "utf8");
  new Function(source)();

  if (!listener) {
    throw new Error(
      "the panel registered no chrome.runtime.onMessage listener, so every frame the " +
        "daemon sends would be dropped and the panel would sit on 'Checking…' forever",
    );
  }
  harness.emit = (event, payload = {}) => {
    listener?.({ kind: "panel_event", event, payload });
  };
  harness.restatus = async () => {
    listener?.({ kind: "panel_status" });
    // The refresh is a round trip through the stub; wait for the paint it causes.
    await waitFor(() => expect(el("state-word").textContent).not.toBe(""));
  };
  return harness;
}

/** Mount, and wait for the first `refresh()` to have painted the header. */
async function panelReady(over: Record<string, unknown> = {}): Promise<Harness> {
  const h = mountPanel(over);
  await waitFor(() => expect(el("state-word").textContent).not.toBe("Checking…"));
  return h;
}

/** Drive one running turn, the only way the panel can be put in one: a frame. */
function startTurn(h: Harness, text = "Working on it"): void {
  h.emit("delta", { text });
}

/* -------------------------------------------------------------------------- */

if (absent.length) {
  describe("the built browser add-on", () => {
    it("must be built before this suite runs", () => {
      throw new Error(`${BUILD_HINT}\nmissing: ${absent.join(", ")}`);
    });
  });
} else {
  afterEach(() => {
    document.head.innerHTML = "";
    document.body.innerHTML = "";
    for (const name of Array.from(document.body.attributes).map((a) => a.name)) {
      document.body.removeAttribute(name);
    }
    delete (globalThis as unknown as { chrome?: unknown }).chrome;
  });

  /* ------------------------------------------------------------------------ */
  /*  It runs at all                                                          */
  /* ------------------------------------------------------------------------ */

  describe("the built panel runs (v1.242.0)", () => {
    it("executes, finds its own elements, and announces itself to the worker", async () => {
      const h = await panelReady();
      // `open` is posted at the end of the bundle. If any statement above it threw
      // — a null element, a missing import, a renamed id — this never arrives, and
      // that is the exact break no source pin in this repository can see.
      await waitFor(() =>
        expect(h.sent.some((m) => m["kind"] === "panel_action" && m["action"] === "open")).toBe(
          true,
        ),
      );
      expect(h.sent.some((m) => m["kind"] === "status")).toBe(true);
    });

    it("prints the version of the add-on the browser is actually running", async () => {
      await panelReady();
      const manifest = JSON.parse(readFileSync(join(ADDON, "manifest.json"), "utf8"));
      // R2. chrome://extensions showed 1.235.0 before and after an app update
      // because Chrome keeps the copy it loaded; this line is the only thing that
      // tells a user WHICH build answered them.
      expect(el("version").textContent).toBe(manifest.version);
      expect(el("version").textContent).not.toBe("");
    });
  });

  /* ------------------------------------------------------------------------ */
  /*  Not paired                                                              */
  /* ------------------------------------------------------------------------ */

  describe("a browser that is not paired", () => {
    it("says it is not connected and names no access mode it was not told", async () => {
      await panelReady();
      expect(el("state-word").textContent).toBe("Not connected");
      expect(el("note").textContent).toContain("Open Jarvis");
      expect(el("state").dataset.tone).toBe("off");
      // ABSENCE, NAMED. A blank where a mode belongs reads AS the mode.
      expect(el("access").textContent).toBe("Unknown — set in Jarvis");
      // Offline offers no toggle: the bridge is already retrying, so a button
      // here would be one whose press does nothing.
      expect((el("toggle") as HTMLButtonElement).hidden).toBe(true);
    });

    it("shows the pairing wait, with the button that ends it", async () => {
      const h = await panelReady();
      h.status = bridgeStatus({ state: "pairing" });
      await h.restatus();
      await waitFor(() => expect(el("state-word").textContent).toBe("Waiting for Iron Jarvis"));
      const toggle = el("toggle") as HTMLButtonElement;
      expect(toggle.hidden).toBe(false);
      expect(toggle.textContent).toBe("Disconnect");
    });
  });

  /* ------------------------------------------------------------------------ */
  /*  Access                                                                  */
  /* ------------------------------------------------------------------------ */

  describe("browser access decides what the panel offers", () => {
    it("off: says it can run nothing, and offers no composer", async () => {
      await panelReady({ state: "connected", paired: true, hostPermission: true, access: "off" });
      await waitFor(() => expect(document.body.dataset.access).toBe("off"));
      expect(el("access").textContent).toBe("Off");
      const empty = el("empty").textContent ?? "";
      expect(empty).toContain("Browser access is off");
      expect(empty).toContain("can run nothing");
      // The composer is hidden by the page's own stylesheet, keyed on the SAME
      // attribute the header printed from — so the panel cannot offer a box whose
      // every message the daemon would refuse.
      expect(getComputedStyle(document.querySelector("footer") as Element).display).toBe("none");
    });

    it("read_only: names the mode and puts the composer back", async () => {
      await panelReady({
        state: "connected",
        paired: true,
        hostPermission: true,
        access: "read_only",
      });
      await waitFor(() => expect(el("access").textContent).toBe("Read only"));
      expect(document.body.dataset.access).toBe("read_only");
      expect(el("state-word").textContent).toBe("Connected");
      expect(getComputedStyle(document.querySelector("footer") as Element).display).not.toBe(
        "none",
      );
      // And the access-off explainer is gone, rather than sitting under a live
      // composer contradicting it.
      expect(getComputedStyle(el("empty")).display).toBe("none");
    });
  });

  /* ------------------------------------------------------------------------ */
  /*  A turn                                                                  */
  /* ------------------------------------------------------------------------ */

  describe("a turn, as the daemon narrates it", () => {
    it("streams one answer into one turn and switches the controls", async () => {
      const h = await panelReady({
        state: "connected",
        paired: true,
        hostPermission: true,
        access: "read_only",
      });
      h.emit("delta", { text: "Reading " });
      h.emit("delta", { text: "the page" });
      expect(document.body.dataset.turn).toBe("running");
      // ONE node, appended to — not two turns for two deltas.
      const answers = turns().filter((t) => t.dataset["who"] === "jarvis");
      expect(answers).toHaveLength(1);
      expect(answers[0].textContent).toBe("Reading the page");
      // Send is out, Stop and Steer are in, and all three come off one attribute.
      expect(getComputedStyle(el("send")).display).toBe("none");
      expect(getComputedStyle(el("stop")).display).not.toBe("none");

      h.emit("done", {});
      expect(document.body.dataset.turn).toBe("idle");
      expect(getComputedStyle(el("stop")).display).toBe("none");
    });

    it("a tool round is its own line, and the next delta starts a new answer", async () => {
      const h = await panelReady({ state: "connected", access: "read_only", paired: true });
      h.emit("delta", { text: "First" });
      h.emit("tool", { name: "read_page", text: "Reading the page" });
      h.emit("delta", { text: "Second" });
      const said = turns().map((t) => `${t.dataset["who"]}:${t.textContent}`);
      expect(said).toEqual(["jarvis:First", "tool:Reading the page", "jarvis:Second"]);
    });

    it("Stop promises only what a stop can do", async () => {
      const h = await panelReady({ state: "connected", access: "read_only", paired: true });
      startTurn(h);
      (el("stop") as HTMLButtonElement).click();
      // NOT flipped to idle on the press: a tool already running finishes, and the
      // daemon's `done` is what ends the turn.
      expect(document.body.dataset.turn).toBe("running");
      expect(transcript()).toContain("The step already running will finish");
      await waitFor(() =>
        expect(h.sent.some((m) => m["action"] === "stop")).toBe(true),
      );
    });

    it("an error frame ends the turn and says so in the panel", async () => {
      const h = await panelReady({ state: "connected", access: "read_only", paired: true });
      startTurn(h);
      h.emit("error", { text: "Iron Jarvis could not reach that page." });
      expect(document.body.dataset.turn).toBe("idle");
      expect(transcript()).toContain("could not reach that page");
    });
  });

  /* ------------------------------------------------------------------------ */
  /*  An approval                                                             */
  /* ------------------------------------------------------------------------ */

  describe("an approval is rendered here and answered in the daemon", () => {
    it("appears only when asked, carries the daemon's words, and clears on the answer", async () => {
      const h = await panelReady({ state: "connected", access: "interactive", paired: true });
      // Nothing pending: the card is not sitting there with placeholder words.
      expect((el("approval") as HTMLElement).hidden).toBe(true);

      h.emit("approval", { id: "ask_7", text: "Jarvis wants to click Pay now." });
      expect((el("approval") as HTMLElement).hidden).toBe(false);
      expect(el("approval-text").textContent).toBe("Jarvis wants to click Pay now.");

      (el("approve") as HTMLButtonElement).click();
      expect((el("approval") as HTMLElement).hidden).toBe(true);
      await waitFor(() => {
        const answer = h.sent.find((m) => m["action"] === "approve");
        expect(answer).toBeTruthy();
        expect((answer?.["params"] as Record<string, unknown>)["id"]).toBe("ask_7");
      });
    });

    it("a finished turn takes the card with it", async () => {
      const h = await panelReady({ state: "connected", access: "interactive", paired: true });
      h.emit("approval", { id: "ask_8", text: "Jarvis wants to submit this form." });
      expect((el("approval") as HTMLElement).hidden).toBe(false);
      h.emit("done", {});
      expect((el("approval") as HTMLElement).hidden).toBe(true);
    });
  });

  /* ------------------------------------------------------------------------ */
  /*  A steer                                                                 */
  /* ------------------------------------------------------------------------ */

  describe("a steer note is pending until the daemon says it landed", () => {
    async function steering(): Promise<Harness> {
      const h = await panelReady({ state: "connected", access: "read_only", paired: true });
      startTurn(h);
      (el("ask") as HTMLTextAreaElement).value = "check the second tab instead";
      (el("steer") as HTMLButtonElement).click();
      return h;
    }

    it("says PENDING while it is pending", async () => {
      await steering();
      const note = turns().find((t) => t.dataset["who"] === "steer");
      expect(note?.textContent).toBe("Steer (pending): check the second tab instead");
      expect(note?.dataset["pending"]).toBe("true");
    });

    it("becomes a plain note once the turn takes it", async () => {
      const h = await steering();
      h.emit("steered", { id: "steer_1", text: "check the second tab instead" });
      const note = turns().find((t) => t.dataset["who"] === "steer");
      expect(note?.textContent).toBe("Steer: check the second tab instead");
      expect(note?.dataset["pending"]).toBe("false");
    });

    it("a turn that ended without taking it SAYS SO", async () => {
      // The whole reason the note was marked pending. A steer the turn never
      // consumed, left reading as delivered, is the panel claiming an effect the
      // daemon never had.
      const h = await steering();
      h.emit("done", {});
      const note = turns().find((t) => t.dataset["who"] === "steer");
      expect(note?.textContent).toBe(
        "Steer: check the second tab instead — not taken; the turn ended first",
      );
      expect(note?.dataset["pending"]).toBe("false");
      expect(document.body.dataset.turn).toBe("idle");
    });
  });

  /* ------------------------------------------------------------------------ */
  /*  Nothing left the browser                                                */
  /* ------------------------------------------------------------------------ */

  describe("a press that never left the browser is reported as such", () => {
    it("a question that did not reach the daemon does not sit there thinking", async () => {
      const h = await panelReady({ state: "connected", access: "read_only", paired: true });
      h.delivered = false;
      (el("ask") as HTMLTextAreaElement).value = "what is on this page?";
      (el("send") as HTMLButtonElement).click();
      await waitFor(() => expect(transcript()).toContain("did not reach Iron Jarvis"));
      // And the composer came back, rather than leaving the panel stuck mid-turn.
      expect(document.body.dataset.turn).toBe("idle");
    });

    it("a steer that did not reach the daemon is not left reading as pending", async () => {
      const h = await panelReady({ state: "connected", access: "read_only", paired: true });
      startTurn(h);
      h.delivered = false;
      (el("ask") as HTMLTextAreaElement).value = "stop at the summary";
      (el("steer") as HTMLButtonElement).click();
      await waitFor(() => {
        const note = turns().find((t) => t.dataset["who"] === "steer");
        expect(note?.dataset["pending"]).toBe("false");
        expect(note?.textContent).toContain("not sent; this browser is not connected");
      });
    });
  });
}
