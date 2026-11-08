/**
 * v1.454.0: the pointer, the click ripple and the "Jarvis is controlling this
 * tab" badge (extensions/chrome/src/background/pointer.ts), and the page
 * reader's two answers they are drawn from (content/actions.ts `locate`,
 * `lastActed`).
 *
 * "I'd also like to see when it has control of the browser and specifically when
 * it is clicking with something that displays its screen movement and item
 * clicked."
 *
 * The painters run INSIDE the page in the product (`chrome.scripting.
 * executeScript`); here the stub runs the same function against jsdom's
 * document, so what they draw is asserted on real nodes:
 *
 *  - every node is `pointer-events: none` and aria-hidden, at the top z-index;
 *  - the words are CSS generated content, never text in the DOM (a page read
 *    must not read them as the page's own words);
 *  - the painters touch only their own nodes and read nothing of the page;
 *  - under reduced motion the pointer is placed without moving, the ripple is
 *    still, and the target is STILL highlighted;
 *  - before a click or typing the worker locates the target, draws, and waits
 *    POINTER_MOVE_MS; a target it cannot find draws nothing and waits nothing;
 *  - `locate` never scrolls and never refuses; `lastActed` is where the last
 *    click landed.
 *
 * Excluded from the dashboard's `tsc` (it reaches `chrome`), like the picker's.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// What jsdom lacks and the add-on's click path uses (the picker test's own fills).
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

/** Every executeScript call: what ran and with which arguments. */
const injected: { func: string; args: unknown[] }[] = [];
(globalThis as unknown as { chrome: unknown }).chrome = {
  runtime: { id: "test", sendMessage: async () => ({}) },
  scripting: {
    // The product serialises `func` into the page; here it runs in jsdom's page.
    executeScript: async ({ func, args }: { func: (...a: unknown[]) => unknown; args: unknown[] }) => {
      injected.push({ func: func.name, args });
      return [{ result: func(...args) }];
    },
  },
};

const pointerMod = await import("../../extensions/chrome/src/background/pointer");
const actions = await import("../../extensions/chrome/src/content/actions");
const channel = await import("../../extensions/chrome/src/content/channel");
const {
  ActionPointer,
  BADGE_ID,
  POINTER_ID,
  POINTER_MOVE_MS,
  pagePainters,
  badgeInTab,
  clearInTab,
  boxFrom,
} = pointerMod;

const BOX = { x: 100, y: 200, width: 80, height: 30 };

function setReducedMotion(on: boolean): void {
  (window as unknown as { matchMedia: unknown }).matchMedia = (q: string) => ({
    matches: on && q.includes("prefers-reduced-motion: reduce"),
    media: q,
    addEventListener() {},
    removeEventListener() {},
  });
}

/** Every node the painters made carries no text of its own. */
function ownText(id: string): string {
  return (document.getElementById(id)?.textContent ?? "").trim();
}

