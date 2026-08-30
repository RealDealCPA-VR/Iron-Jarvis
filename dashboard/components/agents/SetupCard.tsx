"use client";

// "Set up agents" — the management surface, collapsed into one card so the
// threads stay the star. Two columns on lg: your (dynamic) agents and remote
// agents. Collapsed by default; the open state persists in localStorage.

import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  CheckCircle2,
  ChevronDown,
  Cpu,
  Globe,
  Pencil,
  Plus,
  Save,
  Settings2,
  Sparkles,
  Wrench,
  X,
} from "lucide-react";
import { API_BASE, ApiError, del, get, ijToken, patch, post, put } from "@/lib/api";
import type { DynamicAgent, ModelOption } from "@/lib/types";
import {
  Badge,
  ConfirmButton,
  ErrorNote,
  LoaderInline,
  SectionLabel,
  SuccessNote,
} from "@/components/ui";
import AgentFace, {
  EYE_STYLES,
  FACE_COLORS,
  FACE_SHAPES,
  type FaceOverride,
} from "./AgentFace";
import { AgentPortrait } from "./AgentPortrait";
import type { RemoteAgentInfo } from "./identity";

// The disclosure key lives on the PAGE now (v1.185.0) — one owner, one write,
// one read. See the `open` prop on SetupCard for what that replaced.

/** Dynamic-agent rows carry their editable config (GET /agents includes it).
 *  `system_prompt` / `tools` / `effective_tools` / `base_type` all live on
 *  `DynamicAgent` now (lib/types.ts); only the portrait is row-local. */
export type DynamicAgentFull = DynamicAgent & {
  /** v1.171.0 additive: the stored portrait's serve path, or null/absent. */
  avatar?: string | null;
  /** v1.180.0 additive: the chosen face, or null/absent to derive from the
   *  name. Absent on a daemon older than v1.180.0 — which is the derived
   *  face, i.e. exactly what that daemon has always drawn. */
  face?: FaceOverride | null;
};

/** Every stored override, keyed by agent name (GET /agents/faces). `null`
 *  means "not loaded yet"; a LOADED map is authoritative — a name missing from
 *  it has no override, which is how a Reset stops showing a stale face. */
export type FaceMap = Record<string, FaceOverride> | null;

/**
 * The override to draw an agent with.
 *
 * A LOADED map wins outright, including when it has no entry for this name:
 * that is what makes a Reset visible immediately instead of waiting for the
 * agents list to be refetched (the row's own `face` field would still carry
 * the removed override until then, which would show the user a face that is
 * no longer stored). Before the map lands — and on a daemon that has no
 * /agents/faces at all — the row's own field is used, and absent means
 * derived, which is exactly the pre-v1.180.0 behaviour.
 */
export function faceFor(
  faces: FaceMap,
  name: string,
  rowFace?: FaceOverride | null,
): FaceOverride | null {
  if (faces) return faces[name] ?? null;
  return rowFace ?? null;
}

/** <img> can't send the Authorization header — token rides as a query param
 *  (the creative-gallery pattern). `rev` busts the browser cache after an
 *  upload/generate/remove, since the URL itself never changes.
 *  The upload CAP and the file→base64 step moved to `AgentPortrait`
 *  (v1.214.0) along with the controls; this card only renders portraits. */
function avatarSrc(rel: string, rev: number): string {
  const token = ijToken();
  return `${API_BASE}${rel}?v=${rev}${token ? `&token=${encodeURIComponent(token)}` : ""}`;
}

/* ------------------------------------------------------- tools, truthfully --- */

/**
 * The two tool fields and why the card must read BOTH (v1.178.0).
 *
 * MEASURED: this form POSTed `tools: []` hardcoded — there was no tools control
 * at all — and the daemon stored that as a literal empty allowlist, so every
 * agent created here advertised NOTHING to its model and read as a dumb agent
 * rather than an empty roster. The daemon now reads an empty STORED list as
 * "not specified" and resolves it to the base type's roster, and returns both:
 *
 *   `tools`            the stored list, exactly as saved — the field we PATCH
 *   `effective_tools`  what the agent actually holds, inheritance resolved
 *
 * Render `effective_tools`; PATCH `tools`. Rendering the stored list alone says
 * "no tools" about an agent that works (the bug this closes), and PATCHing the
 * effective list back would freeze inheritance into an explicit allowlist on
 * the first save the user made for an entirely unrelated reason.
 *
 * `effective_tools` is ABSENT on a daemon older than v1.178.0 — `null` here
 * means "this daemon does not report it", which is NOT the same as "the roster
 * is empty" and must never be rendered as one.
 */
function effectiveOrNull(agent: DynamicAgentFull): string[] | null {
  return Array.isArray(agent.effective_tools) ? agent.effective_tools : null;
}

/** How the agent's roster is decided right now — drives every label below. */
type ToolOrigin = "explicit" | "inherited" | "unreported";

function toolOrigin(agent: DynamicAgentFull): ToolOrigin {
  if ((agent.tools ?? []).length > 0) return "explicit";
  return effectiveOrNull(agent) ? "inherited" : "unreported";
}

/** "the builder base type" when the daemon named it, a generic phrase when it
 *  did not — `base_type` is optional on the wire and inventing one would be a
 *  small lie in the one place the user is deciding what an agent can do. */
function baseTypePhrase(agent: DynamicAgentFull): string {
  const base = (agent.base_type ?? "").trim();
  return base ? `the ${base} base type` : "its base type";
}

/* ------------------------------------------------------------ face picker --- */

/**
 * One row of face choices — a radiogroup whose FIRST option is "from the
 * name". Module-level on purpose: declared inside `FacePicker` it would be a
 * NEW component type on every keystroke of state, so React would unmount and
 * remount the whole row after each click and throw keyboard focus away.
 */
