import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";

/**
 * v1.325.0 — RegenerateMenu: "Try again", or "Try again with…" another model.
 *
 * Pinned here:
 *  - the quick button still retries at once with the same model (no argument);
 *  - the menu: "Try again" first, then recent picks, then every other model
 *    (connected first, on this computer → included → metered), the current
 *    pick never listed twice; a filter box once the catalog is long;
 *  - a model that is not connected is listed, says why, and cannot be picked
 *    by click or keyboard;
 *  - THE PRIVACY LINE: only when the reply came from THIS computer and the row
 *    under the pointer / keyboard would send the conversation elsewhere;
 *  - keyboard: arrows move (skipping disabled rows), Enter picks, Esc closes
 *    and gives focus back; disabled = nothing opens, nothing retries.
 */

import { RegenerateMenu, privacyLine, answeredLocally, REGEN_FILTER_AT } from "@/components/chat/RegenerateMenu";
import type { ModelOption } from "@/lib/types";

const LOCAL: ModelOption = { provider: "fleet-1", model: "qwen3-8b", kind: "local", name: "Office fleet", available: true };
const LOCAL2: ModelOption = { provider: "fleet-1", model: "qwen3-32b", kind: "local", name: "Office fleet", available: true };
const SUB: ModelOption = { provider: "claude-cli", model: "opus", kind: "cli", name: "Claude Code", label: "Opus 5.5", available: true };
const KEYLESS: ModelOption = { provider: "anthropic", model: "claude-sonnet", kind: "api", inherited_from: "claude-cli", name: "Anthropic", available: true };
const API: ModelOption = { provider: "openai", model: "gpt-5", kind: "api", name: "OpenAI", available: true };
const OFF: ModelOption = { provider: "xai", model: "grok-5", kind: "api", name: "xAI", available: false };

const SMALL = [API, OFF, SUB, LOCAL, LOCAL2];
const v = (m: ModelOption) => `${m.provider}::${m.model}`;

afterEach(() => {
  cleanup();
});

function setup(over: Partial<Parameters<typeof RegenerateMenu>[0]> = {}) {
  const onRegenerate = vi.fn();
  const utils = render(
    <RegenerateMenu
      models={SMALL}
      recent={[]}
      current={v(LOCAL)}
      answeredBy={{ provider: "fleet-1", kind: "local" }}
      onRegenerate={onRegenerate}
      {...over}
    />,
  );
  return { onRegenerate, ...utils };
}

const openMenu = () =>
  act(async () => {
    fireEvent.click(screen.getByTestId("regen-more"));
  });
const items = () => screen.queryAllByTestId("regen-item");
const choices = () => items().map((el) => el.getAttribute("data-choice"));
const activeChoice = () => items().find((el) => el.getAttribute("data-active") === "true")?.getAttribute("data-choice");
const key = (k: string) =>
  act(async () => {
    fireEvent.keyDown(document.activeElement as Element, { key: k });
  });

describe("RegenerateMenu — the quick button", () => {
  it("retries with the same model at once (no argument), no menu", () => {
    const { onRegenerate } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRegenerate).toHaveBeenCalledTimes(1);
    expect(onRegenerate).toHaveBeenCalledWith();
    expect(screen.queryByTestId("regen-menu")).toBeNull();
  });

  it("carries the page's own label when given one", () => {
    setup({ quickLabel: "Regenerate reply" });
    expect(screen.getByRole("button", { name: "Regenerate reply" })).toBeInTheDocument();
  });

  it("disabled: neither button acts", async () => {
    const { onRegenerate } = setup({ disabled: true });
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await openMenu();
    expect(onRegenerate).not.toHaveBeenCalled();
    expect(screen.queryByTestId("regen-menu")).toBeNull();
  });

  it("a turn starting under an open menu closes it", async () => {
    const { rerender, onRegenerate } = setup();
    await openMenu();
    expect(screen.getByTestId("regen-menu")).toBeInTheDocument();
    rerender(
      <RegenerateMenu models={SMALL} recent={[]} current={v(LOCAL)} onRegenerate={onRegenerate} disabled />,
    );
    expect(screen.queryByTestId("regen-menu")).toBeNull();
  });
});

