"use client";

/**
 * v1.405.0 — "Open in VS Code / Cursor" presses for a CODING SESSION.
 *
 * Every press here reads the session's `EditorContext` (lib/codeEditor),
 * which `HarnessSession` provides once it knows the editor this PC has. With
 * no context (the chat page) or no editor, NOTHING renders — never a dead
 * press. A press asks the daemon (`POST /editor/open`) to open an absolute
 * path at a line; the daemon builds the editor's URL itself. A refusal is
 * said in one line beside the press, in the daemon's own words.
 *
 *  - `OpenInEditorButton`: "Open in VS Code" (a file header), "Open at line
 *    12" (a hunk), or the words of a reference (a finding);
 *  - `StepOpenInEditor`: beside a coding step that worked on a file
 *    (read_file, edit_file, write_file, multi_edit, apply_patch,
 *    notebook_edit) — shown on hover (always on a touch screen);
 *  - `LinkedRefs`: a report's text with every `path:line` a press.
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Loader2, SquareArrowOutUpRight } from "lucide-react";
import {
  EditorContext,
  findEditorRefs,
  openInEditor,
  readEditorOptions,
  type EditorOpener,
  type EditorOptions,
  openFailedWords,
  pathBase,
  resolveInFolder,
  stepEditorTarget,
  useEditor,
} from "@/lib/codeEditor";

const QUIET =
  "inline-flex shrink-0 items-center gap-1 rounded-md px-1 text-[12px] leading-5 text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:opacity-50 motion-reduce:transition-none";

/** One press that opens `path` (absolute, resolved) at `line`. */
export function OpenInEditorButton({
  path,
  line = null,
  column = null,
  words,
  title,
  testId = "open-in-editor",
  className = "",
  icon = true,
  bare = false,
}: {
  path: string;
  line?: number | null;
  column?: number | null;
  /** The press's words; default "Open in <editor>". */
  words?: ReactNode;
  title?: string;
  testId?: string;
  className?: string;
  icon?: boolean;
  /** Only `className` (a reference inside a paragraph: inline, wraps). */
  bare?: boolean;
}) {
  const editor = useEditor();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!editor || !path) return null;
  const where = line && line > 1 ? ` at line ${line}` : "";
  async function press() {
    if (!editor || busy) return;
    setBusy(true);
    setError(null);
    try {
      await editor.open(path, line, column);
    } catch (e) {
      setError(openFailedWords(e, editor.label));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button
        type="button"
        data-testid={testId}
        data-path={path}
        data-line={line ?? undefined}
        onClick={() => void press()}
        disabled={busy}
        title={title ?? `Open ${pathBase(path)}${where} in ${editor.label}`}
        aria-label={title ?? `Open ${pathBase(path)}${where} in ${editor.label}`}
        className={bare ? className : `${QUIET} ${className}`.trim()}
      >
        {icon ? (
          busy ? (
            <Loader2 size={12} aria-hidden className="animate-spin motion-reduce:animate-none" />
          ) : (
            <SquareArrowOutUpRight size={12} aria-hidden />
          )
        ) : null}
        {words ?? `Open in ${editor.label}`}
      </button>
      {error ? (
        <span role="alert" data-testid={`${testId}-error`} className="min-w-0 break-words text-[12px] text-tone-danger">
          {error}
        </span>
      ) : null}
    </>
  );
}

/**
 * Beside a coding step that worked on a file: "Open in VS Code" (a read with
 * an `offset` opens at that line). Null outside a coding session, for any
 * other tool, or when the step's path cannot be read safely.
 */
export function useStepEditorPath(
  name: string,
  args?: Record<string, unknown> | null,
  input?: string | null,
): { path: string; line: number | null } | null {
  const editor = useEditor();
  if (!editor) return null;
  const target = stepEditorTarget(name, args, input);
  if (!target) return null;
  const path = resolveInFolder(editor.folder, target.path);
  return path ? { path, line: target.line } : null;
}

export function StepOpenInEditor({ path, line }: { path: string; line: number | null }) {
  return (
    <OpenInEditorButton
      path={path}
      line={line}
      testId="step-open-in-editor"
      icon={false}
      className="[@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover/step:opacity-100 [@media(hover:hover)]:group-focus-within/step:opacity-100 focus-visible:opacity-100"
    />
  );
}

/**
 * A report's text with every `path:line` (`path:line:col`, `path#L12`) a
 * press that opens that place — a path INSIDE the session's folder only
 * (never a URL, never `..` out of it). Outside a coding session, or with no
 * editor, the text as it was.
 */
export function LinkedRefs({ text }: { text: string }) {
  const editor = useEditor();
  if (!editor || !text) return <>{text}</>;
  const refs = findEditorRefs(text);
  if (!refs.length) return <>{text}</>;
  const out: ReactNode[] = [];
  let at = 0;
  refs.forEach((r, i) => {
    const path = resolveInFolder(editor.folder, r.path, { confine: true });
    if (!path) return;
    if (r.start > at) out.push(text.slice(at, r.start));
    out.push(
      <OpenInEditorButton
        key={`ref-${i}`}
        path={path}
        line={r.line}
        column={r.column}
        words={r.text}
        icon={false}
        testId="editor-ref"
        title={`Open ${r.path} at line ${r.line} in ${editor.label}`}
        bare
        className="inline break-all rounded-sm text-left text-zinc-300 underline decoration-zinc-500 decoration-dotted underline-offset-[3px] transition-colors hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:opacity-50 motion-reduce:transition-none"
      />,
    );
    at = r.end;
  });
  if (at < text.length) out.push(text.slice(at));
  return <>{out}</>;
}

/**
 * What a coding session lends everything inside it (HarnessSession wraps its
 * conversation and its panes in this): the session's folder and the editor a
 * press opens, read ONCE per page visit (`GET /editor/options`). Until the
 * answer is in, or when this PC has no editor, the value is null and no press
 * renders anywhere.
 */
export function SessionEditorProvider({ folder, children }: { folder: string; children: ReactNode }) {
  const [opts, setOpts] = useState<EditorOptions | null>(null);
  useEffect(() => {
    let live = true;
    void readEditorOptions().then((o) => {
      if (live) setOpts(o);
    });
    return () => {
      live = false;
    };
  }, []);
  const value = useMemo<EditorOpener | null>(
    () => (opts?.preferred ? { folder, label: opts.preferredLabel, open: openInEditor } : null),
    [opts, folder],
  );
  return <EditorContext.Provider value={value}>{children}</EditorContext.Provider>;
}
