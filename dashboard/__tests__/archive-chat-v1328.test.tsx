/**
 * ArchiveChatDialog (v1.328.0, calm chat B7) — "Archive '<title>'?".
 *
 * Pinned:
 *  - nothing running: a plain confirm (Archive / Cancel); confirming says
 *    stop=false;
 *  - something running: every item the daemon named is listed, the action
 *    reads "Stop and archive" in the DANGER tone, and confirming says
 *    stop=true; a blank item never turns a confirm into a stop;
 *  - Cancel and Escape close; while busy neither Escape nor the buttons act;
 *  - focus moves into the dialog on open (Cancel first, never the
 *    destructive action) and Tab stays inside (the <Modal> trap);
 *  - an error sentence shows as an alert; an untitled chat reads
 *    "Archive this chat?";
 *  - theme tokens only: no literal colour class in the component.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { ArchiveChatDialog, archiveHeading } from "@/components/chat/ArchiveChatDialog";

afterEach(() => cleanup());

function mount(props: Partial<Parameters<typeof ArchiveChatDialog>[0]> = {}) {
  const onConfirm = vi.fn();
  const onClose = vi.fn();
  const utils = render(
    <ArchiveChatDialog
      title="Quarterly notes"
      running={[]}
      onConfirm={onConfirm}
      onClose={onClose}
      {...props}
    />,
  );
  return { ...utils, onConfirm, onClose };
}

describe("ArchiveChatDialog — nothing running", () => {
  it("is a plain confirm that archives without a stop", async () => {
    const { onConfirm } = mount();
    const dialog = await screen.findByRole("dialog", { name: "Archive 'Quarterly notes'?" });
    expect(within(dialog).getByTestId("archive-chat-heading").textContent).toBe(
      "Archive 'Quarterly notes'?",
    );
    expect(within(dialog).queryByTestId("archive-chat-running")).toBeNull();
    const confirm = within(dialog).getByTestId("archive-chat-confirm");
    expect(confirm.textContent).toBe("Archive");
    expect(confirm.className).not.toContain("btn-danger");
    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith(false);
  });

  it("a blank running entry does not turn the confirm into a stop", async () => {
    const { onConfirm } = mount({ running: ["  "] });
    const confirm = await screen.findByTestId("archive-chat-confirm");
    expect(confirm.textContent).toBe("Archive");
    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledWith(false);
  });

  it("an untitled chat reads 'Archive this chat?'", () => {
    expect(archiveHeading("")).toBe("Archive this chat?");
    expect(archiveHeading("   ")).toBe("Archive this chat?");
    expect(archiveHeading(" Taxes ")).toBe("Archive 'Taxes'?");
  });
});

describe("ArchiveChatDialog — something running", () => {
  it("lists what would stop and offers Stop and archive in the danger tone", async () => {
    const { onConfirm } = mount({
      running: ["a reply in progress", "a question waiting for you"],
    });
    const dialog = await screen.findByRole("dialog");
    const items = within(dialog).getAllByTestId("archive-chat-running-item").map((li) => li.textContent);
    expect(items).toEqual(["a reply in progress", "a question waiting for you"]);
    const confirm = within(dialog).getByTestId("archive-chat-confirm");
    expect(confirm.textContent).toBe("Stop and archive");
    expect(confirm.className).toContain("btn-danger");
    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledWith(true);
  });
});

describe("ArchiveChatDialog — closing, busy, focus", () => {
  it("Cancel and Escape close it", async () => {
    const { onClose } = mount();
    fireEvent.click(await screen.findByTestId("archive-chat-cancel"));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("while busy nothing dismisses or confirms, and the action says so", async () => {
    const { onClose, onConfirm } = mount({ running: ["a reply in progress"], busy: true });
    const confirm = await screen.findByTestId("archive-chat-confirm");
    expect(confirm.textContent).toBe("Stopping…");
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByTestId("archive-chat-cancel") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.click(confirm);
    expect(onClose).not.toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("focus starts on Cancel, never the destructive action, and Tab stays inside", async () => {
    mount({ running: ["a reply in progress"] });
    const cancel = await screen.findByTestId("archive-chat-cancel");
    expect(document.activeElement).toBe(cancel);
    const confirm = screen.getByTestId("archive-chat-confirm");
    confirm.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(cancel);
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(confirm);
  });

  it("an error shows as one alert sentence", async () => {
    mount({ error: "Could not archive this chat. Try again." });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe("Could not archive this chat. Try again.");
    expect(alert.className).toContain("text-tone-danger");
  });
});

describe("ArchiveChatDialog — theme", () => {
  it("uses theme tokens, never literal colours or half-pixel text", () => {
    const src = readFileSync(
      join(__dirname, "..", "components", "chat", "ArchiveChatDialog.tsx"),
      "utf8",
    ).replace(/\r\n/g, "\n");
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(src).not.toMatch(/\b(?:text|bg|border)-(?:red|rose|amber|green|emerald|blue|sky|black)-?\d*/);
    expect(src).not.toMatch(/text-\[\d+\.\d+px\]/);
    expect(src).toContain('from "@/components/Modal"');
  });
});
