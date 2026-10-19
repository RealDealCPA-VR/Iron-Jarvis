/**
 * v1.330.0 — calm chat wave 10, L2: the last boxed shared pieces go calm.
 *
 * In Settings > Connections the saved-server Delete, Disconnect and the
 * Iron-Proxy account Remove (ConfirmButton) and the status chips (Badge) were
 * still bordered boxes, the last pre-calm pieces on a calm page. Both now take
 * `variant="calm"`:
 *  - ConfirmButton calm: a quiet ghost, no border, fills on hover, the armed
 *    step reads in tone-danger; still a real <button>, still two presses, and
 *    the 3 s disarm is the same timer as before.
 *  - Badge calm: the quiet chip the endpoint rows use, with no border and no
 *    fill: neutral words, and only the small dot carries the tone token.
 * v1.356.0 (G80): calm became THE DEFAULT for both; the old bordered looks are
 * `variant="bordered"` (byte-identical), pinned here too. The source guard that
 * keeps a bordered one off the Connections page lives in
 * connections-fleet-calm-v1329.test.tsx.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const hooks = vi.hoisted(() => ({ integrations: [] as unknown[] }));

vi.mock("@/lib/api", () => ({
  ApiError: class extends Error {},
  post: vi.fn(() => Promise.resolve({ ok: true })),
}));
vi.mock("@/lib/useApi", () => ({
  useApi: () => ({ data: { integrations: hooks.integrations }, error: null, loading: false, reload: () => {} }),
}));

import { Badge, CALM_BADGE, CALM_CONFIRM, ConfirmButton } from "@/components/ui";
import { RestHookups } from "@/components/connections/RestHookups";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const tokens = (el: Element) => (el.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);
/** A border of any kind: `border`, `border-x`, `border-white/10`,
 *  `hover:border-…`. (`rounded-*` is not a border.) */
const BORDER = /^(?:[a-z-]+:)*border(?:-|$)/;
const LITERAL_HUE = /(?:emerald|amber|rose|red|green|yellow|cyan|sky|blue|violet|purple|pink|orange|lime|teal|indigo|fuchsia|slate|gray|stone|neutral)-\d{2,3}/;
const HALF_PIXEL = /text-\[\d+\.5px\]/;

/* ===================================================== ConfirmButton calm */

