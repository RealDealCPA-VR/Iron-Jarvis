"use client";

/**
 * "/" and "@" in a Build session's box (v1.373.0, IJ harness part 2 Phase 2):
 * the two menus, the chips a pick leaves in the box, the line under a sent
 * message that says what rode it, and the "/rewind" list.
 *
 * The rules live in `lib/buildComposer` (pure, tested on their own); the key
 * handling lives with the box in HarnessSession. These are drawn INSIDE the
 * composer card and ABOVE it (`useAtMenuFit` with `preferAbove`: as tall as
 * the room above the card allows, never over the box and never past the top
 * of the window; below the card only when not even a row fits above — v1.373.0
 * review), the width of the card — so a phone-width window never pans.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { AtSign, Cpu, FileText, History, Layers, ListChecks, Loader2, Sparkles, X } from "lucide-react";
import { useAtMenuFit } from "@/lib/chatRefsMenuFit";
import { listRewindPoints, type RewindPoint } from "@/lib/buildRewind";
import type { AtFile, CommandId, PickedSkill, SlashRow } from "@/lib/buildComposer";
import { RewindControl } from "@/components/harness/RewindControl";

const PANEL =
  "absolute left-0 right-0 z-20 max-h-[min(28rem,60vh)] overflow-y-auto rounded-xl border border-white/10 bg-zinc-900 shadow-lg shadow-black/40";
/** Above the card (the usual side), or below it when not even a row fits
 *  above (lib/chatRefsMenuFit `preferAbove`) — never over the box. */
const SIDE = { above: "bottom-full mb-2", below: "top-full mt-2" } as const;

const ROW =
  "flex w-full min-w-0 items-center gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors hover:bg-accent/[0.12] hover:text-accent-soft";

/** A panel above the box (its parent, the composer card) — below it only
 *  when not even a row fits above. */
