import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Pencil, Trash2 } from "lucide-react";

/**
 * v1.447.0 (the owner: "run a sweep across the UI for a clean calm look …
 * things that are better suited to be icons than words, but only … if it
 * would not confuse a first time user").
 *
 * Pinned here:
 *  - `IconButton` (components/IconButton.tsx): its accessible name is the
 *    words it replaced; a tip with words shows after the pointer rests
 *    (ICON_TIP_DELAY_MS) or at once on keyboard focus, and is NOT in the
 *    page at rest; Escape and leaving hide it;
 *  - `ConfirmButton icon=…`: the first press is the icon alone, named
 *    `label`, with its tip; the second press says `confirmLabel` in words and
 *    only that one runs the action — still two presses;
 *  - every place the sweep turned words into an icon still names the button
 *    with the old words and gives it a tip (read from the source, so a revert
 *    to the worded button, or a missing name, goes red);
 *  - form labels, section labels and stat labels are sentence case (no
 *    shouted, tracked capitals).
 */

import { ICON_TIP_DELAY_MS, IconButton } from "@/components/IconButton";
import { ConfirmButton, Field, SectionLabel, Stat } from "@/components/ui";

beforeAll(() => {
  // jsdom has no PointerEvent: React reads `pointerType` off the native event.
  if (typeof window.PointerEvent === "undefined") {
    class FakePointerEvent extends MouseEvent {
      pointerType: string;
      constructor(type: string, init: PointerEventInit = {}) {
        super(type, init);
        this.pointerType = init.pointerType ?? "mouse";
      }
    }
    (window as unknown as { PointerEvent: unknown }).PointerEvent = FakePointerEvent;
  }
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const ROOT = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(ROOT, rel), "utf8").replace(/\r\n/g, "\n");

describe("IconButton: named in words, a tip on a pause or on focus", () => {
  it("is named by its label and shows no tip at rest", () => {
    render(<IconButton label="Edit" tip="Edit template" testId="t-edit" icon={<Pencil size={14} aria-hidden />} />);
    const b = screen.getByRole("button", { name: "Edit" });
    expect(b).toHaveAttribute("type", "button");
    expect(b.textContent).toBe("");
    expect(screen.queryByTestId("t-edit-tip")).toBeNull();
  });

  it("shows the tip only after the pointer rests, and hides it when the pointer leaves", () => {
    vi.useFakeTimers();
    render(<IconButton label="Edit" tip="Edit template" testId="t-edit" icon={<Pencil size={14} aria-hidden />} />);
    const b = screen.getByRole("button", { name: "Edit" });
    fireEvent.pointerEnter(b, { pointerType: "mouse" });
    act(() => {
      vi.advanceTimersByTime(ICON_TIP_DELAY_MS - 50);
    });
    expect(screen.queryByTestId("t-edit-tip")).toBeNull();
    act(() => {
      vi.advanceTimersByTime(60);
    });
    const tip = screen.getByTestId("t-edit-tip");
    expect(tip.textContent).toBe("Edit template");
    expect(tip).toHaveAttribute("aria-hidden", "true");
    // Portaled to <body>, never inside the button.
    expect(b.contains(tip)).toBe(false);
    fireEvent.pointerLeave(b, { pointerType: "mouse" });
    expect(screen.queryByTestId("t-edit-tip")).toBeNull();
  });

  it("shows the tip at once on keyboard focus; Escape and blur hide it", () => {
    render(<IconButton label="Refresh" testId="t-ref" icon={<span aria-hidden>↻</span>} />);
    const b = screen.getByRole("button", { name: "Refresh" });
    // jsdom answers `:focus-visible` for ANY focus; a browser does not after a
    // mouse press. Answer as a browser would, so the keyboard rule is tested.
    const realMatches = Element.prototype.matches;
    vi.spyOn(Element.prototype, "matches").mockImplementation(function (this: Element, sel: string) {
      return sel === ":focus-visible" ? false : realMatches.call(this, sel);
    });
    // A mouse press is not a keyboard focus: no tip.
    fireEvent.pointerDown(document.body, { pointerType: "mouse" });
    act(() => b.focus());
    expect(screen.queryByTestId("t-ref-tip")).toBeNull();
    act(() => b.blur());
    // The keyboard brings focus here: the tip shows at once.
    fireEvent.keyDown(document, { key: "Tab" });
    act(() => b.focus());
    expect(screen.getByTestId("t-ref-tip").textContent).toBe("Refresh");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("t-ref-tip")).toBeNull();
    act(() => b.blur());
    act(() => b.focus());
    expect(screen.getByTestId("t-ref-tip")).toBeInTheDocument();
    act(() => b.blur());
    expect(screen.queryByTestId("t-ref-tip")).toBeNull();
  });

  it("a press runs its action and hides the tip", () => {
    const onClick = vi.fn();
    render(<IconButton label="Copy" testId="t-copy" icon={<span aria-hidden>c</span>} onClick={onClick} />);
    const b = screen.getByRole("button", { name: "Copy" });
    act(() => b.focus());
    fireEvent.pointerDown(b, { pointerType: "mouse" });
    fireEvent.click(b);
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("t-copy-tip")).toBeNull();
  });
});

