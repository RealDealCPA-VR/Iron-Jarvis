/**
 * v1.475.0: drive Build › Code's one model picker (tool · model · effort) the
 * way a person does — open the chip, press a row — and read back what is
 * chosen from the chip's own `data-*` attributes.
 *
 * `prefix` is the picker's `testId` ("code-start" in the new-session box,
 * "code-pick" in a session); `chip` is its chip's test id ("code-start-tool",
 * "code-model-chip").

 * NEVER call a helper that opens the panel (effortLevels, modelRowsShown,
 * groupsShown, chooseModel…) inside `waitFor`: waitFor runs its check again
 * on every change to the page, and opening the panel IS a change, so it never
 * stops. Wait for what the page shows first, then read the panel once.
 */
import { act, fireEvent, screen } from "@testing-library/react";

export const START = { prefix: "code-start", chip: "code-start-tool" } as const;
export const SESSION = { prefix: "code-pick", chip: "code-model-chip" } as const;
type Where = { prefix: string; chip: string };

export function chip(where: Where): HTMLButtonElement {
  return screen.getByTestId(where.chip) as HTMLButtonElement;
}

export function isOpen(where: Where): boolean {
  return screen.queryByTestId(`${where.prefix}-tool-menu`) !== null;
}

export function openPicker(where: Where): HTMLElement {
  if (!isOpen(where)) fireEvent.click(chip(where));
  return screen.getByTestId(`${where.prefix}-tool-menu`);
}

export function closePicker(where: Where): void {
  if (!isOpen(where)) return;
  fireEvent.keyDown(screen.getByTestId(`${where.prefix}-tool-menu`), { key: "Escape" });
}

/** What the chip says is chosen. */
export function chosen(where: Where): { tool: string; model: string; effort: string } {
  const c = chip(where);
  return { tool: c.dataset.tool ?? "", model: c.dataset.model ?? "", effort: c.dataset.effort ?? "" };
}

/** Press a model row ("" = the tool's own default), then close the panel. */
export async function chooseModel(where: Where, model: string): Promise<void> {
  openPicker(where);
  fireEvent.click(screen.getByTestId(`${where.prefix}-model-${model || "default"}`));
  await act(async () => {
    await new Promise<void>((r) => requestAnimationFrame(() => r()));
  });
  closePicker(where);
}

/** Press an effort row ("" = Default). */
export function chooseEffort(where: Where, effort: string): void {
  openPicker(where);
  fireEvent.click(screen.getByTestId(`${where.prefix}-effort-${effort || "default"}`));
}

/** The effort rows the panel offers for the chosen model ("" = Default), in
 *  order; [] when the model takes none. Leaves the panel as it found it. */
export function effortLevels(where: Where): string[] {
  const was = isOpen(where);
  const panel = openPicker(where);
  const box = panel.querySelector(`[data-testid="${where.prefix}-efforts"]`);
  const out = Array.from(box?.querySelectorAll<HTMLElement>("[role=radio]") ?? []).map((b) =>
    (b.dataset.testid ?? "").slice(`${where.prefix}-effort-`.length).replace(/^default$/, ""),
  );
  if (!was) closePicker(where);
  return out;
}

/** The model rows ("" = default) with each row's words, in order. */
export function modelRowsShown(where: Where): { id: string; label: string; group: string; disabled: boolean; title: string }[] {
  const was = isOpen(where);
  const panel = openPicker(where);
  const box = panel.querySelector(`[data-testid="${where.prefix}-models"]`);
  const out = Array.from(box?.querySelectorAll<HTMLButtonElement>("[role=option]") ?? []).map((b) => ({
    id: (b.dataset.testid ?? "").slice(`${where.prefix}-model-`.length).replace(/^default$/, ""),
    label: (b.textContent ?? "").replace(/not in the list$/, "").trim(),
    group: b.dataset.group ?? "",
    disabled: b.disabled,
    title: b.getAttribute("title") ?? "",
  }));
  if (!was) closePicker(where);
  return out;
}

/** The effort row elements (to read `disabled` and `title`). */
export function effortButtons(where: Where): HTMLButtonElement[] {
  const panel = openPicker(where);
  return Array.from(panel.querySelectorAll<HTMLButtonElement>(`[data-testid="${where.prefix}-efforts"] [role=radio]`));
}

/** Models grouped as the panel shows them: [[group, [labels]]]. */
export function groupsShown(where: Where): [string, string[]][] {
  const out: [string, string[]][] = [];
  for (const r of modelRowsShown(where)) {
    if (!r.group) continue;
    const last = out[out.length - 1];
    if (last && last[0] === r.group) last[1].push(r.label);
    else out.push([r.group, [r.label]]);
  }
  return out;
}
