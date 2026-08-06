"use client";

// The new-thread / edit-panel experience: pick agents from the three grouped
// sources (Built-in / Yours / Remote) as selectable cards; selecting one opens
// its role picker (preset chips + free text). The assembled panel shows as a
// horizontal strip of chips — that order is the speaking order.
//
// The catalog is roster-fed when the daemon serves GET /agents/roster (the
// caller maps entries to options, offline remotes included-but-disabled);
// older daemons fall back to the raw /agents + /agents/remote lists.

import { useEffect, useState, type ReactNode } from "react";
import {
  Bot,
  CheckCircle2,
  Circle,
  Globe,
  Plus,
  Save,
  Sparkles,
  Users,
  WifiOff,
  X,
} from "lucide-react";
import { ApiError } from "@/lib/api";
import { ErrorNote, LoaderInline } from "@/components/ui";
import {
  AgentAvatar,
  ROLE_PRESETS,
  RolePill,
  SOURCE_LABEL,
  SourceIcon,
  participantKey,
  type AgentSource,
  type Participant,
} from "./identity";

/** One pickable agent. Offline remotes are shown but not addable — an honest
 *  seat you can see exists, not a silent omission. */
export interface PickerOption {
  source: AgentSource;
  name: string;
  description?: string;
  /** True → shown with the offline pill and not selectable. */
  offline?: boolean;
}

/** What the picker offers: the full agent catalog, grouped by source. */
export interface PickerCatalog {
  builtin: PickerOption[];
  dynamic: PickerOption[];
  remotes: PickerOption[];
}

function SourceGroup({
  source,
  icon,
  count,
  hint,
  children,
}: {
  source: AgentSource;
  icon: ReactNode;
  count: number;
  hint: string;
  children: ReactNode;
}) {
  return (
    <section>
      <div className="mb-2 flex items-center gap-2">
        <span className="text-accent-soft/70">{icon}</span>
        <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-400">
          {SOURCE_LABEL[source]}
          {count > 0 && <span className="ml-1 text-zinc-600">· {count}</span>}
        </span>
      </div>
      {count === 0 ? (
        <p className="rounded-xl border border-dashed border-white/[0.08] px-3 py-2.5 text-[11px] leading-relaxed text-zinc-600">
          {hint}
        </p>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2">{children}</div>
      )}
    </section>
  );
}

function AgentPickCard({
  option,
  selected,
  role,
  onToggle,
  onRole,
}: {
  option: PickerOption;
  selected: boolean;
  role: string;
  onToggle: () => void;
  onRole: (role: string) => void;
}) {
  const { source, name, description, offline } = option;
  const key = participantKey(source, name);
  // An offline remote can't be ADDED — but if it's already seated (edit mode),
  // removing it must stay possible.
  const locked = Boolean(offline) && !selected;
  return (
    <div
      className={`rounded-xl border transition-colors ${
        selected
          ? "border-accent/40 bg-accent/[0.07]"
          : locked
            ? "border-white/[0.06] bg-white/[0.02] opacity-55"
            : "border-white/[0.06] bg-white/[0.02] hover:border-white/15"
      }`}
    >
      <button
        type="button"
        onClick={onToggle}
        disabled={locked}
        aria-pressed={selected}
        title={
          locked
            ? `${name} is offline — it can't join right now`
            : selected
              ? `Remove ${name} from the panel`
              : `Add ${name} to the panel`
        }
        className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left disabled:cursor-not-allowed"
      >
        <AgentAvatar agentKey={key} name={name} size="md" />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span
              className={`truncate text-[13px] font-medium ${
                selected ? "text-accent-soft" : "text-zinc-100"
              }`}
            >
              {name}
            </span>
            {offline && (
              <span className="inline-flex shrink-0 items-center gap-1 rounded-md border border-rose-500/25 bg-rose-500/10 px-1.5 py-0.5 text-[10px] font-medium text-rose-300">
                <WifiOff size={10} /> offline
              </span>
            )}
          </span>
          {description && (
            <span className="block truncate text-[11px] text-zinc-500">{description}</span>
          )}
        </span>
        <SourceIcon source={source} size={12} />
        {selected ? (
          <CheckCircle2 size={15} className="shrink-0 text-accent-soft" />
        ) : (
          <Circle size={15} className="shrink-0 text-zinc-700" />
        )}
      </button>

      {selected && (
        <div className="space-y-1.5 border-t border-white/[0.06] px-3 py-2.5">
          <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-zinc-500">
            Role on this panel
          </div>
          <div className="flex flex-wrap gap-1">
            {ROLE_PRESETS.map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => onRole(r)}
                className={`rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide transition-colors ${
                  role === r
                    ? "border-accent/40 bg-accent/[0.14] text-accent-soft"
                    : "border-white/10 bg-white/[0.03] text-zinc-400 hover:border-white/20 hover:text-zinc-200"
                }`}
              >
                {r}
              </button>
            ))}
          </div>
          <input
            value={role}
            onChange={(e) => onRole(e.target.value)}
            placeholder="or type any role…"
            aria-label={`Role for ${name}`}
            className="field py-1 text-xs"
          />
        </div>
      )}
    </div>
  );
}

