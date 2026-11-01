"use client";

/**
 * The conversation of one Build Code view session (v1.409.0), drawn from the
 * folded events (lib/buildApp.foldConversation) with the chat's own pieces:
 * Markdown for the replies, the work rows (components/chat/WorkLine) that
 * open to a step's input and output, the inline diff (DiffView's body) for a
 * change, Open in VS Code / Cursor beside a file step, the plan checklist,
 * and the thinking disclosure. Nothing here talks to Iron Jarvis.
 */

import { memo, useId, useMemo, useState, type ReactNode } from "react";
import {
  Bot,
  RotateCcw,
  CheckSquare,
  FileText,
  FilePen,
  FilePlus,
  Globe,
  ListTodo,
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

/** A step's words when the tool sent no title. */
function toolWords(t: ToolEvent): string {
  if (t.title) return t.title;
  const name = t.name || "a tool";
  switch (t.kind) {
    case "read":
      return t.path ? `Read ${t.path}` : "Read a file";
    case "edit":
      return t.path ? `Changed ${t.path}` : "Changed a file";
    case "write":
      return t.path ? `Wrote ${t.path}` : "Wrote a file";
    case "command":
      return "Ran a command";
    case "search":
      return "Searched";
    case "web":
      return "Looked on the web";
    default:
      return `Used ${name}`;
  }
}

/** How many times a row drew (tests count these; never read by the app). */
export const codeRowDraws = { items: 0, tools: 0 };

/** v1.418.0 (C4.4): read one step's whole text back from the session's
 *  record (BuildCodeView supplies it: `GET …/events/{item}`). */
export type ExpandStep = (item: string) => Promise<ItemText | null>;

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
  const words = toolWords(tool);
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
}: {
  item: ConvItem;
  folder: string;
  press: FailurePresses | null;
  toolName: string;
  facts?: ReceiptFacts;
  onExpand?: ExpandStep;
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
        <div data-testid="code-user" className="flex flex-col items-end gap-1.5">
          {item.text.trim() ? <div className={HARNESS_USER_BUBBLE}>{item.text}</div> : null}
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
        <div data-testid="code-text" data-done={item.done ? "true" : "false"} className={HARNESS_REPLY_PROSE}>
          <MemoMarkdown content={item.text} />
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
}: {
  items: readonly ConvItem[];
  folder: string;
  /** The session's coding tool (the Sign in press names it). */
  tool?: string;
  onContinueOn?: () => void;
  continuing?: boolean;
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
}) {
  const facts = useMemo(() => ({ model, account }), [model, account]);
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
              ? { tool, onContinueOn, continuing, onRetry, cut, onContinueCut, continuingCut, wait, onCancelWait, limitNote }
              : null
          }
          toolName={toolLabel(tool)}
          facts={facts}
          onExpand={onExpand}
        />
      ))}
    </div>
  );
});

export default CodeConversation;
