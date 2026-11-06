"use client";

/**
 * The conversation of one Build Code view session (v1.409.0), drawn from the
 * folded events (lib/buildApp.foldConversation) with the chat's own pieces:
 * Markdown for the replies, the work rows (components/chat/WorkLine) that
 * open to a step's input and output, the inline diff (DiffView's body) for a
 * change, Open in VS Code / Cursor beside a file step, the plan checklist,
 * and the thinking disclosure. Nothing here talks to Iron Jarvis.
 */

import { memo, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Bot,
  Check,
  Copy,
  RotateCcw,
  CheckSquare,
  FileText,
  FilePen,
  FilePlus,
  Globe,
  ListTodo,
  MoreHorizontal,
  Plug,
  Search,
  SquareTerminal,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { MemoMarkdown } from "@/components/Markdown";
import { StepDetailPanel, WorkRow } from "@/components/chat/WorkLine";
import { ThinkingDisclosure } from "@/components/chat/ThinkingDisclosure";
import { PlanChecklist } from "@/components/chat/PlanChecklist";
import { DiffBody } from "@/components/chat/DiffView";
import { StepOpenInEditor } from "@/components/harness/OpenInEditor";
import { resolveInFolder } from "@/lib/codeEditor";
import { friendlyModelName } from "@/lib/friendlyModelName";
import { CliSignInPress } from "@/components/connections/CliSignInPress";
import { HARNESS_REPLY_PROSE, HARNESS_USER_BUBBLE } from "@/components/harness/harnessLook";
import { SentAttachments } from "@/components/harness/HarnessAttachments";
import { REPLY_ACTION_BTN } from "@/components/chat/replyActions";
import { ConfirmButton } from "@/components/ui";
import {
  BRANCH_FROM_HERE,
  BRANCH_FROM_HERE_TITLE,
  MESSAGE_MENU_LABEL,
  REWIND_CONFIRM,
  REWIND_TO_HERE,
  REWIND_TO_HERE_TITLE,
  type BranchFacts,
} from "@/lib/buildAppBranch";
import {
  CANCEL_WAIT,
  CANCEL_WAIT_TITLE,
  CONTINUE_CUT,
  CONTINUE_CUT_TITLE,
  canMoveAccounts,
  carryLineWords,
  cutWords,
  waitWords,
  type CutFacts,
  type WaitFacts,
} from "@/lib/buildCodeCarry";
import {
  CONTINUE_ON_ANOTHER,
  TRY_ACCOUNTS_LEAD,
  tryAccountWords,
  type TryChoice,
  doneWords,
  signInProvider,
  planChecklistSteps,
  receiptLine,
  resolvedWords,
  steerWords,
  toolLabel,
  usageWords,
  helperStepsWords,
  helperSummary,
  showAllWords,
  STEP_STOPPED_WORDS,
  toolStepWords,
  type ItemText,
  type ConvItem,
  type ToolKind,
  type ToolEvent,
} from "@/lib/buildApp";

const TOOL_ICON: Record<ToolKind, LucideIcon> = {
  read: FileText,
  edit: FilePen,
  write: FilePlus,
  command: SquareTerminal,
  search: Search,
  web: Globe,
  mcp: Plug,
  task: Bot,
  todo: ListTodo,
  other: Wrench,
};

/** v1.439.0: the copy icon's own name (its tooltip and accessible name). */
export const COPY_REPLY_LABEL = "Copy reply";
/** v1.439.0: said for a moment after the reply went to the clipboard. */
export const COPIED_WORDS = "Copied";
/** v1.439.0: said when nothing could take the text. */
export const COPY_FAILED_WORDS = "Couldn't copy";
/** v1.439.0: how long "Copied" stays. */
export const COPIED_MS = 1500;

/** v1.441.0 (review D4): what "Copy reply" copies — the WHOLE reply, every
 *  finished text of the turn from the user's message to the next one (a
 *  reply with tool steps between its words used to copy one piece), keyed by
 *  the LAST finished text of that turn: the one row that shows the button.
 *  A message Iron Jarvis sent by itself to carry the reply on is not a new
 *  turn. Steps' own words, thinking and notes are not the reply. Only the
 *  conversation's top-level rows are read (a helper's text is its own). */
export function replyCopyTexts(items: readonly ConvItem[]): Map<string, string> {
  const out = new Map<string, string>();
  let parts: string[] = [];
  let lastKey = "";
  const close = () => {
    if (lastKey && parts.length) out.set(lastKey, parts.join("\n\n"));
    parts = [];
    lastKey = "";
  };
  for (const it of items) {
    if (it.kind === "user" && !it.carry) {
      close();
      continue;
    }
    if (it.kind === "text" && it.done && it.text.trim()) {
      parts.push(it.text.trim());
      lastKey = it.key;
    }
  }
  close();
  return out;
}

/** v1.439.0: a quiet copy icon under a finished reply — shown on hover and
 *  keyboard focus, always on a touch screen. It copies the reply's text as
 *  plain text through the app's own copy path (copyPlain: the desktop
 *  bridge first, then the browser's clipboard) and says "Copied". */
function ReplyCopy({ text }: { text: string }) {
  const [said, setSaid] = useState<"" | "copied" | "failed">("");
  const timer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );
  const copy = async () => {
    let outcome: "copied" | "unavailable" = "unavailable";
    try {
      const { copyPlain } = await import("@/components/settings/MaintenanceTools");
      outcome = await copyPlain(text);
    } catch {
      outcome = "unavailable";
    }
    setSaid(outcome === "copied" ? "copied" : "failed");
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setSaid(""), COPIED_MS);
  };
  return (
    <div
      data-testid="code-reply-actions"
      className={`-ml-1.5 mt-1 flex items-center gap-1 text-zinc-500 ${
        said
          ? ""
          : "opacity-0 transition-opacity focus-within:opacity-100 group-hover/reply:opacity-100 [@media(hover:none)]:opacity-100"
      }`}
    >
      <button
        type="button"
        data-testid="code-reply-copy"
        title={COPY_REPLY_LABEL}
        aria-label={COPY_REPLY_LABEL}
        onClick={() => void copy()}
        className={REPLY_ACTION_BTN}
      >
        {said === "copied" ? <Check size={13} className="text-tone-success" /> : <Copy size={13} />}
      </button>
      <span role="status" data-testid="code-reply-copied" className="text-[11px] text-zinc-500">
        {said === "copied" ? COPIED_WORDS : said === "failed" ? COPY_FAILED_WORDS : ""}
      </span>
    </div>
  );
}

