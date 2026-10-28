/**
 * v1.400.9: "Point at it" — the add-on's picker (extensions/chrome/src/content/picker.ts).
 *
 * The user clicks the element they mean; Jarvis is told which element "this"
 * is. Pinned here against the picker's own source in jsdom:
 *
 *  - what it reads about an element is what a snapshot reads (role, accessible
 *    name, tag, a field's TYPE, the text outside any field) — never a field's
 *    value, a password's above all;
 *  - the css selector it reports resolves back to exactly that element, and
 *    `unique_name` is true only when the role and name name one control;
 *  - the address it reports has no query or fragment (where tokens ride);
 *  - while picking, a transparent shield covers the window, so a person's
 *    click reaches no page element; an event NO PERSON made passes untouched
 *    and picks nothing — that is Jarvis's own click (review fix: swallowing it
 *    made an action report "clicked" on a page that never saw it) — and a
 *    synthetic Escape cancels nothing (`isTrusted`); a minute with no click
 *    gives the page its clicks back; cancelling removes everything it added;
 *  - a pick does not start over the browser's top layer (a modal dialog, a
 *    popover, full screen), which sits above any shield;
 *  - the content script ends a pick before it acts on the page.
 *
 * A TRUSTED click cannot be made in jsdom; the real pick is driven in Edge
 * (scripts/verify_browser_addon.sh's picker check).
 */
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// What jsdom lacks and the add-on's click path uses (actions.ts): a visibility
// check, scrolling into view, PointerEvent, and a MouseEvent that tolerates `view`.
{
  const Mouse = MouseEvent;
  (globalThis as unknown as { MouseEvent: unknown }).MouseEvent = class extends Mouse {
    constructor(type: string, init: MouseEventInit = {}) {
      const { view: _view, ...rest } = init;
      super(type, rest);
    }
  };
  (globalThis as unknown as { PointerEvent: unknown }).PointerEvent = class extends Event {
    constructor(type: string, _init?: unknown) {
      super(type, { bubbles: true, cancelable: true });
    }
  };
  const proto = Element.prototype as unknown as Record<string, unknown>;
  proto.scrollIntoView = function () {};
  proto.checkVisibility = function () {
    return true;
  };
}

const sent: Record<string, unknown>[] = [];
(globalThis as unknown as { chrome: unknown }).chrome = {
  runtime: {
    id: "test",
    sendMessage: async (message: Record<string, unknown>) => {
      sent.push(message);
      return {};
    },
  },
};

import {
  PICK_CANCELLED_KIND,
  PICKED_KIND,
} from "../../extensions/chrome/src/content/channel";
import {
  cancelPick,
  describePicked,
  meantElement,
  pageAddress,
  PICK_TIMEOUT_MS,
  startPick,
  topLayerInTheWay,
  uniqueSelector,
} from "../../extensions/chrome/src/content/picker";
import { click } from "../../extensions/chrome/src/content/actions";

const SECRET = "hunter2-XYZZY-NEVER-WRITTEN-DOWN";

beforeEach(() => {
  sent.length = 0;
  document.title = "Your cart";
  document.body.innerHTML = `
    <main>
      <form id="checkout">
        <label for="pw">Password</label>
        <input id="pw" type="password" autocomplete="current-password" />
        <label>Coupon <input id="coupon" type="text" /></label>
        <button id="apply-coupon" type="button"><span class="icon"></span>Apply coupon</button>
      </form>
      <ul>
        <li><button type="button">Remove</button></li>
        <li><button type="button">Remove</button></li>
      </ul>
      <p>Free shipping over $50.</p>
    </main>`;
  (document.getElementById("pw") as HTMLInputElement).value = SECRET;
  (document.getElementById("coupon") as HTMLInputElement).value = SECRET;
});

afterEach(() => {
  cancelPick();
  vi.useRealTimers();
});

// The content script's page watcher (a MutationObserver installed by the
// index.ts test) must see its last mutations while the page still exists.
afterAll(async () => {
  document.body.innerHTML = "";
  await new Promise((r) => setTimeout(r, 0));
});

