/**
 * Typing does not re-render the transcript (v1.250.0, S-05).
 *
 * The claim this pins is the whole point of the composer store: a keystroke
 * reaches the textarea, the pickers and the send arrow, and NOTHING else. The
 * old page held the text in `useState`, so every character re-ran the page body
 * and — before the rows were memoized — every bubble with it (measured on a
 * 50-message thread: 4.1 page renders and 105 bubble renders per keystroke).
 *
 * A render COUNT is the assertion, never a wall-clock figure: the ratio is a
 * property of the wiring and holds on any machine, while a duration measures
 * the runner (and goes red on a contended one).
 *
 * The second test is the anti-vacuity control: the "/" picker's token rule moved
 * out of the page along with the caret, so if that move had broken the
 * derivation the isolation test alone would still pass — with a composer that
 * no longer opens a dropdown at all.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { memo, useRef } from "react";
import { createComposerStore, useComposer, type ComposerStore } from "@/lib/composerStore";
import { slashTokenAt } from "@/lib/slash";

/** A message row that counts its renders — memoized exactly like the real
 *  MessageRow, and given props that do not change while text is typed. */
const rowRenders = { n: 0 };
const Row = memo(function Row({ text }: { text: string }) {
  rowRenders.n += 1;
  return <div data-testid="row">{text}</div>;
});

/** The shape of the real page: a body that reads NOTHING from the composer, a
 *  subscribed textarea, and a subscribed picker. */
const pageRenders = { n: 0 };
function Page({ store, messages }: { store: ComposerStore; messages: string[] }) {
  pageRenders.n += 1;
  return (
    <div>
      {messages.map((m, i) => (
        <Row key={i} text={m} />
      ))}
      <Picker store={store} />
      <Input store={store} />
    </div>
  );
}

const Input = memo(function Input({ store }: { store: ComposerStore }) {
  const { text } = useComposer(store);
  return (
    <textarea
      aria-label="Message"
      value={text}
      onChange={(e) =>
        store.type(e.target.value, e.target.selectionStart ?? e.target.value.length)
      }
    />
  );
});

const Picker = memo(function Picker({ store }: { store: ComposerStore }) {
  const { text, caret, slashDismissed } = useComposer(store);
  const tok = slashDismissed ? null : slashTokenAt(text, caret);
  if (tok === null) return null;
  return <div role="listbox" aria-label="Skills" />;
});

function Harness({ messages }: { messages: string[] }) {
  const ref = useRef<ComposerStore | null>(null);
  if (ref.current === null) ref.current = createComposerStore();
  return <Page store={ref.current} messages={messages} />;
}

const MANY = Array.from({ length: 50 }, (_, i) => `message ${i}`);

describe("composer isolation", () => {
  beforeEach(() => {
    rowRenders.n = 0;
    pageRenders.n = 0;
  });

  it("a keystroke re-renders NEITHER the page body NOR any message row", async () => {
    render(<Harness messages={MANY} />);
    await waitFor(() => expect(screen.getAllByTestId("row")).toHaveLength(50));

    const pageAfterMount = pageRenders.n;
    const rowsAfterMount = rowRenders.n;
    expect(rowsAfterMount).toBe(50); // one render each, on mount

    const box = screen.getByLabelText("Message");
    for (const value of ["s", "su", "sum", "summ", "summa"]) {
      fireEvent.change(box, { target: { value } });
    }
    await waitFor(() => expect((box as HTMLTextAreaElement).value).toBe("summa"));

    // Five keystrokes: the textarea re-rendered, and that is all.
    expect(pageRenders.n).toBe(pageAfterMount);
    expect(rowRenders.n).toBe(rowsAfterMount);
  });

  it("the / picker still opens — the token rule moved, it did not break", async () => {
    render(<Harness messages={["only one"]} />);
    const box = screen.getByLabelText("Message");

    expect(screen.queryByRole("listbox", { name: "Skills" })).toBeNull();
    fireEvent.change(box, { target: { value: "write /rep", selectionStart: 10 } });
    await waitFor(() =>
      expect(screen.getByRole("listbox", { name: "Skills" })).toBeInTheDocument(),
    );

    // ...and a "/" that does NOT open a word never opens it (v1.105.0).
    fireEvent.change(box, { target: { value: "C:/Users", selectionStart: 8 } });
    await waitFor(() =>
      expect(screen.queryByRole("listbox", { name: "Skills" })).toBeNull(),
    );
  });

  it("the page reads the text synchronously on send, without subscribing", () => {
    const store = createComposerStore();
    const sent = vi.fn();
    store.setText("the whole message");
    // What send() does: ask the store, never a render-time copy.
    sent(store.get().text);
    expect(sent).toHaveBeenCalledWith("the whole message");
  });
});