/** How many times a row drew (tests count these; never read by the app). */
export const codeRowDraws = { items: 0, tools: 0 };

/** v1.418.0 (C4.4): read one step's whole text back from the session's
 *  record (BuildCodeView supplies it: `GET …/events/{item}`). */
export type ExpandStep = (item: string) => Promise<ItemText | null>;

/** v1.441.0: what the user's own messages offer (BuildCodeView supplies it
 *  from the view's `branch`; absent = an older Iron Jarvis, nothing shown). */
export interface MessageActions {
  facts: BranchFacts;
  /** A reply runs (or a press is out): the menu's actions are refused. */
  busy: boolean;
  onBranch: (at: string) => void;
  onRewind: (at: string) => void | Promise<void>;
}

/** v1.441.0: the ⋯ beside one of the user's own messages — Branch from here,
 *  Rewind to here (two presses). Only what the tool can do is listed. */
function MessageMenu({ at, actions }: { at: string; actions: MessageActions }) {
  const [open, setOpen] = useState(false);
  const { facts, busy } = actions;
  if (!facts.forkAt && !facts.rewind) return null;
  return (
    <div className="relative">
      <button
        type="button"
        aria-label={MESSAGE_MENU_LABEL}
        aria-haspopup="menu"
        aria-expanded={open}
        data-testid="code-user-more"
        onClick={() => setOpen((v) => !v)}
        className="grid h-6 w-6 place-items-center rounded-md text-zinc-500 hover:bg-white/[0.06] hover:text-zinc-200 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover/user:opacity-100 focus-visible:opacity-100"
      >
        <MoreHorizontal size={13} />
      </button>
      {open ? (
        <div role="menu" className="absolute right-0 top-full z-20 w-48 rounded-xl bg-ink-800 p-1 shadow-card">
          {facts.forkAt ? (
            <button
              type="button"
              role="menuitem"
              data-testid="code-user-branch"
              disabled={busy}
              title={busy ? "A reply is running: wait until it ends" : BRANCH_FROM_HERE_TITLE}
              onClick={() => {
                setOpen(false);
                actions.onBranch(at);
              }}
              className="flex w-full rounded-lg px-2.5 py-1.5 text-left text-[13px] text-zinc-300 hover:bg-white/[0.06] disabled:opacity-50"
            >
              {BRANCH_FROM_HERE}
            </button>
          ) : null}
          {facts.rewind ? (
            <div role="menuitem" data-testid="code-user-rewind">
              <ConfirmButton
                label={REWIND_TO_HERE}
                confirmLabel={REWIND_CONFIRM}
                title={busy ? "A reply is running: wait until it ends" : REWIND_TO_HERE_TITLE}
                className="w-full justify-start"
                onConfirm={async () => {
                  if (busy) return;
                  setOpen(false);
                  await actions.onRewind(at);
                }}
              />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

const ToolStep = memo(function ToolStep({
  tool,
  folder,
  kids,
  onExpand,
  toolName = "",
}: {
  tool: ToolEvent;
  folder: string;
  /** v1.418.0 (C4.2): what ran inside a helper step. */
  kids?: readonly ConvItem[];
  onExpand?: ExpandStep;
  toolName?: string;
}) {
  codeRowDraws.tools += 1;
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState<ItemText | null>(null);
  const [reading, setReading] = useState(false);
  const [readFailed, setReadFailed] = useState(false);
  const panelId = useId();
  const hasKids = !!kids && kids.length > 0;
  const hasDetail = !!(tool.input || tool.output) || hasKids;
  // Inside the session's folder only (never a share, never `..` out of it).
  const path = tool.path && folder ? resolveInFolder(folder, tool.path, { confine: true }) : null;
  const helper = hasKids ? helperSummary(kids) : null;
  const words = toolStepWords(tool);
  // v1.418.0 (C4.4): a title the tool's own description had to cut says so.
  const cut = words.endsWith("…");
  const meta: ReactNode[] = [];
  if (helper && helper.steps > 0) {
    meta.push(
      <span key="steps" data-testid="code-helper-count" className="shrink-0 text-zinc-600">
        · {helperStepsWords(helper.steps)}
      </span>,
    );
  }
  if (tool.status === "stopped") {
    // v1.427.2: the tool stopped before this step finished (never a spinner).
    meta.push(
      <span key="stopped" data-testid="code-tool-stopped" className="shrink-0 text-zinc-600">
        · {STEP_STOPPED_WORDS}
      </span>,
    );
  }
  if (tool.partial && tool.status === "running" && tool.inputChars) {
    meta.push(
      <span key="partial" data-testid="code-tool-partial" className="shrink-0 text-zinc-600">
        · {tool.inputChars.toLocaleString("en-US")} characters so far
      </span>,
    );
  }
  const expand = async () => {
    if (!onExpand || reading) return;
    setReading(true);
    setReadFailed(false);
    try {
      const got = await onExpand(tool.item);
      if (got) setFull(got);
      else setReadFailed(true);
    } catch {
      setReadFailed(true);
    } finally {
      setReading(false);
    }
  };
  return (
    <div
      data-testid="code-tool"
      data-kind={tool.kind}
      data-status={tool.status}
      data-partial={tool.partial ? "true" : undefined}
      className="group/step min-w-0"
    >
      <div className="flex min-w-0 items-center gap-1">
        <div className="min-w-0 flex-1">
          <WorkRow
            icon={TOOL_ICON[tool.kind]}
            title={words}
            running={tool.status === "running"}
            failed={tool.status === "failed"}
            titleTitle={cut ? `${tool.name || "The tool"}'s own description, cut short` : tool.name || undefined}
            meta={meta.length ? meta : undefined}
            expand={hasDetail ? { open, onToggle: () => setOpen((v) => !v), controls: panelId } : null}
          />
        </div>
        {path && tool.status !== "running" && (tool.kind === "edit" || tool.kind === "write" || tool.kind === "read") ? (
          <StepOpenInEditor path={path} line={tool.line} />
        ) : null}
      </div>
      {tool.subtitle ? (
        // v1.418.0 (C4.4): the command itself, under the tool's description.
        <p
          data-testid="code-tool-subtitle"
          title={tool.subtitle}
          className="ml-[22px] min-w-0 truncate font-mono text-[12px] leading-5 text-zinc-600"
        >
          {tool.subtitle}
        </p>
      ) : null}
      {helper && !open && helper.finalText ? (
        // v1.418.0 (C4.2): a folded helper says what it answered.
        <p data-testid="code-helper-final" className="ml-[22px] line-clamp-2 min-w-0 text-[12px] leading-5 text-zinc-500">
          {helper.finalText}
        </p>
      ) : null}
      {open && hasKids ? (
        <div
          data-testid="code-helper-children"
          className="ml-[7px] mt-1 grid min-w-0 gap-2 border-l border-white/[0.08] pl-3"
        >
          {/* A helper's rows have no Copy reply (v1.441.0, review D4): its words
              are the helper's, not the reply. */}
          {kids!.map((child) => (
            <Item key={child.key} item={child} folder={folder} press={null} toolName={toolName} onExpand={onExpand} />
          ))}
        </div>
      ) : null}
      {open && (tool.input || tool.output) ? (
        <StepDetailPanel
          id={panelId}
          name={tool.kind === "command" ? "shell" : tool.name}
          detail={{ input: full?.input || tool.input, output: full?.output || tool.output }}
        />
      ) : null}
      {open && tool.more && onExpand && !full ? (
        // v1.418.0 (C4.4): the screen got the first part; the record keeps more.
        <div className="ml-[22px] flex items-center gap-2 text-[12px] text-zinc-500">
          <button
            type="button"
            data-testid="code-tool-show-all"
            disabled={reading}
            onClick={() => void expand()}
            className="rounded-sm underline-offset-2 transition-colors hover:text-zinc-300 hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:opacity-60 motion-reduce:transition-none"
          >
            {reading ? "Reading…" : showAllWords(tool)}
          </button>
          {readFailed ? (
            <span data-testid="code-tool-show-all-failed" role="status">
              The rest could not be read.
            </span>
          ) : null}
        </div>
      ) : null}
      {tool.diff ? (
        <div data-testid="code-tool-diff" className="ml-[22px] mt-1 max-h-80 overflow-auto rounded-md">
          <DiffBody change={{ path: tool.path || "file", status: "modified", added: null, removed: null, diff: tool.diff }} />
        </div>
      ) : null}
    </div>
  );
});

/** What the newest failure can offer (only the newest: older ones are
 *  history). */
interface FailurePresses {
  tool: string;
  onContinueOn?: () => void;
  continuing?: boolean;
  /** v1.427.3: after Continue on another account found none free, the
   *  accounts the user may still try (Iron-Proxy parked them for a limit). */
  tryChoices?: readonly TryChoice[];
  onTryAccount?: (id: string) => void;
  /** v1.417.0 (C4.9): send the reply's message again (same words, same
   *  files) — the failure's own words say "Press Retry". */
  onRetry?: () => void;
  /** v1.422.0 (B3): the reply was cut off by a restart and not carried on
   *  by itself — Continue sends the hidden carry-on (in place of Retry). */
  cut?: CutFacts | null;
  onContinueCut?: () => void;
  continuingCut?: boolean;
  /** v1.422.0 (B5): the limit card waits for its account by itself (until
   *  when, with Cancel), and its own note. */
  wait?: WaitFacts | null;
  onCancelWait?: () => void;
  limitNote?: string;
}

/** v1.417.0 (C4.9): who answered, for the receipt (the session row's own
 *  model and account name when a turn's events named no model). */
interface ReceiptFacts {
  model: string;
  account: string;
}

// A row draws again only when ITS item changed (the fold keeps unchanged
// items' objects — lib/buildApp reuseItems) or its press did.
const Item = memo(function Item({
  item,
  folder,
  press,
  toolName,
  facts,
  onExpand,
  copyText = "",
  actions,
}: {
  item: ConvItem;
  folder: string;
  press: FailurePresses | null;
  toolName: string;
  facts?: ReceiptFacts;
  onExpand?: ExpandStep;
  /** v1.441.0 (review D4): the whole reply this row ends ("" = no Copy here). */
  copyText?: string;
  /** v1.441.0: only on a user message the tool can branch or rewind from. */
  actions?: MessageActions;
}) {
  codeRowDraws.items += 1;
  switch (item.kind) {
    case "user":
      if (item.carry) {
        // v1.422.0: a message Iron Jarvis sent BY ITSELF to carry the reply
        // on — one quiet line, never the user's bubble (its words are the
        // app's, written for the tool).
        return (
          <p data-testid="code-carry" data-why={item.carry.why} className="flex items-center gap-2 text-[12px] text-zinc-500">
            <RotateCcw size={13} aria-hidden="true" className="shrink-0" />
            <span className="min-w-0">{carryLineWords(item.carry)}</span>
          </p>
        );
      }
      // v1.412.0: the pictures a message carried, as thumbnails (the chat's
      // own SentAttachments); other files by name. A picture alone is a
      // message (no empty bubble).
      return (
        <div data-testid="code-user" className="group/user flex flex-col items-end gap-1.5">
          <div className="flex max-w-full items-start justify-end gap-1">
            {actions && item.at ? <MessageMenu at={item.at} actions={actions} /> : null}
            {item.text.trim() ? <div className={HARNESS_USER_BUBBLE}>{item.text}</div> : null}
          </div>
          {item.files.length ? <SentAttachments paths={item.files} /> : null}
        </div>
      );
    case "steer":
      // v1.412.0: a note typed while the tool worked, where it was sent.
      return (
        <div data-testid="code-steer" data-state={item.state} className="flex flex-col items-end gap-1">
          <div className={`${HARNESS_USER_BUBBLE} opacity-90`}>
            <span className="mr-1.5 text-[11px] uppercase tracking-wide text-zinc-400">Note</span>
            {item.text}
          </div>
          {item.files.length ? <SentAttachments paths={item.files} /> : null}
          <p data-testid="code-steer-state" className={`text-[11px] ${item.state === "returned" ? "text-tone-warn" : "text-zinc-500"}`}>
            {steerWords(item.state, item.how, toolName)}
          </p>
        </div>
      );
    case "text":
      return (
        <div className="group/reply">
          <div data-testid="code-text" data-done={item.done ? "true" : "false"} className={HARNESS_REPLY_PROSE}>
            <MemoMarkdown content={item.text} />
          </div>
          {item.done && copyText ? <ReplyCopy text={copyText} /> : null}
        </div>
      );
    case "thinking":
      return (
        <div data-testid="code-thinking">
          <ThinkingDisclosure text={item.text} live={item.live} seconds={item.seconds} />
        </div>
      );
    case "tool":
      return <ToolStep tool={item.tool} folder={folder} kids={item.children} onExpand={onExpand} toolName={toolName} />;
    case "plan":
      return (
        <div data-testid="code-plan">
          <PlanChecklist plan={{ steps: planChecklistSteps(item.steps), total: item.steps.length }} />
        </div>
      );
    case "resolved":
      return (
        <p data-testid="code-resolved" data-decision={item.decision} className="flex items-center gap-2 text-[12px] text-zinc-500">
          <CheckSquare size={13} aria-hidden="true" className="shrink-0" />
          <span className="min-w-0 truncate">
            {resolvedWords(item.decision, item.question)}
            {item.about ? `: ${item.about}` : ""}
          </span>
        </p>
      );
    case "notice":
      return (
        <p data-testid="code-notice" className="text-[12px] text-zinc-500">
          {item.text}
        </p>
      );
    case "error":
      return (
        <div data-testid="code-error" data-fix-kind={item.fixKind} role="alert" className="text-[13px]" title={item.technical || undefined}>
          <p className="text-tone-danger">{item.title}</p>
          {item.fix ? <p className="text-zinc-400">{item.fix}</p> : null}
          {press && item.fixKind === "account_limit" && press.wait ? (
            // v1.422.0 (B5): it waits (or looks for another account of the
            // group) and carries on BY ITSELF — said, with until when.
            <p data-testid="code-limit-waiting" role="status" className="mt-1 flex flex-wrap items-center gap-x-2 text-zinc-300">
              <span>{waitWords(press.wait)}</span>
              {press.onCancelWait ? (
                <button
                  type="button"
                  data-testid="code-wait-cancel"
                  onClick={press.onCancelWait}
                  title={CANCEL_WAIT_TITLE}
                  className="inline-flex h-7 items-center rounded-lg px-2 text-[13px] text-zinc-200 transition-colors hover:bg-white/[0.06]"
                >
                  {CANCEL_WAIT}
                </button>
              ) : null}
            </p>
          ) : null}
          {press && item.fixKind === "account_limit" && press.limitNote ? (
            <p data-testid="code-limit-note" className="mt-1 text-zinc-400">
              {press.limitNote}
            </p>
          ) : null}
          {press && item.fixKind === "account_limit" && press.onContinueOn && canMoveAccounts(press.tool) ? (
            <button
              type="button"
              data-testid="code-continue-on"
              disabled={press.continuing}
              onClick={press.onContinueOn}
              className="mt-1 inline-flex h-7 items-center rounded-lg px-2 text-[13px] text-zinc-200 transition-colors hover:bg-white/[0.06] disabled:opacity-50"
            >
              {CONTINUE_ON_ANOTHER}
            </button>
          ) : null}
          {press && item.fixKind === "account_limit" && press.onTryAccount && press.tryChoices?.length
            && canMoveAccounts(press.tool) ? (
            <div data-testid="code-try-accounts" className="mt-1 text-zinc-400">
              <p>{TRY_ACCOUNTS_LEAD}</p>
              <div className="mt-1 flex flex-wrap gap-1">
                {press.tryChoices.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    data-testid={`code-try-account-${c.id}`}
                    disabled={press.continuing}
                    title={c.why || undefined}
                    onClick={() => press.onTryAccount?.(c.id)}
                    className="inline-flex h-7 items-center rounded-lg px-2 text-[13px] text-zinc-200 transition-colors hover:bg-white/[0.06] disabled:opacity-50"
                  >
                    {tryAccountWords(c.title)}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
          {press && item.fixKind === "sign_in" && signInProvider(press.tool) ? (
            <CliSignInPress provider={signInProvider(press.tool)} testIdPrefix="code" />
          ) : null}
          {press && press.cut && item.fixKind !== "account_limit" ? (
            // v1.422.0 (B3): a reply a restart cut off that did not carry on
            // by itself says why, and Continue carries it on (the tool's own
            // resume, told to check what was already done).
            <p data-testid="code-cut-why" className="mt-1 text-zinc-400">
              {cutWords(press.cut)}
            </p>
          ) : null}
          {press && press.cut && press.cut.resumable && press.onContinueCut && item.fixKind !== "account_limit" ? (
            <button
              type="button"
              data-testid="code-continue-cut"
              disabled={press.continuingCut}
              onClick={press.onContinueCut}
              title={CONTINUE_CUT_TITLE}
              className="mt-1 inline-flex h-7 items-center gap-1.5 rounded-lg px-2 text-[13px] text-zinc-200 transition-colors hover:bg-white/[0.06] disabled:opacity-50"
            >
              <RotateCcw size={13} aria-hidden="true" /> {CONTINUE_CUT}
            </button>
          ) : null}
          {press && press.onRetry && item.fixKind !== "account_limit" && !(press.cut && press.cut.resumable) ? (
            <button
              type="button"
              data-testid="code-retry"
              onClick={press.onRetry}
              title="Send the same message again"
              className="mt-1 inline-flex h-7 items-center gap-1.5 rounded-lg px-2 text-[13px] text-zinc-200 transition-colors hover:bg-white/[0.06]"
            >
              <RotateCcw size={13} aria-hidden="true" /> Retry
            </button>
          ) : null}
          {item.technical ? (
            <details className="mt-0.5 text-[12px] text-zinc-500">
              <summary className="cursor-pointer">Details</summary>
              <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[12px]">{item.technical}</pre>
            </details>
          ) : null}
        </div>
      );
    case "usage":
      // v1.417.0 (C4.9): a turn that ended says it inside its receipt.
      if (item.folded) return null;
      return (
        <p data-testid="code-usage" className="text-[11px] text-zinc-600">
          {usageWords(item)}
        </p>
      );
    case "done": {
      const words = doneWords(item.stop);
      const said = words ? (
        <p data-testid="code-done" data-stop={item.stop} className="text-[12px] text-zinc-500">
          {words}
        </p>
      ) : (
        <span data-testid="code-done" data-stop={item.stop} hidden />
      );
      const r = item.receipt;
      if (!r) return said;
      // v1.417.0 (C4.9): the reply line, folded like Chat › Code's receipt:
      // who answered, how long, what it did; the usage line inside.
      const line = receiptLine({ ...r, model: friendlyModelName(r.model || facts?.model || "") }, facts?.account ?? "");
      return (
        <div className="grid gap-1">
          {said}
          {line || r.usage ? (
            <details data-testid="code-receipt" className="text-[11px] text-zinc-600">
              <summary data-testid="code-receipt-line" className="cursor-pointer select-none hover:text-zinc-400">
                {line || "Details"}
              </summary>
              {r.usage ? (
                <p data-testid="code-usage" className="mt-0.5 pl-3">
                  {usageWords(r.usage)}
                </p>
              ) : null}
            </details>
          ) : null}
        </div>
      );
    }
  }
});

export const CodeConversation = memo(function CodeConversation({
  items,
  folder,
  tool = "",
  onContinueOn,
  continuing = false,
  tryChoices,
  onTryAccount,
  onRetry,
  cut = null,
  onContinueCut,
  continuingCut = false,
  wait = null,
  onCancelWait,
  limitNote = "",
  model = "",
  account = "",
  onExpand,
  messageActions,
}: {
  items: readonly ConvItem[];
  folder: string;
  /** The session's coding tool (the Sign in press names it). */
  tool?: string;
  onContinueOn?: () => void;
  continuing?: boolean;
  /** v1.427.3: accounts to try anyway after none was free. */
  tryChoices?: readonly TryChoice[];
  onTryAccount?: (id: string) => void;
  /** v1.417.0 (C4.9): send the failed reply's message again (absent while
   *  a reply runs). */
  onRetry?: () => void;
  /** v1.422.0 (B3): the newest reply was cut off by a restart (Continue). */
  cut?: CutFacts | null;
  onContinueCut?: () => void;
  continuingCut?: boolean;
  /** v1.422.0 (B5): the limit card waits by itself (until when; Cancel). */
  wait?: WaitFacts | null;
  onCancelWait?: () => void;
  limitNote?: string;
  /** v1.417.0 (C4.9): the session's model and account, for the receipt. */
  model?: string;
  account?: string;
  /** v1.418.0 (C4.4): read a step's whole text back (`GET …/events/{item}`); absent = no "Show all" press. */
  onExpand?: ExpandStep;
  /** v1.441.0: Branch from here / Rewind to here (absent = none offered). */
  messageActions?: MessageActions | null;
}) {
  const facts = useMemo(() => ({ model, account }), [model, account]);
  const copies = useMemo(() => replyCopyTexts(items), [items]);
  let lastError = -1;
  items.forEach((it, i) => {
    if (it.kind === "error") lastError = i;
  });
  // A failure is worth a press only while it is the newest thing said.
  const newest = lastError >= 0 && !items.slice(lastError + 1).some((it) => it.kind === "user" || it.kind === "text");
  return (
    <div data-testid="code-conversation" className="grid gap-3">
      {items.map((item, i) => (
        <Item
          key={item.key}
          item={item}
          folder={folder}
          press={
            newest && i === lastError
              ? { tool, onContinueOn, continuing, tryChoices, onTryAccount, onRetry, cut, onContinueCut,
                  continuingCut, wait, onCancelWait, limitNote }
              : null
          }
          toolName={toolLabel(tool)}
          facts={facts}
          onExpand={onExpand}
          copyText={copies.get(item.key) ?? ""}
          actions={
            messageActions && item.kind === "user" && !item.carry && item.at && messageActions.facts.at.has(item.at)
              ? messageActions
              : undefined
          }
        />
      ))}
    </div>
  );
});

export default CodeConversation;
