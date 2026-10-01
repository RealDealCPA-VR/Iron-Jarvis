/**
 * Line diff (v1.297.0 — lifted out of `components/chat/DocPreview.tsx`, where
 * it was born in v1.166.0 as "changes since you last previewed").
 *
 * Three surfaces now read it: the document preview's refresh/compare views,
 * an agent's instructions history (`AgentFiles`) and the reflection coach's
 * before→after proposals (`AgentCoach`). A pure module, no React, so the
 * agents room does not drag the preview panel (and react-markdown) into its
 * chunk just to draw two colours of line.
 */

/** One line of the diff view. `same` lines are kept for reading context. */
export interface DiffLine {
  kind: "same" | "added" | "removed";
  text: string;
}

/** Classic LCS line diff: unchanged lines interleaved with removed (prev-only)
 *  and added (next-only) lines, in document order. Inputs are capped by their
 *  producers (previews 80 rows / 20k chars; an instructions file is small), so
 *  the quadratic table stays tiny; past the guard it degrades to
 *  remove-all/add-all — coarser, never wrong. */
export function diffLines(prev: string[], next: string[]): DiffLine[] {
  const MAX = 1500;
  if (prev.length > MAX || next.length > MAX) {
    return [
      ...prev.map((text) => ({ kind: "removed" as const, text })),
      ...next.map((text) => ({ kind: "added" as const, text })),
    ];
  }
  const m = prev.length;
  const n = next.length;
  // lcs[i][j] = LCS length of prev[i:] vs next[j:]
  const lcs: Uint32Array[] = Array.from(
    { length: m + 1 },
    () => new Uint32Array(n + 1),
  );
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      lcs[i][j] =
        prev[i] === next[j]
          ? lcs[i + 1][j + 1] + 1
          : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (prev[i] === next[j]) {
      out.push({ kind: "same", text: prev[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ kind: "removed", text: prev[i] });
      i++;
    } else {
      out.push({ kind: "added", text: next[j] });
      j++;
    }
  }
  while (i < m) out.push({ kind: "removed", text: prev[i++] });
  while (j < n) out.push({ kind: "added", text: next[j++] });
  return out;
}

/** Split a text into the lines `diffLines` compares — CRLF-tolerant, so an
 *  instructions file saved on Windows diffs against one typed in a browser
 *  textarea without every line reading as changed. */
export function textLines(text: string | null | undefined): string[] {
  return (text ?? "").split(/\r?\n/);
}

/** The diff of two texts, line by line. */
export function diffTexts(before: string | null | undefined, after: string | null | undefined): DiffLine[] {
  return diffLines(textLines(before), textLines(after));
}