export function PanelPicker({
  mode,
  catalog,
  initialTitle = "",
  initialParticipants = [],
  onClose,
  onSubmit,
}: {
  mode: "create" | "edit";
  catalog: PickerCatalog;
  initialTitle?: string;
  initialParticipants?: Participant[];
  onClose: () => void;
  /** Resolves on success (the caller closes the modal); throws to show here. */
  onSubmit: (title: string, participants: Participant[]) => Promise<void>;
}) {
  const [title, setTitle] = useState(initialTitle);
  const [selected, setSelected] = useState<Participant[]>(() =>
    initialParticipants.map((p) => ({ ...p })),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Escape closes (unless a submit is in flight).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !busy) onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  function isSelected(key: string) {
    return selected.some((p) => p.key === key);
  }

  function toggle(source: AgentSource, name: string) {
    const key = participantKey(source, name);
    setSelected((prev) =>
      prev.some((p) => p.key === key)
        ? prev.filter((p) => p.key !== key)
        : [...prev, { key, source, name, role: "" }],
    );
  }

  function setRole(key: string, role: string) {
    setSelected((prev) => prev.map((p) => (p.key === key ? { ...p, role } : p)));
  }

  function roleFor(key: string) {
    return selected.find((p) => p.key === key)?.role ?? "";
  }

  function cards(options: PickerOption[]) {
    return options.map((o) => {
      const key = participantKey(o.source, o.name);
      return (
        <AgentPickCard
          key={key}
          option={o}
          selected={isSelected(key)}
          role={roleFor(key)}
          onToggle={() => toggle(o.source, o.name)}
          onRole={(r) => setRole(key, r)}
        />
      );
    });
  }

  async function submit() {
    if (selected.length === 0 || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit(
        title.trim(),
        selected.map((p) => ({ ...p, role: p.role.trim() })),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onClick={() => {
        if (!busy) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={mode === "create" ? "New agent thread" : "Edit the panel"}
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-white/10 bg-ink-850/95 shadow-card-hover backdrop-blur-xl"
      >
        {/* Header */}
        <header className="flex shrink-0 items-center gap-2 border-b hairline px-4 py-3">
          <Users size={16} className="text-accent-soft/80" />
          <h2 className="text-[13px] font-semibold tracking-wide text-zinc-200">
            {mode === "create" ? "New thread — assemble the panel" : "Edit the panel"}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="ml-auto grid h-7 w-7 place-items-center rounded-lg text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
          >
            <X size={15} />
          </button>
        </header>

        {/* Body */}
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
          {mode === "create" && (
            <div>
              <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                Title
              </label>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="optional — named after the panel if left blank"
                className="field"
              />
            </div>
          )}

          <p className="text-[11px] leading-relaxed text-zinc-500">
            Pick who sits at this round-table and give each a role. Every agent
            answers in the order you pick them, seeing the replies before it —
            so a critic picked after a builder critiques what the builder just
            said.
          </p>

          <SourceGroup
            source="builtin"
            icon={<Bot size={13} />}
            count={catalog.builtin.length}
            hint="No built-in agents available — is the daemon reachable?"
          >
            {cards(catalog.builtin)}
          </SourceGroup>

          <SourceGroup
            source="dynamic"
            icon={<Sparkles size={13} />}
            count={catalog.dynamic.length}
            hint="No agents of your own yet — create one in “Set up agents”; its persona and model carry into every thread it joins."
          >
            {cards(catalog.dynamic)}
          </SourceGroup>

          <SourceGroup
            source="remote"
            icon={<Globe size={13} />}
            count={catalog.remotes.length}
            hint="No remote agents connected — register one in “Set up agents” to bring an agent running on another computer onto the panel."
          >
            {cards(catalog.remotes)}
          </SourceGroup>
        </div>

        {/* Footer — the assembled panel + submit */}
        <footer className="shrink-0 space-y-2.5 border-t hairline px-4 py-3">
          {selected.length > 0 ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-1 text-[10px] font-medium uppercase tracking-[0.12em] text-zinc-500">
                Panel · speaks in this order
              </span>
              {selected.map((p) => (
                <span
                  key={p.key}
                  className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.08] bg-white/[0.03] py-0.5 pl-1 pr-1"
                >
                  <AgentAvatar agentKey={p.key} name={p.name} size="sm" />
                  <span className="text-xs text-zinc-200">{p.name}</span>
                  <RolePill role={p.role.trim() || "participant"} />
                  <button
                    type="button"
                    onClick={() => setSelected((prev) => prev.filter((x) => x.key !== p.key))}
                    aria-label={`Remove ${p.name}`}
                    className="grid h-4 w-4 place-items-center rounded-full text-zinc-500 transition-colors hover:bg-white/[0.08] hover:text-zinc-200"
                  >
                    <X size={10} />
                  </button>
                </span>
              ))}
            </div>
          ) : (
            <p className="text-[11px] text-zinc-500">
              Pick at least one agent to form the panel.
            </p>
          )}
          {error && <ErrorNote>{error}</ErrorNote>}
          <div className="flex items-center justify-end gap-2">
            <button type="button" onClick={onClose} disabled={busy} className="btn-ghost text-xs">
              Cancel
            </button>
            <button
              type="button"
              onClick={submit}
              disabled={busy || selected.length === 0}
              className="btn-accent text-xs"
            >
              {busy ? (
                <LoaderInline label={mode === "create" ? "Creating…" : "Saving…"} />
              ) : mode === "create" ? (
                <>
                  <Plus size={14} /> Create thread
                </>
              ) : (
                <>
                  <Save size={14} /> Save panel
                </>
              )}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
