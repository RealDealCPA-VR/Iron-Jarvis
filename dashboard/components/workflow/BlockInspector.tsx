"use client";

// The side panel for a selected "If" or "For each" (idea 09, phase 09-a).
// An If gets a three-field check (a value from an earlier step, a
// comparison, what to compare it with); a For each gets what it works
// through and its limits. Steps go inside with Add step (the toolbar's, or
// the quiet buttons here for each lane). Overlays the canvas's right edge,
// like the step panel.

import { useId } from "react";
import { Plus, Repeat, Split, Trash2, X } from "lucide-react";
import {
  BLOCK_META,
  DEFAULT_MAX_ITEMS,
  EACH_COST_LINE,
  FOLDER_ITEMS_CAP,
  MAX_ITEMS_CAP,
  MAX_PARALLEL,
  OP_OPTIONS,
  OTHERWISE_WORD,
  THEN_WORD,
  conditionLeaves,
  isOneSided,
  type ConditionLeaf,
} from "./blocks";
import type { BlockNodeData } from "./EachNode";
import { WF_GHOST_ON_SM, WF_ICON_GHOST, WF_LABEL, WF_POPOVER } from "./calm";

export function BlockInspector({
  data,
  onChange,
  onDelete,
  onClose,
  onAddInside,
  suggestions = [],
  hasAgent = false,
  problem = null,
}: {
  data: BlockNodeData;
  onChange: (patch: Partial<BlockNodeData>) => void;
  onDelete: () => void;
  onClose: () => void;
  /** Add a step inside: "body" (a For each), "then" or "else" (an If). */
  onAddInside: (branch: string) => void;
  /** `{{names}}` an earlier step gives (the value box offers them). */
  suggestions?: string[];
  /** A For each with an Agent step inside says what that costs (D-09.1). */
  hasAgent?: boolean;
  /** Why the last save refused this block, in the daemon's words. */
  problem?: string | null;
}) {
  const isIf = data.kind === "if";
  const meta = isIf ? BLOCK_META.if : BLOCK_META.for_each;
  const Icon = isIf ? Split : Repeat;
  const listId = useId();
  return (
    <div
      data-testid="block-inspector"
      className={`${WF_POPOVER} absolute right-3 top-3 bottom-3 z-20 flex w-[300px] max-w-[calc(100%-1.5rem)] flex-col overflow-hidden`}
    >
      <header className="flex items-center justify-between gap-3 border-b hairline px-4 py-2.5">
        <h3 className="flex items-center gap-2 text-[13px] font-medium text-zinc-200">
          <Icon size={14} className="text-zinc-500" />
          Edit {meta.label}
        </h3>
        <button type="button" onClick={onClose} aria-label="Close inspector" className={WF_ICON_GHOST}>
          <X size={14} />
        </button>
      </header>

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {problem ? (
          <p role="alert" data-testid="step-problem" className="rounded-lg bg-tone-danger/10 px-2.5 py-2 text-[12px] text-tone-danger">
            {problem}
          </p>
        ) : null}
        <p className="text-[11px] leading-snug text-zinc-500">{meta.blurb}</p>
        <div>
          <label className={WF_LABEL}>Name</label>
          <input
            value={data.name}
            onChange={(e) => onChange({ name: e.target.value })}
            aria-label={`${meta.label} name`}
            className="field"
          />
        </div>

        <datalist id={listId}>
          {suggestions.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>

        {isIf ? (
          <ConditionFields data={data} onChange={onChange} listId={listId} />
        ) : (
          <EachFields data={data} onChange={onChange} listId={listId} hasAgent={hasAgent} />
        )}

        <div>
          <label className={WF_LABEL}>{isIf ? "If it cannot decide, or a step in it fails" : "If an item fails"}</label>
          <select
            aria-label={isIf ? "If it fails" : "If an item fails"}
            value={data.on_failure === "skip" ? "skip" : "halt"}
            onChange={(e) => onChange({ on_failure: e.target.value })}
            className="field"
          >
            <option value="halt">Stop the run (default)</option>
            <option value="skip">{isIf ? "Skip it and continue" : "Skip that item and go on"}</option>
          </select>
        </div>

        <div className="space-y-1">
          <span className={WF_LABEL}>Steps inside</span>
          {isIf ? (
            <div className="flex flex-wrap gap-1">
              <button type="button" onClick={() => onAddInside("then")} className={WF_GHOST_ON_SM}>
                <Plus size={13} /> Add a step under {THEN_WORD}
              </button>
              <button type="button" onClick={() => onAddInside("else")} className={WF_GHOST_ON_SM}>
                <Plus size={13} /> Add a step under {OTHERWISE_WORD}
              </button>
            </div>
          ) : (
            <button type="button" onClick={() => onAddInside("body")} className={WF_GHOST_ON_SM}>
              <Plus size={13} /> Add a step inside
            </button>
          )}
          <p className="text-[11px] text-zinc-500">
            Select a step inside to edit it. They run in the order shown.
          </p>
        </div>
      </div>

      <footer className="border-t hairline p-3">
        <button
          type="button"
          onClick={onDelete}
          title="Deletes the steps inside it too"
          className="flex h-9 w-full items-center justify-center gap-2 rounded-lg text-[13px] font-medium text-tone-danger transition-colors hover:bg-tone-danger/10 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
        >
          <Trash2 size={15} /> Delete {meta.label} and its steps
        </button>
      </footer>
    </div>
  );
}

function ConditionFields({
  data,
  onChange,
  listId,
}: {
  data: BlockNodeData;
  onChange: (patch: Partial<BlockNodeData>) => void;
  listId: string;
}) {
  const { join, leaves } = conditionLeaves(data.condition);
  const shown: ConditionLeaf[] = leaves.length ? leaves : [{ left: "", op: "is", right: "" }];
  const write = (next: ConditionLeaf[], nextJoin: "all" | "any" | null = join) => {
    const clean = next.map((l) => {
      const leaf: ConditionLeaf = { left: l.left ?? "", op: l.op || "is" };
      if (!isOneSided(leaf.op)) leaf.right = l.right ?? "";
      return leaf;
    });
    onChange({
      condition: (clean.length > 1
        ? { [nextJoin ?? "all"]: clean }
        : clean[0]) as unknown as Record<string, unknown>,
    });
  };
  const setLeaf = (i: number, patch: ConditionLeaf) =>
    write(shown.map((l, k) => (k === i ? { ...l, ...patch } : l)));
  return (
    <div className="space-y-3" data-testid="condition-builder">
      {shown.map((leaf, i) => (
        <div key={i} className="space-y-1.5 rounded-xl border hairline p-2.5">
          <div>
            <label className={WF_LABEL}>A value from an earlier step</label>
            <input
              value={leaf.left ?? ""}
              list={listId}
              onChange={(e) => setLeaf(i, { left: e.target.value })}
              placeholder="e.g. {{Read total.data.total}}"
              aria-label="The value to check"
              className="field font-mono text-[12px]"
            />
          </div>
          <div>
            <label className={WF_LABEL}>Comparison</label>
            <select
              aria-label="Comparison"
              value={leaf.op || "is"}
              onChange={(e) => setLeaf(i, { op: e.target.value })}
              className="field"
            >
              {OP_OPTIONS.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
          {!isOneSided(leaf.op) && (
            <div>
              <label className={WF_LABEL}>Compared with</label>
              <input
                value={leaf.right ?? ""}
                list={listId}
                onChange={(e) => setLeaf(i, { right: e.target.value })}
                placeholder="e.g. 5,000"
                aria-label="Compared with"
                className="field"
              />
            </div>
          )}
          {shown.length > 1 && (
            <button
              type="button"
              onClick={() => write(shown.filter((_, k) => k !== i))}
              className="text-[11px] text-zinc-500 underline-offset-2 hover:text-zinc-200 hover:underline"
            >
              Remove this check
            </button>
          )}
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-2">
        {shown.length > 1 && (
          <select
            aria-label="Which checks must hold"
            value={join ?? "all"}
            onChange={(e) => write(shown, e.target.value as "all" | "any")}
            className="field w-auto text-[12px]"
          >
            <option value="all">All of these must hold</option>
            <option value="any">Any one is enough</option>
          </select>
        )}
        <button
          type="button"
          onClick={() => write([...shown, { left: "", op: "is", right: "" }], join ?? "all")}
          className="text-[11px] text-zinc-500 underline-offset-2 hover:text-zinc-200 hover:underline"
        >
          Add another check
        </button>
      </div>
      <p className="text-[11px] text-zinc-500">
        Numbers like 5,000.00 are read as numbers. A value that is not a number stops the If and
        says why; nothing is guessed.
      </p>
    </div>
  );
}

function EachFields({
  data,
  onChange,
  listId,
  hasAgent,
}: {
  data: BlockNodeData;
  onChange: (patch: Partial<BlockNodeData>) => void;
  listId: string;
  hasAgent: boolean;
}) {
  const over = (data.over ?? {}) as { from?: string; files?: { folder?: string; glob?: string } };
  const fromFiles = over.files !== undefined || over.from === undefined;
  const files = over.files ?? {};
  return (
    <div className="space-y-3" data-testid="each-fields">
      <div>
        <label className={WF_LABEL}>Work through</label>
        <select
          aria-label="Work through"
          value={fromFiles ? "files" : "from"}
          onChange={(e) =>
            onChange({
              over: (e.target.value === "files"
                ? { files: { folder: files.folder ?? "", glob: files.glob ?? "" } }
                : { from: over.from ?? "" }) as Record<string, unknown>,
            })
          }
          className="field"
        >
          <option value="files">Files in a folder</option>
          <option value="from">A list an earlier step found</option>
        </select>
      </div>
      {fromFiles ? (
        <>
          <div>
            <label className={WF_LABEL}>Folder</label>
            <input
              value={files.folder ?? ""}
              onChange={(e) => onChange({ over: { files: { ...files, folder: e.target.value } } })}
              placeholder="e.g. C:\Clients\Intake"
              aria-label="Folder"
              className="field font-mono text-[12px]"
            />
          </div>
          <div>
            <label className={WF_LABEL}>Only names like (optional)</label>
            <input
              value={files.glob ?? ""}
              onChange={(e) => onChange({ over: { files: { ...files, glob: e.target.value } } })}
              placeholder="e.g. *.pdf"
              aria-label="Only names like"
              className="field font-mono text-[12px]"
            />
          </div>
          <p className="text-[11px] text-zinc-500">
            Inside, {"{{item}}"} is the file, {"{{item.name}}"} its name and {"{{index}}"} its number.
          </p>
        </>
      ) : (
        <div>
          <label className={WF_LABEL}>The list</label>
          <input
            value={over.from ?? ""}
            list={listId}
            onChange={(e) => onChange({ over: { from: e.target.value } })}
            placeholder="e.g. {{Find clients.data}}"
            aria-label="The list"
            className="field font-mono text-[12px]"
          />
          <p className="mt-1.5 text-[11px] text-zinc-500">
            Inside, {"{{item}}"} is the item and {"{{index}}"} its number.
          </p>
        </div>
      )}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className={WF_LABEL}>At most</label>
          <input
            type="number"
            min={1}
            max={fromFiles ? FOLDER_ITEMS_CAP : MAX_ITEMS_CAP}
            value={Number(data.max_items ?? DEFAULT_MAX_ITEMS)}
            onChange={(e) => {
              const n = Math.round(Number(e.target.value));
              onChange({ max_items: Number.isFinite(n) ? n : DEFAULT_MAX_ITEMS });
            }}
            aria-label="At most this many items"
            className="field"
          />
          <span className="mt-1 block text-[11px] text-zinc-500">items in a run</span>
        </div>
        <div>
          <label className={WF_LABEL}>Items at once</label>
          <select
            aria-label="Items at once"
            value={String(data.parallel ?? 1)}
            onChange={(e) => onChange({ parallel: Number(e.target.value) })}
            className="field"
          >
            {Array.from({ length: MAX_PARALLEL }, (_, i) => i + 1).map((n) => (
              <option key={n} value={String(n)}>
                {n}
              </option>
            ))}
          </select>
        </div>
      </div>
      {hasAgent && (
        <p data-testid="each-cost-line" className="text-[11px] leading-snug text-tone-warn">
          {EACH_COST_LINE}
        </p>
      )}
    </div>
  );
}