function AboveBox({
  testId,
  fitKey,
  children,
}: {
  testId: string;
  fitKey: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const fit = useAtMenuFit(ref, true, fitKey, { preferAbove: true });
  const side = fit?.side ?? "above";
  return (
    <div
      ref={ref}
      data-testid={testId}
      data-side={side}
      style={fit ? { maxHeight: fit.maxHeight } : undefined}
      className={`${PANEL} ${SIDE[side]}`}
    >
      {children}
    </div>
  );
}

function Hint({ children }: { children: ReactNode }) {
  return <div className="px-2.5 pb-1 pt-1.5 text-[11px] text-zinc-500">{children}</div>;
}

const COMMAND_ICON: Record<CommandId, typeof ListChecks> = {
  plan: ListChecks,
  compact: Layers,
  rewind: History,
  model: Cpu,
};

/** A row keeps the box's focus when pressed (the caret and the "/word" stay). */
const keepFocus = (e: React.MouseEvent) => e.preventDefault();

/** The "/" menu: the session's commands, then the folder's skills, then yours. */
export function SlashMenu({
  listId,
  rows,
  active,
  loading,
  note,
  onPick,
  onHover,
}: {
  /** The listbox's id; row i is `${listId}-row-${i}` (the box points at it). */
  listId: string;
  rows: SlashRow[];
  active: number;
  /** A skill list is still on its way. */
  loading: boolean;
  /** Why the folder's own skills are not offered (an invalid harness layer). */
  note: string;
  onPick: (row: SlashRow) => void;
  onHover: (i: number) => void;
}) {
  return (
    <AboveBox testId="harness-slash-menu" fitKey={`${rows.length}|${loading}|${note}`}>
      <div id={listId} role="listbox" aria-label="Commands and skills" className="p-1">
        <Hint>Commands and skills. ↑↓ and Enter, or keep typing</Hint>
        {rows.map((row, i) => {
          const on = i === active;
          const cls = `${ROW} ${on ? "bg-accent/[0.12] text-accent-soft" : "text-zinc-300"}`;
          if (row.kind === "command") {
            const Icon = COMMAND_ICON[row.command.id];
            return (
              <button
                key={row.key}
                id={`${listId}-row-${i}`}
                type="button"
                role="option"
                aria-selected={on}
                data-testid="harness-slash-command"
                data-name={row.command.name}
                ref={(el) => {
                  if (on) el?.scrollIntoView?.({ block: "nearest" });
                }}
                onMouseDown={keepFocus}
                onMouseEnter={() => onHover(i)}
                onClick={() => onPick(row)}
                className={cls}
              >
                <Icon size={12} aria-hidden className="shrink-0 text-accent-soft/70" />
                <span className="shrink-0 font-mono text-[12px]">/{row.command.name}</span>
                <span className="min-w-0 truncate text-[11px] text-zinc-500">{row.command.description}</span>
              </button>
            );
          }
          const s = row.skill;
          return (
            <button
              key={row.key}
              id={`${listId}-row-${i}`}
              type="button"
              role="option"
              aria-selected={on}
              data-testid="harness-slash-skill"
              data-name={s.name}
              data-source={s.source}
              title={s.description}
              ref={(el) => {
                if (on) el?.scrollIntoView?.({ block: "nearest" });
              }}
              onMouseDown={keepFocus}
              onMouseEnter={() => onHover(i)}
              onClick={() => onPick(row)}
              className={cls}
            >
              <Sparkles size={12} aria-hidden className="shrink-0 text-accent-soft/70" />
              <span className="min-w-0 max-w-[45%] shrink-0 truncate font-mono text-[12px]">{s.name}</span>
              {s.source === "folder" ? (
                <span className="shrink-0 text-[11px] text-zinc-500">this folder</span>
              ) : null}
              <span className="min-w-0 truncate text-[11px] text-zinc-500">{s.description}</span>
            </button>
          );
        })}
        {loading ? (
          <p className="flex items-center gap-1.5 px-2.5 py-1.5 text-[12px] text-zinc-500">
            <Loader2 size={12} aria-hidden className="animate-spin" /> Loading skills…
          </p>
        ) : rows.length === 0 ? (
          <p className="px-2.5 py-1.5 text-[12px] text-zinc-500">No command or skill matches.</p>
        ) : null}
        {note ? <p className="px-2.5 py-1 text-[11px] text-zinc-500">{note}</p> : null}
      </div>
    </AboveBox>
  );
}

/** Where a file sits in the folder: the path of the folder holding it. */
function whereOf(rel: string): string {
  const i = rel.lastIndexOf("/");
  return i > 0 ? rel.slice(0, i) : "";
}

/** The "@" menu: files in this session's folder. */
export function AtMenu({
  listId,
  rows,
  more,
  active,
  loading,
  failed,
  onPick,
  onHover,
}: {
  /** The listbox's id; row i is `${listId}-row-${i}` (the box points at it). */
  listId: string;
  rows: AtFile[];
  more: number;
  active: number;
  loading: boolean;
  failed: boolean;
  onPick: (f: AtFile) => void;
  onHover: (i: number) => void;
}) {
  return (
    <AboveBox testId="harness-at-menu" fitKey={`${rows.length}|${more}|${loading}|${failed}`}>
      <div id={listId} role="listbox" aria-label="Files in this folder" className="p-1">
        <Hint>Files in this folder. The file is read with your message</Hint>
        {rows.map((f, i) => {
          const on = i === active;
          const where = whereOf(f.rel);
          return (
            <button
              key={f.path}
              id={`${listId}-row-${i}`}
              type="button"
              role="option"
              aria-selected={on}
              data-testid="harness-at-file"
              data-path={f.path}
              title={f.rel}
              ref={(el) => {
                if (on) el?.scrollIntoView?.({ block: "nearest" });
              }}
              onMouseDown={keepFocus}
              onMouseEnter={() => onHover(i)}
              onClick={() => onPick(f)}
              className={`${ROW} ${on ? "bg-accent/[0.12] text-accent-soft" : "text-zinc-300"}`}
            >
              <FileText size={12} aria-hidden className="shrink-0 text-accent-soft/70" />
              <span className="min-w-0 truncate text-[12px]">{f.name}</span>
              {where ? (
                <span className="ml-auto min-w-0 shrink truncate pl-2 text-[11px] text-zinc-500">{where}</span>
              ) : null}
            </button>
          );
        })}
        {loading ? (
          <p className="flex items-center gap-1.5 px-2.5 py-1.5 text-[12px] text-zinc-500">
            <Loader2 size={12} aria-hidden className="animate-spin" /> Reading the folder…
          </p>
        ) : failed ? (
          <p className="px-2.5 py-1.5 text-[12px] text-zinc-500">This folder&apos;s files could not be listed.</p>
        ) : rows.length === 0 ? (
          <p className="px-2.5 py-1.5 text-[12px] text-zinc-500">No file in this folder matches.</p>
        ) : null}
        {more > 0 ? (
          <p data-testid="harness-at-more" className="px-2.5 py-1 text-[11px] text-zinc-500">
            {more} more, type to narrow
          </p>
        ) : null}
      </div>
    </AboveBox>
  );
}

const CHIP = "flex max-w-[220px] items-center gap-1.5 rounded-lg bg-white/[0.06] py-1 pl-2 pr-1.5 text-[12px] text-zinc-300";

/** What a pick left in the box: the skill and the files, each with a ×. */
export function PickedChips({
  skill,
  files,
  onRemoveSkill,
  onRemoveFile,
}: {
  skill: PickedSkill | null;
  files: AtFile[];
  onRemoveSkill: () => void;
  onRemoveFile: (i: number) => void;
}) {
  if (!skill && files.length === 0) return null;
  return (
    <div data-testid="harness-picks" className="flex flex-wrap items-center gap-2 px-3 pt-2">
      {skill ? (
        <span
          data-testid="harness-skill-chip"
          data-source={skill.source}
          title={skill.source === "folder" ? `${skill.name}, a skill of this folder, for this message` : `${skill.name}, for this message`}
          className={CHIP}
        >
          <Sparkles size={12} aria-hidden className="shrink-0 text-accent-soft/70" />
          <span className="truncate">{skill.name}</span>
          <button
            type="button"
            aria-label={`Remove the skill ${skill.name}`}
            onClick={onRemoveSkill}
            className="shrink-0 rounded text-zinc-500 hover:text-zinc-200"
          >
            <X size={13} aria-hidden />
          </button>
        </span>
      ) : null}
      {files.map((f, i) => (
        <span key={f.path} data-testid="harness-at-chip" title={f.rel} className={CHIP}>
          <AtSign size={12} aria-hidden className="shrink-0 text-zinc-500" />
          <span className="truncate">{f.name}</span>
          <button
            type="button"
            aria-label={`Remove ${f.name}`}
            onClick={() => onRemoveFile(i)}
            className="shrink-0 rounded text-zinc-500 hover:text-zinc-200"
          >
            <X size={13} aria-hidden />
          </button>
        </span>
      ))}
    </div>
  );
}

/** Under a sent message: the folder files picked with "@" and the skill. */
export function SentPicks({ files, skill }: { files?: readonly string[]; skill?: string }) {
  const list = Array.isArray(files) ? files.filter((p) => typeof p === "string" && p) : [];
  if (!list.length && !skill) return null;
  return (
    <div data-testid="harness-sent-picks" className="flex max-w-[88%] flex-wrap justify-end gap-1.5">
      {skill ? (
        <span data-testid="harness-sent-skill" className="flex items-center gap-1 rounded-lg bg-white/[0.06] px-2 py-1 text-[12px] text-zinc-300">
          <Sparkles size={12} aria-hidden /> {skill}
        </span>
      ) : null}
      {list.map((p, i) => (
        <span
          key={`${p}-${i}`}
          data-testid="harness-sent-file"
          title={p}
          className="flex items-center gap-1 rounded-lg bg-white/[0.06] px-2 py-1 text-[12px] text-zinc-300"
        >
          <AtSign size={12} aria-hidden /> {p.split(/[\\/]/).pop() || p}
        </span>
      ))}
    </div>
  );
}

/** What going back to before this message undoes (its turn and every later
 *  one — lib/buildRewind sums them). Each write or edit is one change. */
function pointWords(p: RewindPoint): string {
  const parts: string[] = [];
  if (p.files) parts.push(p.files === 1 ? "1 file change" : `${p.files} file changes`);
  if (p.commands) parts.push(p.commands === 1 ? "1 command" : `${p.commands} commands`);
  return parts.length ? `${parts.join(", ")} from here on` : "No file changes from here on";
}

/** "/rewind": the session's messages, newest first, each with the existing
 *  two-press "Rewind to here" (lib/buildRewind, the daemon's checkpoints). */
export function RewindList({
  threadId,
  disabled,
  onRewound,
  onTouched,
  onClose,
}: {
  threadId: string;
  /** A reply is running: rewinding waits (the daemon refuses it too). */
  disabled: boolean;
  onRewound: (question: string) => void;
  onTouched: () => void;
  onClose: () => void;
}) {
  const [points, setPoints] = useState<RewindPoint[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    listRewindPoints(threadId)
      .then((p) => {
        if (live) setPoints(p);
      })
      .catch((e) => {
        if (live) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      live = false;
    };
  }, [threadId]);
  return (
    <AboveBox testId="harness-rewind-list" fitKey={`${points?.length ?? -1}|${error ?? ""}`}>
      <div role="group" aria-label="Rewind to before one of your messages" className="space-y-1 p-2">
        <div className="flex items-center gap-2 px-1">
          <p className="min-w-0 flex-1 text-[12px] font-medium text-zinc-200">Go back to before one of your messages</p>
          <button
            type="button"
            data-testid="harness-rewind-close"
            aria-label="Close"
            onClick={onClose}
            className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-200"
          >
            <X size={13} aria-hidden />
          </button>
        </div>
        {disabled ? <p className="px-1 text-[12px] text-zinc-500">Stop the reply first, then rewind.</p> : null}
        {error ? (
          <p role="alert" className="px-1 text-[12px] text-tone-danger">
            {error}
          </p>
        ) : points === null ? (
          <p className="flex items-center gap-1.5 px-1 text-[12px] text-zinc-500">
            <Loader2 size={12} aria-hidden className="animate-spin" /> Reading the messages…
          </p>
        ) : points.length === 0 ? (
          <p className="px-1 text-[12px] text-zinc-500">Nothing to go back to yet.</p>
        ) : (
          points.map((p) => (
            <div key={p.turnId} data-testid="harness-rewind-point" className="space-y-0.5 rounded-lg px-1 py-1.5">
              <p className="line-clamp-2 break-words text-[13px] text-zinc-200">{p.question || "(a message with files only)"}</p>
              <div className="flex flex-wrap items-center justify-between gap-1">
                <span className="text-[11px] text-zinc-500">{pointWords(p)}</span>
                <RewindControl
                  shown
                  threadId={threadId}
                  turnId={p.turnId}
                  disabled={disabled}
                  onRewound={(q) => {
                    onClose();
                    onRewound(q);
                  }}
                  onTouched={onTouched}
                />
              </div>
            </div>
          ))
        )}
      </div>
    </AboveBox>
  );
}
