"use client";

/**
 * Coding sessions › TONIGHT (v1.398.0, night shift N1 — docs/NIGHT-SHIFT-PLAN.md).
 *
 * One calm list of coding tasks queued for the night. Each task is a folder,
 * the user's words and how it runs (the default model, one model, the model
 * team, or "try on" 2–3 models). Inside the night window Iron Jarvis starts
 * each one by itself as a NEW coding session on its own branch — at most N at
 * a time, never two in one folder — and the row says Queued / Working /
 * Done / Failed / Needs you / Stopped, with what happened folded under it.
 *
 * Queued rows can be moved up or down (the order they start in), edited,
 * cancelled (never starts) or removed (two presses). A running task can be
 * opened or stopped; an ended one opened or removed. No new card styles: the
 * session list's own row look, the calm Badge and ConfirmButton.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Loader2, Moon, Plus } from "lucide-react";
import { Badge, ConfirmButton } from "@/components/ui";
import { modelPickWords } from "@/lib/onboarding";
import { useModels } from "@/lib/useModels";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import type { ModelOption } from "@/lib/types";
import {
  EMERGENCY_LINE,
  NIGHT_POLL_MS,
  addNight,
  cancelNight,
  editNight,
  folderName,
  howItRunsWords,
  listNight,
  moveNight,
  nightLimitsLine,
  nightStateWords,
  removeNight,
  taskTitle,
  type NightDraft,
  type NightList,
  type NightPick,
  type NightTask,
} from "@/lib/nightShift";

const QUIET_BUTTON =
  "inline-flex min-h-7 items-center gap-1 rounded-lg px-2 py-1 text-xs text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:opacity-40";

const MAX_TRY_ON = 3;

function errorWords(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function pickKey(p: NightPick): string {
  return `${p.provider}::${p.model}`;
}

type How = "default" | "team" | "try" | `model:${string}`;

function howOf(d: NightDraft): How {
  if ((d.tryOn ?? []).length) return "try";
  if (d.team) return "team";
  if (d.provider) return `model:${pickKey({ provider: d.provider, model: d.model ?? "" })}`;
  return "default";
}

/** The add / edit fields (one form for both). */
function TaskForm({
  initial,
  folders,
  models,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial: NightDraft;
  folders: readonly string[];
  models: readonly ModelOption[];
  submitLabel: string;
  onSubmit: (draft: NightDraft) => Promise<string | null>;
  onCancel: () => void;
}) {
  const [folder, setFolder] = useState(initial.folder);
  const [words, setWords] = useState(initial.words);
  const [how, setHow] = useState<How>(howOf(initial));
  const [tryOn, setTryOn] = useState<NightPick[]>(initial.tryOn ?? []);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const nameOf = (p: NightPick) => modelPickWords(p, models);

  async function submit() {
    if (busy) return;
    const draft: NightDraft = { folder: folder.trim(), words: words.trim() };
    if (how === "team") draft.team = true;
    else if (how === "try") draft.tryOn = tryOn;
    else if (how.startsWith("model:")) {
      const [provider, model] = how.slice("model:".length).split("::");
      draft.provider = provider;
      draft.model = model ?? "";
    }
    setBusy(true);
    setProblem(null);
    const err = await onSubmit(draft);
    setBusy(false);
    if (err) setProblem(err);
  }

  function toggle(p: NightPick) {
    setTryOn((cur) =>
      cur.some((c) => pickKey(c) === pickKey(p))
        ? cur.filter((c) => pickKey(c) !== pickKey(p))
        : cur.length >= MAX_TRY_ON
          ? cur
          : [...cur, p],
    );
  }

  return (
    <form
      data-testid="tonight-form"
      className="space-y-2 rounded-xl px-1 py-2"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <label className="block space-y-1">
        <span className="text-[12px] text-zinc-400">Folder</span>
        <input
          value={folder}
          onChange={(e) => setFolder(e.target.value)}
          list="tonight-folders"
          placeholder="A folder in a git repository"
          data-testid="tonight-folder"
          className="field w-full py-1.5 text-[13px]"
        />
        <datalist id="tonight-folders">
          {folders.map((f) => (
            <option key={f} value={f} />
          ))}
        </datalist>
      </label>
      <label className="block space-y-1">
        <span className="text-[12px] text-zinc-400">What to do</span>
        <textarea
          value={words}
          onChange={(e) => setWords(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              void submit();
            }
          }}
          rows={3}
          placeholder="Fix the failing tests in the parser and say what you changed."
          data-testid="tonight-words"
          className="field w-full resize-y py-1.5 text-[13px]"
        />
      </label>
      <label className="block space-y-1">
        <span className="text-[12px] text-zinc-400">Runs on</span>
        <select
          value={how}
          onChange={(e) => setHow(e.target.value as How)}
          data-testid="tonight-how"
          className="field w-full py-1.5 text-[13px]"
        >
          <option value="default">Your default model</option>
          {models.map((m) => (
            <option key={pickKey(m)} value={`model:${pickKey(m)}`} title={`${m.provider} · ${m.model}`}>
              {modelPickWords(m, models)}
            </option>
          ))}
          <option value="team">Your model team</option>
          <option value="try">Try it on several models</option>
        </select>
      </label>
      {how === "try" ? (
        <fieldset data-testid="tonight-try-on" className="space-y-1">
          <legend className="text-[12px] text-zinc-400">Pick 2 or 3 models; each tries it on its own branch.</legend>
          <div className="max-h-40 space-y-0.5 overflow-y-auto">
            {models.map((m) => {
              const on = tryOn.some((c) => pickKey(c) === pickKey(m));
              return (
                <label key={pickKey(m)} className="flex min-w-0 items-center gap-2 text-[13px] text-zinc-300">
                  <input
                    type="checkbox"
                    checked={on}
                    disabled={!on && tryOn.length >= MAX_TRY_ON}
                    onChange={() => toggle({ provider: m.provider, model: m.model })}
                  />
                  <span className="min-w-0 truncate">{nameOf(m)}</span>
                </label>
              );
            })}
          </div>
        </fieldset>
      ) : null}
      {problem ? (
        <p role="alert" data-testid="tonight-problem" className="text-[12px] text-tone-danger">
          {problem}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="submit"
          disabled={busy || !folder.trim() || !words.trim()}
          data-testid="tonight-submit"
          className="inline-flex min-h-8 items-center gap-1.5 rounded-lg bg-accent/15 px-3 py-1 text-[13px] text-zinc-100 transition-colors hover:bg-accent/25 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:opacity-50"
        >
          {busy ? <Loader2 size={12} className="animate-spin" /> : null}
          {submitLabel}
        </button>
        <button type="button" onClick={onCancel} className={QUIET_BUTTON}>
          Close
        </button>
      </div>
    </form>
  );
}

function TaskRow({
  task,
  first,
  last,
  folders,
  models,
  onChanged,
  onOpenSession,
}: {
  task: NightTask;
  first: boolean;
  last: boolean;
  folders: readonly string[];
  models: readonly ModelOption[];
  onChanged: () => void;
  onOpenSession: (threadId: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const words = nightStateWords(task.state);
  const nameOf = (p: NightPick) => modelPickWords(p, models);

  async function act(fn: () => Promise<unknown>) {
    setProblem(null);
    try {
      await fn();
    } catch (e) {
      setProblem(errorWords(e));
    }
    onChanged();
  }

  if (editing) {
    return (
      <li data-testid={`tonight-task-${task.id}`} className="py-1">
        <TaskForm
          initial={{
            folder: task.folder,
            words: task.words,
            provider: task.provider,
            model: task.model,
            team: task.team,
            tryOn: task.tryOn,
          }}
          folders={folders}
          models={models}
          submitLabel="Save"
          onSubmit={async (draft) => {
            try {
              await editNight(task.id, draft);
              setEditing(false);
              onChanged();
              return null;
            } catch (e) {
              return errorWords(e);
            }
          }}
          onCancel={() => setEditing(false)}
        />
      </li>
    );
  }

  return (
    <li data-testid={`tonight-task-${task.id}`} data-state={task.state} className="space-y-1 rounded-[10px] px-2 py-2">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <Badge value={words.word} tone={words.tone} keepCase data-testid={`tonight-state-${task.id}`} data-status={task.state} />
        <span className="min-w-0 flex-1 break-words text-[13px] text-zinc-200">{taskTitle(task.words)}</span>
      </div>
      <p className="break-words text-[12px] text-zinc-500">
        {folderName(task.folder)}
        {task.branch ? ` · ${task.branch}` : ""} · {howItRunsWords(task, nameOf)}
      </p>
      {task.notes.length ? (
        <details className="text-[12px] text-zinc-400" data-testid={`tonight-notes-${task.id}`}>
          <summary className="cursor-pointer select-none text-zinc-500 hover:text-zinc-300">
            What happened ({task.notes.length})
          </summary>
          <ul className="mt-1 space-y-0.5 pl-3">
            {task.notes.map((n, i) => (
              <li key={`${n.kind}-${i}`} className="break-words">
                {n.text}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <div className="flex flex-wrap items-center gap-1">
        {task.state === "queued" ? (
          <>
            <button
              type="button"
              disabled={first}
              aria-label={`Move “${taskTitle(task.words)}” up`}
              data-testid={`tonight-up-${task.id}`}
              onClick={() => void act(() => moveNight(task.id, "up"))}
              className={QUIET_BUTTON}
            >
              <ArrowUp size={12} aria-hidden /> Up
            </button>
            <button
              type="button"
              disabled={last}
              aria-label={`Move “${taskTitle(task.words)}” down`}
              data-testid={`tonight-down-${task.id}`}
              onClick={() => void act(() => moveNight(task.id, "down"))}
              className={QUIET_BUTTON}
            >
              <ArrowDown size={12} aria-hidden /> Down
            </button>
            <button type="button" data-testid={`tonight-edit-${task.id}`} onClick={() => setEditing(true)} className={QUIET_BUTTON}>
              Edit
            </button>
            <button
              type="button"
              data-testid={`tonight-cancel-${task.id}`}
              onClick={() => void act(() => cancelNight(task.id))}
              className={QUIET_BUTTON}
            >
              Cancel
            </button>
          </>
        ) : null}
        {task.threadId && task.state !== "queued" ? (
          <button
            type="button"
            data-testid={`tonight-open-${task.id}`}
            onClick={() => onOpenSession(task.threadId)}
            className={QUIET_BUTTON}
          >
            Open
          </button>
        ) : null}
        {task.state === "running" ? (
          <ConfirmButton
            variant="calm"
            label="Stop"
            confirmLabel="Stop it?"
            onConfirm={() => act(() => cancelNight(task.id))}
          />
        ) : (
          <ConfirmButton variant="calm" label="Remove" confirmLabel="Remove it?" onConfirm={() => act(() => removeNight(task.id))} />
        )}
      </div>
      {problem ? (
        <p role="alert" className="text-[12px] text-tone-danger">
          {problem}
        </p>
      ) : null}
    </li>
  );
}

export function TonightList({
  folders,
  onOpenSession,
}: {
  /** The folders the user's coding sessions use (the folder box offers them). */
  folders: readonly string[];
  onOpenSession: (threadId: string) => void;
}) {
  const [list, setList] = useState<NightList | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const { usable } = useModels();

  const refresh = useCallback(async () => {
    try {
      setList(await listNight());
      setLoadError(null);
    } catch (e) {
      setLoadError(
        e && typeof e === "object" && "status" in e && (e as { status?: number }).status === 404
          ? "This version of Iron Jarvis has no night list yet."
          : errorWords(e),
      );
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);
  useVisibleInterval(() => void refresh(), NIGHT_POLL_MS);

  const queued = useMemo(() => (list?.tasks ?? []).filter((t) => t.state === "queued"), [list]);

  return (
    <section data-testid="tonight" aria-labelledby="tonight-title" className="mx-auto w-full max-w-[768px] space-y-3 px-1">
      <header className="space-y-1">
        <h2 id="tonight-title" className="flex items-center gap-2 text-[15px] font-medium text-zinc-100">
          <Moon size={14} aria-hidden /> Tonight
        </h2>
        {list ? (
          <>
            <p data-testid="tonight-window" className="text-[13px] text-zinc-300">
              {list.window.words}
            </p>
            <p className="text-[12px] text-zinc-500">{nightLimitsLine(list)}</p>
          </>
        ) : null}
        {list?.emergencyStop ? (
          <p role="status" data-testid="tonight-emergency" className="text-[12px] text-tone-warn">
            {EMERGENCY_LINE}
          </p>
        ) : null}
      </header>

      {adding ? (
        <TaskForm
          initial={{ folder: folders[0] ?? "", words: "" }}
          folders={folders}
          models={usable}
          submitLabel="Add to tonight"
          onSubmit={async (draft) => {
            try {
              await addNight(draft);
              setAdding(false);
              void refresh();
              return null;
            } catch (e) {
              return errorWords(e);
            }
          }}
          onCancel={() => setAdding(false)}
        />
      ) : (
        <button
          type="button"
          data-testid="tonight-add"
          onClick={() => setAdding(true)}
          className="flex h-8 items-center gap-2 rounded-[10px] px-2.5 text-[13px] text-zinc-300 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
        >
          <Plus size={14} aria-hidden /> Add a task
        </button>
      )}

      {loadError ? (
        <p role="alert" className="px-2 text-[12px] text-tone-danger">
          {loadError}
        </p>
      ) : list === null ? (
        <p className="flex items-center gap-1.5 px-2 text-[12px] text-zinc-500">
          <Loader2 size={12} className="animate-spin" /> Reading tonight’s list…
        </p>
      ) : list.tasks.length === 0 ? (
        <p data-testid="tonight-empty" className="px-2 text-[13px] text-zinc-500">
          Nothing is queued for tonight. Add a task, and Iron Jarvis starts it in the night window on a branch of its
          own; you find it here in the morning.
        </p>
      ) : (
        <ol data-testid="tonight-tasks" className="divide-y divide-white/[0.05]">
          {list.tasks.map((t) => (
            <TaskRow
              key={t.id}
              task={t}
              first={queued[0]?.id === t.id}
              last={queued[queued.length - 1]?.id === t.id}
              folders={folders}
              models={usable}
              onChanged={() => void refresh()}
              onOpenSession={onOpenSession}
            />
          ))}
        </ol>
      )}
    </section>
  );
}
