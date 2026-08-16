"use client";

// "Set up agents" — the management surface, collapsed into one card so the
// threads stay the star. Two columns on lg: your (dynamic) agents and remote
// agents. Collapsed by default; the open state persists in localStorage.

import { useEffect, useMemo, useRef, useState } from "react";
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
  Trash2,
  Upload,
  Wand2,
  Wrench,
  X,
} from "lucide-react";
import { API_BASE, ApiError, del, get, ijToken, patch, post } from "@/lib/api";
import type { DynamicAgent, ModelOption } from "@/lib/types";
import {
  Badge,
  ConfirmButton,
  ErrorNote,
  LoaderInline,
  SectionLabel,
  SuccessNote,
} from "@/components/ui";
import AgentFace from "./AgentFace";
import type { RemoteAgentInfo } from "./identity";

const OPEN_KEY = "ij_agents_setup_open";

/** Dynamic-agent rows carry their editable config (GET /agents includes it).
 *  `system_prompt` / `tools` / `effective_tools` / `base_type` all live on
 *  `DynamicAgent` now (lib/types.ts); only the portrait is row-local. */
export type DynamicAgentFull = DynamicAgent & {
  /** v1.171.0 additive: the stored portrait's serve path, or null/absent. */
  avatar?: string | null;
};

/** Portrait upload cap — mirrors the daemon's 2MB decoded limit so an
 *  oversized pick fails HERE with a plain line instead of a 413 round-trip. */
const AVATAR_MAX_BYTES = 2 * 1024 * 1024;

/** <img> can't send the Authorization header — token rides as a query param
 *  (the creative-gallery pattern). `rev` busts the browser cache after an
 *  upload/generate/remove, since the URL itself never changes. */
function avatarSrc(rel: string, rev: number): string {
  const token = ijToken();
  return `${API_BASE}${rel}?v=${rev}${token ? `&token=${encodeURIComponent(token)}` : ""}`;
}

/** File → bare base64 (the data:-URL prefix stripped). */
function fileToB64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const s = String(reader.result || "");
      resolve(s.slice(s.indexOf(",") + 1));
    };
    reader.onerror = () => reject(reader.error ?? new Error("could not read the file"));
    reader.readAsDataURL(file);
  });
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

type RemoteKind = "http-task" | "openai-chat" | "openai-responses";
/** The two OpenAI dialects both carry a model id; they differ only in the
 *  request field (`messages` vs `input`). */
const OPENAI_KINDS: string[] = ["openai-chat", "openai-responses"];

const modelKey = (m: ModelOption) => `${m.provider}|${m.model}`;

/* ------------------------------------------------------------ your agents --- */