function FaceRow({
  label,
  agentName,
  options,
  current,
  onChoose,
  render,
  testId,
}: {
  label: string;
  agentName: string;
  options: readonly string[];
  /** The pinned value, or null for "derive this field from the name". */
  current: string | null;
  onChoose: (value: string | null) => void;
  render: (value: string | null) => ReactNode;
  testId: string;
}) {
  return (
    <div data-testid={testId}>
      <div className="mb-1 text-[10px] uppercase tracking-[0.12em] text-zinc-500">
        {label}
      </div>
      <div
        role="radiogroup"
        aria-label={`${label} for ${agentName}`}
        className="flex flex-wrap gap-1"
      >
        {[null, ...options].map((value) => {
          const active = current === value;
          return (
            <button
              key={value ?? "__derived__"}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => onChoose(value)}
              title={
                value === null
                  ? `${label}: from the name “${agentName}”`
                  : `${label}: ${value}`
              }
              // Every control accessibly named — the swatches and mini faces
              // carry no text of their own.
              aria-label={
                value === null ? `${label} from the name` : `${label} ${value}`
              }
              className={`grid h-8 min-w-8 place-items-center rounded-lg border px-1.5 transition-colors ${
                active
                  ? "border-accent/60 bg-accent/[0.10]"
                  : "border-white/[0.07] bg-white/[0.02] hover:border-accent/30"
              }`}
            >
              {render(value)}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * Choose an agent's face: shape, eyes, colour (v1.180.0).
 *
 * The face has been DERIVED from the agent's name since v1.171.0 — pretty, and
 * fixed. This is the control that makes the seed a default instead of a
 * ceiling, and it is deliberately PER FIELD: leaving one on "From the name"
 * keeps it deriving, so picking a colour does not silently freeze the shape as
 * well. That matters because the derived face is the thing that stays
 * consistent everywhere; the fewer fields pinned, the less there is to drift.
 *
 * Nothing is written until Apply. The preview updates instantly (it is the
 * real `AgentFace`, resolved through the same `resolveFace` every surface
 * uses, so what is previewed is exactly what the roster will draw), while the
 * stored record changes once, on purpose — a PUT per swatch click would make a
 * casual browse of the palette into twenty writes.
 *
 * Apply with nothing pinned sends DELETE, not an empty PUT: an override with
 * no fields is not a state the daemon stores, and "reset" is what the user
 * means by it.
 */
export function FacePicker({
  name,
  avatarUrl,
  face,
  onChanged,
}: {
  name: string;
  /** A stored portrait still WINS over any chosen face — shown, and said. */
  avatarUrl?: string;
  face: FaceOverride | null;
  onChanged: () => void;
}) {
  const [draft, setDraft] = useState<FaceOverride>(face ?? {});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  // Re-sync when the daemon's answer arrives (or changes underneath us) so the
  // control always opens on the truth as STORED, never on a stale draft.
  //
  // `name` IS A DEPENDENCY (reviewer, v1.180.0). The built-in strip reuses ONE
  // picker instance for whichever chip is open, so switching from an agent with
  // no override to another with no override leaves `face` at `null` both times
  // — the effect would not re-run and the second agent's picker opened
  // PRE-PINNED with the first agent's unapplied choice, reading "chosen" and
  // one Apply away from writing it to the wrong agent.
  useEffect(() => {
    setDraft(face ?? {});
  }, [face, name]);

  const pinned = Boolean(draft.shape || draft.color || draft.eyes);

  function choose(field: keyof FaceOverride, value: string | null) {
    setOk(null);
    // A previous failure described the previous attempt — keeping it on screen
    // while the user picks something else says the new choice already failed.
    setError(null);
    setDraft((prev) => ({ ...prev, [field]: value }));
  }

  async function apply() {
    setBusy(true);
    setError(null);
    setOk(null);
    try {
      if (pinned) {
        await put(`/agents/${encodeURIComponent(name)}/face`, {
          shape: draft.shape ?? null,
          color: draft.color ?? null,
          eyes: draft.eyes ?? null,
        });
      } else {
        await del(`/agents/${encodeURIComponent(name)}/face`);
      }
      onChanged();
      // TELL THE REST OF THE APP (v1.180.0). `onChanged` refreshes THIS
      // card's map; every other AgentFace in the app reads the shared
      // provider, which would otherwise keep drawing the old face until a
      // reload — a chosen face that appears only where it was chosen is
      // the defect this release exists to fix.
      window.dispatchEvent(new CustomEvent("ij:agent-face-changed"));
      // Set LAST in the handler: this note is what a test (and the user) can
      // safely read as "the write landed" — an earlier signal would be true
      // before the request finished (the v1.177.1 / v1.178.0 lesson).
      setOk(pinned ? "Face saved." : "Back to the face this name draws.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    setError(null);
    setOk(null);
    try {
      await del(`/agents/${encodeURIComponent(name)}/face`);
      setDraft({});
      onChanged();
      window.dispatchEvent(new CustomEvent("ij:agent-face-changed"));
      setOk("Back to the face this name draws.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      data-testid={`face-picker-${name}`}
      className="space-y-2.5 rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5"
    >
      <div className="flex flex-wrap items-center gap-2">
        <AgentFace
          name={name}
          mood="idle"
          size={34}
          face={draft}
          avatarUrl={avatarUrl}
          title={`${name} — the face as chosen`}
        />
        <span className="text-[11px] font-medium text-zinc-300">Face</span>
        <span className="text-[10px] text-zinc-500">
          {pinned ? "chosen" : "drawn from the name"}
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={apply}
            disabled={busy}
            className="btn-accent px-2.5 py-1 text-[11px]"
          >
            {busy ? <LoaderInline label="Saving…" /> : "Apply face"}
          </button>
          <button
            type="button"
            onClick={reset}
            disabled={busy}
            title={`Reset ${name}'s face to the one its name draws`}
            className="rounded-lg border border-white/10 px-2 py-1 text-[11px] text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft disabled:opacity-50"
          >
            Reset
          </button>
        </span>
      </div>

      {avatarUrl && (
        <p className="text-[10px] leading-relaxed text-amber-300/80">
          A stored portrait is shown instead of the drawn face wherever this
          agent appears — remove it to see this one.
        </p>
      )}

      {/* Each option previews the face it would actually produce — the real
          AgentFace, resolved through the same rule the roster uses, so the
          swatch cannot promise a face the app then draws differently. */}
      <FaceRow
        testId={`face-row-shape-${name}`}
        label="Shape"
        agentName={name}
        options={FACE_SHAPES}
        current={draft.shape ?? null}
        onChoose={(v) => choose("shape", v)}
        render={(value) =>
          value === null ? (
            <Sparkles size={12} className="text-zinc-500" aria-hidden />
          ) : (
            <AgentFace
              name={name}
              size={18}
              title=""
              face={{ shape: value, color: draft.color, eyes: draft.eyes }}
            />
          )
        }
      />
      <FaceRow
        testId={`face-row-eyes-${name}`}
        label="Eyes"
        agentName={name}
        options={EYE_STYLES}
        current={draft.eyes ?? null}
        onChoose={(v) => choose("eyes", v)}
        render={(value) =>
          value === null ? (
            <Sparkles size={12} className="text-zinc-500" aria-hidden />
          ) : (
            <AgentFace
              name={name}
              size={18}
              title=""
              face={{ shape: draft.shape, color: draft.color, eyes: value }}
            />
          )
        }
      />
      <FaceRow
        testId={`face-row-color-${name}`}
        label="Colour"
        agentName={name}
        options={FACE_COLORS}
        current={draft.color ?? null}
        onChoose={(v) => choose("color", v)}
        render={(value) =>
          value === null ? (
            <Sparkles size={12} className="text-zinc-500" aria-hidden />
          ) : (
            <span
              aria-hidden
              className="block h-4 w-4 rounded-full"
              style={{ backgroundColor: value }}
            />
          )
        }
      />

      {ok && <SuccessNote>{ok}</SuccessNote>}
      {error && <ErrorNote>{error}</ErrorNote>}
    </div>
  );
}

type RemoteKind = "http-task" | "openai-chat" | "openai-responses";
/** The two OpenAI dialects both carry a model id; they differ only in the
 *  request field (`messages` vs `input`). */
const OPENAI_KINDS: string[] = ["openai-chat", "openai-responses"];

const modelKey = (m: ModelOption) => `${m.provider}|${m.model}`;

/* ------------------------------------------------------------ your agents --- */

function DynamicRow({
  agent,
  face,
  facesSupported,
  onChanged,
  onFaceChanged,
}: {
  agent: DynamicAgentFull;
  /** The stored face override, or null to derive from the name (v1.180.0). */
  face: FaceOverride | null;
  /** False on a daemon with no face routes — the picker is hidden rather than
   *  shown and always failing. The face still renders (derived), which is
   *  exactly what that daemon has always drawn. */
  facesSupported: boolean;
  onChanged: () => void;
  onFaceChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // --- tools (v1.178.0) --------------------------------------------------
  // `mode` is what the user has CHOSEN in this editing session; `toolsDirty`
  // is whether they touched the control at all. Both matter: an untouched
  // picker must send NO `tools` field, so a save made to fix a typo in the
  // description cannot convert an inheriting agent into an explicit allowlist.
  const [toolMode, setToolMode] = useState<"inherit" | "explicit">("inherit");
  const [chosen, setChosen] = useState<string[]>([]);
  const [toolsDirty, setToolsDirty] = useState(false);
  const [toolFilter, setToolFilter] = useState("");
  const [catalog, setCatalog] = useState<string[] | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const catalogFetched = useRef(false);

  const storedTools = useMemo(() => agent.tools ?? [], [agent.tools]);
  const effectiveTools = effectiveOrNull(agent);
  const origin = toolOrigin(agent);
  // What the row DISPLAYS as the agent's roster. `effective_tools` already
  // equals the stored list when one is set, so this is one branch, not two —
  // but on an older daemon (no effective_tools) a non-empty stored list is
  // still honest truth and worth showing.
  const shownTools = effectiveTools ?? (storedTools.length > 0 ? storedTools : null);

  /** The tool registry (GET /tools) — the daemon's own list of what exists.
   *  Fetched lazily, once, the first time a picker is opened: ~60 specs is a
   *  wasteful payload for a card most sessions never expand. */
  async function loadCatalog() {
    if (catalogFetched.current) return;
    catalogFetched.current = true;
    try {
      const r = await get<{ tools?: { name?: string }[] }>("/tools");
      const names = (r?.tools ?? [])
        .map((t) => String(t?.name ?? "").trim())
        .filter(Boolean);
      setCatalog(Array.from(new Set(names)).sort());
    } catch (err) {
      // No catalog is NOT no tools: the picker falls back to the agent's own
      // roster below, so narrowing still works and nothing is claimed absent.
      setCatalogError(err instanceof ApiError ? err.message : String(err));
    }
  }

  /** Everything checkable. The registry's list UNIONed with what this agent
   *  already holds — a tool it holds whose provider has since disconnected
   *  (an MCP pack, a deleted custom tool) would otherwise vanish from the
   *  picker and be silently dropped by the very next save. */
  const pickable = useMemo(() => {
    const set = new Set<string>(catalog ?? []);
    for (const t of effectiveTools ?? []) set.add(t);
    for (const t of storedTools) set.add(t);
    return Array.from(set).sort();
  }, [catalog, effectiveTools, storedTools]);

  const visibleTools = useMemo(() => {
    const q = toolFilter.trim().toLowerCase();
    return q ? pickable.filter((n) => n.toLowerCase().includes(q)) : pickable;
  }, [pickable, toolFilter]);

  function chooseExplicit() {
    // Start from what the agent actually holds, so "narrow it" is unchecking
    // rather than rebuilding the roster from memory. This is deliberately
    // dirty-on-click: pressing a button named "Choose specific tools" IS the
    // decision to stop inheriting, and the note under the list says so.
    setChosen(storedTools.length > 0 ? [...storedTools] : [...(effectiveTools ?? [])]);
    setToolMode("explicit");
    setToolsDirty(true);
    void loadCatalog();
  }

  function backToInherited() {
    setToolMode("inherit");
    setToolsDirty(true); // save will PATCH `tools: []` — the "not specified" value
  }

  function toggleTool(name: string) {
    setChosen((prev) =>
      prev.includes(name) ? prev.filter((t) => t !== name) : [...prev, name],
    );
    setToolsDirty(true);
  }
  // Portrait cache-buster (v1.171.0). `rev` ONLY busts the <img> cache —
  // whether a portrait EXISTS always comes from the daemon via agent.avatar,
  // so a failed write can never leave the row pretending one is stored. The
  // controls themselves moved to `AgentPortrait` (v1.214.0), which every kind
  // of agent now shares; this row keeps `rev` because the FACE PICKER below
  // renders the portrait too, and it has to stop showing the old bytes the
  // moment a new picture lands.
  const [rev, setRev] = useState(0);
  const avatarUrl = agent.avatar ? avatarSrc(agent.avatar, rev) : undefined;

  /** A portrait write landed: bust this row's cache AND refetch the list, so
   *  `agent.avatar` (presence) and the rendered bytes agree. */
  function portraitChanged() {
    setRev((v) => v + 1);
    onChanged();
  }

  function startEdit() {
    setPrompt(agent.system_prompt ?? "");
    setDescription(agent.description ?? "");
    setError(null);
    // Open on the truth as stored, and UNTOUCHED — see `toolsDirty`.
    setToolMode(storedTools.length > 0 ? "explicit" : "inherit");
    setChosen([...storedTools]);
    setToolsDirty(false);
    setToolFilter("");
    setEditing(true);
    if (storedTools.length > 0) void loadCatalog();
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      // An empty prompt keeps the current one (PATCH only changes sent fields).
      const body: Record<string, unknown> = {};
      if (prompt.trim()) body.system_prompt = prompt.trim();
      if (description.trim() !== (agent.description ?? "").trim()) {
        body.description = description.trim();
      }
      // TOOLS ARE SENT ONLY WHEN THE USER TOUCHED THE PICKER (v1.178.0), and
      // what is sent is the STORED shape, never `effective_tools`:
      //   explicit -> the chosen allowlist verbatim
      //   inherit  -> [] , the daemon's "not specified" -> base type's roster
      // An untouched picker sends nothing at all, so an edit to the persona or
      // the description leaves inheritance exactly as it was. Writing the
      // effective roster here instead would look identical in the UI and
      // silently pin the agent to today's tool list forever.
      if (toolsDirty) body.tools = toolMode === "explicit" ? [...chosen] : [];
      await patch(`/agents/${encodeURIComponent(agent.name)}`, body);
      setEditing(false);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    try {
      await del(`/agents/${encodeURIComponent(agent.name)}`);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <li className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2">
      <div className="flex items-center gap-2">
        <AgentFace
          name={agent.name}
          mood="idle"
          size={20}
          avatarUrl={avatarUrl}
          face={face}
        />
        <span className="min-w-0 truncate text-[13px] font-medium text-zinc-100">
          {agent.name}
        </span>
        {agent.model && (
          <span className="inline-flex shrink-0 items-center gap-1 rounded-md border border-accent/30 bg-accent/[0.08] px-1.5 py-0.5 font-mono text-[10px] text-accent-soft">
            <Cpu size={10} />
            {agent.provider ? `${agent.provider} · ${agent.model}` : agent.model}
          </span>
        )}
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          {!editing && (
            <button
              type="button"
              onClick={startEdit}
              title={`Edit the persona of "${agent.name}"`}
              className="grid h-6 w-6 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-accent-soft"
            >
              <Pencil size={12} />
            </button>
          )}
          <ConfirmButton onConfirm={remove} label="Delete" title={`Delete agent "${agent.name}"`} />
        </span>
      </div>
      {agent.description && !editing && (
        <p className="mt-0.5 truncate pl-7 text-[11px] text-zinc-500">{agent.description}</p>
      )}

      {/* WHAT THIS AGENT ACTUALLY HOLDS (v1.178.0), always visible — the row
          used to say nothing at all about tools, which is how every agent
          created here shipped with an empty allowlist unnoticed. */}
      <div data-testid={`tools-summary-${agent.name}`} className="mt-1 pl-7">
        <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
          <Wrench size={10} className="shrink-0 text-amber-300/80" aria-hidden />
          {origin === "explicit" && (
            <span className="text-[10px] text-zinc-400">
              Tools · {storedTools.length} chosen — an explicit set
            </span>
          )}
          {origin === "inherited" && (
            <span className="text-[10px] text-zinc-400">
              Tools · {(effectiveTools ?? []).length} inherited from{" "}
              {baseTypePhrase(agent)}
            </span>
          )}
          {/* The older-daemon degrade: it does not report the resolved roster,
              so say the rule (it inherits) and show NO list. An empty chip row
              here would read as "this agent can do nothing", which is exactly
              the lie this feature exists to stop telling. */}
          {origin === "unreported" && (
            <span className="text-[10px] text-zinc-500">
              Tools · inherits {baseTypePhrase(agent)} — this daemon doesn’t
              report which ones
            </span>
          )}
        </div>
        {shownTools && shownTools.length > 0 && (
          <div className="mt-1 flex max-h-16 flex-wrap gap-1 overflow-y-auto">
            {shownTools.map((t) => (
              <span
                key={t}
                className="rounded border border-white/[0.07] bg-white/[0.03] px-1 py-px font-mono text-[10px] text-zinc-400"
              >
                {t}
              </span>
            ))}
          </div>
        )}
      </div>

      {editing && (
        <div className="mt-2 space-y-2 border-t hairline pt-2">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={3}
            placeholder={
              agent.system_prompt
                ? "Edit the persona prompt…"
                : "Leave blank to keep the current prompt…"
            }
            aria-label={`Persona prompt for ${agent.name}`}
            className="field resize-y text-xs"
          />
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="short description (optional)"
            aria-label={`Description for ${agent.name}`}
            className="field text-xs"
          />

          {/* THE TOOLS PICKER. Two states, one decision: inherit the base
              type's roster (the default, stored as an empty list) or pin an
              explicit allowlist. Only what is touched here is ever sent. */}
          <div
            data-testid={`tools-editor-${agent.name}`}
            className="space-y-2 rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5"
          >
            <div className="flex flex-wrap items-center gap-2">
              <Wrench size={12} className="shrink-0 text-amber-300" aria-hidden />
              <span className="text-[11px] font-medium text-zinc-300">Tools</span>
              <span className="ml-auto">
                {toolMode === "inherit" ? (
                  <button
                    type="button"
                    onClick={chooseExplicit}
                    className="inline-flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft"
                  >
                    Choose specific tools
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={backToInherited}
                    className="inline-flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft"
                  >
                    Use inherited tools
                  </button>
                )}
              </span>
            </div>

            {toolMode === "inherit" ? (
              <p className="text-[10px] leading-relaxed text-zinc-500">
                {effectiveTools
                  ? `Inherits ${baseTypePhrase(agent)} — ${effectiveTools.length} tools today, and it follows that roster as it changes.`
                  : `Inherits ${baseTypePhrase(agent)}. This daemon doesn’t report the resolved list.`}
              </p>
            ) : (
              <>
                <input
                  value={toolFilter}
                  onChange={(e) => setToolFilter(e.target.value)}
                  placeholder="filter tools…"
                  aria-label={`Filter tools for ${agent.name}`}
                  className="field text-xs"
                />
                <div className="max-h-40 space-y-0.5 overflow-y-auto pr-1">
                  {visibleTools.map((t) => (
                    <label
                      key={t}
                      className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 hover:bg-white/[0.04]"
                    >
                      <input
                        type="checkbox"
                        checked={chosen.includes(t)}
                        onChange={() => toggleTool(t)}
                        className="accent-cyan-400"
                      />
                      <span className="min-w-0 truncate font-mono text-[11px] text-zinc-300">
                        {t}
                      </span>
                    </label>
                  ))}
                  {visibleTools.length === 0 && (
                    <p className="px-1 py-1 text-[10px] text-zinc-500">
                      no tool matches “{toolFilter.trim()}”
                    </p>
                  )}
                </div>
                <p className="text-[10px] leading-relaxed text-amber-300/80">
                  {chosen.length} chosen. An explicit set stops this agent
                  picking up later changes to {baseTypePhrase(agent)}
                  {chosen.length === 0
                    ? " — and with nothing checked, saving clears it back to inherited."
                    : "."}
                </p>
                {catalogError && (
                  <p className="text-[10px] text-zinc-500">
                    Couldn’t load the full tool list ({catalogError}) — showing
                    what this agent already holds.
                  </p>
                )}
              </>
            )}
          </div>

          {/* PORTRAIT (v1.171.0, shared since v1.214.0). The controls used to
              live inline here, which is the only reason a portrait was
              something an agent the user CREATED could have — the daemon's
              storage was never kind-specific. `AgentPortrait` is that row
              taking a NAME, so built-in and remote agents reach the same
              implementation, and an upload now goes through the square
              cropper on its way. */}
          <AgentPortrait
            name={agent.name}
            avatar={agent.avatar}
            face={face}
            onChanged={portraitChanged}
          />

          {/* THE FACE PICKER (v1.180.0) — shape, eyes, colour, live preview,
              reset. It writes on its own Apply, independent of this form's
              Save, because a face is not part of the persona edit and should
              not need one to stick. */}
          {facesSupported && (
            <FacePicker
              name={agent.name}
              avatarUrl={avatarUrl}
              face={face}
              onChanged={onFaceChanged}
            />
          )}
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={save}
              disabled={busy}
              className="btn-accent py-1 text-xs"
            >
              {busy ? <LoaderInline label="Saving…" /> : <><Save size={13} /> Save</>}
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="btn-ghost py-1 text-xs"
            >
              <X size={13} /> Cancel
            </button>
          </div>
        </div>
      )}
      {error && <div className="mt-2"><ErrorNote>{error}</ErrorNote></div>}
    </li>
  );
}

export function YourAgentsSection({
  dynamic,
  models,
  faces,
  facesSupported,
  onChanged,
  onFaceChanged,
}: {
  dynamic: DynamicAgentFull[];
  models: ModelOption[];
  /** Loaded override map, or null while it is still loading. A LOADED map is
   *  authoritative (see FaceMap); until it lands, the row's own `face` field
   *  from GET /agents is used so a face never flickers to derived. */
  faces: FaceMap;
  facesSupported: boolean;
  onChanged: () => void;
  onFaceChanged: () => void;
}) {
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [description, setDescription] = useState("");
  const [model, setModel] = useState(""); // "provider|model"
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !prompt.trim()) return;
    setBusy(true);
    setError(null);
    setOk(null);
    const [provider, modelName] = model ? model.split("|") : ["", ""];
    try {
      await post("/agents", {
        name: name.trim(),
        system_prompt: prompt.trim(),
        // `[]` is "not specified", NOT "no tools" — since v1.178.0 the daemon
        // resolves an empty stored list to the base type's roster, so a new
        // agent starts able to work and is narrowed afterwards with the
        // picker on its row. This line used to mean the opposite: it stored a
        // literal empty allowlist and every agent created here held nothing.
        tools: [],
        description: description.trim(),
        provider,
        model: modelName,
      });
      setOk(
        `"${name.trim()}" is ready — it inherits its base type's tools; open it to narrow them.`,
      );
      setName("");
      setPrompt("");
      setDescription("");
      setModel("");
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <Sparkles size={13} className="text-violet-300" />
        <SectionLabel>Your agents{dynamic.length ? ` · ${dynamic.length}` : ""}</SectionLabel>
      </div>
      <p className="text-[11px] leading-relaxed text-zinc-500">
        An agent of your own is a persona prompt plus an optional preferred
        model — it carries both into every thread it joins.
      </p>

      {dynamic.length > 0 && (
        <ul className="space-y-2">
          {dynamic.map((a) => (
            <DynamicRow
              key={a.name}
              agent={a}
              face={faceFor(faces, a.name, a.face)}
              facesSupported={facesSupported}
              onChanged={onChanged}
              onFaceChanged={onFaceChanged}
            />
          ))}
        </ul>
      )}

      <form
        onSubmit={create}
        className="space-y-2.5 rounded-xl border border-white/[0.05] bg-white/[0.02] p-3"
      >
        <SectionLabel>Create an agent</SectionLabel>
        <div className="grid grid-cols-2 gap-2">
          {/* The deterministic face previews LIVE as the name is typed — the
              same seed every other surface uses, so what you see here is
              exactly the face this agent will wear everywhere (v1.171.0). */}
          <div className="flex min-w-0 items-center gap-2">
            <AgentFace
              name={name.trim() || "?"}
              mood="idle"
              size={24}
              title={
                name.trim()
                  ? `${name.trim()} — the face this name draws`
                  : "the face appears as you type a name"
              }
            />
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="name — e.g. skeptic"
              aria-label="Agent name"
              className="field min-w-0 flex-1 text-xs"
            />
          </div>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            aria-label="Preferred model"
            className="field text-xs"
          >
            <option value="">Default model</option>
            {models.map((m) => (
              <option key={modelKey(m)} value={modelKey(m)}>
                {m.provider} · {m.model}
              </option>
            ))}
          </select>
        </div>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={2}
          placeholder="Persona — “You are a security-minded skeptic who challenges every assumption…”"
          aria-label="Persona prompt"
          className="field resize-y text-xs"
        />
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="short description (optional)"
          aria-label="Description"
          className="field text-xs"
        />
        <button
          type="submit"
          disabled={busy || !name.trim() || !prompt.trim()}
          className="btn-accent w-full py-1.5 text-xs"
        >
          {busy ? <LoaderInline label="Creating…" /> : <><Plus size={13} /> Create agent</>}
        </button>
        {ok && <SuccessNote>{ok}</SuccessNote>}
        {error && <ErrorNote>{error}</ErrorNote>}
      </form>
    </section>
  );
}

/* ---------------------------------------------------------- remote agents --- */

/**
 * Edit an already-connected remote agent (v1.164.0).
 *
 * Exists because the row only offered Test and Delete, so one mistyped
 * character in a base URL meant re-entering the whole record — including a
 * bearer token the user may not still have.
 *
 * THE SECRET BOX IS EMPTY AND STAYS EMPTY. The token is stored encrypted and
 * never returned, so it CANNOT be prefilled; leaving the field alone keeps
 * whatever is stored, and removing a credential takes the explicit checkbox.
 * (The backend enforces the same three-way split — an empty box is "I didn't
 * type one", never "delete it".)
 *
 * The NAME is shown read-only: panels and threads refer to a remote by name, so
 * renaming here would orphan those references without saying so.
 */
function RemoteEditForm({
  agent,
  onDone,
  onCancel,
}: {
  agent: RemoteAgentInfo;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [baseUrl, setBaseUrl] = useState(agent.base_url);
  const [kind, setKind] = useState<RemoteKind>((agent.kind as RemoteKind) || "http-task");
  const [model, setModel] = useState(agent.model || "");
  const [secret, setSecret] = useState("");
  const [clearToken, setClearToken] = useState(false);
  const [enabled, setEnabled] = useState(agent.enabled !== false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!baseUrl.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const body: Record<string, unknown> = {
        base_url: baseUrl.trim(),
        kind,
        model: OPENAI_KINDS.includes(kind) ? model.trim() : "",
        enabled,
      };
      // Only ever SEND a token when one was typed — an absent field is what
      // tells the daemon to keep the stored credential.
      if (clearToken) body.clear_token = true;
      else if (secret.trim()) body.token = secret.trim();
      await patch(`/agents/remote/${encodeURIComponent(agent.name)}`, body);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={save}
      data-testid={`remote-edit-${agent.name}`}
      className="mt-2 space-y-2.5 rounded-lg border border-accent/20 bg-accent/[0.03] p-2.5"
    >
      <div className="grid grid-cols-2 gap-2">
        <div className="flex items-center gap-1.5 rounded-lg border border-white/[0.06] bg-white/[0.02] px-2 py-1.5 text-xs text-zinc-500">
          <span className="truncate" title="A remote's name is how panels and threads refer to it — delete and re-add to rename.">
            {agent.name}
          </span>
        </div>
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value as RemoteKind)}
          aria-label="Remote kind"
          className="field text-xs"
        >
          <option value="http-task">http-task (task API)</option>
          <option value="openai-chat">openai-chat (chat/completions)</option>
          <option value="openai-responses">openai-responses (Responses API)</option>
        </select>
      </div>
      <input
        value={baseUrl}
        onChange={(e) => setBaseUrl(e.target.value)}
        placeholder="base URL"
        aria-label="Base URL"
        autoComplete="off"
        className="field font-mono text-xs"
      />
      {OPENAI_KINDS.includes(kind) && (
        <input
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="model — gpt-4o-mini / llama3"
          aria-label="Model"
          autoComplete="off"
          className="field font-mono text-xs"
        />
      )}
      <input
        type="password"
        value={secret}
        onChange={(e) => setSecret(e.target.value)}
        disabled={clearToken}
        placeholder={
          agent.has_credential
            ? "secret — leave blank to keep the current one"
            : "secret (optional)"
        }
        aria-label="Bearer secret"
        autoComplete="off"
        className="field font-mono text-xs disabled:opacity-40"
      />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
        <label className="inline-flex items-center gap-1.5 text-[11px] text-zinc-400">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="accent-cyan-400"
          />
          enabled
        </label>
        {agent.has_credential && (
          <label className="inline-flex items-center gap-1.5 text-[11px] text-zinc-400">
            <input
              type="checkbox"
              checked={clearToken}
              onChange={(e) => setClearToken(e.target.checked)}
              className="accent-rose-400"
            />
            remove the stored secret
          </label>
        )}
        <span className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-white/10 px-2 py-1 text-[11px] text-zinc-400 transition-colors hover:text-zinc-200"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={busy || !baseUrl.trim()}
            className="btn-accent px-2.5 py-1 text-[11px]"
          >
            {busy ? <LoaderInline label="Saving…" /> : "Save"}
          </button>
        </span>
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}
    </form>
  );
}

function RemoteRow({
  agent,
  face,
  facesSupported,
  onChanged,
  onFaceChanged,
}: {
  agent: RemoteAgentInfo;
  face: FaceOverride | null;
  facesSupported: boolean;
  onChanged: () => void;
  onFaceChanged: () => void;
}) {
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; detail: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);

  async function runTest() {
    setTesting(true);
    setError(null);
    setTest(null);
    try {
      const r = await post<{ ok?: boolean; detail?: string }>(
        `/agents/remote/${encodeURIComponent(agent.name)}/test`,
      );
      setTest({
        ok: r.ok !== false,
        detail: r.detail ?? (r.ok !== false ? "Reachable." : "Unreachable."),
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setTesting(false);
    }
  }

  async function remove() {
    try {
      await del(`/agents/remote/${encodeURIComponent(agent.name)}`);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <li className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <AgentFace name={agent.name} mood="idle" size={20} face={face} />
        <span className="min-w-0 truncate text-[13px] font-medium text-zinc-100">
          {agent.name}
        </span>
        <Badge value={agent.kind} tone="cyan" />
        {agent.model && (
          <span className="inline-flex shrink-0 items-center gap-1 rounded-md border border-accent/30 bg-accent/[0.08] px-1.5 py-0.5 font-mono text-[10px] text-accent-soft">
            <Cpu size={10} /> {agent.model}
          </span>
        )}
        {agent.enabled === false && (
          <span className="rounded-md border border-zinc-500/25 bg-zinc-500/10 px-1.5 py-0.5 text-[10px] font-medium text-zinc-400">
            disabled
          </span>
        )}
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            onClick={runTest}
            disabled={testing}
            title={`Check that "${agent.name}" is reachable`}
            className="inline-flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft disabled:opacity-50"
          >
            {testing ? <LoaderInline label="…" /> : <><CheckCircle2 size={12} /> Test</>}
          </button>
          <button
            type="button"
            onClick={() => setEditing((v) => !v)}
            aria-expanded={editing}
            title={`Fix "${agent.name}" without re-entering it`}
            className="inline-flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft"
          >
            <Pencil size={12} /> Edit
          </button>
          <ConfirmButton
            onConfirm={remove}
            label="Delete"
            title={`Remove remote agent "${agent.name}"`}
          />
        </span>
      </div>
      {editing && (
        <>
          <RemoteEditForm
            agent={agent}
            onDone={() => {
              setEditing(false);
              setTest(null); // a stale "Reachable." would describe the OLD config
              onChanged();
            }}
            onCancel={() => setEditing(false)}
          />
          {/* A remote wears a face on every panel too — same picker, same
              store, keyed by the same name (v1.180.0). */}
          {facesSupported && (
            <div className="mt-2">
              <FacePicker name={agent.name} face={face} onChanged={onFaceChanged} />
            </div>
          )}
        </>
      )}
      <div className="mt-1 overflow-x-auto pl-7">
        <code className="whitespace-pre font-mono text-[10px] text-zinc-500">
          {agent.base_url}
        </code>
      </div>
      {test && (
        <p className={`mt-1.5 pl-7 text-[11px] ${test.ok ? "text-emerald-300" : "text-rose-300"}`}>
          {test.detail}
        </p>
      )}
      {error && <div className="mt-2"><ErrorNote>{error}</ErrorNote></div>}
    </li>
  );
}

export function RemoteAgentsSection({
  remotes,
  faces,
  facesSupported,
  onChanged,
  onFaceChanged,
}: {
  remotes: RemoteAgentInfo[];
  faces: FaceMap;
  facesSupported: boolean;
  onChanged: () => void;
  onFaceChanged: () => void;
}) {
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [kind, setKind] = useState<RemoteKind>("http-task");
  const [model, setModel] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  async function connect(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !baseUrl.trim()) return;
    setBusy(true);
    setError(null);
    setOk(null);
    try {
      await post("/agents/remote", {
        name: name.trim(),
        base_url: baseUrl.trim(),
        kind,
        model: OPENAI_KINDS.includes(kind) ? model.trim() : "",
        token: secret.trim(), // stored encrypted in the vault, never returned
        enabled: true,
      });
      setOk(`"${name.trim()}" connected — it can join threads now.`);
      setName("");
      setBaseUrl("");
      setModel("");
      setSecret("");
      setKind("http-task");
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <Globe size={13} className="text-emerald-300" />
        <SectionLabel>Remote agents{remotes.length ? ` · ${remotes.length}` : ""}</SectionLabel>
      </div>
      <p className="text-[11px] leading-relaxed text-zinc-500">
        Reach an agent you run elsewhere — a Hermes on another machine, an
        OpenAI-compatible endpoint. Connect it once and it can sit on any panel.
      </p>

      {remotes.length > 0 && (
        <ul className="space-y-2">
          {remotes.map((r) => (
            <RemoteRow
              key={r.name}
              agent={r}
              face={faceFor(faces, r.name)}
              facesSupported={facesSupported}
              onChanged={onChanged}
              onFaceChanged={onFaceChanged}
            />
          ))}
        </ul>
      )}

      <form
        onSubmit={connect}
        className="space-y-2.5 rounded-xl border border-white/[0.05] bg-white/[0.02] p-3"
      >
        <SectionLabel>Connect a remote agent</SectionLabel>
        <div className="grid grid-cols-2 gap-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="name — e.g. my-hermes"
            aria-label="Remote agent name"
            className="field text-xs"
          />
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as RemoteKind)}
            aria-label="Remote kind"
            className="field text-xs"
          >
            <option value="http-task">http-task (task API)</option>
            <option value="openai-chat">openai-chat (chat/completions)</option>
            <option value="openai-responses">openai-responses (Responses API)</option>
          </select>
        </div>
        <input
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="base URL — http://192.168.1.20:8080"
          aria-label="Base URL"
          autoComplete="off"
          className="field font-mono text-xs"
        />
        <div className="grid grid-cols-2 gap-2">
          <input
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            placeholder="secret (optional)"
            aria-label="Bearer secret"
            autoComplete="off"
            className="field font-mono text-xs"
          />
          {OPENAI_KINDS.includes(kind) ? (
            <input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="model — gpt-4o-mini / llama3"
              aria-label="Model"
              autoComplete="off"
              className="field font-mono text-xs"
            />
          ) : (
            <span className="self-center text-[10px] text-zinc-600">
              secret is stored encrypted, never shown again
            </span>
          )}
        </div>
        <button
          type="submit"
          disabled={busy || !name.trim() || !baseUrl.trim()}
          className="btn-accent w-full py-1.5 text-xs"
        >
          {busy ? <LoaderInline label="Connecting…" /> : <><Plus size={13} /> Connect remote</>}
        </button>
        {ok && <SuccessNote>{ok}</SuccessNote>}
        {error && <ErrorNote>{error}</ErrorNote>}
      </form>
    </section>
  );
}

/* ------------------------------------------------- built-ins, with faces --- */

/**
 * The built-in specialists (v1.180.0: their faces are customizable too).
 *
 * They used to be a row of flat badges. They still are a row — same list, same
 * "always available" label, nothing lost — but each chip now wears the agent's
 * actual face and opens the same picker the dynamic rows use, because "the
 * faces for each agent should be customizable" includes the eight agents the
 * user never created and cannot edit anywhere else.
 *
 * On a daemon with no face routes the chips fall back to the original badges:
 * a control that could only fail is worse than the plain list it replaced.
 */
export function BuiltinFaces({
  builtin,
  faces,
  facesSupported,
  onFaceChanged,
}: {
  builtin: string[];
  faces: FaceMap;
  facesSupported: boolean;
  onFaceChanged: () => void;
}) {
  const [openName, setOpenName] = useState<string | null>(null);
  return (
    <div className="mb-5 space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-400">
          Built-in · always available
        </span>
        {builtin.map((b) =>
          facesSupported ? (
            <button
              key={b}
              type="button"
              onClick={() => setOpenName((cur) => (cur === b ? null : b))}
              aria-expanded={openName === b}
              title={`Customize ${b}'s face`}
              className={`inline-flex items-center gap-1.5 rounded-md border px-1.5 py-0.5 text-[11px] transition-colors ${
                openName === b
                  ? "border-accent/60 bg-accent/[0.10] text-accent-soft"
                  : "border-accent/30 bg-accent/[0.06] text-accent-soft hover:border-accent/50"
              }`}
            >
              {/* Decorative: the chip's own text is the name (title="" +
                  aria-hidden, the v1.171.0 rule). */}
              <AgentFace name={b} size={14} title="" face={faceFor(faces, b)} />
              {b}
            </button>
          ) : (
            <Badge key={b} value={b} tone="cyan" />
          ),
        )}
      </div>
      {openName && (
        // `key` so switching chips MOUNTS a fresh picker rather than handing
        // the next agent the previous one's draft (reviewer, v1.180.0). The
        // effect above also keys off `name`; this is the belt to that braces —
        // a draft is per-agent and must never outlive the agent it was made for.
        <FacePicker
          key={openName}
          name={openName}
          face={faceFor(faces, openName)}
          onChanged={onFaceChanged}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------- card --- */

export function SetupCard({
  builtin,
  dynamic,
  remotes,
  models,
  onAgentsChanged,
  onRemotesChanged,
  open,
  onOpenChange,
}: {
  builtin: string[];
  dynamic: DynamicAgentFull[];
  remotes: RemoteAgentInfo[];
  models: ModelOption[];
  onAgentsChanged: () => void;
  onRemotesChanged: () => void;
  /**
   * Whether the card's body is disclosed. OWNED BY THE PAGE (v1.185.0).
   *
   * This used to be internal state hydrated from `OPEN_KEY`, while the page
   * WROTE that same key before mounting the card — two independent states over
   * one string. They agreed, but only because of the ordering: the page had to
   * write storage first so the card's hydration would come up open (see the
   * page's SETUP_OPEN_KEY comment, which documents that handshake as a race
   * fix). A handshake is not a source of truth; it is a thing that holds until
   * somebody reorders two lines. Now the page holds the value and the card
   * renders it, so there is nothing left to keep in step.
   */
  open: boolean;
  /** The card's own chevron. Visibility stays the page's call, not this one's. */
  onOpenChange: (open: boolean) => void;
}) {

  // --- stored faces (v1.180.0) -------------------------------------------
  // ONE fetch for every agent's override instead of one per row: this card
  // draws a face for every built-in, dynamic and remote agent, and it is
  // collapsed by default, so the load waits until it is opened.
  const [faces, setFaces] = useState<FaceMap>(null);
  const [facesSupported, setFacesSupported] = useState(true);

  const loadFaces = useCallback(async () => {
    try {
      const r = await get<{ faces?: Record<string, FaceOverride> }>("/agents/faces");
      setFaces(r?.faces ?? {});
      setFacesSupported(true);
    } catch (err) {
      // A daemon older than v1.180.0 has no such route. DEGRADE: every face
      // derives from its name (what that daemon has always drawn) and the
      // pickers hide themselves — never an error, never a broken control. An
      // empty map is the TRUTH there: that daemon stores no overrides at all.
      if (err instanceof ApiError && err.status === 404) {
        setFacesSupported(false);
        setFaces({});
        return;
      }
      // ANY OTHER FAILURE TELLS US NOTHING about what is stored (reviewer,
      // v1.180.0). A loaded map is AUTHORITATIVE, so writing `{}` here would
      // claim "no agent has an override" on the strength of a timeout: every
      // customized face would silently redraw as derived, and the refetch that
      // follows an Apply would reset the open picker to "drawn from the name"
      // one line under "Face saved." Keep the last confirmed answer (or "not
      // loaded", which falls back to each row's own `face` field from
      // GET /agents) and let the next load correct it.
    }
  }, []);

  useEffect(() => {
    if (open) void loadFaces();
  }, [open, loadFaces]);

  function toggle() {
    onOpenChange(!open);
  }

  return (
    <section className="card-surface">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-5 py-3.5 text-left"
      >
        <Settings2 size={15} className="shrink-0 text-accent-soft/80" />
        <span className="min-w-0 flex-1">
          <span className="block text-[13px] font-semibold tracking-wide text-zinc-200">
            Set up agents
          </span>
          <span className="block truncate text-[11px] text-zinc-500">
            Create agents of your own and connect remote ones — all of them can
            sit on a thread panel.
          </span>
        </span>
        <span className="hidden shrink-0 text-[11px] text-zinc-500 sm:block">
          {dynamic.length} yours · {remotes.length} remote
        </span>
        <ChevronDown
          size={16}
          className={`shrink-0 text-zinc-500 transition-transform duration-200 ${
            open ? "rotate-180" : ""
          }`}
        />
      </button>

      {open && (
        <div className="border-t hairline p-5">
          {builtin.length > 0 && (
            <BuiltinFaces
              builtin={builtin}
              faces={faces}
              facesSupported={facesSupported}
              onFaceChanged={loadFaces}
            />
          )}
          <div className="grid gap-8 lg:grid-cols-2">
            <YourAgentsSection
              dynamic={dynamic}
              models={models}
              faces={faces}
              facesSupported={facesSupported}
              onChanged={onAgentsChanged}
              onFaceChanged={loadFaces}
            />
            <RemoteAgentsSection
              remotes={remotes}
              faces={faces}
              facesSupported={facesSupported}
              onChanged={onRemotesChanged}
              onFaceChanged={loadFaces}
            />
          </div>
        </div>
      )}
    </section>
  );
}