describe("ConfirmButton variant=\"calm\": a quiet ghost with the same two presses", () => {
  it("is a real button: type=button, focusable, no border, fills on hover, a focus ring", () => {
    render(<ConfirmButton variant="calm" onConfirm={() => {}} label="Delete" />);
    const btn = screen.getByRole("button", { name: "Delete" });
    expect(btn.tagName).toBe("BUTTON");
    expect(btn.getAttribute("type")).toBe("button");
    expect((btn as HTMLButtonElement).disabled).toBe(false);
    btn.focus();
    expect(document.activeElement).toBe(btn);

    const cls = tokens(btn);
    expect(cls.filter((c) => BORDER.test(c))).toEqual([]);
    expect(cls).toContain("hover:bg-white/[0.06]");
    expect(cls).toContain("focus-visible:ring-1");
    // A finger can hit it: at least 28 px tall.
    expect(cls).toContain("min-h-7");
    // Theme tokens only, whole pixels.
    expect(btn.className).not.toMatch(LITERAL_HUE);
    expect(btn.className).not.toMatch(HALF_PIXEL);
    expect(btn.getAttribute("data-confirm-variant")).toBe("calm");
    expect(btn.getAttribute("data-armed")).toBe("false");
  });

  it("the first press only arms it; the armed step reads in tone-danger; the second press runs it once", async () => {
    const onConfirm = vi.fn();
    render(<ConfirmButton variant="calm" onConfirm={onConfirm} label="Delete" confirmLabel="Delete?" />);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onConfirm).not.toHaveBeenCalled();

    const armed = screen.getByRole("button", { name: "Delete?" });
    expect(armed.getAttribute("data-armed")).toBe("true");
    const cls = tokens(armed);
    expect(cls).toContain("text-tone-danger");
    expect(cls.filter((c) => BORDER.test(c))).toEqual([]);
    expect(armed.className).not.toMatch(LITERAL_HUE);

    await act(async () => {
      fireEvent.click(armed);
    });
    expect(onConfirm).toHaveBeenCalledTimes(1);
    // It disarms after running.
    const back = screen.getByRole("button", { name: "Delete" });
    expect(back.getAttribute("data-armed")).toBe("false");
    expect(tokens(back)).not.toContain("text-tone-danger");
  });

  it("disarms by itself after 3 s, and a press after that only arms again", () => {
    vi.useFakeTimers();
    const onConfirm = vi.fn();
    render(<ConfirmButton variant="calm" onConfirm={onConfirm} label="Remove" confirmLabel="Press again to remove" />);
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(screen.getByRole("button", { name: "Press again to remove" })).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(2900);
    });
    // Still armed just before the 3 s mark (the timer is the old one).
    expect(screen.getByRole("button", { name: "Press again to remove" })).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(200);
    });
    const idle = screen.getByRole("button", { name: "Remove" });
    expect(idle.getAttribute("data-armed")).toBe("false");

    fireEvent.click(idle);
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Press again to remove" })).toBeTruthy();
  });

  it("is disabled while the action runs, then comes back", async () => {
    let finish: () => void = () => {};
    const onConfirm = vi.fn(() => new Promise<void>((r) => (finish = r)));
    render(<ConfirmButton variant="calm" onConfirm={onConfirm} label="Delete" confirmLabel="Delete?" />);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Delete?" }));
    });
    const busy = screen.getByRole("button");
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    // A press while busy does nothing.
    fireEvent.click(busy);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    await act(async () => {
      finish();
    });
    expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
  });

  it("keeps the caller's className (the Connections rows pass whitespace-nowrap / shrink-0)", () => {
    render(<ConfirmButton variant="calm" onConfirm={() => {}} label="Disconnect" className="whitespace-nowrap py-1.5" />);
    const cls = tokens(screen.getByRole("button", { name: "Disconnect" }));
    expect(cls).toContain("whitespace-nowrap");
    expect(cls).toContain("py-1.5");
  });

  it("the exported look strings hold the same rules", () => {
    for (const s of [CALM_CONFIRM.base, CALM_CONFIRM.idle, CALM_CONFIRM.armed]) {
      expect(s.split(/\s+/).filter((c) => BORDER.test(c))).toEqual([]);
      expect(s).not.toMatch(LITERAL_HUE);
    }
    expect(CALM_CONFIRM.armed).toContain("text-tone-danger");
  });
});

// v1.356.0 (G80, updated on purpose): calm is THE DEFAULT now. The old
// hairline-bordered button is `variant="bordered"`, byte-identical to what
// the default drew before (kept for Build's boxed close dialog).
describe("ConfirmButton: calm by default, the old bordered look on request", () => {
  it("variant=\"bordered\" is still the hairline-bordered button, with no calm data attributes", () => {
    render(<ConfirmButton variant="bordered" onConfirm={() => {}} label="Delete" />);
    const btn = screen.getByRole("button", { name: "Delete" });
    expect(btn.className).toBe(
      "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-50 border-white/10 text-zinc-400 hover:border-tone-danger/30 hover:text-tone-danger ",
    );
    expect(btn.hasAttribute("data-confirm-variant")).toBe(false);
    expect(btn.hasAttribute("data-armed")).toBe(false);
    fireEvent.click(btn);
    const armed = screen.getByRole("button", { name: "Confirm?" });
    expect(tokens(armed)).toContain("border-tone-danger/50");
  });

  it("no variant (and variant=\"default\") draws the calm ghost, exactly variant=\"calm\"'s classes", () => {
    render(<ConfirmButton variant="calm" onConfirm={() => {}} label="Calm" />);
    const calm = screen.getByRole("button", { name: "Calm" }).className;
    cleanup();
    for (const el of [
      <ConfirmButton key="none" onConfirm={() => {}} label="Calm" />,
      <ConfirmButton key="default" variant="default" onConfirm={() => {}} label="Calm" />,
    ]) {
      render(el);
      const btn = screen.getByRole("button", { name: "Calm" });
      expect(btn.className).toBe(calm);
      expect(btn.getAttribute("data-confirm-variant")).toBe("calm");
      expect(tokens(btn).filter((c) => BORDER.test(c))).toEqual([]);
      cleanup();
    }
  });
});

/* ============================================================== Badge calm */

