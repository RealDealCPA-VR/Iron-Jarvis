/**
 * v1.450.0 (Wave D, item 37): the four native confirm() prompts became the
 * page's own question (components/PageQuestion.tsx). Tests that used to stub
 * `window.confirm` answer the page's question instead, through this helper:
 *
 *   const asked = answerPageQuestions("pane-undo-confirm", true);
 *   …the press…
 *   await waitFor(() => expect(asked).toHaveBeenCalledTimes(1));
 *   expect(asked.mock.calls[0][0]).toContain("NEWEST write");
 *
 * `asked` is a vi.fn: it is called with the question's words each time the
 * question appears, and its return value is the answer (true presses the yes
 * button, false the no button) — so `asked.mockReturnValue(false)` declines,
 * exactly as `confirmSpy.mockReturnValue(false)` did. It presses the REAL
 * buttons, so the component's own wiring is what is tested. `stop()` ends it.
 */
import { afterEach, vi, type Mock } from "vitest";

const live: PageQuestionAnswerer[] = [];
// Every answerer ends with its test: a stale one must never answer the next
// test's question.
afterEach(() => {
  while (live.length) live.pop()?.stop();
});

export type PageQuestionAnswerer = Mock<(words: string) => boolean> & { stop: () => void };

export function answerPageQuestions(testId: string, answer = true): PageQuestionAnswerer {
  const fn = vi.fn((_words: string) => answer) as PageQuestionAnswerer;
  const seen = new WeakSet<Element>();
  const look = () => {
    const q = document.querySelector(`[data-testid="${testId}"]`);
    if (!q || seen.has(q)) return;
    seen.add(q);
    const words = q.getAttribute("aria-label") ?? q.textContent ?? "";
    const yes = fn(words);
    const btn = document.querySelector<HTMLButtonElement>(`[data-testid="${testId}-${yes ? "yes" : "no"}"]`);
    // Pressed after this mutation settles, as a person would.
    setTimeout(() => btn?.click(), 0);
  };
  const obs = new MutationObserver(look);
  obs.observe(document.body, { childList: true, subtree: true });
  fn.stop = () => obs.disconnect();
  live.push(fn);
  return fn;
}