describe("ConfirmButton with an icon: a trash can first, words second, two presses", () => {
  it("first press is the icon named by its label; it arms; the second press (in words) runs it", async () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmButton
        label="Delete"
        confirmLabel="Delete?"
        title="Delete template X"
        testId="t-del"
        icon={<Trash2 size={14} aria-hidden />}
        onConfirm={onConfirm}
      />,
    );
    const b = screen.getByRole("button", { name: "Delete" });
    expect(b.textContent).toBe("");
    fireEvent.keyDown(document, { key: "Tab" });
    act(() => b.focus());
    expect(screen.getByTestId("t-del-tip").textContent).toBe("Delete template X");
    fireEvent.click(b);
    expect(onConfirm).not.toHaveBeenCalled();
    const armed = screen.getByRole("button", { name: "Delete?" });
    expect(armed).toHaveAttribute("data-armed", "true");
    expect(screen.queryByTestId("t-del-tip")).toBeNull();
    await act(async () => {
      fireEvent.click(armed);
    });
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Delete" })).toHaveAttribute("data-armed", "false");
  });

  it("without an icon it is the worded button, exactly as before", () => {
    render(<ConfirmButton label="Remove" onConfirm={() => {}} />);
    expect(screen.getByRole("button", { name: "Remove" }).textContent).toBe("Remove");
  });
});