function DynamicRow({ agent, onChanged }: { agent: DynamicAgentFull; onChanged: () => void }) {
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
  // Portrait state (v1.171.0). `rev` only busts the <img> cache — whether a
  // portrait EXISTS always comes from the daemon via agent.avatar, so a
  // failed write can never leave the row pretending one is stored.
  const [rev, setRev] = useState(0);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const [avatarError, setAvatarError] = useState<string | null>(null);
  const avatarUrl = agent.avatar ? avatarSrc(agent.avatar, rev) : undefined;

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

  async function uploadAvatar(file: File) {
    if (file.size > AVATAR_MAX_BYTES) {
      setAvatarError("portrait too large — 2 MB max");
      return;
    }
    setAvatarBusy(true);
    setAvatarError(null);
    try {
      const b64 = await fileToB64(file);
      await post(`/agents/${encodeURIComponent(agent.name)}/avatar`, { image_b64: b64 });
      setRev((v) => v + 1);
      onChanged();
    } catch (err) {
      setAvatarError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setAvatarBusy(false);
    }
  }

  async function generateAvatar() {
    setAvatarBusy(true);
    setAvatarError(null);
    try {
      await post(`/agents/${encodeURIComponent(agent.name)}/avatar`, { generate: true });
      setRev((v) => v + 1);
      onChanged();
    } catch (err) {
      // The daemon's honest 409 ("no image model is connected — …") lands
      // here as plain text — shown as-is, never swapped for a placeholder.
      setAvatarError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setAvatarBusy(false);
    }
  }

  async function removeAvatar() {
    setAvatarBusy(true);
    setAvatarError(null);
    try {
      await del(`/agents/${encodeURIComponent(agent.name)}/avatar`);
      setRev((v) => v + 1);
      onChanged();
    } catch (err) {
      setAvatarError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setAvatarBusy(false);
    }
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
        <AgentFace name={agent.name} mood="idle" size={20} avatarUrl={avatarUrl} />
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

          {/* Portrait row (v1.171.0): the current face (or stored portrait),
              Upload / Generate / Remove. Generate goes through the daemon's
              real image path; with no image model configured the daemon's
              honest 409 renders below as plain text — never a placeholder. */}
          <div
            data-testid={`avatar-row-${agent.name}`}
            className="flex flex-wrap items-center gap-2"
          >
            <AgentFace name={agent.name} mood="idle" size={28} avatarUrl={avatarUrl} />
            <span className="text-[11px] text-zinc-500">Portrait</span>
            <span className="ml-auto flex items-center gap-1.5">
              <label
                className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft"
                title={`Upload a portrait for "${agent.name}" (PNG/JPEG/WebP, 2 MB max)`}
              >
                <Upload size={12} /> Upload
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  className="hidden"
                  aria-label={`Upload a portrait for ${agent.name}`}
                  disabled={avatarBusy}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void uploadAvatar(f);
                    e.target.value = "";
                  }}
                />
              </label>
              <button
                type="button"
                onClick={generateAvatar}
                disabled={avatarBusy}
                title={`Generate a portrait for "${agent.name}" with the connected image model`}
                className="inline-flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft disabled:opacity-50"
              >
                {avatarBusy ? <LoaderInline label="…" /> : <><Wand2 size={12} /> Generate</>}
              </button>
              {agent.avatar && (
                <button
                  type="button"
                  onClick={removeAvatar}
                  disabled={avatarBusy}
                  title={`Remove the stored portrait of "${agent.name}" (back to the drawn face)`}
                  className="inline-flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-[11px] font-medium text-zinc-400 transition-colors hover:border-rose-400/40 hover:text-rose-300 disabled:opacity-50"
                >
                  <Trash2 size={12} /> Remove
                </button>
              )}
            </span>
          </div>
          {avatarError && <ErrorNote>{avatarError}</ErrorNote>}
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

function YourAgentsSection({
  dynamic,
  models,
  onChanged,
}: {
  dynamic: DynamicAgentFull[];
  models: ModelOption[];
  onChanged: () => void;
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
            <DynamicRow key={a.name} agent={a} onChanged={onChanged} />
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

function RemoteRow({ agent, onChanged }: { agent: RemoteAgentInfo; onChanged: () => void }) {
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
        <AgentFace name={agent.name} mood="idle" size={20} />
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
        <RemoteEditForm
          agent={agent}
          onDone={() => {
            setEditing(false);
            setTest(null); // a stale "Reachable." would describe the OLD config
            onChanged();
          }}
          onCancel={() => setEditing(false)}
        />
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

function RemoteAgentsSection({
  remotes,
  onChanged,
}: {
  remotes: RemoteAgentInfo[];
  onChanged: () => void;
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
            <RemoteRow key={r.name} agent={r} onChanged={onChanged} />
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

/* ------------------------------------------------------------------- card --- */

export function SetupCard({
  builtin,
  dynamic,
  remotes,
  models,
  onAgentsChanged,
  onRemotesChanged,
}: {
  builtin: string[];
  dynamic: DynamicAgentFull[];
  remotes: RemoteAgentInfo[];
  models: ModelOption[];
  onAgentsChanged: () => void;
  onRemotesChanged: () => void;
}) {
  // Collapsed by default; hydrated from localStorage after mount so the
  // server-rendered markup always matches the first client render.
  const [open, setOpen] = useState(false);
  useEffect(() => {
    try {
      setOpen(localStorage.getItem(OPEN_KEY) === "1");
    } catch {
      /* storage unavailable — stays collapsed */
    }
  }, []);

  function toggle() {
    const next = !open;
    setOpen(next);
    try {
      localStorage.setItem(OPEN_KEY, next ? "1" : "0");
    } catch {
      /* persistence is best-effort */
    }
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
            <div className="mb-5 flex flex-wrap items-center gap-1.5">
              <span className="mr-1 text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-400">
                Built-in · always available
              </span>
              {builtin.map((b) => (
                <Badge key={b} value={b} tone="cyan" />
              ))}
            </div>
          )}
          <div className="grid gap-8 lg:grid-cols-2">
            <YourAgentsSection dynamic={dynamic} models={models} onChanged={onAgentsChanged} />
            <RemoteAgentsSection remotes={remotes} onChanged={onRemotesChanged} />
          </div>
        </div>
      )}
    </section>
  );
}