describe("RegenerateMenu — the menu", () => {
  it("'Try again' first, then the others: connected, local → included → metered, offline last; current not repeated", async () => {
    setup();
    await openMenu();
    const menu = screen.getByRole("menu");
    expect(within(menu).getByText("Try again with…")).toBeInTheDocument();
    expect(choices()).toEqual(["", v(LOCAL2), v(SUB), v(API), v(OFF)]);
    // The first row names the model it would ask again.
    expect(items()[0].textContent).toContain("same model · qwen3-8b");
    // The picker's label is what a row shows.
    expect(items()[2].textContent).toContain("Opus 5.5");
  });

  it("'Try again' calls with no argument; a model row calls with provider::model", async () => {
    const { onRegenerate } = setup();
    await openMenu();
    fireEvent.click(items()[0]);
    expect(onRegenerate).toHaveBeenLastCalledWith();
    expect(screen.queryByTestId("regen-menu")).toBeNull();
    await openMenu();
    fireEvent.click(items().find((el) => el.getAttribute("data-choice") === v(SUB)) as HTMLElement);
    expect(onRegenerate).toHaveBeenLastCalledWith(v(SUB));
  });

  it("recent picks come first under their own heading, and are not repeated below", async () => {
    setup({ recent: [v(API), v(LOCAL), "gone::model"] });
    await openMenu();
    // current (LOCAL) is the Try again row; an unknown recent is dropped.
    expect(choices()).toEqual(["", v(API), v(LOCAL2), v(SUB), v(OFF)]);
    expect(screen.getByText("Recent")).toBeInTheDocument();
    expect(screen.getByText("All models")).toBeInTheDocument();
  });

  it("the default model as current: every model is listed", async () => {
    setup({ current: "" });
    await openMenu();
    expect(items()[0].textContent).toContain("same model · default model");
    expect(choices()).toContain(v(LOCAL));
  });

  it("a model that is not connected says why and cannot be picked", async () => {
    const { onRegenerate } = setup();
    await openMenu();
    const off = items().find((el) => el.getAttribute("data-choice") === v(OFF)) as HTMLElement;
    expect(off.getAttribute("aria-disabled")).toBe("true");
    expect(off.textContent).toContain("not connected");
    expect(off.getAttribute("title")).toMatch(/xAI isn't connected/);
    fireEvent.click(off);
    expect(onRegenerate).not.toHaveBeenCalled();
    expect(screen.getByTestId("regen-menu")).toBeInTheDocument();
  });

  it("no filter box at the threshold; one past it, and typing narrows + Enter picks the first match", async () => {
    const many: ModelOption[] = Array.from({ length: REGEN_FILTER_AT }, (_, i) => ({
      provider: "openai",
      model: `m-${i}`,
      kind: "api" as const,
      available: true,
    }));
    const { unmount } = render(
      <RegenerateMenu models={many} recent={[]} current="" onRegenerate={() => {}} />,
    );
    await openMenu();
    expect(screen.queryByTestId("regen-filter")).toBeNull();
    unmount();

    const onRegenerate = vi.fn();
    render(
      <RegenerateMenu
        models={[...many, SUB]}
        recent={[]}
        current=""
        onRegenerate={onRegenerate}
      />,
    );
    await openMenu();
    const filter = screen.getByTestId("regen-filter");
    expect(document.activeElement).toBe(filter);
    await act(async () => {
      fireEvent.change(filter, { target: { value: "opus" } });
    });
    expect(choices()).toEqual(["", v(SUB)]);
    expect(activeChoice()).toBe(v(SUB));
    await key("Enter");
    expect(onRegenerate).toHaveBeenCalledWith(v(SUB));
  });

  it("a typed name that finds nothing says so, and Enter does not retry", async () => {
    const many: ModelOption[] = Array.from({ length: REGEN_FILTER_AT + 1 }, (_, i) => ({
      provider: "openai",
      model: `m-${i}`,
      kind: "api" as const,
    }));
    const onRegenerate = vi.fn();
    render(<RegenerateMenu models={many} recent={[]} current="" onRegenerate={onRegenerate} />);
    await openMenu();
    await act(async () => {
      fireEvent.change(screen.getByTestId("regen-filter"), { target: { value: "zzz" } });
    });
    expect(screen.getByText("No model matches.")).toBeInTheDocument();
    await key("Enter");
    expect(onRegenerate).not.toHaveBeenCalled();
  });
});

describe("RegenerateMenu — keyboard", () => {
  it("arrows move and skip a disabled row (wrapping), Enter picks, Esc closes and refocuses", async () => {
    const { onRegenerate } = setup();
    await openMenu();
    expect(document.activeElement).toBe(screen.getByRole("menu"));
    expect(activeChoice()).toBe("");
    await key("ArrowDown");
    expect(activeChoice()).toBe(v(LOCAL2));
    await key("ArrowDown");
    await key("ArrowDown");
    expect(activeChoice()).toBe(v(API));
    await key("ArrowDown"); // OFF is disabled → wraps to "Try again"
    expect(activeChoice()).toBe("");
    await key("ArrowUp"); // and back up past OFF
    expect(activeChoice()).toBe(v(API));
    expect(screen.getByRole("menu").getAttribute("aria-activedescendant")).toBe(
      items().find((el) => el.getAttribute("data-active") === "true")?.id,
    );
    await key("Enter");
    expect(onRegenerate).toHaveBeenCalledWith(v(API));

    await openMenu();
    await key("Escape");
    expect(screen.queryByTestId("regen-menu")).toBeNull();
    expect(document.activeElement).toBe(screen.getByTestId("regen-more"));
  });

  it("Esc inside the menu never reaches the page", async () => {
    const pageKeys = vi.fn();
    window.addEventListener("keydown", pageKeys);
    setup();
    await openMenu();
    await key("Escape");
    window.removeEventListener("keydown", pageKeys);
    expect(pageKeys).not.toHaveBeenCalled();
  });

  it("a press outside closes it", async () => {
    setup();
    await openMenu();
    await act(async () => {
      fireEvent.mouseDown(document.body);
    });
    expect(screen.queryByTestId("regen-menu")).toBeNull();
  });
});

describe("RegenerateMenu — the privacy line", () => {
  const LINE = /This sends the conversation to Claude Code — it leaves this computer\./;

  it("a local reply + hover on a non-local model: one plain line, tied to the row", async () => {
    setup();
    await openMenu();
    expect(screen.queryByTestId("regen-privacy")).toBeNull(); // "Try again" stays put
    const sub = items().find((el) => el.getAttribute("data-choice") === v(SUB)) as HTMLElement;
    fireEvent.mouseEnter(sub);
    const note = screen.getByTestId("regen-privacy");
    expect(note.textContent).toMatch(LINE);
    expect(sub.getAttribute("aria-describedby")).toBe(note.id);
  });

  it("keyboard focus shows it too, and a local row shows none", async () => {
    setup();
    await openMenu();
    await key("ArrowDown"); // LOCAL2 — stays on this computer
    expect(screen.queryByTestId("regen-privacy")).toBeNull();
    await key("ArrowDown"); // SUB
    expect(screen.getByTestId("regen-privacy").textContent).toMatch(LINE);
  });

  it("a cloud reply shows none, even on a cloud row", async () => {
    setup({ answeredBy: { provider: "openai", kind: "api" }, current: v(API) });
    await openMenu();
    fireEvent.mouseEnter(items().find((el) => el.getAttribute("data-choice") === v(SUB)) as HTMLElement);
    expect(screen.queryByTestId("regen-privacy")).toBeNull();
  });

  it("no answeredBy: none (the menu never claims where a reply came from)", async () => {
    setup({ answeredBy: undefined });
    await openMenu();
    fireEvent.mouseEnter(items().find((el) => el.getAttribute("data-choice") === v(SUB)) as HTMLElement);
    expect(screen.queryByTestId("regen-privacy")).toBeNull();
  });

  it("answeredBy without kind is read off the catalog", () => {
    expect(answeredLocally({ provider: "fleet-1" }, SMALL)).toBe(true);
    expect(answeredLocally({ provider: "openai" }, SMALL)).toBe(false);
    expect(answeredLocally({ provider: "unknown" }, SMALL)).toBe(false);
    expect(answeredLocally({ provider: "openai", kind: "local" }, SMALL)).toBe(true);
  });

  it("an inherited (subscription) row and a row of unknown location both count as leaving", () => {
    expect(privacyLine(KEYLESS, true)).toBe(
      "This sends the conversation to Anthropic — it leaves this computer.",
    );
    expect(privacyLine({ provider: "mystery", model: "x" }, true)).toMatch(/leaves this computer/);
    expect(privacyLine(LOCAL2, true)).toBe("");
    expect(privacyLine(SUB, false)).toBe("");
    expect(privacyLine(undefined, true)).toBe("");
  });
});
