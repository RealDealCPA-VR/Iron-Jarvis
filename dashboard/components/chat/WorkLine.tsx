"use client";

/**
 * Calm chat W1-5 (v1.326.0): the WORK behind a reply, as quiet grey rows.
 *
 * Process detail must never look like a card. While a turn runs, each tool
 * call, the model's thinking and the step it is on is ONE grey line: a 14px
 * icon, a few plain words, a small dot, and a short summary (a file name with
 * a dotted underline, a search, a duration). The step still running carries a
 * spinner and a soft pulse, both of which stop under reduced motion.
 *
 * When the answer lands, all of it folds under ONE line above the reply:
 * "Worked for 3.2 s · read 2 files, ran 1 tool", with a chevron. Pressing it
 * shows the rows again. The line is built only from what the message already
 * stores (steps, timing, the tools that ran, the thinking and how long it
 * took); a reply with none of that shows no line at all.
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
 */

import { memo, useId, useState, type ReactNode } from "react";
import {
  Check,
  ChevronDown,
  FilePen,
  FilePlus,
  FileText,
  Folder,
  Globe,
  Loader2,
  Search,
  SquareTerminal,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";
import type { ToolCard } from "@/lib/useChatStream";
import { ThinkingDisclosure } from "@/components/chat/ThinkingDisclosure";
import { secondsText, type ReceiptStep, type ReceiptTiming } from "@/components/chat/TurnReceipt";
import { cleanTarget, secretLooking, stepTarget, toolKind, type WorkKind } from "@/lib/workTarget";

/* ------------------------------------------------------------- vocabulary */

// v1.329.0: the kinds table moved to lib/workTarget (the stream hook reads it
// to keep each step's safe target); re-exported here for existing callers.
export { toolKind, type WorkKind };

function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

function times(n: number): string {
  return n === 1 ? "" : ` ${n} times`;
}

interface KindWords {
  icon: LucideIcon;
  /** Row title once the step is done. */
  done: string;
  /** Row title while it runs. */
  doing: string;
  /** v1.329.0: the words BEFORE a target, once done and while running —
   *  "Searched the web for" + "pier 9 hours", "Read" + "harbor.xlsx". */
  doneOn: string;
  doingOn: string;
  /** The count in the folded line ("read 2 files"). */
  count: (n: number) => string;
}

const WORDS: Record<WorkKind, KindWords> = {
  read: { icon: FileText, done: "Read", doing: "Reading", doneOn: "Read", doingOn: "Reading", count: (n) => `read ${plural(n, "file")}` },
  make: { icon: FilePlus, done: "Made", doing: "Making", doneOn: "Made", doingOn: "Making", count: (n) => `made ${plural(n, "file")}` },
  change: { icon: FilePen, done: "Changed", doing: "Changing", doneOn: "Changed", doingOn: "Changing", count: (n) => `changed ${plural(n, "file")}` },
  folder: { icon: Folder, done: "Looked in", doing: "Looking in", doneOn: "Looked in", doingOn: "Looking in", count: (n) => `looked in ${plural(n, "folder")}` },
  search: { icon: Search, done: "Searched files", doing: "Searching files", doneOn: "Searched files for", doingOn: "Searching files for", count: (n) => `searched files${times(n)}` },
  web: { icon: Globe, done: "Searched the web", doing: "Searching the web", doneOn: "Searched the web for", doingOn: "Searching the web for", count: (n) => `searched the web${times(n)}` },
  page: { icon: Globe, done: "Read a web page", doing: "Reading a web page", doneOn: "Read a page on", doingOn: "Reading a page on", count: (n) => `read ${plural(n, "web page")}` },
  command: { icon: SquareTerminal, done: "Ran", doing: "Running", doneOn: "Ran", doingOn: "Running", count: (n) => `ran ${plural(n, "command")}` },
  tool: { icon: Wrench, done: "Ran", doing: "Running", doneOn: "Ran", doingOn: "Running", count: (n) => `ran ${plural(n, "tool")}` },
};

/** v1.329.0: the subject a row names after its words, and whether it is a
 *  file. A known tool names its safe target; any other tool names its own id
 *  ("Ran excel_query"). Null = no subject: the row keeps the older shape,
 *  words · tool id (a step saved before targets existed). */
function rowSubject(kind: WorkKind, name: string, target: string | null): { text: string; file: boolean } | null {
  if (kind === "tool") return { text: name, file: false };
  if (!target) return null;
  return { text: target, file: kind === "read" || kind === "make" || kind === "change" || kind === "folder" };
}

/** The order counts are said in: looking before making, tools last. */
const KIND_ORDER: WorkKind[] = ["read", "folder", "search", "page", "web", "change", "make", "command", "tool"];

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
      out.push({
        name: st.name,
        ok: typeof st.ok === "boolean" ? st.ok : null,
        ms: typeof st.ms === "number" && Number.isFinite(st.ms) && st.ms >= 0 ? st.ms : null,
        ...(target ? { target } : {}),
      });
    }
  }
  if (out.length) return out;
  if (Array.isArray(input.toolsUsed)) {
    for (const t of input.toolsUsed) {
      if (typeof t === "string" && t.trim()) out.push({ name: t, ok: null, ms: null });
    }
  }
  return out;
}