beforeEach(() => {
  injected.length = 0;
  document.head.innerHTML = "";
  document.body.innerHTML = "";
  setReducedMotion(false);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the page painters draw over the page and never become part of it", () => {
  it("the pointer moves to the element's centre, says Clicking, and catches no input", () => {
    pagePainters.paintPointer(POINTER_ID, BOX, "aim-click", 420, 1100);
    const pointer = document.getElementById(POINTER_ID) as HTMLElement;
    expect(pointer).not.toBeNull();
    expect(pointer.getAttribute("aria-hidden")).toBe("true");
    expect(pointer.dataset["phase"]).toBe("aim-click");
    expect(pointer.dataset["still"]).toBe("0");
    // The centre of the box: (100 + 40, 200 + 15).
    expect(pointer.style.transform).toBe("translate(140px,215px)");
    const target = document.getElementById(`${POINTER_ID}-target`) as HTMLElement;
    expect(target.style.left).toBe("97px");
    expect(target.style.width).toBe("86px");
    const css = document.getElementById(`${POINTER_ID}-style`)?.textContent ?? "";
    for (const id of [POINTER_ID, `${POINTER_ID}-target`, `${POINTER_ID}-ripple`]) {
      const rule = new RegExp(`#${id}\\{[^}]*pointer-events:none`);
      expect(css).toMatch(rule);
    }
    expect(css).toContain("z-index:2147483647");
    // The words are generated content, not DOM text a page read would find.
    expect(css).toContain('content:"Clicking"');
    expect(css).toContain('content:"Typing"');
    expect(ownText(POINTER_ID)).toBe("");
    expect(document.body.textContent?.trim() ?? "").toBe("");
  });

  it("a click lands as a ripple at the same spot; painting twice keeps one of each", () => {
    pagePainters.paintPointer(POINTER_ID, BOX, "aim-click", 420, 1100);
    pagePainters.paintPointer(POINTER_ID, BOX, "click", 420, 1100);
    pagePainters.paintPointer(POINTER_ID, BOX, "click", 420, 1100);
    const ripple = document.getElementById(`${POINTER_ID}-ripple`) as HTMLElement;
    expect(ripple.style.left).toBe("140px");
    expect(ripple.style.top).toBe("215px");
    expect(document.querySelectorAll(`#${POINTER_ID}`)).toHaveLength(1);
    expect(document.querySelectorAll(`#${POINTER_ID}-ripple`)).toHaveLength(1);
    expect(document.querySelectorAll(`#${POINTER_ID}-style`)).toHaveLength(1);
    // Typing says so, and draws no ripple of its own.
    document.getElementById(`${POINTER_ID}-ripple`)?.remove();
    pagePainters.paintPointer(POINTER_ID, BOX, "type", 420, 1100);
    expect((document.getElementById(POINTER_ID) as HTMLElement).dataset["phase"]).toBe("type");
    expect(document.getElementById(`${POINTER_ID}-ripple`)).toBeNull();
  });

  it("under reduced motion the pointer is placed, the ripple is still, and the target still shows", () => {
    vi.useFakeTimers();
    setReducedMotion(true);
    const reduced = pagePainters.paintPointer(POINTER_ID, BOX, "click", 420, 1100);
    expect(reduced).toBe(true);
    const pointer = document.getElementById(POINTER_ID) as HTMLElement;
    expect(pointer.dataset["still"]).toBe("1");
    const ripple = document.getElementById(`${POINTER_ID}-ripple`) as HTMLElement;
    expect(ripple.dataset["still"]).toBe("1");
    const css = document.getElementById(`${POINTER_ID}-style`)?.textContent ?? "";
    expect(css).toMatch(new RegExp(`#${POINTER_ID}\\[data-still="1"\\]\\{transition:none\\}`));
    // The highlight is there — the target is shown, just not animated...
    const target = document.getElementById(`${POINTER_ID}-target`) as HTMLElement;
    expect(target).not.toBeNull();
    expect(target.dataset["fade"]).toBe("0");
    // ...and taken down by time instead of a fade.
    vi.advanceTimersByTime(420 + 1100 + 10);
    expect(document.getElementById(`${POINTER_ID}-target`)).toBeNull();
  });

  it("the badge says the tab is controlled, in generated content, and clears by name", () => {
    pagePainters.paintBadge(BADGE_ID);
    pagePainters.paintBadge(BADGE_ID);
    expect(document.querySelectorAll(`#${BADGE_ID}`)).toHaveLength(1);
    const css = document.getElementById(`${BADGE_ID}-style`)?.textContent ?? "";
    expect(css).toContain('content:"Jarvis is controlling this tab"');
    expect(css).toMatch(new RegExp(`#${BADGE_ID}\\{[^}]*pointer-events:none`));
    expect(ownText(BADGE_ID)).toBe("");
    // Clearing removes exactly what was drawn, and leaves the page's own nodes.
    const own = document.createElement("p");
    own.id = "the-pages-own";
    own.textContent = "page text";
    document.body.appendChild(own);
    pagePainters.paintPointer(POINTER_ID, BOX, "click", 420, 1100);
    pagePainters.clearBadge(BADGE_ID);
    pagePainters.clearPointer(POINTER_ID);
    for (const id of [BADGE_ID, `${BADGE_ID}-style`, POINTER_ID, `${POINTER_ID}-target`, `${POINTER_ID}-ripple`, `${POINTER_ID}-style`]) {
      expect(document.getElementById(id)).toBeNull();
    }
    expect(document.getElementById("the-pages-own")?.textContent).toBe("page text");
  });

  it("badgeInTab and clearInTab inject the painters by name with their ids", async () => {
    await badgeInTab(7);
    await clearInTab(7);
    expect(injected.map((i) => [i.func, i.args[0]])).toEqual([
      ["paintBadge", BADGE_ID],
      ["clearPointer", POINTER_ID],
      ["clearBadge", BADGE_ID],
    ]);
  });
});

