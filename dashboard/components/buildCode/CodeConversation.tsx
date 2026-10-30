"use client";

/**
 * The conversation of one Build Code view session (v1.409.0), drawn from the
 * folded events (lib/buildApp.foldConversation) with the chat's own pieces:
 * Markdown for the replies, the work rows (components/chat/WorkLine) that
 * open to a step's input and output, the inline diff (DiffView's body) for a
 * change, Open in VS Code / Cursor beside a file step, the plan checklist,
 * and the thinking disclosure. Nothing here talks to Iron Jarvis.
 */

import { memo, useId, useState } from "react";
import {
  Bot,
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
import { HARNESS_REPLY_PROSE, HARNESS_USER_BUBBLE } from "@/components/harness/harnessLook";
import {
  doneWords,
  planChecklistSteps,
  resolvedWords,
  usageWords,
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

function ToolStep({ tool, folder }: { tool: ToolEvent; folder: string }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const hasDetail = !!(tool.input || tool.output);
  // Inside the session's folder only (never a share, never `..` out of it).
  const path = tool.path && folder ? resolveInFolder(folder, tool.path, { confine: true }) : null;
  return (
    <div data-testid="code-tool" data-kind={tool.kind} data-status={tool.status} className="group/step min-w-0">
      <div className="flex min-w-0 items-center gap-1">
        <div className="min-w-0 flex-1">
          <WorkRow
            icon={TOOL_ICON[tool.kind]}
            title={toolWords(tool)}
            running={tool.status === "running"}
            failed={tool.status === "failed"}
            titleTitle={tool.name || undefined}
            expand={hasDetail ? { open, onToggle: () => setOpen((v) => !v), controls: panelId } : null}
          />
        </div>
        {path && tool.status !== "running" && (tool.kind === "edit" || tool.kind === "write" || tool.kind === "read") ? (
          <StepOpenInEditor path={path} line={tool.line} />
        ) : null}
      </div>
      {open && hasDetail ? (
        <StepDetailPanel id={panelId} name={tool.kind === "command" ? "shell" : tool.name} detail={{ input: tool.input, output: tool.output }} />
      ) : null}
      {tool.diff ? (
        <div data-testid="code-tool-diff" className="ml-[22px] mt-1 max-h-80 overflow-auto rounded-md">
          <DiffBody change={{ path: tool.path || "file", status: "modified", added: null, removed: null, diff: tool.diff }} />
        </div>
      ) : null}
    </div>
  );
}

function Item({ item, folder }: { item: ConvItem; folder: string }) {
  switch (item.kind) {
    case "user":
      return (
        <div data-testid="code-user" className="flex justify-end">
          <div className={HARNESS_USER_BUBBLE}>
            {item.text}
            {item.files.length ? (
              <span className="mt-1 block text-[12px] text-zinc-400">{item.files.join(", ")}</span>
            ) : null}
          </div>
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
          <ThinkingDisclosure text={item.text} />
        </div>
      );
    case "tool":
      return <ToolStep tool={item.tool} folder={folder} />;
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
            {resolvedWords(item.decision)}
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
          {item.technical ? (
            <details className="mt-0.5 text-[12px] text-zinc-500">
              <summary className="cursor-pointer">Details</summary>
              <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[12px]">{item.technical}</pre>
            </details>
          ) : null}
        </div>
      );
    case "usage":
      return (
        <p data-testid="code-usage" className="text-[11px] text-zinc-600">
          {usageWords(item)}
        </p>
      );
    case "done": {
      const words = doneWords(item.stop);
      return words ? (
        <p data-testid="code-done" data-stop={item.stop} className="text-[12px] text-zinc-500">
          {words}
        </p>
      ) : (
        <span data-testid="code-done" data-stop={item.stop} hidden />
      );
    }
  }
}

export const CodeConversation = memo(function CodeConversation({
  items,
  folder,
}: {
  items: readonly ConvItem[];
  folder: string;
}) {
  return (
    <div data-testid="code-conversation" className="grid gap-3">
      {items.map((item) => (
        <Item key={item.key} item={item} folder={folder} />
      ))}
    </div>
  );
});

export default CodeConversation;
