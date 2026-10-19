"use client";

/**
 * Calm chat W1-5 (v1.326.0): the WORK behind a reply, as quiet grey rows.
 *
 * Process detail must never look like a card. While a turn runs, each tool
 * call, the model's thinking and the step it is on is ONE grey line: a 14px
 * icon, a few plain words, a small dot, and a short summary (a file name with
 * a dotted underline, a search, a duration). The step still running carries a
 * spinner and a soft shimmer across its words (v1.348.0, `.ij-shimmer` in
 * app/globals.css; it replaced an opacity pulse), both of which stop under
 * reduced motion.
 *
 * v1.348.0: the running state reads like the finished one. Above the live
 * rows, ONE quiet line counts up in the folded line's own words, "Working for
 * 12 s · read 2 files, searched the web" (the same counter, lib/stepWords
 * stepCounts, so the two can never drift), and its clock ticks itself (a
 * leaf, so nothing above it re-renders each second). Under it only the last
 * few steps show; older finished ones fold into a quiet "N earlier steps"
 * toggle that opens them in place. A step still running is never folded.
 *
 * When the answer lands, all of it folds under ONE line above the reply:
 * "Worked for 3.2 s · read 2 files, ran 1 tool", with a chevron. Pressing it
 * shows the rows again. The line is built only from what the message already
 * stores (steps, timing, the tools that ran, the thinking and how long it
 * took); a reply with none of that shows no line at all.
 *
 * v1.355.0 (readiness G63): a PLAIN answer (no step, no thinking) whose turn
 * timing was stored reads "Answered in 2.1 s" on the same quiet line, with
 * nothing to open (no chevron, not a button). Without timing (an older chat,
 * the POST lane) it still shows nothing.
 *
 * Plain words only for tools this file KNOWS (built-ins, by exact name). Any
 * other tool reads as "ran a tool" with its own id beside it: the app does not
 * describe what it cannot vouch for (the lib/toolWords rule).
 *
 * v1.329.0: a row says what was done to WHAT, live and after the turn alike:
 * "Read harbor.xlsx", "Searched the web for pier 9 hours", "Ran excel_query".
 * The subject is the step's safe target (lib/workTarget: a base name, a short
 * query, a host, a program name; never a full path, a whole argument or
 * anything credential-shaped), saved on the step so a reopened chat still
 * says it. A step saved before then keeps the older "Read · read_file".
 *
 * v1.349.0: click a step to see what it did (agent-elements' expandable tool
 * rows; the idea, never its boxes). A step that carries the daemon's masked
 * `detail` (lib/stepDetail) is a real disclosure button: the same quiet grey
 * row plus a small chevron, opening beneath itself to the call's input (a
 * command as one "$ command" line, anything else as short "key: value" lines
 * through wave A's approval formatters) and its output in monospace, wrapped,
 * the first STEP_OUTPUT_LINES lines with "Show all" for the rest. No box, no
 * border, no tint. Both the folded "Worked for" rows (a reopened chat too:
 * the detail is saved with the step) and the live rows open. A step with no
 * detail (an older chat, a running call) is the same plain row as before.
 *
 * v1.351.0 (G07): the folded line tells the truth about failures. A failed
 * step counts only as failed ("read 1 web page, 7 failed", never "read 8 web
 * pages"), and a turn with any failed step folds under a warning triangle in
 * the theme's warn tone instead of the check.
 *
 * v1.352.0 (G22): a connected app's step reads "gmail: search messages" under
 * a plug, counted "used gmail"; a browser step reads "Used your browser ·
 * click" under a window, counted "used your browser" (lib/stepWords). The
 * tool's id stays in the hover title. A FAILED app step offers one quiet
 * "Fix in Tools" link beneath it (the Tools page, where the pack's own
 * failure, last output and Retry live — the bell's pack_failed door).
 */