describe("labels are sentence case, never shouted", () => {
  it("Field, SectionLabel and Stat carry no uppercase or tracking class", () => {
    render(
      <div>
        <Field label="Name">
          <input />
        </Field>
        <SectionLabel>Several accounts</SectionLabel>
        <Stat label="Total cost" value="$1" />
      </div>,
    );
    for (const word of ["Name", "Several accounts", "Total cost"]) {
      const el = screen.getByText(word);
      expect(el.className, word).not.toMatch(/(^|\s)uppercase(\s|$)/);
      expect(el.className, word).not.toMatch(/tracking-/);
    }
  });

  it("no hand-copied form label in the swept files shouts any more", () => {
    const files = [
      "app/schedules/page.tsx",
      "app/projects/page.tsx",
      "app/documents/page.tsx",
      "app/reflex/page.tsx",
      "app/sentinels/page.tsx",
      "app/webhooks/page.tsx",
      "components/memory/LongTerm.tsx",
      "components/settings/pages/NotificationsPage.tsx",
      "components/settings/pages/ToolsPage.tsx",
      "components/settings/pages/SecretsPage.tsx",
    ];
    for (const f of files) {
      expect(read(f), f).not.toContain('text-[11px] uppercase tracking-[0.1em] text-zinc-400"');
    }
  });

  it("the browser settings' facts are quiet label-value pairs, not boxed capitals", () => {
    const src = read("components/settings/pages/BrowserPage.tsx");
    const chip = src.slice(src.indexOf("function Chip("), src.indexOf("function Chip(") + 600);
    expect(chip).not.toMatch(/uppercase/);
    expect(chip).not.toMatch(/rounded-xl border/);
  });

  it("the demo strip's sentence keeps a real width on a phone (buttons wrap under it)", () => {
    const src = read("components/SimulatedBanner.tsx");
    expect(src.match(/<p className="min-w-\[12rem\] flex-1 text-\[13px\]/g) ?? []).toHaveLength(3);
    expect(src).not.toContain('<p className="min-w-0 flex-1 text-[13px]');
  });

  it("the sidebar, chat-group, Everything and Settings headings are sentence case", () => {
    expect(read("components/AppSidebar.tsx")).not.toMatch(/uppercase tracking-wide text-zinc-500/);
    expect(read("components/chat/ThreadGroups.tsx")).not.toMatch(/uppercase tracking-\[0\.06em\]/);
    expect(read("app/everything/page.tsx")).not.toMatch(/font-semibold uppercase tracking-wide/);
    expect(read("components/settings/SettingsHome.tsx")).not.toMatch(/font-semibold uppercase tracking-wide/);
  });
});

/** Every icon the sweep introduced: the file, the words the button is still
 *  named by (aria-label via `label=`), the tip, and the worded button it
 *  replaced (which must be gone). */
const SITES: { file: string; label: string; tip: RegExp; old: string }[] = [
  {
    file: "app/templates/page.tsx",
    label: 'label="Edit"',
    tip: /tip=\{`Edit template "\$\{t\.name\}"`\}/,
    old: "<Pencil size={12} /> Edit",
  },
  {
    file: "app/templates/page.tsx",
    label: 'label="Delete"\n                            confirmLabel="Delete?"\n                            icon={<Trash2',
    tip: /title=\{`Delete template "\$\{t\.name\}"`\}/,
    old: 'label="Delete"\n                            title=',
  },
  {
    file: "components/settings/pages/NotificationsPage.tsx",
    label: 'label="Edit"\n                          tip={`Edit ${c.name}`}',
    tip: /tip=\{`Edit \$\{c\.name\}`\}/,
    old: "<Pencil size={13} /> Edit\n",
  },
  {
    file: "components/settings/pages/NotificationsPage.tsx",
    label: 'confirmLabel="Delete?"\n                          icon={<Trash2',
    tip: /title=\{`Delete destination \$\{c\.name\}`\}/,
    old: "onConfirm={() => deleteChannel(c.name)}\n                          title=",
  },
  {
    file: "components/settings/pages/ToolsPage.tsx",
    label: 'label="Edit"\n                          tip={`Edit "${s.name}"`}\n                          testId={`mcp-edit-${s.name}`}',
    tip: /tip=\{`Edit "\$\{s\.name\}"`\}/,
    old: "<Pencil size={12} /> Edit",
  },
  {
    file: "components/settings/pages/ToolsPage.tsx",
    label: 'label="Remove"\n                          confirmLabel="Remove?"\n                          icon={<Trash2',
    tip: /title=\{`Remove "\$\{s\.name\}" and its saved settings/,
    old: 'label="Remove"\n                          title={`Remove "${s.name}"',
  },
  {
    file: "components/settings/pages/ToolsPage.tsx",
    label: 'label="Delete"\n                      confirmLabel="Delete?"\n                      icon={<Trash2',
    tip: /title=\{`Delete tool "\$\{tool\.name\}"`\}/,
    old: 'label="Delete"\n                      title={`Delete tool',
  },
  {
    file: "components/workflow/SavedWorkflows.tsx",
    label: "label={`Delete ${d.name}`}",
    tip: /tip=\{`Delete “\$\{d\.name\}”`\}/,
    old: "<Trash2 size={13} /> Delete\n",
  },
  {
    file: "app/fleet/page.tsx",
    label: 'label="Refresh"',
    tip: /tip="Force a fresh probe of every node"/,
    old: "Refresh\n              </button>",
  },
  {
    file: "app/usage/page.tsx",
    label: 'label="Refresh"',
    tip: /tip="Reload usage data"/,
    old: "Refresh\n              </button>",
  },
  {
    file: "components/browser/YourBrowserCard.tsx",
    label: "label={`Copy ${folder}`}",
    tip: /tip="Copy"/,
    old: "<ClipboardCopy size={12} /> Copy",
  },
  {
    file: "components/browser/BrowserSetupModal.tsx",
    label: "label={`Copy ${folder}`}",
    tip: /tip="Copy"/,
    old: "<ClipboardCopy size={12} /> Copy",
  },
  {
    file: "components/settings/BrainProfiles.tsx",
    label: 'label="Edit"\n                      tip={`Edit ${p.name}`}',
    tip: /tip=\{`Edit \$\{p\.name\}`\}/,
    old: "                      Edit\n                    </button>",
  },
  {
    file: "components/settings/BrainProfiles.tsx",
    label: 'label="Delete"\n                      confirmLabel="Delete this profile?"\n                      icon={<Trash2',
    tip: /title=\{`Delete \$\{p\.name\}`\}/,
    old: 'confirmLabel="Delete this profile?"\n                      onConfirm',
  },
];

describe("every icon the sweep introduced keeps its words as its name and a tip", () => {
  for (const s of SITES) {
    it(`${s.file}: ${s.label.split("\n")[0]}`, () => {
      const src = read(s.file);
      expect(src).toContain(s.label);
      expect(src).toMatch(s.tip);
      expect(src).not.toContain(s.old);
    });
  }

  it("ContinueStrip's three dismisses are a × named Dismiss (never the word on screen)", () => {
    const src = read("components/terminal/ContinueStrip.tsx");
    const icons = src.match(/<IconButton\n\s*label="Dismiss"\n\s*tone="warn"/g) ?? [];
    expect(icons).toHaveLength(3);
    expect(src).not.toMatch(/>\n\s*Dismiss\n\s*<\/button>/);
  });
});
