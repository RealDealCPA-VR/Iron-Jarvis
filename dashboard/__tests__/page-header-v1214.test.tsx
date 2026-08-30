import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

/**
 * THE MODULE HEADER IS A NAME, AND THE EXPLANATION IS ON DEMAND (v1.214.1).
 *
 * Reported: "in all the modules there is a title like Overview (with a
 * subtitle that tells you about) … lets make it so the only thing present is
 * the title of the module for each module and if the user hovers over the
 * title it will give them the same details of the subtitle as a popup/modal
 * but not visible otherwise. This will provide a cleaner surface area as the
 * user is engaged with any specific module."
 *
 * `PageHeader` is one component behind all 38 pages, 36 of which pass a
 * subtitle, so this file is where the whole change is provable.
 *
 * THE TEST THAT MATTERS MOST is the a11y one. "Not visible otherwise" is a
 * statement about PIXELS, and the easy way to implement it — unmount the
 * subtitle until hover — would quietly delete the description for every
 * screen-reader user, who has no hover to offer. So the popover is rendered
 * always and merely made invisible, and `aria-describedby` resolves on every
 * page load exactly as it did when the line was printed in full.
 *
 * Visibility is asserted through `data-open` rather than `toBeVisible()`:
 * the popover is hidden with a Tailwind `opacity-0` class, and no stylesheet
 * is loaded in jsdom, so computed style would report it visible either way and
 * the assertion would pass against an implementation that never hid anything.
 */

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  return {
    AnimatePresence: ({ children }: { children?: unknown }) =>
      createElement(Fragment, null, children as never),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => tagFor(String(tag)),
    }),
  };
});

import { PageHeader } from "@/components/PageHeader";

const SUB =
  "Assemble a round-table of agents — built-in, yours, and agents on other computers.";

const title = () => screen.getByTestId("page-title");
const tip = () => screen.getByTestId("page-subtitle");
const isOpen = () => tip().getAttribute("data-open") === "true";

afterEach(cleanup);

describe("the subtitle is not on the page", () => {
  it("starts closed — the module shows its name and nothing else", () => {
    render(<PageHeader title="Agents" subtitle={SUB} />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toContain("Agents");
    expect(isOpen()).toBe(false);
    expect(tip().className).toContain("opacity-0");
    // ...and it costs the page no room: an absolutely-positioned popover is
    // what makes this a CLEANER surface rather than a hidden one.
    expect(tip().className).toContain("absolute");
  });

  it("still describes the title for a screen reader, always", () => {
    // The half that is easy to lose. A description that exists only on hover
    // is a description assistive technology can never reach — and
    // `aria-describedby` cannot resolve to an element that is not in the
    // document, so unmounting it would break the wiring silently.
    render(<PageHeader title="Agents" subtitle={SUB} />);
    const described = title().getAttribute("aria-describedby");
    expect(described).toBeTruthy();
    expect(document.getElementById(described!)).toBe(tip());
    expect(tip().textContent).toBe(SUB);
    expect(tip().getAttribute("role")).toBe("tooltip");
  });
});

describe("three ways in, because there are three kinds of user", () => {
  it("HOVER opens it and leaving closes it", () => {
    render(<PageHeader title="Agents" subtitle={SUB} />);
    fireEvent.mouseEnter(title());
    expect(isOpen()).toBe(true);
    expect(tip().className).toContain("opacity-100");
    fireEvent.mouseLeave(title());
    expect(isOpen()).toBe(false);
  });

  it("FOCUS opens it, so it is not mouse-only", () => {
    // A keyboard user never fires mouseenter. The trigger is reachable by Tab
    // and announces the state it controls.
    render(<PageHeader title="Agents" subtitle={SUB} />);
    expect(title().getAttribute("tabindex")).toBe("0");
    expect(title().getAttribute("aria-expanded")).toBe("false");
    fireEvent.focus(title());
    expect(isOpen()).toBe(true);
    expect(title().getAttribute("aria-expanded")).toBe("true");
    fireEvent.blur(title());
    expect(isOpen()).toBe(false);
  });

  it("CLICK toggles it, so touch works at all", () => {
    // There is no hover on a phone. Without this the description would be
    // unreachable on exactly the devices with the least room for it.
    render(<PageHeader title="Agents" subtitle={SUB} />);
    fireEvent.click(title());
    expect(isOpen()).toBe(true);
    fireEvent.click(title());
    expect(isOpen()).toBe(false);
  });

  it("Enter and Space work on the trigger", () => {
    render(<PageHeader title="Agents" subtitle={SUB} />);
    fireEvent.keyDown(title(), { key: "Enter" });
    expect(isOpen()).toBe(true);
    fireEvent.keyDown(title(), { key: "Enter" });
    expect(isOpen()).toBe(false);
    fireEvent.keyDown(title(), { key: " " });
    expect(isOpen()).toBe(true);
  });
});

describe("it closes the ways an opened thing has to close", () => {
  it("Escape closes it", () => {
    render(<PageHeader title="Agents" subtitle={SUB} />);
    fireEvent.click(title());
    expect(isOpen()).toBe(true);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(isOpen()).toBe(false);
  });

  it("a click elsewhere closes it", () => {
    // Opened by a tap on touch, so it has to be closable by a tap past it.
    render(
      <div>
        <PageHeader title="Agents" subtitle={SUB} />
        <button type="button">somewhere else</button>
      </div>,
    );
    fireEvent.click(title());
    expect(isOpen()).toBe(true);
    fireEvent.mouseDown(screen.getByRole("button", { name: "somewhere else" }));
    expect(isOpen()).toBe(false);
  });

  it("binds no document listeners while closed", () => {
    // 38 pages carry this header. A pair of idle listeners on each is the kind
    // of cost that is invisible until it is not.
    const add = vi.spyOn(document, "addEventListener");
    render(<PageHeader title="Agents" subtitle={SUB} />);
    const idle = add.mock.calls.filter(([e]) => e === "keydown" || e === "mousedown");
    expect(idle).toHaveLength(0);
    fireEvent.click(title());
    const armed = add.mock.calls.filter(([e]) => e === "keydown" || e === "mousedown");
    expect(armed).toHaveLength(2);
    add.mockRestore();
  });
});

describe("a header with no subtitle", () => {
  it("is a plain title — no trigger, no popover, nothing to hover", () => {
    // Chat and Build pass none. They must not grow an affordance that would
    // open an empty box.
    render(<PageHeader title="Chat" />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Chat");
    expect(screen.queryByTestId("page-title")).toBeNull();
    expect(screen.queryByTestId("page-subtitle")).toBeNull();
  });

  it("keeps the actions slot working either way", () => {
    render(
      <PageHeader
        title="Agents"
        subtitle={SUB}
        actions={<button type="button">Do a thing</button>}
      />,
    );
    expect(screen.getByRole("button", { name: "Do a thing" })).toBeTruthy();
  });
});
