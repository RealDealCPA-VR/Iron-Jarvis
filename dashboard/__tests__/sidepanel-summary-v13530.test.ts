/**
 * v1.353.0 lane 3D (G53) — the browser sidebar's folded line counts failures
 * and time.
 *
 * Every sidebar turn used to fold to "4 steps": a click that failed was amber
 * only INSIDE the fold, so a turn that half-failed read like one that worked.
 * Now the line a turn folds to is "4 steps · 1 failed · 12 s", the block
 * carries `data-failed`, and the panel's stylesheet inks that line amber.
 *
 * Driven through the BUILT add-on (extensions/chrome/dist — the bundle Chrome
 * loads), exactly as `sidepanel-runtime-v1242` does: the real HTML, the real
 * IIFE, a stubbed `chrome`. A missing bundle is a FAILURE naming the build
 * command, never a skip. The time is the panel's own clock (`Date.now`, read
 * by the bundle) moved by the test — the duration words are compared with the
 * chat receipt's `secondsText` on the same numbers, so the two surfaces say a
 * duration the same way.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/dom";
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { secondsText } from "@/components/chat/TurnReceipt";

const ADDON = resolve(process.cwd(), "..", "extensions", "chrome");
const PANEL_HTML = join(ADDON, "dist", "sidepanel.html");
const PANEL_JS = join(ADDON, "dist", "sidepanel.js");
const SRC_HTML = join(ADDON, "src", "sidepanel", "sidepanel.html");

const absent = [PANEL_HTML, PANEL_JS].filter((p) => !existsSync(p));

type Message = Record<string, unknown>;

function el(id: string): HTMLElement {
  const node = document.getElementById(id);
  if (!node) throw new Error(`the built panel has no #${id}`);
  return node;
}

/** Mount the real page and run the real bundle (the v1242 harness, trimmed). */
function mountPanel(): { sent: Message[]; emit: (event: string, payload?: Record<string, unknown>) => void } {
  const html = readFileSync(PANEL_HTML, "utf8").replace(/\r\n/g, "\n");
  const head = /<head>([\s\S]*?)<\/head>/.exec(html);
  const bodyOpen = /<body([^>]*)>/.exec(html);
  if (!head || !bodyOpen) throw new Error("dist/sidepanel.html is not the page this test knows");
  document.head.innerHTML = head[1];
  document.body.innerHTML = html.slice((bodyOpen.index ?? 0) + bodyOpen[0].length, html.lastIndexOf("</body>"));
  for (const [, name, value] of bodyOpen[1].matchAll(/([\w-]+)="([^"]*)"/g)) {
    document.body.setAttribute(name, value);
  }
  const sent: Message[] = [];
  let listener: ((message: unknown) => void) | null = null;
  (globalThis as unknown as { chrome: unknown }).chrome = {
    runtime: {
      id: "lgihfomaieifpnemakmpadmggjnoojmm",
      getManifest: () => JSON.parse(readFileSync(join(ADDON, "manifest.json"), "utf8")),
      sendMessage: async (message: Message) => {
        sent.push(message);
        if (message["kind"] === "status") {
          return {
            state: "connected",
            paired: true,
            hostPermission: true,
            access: "interactive",
            lastError: "",
            pendingCommands: 0,
          };
        }
        if (message["kind"] === "panel_action") return { sent: true };
        return {};
      },
      onMessage: { addListener: (fn: (message: unknown) => void) => (listener = fn) },
    },
    storage: { local: { get: async () => ({}), set: async () => {}, remove: async () => {} } },
  };
  const proto = Element.prototype as unknown as { scrollTo?: () => void };
  if (typeof proto.scrollTo !== "function") proto.scrollTo = () => {};
  new Function(readFileSync(PANEL_JS, "utf8"))();
  if (!listener) throw new Error("the panel registered no listener");
  return {
    sent,
    emit: (event, payload = {}) => listener?.({ kind: "panel_event", event, payload }),
  };
}

async function ready() {
  const h = mountPanel();
  await waitFor(() => expect(el("state-word").textContent).not.toBe("Checking…"));
  return h;
}

/** Send a question through the real composer (the only way a turn's start is known). */
async function send(h: ReturnType<typeof mountPanel>, text: string) {
  (el("ask") as HTMLTextAreaElement).value = text;
  (el("send") as HTMLButtonElement).click();
  await waitFor(() =>
    expect(h.sent.filter((m) => m["action"] === "send").some((m) => (m["params"] as Message)?.["text"] === text)).toBe(true),
  );
}

function blocks(): HTMLDetailsElement[] {
  return Array.from(el("transcript").querySelectorAll<HTMLDetailsElement>("details.work"));
}