describe("Badge variant=\"calm\": no border, no fill, only the dot carries the tone", () => {
  const CASES: Array<[Parameters<typeof Badge>[0]["tone"], string]> = [
    ["green", "bg-tone-success"],
    ["amber", "bg-tone-warn"],
    ["red", "bg-tone-danger"],
    ["cyan", "bg-accent"],
    ["violet", "bg-tone-violet"],
    ["slate", "bg-zinc-500"],
  ];

  it.each(CASES)("tone %s: a quiet shell, the dot is %s with no glow", (tone, dot) => {
    render(<Badge variant="calm" value="Needs sign-in" tone={tone} keepCase />);
    const chip = screen.getByText("Needs sign-in");
    expect(chip.getAttribute("data-badge-variant")).toBe("calm");
    const cls = tokens(chip);
    expect(cls.filter((c) => BORDER.test(c))).toEqual([]);
    // No fill: the words are neutral, never a tinted pill.
    expect(cls.filter((c) => /^bg-/.test(c))).toEqual([]);
    expect(cls).toContain("text-zinc-400");
    expect(cls).toContain("text-[11px]");
    expect(chip.className).not.toMatch(LITERAL_HUE);
    expect(chip.className).not.toMatch(HALF_PIXEL);

    const mark = chip.querySelector("span") as HTMLElement;
    expect(mark.getAttribute("aria-hidden")).toBe("true");
    expect(tokens(mark)).toContain(dot);
    expect(mark.className).not.toMatch(/shadow/);
    cleanup();
  });

  it("capitalises a status word unless keepCase, like the default", () => {
    render(<Badge variant="calm" value="ready" />);
    expect(tokens(screen.getByText("ready"))).toContain("capitalize");
    cleanup();
    render(<Badge variant="calm" value="Not set up yet" keepCase />);
    expect(tokens(screen.getByText("Not set up yet"))).not.toContain("capitalize");
  });

  it("the exported shell is the same quiet chip", () => {
    expect(CALM_BADGE.split(/\s+/).filter((c) => BORDER.test(c))).toEqual([]);
    expect(CALM_BADGE).not.toMatch(/(?:^|\s)bg-/);
  });

  // v1.356.0 (G80, updated on purpose): the default Badge IS the calm chip;
  // the bordered, tinted pill is `variant="bordered"`, unchanged.
  it("variant=\"bordered\" is the old bordered, tinted pill", () => {
    render(<Badge variant="bordered" value="completed" />);
    const chip = screen.getByText("completed");
    expect(chip.hasAttribute("data-badge-variant")).toBe(false);
    const cls = tokens(chip);
    expect(cls).toContain("border");
    expect(cls).toContain("bg-tone-success/10");
  });

  it("no variant (and variant=\"default\") draws the calm chip, exactly variant=\"calm\"'s", () => {
    render(<Badge variant="calm" value="completed" />);
    const calm = screen.getByText("completed").outerHTML;
    cleanup();
    render(<Badge value="completed" />);
    expect(screen.getByText("completed").outerHTML).toBe(calm);
    cleanup();
    render(<Badge variant="default" value="completed" />);
    expect(screen.getByText("completed").outerHTML).toBe(calm);
  });
});

/* ============================================ Connections: REST hookups */

describe("Settings > Connections: the REST hookup state chips are calm", () => {
  it("Ready / Off / Not set up yet draw the calm Badge", () => {
    hooks.integrations = [
      { id: "crm", kind: "rest", display_name: "Harbor CRM sandbox", enabled: true, configured: true, required_secrets: [] },
      { id: "spare", kind: "rest", display_name: "Spare hookup", enabled: false, configured: true, required_secrets: [] },
      { id: "rest_api", kind: "rest", display_name: "Generic REST API", enabled: false, configured: false, required_secrets: [] },
    ];
    render(<RestHookups />);
    for (const [name, word] of [
      ["Harbor CRM sandbox", "Ready"],
      ["Spare hookup", "Off"],
      ["Generic REST API", "Not set up yet"],
    ]) {
      const card = screen.getByText(name).closest("section") as HTMLElement;
      const chip = within(card).getByText(word);
      expect(chip.getAttribute("data-badge-variant"), name).toBe("calm");
      expect(tokens(chip).filter((c) => BORDER.test(c)), name).toEqual([]);
    }
    const ready = screen.getByText("Ready").querySelector("span") as HTMLElement;
    expect(tokens(ready)).toContain("bg-tone-success");
  });
});