describe("the worker's pointer, around a click", () => {
  type Run = (op: string, params: Record<string, unknown>, host: boolean) => Promise<Record<string, unknown>>;

  function pointer(answers: Record<string, Record<string, unknown>>, waits: number[]) {
    const asked: string[] = [];
    const run: Run = async (op) => {
      asked.push(op);
      return answers[op] ?? { found: false };
    };
    const p = new ActionPointer(run, async (ms) => {
      waits.push(ms);
    });
    return { p, asked };
  }

  it("before a click: locate, draw the move, then wait for it", async () => {
    const waits: number[] = [];
    const { p, asked } = pointer(
      { [channel.LOCATE_OP]: { found: true, on_screen: true, tab_id: 5, ...BOX } },
      waits,
    );
    const aimed = await p.before("click", { target: { css: "#go" } }, true);
    expect(asked).toEqual([channel.LOCATE_OP]);
    expect(aimed).toEqual({ tabId: 5, box: BOX });
    expect(injected.map((i) => i.func)).toEqual(["paintPointer"]);
    expect(injected[0]?.args.slice(0, 3)).toEqual([POINTER_ID, BOX, "aim-click"]);
    expect(waits).toEqual([POINTER_MOVE_MS]);
  });

  it("a target it cannot find, one off screen, or no site access: nothing drawn, no wait", async () => {
    for (const [answer, host] of [
      [{ found: false, tab_id: 5 }, true],
      [{ found: true, on_screen: false, tab_id: 5, ...BOX }, true],
      [{ found: true, on_screen: true, tab_id: 5, ...BOX }, false],
    ] as const) {
      injected.length = 0;
      const waits: number[] = [];
      const { p } = pointer({ [channel.LOCATE_OP]: answer }, waits);
      expect(await p.before("click", {}, host)).toBeNull();
      expect(injected).toEqual([]);
      expect(waits).toEqual([]);
    }
  });

  it("a reader that throws never fails the action", async () => {
    const waits: number[] = [];
    const p = new ActionPointer(async () => {
      throw new Error("the page reader did not answer");
    }, async (ms) => {
      waits.push(ms);
    });
    await expect(p.before("type_text", {}, true)).resolves.toBeNull();
    await expect(p.after("click", { tab_id: 5 }, true)).resolves.toBeUndefined();
    expect(waits).toEqual([]);
  });

  it("only clicks and typing are pointed at", async () => {
    const waits: number[] = [];
    const { p, asked } = pointer({}, waits);
    expect(await p.before("scroll", {}, true)).toBeNull();
    expect(await p.before("press_key", {}, true)).toBeNull();
    expect(asked).toEqual([]);
  });

  it("after a click: the ripple where the element was when it was clicked", async () => {
    const waits: number[] = [];
    const landed = { found: true, on_screen: true, tab_id: 5, x: 10, y: 20, width: 40, height: 20 };
    const { p, asked } = pointer({ [channel.LAST_ACTED_OP]: landed }, waits);
    await p.after("click", { tab_id: 5 }, true);
    expect(asked).toEqual([channel.LAST_ACTED_OP]);
    expect(injected[0]?.args.slice(0, 3)).toEqual([POINTER_ID, { x: 10, y: 20, width: 40, height: 20 }, "click"]);
    expect(waits).toEqual([]);
  });

  it("boxFrom refuses anything that is not a real box", () => {
    expect(boxFrom(null)).toBeNull();
    expect(boxFrom({ found: false })).toBeNull();
    expect(boxFrom({ found: true, x: 1, y: 2, width: 0, height: 5 })).toBeNull();
    expect(boxFrom({ found: true, x: "1", y: 2, width: 3, height: 5 })).toBeNull();
    expect(boxFrom({ found: true, x: 1, y: 2, width: 3, height: Number.NaN })).toBeNull();
    expect(boxFrom({ found: true, x: 1, y: 2, width: 3, height: 5 })).toEqual({ x: 1, y: 2, width: 3, height: 5 });
  });
});

describe("the page reader's two answers", () => {
  it("locate gives the target's box without scrolling it, and refuses nothing", () => {
    document.body.innerHTML = '<button id="go">Go</button>';
    const button = document.getElementById("go") as HTMLElement;
    button.getBoundingClientRect = () => ({ left: 12, top: 34, width: 56, height: 20 }) as DOMRect;
    const scrolled = vi.fn();
    button.scrollIntoView = scrolled;
    const found = actions.locate({ tab_id: 3, target: { css: "#go" } });
    expect(found).toMatchObject({ found: true, on_screen: true, tab_id: 3, x: 12, y: 34, width: 56, height: 20 });
    expect(scrolled).not.toHaveBeenCalled();
    // Nothing the element says or holds crosses: no name, no text.
    expect(Object.keys(found).sort()).toEqual(["found", "height", "on_screen", "tab_id", "width", "x", "y"]);
    // A target that is not there, or a bad selector: an answer, never a throw.
    expect(actions.locate({ tab_id: 3, target: { css: "#missing" } })).toEqual({ found: false, tab_id: 3 });
    expect(actions.locate({ tab_id: 3, target: { css: "[[[" } })).toEqual({ found: false, tab_id: 3 });
    expect(actions.locate({ tab_id: 3 })).toEqual({ found: false, tab_id: 3 });
  });

  it("a box above the window is found but not on screen", () => {
    document.body.innerHTML = '<button id="up">Up</button>';
    const button = document.getElementById("up") as HTMLElement;
    button.getBoundingClientRect = () => ({ left: 10, top: -400, width: 50, height: 20 }) as DOMRect;
    expect(actions.locate({ target: { css: "#up" } })).toMatchObject({ found: true, on_screen: false });
  });

  it("lastActed is where the last click landed, after its scroll", () => {
    document.body.innerHTML = '<button id="buy">Buy</button>';
    const button = document.getElementById("buy") as HTMLElement;
    let top = 900;
    button.scrollIntoView = () => {
      top = 300; // the click's own scroll brings it into view
    };
    button.getBoundingClientRect = () => ({ left: 5, top, width: 60, height: 24 }) as DOMRect;
    actions.click({ tab_id: 9, target: { css: "#buy" } });
    expect(actions.lastActed({ tab_id: 9 })).toMatchObject({ found: true, tab_id: 9, x: 5, y: 300, width: 60, height: 24 });
  });
});
