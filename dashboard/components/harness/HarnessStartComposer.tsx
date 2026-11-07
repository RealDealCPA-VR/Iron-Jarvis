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
 *
 * v1.437.0 (the user: "Try on is not available at all in a new chat, and
 * neither are Team, effort or the account"): the box carries THE SESSION
 * COMPOSER'S OWN ROW — the same components in the same one-row toolbar
 * inside the box, with its fit rules (lib/toolbarFit `HARNESS_FIT_ORDER`):
 * the permission chip (with Plan), "Try on…" (HarnessTryOn), Team
 * (HarnessTeam, in its `draft` form: there is no session yet), the effort
 * chip (only where the chosen model offers levels) and the model menu WITH
 * the accounts (components/chat/ModelMenu + lib/harnessModelMenu
 * `companyMenu` — the same folded list the session's composer reads, never a
 * second one). Under the box stay the folder chip, the branch switch, Local
 * only and the folder's defaults. Everything still rides the ONE create
 * (`startCreateBody`: the account, the effort, Plan, the team) and the "Try
 * on…" picks ride the first message (`try_on`). Try on needs a folder in a
 * git repository that can start a branch, and a session that does not start
 * by planning: otherwise its chip says why (`tryOnAvailability`) instead of
 * opening the list. Under Local only the model menu, "Try on…" and the team
 * list offer only models that stay on this PC or your network.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ArrowUp, House, Loader2 } from "lucide-react";
import { FolderChip } from "@/components/folders/FolderChip";
import { PermissionChip } from "@/components/chat/PermissionChip";
import {
  ModelMenu,
  ReasoningChip,
  modelTriggerOf,
  reasoningLevelsOf,
  reasoningRowFor,
} from "@/components/chat/ModelMenu";
import { TryOnControl } from "@/components/harness/HarnessTryOn";
import { TeamControl, type KnownMembers } from "@/components/harness/HarnessTeam";
import { fetchFolderGit, folderGitLine, type FolderGit } from "@/lib/buildGit";
import { useModels } from "@/lib/useModels";
import { useDaemon } from "@/lib/daemon";
import { modelRowName } from "@/lib/onboarding";
import type { PermissionMode } from "@/lib/permissionLevels";
import { friendlyModelName } from "@/lib/friendlyModelName";
import { staysOnThisNetwork } from "@/lib/localOnly";
import { reasoningDefaultOf } from "@/lib/reasoningChip";
import { HARNESS_FIT_ORDER, fitStepOn, useToolbarFit, type FitStep } from "@/lib/toolbarFit";
import { cliVendorOf, companyMenu } from "@/lib/harnessModelMenu";
import { fetchAccounts, type AccountsView, type BuildAccount } from "@/lib/buildAccounts";
import { sameRow, type TryOnPick } from "@/lib/buildAttempts";
import type { TeamNames } from "@/lib/sessionTeam";
import { useFolderDefaults } from "@/components/folders/FolderDefaultsPanel";
import {
  SAVE_DEFAULTS_LABEL,
  codeDefaultsWords,
  folderBaseName,
  saveFolderDefaults,
  sameCodeDefaults,
  usingDefaultsLine,
  type CodeDefaults,
} from "@/lib/folderDefaults";
import { HARNESS_COLUMN, HARNESS_COMPOSER_BOX } from "@/components/harness/harnessLook";
import { BUILD_CODE_PLACE, TO_BUILD_CODE_LEAD, buildCodeHref } from "@/lib/buildApp";
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
  /** v1.437.0: the Iron-Proxy account the model was picked under (the model
   *  menu's accounts). Omitted: the daemon's own rule — the Default account
   *  of the vendor the model uses. */
  account?: string;
  /** The reasoning level: the effort chip's (v1.437.0), else — untouched —
   *  the folder's while the model chip shows the folder's own model
   *  (v1.426.0, E5). */
  reasoning?: string;
  approval_mode?: PermissionMode;
  /** v1.437.0: the session starts by planning (the permission chip's Plan). */
  mode?: "plan";
  /** v1.437.0: the team set in the box (null = work alone). Omitted: the
   *  folder's saved team, which the create applies by itself. */
  team?: TeamNames | null;
  /** v1.437.0 review: what the box knows about each member it put on the
   *  team (the model it runs on) — never sent; a refused first message's
   *  team comes back named by its models, and Local only can take a cloud
   *  member back. */
  teamMembers?: KnownMembers;
  local_only?: boolean;
  /** v1.437.0: the models the FIRST MESSAGE is tried on ("Try on…") — it
   *  rides that message (`try_on`), never the create. */
  tryOn?: TryOnPick[];
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
  // v1.437.0: an account names a model's vendor — never one without a model.
  if (req.account && req.provider) out.account = req.account;
  if (req.reasoning) out.reasoning = req.reasoning;
  if (req.approval_mode) out.approval_mode = req.approval_mode;
  if (req.mode === "plan") out.mode = "plan";
  // v1.437.0: present = the team the box set (null = work alone, explicitly —
  // the folder's saved team is then NOT applied); omitted = the folder's.
  if (req.team !== undefined) {
    out.team =
      req.team && req.team.workers.length ? { workers: req.team.workers, reviewer: req.team.reviewer || "" } : null;
  }
  if (req.local_only) out.local_only = true;
  return out;
}

