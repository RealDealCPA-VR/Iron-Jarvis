/**
 * Fourth review BS1 / C-BS-1: the sidebar's card says why Auto-allow does not
 * answer it.
 *
 * In a Local only sidebar reply every page action asks first, and the
 * Auto-allow switch does not answer that card (the daemon refuses it). The
 * daemon now sends such a card with `floor: true` (Allow once and Deny only,
 * drawn by every add-on since v1.522.0) and one `note` line; this file runs
 * the BUILT add-on (`extensions/chrome/dist`, the way
 * `sidepanel-runtime-v1242.test.ts` runs it) and asserts the line is drawn
 * under the card, kept when an "Allow all" refusal puts the card back, and
 * absent on an ordinary card.
 *
 * It must not skip: a missing build is a failure naming the command.
 */
import { afterEach, describe, expect, it } from "vitest";
import { waitFor } from "@testing-library/dom";
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

const ADDON = resolve(process.cwd(), "..", "extensions", "chrome");
const PANEL_HTML = join(ADDON, "dist", "sidepanel.html");
const PANEL_JS = join(ADDON, "dist", "sidepanel.js");
const absent = [PANEL_HTML, PANEL_JS].filter((p) => !existsSync(p));

const LOCAL_ONLY_LINE =
  "Local only is on, so every page action asks first. Auto-allow does not cover it.";

type Message = Record<string, unknown>;

function el(id: string): HTMLElement {
  const node = document.getElementById(id);
  if (!node) throw new Error(`the built panel has no #${id}: the HTML and the bundle disagree`);
  return node;
}

/** Mount the real HTML and run the real bundle against a stubbed `chrome`. */
function mountPanel(): { sent: Message[]; emit: (event: string, payload?: Message) => void } {
  const html = readFileSync(PANEL_HTML, "utf8").replace(/\r\n/g, "\n");
  const head = /<head>([\s\S]*?)<\/head>/.exec(html);
  const bodyOpen = /<body([^>]*)>/.exec(html);
  if (!head || !bodyOpen) throw new Error("dist/sidepanel.html is not the page this test knows how to mount");
  document.head.innerHTML = head[1];
  document.body.innerHTML = html.slice(
    (bodyOpen.index ?? 0) + bodyOpen[0].length,
    html.lastIndexOf("</body>"),
  );
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
            state: "connected", paired: true, hostPermission: true, access: "interactive",
            lastError: "", pendingCommands: 0,
          };
        }
        if (message["kind"] === "panel_action") return { sent: true };
        return {};
      },
      onMessage: { addListener: (fn: (message: unknown) => void) => { listener = fn; } },
    },
    storage: { local: { get: async () => ({}), set: async () => {}, remove: async () => {} } },
  };
  const proto = Element.prototype as unknown as { scrollTo?: () => void };
  if (typeof proto.scrollTo !== "function") proto.scrollTo = () => {};
  new Function(readFileSync(PANEL_JS, "utf8"))();
  if (!listener) throw new Error("the panel registered no onMessage listener");
  return {
    sent,
    emit: (event, payload = {}) => listener?.({ kind: "panel_event", event, payload }),
  };
}

if (absent.length) {
  describe("the built browser add-on", () => {
    it("must be built before this suite runs", () => {
      throw new Error(
        "the built browser add-on is missing. Run `node scripts/build.mjs` in extensions/chrome." +
          `\nmissing: ${absent.join(", ")}`,
      );
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

  describe("a card Auto-allow does not answer says so (fourth review C-BS-1)", () => {
    it("draws the note under a Local only card, with Allow once and Deny only", async () => {
      const h = mountPanel();
      await waitFor(() => expect(el("state-word").textContent).toBe("Connected"));
      h.emit("approval", {
        id: "ask_lo",
        text: "Iron Jarvis wants to click 'Search'. Allow it?",
        floor: true,
        note: LOCAL_ONLY_LINE,
      });
      expect((el("approval") as HTMLElement).hidden).toBe(false);
      const note = el("approval-note");
      expect(note.hidden).toBe(false);
      expect(note.textContent).toBe(LOCAL_ONLY_LINE);
      expect((el("approve-always") as HTMLElement).hidden).toBe(true);
      expect((el("approve") as HTMLElement).hidden).toBe(false);

      // An "Allow all" refusal puts the same card back, line included.
      (el("approve") as HTMLButtonElement).click();
      await waitFor(() => expect((el("approval") as HTMLElement).hidden).toBe(true));
      h.emit("error", { text: "Choose Allow once or Deny.", reason: "risk_card_all" });
      expect((el("approval") as HTMLElement).hidden).toBe(false);
      expect(el("approval-note").textContent).toBe(LOCAL_ONLY_LINE);
      expect(el("approval-note").hidden).toBe(false);
    });

    it("an ordinary card, and an older daemon's card, carry no line", async () => {
      const h = mountPanel();
      await waitFor(() => expect(el("state-word").textContent).toBe("Connected"));
      h.emit("approval", { id: "ask_lo", text: "Wants to click Search.", floor: true, note: LOCAL_ONLY_LINE });
      expect(el("approval-note").hidden).toBe(false);
      h.emit("approval", { id: "ask_plain", text: "Iron Jarvis wants to click 'Next'. Allow it?" });
      expect(el("approval-note").hidden).toBe(true);
      expect(el("approval-note").textContent).toBe("");
      expect((el("approve-always") as HTMLElement).hidden).toBe(false);
    });

    it("the switch's tooltip says a Local only chat still asks", async () => {
      mountPanel();
      expect((el("auto") as HTMLButtonElement).title).toContain(
        "In a Local only chat every page action still asks.",
      );
    });
  });
}
