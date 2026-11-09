/**
 * v1.459.0: "Record a task" — the add-on's recorder, pinned against its own
 * source in jsdom (extensions/chrome/src/content/recorder.ts, the worker's
 * background/recorder.ts and the panel's sidepanel/recording.ts).
 *
 *  - a step names the element by role and accessible name and NEVER carries
 *    what the user typed or chose (the value is put in the field here, then
 *    looked for in every step);
 *  - a password field is marked sensitive; a click into a text box, a list or
 *    a file field is not a step of its own;
 *  - page words are masked before they leave the tab;
 *  - an event no person made records nothing, and Iron Jarvis's own actions
 *    (`whileActing`) are never recorded;
 *  - the worker keeps scheme+host+path only, tells a page a step led to
 *    ("arrive") from one the user opened ("navigate"), and caps the list;
 *  - typing into a <select> CHOOSES the option with that label, and refuses
 *    naming the options when there is none.
 *
 * A TRUSTED event cannot be made in jsdom; the live recording is driven in Edge.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";

{
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
  clickFromEnter,
  ENTER_CLICK_MS,
  fieldOf,
  isTextEntry,
  startRecording,
  stepForChange,
  stepForClick,
  stepForKey,
  stepForTyping,
  stopRecording,
  targetOf,
  whileActing,
} from "../../extensions/chrome/src/content/recorder";
import { maskText } from "../../extensions/chrome/src/content/mask";
import { typeText } from "../../extensions/chrome/src/content/actions";
import {
  addPage,
  addStep,
  ARRIVE_AFTER_STEP_MS,
  cleanStep,
  newRecording,
  pageUrl,
  recordingPainters,
} from "../../extensions/chrome/src/background/recorder";
import {
  placeholderFor,
  reviewRows,
  saveSteps,
  stepWords,
} from "../../extensions/chrome/src/sidepanel/recording";

const SECRET = "hunter2-XYZZY-NEVER-WRITTEN-DOWN";

beforeEach(() => {
  sent.length = 0;
  document.body.innerHTML = `
    <form>
      <label for="cust">Customer name</label><input id="cust" type="text" />
      <label for="pw">Password</label><input id="pw" type="password" autocomplete="current-password" />
      <label for="terms">Terms</label>
      <select id="terms"><option>Net 15</option><option>Net 30</option><option disabled>Net 90</option></select>
      <label for="file">Receipt</label><input id="file" type="file" />
      <button id="go" type="button"><span id="inner">New invoice</span></button>
      <button id="leak" type="button">Copy sk-ant-api03-ABCDEFGHIJKLMNOPQRST</button>
      <label><input id="agree" type="checkbox" /> I agree</label>
    </form>`;
  (document.getElementById("cust") as HTMLInputElement).setAttribute("value", "");
});

afterEach(() => {
  stopRecording();
});

const $ = (id: string) => document.getElementById(id) as HTMLElement;

describe("a step says WHICH element, never what was typed", () => {
  it("a click on the text inside a button is the button, by role and name", () => {
    const step = stepForClick($("inner"));
    expect(step?.kind).toBe("click");
    expect(step?.target).toMatchObject({ role: "button", name: "New invoice", tag: "button" });
  });

  it("typing names the field and carries no text, even with a value in it", () => {
    const cust = $("cust") as HTMLInputElement;
    // Put something in the field the way a person would leave it.
    cust.setAttribute("value", SECRET);
    const step = stepForTyping(cust);
    expect(step?.kind).toBe("type");
    expect(step?.target).toMatchObject({ role: "textbox", name: "Customer name", input_type: "text" });
    expect(step?.sensitive).toBe(false);
    expect(JSON.stringify(step)).not.toContain(SECRET);
  });

  it("a password field is sensitive, and its contents are never in the step", () => {
    const pw = $("pw") as HTMLInputElement;
    pw.setAttribute("value", SECRET);
    const step = stepForTyping(pw);
    expect(step?.sensitive).toBe(true);
    expect(JSON.stringify(step)).not.toContain(SECRET);
  });

  it("a chosen option is a select step with no option in it; a file field is the user's", () => {
    const sel = stepForChange($("terms"));
    expect(sel?.kind).toBe("select");
    expect(JSON.stringify(sel)).not.toContain("Net 15");
    const file = stepForChange($("file"));
    expect(file).toMatchObject({ kind: "file", sensitive: true });
    expect(stepForChange($("agree"))).toBeNull();
  });

  it("a click into a text box, a list or a file field is not a step of its own", () => {
    expect(stepForClick($("cust"))).toBeNull();
    expect(stepForClick($("terms"))).toBeNull();
    expect(stepForClick($("file"))).toBeNull();
    expect(stepForClick($("agree"))?.kind).toBe("click");
    expect(isTextEntry($("cust"))).toBe(true);
    expect(isTextEntry($("agree"))).toBe(false);
    expect(fieldOf($("cust"))).toBe($("cust"));
  });

  it("only Enter and Escape are kept; Enter in a field names the field", () => {
    expect(stepForKey("Enter", $("cust"))).toMatchObject({ kind: "key", key: "Enter", target: { name: "Customer name" } });
    expect(stepForKey("Escape", null)).toMatchObject({ kind: "key", key: "Escape" });
    expect(stepForKey("Escape", null)?.target).toBeUndefined();
    expect(stepForKey("a", $("cust"))).toBeNull();
    expect(stepForKey("Delete", null)).toBeNull();
  });

  it("page words are masked before they leave the tab", () => {
    expect(targetOf($("leak")).name).not.toContain("ABCDEFGHIJKLMNOPQRST");
    expect(maskText("token=abc123def")).toBe("token=***");
  });
});

describe("Enter in a field and the browser's own submit click (found in real Edge)", () => {
  it("a click with no pointer right after a recorded Enter is the browser's, not a step", () => {
    expect(clickFromEnter(0, 1500, 1000)).toBe(true);
    expect(clickFromEnter(0, 1000 + ENTER_CLICK_MS + 1, 1000)).toBe(false);
    // A real mouse click (detail >= 1) is always the person's.
    expect(clickFromEnter(1, 1100, 1000)).toBe(false);
    // No Enter recorded: a keyboard "click" (Space on a button) is a step.
    expect(clickFromEnter(0, 1100, 0)).toBe(false);
  });
});

describe("only the person's own actions are recorded", () => {
  it("an event no person made records nothing", async () => {
    startRecording();
    $("go").click(); // jsdom's click is untrusted, like a page script's
    $("cust").dispatchEvent(new Event("input", { bubbles: true }));
    $("cust").dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    $("terms").dispatchEvent(new Event("change", { bubbles: true }));
    await Promise.resolve();
    expect(sent.filter((m) => m["kind"] === "recorded_step")).toEqual([]);
  });

  it("whileActing hands back the action's own answer", () => {
    expect(whileActing(() => 42)).toBe(42);
    expect(() => whileActing(() => { throw new Error("x"); })).toThrow("x");
    // The counter is restored after a throw: the next action is not left deaf-forever.
    expect(whileActing(() => "again")).toBe("again");
  });

  it("starting twice is one recording; stopping says whether one ran", () => {
    expect(startRecording()).toEqual({ started: true });
    expect(startRecording()).toEqual({ started: true });
    expect(stopRecording()).toEqual({ stopped: true });
    expect(stopRecording()).toEqual({ stopped: false });
  });
});

describe("the worker's list", () => {
  it("keeps scheme, host and path — never the query string or the fragment", () => {
    expect(pageUrl("https://bank.example.com/login?token=abc#x")).toBe("https://bank.example.com/login");
    expect(pageUrl("chrome://settings")).toBe("");
    expect(pageUrl("not a url")).toBe("");
  });

  it("a page a step led to is an arrive; one the user opened later is a navigate; the same page is nothing", () => {
    let rec = newRecording(7, "https://a.example.com/start?x=1", 1000);
    expect(rec.steps).toEqual([{ kind: "navigate", url: "https://a.example.com/start", at: 1000 }]);
    rec = addStep(rec, { kind: "click", at: 2000 });
    rec = addPage(rec, "https://a.example.com/next", 2000 + ARRIVE_AFTER_STEP_MS);
    expect(rec.steps[2]).toMatchObject({ kind: "arrive", url: "https://a.example.com/next" });
    expect(addPage(rec, "https://a.example.com/next?other=1", 9000)).toBe(rec);
    rec = addPage(rec, "https://b.example.com/", 2000 + ARRIVE_AFTER_STEP_MS + 60_000);
    expect(rec.steps[3]).toMatchObject({ kind: "navigate", url: "https://b.example.com/" });
  });

  it("the list is capped and says how many were left out", () => {
    let rec = newRecording(1, "https://a.example.com/", 0);
    for (let i = 0; i < 120; i += 1) rec = addStep(rec, { kind: "click", at: i });
    expect(rec.steps.length).toBe(100);
    expect(rec.dropped).toBe(21);
  });

  it("a step from a page keeps only a step's shape", () => {
    expect(cleanStep({ kind: "run", target: {} }, 1)).toBeNull();
    const kept = cleanStep({ kind: "type", value: SECRET, target: { role: "textbox", name: "x", value: SECRET } }, 5);
    expect(JSON.stringify(kept)).not.toContain(SECRET);
    expect(kept).toMatchObject({ kind: "type", at: 5 });
  });

  it("the sign on the page is inert, hidden from readers, and its words are CSS", () => {
    recordingPainters.paintRecording("ij-rec-test");
    const edge = document.getElementById("ij-rec-test") as HTMLElement;
    const pill = document.getElementById("ij-rec-test-pill") as HTMLElement;
    expect(edge.getAttribute("aria-hidden")).toBe("true");
    expect(pill.getAttribute("aria-hidden")).toBe("true");
    expect(pill.textContent).toBe("");
    expect(document.getElementById("ij-rec-test-style")?.textContent).toContain("Recording your steps");
    recordingPainters.paintRecording("ij-rec-test");
    expect(document.querySelectorAll("#ij-rec-test").length).toBe(1);
    recordingPainters.clearRecording("ij-rec-test");
    expect(document.getElementById("ij-rec-test-pill")).toBeNull();
  });
});

describe("the review", () => {
  it("names a value from the field's own name, unique within the recording", () => {
    const used = new Set<string>();
    expect(placeholderFor({ name: "Customer name" }, used)).toBe("customer_name");
    expect(placeholderFor({ name: "Customer name" }, used)).toBe("customer_name_2");
    expect(placeholderFor({ name: "2024 total" }, used)).toBe("value_2024_total");
    expect(placeholderFor({}, used)).toBe("value");
  });

  it("only a typed text or a choice takes a value; a password never does", () => {
    const rows = reviewRows([
      { kind: "type", target: { name: "Email" } },
      { kind: "type", sensitive: true, target: { name: "Password" } },
      { kind: "click", target: { role: "button", name: "Go" } },
    ]);
    rows[1]!.fixed = SECRET; // even if something put text there, it is not sent
    const out = saveSteps(rows);
    expect(out[0]).toMatchObject({ placeholder: "email" });
    expect(out[1]).not.toHaveProperty("fixed");
    expect(out[1]).not.toHaveProperty("placeholder");
    expect(out[2]).not.toHaveProperty("placeholder");
    expect(JSON.stringify(out)).not.toContain(SECRET);
    expect(stepWords({ kind: "arrive", url: "https://a.example.com/b" })).toBe("Lands on a.example.com/b");
  });
});

describe("typing into a list chooses the option with that label (the replay)", () => {
  it("chooses, fires change, and refuses a missing or disabled label naming the options", () => {
    let changed = 0;
    $("terms").addEventListener("change", () => (changed += 1));
    typeText({ target: { css: "#terms" }, text: " net 30 ", tab_id: 1 });
    const options = Array.from(document.querySelectorAll("#terms option")) as HTMLOptionElement[];
    expect(options[1]!.selected).toBe(true);
    expect(changed).toBe(1);
    expect(() => typeText({ target: { css: "#terms" }, text: "Net 60", tab_id: 1 })).toThrow(/Net 15/);
    expect(() => typeText({ target: { css: "#terms" }, text: "Net 90", tab_id: 1 })).toThrow(/no option/);
  });
});