/** v1.437.0: whether "Try on…" can be used in the new-session box, and why
 *  not in words. `settled` = the answer is known (false only while the
 *  folder's git facts are still being read: picks made before are kept until
 *  then). The daemon's own refusal is `harness/attempts.NEEDS_GIT`: each
 *  attempt works on its own branch. Pure. */
export function tryOnAvailability(o: {
  folder: string | null;
  git: FolderGit | null;
  gitKnown: boolean;
  plan: boolean;
}): { why: string; settled: boolean } {
  if (!o.folder) return { why: TRY_ON_NEEDS_FOLDER, settled: true };
  if (!o.gitKnown) return { why: TRY_ON_CHECKING, settled: false };
  if (!o.git) return { why: TRY_ON_UNREAD, settled: true };
  if (!o.git.git) return { why: TRY_ON_NO_GIT, settled: true };
  if (!o.git.repo) return { why: TRY_ON_NOT_A_REPO, settled: true };
  if (!o.git.canBranch) return { why: `${o.git.note || TRY_ON_NO_BRANCH_LEAD} ${TRY_ON_NEEDS_BRANCH}`, settled: true };
  if (o.plan) return { why: TRY_ON_PLANNING, settled: true };
  return { why: "", settled: true };
}

export const TRY_ON_NEEDS_FOLDER =
  "Try on needs a folder in a git repository: each model works on its own branch. Choose one first.";
export const TRY_ON_CHECKING = "Checking whether this folder is in a git repository…";
export const TRY_ON_UNREAD =
  "Iron Jarvis could not tell whether this folder is in a git repository, so Try on is off for now.";
export const TRY_ON_NO_GIT = "Try on needs git, which is not installed on this PC: each model works on its own branch.";
export const TRY_ON_NOT_A_REPO =
  "Try on needs a folder in a git repository: each model works on its own branch, and this folder is not in one.";
const TRY_ON_NO_BRANCH_LEAD = "This repository cannot start a branch here.";
const TRY_ON_NEEDS_BRANCH = "Try on needs one: each model works on its own branch.";
export const TRY_ON_PLANNING =
  "This session starts by planning. Turn Plan off to try your first message on several models.";
/** The daemon's WORDS_ONLY refusal, said before anything is made. */
export const TRY_ON_WORDS_ONLY =
  "Trying on several models takes the message's words only. Remove the attached files, or clear Try on.";
/** The model menu's one line under Local only: it lists only those models. */
export const START_LOCAL_ONLY_MENU_LINE = "Local only: models on this PC or your network";

/** v1.437.0: what turning Local only on left out (a model picked before that
 *  leaves this PC, models picked to try on), in words. Pure. */
export function localOnlyDroppedWords(names: readonly string[]): string {
  if (!names.length) return "";
  const one = names.length === 1;
  return `${names.join(", ")} ${one ? "leaves" : "leave"} this PC, so ${one ? "it is" : "they are"} not used while Local only is on.`;
}

/** v1.418.0 review: is a refusal about the FOLDER (said on the folder chip)
 *  — the create's own folder and branch refusals — or about something else
 *  (Iron Jarvis not answering, the model, a team: said under the box, never
 *  in red on a folder the user chose rightly). Pure. */
