"use client";

/**
 * v1.418.0 (fix plan C2.6): Chat › Code STARTS AT THE COMPOSER.
 *
 * The empty view and New session are this box with the caret in it, and one
 * row of chips under it, the way Build › Code starts (components/buildCode/
 * StartComposer): Folder (Choose… = Windows' own picker in the desktop app,
 * the app's folder tree in a browser — components/folders/FolderChip),
 * "Work on a new branch" (only for a folder in a git repository that can
 * start one), the approvals posture, the model, and Local only.
 *
 * NOTHING EXISTS ON THE DAEMON UNTIL SEND: Send makes the session with ONE
 * `POST /build/sessions` carrying everything the chips say, and the words go
 * to that session as its first message (the host hands them over). There is
 * no empty session to move, and nothing to clean up when the user leaves.
 * A refusal (a folder the daemon will not use) is said in words, nothing is
 * made, and the words stay in the box.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, House, Loader2 } from "lucide-react";
import { FolderChip } from "@/components/folders/FolderChip";
import { PermissionChip } from "@/components/chat/PermissionChip";
import { fetchFolderGit, folderGitLine, type FolderGit } from "@/lib/buildGit";
import { useModels } from "@/lib/useModels";
import { modelRowName } from "@/lib/onboarding";
import type { PermissionMode } from "@/lib/permissionLevels";
import { HARNESS_COLUMN, HARNESS_COMPOSER_BOX } from "@/components/harness/harnessLook";
import { PANE_COMPOSER_CARD, paneSendClass } from "@/components/terminal/paneChatLook";
import {
  AttachButton,
  AttachmentChips,
  pastedFiles,
  useHarnessAttachments,
  type HarnessFile,
} from "@/components/harness/HarnessAttachments";

/** What Send hands the host: the create's fields and the first message. */
export interface StartRequest {
  /** "" = no folder (the session makes its own with this message). */
  folder: string;
  /** Only when the branch switch was shown. */
  branch?: boolean;
  provider?: string;
  model?: string;
  approval_mode?: PermissionMode;
  local_only?: boolean;
  text: string;
  files: HarnessFile[];
}

/** The create's body for a request (what rides `POST /build/sessions` beside
 *  the folder). Only what the user chose rides it — an untouched chip leaves
 *  the daemon's default. Pure. */
export function startCreateBody(req: StartRequest): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (typeof req.branch === "boolean") out.branch = req.branch;
  if (req.provider) out.provider = req.provider;
  if (req.model) out.model = req.model;
  if (req.approval_mode) out.approval_mode = req.approval_mode;
  if (req.local_only) out.local_only = true;
  return out;
}

/** v1.418.0 review: is a refusal about the FOLDER (said on the folder chip)
 *  — the create's own folder and branch refusals — or about something else
 *  (Iron Jarvis not answering, the model, a team: said under the box, never
 *  in red on a folder the user chose rightly). Pure. */
export function isFolderProblem(words: string): boolean {
  return /\b(folder|branch|repositor(y|ies)|worktree)\b/i.test(words);
}

/** v1.418.0 review: what the composer opens with when the view sends the
 *  user BACK to it — a first message the daemon refused before its turn
 *  started (the session the start made is removed): the words, the files
 *  and every chip as they were, and the refusal. */
export interface StartSeed {
  request: StartRequest;
  problem: string;
}

/** v1.418.0 review: New session / Ctrl+N / a folder's "+" pressed while the
 *  composer is ALREADY open: it keeps the words (it is never opened afresh
 *  over them) — `folder` (when given) becomes the chosen folder, and the
 *  caret goes back in the box. `n` counts the presses. */
export interface StartAgain {
  n: number;
  folder: string | null;
}

/** The posture a new session starts with when the chip is not touched (the
 *  daemon's default for a session: "Ask first"). */
export const START_DEFAULT_POSTURE: PermissionMode = "approve_for_me";

const CHIP =
  "inline-flex h-7 min-w-0 items-center gap-1 rounded-lg px-2 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50";