import { memo, useId, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import {
  AppWindow,
  Check,
  ChevronDown,
  ChevronRight,
  FilePen,
  FilePlus,
  FileText,
  Folder,
  Globe,
  Loader2,
  Plug,
  Search,
  SquareTerminal,
  Timer,
  TriangleAlert,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";
import type { ToolCard } from "@/lib/useChatStream";
import { ThinkingDisclosure } from "@/components/chat/ThinkingDisclosure";
import { secondsText, type ReceiptStep, type ReceiptTiming } from "@/components/chat/TurnReceipt";
import { elapsedWords, useNow } from "@/components/chat/TurnClock";
import { cleanTarget, secretLooking, stepTarget, toolKind, type WorkKind } from "@/lib/workTarget";
import { failedSteps, failedWords, kindWords, rowSubject, stepCounts, stepHint, stepPhrase } from "@/lib/stepWords";
import { appToolParts } from "@/lib/toolWords";
import {
  STEP_OUTPUT_LINES,
  capStepDetails,
  cleanStepDetail,
  outputLines,
  stepInputView,
  type StepDetail,
  type StepInputView,
} from "@/lib/stepDetail";
import type { ArgEntry } from "@/lib/approvalPreview";

/* ------------------------------------------------------------- vocabulary */

// v1.329.0: the kinds table moved to lib/workTarget (the stream hook reads it
// to keep each step's safe target); re-exported here for existing callers.
export { toolKind, type WorkKind };

/* v1.329.0 (G2): the WORDS live in lib/stepWords, shared with the expanded
 * receipt so both say a step the same way; only the icons stay here. */
const ICON: Record<WorkKind, LucideIcon> = {
  read: FileText,
  make: FilePlus,
  change: FilePen,
  folder: Folder,
  search: Search,
  web: Globe,
  page: Globe,
  command: SquareTerminal,
  // v1.352.0 (G22): the browser the user connected, and a connected app.
  browser: AppWindow,
  app: Plug,
  tool: Wrench,
};

/** v1.324.0: an app's progress report in words — "40%" when it said how far
 *  it has to go, else "step 3"; its own message after a dot, kept short.
 *  (Moved here from the chat page with the live rows that show it.) */
export function progressWords(p: { progress: number; total: number | null; message: string }): string {
  const head =
    p.total && p.total > 0
      ? `${Math.max(0, Math.min(100, Math.round((p.progress / p.total) * 100)))}%`
      : `step ${Math.round(p.progress)}`;
  const msg = (p.message || "").trim();
  return msg ? `${head} · ${msg.length > 80 ? `${msg.slice(0, 79)}…` : msg}` : head;
}

/* ---------------------------------------------------------------- summary */

/** What the folded line is built from: fields the message already stores. */
export interface WorkInput {
  steps?: ReceiptStep[] | null;
  toolsUsed?: string[] | null;
  timing?: ReceiptTiming | null;
  thinking?: string | null;
  thinkingSeconds?: number | null;
}

/** The steps to account for: the stored steps (one per call, with timing)
 *  when there are any, else one row per tool the reply says it ran. Shapes
 *  cross a JSON boundary (a saved thread), so each entry is checked. */
export function workSteps(input: WorkInput): ReceiptStep[] {
  const out: ReceiptStep[] = [];
  if (Array.isArray(input.steps)) {
    for (const st of input.steps) {
      if (!st || typeof st !== "object" || typeof st.name !== "string" || !st.name.trim()) continue;
      // v1.329.0: a saved target is made safe AGAIN on the way out (the
      // thread file may be older, or hand-edited); junk reads as none.
      const target = cleanTarget((st as { target?: unknown }).target);
      // v1.349.0: so is a saved detail (its two strings, masked and cut).
      const detail = cleanStepDetail((st as { detail?: unknown }).detail);
      out.push({
        name: st.name,
        ok: typeof st.ok === "boolean" ? st.ok : null,
        ms: typeof st.ms === "number" && Number.isFinite(st.ms) && st.ms >= 0 ? st.ms : null,
        ...(target ? { target } : {}),
        ...(detail ? { detail } : {}),
      });
    }
  }
  // v1.349.0: and the message's budget holds on the way out too.
  if (out.length) return capStepDetails(out);
  if (Array.isArray(input.toolsUsed)) {
    for (const t of input.toolsUsed) {
      if (typeof t === "string" && t.trim()) out.push({ name: t, ok: null, ms: null });
    }
  }
  return out;
}

/** "read 2 files, ran 1 tool" pieces, in a fixed order, plus "1 failed".
 *  v1.348.0: the counter itself lives in lib/stepWords (stepCounts), shared
 *  with the live line; this name stays for existing callers. */
export function workCounts(steps: ReceiptStep[]): string[] {
  return stepCounts(steps);
}

/** How long the turn took, from its own timing; null when not known. */
export function workDuration(timing: ReceiptTiming | null | undefined): string | null {
  if (!timing || typeof timing !== "object") return null;
  const { startedAt, endedAt } = timing;
  if (typeof startedAt !== "number" || typeof endedAt !== "number") return null;
  if (!Number.isFinite(startedAt) || !Number.isFinite(endedAt) || endedAt < startedAt) return null;
  return secondsText(endedAt - startedAt);
}

/**
 * The folded line's words, or null when the reply has nothing to say:
 *   "Worked for 3.2 s · read 2 files, ran 1 tool"
 *   "Read 2 files"                         (no timing stored)
 *   "Worked for 6.0 s · thought for 4 s"   (thinking only)
 *   "Answered in 2.1 s"                    (a plain answer, v1.355.0 G63)
 */
export function workSummary(input: WorkInput): string | null {
  return summaryOf(workSteps(input), input);
}

/** v1.355.0 (readiness G63): true when the reply did no work worth folding
 *  (no step, no thinking) but its turn's timing is known — the line then
 *  says only how long the answer took, and has nothing to open. */
function plainAnswer(steps: ReceiptStep[], input: WorkInput): boolean {
  const thought = typeof input.thinking === "string" && input.thinking.trim().length > 0;
  return !steps.length && !thought && workDuration(input.timing) !== null;
}

/** workSummary's words from steps already read (WorkLine reads them once). */
function summaryOf(steps: ReceiptStep[], input: WorkInput): string | null {
  const thought = typeof input.thinking === "string" && input.thinking.trim().length > 0;
  // v1.355.0 (G63): a plain answer says how long it took (it used to show
  // nothing, so a reply with no tools had no duration anywhere).
  if (plainAnswer(steps, input)) return `Answered in ${workDuration(input.timing)}`;
  if (!steps.length && !thought) return null;
  const counts = workCounts(steps);
  if (!counts.length) {
    const s = input.thinkingSeconds;
    counts.push(
      typeof s === "number" && Number.isFinite(s) && s >= 0
        ? `thought for ${Math.max(1, Math.round(s))} s`
        : "thought it through",
    );
  }
  const list = counts.join(", ");
  const dur = workDuration(input.timing);
  if (dur) return `Worked for ${dur} · ${list}`;
  return list.charAt(0).toUpperCase() + list.slice(1);
}

/* -------------------------------------------------------------------- rows */

function Dot() {
  return <span aria-hidden="true" className="h-[3px] w-[3px] shrink-0 rounded-full bg-zinc-600" />;
}

export interface WorkRowProps {
  icon: LucideIcon;
  title: string;
  /** The short summary after the dot. */
  detail?: string | null;
  /** The detail is a path or file name: dotted underline, full path on hover. */
  path?: boolean;
  detailTitle?: string;
  /** Quiet extras after another dot (a duration, a progress report). */
  meta?: ReactNode;
  running?: boolean;
  failed?: boolean;
  /** The row's own hover text (a tool's full output). */
  hint?: string;
  /** v1.329.0: what the step was done TO, said right after the title as one
   *  phrase ("Read harbor.xlsx", "Searched the web for pier 9 hours"). Takes
   *  the place of `detail`. */
  subject?: string | null;
  /** The subject is a file or folder name: dotted underline. */
  subjectFile?: boolean;
  /** The subject's hover text (its tool id, a live file's full path). */
  subjectTitle?: string;
  /** v1.349.0: the row OPENS (its step carries a detail): it is drawn as a
   *  real disclosure button, the same quiet line plus a small chevron.
   *  `controls` is the id of the panel drawn beneath it while open. Absent:
   *  the plain row, exactly as before. */
  expand?: { open: boolean; onToggle: () => void; controls: string } | null;
}

/** ONE grey line: icon, title, dot, summary. Never a box. */
export function WorkRow({
  icon: Icon,
  title,
  detail,
  path,
  detailTitle,
  meta,
  running,
  failed,
  hint,
  subject,
  subjectFile,
  subjectTitle,
  expand,
}: WorkRowProps) {
  // v1.348.0: a running title SHIMMERS (a soft light sweep, `.ij-shimmer` in
  // app/globals.css, from the theme's own inks) instead of pulsing; the
  // stylesheet makes it still text under reduced motion and forced colours.
  const titleTone = running
    ? "ij-shimmer text-zinc-300"
    : failed
      ? "text-tone-danger"
      : "text-zinc-400";
  const body = (
    <>
      {running ? (
        <Loader2 size={14} aria-hidden="true" className="shrink-0 animate-spin text-zinc-400 motion-reduce:animate-none" />
      ) : failed ? (
        <X size={14} aria-hidden="true" className="shrink-0 text-tone-danger" />
      ) : (
        <Icon size={14} aria-hidden="true" className="shrink-0" />
      )}
      {subject ? (
        // v1.329.0: words and subject read as ONE phrase with a real space,
        // and the phrase (not the words) gives way on a narrow screen.
        <span className="min-w-0 truncate">
          <span className={titleTone}>{title}</span>{" "}
          <span
            data-testid="work-target"
            title={subjectTitle ?? subject}
            className={`text-zinc-300 ${
              subjectFile ? "underline decoration-zinc-500 decoration-dotted underline-offset-[3px]" : ""
            }`}
          >
            {subject}
          </span>
        </span>
      ) : (
        <span className={`shrink-0 ${titleTone}`}>{title}</span>
      )}
      {!subject && detail ? (
        <>
          <Dot />
          <span
            title={detailTitle ?? detail}
            className={`min-w-0 truncate ${
              path ? "text-zinc-300 underline decoration-zinc-500 decoration-dotted underline-offset-[3px]" : ""
            }`}
          >
            {detail}
          </span>
        </>
      ) : null}
      {meta}
      {expand ? (
        <ChevronRight
          size={12}
          aria-hidden="true"
          data-testid="work-row-chevron"
          className={`shrink-0 text-zinc-600 transition-transform motion-reduce:transition-none ${
            expand.open ? "rotate-90" : ""
          }`}
        />
      ) : null}
    </>
  );
  const state = running ? "running" : failed ? "failed" : "done";
  if (expand) {
    // v1.349.0: a native button (Enter and Space open it), the same quiet
    // line: no box, no border, no fill. Only keyboard focus draws a ring.
    return (
      <button
        type="button"
        data-testid="work-row"
        data-state={state}
        title={hint}
        aria-expanded={expand.open}
        aria-controls={expand.open ? expand.controls : undefined}
        onClick={expand.onToggle}
        className="flex w-full min-w-0 items-center gap-2 rounded-sm text-left text-[13px] leading-5 text-zinc-500 transition-colors hover:text-zinc-300 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 motion-reduce:transition-none"
      >
        {body}
      </button>
    );
  }
  return (
    <div
      data-testid="work-row"
      data-state={state}
      title={hint}
      className="flex min-w-0 items-center gap-2 text-[13px] leading-5 text-zinc-500"
    >
      {body}
    </div>
  );
}

/* ---------------------------------------------------- a step's detail (v1.349.0) */

/** One argument as a short `key: value` line (wave A's ArgEntry). */
function DetailArg({ entry }: { entry: ArgEntry }) {
  return (
    <p className="min-w-0 whitespace-pre-wrap break-words">
      <span className="text-zinc-500">{entry.key}:</span>{" "}
      {entry.empty ? (
        <span className="text-zinc-600">(empty)</span>
      ) : (
        <span className="text-zinc-400">{entry.short}</span>
      )}
    </p>
  );
}

/** The call's input: "$ command", code, or `key: value` lines. */
function DetailInput({ view }: { view: StepInputView }) {
  const mono = "min-w-0 whitespace-pre-wrap break-words font-mono text-[12px] leading-5 text-zinc-300";
  switch (view.kind) {
    case "command":
      return (
        <>
          {/* The `$` is for the eye; a screen reader hears the command. */}
          <pre data-testid="step-detail-command" className={mono}>
            <span aria-hidden="true" className="select-none text-zinc-500">
              ${" "}
            </span>
            {view.command}
          </pre>
          {view.rest.map((e) => (
            <DetailArg key={e.key} entry={e} />
          ))}
        </>
      );
    case "code":
      return (
        <>
          <pre data-testid="step-detail-code" className={mono}>
            {view.code}
          </pre>
          {view.rest.map((e) => (
            <DetailArg key={e.key} entry={e} />
          ))}
        </>
      );
    case "args":
      return (
        <>
          {view.entries.map((e) => (
            <DetailArg key={e.key} entry={e} />
          ))}
        </>
      );
    case "text":
      return (
        <pre data-testid="step-detail-input-text" className={mono}>
          {view.text}
        </pre>
      );
  }
}

/**
 * What a step did, opened beneath its row: the input, then the output in
 * monospace, wrapped, the first STEP_OUTPUT_LINES lines and a quiet "Show
 * all N lines" for the rest. Indented to the row's words; no box, no border,
 * no fill. Both parts are labelled groups for a screen reader.
 */
export function StepDetailPanel({ id, name, detail }: { id: string; name: string; detail: StepDetail }) {
  const [all, setAll] = useState(false);
  const outId = useId();
  const input = stepInputView(name, detail.input);
  const lines = outputLines(detail.output);
  const capped = lines.length > STEP_OUTPUT_LINES;
  const shown = all || !capped ? lines : lines.slice(0, STEP_OUTPUT_LINES);
  return (
    <div
      id={id}
      data-testid="step-detail"
      className="grid min-w-0 gap-1 pb-1 pl-[22px] text-[12px] leading-5 text-zinc-500"
    >
      {input ? (
        <div role="group" aria-label="Input" data-testid="step-detail-input" className="grid min-w-0 gap-0.5">
          <DetailInput view={input} />
        </div>
      ) : null}
      <div role="group" aria-label="Output" className="grid min-w-0 justify-items-start gap-0.5">
        {lines.length ? (
          <pre
            id={outId}
            data-testid="step-detail-output"
            className="w-full min-w-0 whitespace-pre-wrap break-words font-mono text-[12px] leading-5 text-zinc-400"
          >
            {shown.join("\n")}
          </pre>
        ) : (
          <p data-testid="step-detail-no-output" className="text-zinc-600">
            No output.
          </p>
        )}
        {capped ? (
          <button
            type="button"
            data-testid="step-detail-more"
            aria-expanded={all}
            aria-controls={outId}
            onClick={() => setAll((v) => !v)}
            className="rounded-sm text-[12px] text-zinc-500 underline-offset-2 transition-colors hover:text-zinc-300 hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 motion-reduce:transition-none"
          >
            {all ? "Show fewer lines" : `Show all ${lines.length} lines`}
          </button>
        ) : null}
      </div>
    </div>
  );
}

/**
 * v1.352.0 (G22): the way forward beside a connected app's FAILED step — one
 * quiet link to the Tools page (the bell's pack_failed door), where the
 * pack's own failure, its last output and Retry live. A sibling of the row,
 * never inside it (a row that opens is a button), indented to the row's
 * words; no box, no border.
 */
export function FixInTools({ name }: { name: string }) {
  const parts = appToolParts(name);
  return (
    <div className="min-w-0 pl-[22px] text-[12px] leading-5">
      <Link
        href="/tools"
        data-testid="step-fix-tools"
        title={parts ? `Open Tools to check ${parts.app}` : "Open Tools"}
        className="rounded-sm text-zinc-500 underline-offset-2 transition-colors hover:text-zinc-300 hover:underline focus-visible:text-zinc-300 focus-visible:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 motion-reduce:transition-none"
      >
        Fix in Tools
      </Link>
    </div>
  );
}

/** The last segment of a path, either separator. */
function baseName(p: string): string {
  const parts = p.split(/[/\\]/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : p;
}

const PATH_KEYS = ["path", "file_path", "file", "filename", "source", "src", "folder", "directory", "dir", "root"];
const TEXT_KEYS = ["query", "q", "pattern", "url", "command"];

/** The one thing worth naming from a live call's (already redacted) args: a
 *  file or folder (shown by its name), else a search, a pattern, a URL or a
 *  command, kept to one short line. Null when nothing fits. */
export function argDetail(args: Record<string, unknown> | undefined): { text: string; full: string; path: boolean } | null {
  if (!args || typeof args !== "object") return null;
  for (const k of PATH_KEYS) {
    const v = args[k];
    if (typeof v === "string" && v.trim()) {
      const full = v.trim();
      return { text: baseName(full), full, path: true };
    }
  }
  const paths = args.paths;
  if (Array.isArray(paths) && typeof paths[0] === "string" && paths[0].trim()) {
    const full = paths.filter((p): p is string => typeof p === "string").join(", ");
    const more = paths.length > 1 ? ` and ${paths.length - 1} more` : "";
    return { text: `${baseName(paths[0].trim())}${more}`, full, path: true };
  }
  for (const k of TEXT_KEYS) {
    const v = args[k];
    if (typeof v === "string" && v.trim()) {
      const full = v.replace(/\s+/g, " ").trim();
      const text = full.length > 80 ? `${full.slice(0, 79)}…` : full;
      return { text: k === "query" || k === "q" ? `“${text}”` : text, full, path: false };
    }
  }
  return null;
}

function firstLine(s: string | undefined): string {
  if (typeof s !== "string") return "";
  const line = s.split(/\r?\n/).find((l) => l.trim()) ?? "";
  return line.trim();
}

/** How much of a live call's argument text a hover shows. Kept under the
 *  credential check's span (lib/workTarget CHECK_SPAN, 240) so every shown
 *  character, and 40 past the cut, has been checked. */
const ARG_HOVER_MAX = 200;

/** A live command's or unknown tool's hover: its one-line argument text, then
 *  its tool id ("git push origin main (shell)"). Null when there is no such
 *  text, or when it looks like it carries a credential (the row then keeps
 *  its older hover). On this screen only: never saved with the step. */
function liveArgHover(card: ToolCard): string | null {
  const full = argDetail(card.args)?.full.replace(/\s+/g, " ").trim();
  if (!full || secretLooking(full)) return null;
  const shown = full.length > ARG_HOVER_MAX ? `${full.slice(0, ARG_HOVER_MAX - 1)}…` : full;
  return `${shown} (${card.name})`;
}

/** One live tool call as a row (the streaming hooks' ToolCard). v1.329.0:
 *  the same words as a saved step ("Reading harbor.xlsx", "Searching the web
 *  for pier 9 hours", "Running excel_query"), from the same safe target
 *  (lib/workTarget). A known tool with nothing safe to name falls back to its
 *  words · its own id, never to a raw argument. */
function ToolRow({ card }: { card: ToolCard }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const running = card.status !== "done";
  const failed = !running && card.ok === false;
  // v1.349.0: a FINISHED call with the daemon's masked detail opens to show
  // it (read through the one reader again: a card may come from anywhere).
  // (Read once per card change, not on every render: the live rows
  // re-render as frames arrive.)
  const stepDetail = useMemo(() => (running ? null : cleanStepDetail(card.detail)), [running, card.detail]);
  const kind = toolKind(card.name);
  const subject = rowSubject(kind, card.name, stepTarget(card.name, card.args));
  // The hover: a live FILE keeps its full path (it is on this screen only and
  // is never saved); a search or a page shows the safe words and the tool id.
  const named = subject?.file ? argDetail(card.args) : null;
  // A running command or an unknown tool shows what it was CALLED WITH on
  // hover ("git push origin main (shell)"): with a grant there is no card, so
  // this is the only place on the chat page that says which command runs.
  // Screen only, never saved; dropped whole when it looks like a credential.
  // (v1.352.0: an app's or a browser step's hover keeps saying it too — they
  // read as "tool" before they had words of their own.)
  const argHover =
    kind === "command" || kind === "tool" || kind === "app" || kind === "browser" ? liveArgHover(card) : null;
  const subjectTitle =
    kind === "tool"
      ? argHover ?? card.name
      : subject
        ? argHover ??
          `${named?.path && named.full.endsWith(subject.text) ? named.full : subject.text} (${card.name})`
        : undefined;
  const elapsed =
    !running && card.startedAt && card.endedAt ? secondsText(card.endedAt - card.startedAt) : null;
  // v1.349.0: a row that opens previews (and no longer hovers) the MASKED,
  // unfenced output it opens to, so the line and its detail agree.
  const preview = !running ? firstLine(stepDetail ? stepDetail.output : card.output) : "";
  const row = (
    <WorkRow
      icon={ICON[kind]}
      title={kindWords(kind, card.name, { subject: !!subject, running })}
      subject={subject?.text ?? null}
      subjectFile={!!subject?.file}
      subjectTitle={subjectTitle}
      detail={subject ? null : stepHint(kind, card.name)}
      detailTitle={subject ? card.name : argHover ?? card.name}
      running={running}
      failed={failed}
      hint={stepDetail ? undefined : card.output || undefined}
      expand={stepDetail ? { open, onToggle: () => setOpen((v) => !v), controls: panelId } : null}
      meta={
        <>
          {/* v1.323.0: how long a finished step took. */}
          {elapsed ? (
            <>
              <Dot />
              <span data-testid="tool-elapsed" className="shrink-0">
                {elapsed}
              </span>
            </>
          ) : null}
          {/* v1.324.0: an app reporting how far along it is. */}
          {running && card.progress ? (
            <>
              <Dot />
              <span data-testid="tool-progress" className="min-w-0 truncate">
                {progressWords(card.progress)}
              </span>
            </>
          ) : null}
          {failed ? (
            <>
              <Dot />
              <span className="shrink-0 text-tone-danger">failed</span>
            </>
          ) : null}
          {preview ? (
            <>
              <Dot />
              <span className="min-w-0 truncate text-zinc-600">{preview}</span>
            </>
          ) : null}
        </>
      }
    />
  );
  const fix = failed && kind === "app" ? <FixInTools name={card.name} /> : null;
  if (!stepDetail && !fix) return row;
  return (
    <>
      {row}
      {open && stepDetail ? <StepDetailPanel id={panelId} name={card.name} detail={stepDetail} /> : null}
      {fix}
    </>
  );
}

/* ------------------------------------------------- the live summary (v1.348.0) */

/** The FINISHED calls of a running turn as steps, for the live line's counts.
 *  A call still running is not counted yet: the line says what HAS been done
 *  ("read 2 files" while the third file is still being read in its row). */
export function liveSteps(cards: readonly ToolCard[]): ReceiptStep[] {
  const out: ReceiptStep[] = [];
  for (const c of cards) {
    if (!c || c.status !== "done" || typeof c.name !== "string" || !c.name.trim()) continue;
    const ms =
      typeof c.startedAt === "number" && typeof c.endedAt === "number" && c.endedAt >= c.startedAt
        ? c.endedAt - c.startedAt
        : null;
    out.push({ name: c.name, ok: typeof c.ok === "boolean" ? c.ok : null, ms });
  }
  return out;
}

/**
 * The live line's words, shaped like the folded line's ("Worked for 14 s ·
 * read 2 files, ran 1 tool") and counted by the same counter:
 *   "Working for 12 s · read 2 files, searched the web"
 *   "Working for 4 s"                (nothing finished yet)
 *   "Working · read 1 file"          (no clock known, or under a second)
 */
export function liveWorkSummary(steps: ReceiptStep[], elapsedMs: number | null): string {
  const counts = stepCounts(steps);
  const clock = elapsedWords(elapsedMs);
  const head = clock ? `Working for ${clock}` : "Working";
  return counts.length ? `${head} · ${counts.join(", ")}` : head;
}

/** How many of the newest steps stay on screen while a turn runs. */
export const LIVE_RECENT = 3;

/**
 * Which live calls show while the window is closed: the newest LIVE_RECENT,
 * plus any call still RUNNING however old (parallel calls: a running step is
 * never folded away). `earlier` is how many finished calls fold into the
 * "N earlier steps" toggle; folding fewer than two would trade one row for a
 * toggle row and save nothing, so then everything shows.
 */
export function liveWindow(cards: readonly ToolCard[]): { recent: readonly ToolCard[]; earlier: number } {
  const cut = cards.length - LIVE_RECENT;
  if (cut < 2) return { recent: cards, earlier: 0 };
  const recent = cards.filter((c, i) => i >= cut || c.status !== "done");
  const earlier = cards.length - recent.length;
  return earlier < 2 ? { recent: cards, earlier: 0 } : { recent, earlier };
}

/** When the first call started (client clock); null when none says. */
function firstStart(cards: readonly ToolCard[]): number | null {
  let first: number | null = null;
  for (const c of cards) {
    const t = c.startedAt;
    if (typeof t === "number" && Number.isFinite(t) && (first === null || t < first)) first = t;
  }
  return first;
}

/** The live line. A LEAF that ticks itself (useNow): its clock re-renders
 *  this line alone, never the rows, the bubble or the chat page.
 *  `aria-live="off"`: it sits inside the bubble's polite live region, and a
 *  line that changes every second must not be read out every second (the
 *  rows themselves still are, as they arrive). */
function LiveWorkLine({ steps, since }: { steps: ReceiptStep[]; since: number | null }) {
  const now = useNow(since != null);
  const ms = since != null ? Math.max(0, now - since) : null;
  return (
    <div
      data-testid="live-work-summary"
      aria-live="off"
      className="flex min-w-0 items-center gap-2 text-[13px] leading-5 text-zinc-500"
    >
      <Timer size={14} aria-hidden="true" className="shrink-0" />
      <span data-testid="live-work-words" className="min-w-0 truncate tabular-nums">
        {liveWorkSummary(steps, ms)}
      </span>
    </div>
  );
}

/** The live tool calls of a running turn. Memoized: it sits in the
 *  per-frame subtree and its cards change only on a tool frame (v1.257.0
 *  S-02).
 *
 *  v1.348.0: ONE quiet line on top, "Working for 12 s · read 2 files", then
 *  only the newest few rows; older finished ones fold into a quiet "N earlier
 *  steps" toggle that opens them in place, in call order. `since` is when the
 *  turn started (the stream's own clock), so the line counts the whole turn
 *  the way the folded "Worked for …" does; without it the line counts from
 *  the first call. */
export const LiveToolRows = memo(function LiveToolRows({
  cards,
  since,
}: {
  cards: readonly ToolCard[];
  since?: number | null;
}) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  if (!cards.length) return null;
  const { recent, earlier } = liveWindow(cards);
  const shown = open || !earlier ? cards : recent;
  const start = typeof since === "number" && Number.isFinite(since) ? since : firstStart(cards);
  return (
    <div data-testid="live-tool-rows" className="grid gap-1">
      <LiveWorkLine steps={liveSteps(cards)} since={start} />
      {earlier ? (
        <button
          type="button"
          data-testid="live-earlier"
          aria-expanded={open}
          aria-controls={listId}
          onClick={() => setOpen((v) => !v)}
          className="flex min-w-0 items-center gap-2 text-left text-[13px] leading-5 text-zinc-500 transition-colors hover:text-zinc-300 focus-visible:text-zinc-300 focus-visible:underline focus-visible:underline-offset-[3px] focus-visible:outline-none motion-reduce:transition-none"
        >
          <ChevronRight
            size={14}
            aria-hidden="true"
            className={`shrink-0 transition-transform motion-reduce:transition-none ${open ? "rotate-90" : ""}`}
          />
          <span className="min-w-0 truncate">{earlier === 1 ? "1 earlier step" : `${earlier} earlier steps`}</span>
        </button>
      ) : null}
      <div id={listId} data-testid="live-steps" className="grid gap-1">
        {shown.map((c) => (
          <ToolRow key={c.id} card={c} />
        ))}
      </div>
    </div>
  );
});

/** A finished step from the message's record. No raw arguments are stored,
 *  only the step's safe `target` (v1.329.0): "Read harbor.xlsx", "Ran
 *  excel_query". A step saved before targets existed keeps the older row,
 *  words · tool id ("Read · read_file"). v1.349.0: a step saved with the
 *  daemon's masked `detail` opens beneath itself to show it. */
function StepRow({ step }: { step: ReceiptStep }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  // v1.329.0 (G2): the same phrase the expanded receipt says (lib/stepWords).
  const p = stepPhrase(step.name, step.target ?? null);
  const failed = step.ok === false;
  const dur = secondsText(step.ms);
  // v1.349.0: the saved detail (workSteps already read it back safely).
  const stepDetail = step.detail ?? null;
  const row = (
    <WorkRow
      icon={ICON[p.kind]}
      title={p.words}
      subject={p.subject}
      subjectFile={p.file}
      subjectTitle={p.subject ? (p.kind === "tool" ? step.name : `${p.subject} (${step.name})`) : undefined}
      detail={p.hint}
      // v1.352.0 (G22): a browser step's quiet word is its action ("click");
      // its tool id is the hover.
      detailTitle={p.hint && p.hint !== step.name ? step.name : undefined}
      failed={failed}
      expand={stepDetail ? { open, onToggle: () => setOpen((v) => !v), controls: panelId } : null}
      meta={
        <>
          {dur ? (
            <>
              <Dot />
              <span className="shrink-0">{dur}</span>
            </>
          ) : null}
          {failed ? (
            <>
              <Dot />
              <span className="shrink-0 text-tone-danger">failed</span>
            </>
          ) : null}
        </>
      }
    />
  );
  const fix = failed && p.kind === "app" ? <FixInTools name={step.name} /> : null;
  if (!stepDetail && !fix) return row;
  return (
    <>
      {row}
      {open && stepDetail ? <StepDetailPanel id={panelId} name={step.name} detail={stepDetail} /> : null}
      {fix}
    </>
  );
}

/* --------------------------------------------------------------- the fold */

/**
 * The ONE line above a settled reply: "Worked for 3.2 s · read 2 files", a
 * chevron, and the rows underneath when opened. A plain answer with stored
 * timing reads "Answered in 2.1 s" (v1.355.0, nothing to open). Renders
 * nothing for a reply with neither.
 */
export function WorkLine(props: WorkInput) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  // v1.349.0 (integrate): the steps are read ONCE per change of what they are
  // read from — workSteps masks every saved detail again (up to 40 a
  // message), and this line used to read them twice on every render.
  const { steps: savedSteps, toolsUsed } = props;
  const steps = useMemo(() => workSteps({ steps: savedSteps, toolsUsed }), [savedSteps, toolsUsed]);
  const summary = summaryOf(steps, props);
  if (!summary) return null;
  if (plainAnswer(steps, props)) {
    // v1.355.0 (G63): "Answered in 2.1 s". The same quiet line and hairline
    // as the folded one, but not a button: there is nothing under it to
    // open, so no chevron and no expanded state for a screen reader.
    return (
      <div data-testid="work-line" data-kind="answered" className="mb-2">
        <p className="flex w-full min-w-0 items-center gap-2 border-b border-white/[0.08] pb-2 text-[13px] text-zinc-500">
          <Check size={14} aria-hidden="true" data-testid="work-line-check" className="shrink-0" />
          <span data-testid="work-summary" className="min-w-0 truncate">
            {summary}
          </span>
        </p>
      </div>
    );
  }
  const thinking = typeof props.thinking === "string" ? props.thinking : "";
  // v1.351.0 (G07): a turn where any step failed folds under a warning mark,
  // never a check, and its "N failed" piece carries the same tone. The row
  // stays the same quiet line; only the theme's warn token marks it.
  const failed = failedSteps(steps);
  const failPiece = failed ? failedWords(failed) : "";
  const split = failPiece && summary.endsWith(failPiece) ? summary.length - failPiece.length : -1;
  return (
    <div data-testid="work-line" className="mb-2">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((v) => !v)}
        data-outcome={failed ? "some-failed" : "ok"}
        className="flex w-full min-w-0 items-center gap-2 border-b border-white/[0.08] pb-2 text-left text-[13px] text-zinc-500 transition-colors hover:text-zinc-300 focus-visible:text-zinc-300 focus-visible:outline-none motion-reduce:transition-none"
      >
        {failed ? (
          <TriangleAlert
            size={14}
            aria-hidden="true"
            data-testid="work-line-warn"
            className="shrink-0 text-tone-warn"
          />
        ) : (
          <Check size={14} aria-hidden="true" data-testid="work-line-check" className="shrink-0" />
        )}
        <span data-testid="work-summary" className="min-w-0 truncate">
          {split >= 0 ? (
            <>
              {summary.slice(0, split)}
              <span data-testid="work-summary-failed" className="text-tone-warn">
                {failPiece}
              </span>
            </>
          ) : (
            summary
          )}
        </span>
        <ChevronDown
          size={14}
          aria-hidden="true"
          className={`shrink-0 transition-transform motion-reduce:transition-none ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div id={panelId} data-testid="work-rows" className="mt-2 grid gap-1">
          {thinking.trim() ? (
            <ThinkingDisclosure text={thinking} seconds={props.thinkingSeconds ?? null} />
          ) : null}
          {steps.map((st, i) => (
            <StepRow key={`${st.name}-${i}`} step={st} />
          ))}
        </div>
      )}
    </div>
  );
}