describe("what a pick reads", () => {
  it("a button: role, name, tag, a selector by its id, a name that is unique", () => {
    const button = document.getElementById("apply-coupon")!;
    const picked = describePicked(button);
    expect(picked).toMatchObject({
      role: "button",
      name: "Apply coupon",
      tag: "button",
      input_type: "",
      text: "Apply coupon",
      css: "#apply-coupon",
      unique_name: true,
      title: "Your cart",
    });
    expect(document.querySelectorAll(picked.css)).toHaveLength(1);
  });

  it("a password field is described by its type, its contents are never read", () => {
    const picked = describePicked(document.getElementById("pw")!);
    expect(picked.input_type).toBe("password");
    expect(picked.text).toBe("");
    expect(JSON.stringify(picked)).not.toContain(SECRET);
  });

  it("a container's text leaves out every field's value", () => {
    const picked = describePicked(document.getElementById("checkout")!);
    expect(picked.text).toContain("Apply coupon");
    expect(JSON.stringify(picked)).not.toContain(SECRET);
  });

  it("a name two controls share is not unique, and the selector still picks one", () => {
    const second = document.querySelectorAll("li button")[1] as HTMLElement;
    const picked = describePicked(second);
    expect(picked.name).toBe("Remove");
    expect(picked.unique_name).toBe(false);
    const found = document.querySelectorAll(picked.css);
    expect(found).toHaveLength(1);
    expect(found[0]).toBe(second);
  });

  it("a click on the text inside a button means the button", () => {
    const icon = document.querySelector("#apply-coupon .icon")!;
    expect(meantElement(icon)).toBe(document.getElementById("apply-coupon"));
    const para = document.querySelector("p")!;
    expect(meantElement(para)).toBe(para);
  });

  it("an id with odd characters still yields a selector that finds the element", () => {
    document.body.insertAdjacentHTML("beforeend", '<button id="a:b.c 1">Odd</button>');
    const odd = document.getElementById("a:b.c 1")!;
    const css = uniqueSelector(odd);
    expect(css).not.toBe("");
    expect(document.querySelector(css)).toBe(odd);
  });

  it("the address leaves out the query and the fragment", () => {
    expect(pageAddress("https://shop.example/cart?session=abc123&sig=zz#pay")).toBe(
      "https://shop.example/cart",
    );
    expect(pageAddress("not a url")).toBe("");
  });
});