export function HarnessStartComposer({
  initialFolder = null,
  pickFrom = null,
  workRoot = "",
  seed = null,
  again = null,
  onStart,
}: {
  /** v1.418.0 review: open with what a refused first message held. */
  seed?: StartSeed | null;
  /** v1.418.0 review: New session pressed again while this box is open. */
  again?: StartAgain | null;
  /** A folder chosen before the box opened (a folder heading's "+"). */
  initialFolder?: string | null;
  /** Where Choose… opens when no folder is chosen. */
  pickFrom?: string | null;
  /** Where a session with no folder keeps its files. */
  workRoot?: string;
  /** Make the session and send the words; resolves to the refusal in words,
   *  or null once the session is made. */
  onStart: (req: StartRequest) => Promise<string | null>;
}) {
  const boxRef = useRef<HTMLTextAreaElement | null>(null);
  const seeded = seed?.request ?? null;
  const [text, setText] = useState(seeded?.text ?? "");
  const [folder, setFolder] = useState<string | null>(seeded ? seeded.folder || null : initialFolder || null);
  const [git, setGit] = useState<FolderGit | null>(null);
  const [gitNote, setGitNote] = useState<string | null>(null);
  const [onBranch, setOnBranch] = useState(seeded?.branch ?? true);
  const [posture, setPosture] = useState<PermissionMode | null>(seeded?.approval_mode ?? null);
  const [pick, setPick] = useState(seeded?.provider ? `${seeded.provider}::${seeded.model ?? ""}` : "");
  const [localOnly, setLocalOnly] = useState(!!seeded?.local_only);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(seed?.problem || null);
  // The branch switch as the user left it (a seed's own answer survives the
  // folder's git read that resets it to on).
  const branchKeptRef = useRef<boolean | null>(typeof seeded?.branch === "boolean" ? seeded.branch : null);
  const busyRef = useRef(false);
  const att = useHarnessAttachments();
  const catalog = useModels();
  const usable = catalog.usable;

  // The caret is in the box the moment the composer shows.
  useEffect(() => {
    boxRef.current?.focus({ preventScroll: true });
    // v1.418.0 review: a refused first message's pictures come back too.
    if (seeded?.files.length) att.restore(seeded.files);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // v1.418.0 review: pressed New session (or a folder's "+") again while
  // open — the words stay; a folder given becomes the chosen one.
  const againN = again?.n ?? 0;
  useEffect(() => {
    if (!againN) return;
    if (again?.folder) {
      setProblem(null);
      setFolder(again.folder);
    }
    boxRef.current?.focus({ preventScroll: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [againN]);

  // A folder's git facts decide whether the branch switch shows (only a
  // repository that can start a branch asks).
  useEffect(() => {
    let live = true;
    setGit(null);
    setGitNote(null);
    if (!folder) return;
    void fetchFolderGit(folder).then((info) => {
      if (!live) return;
      setGit(info);
      setGitNote(folderGitLine(info));
      const keptBranch = branchKeptRef.current;
      branchKeptRef.current = null;
      setOnBranch(keptBranch ?? true);
    });
    return () => {
      live = false;
    };
  }, [folder]);
  const canBranch = !!folder && !!git && git.repo && git.canBranch;

  const options = useMemo(
    () => usable.map((m) => ({ value: `${m.provider}::${m.model}`, label: modelRowName(m, catalog.models) })),
    [usable, catalog.models],
  );

  async function send() {
    if (busyRef.current) return;
    if (att.busyRef.current) {
      att.whenSettled(() => void send());
      return;
    }
    const words = (boxRef.current?.value ?? text).trim();
    const files = att.filesRef.current;
    if (!words && !files.length) return;
    busyRef.current = true;
    setBusy(true);
    setProblem(null);
    const [provider, model] = pick ? pick.split("::") : ["", ""];
    const err = await onStart({
      folder: folder ?? "",
      ...(canBranch ? { branch: onBranch } : {}),
      ...(provider ? { provider, model } : {}),
      ...(posture ? { approval_mode: posture } : {}),
      ...(localOnly ? { local_only: true } : {}),
      text: words,
      files,
    });
    busyRef.current = false;
    setBusy(false);
    if (err) setProblem(err);
  }

  const folderProblem = problem && isFolderProblem(problem) ? problem : null;
  const otherProblem = problem && !folderProblem ? problem : null;
  const ready = !busy && (!!text.trim() || att.files.length > 0);
  const noFolderTitle = workRoot
    ? `No folder: the session keeps its files in a new folder in ${workRoot}, made with your first message.`
    : "No folder: the session keeps its files in a new folder, made with your first message.";

  return (
    <div data-testid="harness-new-composer" className={`flex h-full min-h-[240px] flex-col justify-center px-1 ${HARNESS_COLUMN}`}>
      <p className="mb-3 text-center text-[14px] font-medium text-zinc-200">What should we build?</p>
      <div data-dock-composer="" className={`flex ${PANE_COMPOSER_CARD}`}>
        <AttachmentChips files={att.files} uploading={att.uploading} onRemove={att.remove} />
        {att.problem ? (
          <p role="alert" className="px-3 pt-1 text-[12px] text-tone-warn">
            {att.problem}
          </p>
        ) : null}
        {otherProblem ? (
          <p data-testid="harness-new-problem" role="alert" className="px-3 pt-2 text-[12px] text-tone-danger">
            {otherProblem}
          </p>
        ) : null}
        <textarea
          ref={boxRef}
          aria-label="Message"
          data-testid="harness-new-box"
          placeholder={folder ? `Build in ${folder}…` : "Ask for anything to build or change…"}
          value={text}
          rows={2}
          disabled={busy}
          onChange={(e) => setText(e.target.value)}
          onPaste={(e) => {
            const pasted = pastedFiles(e.clipboardData);
            if (!pasted.length) return;
            e.preventDefault();
            void att.add(pasted, { pasted: true });
          }}
          onKeyDown={(e) => {
            if (e.nativeEvent.isComposing || e.keyCode === 229) return;
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          className={HARNESS_COMPOSER_BOX}
        />
        <div className="flex min-w-0 items-center gap-1 px-2 pb-2">
          <AttachButton onPick={(files) => void att.add(files)} disabled={busy} />
          <button
            type="button"
            data-testid="harness-new-send"
            aria-label="Send"
            title="Start the session and send"
            disabled={!ready}
            onClick={() => void send()}
            className={paneSendClass(ready)}
          >
            {busy ? <Loader2 size={15} className="animate-spin" /> : <ArrowUp size={15} strokeWidth={2.4} />}
          </button>
        </div>
      </div>
      <div data-testid="harness-new-chips" className="flex min-w-0 flex-wrap items-start gap-1 px-2 pt-1.5">
        <FolderChip
          folder={folder}
          onPick={(p) => {
            setProblem(null);
            setFolder(p);
          }}
          onClear={folder ? () => setFolder(null) : undefined}
          problem={folderProblem}
          disabled={busy}
          startAt={pickFrom}
          noFolderTitle={noFolderTitle}
          testId="harness-new-folder"
        />
        <PermissionChip
          id="harness-new-posture"
          value={posture ?? START_DEFAULT_POSTURE}
          onChange={(m) => setPosture(m)}
          disabled={busy}
          iconOnlyOnPhone
        />
        <label className={`${CHIP} cursor-pointer`} title="The model this session answers with">
          <span className="sr-only">Model</span>
          <select
            data-testid="harness-new-model"
            aria-label="Model"
            value={pick}
            disabled={busy}
            onChange={(e) => setPick(e.target.value)}
            className="min-w-0 max-w-[14rem] cursor-pointer truncate bg-transparent text-[12px] text-zinc-300 focus:outline-none"
          >
            <option value="">Default model</option>
            {options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          data-testid="harness-new-local-only"
          aria-pressed={localOnly}
          disabled={busy}
          onClick={() => setLocalOnly((v) => !v)}
          title={
            localOnly
              ? "Local only is on: nothing in this session goes to a cloud model without your OK"
              : "Keep this session on this PC: nothing goes to a cloud model without your OK"
          }
          className={`${CHIP} ${localOnly ? "font-medium text-tone-success shadow-[0_0_0_1px_rgb(var(--tone-success)/0.45),0_0_12px_rgb(var(--tone-success)/0.25)]" : ""}`}
        >
          <House size={13} aria-hidden />
          Local only
        </button>
        {canBranch ? (
          <label
            data-testid="harness-new-branch"
            className="flex w-full min-w-0 items-start gap-2 rounded-xl px-2.5 py-1.5 text-[12px] text-zinc-300"
          >
            <input
              type="checkbox"
              data-testid="harness-new-branch-switch"
              checked={onBranch}
              disabled={busy}
              onChange={(e) => setOnBranch(e.target.checked)}
              className="mt-0.5 accent-accent"
            />
            <span className="min-w-0">
              Work on a new branch
              <span className="block text-[11px] text-zinc-500">
                {onBranch
                  ? `A worktree beside the repository, on its own ij/ branch from ${git?.branch || "the current commit"}. Your checkout is not touched.`
                  : "Off: the session works in the folder directly, on the branch checked out there."}
              </span>
            </span>
          </label>
        ) : gitNote ? (
          <span data-testid="harness-new-folder-note" className="self-center px-1 text-[11px] text-zinc-500">
            {gitNote}
          </span>
        ) : null}
      </div>
    </div>
  );
}

export default HarnessStartComposer;