/** "read 2 files, ran 1 tool" pieces, in a fixed order, plus "1 failed". */
export function workCounts(steps: ReceiptStep[]): string[] {
  const n = new Map<WorkKind, number>();
  let failed = 0;
  for (const st of steps) {
    const k = toolKind(st.name);
    n.set(k, (n.get(k) ?? 0) + 1);
    if (st.ok === false) failed += 1;
  }
  const out: string[] = [];
  for (const k of KIND_ORDER) {
    const c = n.get(k);
    if (c) out.push(WORDS[k].count(c));
  }
  if (failed) out.push(`${failed} failed`);
  return out;
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
 * The folded line's words, or null when the reply has no process detail:
 *   "Worked for 3.2 s · read 2 files, ran 1 tool"
 *   "Read 2 files"                         (no timing stored)
 *   "Worked for 6.0 s · thought for 4 s"   (thinking only)
 */
export function workSummary(input: WorkInput): string | null {
  const steps = workSteps(input);
  const thought = typeof input.thinking === "string" && input.thinking.trim().length > 0;
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
}: WorkRowProps) {
  const titleTone = running
    ? "animate-pulse text-zinc-300 motion-reduce:animate-none"
    : failed
      ? "text-tone-danger"
      : "text-zinc-400";
  return (
    <div
      data-testid="work-row"
      data-state={running ? "running" : failed ? "failed" : "done"}
      title={hint}
      className="flex min-w-0 items-center gap-2 text-[13px] leading-5 text-zinc-500"
    >
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
  const running = card.status !== "done";
  const failed = !running && card.ok === false;
  const kind = toolKind(card.name);
  const w = WORDS[kind];
  const subject = rowSubject(kind, card.name, stepTarget(card.name, card.args));
  // The hover: a live FILE keeps its full path (it is on this screen only and
  // is never saved); a search or a page shows the safe words and the tool id.
  const named = subject?.file ? argDetail(card.args) : null;
  // A running command or an unknown tool shows what it was CALLED WITH on
  // hover ("git push origin main (shell)"): with a grant there is no card, so
  // this is the only place on the chat page that says which command runs.
  // Screen only, never saved; dropped whole when it looks like a credential.
  const argHover = kind === "command" || kind === "tool" ? liveArgHover(card) : null;
  const subjectTitle =
    kind === "tool"
      ? argHover ?? card.name
      : subject
        ? argHover ??
          `${named?.path && named.full.endsWith(subject.text) ? named.full : subject.text} (${card.name})`
        : undefined;
  const elapsed =
    !running && card.startedAt && card.endedAt ? secondsText(card.endedAt - card.startedAt) : null;
  const preview = !running ? firstLine(card.output) : "";
  return (
    <WorkRow
      icon={w.icon}
      title={subject ? (running ? w.doingOn : w.doneOn) : running ? w.doing : w.done}
      subject={subject?.text ?? null}
      subjectFile={!!subject?.file}
      subjectTitle={subjectTitle}
      detail={subject ? null : card.name}
      detailTitle={subject ? card.name : argHover ?? card.name}
      running={running}
      failed={failed}
      hint={card.output || undefined}
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
}

/** The live tool calls of a running turn, one grey row each. Memoized: it
 *  sits in the per-frame subtree and its cards change only on a tool frame
 *  (v1.257.0 S-02). */
export const LiveToolRows = memo(function LiveToolRows({ cards }: { cards: readonly ToolCard[] }) {
  if (!cards.length) return null;
  return (
    <div data-testid="live-tool-rows" className="grid gap-1">
      {cards.map((c) => (
        <ToolRow key={c.id} card={c} />
      ))}
    </div>
  );
});

/** A finished step from the message's record. No arguments are stored, only
 *  the step's safe `target` (v1.329.0): "Read harbor.xlsx", "Ran
 *  excel_query". A step saved before targets existed keeps the older row,
 *  words · tool id ("Read · read_file"). */
function StepRow({ step }: { step: ReceiptStep }) {
  const kind = toolKind(step.name);
  const w = WORDS[kind];
  const failed = step.ok === false;
  const dur = secondsText(step.ms);
  const subject = rowSubject(kind, step.name, step.target ?? null);
  return (
    <WorkRow
      icon={w.icon}
      title={subject ? w.doneOn : w.done}
      subject={subject?.text ?? null}
      subjectFile={!!subject?.file}
      subjectTitle={subject ? (kind === "tool" ? step.name : `${subject.text} (${step.name})`) : undefined}
      detail={subject ? null : step.name}
      failed={failed}
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
}

/* --------------------------------------------------------------- the fold */

/**
 * The ONE line above a settled reply: "Worked for 3.2 s · read 2 files", a
 * chevron, and the rows underneath when opened. Renders nothing for a reply
 * with no process detail.
 */
export function WorkLine(props: WorkInput) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const summary = workSummary(props);
  if (!summary) return null;
  const steps = workSteps(props);
  const thinking = typeof props.thinking === "string" ? props.thinking : "";
  return (
    <div data-testid="work-line" className="mb-2">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full min-w-0 items-center gap-2 border-b border-white/[0.08] pb-2 text-left text-[13px] text-zinc-500 transition-colors hover:text-zinc-300 focus-visible:text-zinc-300 focus-visible:outline-none"
      >
        <Check size={14} aria-hidden="true" className="shrink-0" />
        <span data-testid="work-summary" className="min-w-0 truncate">
          {summary}
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
