"use client";

/**
 * The AGENTS ROOM (v1.214.0) — every agent, and everything you can do to one,
 * behind one door and in a dialog that owns the whole window.
 *
 * WHAT IT REPLACES, and why it is a dialog rather than a card. Since v1.179.0
 * agent configuration lived behind the rail's gear and revealed `SetupCard`
 * INTO THE PAGE, under the conversation. That put a form the size of a form
 * inside a column the size of a column, which is the shape the user reported:
 * a configuration surface bounded by the card it happened to be rendered in.
 * A dialog is not bounded by anything — as long as it is actually attached to
 * the page, which is what `components/Modal.tsx` exists to guarantee (read its
 * header for the `backdrop-filter` containing-block trap that made the old
 * "+" popup clip).
 *
 * WHAT IS NEW HERE, beyond the move:
 *   * EVERY AGENT IS CUSTOMIZABLE. The list is the ROSTER, so built-in,
 *     yours and remote agents all appear — and each one gets the same two
 *     controls: a portrait (upload through the square cropper, generate, or
 *     remove) and a face (shape, eyes, colour). The daemon always allowed
 *     this; only the UI drew the line, and it drew it at "agents you created".
 *   * ONE PLACE TO PICK WHO YOU WORK WITH. Selecting an agent, talking to one,
 *     and aiming work at one were the rail's job; the rail is now the thread
 *     list, so those actions come along into the room. The page still OWNS the
 *     selection — this dialog reports, it does not decide.
 *
 * The two tabs are two different questions and are deliberately not merged:
 *   AGENTS   who exists, what they look like, and working with one.
 *   MANAGE   creating an agent of your own, connecting a remote one, and
 *            editing a dynamic agent's persona/tools — the surfaces
 *            `SetupCard` has always held, rendered here at full width.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Briefcase,
  MessageCircle,
  Plus,
  Settings2,
  Sparkles,
  Users,
  WifiOff,
  X,
} from "lucide-react";
import { ApiError, get } from "@/lib/api";
import type { ModelOption } from "@/lib/types";
import { Empty } from "@/components/ui";
import { Modal } from "@/components/Modal";
import { timeAgo } from "@/lib/format";
import AgentFace, { type FaceOverride } from "./AgentFace";
import { AgentPortrait } from "./AgentPortrait";
import {
  FacePicker,
  RemoteAgentsSection,
  YourAgentsSection,
  faceFor,
  type DynamicAgentFull,
  type FaceMap,
} from "./SetupCard";
import {
  KIND_PILL,
  LivePill,
  bareName,
  livenessOf,
  rosterAvatarSrc,
  statsText,
  type RosterEntry,
} from "./RosterStrip";
import { SOURCE_LABEL, type AgentSource, type RemoteAgentInfo } from "./identity";

export type AgentsTab = "agents" | "manage";

/** Everything a row needs to draw itself, resolved once so the list and the
 *  detail panel can never disagree about who they are showing. */