if (absent.length) {
  describe("the built browser add-on", () => {
    it("must be built before this suite runs", () => {
      throw new Error(
        "the built browser add-on is missing. Run `node scripts/build.mjs` in extensions/chrome " +
          `(CI does this before the dashboard suite).\nmissing: ${absent.join(", ")}`,
      );
    });
  });
} else {
  afterEach(() => {
    vi.restoreAllMocks();
    document.head.innerHTML = "";
    document.body.innerHTML = "";
    for (const name of Array.from(document.body.attributes).map((a) => a.name)) {
      document.body.removeAttribute(name);
    }
    delete (globalThis as unknown as { chrome?: unknown }).chrome;
  });

  describe("the folded line counts failures and time (G53)", () => {
    it("a turn where one click failed folds to '4 steps · 1 failed · 12 s', flagged for amber", async () => {
      const h = await ready();
      let now = 1_000_000;
      vi.spyOn(Date, "now").mockImplementation(() => now);
      await send(h, "fill in the form");
      h.emit("tool", { name: "browser_read_page", text: "Reading the page…", status: "started", ok: null });
      h.emit("tool", { name: "browser_read_page", text: "Read the page.", status: "finished", ok: true });
      h.emit("tool", { name: "browser_click", text: "Clicking 'Next'…", status: "started", ok: null });
      h.emit("tool", { name: "browser_click", text: "Could not click 'Next'.", status: "finished", ok: false });
      h.emit("tool", { name: "browser_type", text: "Typing into 'Name'…", status: "started", ok: null });
      h.emit("tool", { name: "browser_type", text: "Typed into 'Name'.", status: "finished", ok: true });
      h.emit("tool", { name: "browser_scroll", text: "Scrolled down.", status: "finished", ok: true });
      now += 12_000;
      h.emit("done", {});
      const [block] = blocks();
      expect(block.querySelector("summary")?.textContent).toBe("4 steps · 1 failed · 12 s");
      expect(block.dataset["failed"]).toBe("1");
      expect(block.dataset["live"]).toBe("false");
    });

    it("a clean turn says no failures and still says how long", async () => {
      const h = await ready();
      let now = 5_000_000;
      vi.spyOn(Date, "now").mockImplementation(() => now);
      await send(h, "read it");
      h.emit("tool", { name: "browser_read_page", text: "Read the page.", status: "finished", ok: true });
      now += 4_200;
      h.emit("done", {});
      const [block] = blocks();
      expect(block.querySelector("summary")?.textContent).toBe("1 step · 4.2 s");
      expect(block.dataset["failed"]).toBeUndefined();
    });

    it("a turn that ends on an error folds the same way", async () => {
      const h = await ready();
      let now = 9_000_000;
      vi.spyOn(Date, "now").mockImplementation(() => now);
      await send(h, "buy it");
      h.emit("tool", { name: "browser_click", text: "Could not click 'Buy'.", status: "finished", ok: false });
      now += 75_000;
      h.emit("error", { text: "The model stopped." });
      const [block] = blocks();
      expect(block.querySelector("summary")?.textContent).toBe("1 step · 1 failed · 1 min 15 s");
      expect(block.dataset["failed"]).toBe("1");
    });

    it("says a duration exactly as the chat receipt does (secondsText), turn after turn", async () => {
      const h = await ready();
      let now = 20_000_000;
      vi.spyOn(Date, "now").mockImplementation(() => now);
      const spans = [800, 9_949, 12_400, 59_600, 185_000, 3_600_000];
      for (const [k, ms] of spans.entries()) {
        await send(h, `turn ${k}`);
        h.emit("tool", { name: "browser_read_page", text: "Read the page.", status: "finished", ok: true });
        now += ms;
        h.emit("done", {});
        const block = blocks()[k];
        expect(block.querySelector("summary")?.textContent).toBe(`1 step · ${secondsText(ms)}`);
      }
    });

    it("a turn this panel did not send (reopened mid-turn) says no time it cannot know", async () => {
      const h = await ready();
      h.emit("tool", { name: "browser_click", text: "Clicked 'Next'.", status: "finished", ok: true });
      h.emit("done", {});
      expect(blocks()[0].querySelector("summary")?.textContent).toBe("1 step");
    });

    it("a turn with no steps leaves no start behind for a later turn this panel did not send", async () => {
      // The worker broadcasts panel events to every open panel, so a panel can
      // see another window's turn. Its own earlier turn that took no step must
      // not lend that turn a start time.
      const h = await ready();
      let now = 30_000_000;
      vi.spyOn(Date, "now").mockImplementation(() => now);
      await send(h, "just say hi");
      h.emit("delta", { text: "Hi!" });
      now += 2_000;
      h.emit("done", {});
      expect(blocks()).toHaveLength(0);
      now += 60_000;
      h.emit("tool", { name: "browser_click", text: "Clicked 'Next'.", status: "finished", ok: true });
      now += 5_000;
      h.emit("done", {});
      expect(blocks()[0].querySelector("summary")?.textContent).toBe("1 step");
    });

    it("the stylesheet inks a failed turn's folded line amber, from the palette token (source + dist)", () => {
      for (const file of [SRC_HTML, PANEL_HTML]) {
        const html = readFileSync(file, "utf8").replace(/\r\n/g, "\n");
        expect(html, file).toMatch(/\.work\[data-failed\]\s*>\s*summary\s*\{\s*color:\s*var\(--amber\);\s*\}/);
      }
    });
  });
}
