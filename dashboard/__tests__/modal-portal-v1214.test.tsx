import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * THE POPUP THAT WAS NOT ACTUALLY FIXED TO THE VIEWPORT (v1.214.0).
 *
 * Reported verbatim: the add-agent "+" popup "is bound by the size of the
 * thread (chat window) and on a small card doesn't show everthing from this
 * pop up".
 *
 * THE DIAGNOSIS, because it is not a sizing mistake and the fix makes no sense
 * without it. `PanelPicker` was `fixed inset-0`, which everyone reads as "the
 * viewport" — and it was not, because of WHERE IT WAS RENDERED. It is returned
 * from inside `RoundTable`, whose root is
 *
 *     <div class="card-surface flex min-w-0 flex-col overflow-hidden">
 *
 * and `.card-surface` carries `backdrop-filter: blur(18px) saturate(150%)`
 * (globals.css). Per CSS Filter Effects, an element with a `backdrop-filter`
 * other than `none` becomes the CONTAINING BLOCK for its fixed-position
 * descendants — the same rule `transform`, `perspective`, `filter` and
 * `contain: paint` have. So `inset-0` resolved to the thread card's box, and
 * the card's own `overflow-hidden` clipped whatever did not fit: on a short
 * card, the footer with the Save button.
 *
 * There is no fix from inside the subtree — an element cannot opt out of an
 * ancestor's containing block. It has to LEAVE, so `components/Modal.tsx`
 * renders through a portal into `document.body`.
 *
 * WHY THESE TESTS ASSERT THE PORTAL AND NOT THE PIXELS. jsdom computes no
 * layout, so "is it clipped" is not a question it can answer; the escape is
 * the mechanism, and the mechanism is what a regression would undo. A modal
 * whose DOM parent is the page again is the bug, whatever its CSS says.
 */

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError,
    API_BASE: "http://test",
    ijToken: () => "tok-1",
    get: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    post: () => Promise.resolve({}),
  };
});

import { Modal } from "@/components/Modal";
import { PanelPicker } from "@/components/agents/PanelPicker";

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
});

/** The exact shape the bug lived in: a `.card-surface` with `overflow-hidden`,
 *  which is what every panel in this app is. */
function InACard({ children }: { children: React.ReactNode }) {
  return (
    <div data-testid="host-card" className="card-surface overflow-hidden">
      {children}
    </div>
  );
}

describe("Modal — it leaves the card it was rendered from", () => {
  it("mounts into document.body, not into the page subtree", () => {
    render(
      <InACard>
        <Modal label="Test dialog" onClose={vi.fn()}>
          <p>body</p>
        </Modal>
      </InACard>,
    );
    const dialog = screen.getByRole("dialog");
    // The overlay is the dialog's parent, and the overlay's parent is <body>.
    expect(dialog.parentElement?.parentElement).toBe(document.body);
    // ...and, the whole point, NOT inside the card that would have sized and
    // clipped it.
    expect(screen.getByTestId("host-card").contains(dialog)).toBe(false);
  });

  it("carries its own accessible name and modal semantics", () => {
    render(<Modal label="Test dialog" onClose={vi.fn()}>body</Modal>);
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-label")).toBe("Test dialog");
  });

  it("closes on Escape and on the backdrop, but not on its own body", () => {
    const onClose = vi.fn();
    render(
      <Modal label="Test dialog" onClose={onClose} testId="m">
        <p>body</p>
      </Modal>,
    );
    fireEvent.click(screen.getByText("body"));
    expect(onClose).not.toHaveBeenCalled(); // the click must not bubble out
    fireEvent.click(screen.getByTestId("m"));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("cannot be dismissed out from under a request in flight", () => {
    const onClose = vi.fn();
    render(
      <Modal label="Test dialog" onClose={onClose} busy testId="m">
        <p>body</p>
      </Modal>,
    );
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(screen.getByTestId("m"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("locks the page behind it, and restores what it found", () => {
    // With the overlay out in <body>, a wheel over the backdrop scrolls the
    // page behind it — which reads as the dialog sliding away.
    document.body.style.overflow = "scroll";
    const { unmount } = render(
      <Modal label="Test dialog" onClose={vi.fn()}>body</Modal>,
    );
    expect(document.body.style.overflow).toBe("hidden");
    unmount();
    // RESTORED, not cleared: the portrait cropper opens over the agents room,
    // and the inner one's unmount must not unlock the page under the outer.
    expect(document.body.style.overflow).toBe("scroll");
  });

  it("keeps a stacked dialog's lock when the inner one closes", () => {
    const { rerender } = render(
      <>
        <Modal label="Outer" onClose={vi.fn()}>outer</Modal>
        <Modal label="Inner" onClose={vi.fn()}>inner</Modal>
      </>,
    );
    expect(document.body.style.overflow).toBe("hidden");
    rerender(
      <>
        <Modal label="Outer" onClose={vi.fn()}>outer</Modal>
      </>,
    );
    expect(document.body.style.overflow).toBe("hidden");
  });
});

describe("the add-agent picker — the popup from the report", () => {
  const catalog = {
    builtin: [{ source: "builtin" as const, name: "builder" }],
    dynamic: [{ source: "dynamic" as const, name: "analyst" }],
    remotes: [],
  };

  it("escapes the thread card it is rendered from", async () => {
    // THE REGRESSION GUARD. Rendered exactly where RoundTable renders it —
    // inside a `.card-surface` with `overflow-hidden` — the picker must not be
    // a descendant of that card.
    render(
      <InACard>
        <PanelPicker
          mode="edit"
          catalog={catalog}
          initialParticipants={[]}
          onClose={vi.fn()}
          onSubmit={vi.fn()}
        />
      </InACard>,
    );
    const dialog = await screen.findByRole("dialog");
    expect(screen.getByTestId("host-card").contains(dialog)).toBe(false);
    expect(dialog.parentElement?.parentElement).toBe(document.body);
  });

  it("still shows the part that used to be cut off — the footer's action", async () => {
    // "on a small card doesn't show everthing from this pop up": what fell off
    // the bottom was the footer, i.e. the button the dialog exists for.
    render(
      <InACard>
        <PanelPicker
          mode="create"
          catalog={catalog}
          initialParticipants={[]}
          onClose={vi.fn()}
          onSubmit={vi.fn()}
        />
      </InACard>,
    );
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByRole("button", { name: /Create thread/ }),
    ).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: /^Cancel$/ })).toBeTruthy();
  });

  it("keeps its own Escape-to-close now that Modal owns the listener", async () => {
    const onClose = vi.fn();
    render(
      <PanelPicker
        mode="create"
        catalog={catalog}
        initialParticipants={[]}
        onClose={onClose}
        onSubmit={vi.fn()}
      />,
    );
    await screen.findByRole("dialog");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});