function useFaces(): { faces: FaceMap; supported: boolean; reload: () => void } {
  const [faces, setFaces] = useState<FaceMap>(null);
  const [supported, setSupported] = useState(true);

  const reload = useCallback(async () => {
    try {
      const r = await get<{ faces?: Record<string, FaceOverride> }>("/agents/faces");
      setFaces(r?.faces ?? {});
      setSupported(true);
    } catch (err) {
      // A daemon older than v1.180.0 has no such route: every face derives
      // from its name and the pickers hide themselves. ANY OTHER failure tells
      // us nothing about what is stored, so the last confirmed answer stands
      // rather than claiming "no agent has an override" on a timeout (the
      // v1.180.0 reviewer finding — kept verbatim in behaviour).
      if (err instanceof ApiError && err.status === 404) {
        setSupported(false);
        setFaces({});
      }
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { faces, supported, reload };
}

export function AgentsModal({
  roster,
  dynamic,
  remotes,
  models,
  selected,
  initialTab = "agents",
  onSelect,
  onTalk,
  onAssign,
  onAgentsChanged,
  onRemotesChanged,
  onClose,
}: {
  roster: RosterEntry[];
  dynamic: DynamicAgentFull[];
  remotes: RemoteAgentInfo[];
  models: ModelOption[];
  /** Who the PAGE is working with (kind + BARE name), or nobody. */
  selected: { kind: AgentSource; name: string } | null;
  initialTab?: AgentsTab;
  /** A face was clicked. `canWork` is the delegable + healthy gate, computed
   *  here so the page never has to re-derive it and get it subtly wrong. */
  onSelect: (kind: AgentSource, name: string, canWork: boolean) => void;
  /** Open (or start) the 1:1 thread with this agent. Absent on a daemon with
   *  no thread routes, and then no Talk button renders. */
  onTalk?: (kind: AgentSource, name: string) => void;
  /** Aim the work at this agent. */
  onAssign?: (kind: AgentSource, name: string) => void;
  onAgentsChanged: () => void;
  onRemotesChanged: () => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<AgentsTab>(initialTab);
  const { faces, supported: facesSupported, reload: reloadFaces } = useFaces();

  const entries = roster.filter(
    (e): e is RosterEntry => Boolean(e) && typeof e.name === "string",
  );

  // WHICH ROW IS OPEN. Seeded from the page's selection so the room opens on
  // whoever the user was already working with; falls back to the first entry,
  // which is a PREVIEW and is never announced as a selection (the v1.178.0
  // rule — `aria-current` below is gated on the page's own pick, not on this).
  const pickedEntry = selected
    ? entries.find((e) => e.kind === selected.kind && bareName(e.name) === selected.name)
    : undefined;
  const [openName, setOpenName] = useState<string | null>(pickedEntry?.name ?? null);
  const open = entries.find((e) => e.name === openName) ?? pickedEntry ?? entries[0];

  function choose(e: RosterEntry) {
    setOpenName(e.name);
    onSelect(e.kind, bareName(e.name), e.delegable && e.healthy);
  }

  const tabBtn = (id: AgentsTab, icon: React.ReactNode, label: string) => (
    <button
      key={id}
      type="button"
      role="tab"
      aria-selected={tab === id}
      onClick={() => setTab(id)}
      className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[12px] font-medium transition-colors ${
        tab === id
          ? "bg-accent/[0.12] text-accent-soft ring-1 ring-inset ring-accent/30"
          : "text-zinc-400 hover:bg-white/[0.05] hover:text-zinc-200"
      }`}
    >
      {icon}
      {label}
    </button>
  );

  return (
    <Modal
      label="Agents — who exists, and how they look"
      onClose={onClose}
      className="h-[88vh] w-full max-w-5xl"
      testId="agents-modal"
    >
      <header className="flex shrink-0 flex-wrap items-center gap-2 border-b hairline px-4 py-3">
        <Users size={16} className="text-accent-soft/80" aria-hidden />
        <h2 className="text-[13px] font-semibold tracking-wide text-zinc-200">Agents</h2>
        <div role="tablist" aria-label="Agents" className="ml-3 flex items-center gap-1">
          {tabBtn("agents", <Sparkles size={12} aria-hidden />, "Roster")}
          {tabBtn("manage", <Settings2 size={12} aria-hidden />, "New & manage")}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          title="Close"
          className="ml-auto rounded-lg p-1 text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200"
        >
          <X size={15} />
        </button>
      </header>

      {tab === "agents" ? (
        <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[15rem_minmax(0,1fr)]">
          {/* WHO EXISTS. Its own scroll region: a thirty-agent roster must not
              push the detail panel — or the dialog's own footer — anywhere. */}
          <div
            data-testid="agents-modal-list"
            className="min-h-0 space-y-0.5 overflow-y-auto border-b hairline p-2 md:border-b-0 md:border-r"
          >
            {entries.length === 0 ? (
              <p className="p-3 text-[11.5px] leading-relaxed text-zinc-500">
                This daemon serves no roster, so there is nobody to show here.
                The New &amp; manage tab still works.
              </p>
            ) : (
              entries.map((e) => {
                const bare = bareName(e.name);
                const off = e.kind === "remote" && !e.healthy;
                const live = livenessOf(e);
                const isOpen = open?.name === e.name;
                const isPicked =
                  Boolean(pickedEntry) && pickedEntry?.name === e.name;
                return (
                  <button
                    key={e.name}
                    type="button"
                    onClick={() => choose(e)}
                    // Announced only for the PAGE's pick — the open row is a
                    // panel state, not a claim about who takes the work.
                    aria-current={isPicked ? "true" : undefined}
                    title={`${bare} — ${SOURCE_LABEL[e.kind] ?? e.kind}${
                      off ? " (offline)" : ""
                    }${!e.delegable ? " (chat-only)" : ""}`}
                    className={`flex w-full items-center gap-2 rounded-xl border px-2 py-1.5 text-left transition-colors ${
                      isOpen
                        ? "border-accent/25 bg-accent/[0.08]"
                        : "border-transparent hover:bg-white/[0.04]"
                    }`}
                  >
                    <AgentFace
                      name={bare}
                      mood="idle"
                      size={26}
                      title=""
                      face={faceFor(faces, bare, e.face)}
                      avatarUrl={
                        e.avatar ? rosterAvatarSrc(e.avatar, e.last_active) : undefined
                      }
                      className={off ? "opacity-50" : ""}
                    />
                    <span
                      className={`min-w-0 flex-1 truncate text-[12.5px] ${
                        isOpen ? "text-accent-soft" : "text-zinc-300"
                      }`}
                    >
                      {bare}
                    </span>
                    {live && (
                      <LivePill
                        state={live}
                        bare={bare}
                        testId={`roster-activity-${bare}`}
                      />
                    )}
                    {off && (
                      <span className="inline-flex shrink-0 items-center gap-0.5 rounded-md border border-rose-500/25 bg-rose-500/10 px-1 py-px text-[9.5px] font-medium text-rose-300">
                        <WifiOff size={9} aria-hidden /> offline
                      </span>
                    )}
                    {e.kind === "remote" && (
                      <span
                        data-testid={`roster-kind-${bare}`}
                        className={`shrink-0 rounded-md border px-1 py-px text-[9.5px] font-medium ${KIND_PILL.remote}`}
                      >
                        {SOURCE_LABEL.remote}
                      </span>
                    )}
                  </button>
                );
              })
            )}
            {/* CREATING ONE IS IN THE LIST, not only behind the other tab.
                The icon that opens this dialog is the door to BOTH questions —
                "who do I have" and "give me a new one" — and burying the
                second under a tab would make the common first-run gesture two
                clicks from a place the user has to know to look. */}
            <button
              type="button"
              onClick={() => setTab("manage")}
              data-testid="agents-modal-new"
              title="Create an agent of your own, or connect one on another computer"
              className="mt-1 flex w-full items-center gap-2 rounded-xl border border-dashed border-white/[0.10] px-2 py-2 text-left text-zinc-400 transition-colors hover:border-accent/40 hover:bg-white/[0.04] hover:text-accent-soft"
            >
              <span className="grid h-[26px] w-[26px] shrink-0 place-items-center rounded-full border border-white/[0.10]">
                <Plus size={13} aria-hidden />
              </span>
              <span className="min-w-0 flex-1 truncate text-[12.5px]">
                New agent
                <span className="sr-only"> — local or remote</span>
              </span>
            </button>
          </div>

          {/* ONE AGENT, IN FULL. */}
          <div className="min-h-0 overflow-y-auto p-4">
            {open ? (
              <AgentDetail
                // A FRESH PANEL PER AGENT. The face picker holds an unapplied
                // draft, and a draft is per-agent — reusing one instance is
                // what let v1.180.0's built-in chips hand the next agent the
                // previous one's choice, one Apply from writing it to the
                // wrong record.
                key={open.name}
                entry={open}
                faces={faces}
                facesSupported={facesSupported}
                onTalk={onTalk}
                onAssign={onAssign}
                onAgentsChanged={onAgentsChanged}
                onFaceChanged={reloadFaces}
              />
            ) : (
              <Empty icon={<Users size={22} />}>
                No agents to configure yet — create one in New &amp; manage.
              </Empty>
            )}
          </div>
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <div className="grid gap-8 lg:grid-cols-2">
            <YourAgentsSection
              dynamic={dynamic}
              models={models}
              faces={faces}
              facesSupported={facesSupported}
              onChanged={onAgentsChanged}
              onFaceChanged={reloadFaces}
            />
            <RemoteAgentsSection
              remotes={remotes}
              faces={faces}
              facesSupported={facesSupported}
              onChanged={onRemotesChanged}
              onFaceChanged={reloadFaces}
            />
          </div>
        </div>
      )}
    </Modal>
  );
}

/** One agent in full: who they are, what they look like, and working with
 *  them. Keyed by the caller so switching rows MOUNTS a fresh panel — the face
 *  picker holds an unapplied draft, and a draft is per-agent (the v1.180.0
 *  lesson, which cost an Apply written to the wrong agent). */
function AgentDetail({
  entry,
  faces,
  facesSupported,
  onTalk,
  onAssign,
  onAgentsChanged,
  onFaceChanged,
}: {
  entry: RosterEntry;
  faces: FaceMap;
  facesSupported: boolean;
  onTalk?: (kind: AgentSource, name: string) => void;
  onAssign?: (kind: AgentSource, name: string) => void;
  onAgentsChanged: () => void;
  onFaceChanged: () => void;
}) {
  const bare = bareName(entry.name);
  const off = entry.kind === "remote" && !entry.healthy;
  const face = faceFor(faces, bare, entry.face);
  const live = livenessOf(entry);
  /** The same delegable + healthy gate the rail has always used: a
   *  non-delegable entry (the supervisor) is chat-only, and an offline remote
   *  cannot take a session.
   *
   *  IT NEEDS NO "has the user really picked somebody" CLAUSE, which the rail
   *  did (v1.179.0). There, `selected` fell back to entries[0] as a preview
   *  while the buttons read from that same variable, so a Give-work click
   *  before any pick quietly meant the supervisor. Here the buttons are handed
   *  THIS entry explicitly — `onAssign(entry.kind, bare)` — beside a 52px
   *  portrait and the agent's name in full. There is nothing for the click to
   *  be ambiguous about. */
  const actionable = entry.delegable && entry.healthy;

  return (
    <div data-testid={`agent-detail-${bare}`} className="space-y-4">
      <div className="flex items-start gap-3">
        <AgentFace
          name={bare}
          mood="idle"
          size={52}
          face={face}
          avatarUrl={
            entry.avatar ? rosterAvatarSrc(entry.avatar, entry.last_active) : undefined
          }
          title={`${bare} — ${SOURCE_LABEL[entry.kind] ?? entry.kind}`}
          className={off ? "opacity-50" : ""}
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="truncate text-[15px] font-semibold text-zinc-100">
              {bare}
            </span>
            <span
              // Same testid the rail's rows use — provenance is provenance
              // wherever it is drawn, and one name for it keeps the assertions
              // (and anyone reading them) from having to know which surface
              // they are looking at.
              data-testid={`roster-kind-${bare}`}
              className={`shrink-0 rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${
                KIND_PILL[entry.kind] ?? KIND_PILL.remote
              }`}
            >
              {SOURCE_LABEL[entry.kind] ?? entry.kind}
            </span>
            {live && <LivePill state={live} bare={bare} testId={`detail-activity-${bare}`} />}
            {off && (
              <span className="inline-flex shrink-0 items-center gap-1 rounded-md border border-rose-500/25 bg-rose-500/10 px-1.5 py-0.5 text-[10px] font-medium text-rose-300">
                <WifiOff size={10} aria-hidden /> offline
              </span>
            )}
            {!entry.delegable && (
              <span className="shrink-0 text-[10px] text-zinc-600">
                (chat-only for now)
              </span>
            )}
            <span className="ml-auto shrink-0 text-[11px] tabular-nums text-zinc-500">
              {statsText(entry)}
            </span>
          </div>
          {entry.description && (
            <p className="mt-1 text-[11.5px] leading-relaxed text-zinc-500">
              {entry.description}
            </p>
          )}
          {entry.last_message && (
            <p
              data-testid="roster-preview"
              className="mt-1 flex items-baseline gap-1.5 text-[11.5px] leading-relaxed"
            >
              <span className="min-w-0 flex-1 text-zinc-400">{entry.last_message}</span>
              {entry.last_active && (
                <span
                  data-testid="roster-when"
                  className="shrink-0 text-[10.5px] tabular-nums text-zinc-600"
                >
                  {timeAgo(entry.last_active)}
                </span>
              )}
            </p>
          )}
        </div>
      </div>

      {(onTalk || onAssign) && actionable && (
        <div className="flex flex-wrap items-center gap-2">
          {onTalk && (
            <button
              type="button"
              onClick={() => onTalk(entry.kind, bare)}
              title={`Talk with ${bare} at the round-table`}
              className="btn-ghost px-2.5 py-1.5 text-[11.5px]"
            >
              <MessageCircle size={12} /> Talk
            </button>
          )}
          {onAssign && (
            <button
              type="button"
              onClick={() => onAssign(entry.kind, bare)}
              title={`Give ${bare} a job`}
              className="btn-ghost px-2.5 py-1.5 text-[11.5px]"
            >
              <Briefcase size={12} /> Give work
            </button>
          )}
        </div>
      )}

      {/* APPEARANCE — the same two controls for every kind of agent
          (v1.214.0). Portrait first: it WINS over the drawn face wherever the
          agent appears, so the picker below says so rather than quietly
          drawing something the app will not show. */}
      <div className="space-y-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
        <AgentPortrait
          name={bare}
          avatar={entry.avatar}
          face={face}
          onChanged={onAgentsChanged}
        />
        {facesSupported ? (
          <FacePicker
            name={bare}
            avatarUrl={
              entry.avatar ? rosterAvatarSrc(entry.avatar, entry.last_active) : undefined
            }
            face={face}
            onChanged={onFaceChanged}
          />
        ) : (
          <p className="text-[11px] leading-relaxed text-zinc-500">
            This daemon predates chosen faces, so every face is drawn from the
            agent&rsquo;s name. A portrait still works.
          </p>
        )}
      </div>

      {entry.kind === "dynamic" && (
        <p className="text-[11px] leading-relaxed text-zinc-500">
          Its persona prompt, preferred model and tools live under{" "}
          <span className="text-zinc-400">New &amp; manage</span>.
        </p>
      )}
    </div>
  );
}

export default AgentsModal;