export function isFolderProblem(words: string): boolean {
  // v1.437.0: the daemon's refusal of the "Try on…" picks names a git folder
  // ("Trying on several models needs a git folder…") — it is about Try on,
  // said under the box, never in red on the folder chip.
  if (/\btry(ing)? (this |it )?on\b/i.test(words)) return false;
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
  /** v1.437.0: the folder `git` was read for (until then Try on waits). */
  const [gitFor, setGitFor] = useState<string | null>(null);
  const [gitNote, setGitNote] = useState<string | null>(null);
  const [onBranch, setOnBranch] = useState(seeded?.branch ?? true);
  const [posture, setPosture] = useState<PermissionMode | null>(seeded?.approval_mode ?? null);
  const [pick, setPick] = useState(seeded?.provider ? `${seeded.provider}::${seeded.model ?? ""}` : "");
  // v1.437.0: the account the model was picked under ("" = the daemon's rule).
  const [account, setAccount] = useState(seeded?.account ?? "");
  // v1.437.0: the effort chip's level — null = untouched (the folder's, while
  // the folder's own model is shown; else the model's own default).
  const [effort, setEffort] = useState<string | null>(seeded?.reasoning || null);
  const [plan, setPlan] = useState(seeded?.mode === "plan");
  // v1.437.0: undefined = untouched (the folder's saved team).
  const [team, setTeam] = useState<TeamNames | null | undefined>(seeded?.team);
  // v1.437.0 review: the model each member the box put on the team runs on.
  const [teamMembers, setTeamMembers] = useState<KnownMembers>(seeded?.teamMembers ?? {});
  const [tryOn, setTryOn] = useState<TryOnPick[]>(seeded?.tryOn ?? []);
  const [localOnly, setLocalOnly] = useState(!!seeded?.local_only);
  const [localNote, setLocalNote] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [accounts, setAccounts] = useState<AccountsView | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(seed?.problem || null);
  // The branch switch as the user left it (a seed's own answer survives the
  // folder's git read that resets it to on).
  const branchKeptRef = useRef<boolean | null>(typeof seeded?.branch === "boolean" ? seeded.branch : null);
  const busyRef = useRef(false);
  const att = useHarnessAttachments();
  const catalog = useModels();
  const usable = catalog.usable;
  const daemon = useDaemon();
  const defaultProvider = daemon.health?.default_provider ?? "";
  const defaultModel = daemon.health?.default_model ?? "";
  // v1.437.0: the session composer's own one-row toolbar rules.
  const [fitRef, fitLevel] = useToolbarFit(HARNESS_FIT_ORDER);
  const fitOn = (step: FitStep) => fitStepOn(HARNESS_FIT_ORDER, fitLevel, step);
  // v1.426.0 (E5): the folder's defaults for new sessions. A chip the user
  // touched (or a refused first message's own choice) wins; an untouched one
  // shows the folder's value.
  const folderDefaults = useFolderDefaults(folder);
  const codeDefaults = folderDefaults?.code ?? null;
  const pickTouchedRef = useRef(!!seeded?.provider);
  const postureTouchedRef = useRef(!!seeded?.approval_mode);
  const [defaultsNote, setDefaultsNote] = useState<string | null>(null);

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
    setGitFor(null);
    setGitNote(null);
    if (!folder) return;
    void fetchFolderGit(folder).then((info) => {
      if (!live) return;
      setGit(info);
      setGitFor(folder);
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

  // v1.437.0: the Iron-Proxy accounts (GET /build/accounts — the session's
  // own list), read when the box opens and each time the model menu opens.
  // An Iron Jarvis with no list (a 404: `fetchAccounts` answers null) shows
  // none and is not asked again.
  const readAccounts = useCallback(async () => {
    try {
      setAccounts(await fetchAccounts());
    } catch {
      // v1.437.0 review: a read that got no answer (Iron Jarvis still
      // starting — the empty Code view IS this box — or offline) keeps the
      // list it last read (none before the first answer) and is asked again
      // when the menu opens; only a 404 means "no list". It used to stop
      // asking for good.
    }
  }, []);
  useEffect(() => {
    void readAccounts();
  }, [readAccounts]);
  const accountsKnownMissing = accounts === null;
  useEffect(() => {
    if (menuOpen && !accountsKnownMissing) void readAccounts();
    // Each opening, not each answer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [menuOpen]);

  const options = useMemo(
    () => usable.map((m) => ({ value: `${m.provider}::${m.model}`, label: modelRowName(m, catalog.models) })),
    [usable, catalog.models],
  );

  // v1.426.0 (E5): an untouched model / approval chip follows the folder —
  // its defaults when it has them (a model only while it can answer), the
  // app's own when it has none (a folder chosen after another one).
  // v1.426.0 review: the hook's value only — it hears every save in this
  // window (the folder heading's panel too); a copy of this box's own last
  // save would keep defaults the panel just cleared.
  const shownDefaults = codeDefaults;
  const defaultPick = shownDefaults?.provider ? `${shownDefaults.provider}::${shownDefaults.model ?? ""}` : "";
  // v1.426.0 review (lane B): under Local only an untouched chip never takes a
  // folder model that leaves this PC and your own network — the session's
  // first message would only be refused (this view has no "send once"); the
  // words say why. (v1.437.0: the model menu no longer offers it under Local
  // only either.)
  const defaultRow = defaultPick ? usable.find((m) => `${m.provider}::${m.model}` === defaultPick) : undefined;
  const defaultLeaves = localOnly && !!defaultRow && !staysOnThisNetwork(defaultRow);
  const defaultPickUsable = !!defaultPick && options.some((o) => o.value === defaultPick) && !defaultLeaves;
  useEffect(() => {
    if (!pickTouchedRef.current) {
      setPick(defaultPickUsable ? defaultPick : "");
      setAccount("");
    }
    if (!postureTouchedRef.current) setPosture(shownDefaults?.approval_mode ?? null);
    // `localOnly`: a pick Local only took back follows the folder again.
  }, [defaultPick, defaultPickUsable, shownDefaults?.approval_mode, localOnly]);
  useEffect(() => {
    setDefaultsNote(null);
  }, [folder]);
  // The folder's reasoning level rides the create only while the model chip
  // shows the folder's own model (a level is a model's) — and (v1.437.0) the
  // effort chip is untouched: a level the user picked wins.
  const folderReasoning = shownDefaults?.reasoning && pick && pick === defaultPick ? shownDefaults.reasoning : "";
  const reasoning = effort ?? folderReasoning;

  // ---- v1.437.0: the model menu WITH the accounts (the session's own menu).
  // Under Local only it lists only the models that stay on this PC or your
  // network: this view has no "send once to the cloud".
  const menuModels = useMemo(
    () => (localOnly ? catalog.models.filter((m) => staysOnThisNetwork(m)) : catalog.models),
    [catalog.models, localOnly],
  );
  const [pickProvider, pickModel] = pick ? pick.split("::") : ["", ""];
  // The account the session would start on: the one picked with the model,
  // else the Default of that model's vendor (the create's own rule). A pick
  // with no vendor (an API key, a local model, the default) has none.
  const vendor = cliVendorOf(pickProvider);
  const startAccount: BuildAccount | null = useMemo(() => {
    if (!accounts || !vendor) return null;
    const group = accounts.groups[vendor] ?? [];
    return (account ? group.find((a) => a.id === account) : group.find((a) => a.isDefault)) ?? null;
  }, [accounts, vendor, account]);
  const companies = useMemo(
    () => companyMenu(menuModels, accounts, startAccount, pick),
    [menuModels, accounts, startAccount, pick],
  );
  const modelPart = modelTriggerOf(pick, catalog.models, defaultProvider, defaultModel);
  const trigger = startAccount ? { ...modelPart, account: startAccount.name } : modelPart;
  // The effort chip: the levels of the row the pick runs on, looked up in the
  // folded list first (the session composer's rule).
  const reasoningRow =
    reasoningRowFor(companies.choice, companies.models, defaultProvider, defaultModel) ??
    reasoningRowFor(pick, catalog.models, defaultProvider, defaultModel);
  const levels = reasoningLevelsOf(reasoningRow);
  const reasoningDef = reasoningDefaultOf(reasoningRow);
  // A level the newly picked model does not offer is dropped (Build › Code's
  // rule) — a model that offers none takes none: the chip then shows the
  // model's own default, or is not there at all.
  const levelsKey = levels.join(",");
  const prevPickRef = useRef(pick);
  useEffect(() => {
    if (prevPickRef.current === pick) return;
    prevPickRef.current = pick;
    const offered = levelsKey ? levelsKey.split(",") : [];
    setEffort((e) => (e && !offered.includes(e) ? "" : e));
  }, [pick, levelsKey]);

  // ---- v1.437.0: "Try on…" — a folder in a git repository that can start a
  // branch, a session that does not start by planning.
  const tryOnState = tryOnAvailability({ folder, git, gitKnown: !!folder && gitFor === folder, plan });
  const tryOnBlocked = !!tryOnState.why && tryOnState.settled;
  // Picks made while it could be used go once it definitely cannot (another
  // folder, Plan on): never sent to be refused.
  useEffect(() => {
    if (tryOnBlocked && tryOn.length) setTryOn([]);
  }, [tryOnBlocked, tryOn.length]);
  const teamSet = !!team && team.workers.length > 0;

  /** The catalog row a pick spells (the folded list first: a keyless API's
   *  model picked under its CLI). */
  const rowOf = (value: string) =>
    companies.models.find((m) => `${m.provider}::${m.model}` === value) ??
    catalog.models.find((m) => `${m.provider}::${m.model}` === value);

  /** Local only: nothing chosen that leaves this PC stays chosen — a model
   *  the user picked, models picked to try on, (v1.437.0 review) a team member
   *  the box put on the team — and the words say what went. */
  function toggleLocalOnly() {
    const next = !localOnly;
    setLocalOnly(next);
    setLocalNote(null);
    if (!next) return;
    const left: string[] = [];
    if (pick && pickTouchedRef.current && !staysOnThisNetwork(rowOf(pick))) {
      left.push(trigger.text);
      pickTouchedRef.current = false;
      setPick("");
      setAccount("");
    }
    // v1.437.0 review: "leaves this PC" is the catalog row's own answer —
    // never "not offered right now": a local model whose box is down for a
    // moment (`available: false`) was taken back as leaving this PC. A model
    // the catalog does not list is left to the daemon, which refuses a cloud
    // model in a Local only session before anything starts.
    const leavesThisPc = (p: { provider: string; model: string }) => {
      const row = catalog.models.find((r) => sameRow(r, p));
      return !!row && !staysOnThisNetwork(row);
    };
    const kept = tryOn.filter((p) => !leavesThisPc(p));
    if (kept.length !== tryOn.length) {
      left.push(...tryOn.filter((p) => !kept.includes(p)).map((p) => p.label || friendlyModelName(p.model)));
      setTryOn(kept);
    }
    // v1.437.0 review: a member picked in the box on a model that leaves this
    // PC goes too (the team list offers only what stays). A member the box
    // knows nothing about (the folder's saved team, untouched) is the daemon's
    // to keep local: a piece is never handed to a model that leaves.
    if (team) {
      const leaves = (name: string) => {
        const m = teamMembers[name];
        return !!m && leavesThisPc(m);
      };
      const gone = [...team.workers, ...(team.reviewer ? [team.reviewer] : [])].filter(leaves);
      if (gone.length) {
        left.push(...gone.map((n) => teamMembers[n]?.label || friendlyModelName(teamMembers[n]?.model ?? n)));
        const workers = team.workers.filter((w) => !leaves(w));
        const reviewer = team.reviewer && !leaves(team.reviewer) ? team.reviewer : "";
        // No worker left: the session works alone (a reviewer checks workers).
        setTeam(workers.length ? { workers, reviewer } : null);
      }
    }
    if (left.length) setLocalNote(localOnlyDroppedWords(left));
  }
  const modelName = (provider: string, model: string) =>
    options.find((o) => o.value === `${provider}::${model}`)?.label ?? friendlyModelName(model || provider);
  const defaultsWords = codeDefaultsWords(shownDefaults, modelName);
  const chosen: CodeDefaults | null = (() => {
    const [p, m] = pick ? pick.split("::") : ["", ""];
    const out: CodeDefaults = {
      ...(p ? { provider: p, ...(m ? { model: m } : {}) } : {}),
      // v1.437.0 review: a level is a MODEL's — the box applies the folder's
      // level only while its own model is shown (`folderReasoning`), so a
      // level saved with no model (the effort chip on the default model)
      // would be said as the folder's default and never used.
      ...(p && reasoning ? { reasoning } : {}),
      ...(posture ? { approval_mode: posture } : {}),
    };
    return Object.keys(out).length ? out : null;
  })();
  const offerSave = !!folder && !!chosen && posture !== "yolo" && !sameCodeDefaults(chosen, shownDefaults ?? null);

  async function saveDefaults() {
    if (!folder || !chosen) return;
    setDefaultsNote(null);
    try {
      await saveFolderDefaults(folder, "code", chosen);
    } catch (e) {
      setDefaultsNote(e instanceof Error && e.message ? e.message : "The defaults were not saved.");
    }
  }

  async function send() {
    if (busyRef.current) return;
    if (att.busyRef.current) {
      att.whenSettled(() => void send());
      return;
    }
    const words = (boxRef.current?.value ?? text).trim();
    const files = att.filesRef.current;
    if (!words && !files.length) return;
    // v1.437.0: the picks ride the first message unless Try on definitely
    // cannot be used here (while the folder is still being read they go: the
    // daemon decides).
    const trying = tryOn.length > 0 && !tryOnBlocked;
    if (trying && files.length) {
      setProblem(TRY_ON_WORDS_ONLY);
      return;
    }
    busyRef.current = true;
    setBusy(true);
    setProblem(null);
    const err = await onStart({
      folder: folder ?? "",
      ...(canBranch ? { branch: onBranch } : {}),
      ...(pickProvider ? { provider: pickProvider, model: pickModel ?? "" } : {}),
      ...(pickProvider && account ? { account } : {}),
      ...(reasoning ? { reasoning } : {}),
      ...(posture ? { approval_mode: posture } : {}),
      ...(plan ? { mode: "plan" as const } : {}),
      ...(team !== undefined ? { team } : {}),
      ...(team && Object.keys(teamMembers).length ? { teamMembers } : {}),
      ...(localOnly ? { local_only: true } : {}),
      ...(trying ? { tryOn } : {}),
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
        {/* v1.437.0: the session composer's own row (HarnessSession's
            `harness-composer-toolbar`): one fit group in HARNESS_FIT_ORDER —
            `data-fit-row` keeps it one line while it fits, a step per
            `data-fit-<step>`, the right group shrinking through the model's
            name; none (a phone, a box too narrow) wraps as before. */}
        <div
          ref={fitRef}
          data-testid="harness-new-toolbar"
          className="group/fit flex min-w-0 flex-wrap items-center gap-1 px-2 pb-2 data-[fit-row]:flex-nowrap"
        >
          <AttachButton onPick={(files) => void att.add(files)} disabled={busy} />
          <PermissionChip
            id="harness-new-posture"
            value={posture ?? START_DEFAULT_POSTURE}
            onChange={(m) => {
              postureTouchedRef.current = true;
              setPosture(m);
            }}
            disabled={busy}
            iconOnlyOnPhone
            iconOnly={fitOn("ask")}
            // Plan lives in the permission chip, as in the session.
            planMode={plan}
            onPlanChange={(on) => setPlan(on)}
          />
          {/* Try the first message on up to three models, each on its own
              branch — or, where it cannot be used yet, the reason why. */}
          <TryOnControl
            models={catalog.models}
            localOnly={localOnly}
            picks={tryOn}
            onChange={setTryOn}
            disabled={busy}
            iconOnly={fitOn("tryon")}
            teamSet={teamSet}
            unavailable={tryOnState.why}
          />
          {/* The new session's team, before the session exists: untouched,
              the folder's saved team (the create applies it by itself). */}
          <TeamControl
            threadId=""
            folder={folder ?? ""}
            noFolder={!folder}
            team={null}
            teamSetup={null}
            leadWords={trigger.text}
            models={catalog.models}
            localOnly={localOnly}
            disabled={busy}
            onView={() => {}}
            draft={{
              team,
              onChange: setTeam,
              known: teamMembers,
              onKnown: (name, facts) => setTeamMembers((k) => ({ ...k, [name]: facts })),
            }}
          />
          <div
            data-testid="harness-new-actions"
            className="ml-auto flex min-w-0 max-w-full flex-wrap items-center justify-end gap-1 group-data-[fit-row]/fit:flex-nowrap group-data-[fit-row]/fit:justify-start group-data-[fit-row]/fit:pl-3"
          >
            {levels.length > 0 ? (
              <ReasoningChip
                levels={levels}
                value={reasoning}
                def={reasoningDef}
                short={fitOn("short")}
                disabled={busy}
                onChange={(level) => setEffort(level)}
              />
            ) : null}
            {/* The model menu WITH the accounts (the session's own). */}
            <div data-testid="harness-new-model" data-choice={pick} data-account={account || undefined} className="flex min-w-0">
              <ModelMenu
                open={menuOpen}
                onOpenChange={setMenuOpen}
                choice={pick}
                models={menuModels}
                trigger={trigger}
                onPick={(v, acct) => {
                  pickTouchedRef.current = true;
                  setPick(v);
                  setAccount(v ? (acct ?? "") : "");
                }}
                disabled={busy}
                disabledTitle="Starting the session…"
                localOnlyLine={localOnly ? START_LOCAL_ONLY_MENU_LINE : null}
                companies={companies}
              />
            </div>
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
        <button
          type="button"
          data-testid="harness-new-local-only"
          aria-pressed={localOnly}
          disabled={busy}
          onClick={toggleLocalOnly}
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
                  ? `Its own copy of the folder beside the repository, on its own ij/ branch from ${git?.branch || "the current commit"}. Your checkout is not touched.`
                  : "Off: the session works in the folder directly, on the branch checked out there."}
              </span>
            </span>
          </label>
        ) : gitNote ? (
          <span data-testid="harness-new-folder-note" className="self-center px-1 text-[11px] text-zinc-500">
            {gitNote}
          </span>
        ) : null}
        {/* v1.437.0: what turning Local only on left out, in words. */}
        {localNote ? (
          <span data-testid="harness-new-local-note" role="status" className="w-full px-2 text-[11px] text-zinc-500">
            {localNote}
          </span>
        ) : null}
        {/* v1.426.0 (E5): this folder's defaults for new sessions, in words,
            and the press that makes the chips above them. */}
        {folder && (defaultsWords || offerSave || defaultsNote) ? (
          <span data-testid="harness-new-folder-defaults" className="flex w-full min-w-0 flex-wrap items-center gap-x-2 px-2 text-[11px] text-zinc-500">
            {defaultsWords ? (
              <span data-testid="harness-new-folder-defaults-words">
                {usingDefaultsLine(folderBaseName(folder), defaultsWords)}
                {defaultLeaves
                  ? ` ${modelName(shownDefaults?.provider ?? "", shownDefaults?.model ?? "")} leaves this PC, so it is not used while Local only is on.`
                  : defaultPick && !defaultPickUsable
                    ? ` ${modelName(shownDefaults?.provider ?? "", shownDefaults?.model ?? "")} cannot answer right now, so the default model does.`
                    : ""}
              </span>
            ) : null}
            {offerSave ? (
              <button
                type="button"
                data-testid="harness-new-save-defaults"
                disabled={busy}
                onClick={() => void saveDefaults()}
                className="rounded-md px-1 text-zinc-400 hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
              >
                {SAVE_DEFAULTS_LABEL}
              </button>
            ) : null}
            {defaultsNote ? (
              <span role="alert" className="text-tone-danger">
                {defaultsNote}
              </span>
            ) : null}
          </span>
        ) : null}
      </div>
      {/* v1.443.0 (ease-of-use review, item 11): the two Code views point at
          each other — here Jarvis writes the code; Build › Code runs the
          user's own Claude Code or Codex. */}
      <p data-testid="harness-to-build-code" className="mt-2 px-2 text-[11px] text-zinc-500">
        {TO_BUILD_CODE_LEAD}{" "}
        <Link
          href={buildCodeHref()}
          className="rounded-sm text-zinc-400 underline decoration-white/20 underline-offset-2 transition-colors hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60"
        >
          {BUILD_CODE_PLACE}
        </Link>
        .
      </p>
    </div>
  );
}

export default HarnessStartComposer;