describe("while picking", () => {
  const overlay = () =>
    Array.from(document.documentElement.children).filter(
      (n) => n !== document.head && n !== document.body,
    ) as HTMLElement[];

  it("a shield covers the window and takes the pointer; the banner says how to stop", () => {
    expect(startPick()).toEqual({ started: true });
    const [shield, box, banner] = overlay();
    expect(shield.style.pointerEvents).toBe("auto");
    expect(shield.style.position).toBe("fixed");
    // The highest z-index there is, appended after the page's own content.
    expect(shield.style.zIndex).toBe("2147483647");
    expect(box.style.pointerEvents).toBe("none");
    expect(banner.style.pointerEvents).toBe("none");
    expect(banner.textContent).toBe("Click what you mean · Esc to cancel");
    expect(banner.getAttribute("role")).toBe("status");
  });

  it("an event no person made passes untouched and picks nothing", () => {
    const button = document.getElementById("apply-coupon")!;
    const onPage = vi.fn();
    const onDown = vi.fn();
    button.addEventListener("click", onPage);
    button.addEventListener("mousedown", onDown);
    startPick();
    button.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    button.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
    expect(onPage).toHaveBeenCalledTimes(1);
    expect(onDown).toHaveBeenCalledTimes(1);
    expect(sent.filter((m) => m["kind"] === PICKED_KIND)).toEqual([]);
    // Once the pick ends nothing is left over.
    expect(cancelPick()).toEqual({ cancelled: true });
    expect(overlay()).toEqual([]);
    expect(sent.filter((m) => m["kind"] === PICK_CANCELLED_KIND)).toEqual([
      { kind: PICK_CANCELLED_KIND, reason: "cancelled" },
    ]);
  });

  it("Jarvis's own click during a pick reaches the page and does what it reports", () => {
    // The reviewer's case: the add-on's click() dispatches untrusted events; a
    // picker that swallowed them made `clicked` a lie.
    document.body.insertAdjacentHTML(
      "beforeend",
      '<label><input id="agree" type="checkbox"> I agree</label><button id="go" type="button">Go</button>',
    );
    const onGo = vi.fn();
    document.getElementById("go")!.addEventListener("click", onGo);
    startPick();
    click({ target: { css: "#go" } });
    click({ target: { css: "#agree" } });
    expect(onGo).toHaveBeenCalledTimes(1);
    expect((document.getElementById("agree") as HTMLInputElement).checked).toBe(true);
  });

  it("a pick does not start over the browser's top layer", () => {
    const holder = document.createElement("div");
    Object.defineProperty(document, "fullscreenElement", { value: holder, configurable: true });
    try {
      expect(topLayerInTheWay()).toBe(true);
      expect(startPick()).toEqual({ started: false, reason: "popup" });
      expect(overlay()).toEqual([]);
    } finally {
      Object.defineProperty(document, "fullscreenElement", { value: null, configurable: true });
    }
    expect(topLayerInTheWay()).toBe(false);
  });

  it("the content script ends a pick before it acts on the page", async () => {
    const listeners: ((m: unknown, s: unknown, r: (x: unknown) => void) => boolean)[] = [];
    const chromeStub = (globalThis as unknown as { chrome: { runtime: Record<string, unknown> } }).chrome;
    chromeStub.runtime.onMessage = { addListener: (fn: (typeof listeners)[number]) => listeners.push(fn) };
    const { PAGE_CHANNEL } = await import("../../extensions/chrome/src/content/channel");
    const proto = await import("../../extensions/chrome/src/protocol");
    await import("../../extensions/chrome/src/content/index");
    expect(listeners).toHaveLength(1);
    const ask = (op: string, params: Record<string, unknown> = {}) => {
      let reply: unknown = null;
      listeners[0]({ channel: PAGE_CHANNEL, op, params }, {}, (x) => (reply = x));
      return reply as { ok: boolean; result?: Record<string, unknown> };
    };
    expect(ask("pick_element").result).toEqual({ started: true });
    expect(overlay()).toHaveLength(3);
    const go = document.getElementById("apply-coupon")!;
    const onGo = vi.fn();
    go.addEventListener("click", onGo);
    const answer = ask(proto.METHOD_CLICK, { target: { css: "#apply-coupon" } });
    expect(answer.ok).toBe(true);
    expect(onGo).toHaveBeenCalledTimes(1);
    expect(overlay()).toEqual([]);
    expect(sent).toContainEqual({ kind: PICK_CANCELLED_KIND, reason: "cancelled" });
  });

  it("a synthetic Escape cancels nothing", () => {
    startPick();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(overlay()).toHaveLength(3);
    expect(sent).toEqual([]);
  });

  it("a minute with no click gives the page its clicks back and says so", () => {
    vi.useFakeTimers();
    startPick();
    vi.advanceTimersByTime(PICK_TIMEOUT_MS - 1);
    expect(overlay()).toHaveLength(3);
    vi.advanceTimersByTime(1);
    expect(overlay()).toEqual([]);
    expect(sent).toEqual([{ kind: PICK_CANCELLED_KIND, reason: "timed out" }]);
  });

  it("a second start replaces the first, which reports it was replaced", () => {
    startPick();
    startPick();
    expect(overlay()).toHaveLength(3);
    expect(sent).toEqual([{ kind: PICK_CANCELLED_KIND, reason: "replaced" }]);
    expect(cancelPick()).toEqual({ cancelled: true });
    expect(cancelPick()).toEqual({ cancelled: false });
  });
});
