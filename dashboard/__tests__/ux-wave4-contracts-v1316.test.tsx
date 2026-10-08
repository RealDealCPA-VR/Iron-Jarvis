/**
 * v1.316.0 — UX wave 4 shared contracts (coordinator):
 *  - <Field label> ties its label to its one control (htmlFor ↔ id via useId,
 *    an existing id kept, aria-labels untouched).
 *  - agentLabel(name, {builtin, origin}) — text names for built-ins, custom
 *    names as typed, Jarvis for a mission coordinator.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Field } from "@/components/ui";
import { agentLabel } from "@/lib/agentWorlds";

describe("Field", () => {
  it("the label focuses and names its control", () => {
    render(
      <Field label="Name" hint="Shown on the card">
        <input />
      </Field>,
    );
    const input = screen.getByLabelText("Name");
    expect(input.tagName).toBe("INPUT");
    fireEvent.click(screen.getByText("Name"));
    expect(screen.getByText("Shown on the card")).toBeInTheDocument();
  });

  it("two forms on one page never share an id", () => {
    render(
      <>
        <Field label="Name"><input /></Field>
        <Field label="Name"><input /></Field>
      </>,
    );
    const [a, b] = screen.getAllByLabelText("Name");
    expect(a.id).toBeTruthy();
    expect(a.id).not.toBe(b.id);
  });

  it("keeps a control's own id and its aria-label", () => {
    render(
      <Field label="Direction">
        <select id="dir" aria-label="Direction"><option>In</option></select>
      </Field>,
    );
    const sel = screen.getByLabelText("Direction");
    expect(sel.id).toBe("dir");
    expect(document.querySelector('label[for="dir"]')).not.toBeNull();
  });
});

describe("agentLabel", () => {
  it.each([
    ["builder", { builtin: true }, "Builder"],
    ["file_manager", { builtin: true }, "File manager"],
    ["ledger-checker", { builtin: false }, "ledger-checker"],
    ["ledger-checker", {}, "ledger-checker"],
    ["supervisor", { builtin: true, origin: "job:mission" }, "Jarvis"],
    ["supervisor", { builtin: true, origin: "schedule:x" }, "Supervisor"],
  ])("%s %j → %s", (name, opts, want) => {
    expect(agentLabel(name, opts)).toBe(want);
  });
});
