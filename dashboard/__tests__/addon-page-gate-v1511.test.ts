/**
 * v1.511.0 (second review B2, B9): the add-on's half of the browser gate.
 *
 *  - B9: every acting operation (click, typing, a key, a scroll) refuses with
 *    PAGE_CHANGED, before it touches anything, when the document's origin is
 *    not the one the daemon planned (and the user approved) the action on. A
 *    frame with no origin (an older daemon) is not refused.
 *  - B2: a snapshot's field row says what Enter does there (`enter`), judged
 *    as `actions.pressOn` acts, and — when Enter submits a form — what that
 *    form's own button(s) read (`form_submit`). An unnamed button reads "".
 *
 * Run in jsdom against the add-on's own source (no `chrome` is reached).
 */
import { beforeEach, describe, expect, it } from "vitest";

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

const actions = await import("../../extensions/chrome/src/content/actions");
const snapshot = await import("../../extensions/chrome/src/content/snapshot");
const { BridgeError } = await import("../../extensions/chrome/src/bridge/errors");

const ELSEWHERE = "https://elsewhere.example";

function codeOf(run: () => unknown): string {
  try {
    run();
  } catch (err) {
    return err instanceof BridgeError ? String(err.code) : `not a BridgeError: ${String(err)}`;
  }
  return "(did not throw)";
}

describe("B9: an acting frame for another page is refused", () => {
  let clicks = 0;
  let typed = 0;
  let keys = 0;

  beforeEach(() => {
    document.body.innerHTML =
      '<form><input id="amount" aria-label="Amount"><button id="go" type="submit">Transfer</button></form>';
    clicks = 0;
    typed = 0;
    keys = 0;
    const go = document.getElementById("go") as HTMLButtonElement;
    go.addEventListener("click", (event) => {
      event.preventDefault();
      clicks += 1;
    });
    const amount = document.getElementById("amount") as HTMLInputElement;
    amount.addEventListener("focus", () => {
      typed += 1;
    });
    document.addEventListener("keydown", () => {
      keys += 1;
    });
  });

  it("spells the origin the way the daemon does", () => {
    expect(actions.originHere()).toBe(`${location.protocol}//${location.host}`.toLowerCase());
  });

  it("refuses a click, typing, a key and a scroll with PAGE_CHANGED and does nothing", () => {
    const target = { css: "#go" };
    expect(codeOf(() => actions.click({ target, origin: ELSEWHERE }))).toBe("PAGE_CHANGED");
    expect(
      codeOf(() =>
        actions.typeText({ target: { css: "#amount" }, text: "250", clear: false, press_enter: true, origin: ELSEWHERE }),
      ),
    ).toBe("PAGE_CHANGED");
    expect(codeOf(() => actions.pressKey({ key: "Enter", target, origin: ELSEWHERE }))).toBe("PAGE_CHANGED");
    expect(codeOf(() => actions.scroll({ direction: "down", origin: ELSEWHERE }))).toBe("PAGE_CHANGED");
    expect(clicks).toBe(0);
    expect(typed).toBe(0);
    expect(keys).toBe(0);
  });

  it("acts when the origin is this page's, and when no origin was sent", () => {
    actions.click({ target: { css: "#go" }, origin: actions.originHere() });
    expect(clicks).toBe(1);
    actions.click({ target: { css: "#go" } });
    expect(clicks).toBe(2);
  });

  it("names the refusal with the daemon's own remedy", () => {
    let message = "";
    try {
      actions.click({ target: { css: "#go" }, origin: ELSEWHERE });
    } catch (err) {
      message = err instanceof Error ? err.message : String(err);
    }
    expect(message).toContain("nothing was done");
  });
});

describe("B2: a field row says what Enter does and what the form's button reads", () => {
  function rows(): Record<string, Record<string, unknown>> {
    const read = snapshot.readPage({ tab_id: 1 }) as unknown as { elements: Record<string, unknown>[] };
    const byName: Record<string, Record<string, unknown>> = {};
    for (const row of read.elements) {
      byName[String(row.name)] = row;
    }
    return byName;
  }

  it("judges each kind of field as pressOn acts on it", () => {
    document.body.innerHTML = [
      '<form><input aria-label="Amount"><button type="submit">Transfer</button></form>',
      '<form><input aria-label="Find"><input type="submit" value="Search"></form>',
      '<form><input aria-label="Pair"><button>Save</button><button type="submit">Delete all</button></form>',
      '<form><input aria-label="Lonely"></form>',
      '<form><input aria-label="Icon"><button type="submit"></button></form>',
      '<input aria-label="Loose">',
      '<textarea aria-label="Notes"></textarea>',
      '<div contenteditable="true" role="textbox" aria-label="Composer"></div>',
    ].join("");
    const r = rows();
    expect(r["Amount"]).toMatchObject({ enter: "submits", form_submit: "Transfer" });
    expect(r["Find"]).toMatchObject({ enter: "submits", form_submit: "Search" });
    expect(r["Pair"]).toMatchObject({ enter: "submits", form_submit: "Save / Delete all" });
    expect(r["Lonely"]).toMatchObject({ enter: "submits", form_submit: "" });
    expect(r["Icon"]).toMatchObject({ enter: "submits", form_submit: "" });
    expect(r["Loose"]).toMatchObject({ enter: "page" });
    expect(r["Loose"]).not.toHaveProperty("form_submit");
    expect(r["Notes"]).toMatchObject({ enter: "newline" });
    expect(r["Composer"]).toMatchObject({ enter: "nothing" });
    // A button is not a field: Enter there is judged as the click it is.
    expect(r["Transfer"]).not.toHaveProperty("enter");
  });
});
