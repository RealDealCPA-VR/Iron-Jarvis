"use client";

// The per-project WORKSPACE — a focused, Claude-Projects-style page. Layout:
//   header (editable name + model default + folder + activate)
//   → collapsible custom instructions + editable brief
//   → tab bar (Chat · Tasks · Board · Activity)
//   → on Chat/Tasks a Knowledge right-rail sits beside the content (knowledge
//     grounds every chat + task); Board/Activity get the full width.
// The Board tab is the ONLY thing that polls /sessions, and only while active.

import { Suspense, use, useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  Archive,
  ArchiveRestore,
  ArrowLeft,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Folder,
  FolderOpen,
  History,
  Images,
  MessageSquare,
  Music,
  Pencil,
  Play,
  RotateCw,
  Sparkles,
  SquareKanban,
  Trash2,
  Zap,
  ZapOff,
  X,
} from "lucide-react";
import { useApi, usePolledApi } from "@/lib/useApi";
import { patch, post, del, ApiError, API_BASE, ijToken } from "@/lib/api";
import { useReviews } from "@/lib/useReviews";
import { KanbanBoard } from "@/components/kanban/KanbanBoard";
import { ProjectChat } from "@/components/project/ProjectChat";
import { ProjectTasks } from "@/components/project/ProjectTasks";
import { KnowledgePanel } from "@/components/project/KnowledgePanel";
import { FilePickerModal } from "@/components/FilePickerModal";
import { ConfirmButton } from "@/components/ui";
import type { ModelOption, Project, SessionView } from "@/lib/types";
import {
  Card,
  Badge,
  OfflineHint,
  Empty,
  ErrorNote,
  LoaderInline,
  SkeletonRows,
} from "@/components/ui";
import { PageShell, Reveal } from "@/components/motion";
import { timeAgo } from "@/lib/format";

/** GET /projects/{id} → the project (plus workspace fields) and recent sessions. */
interface ProjectWorkspace extends Project {
  instructions?: string;
  default_provider?: string;
  default_model?: string;
}
interface ProjectDetail {
  project: ProjectWorkspace;
  sessions: SessionView[];
}

type TabId = "chat" | "tasks" | "board" | "media" | "activity";
const TABS: { id: TabId; label: string; icon: ReactNode }[] = [
  { id: "chat", label: "Chat", icon: <MessageSquare size={14} /> },
  { id: "tasks", label: "Tasks", icon: <Bot size={14} /> },
  { id: "board", label: "Board", icon: <SquareKanban size={14} /> },
  { id: "media", label: "Media", icon: <Images size={14} /> },
  { id: "activity", label: "Activity", icon: <History size={14} /> },
];

/** One media artifact from GET /creative/items?project_id=…. */
interface ProjectMediaItem {
  name: string;
  media: "image" | "video" | "audio" | null;
  filename: string;
  url: string;
  created_at: string | null;
}

/** Media tags can't send the Authorization header — the token rides as ?token=. */
function mediaSrc(url: string): string {
  const t = ijToken();
  const sep = url.includes("?") ? "&" : "?";
  return `${API_BASE}${url}${t ? `${sep}token=${encodeURIComponent(t)}` : ""}`;
}

/** The project's CREATIONS — every generation tagged to this project (studio,
 *  pixio, task outputs) via the artifact spine. Ties Creative into the workspace. */
function ProjectMedia({ projectId }: { projectId: string }) {
  const { data, loading, error } = useApi<{ items: ProjectMediaItem[] }>(
    `/creative/items?project_id=${encodeURIComponent(projectId)}&limit=200`,
  );
  const items = data?.items ?? [];
  return (
    <Card title={items.length ? `Media · ${items.length}` : "Media"} icon={<Images size={15} />}>
      {loading && !data ? (
        <SkeletonRows rows={3} />
      ) : error && error.status === 0 ? (
        <p className="py-2 text-sm text-zinc-500">Media unavailable — the daemon looks offline.</p>
      ) : items.length === 0 ? (
        <Empty icon={<Images size={22} />}>
          No media in this project yet — generate something in Creative (or run a media task) while
          this project is active, and it lands here.
        </Empty>
      ) : (
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 md:grid-cols-4">
          {items.map((m) => (
            <Link
              key={m.name}
              href="/creative"
              title={m.filename}
              className="group block overflow-hidden rounded-lg border border-white/10 transition-colors hover:border-accent/40"
            >
              {m.media === "image" ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={mediaSrc(m.url)}
                  alt={m.filename}
                  className="aspect-square w-full object-cover"
                />
              ) : (
                <div className="flex aspect-square w-full items-center justify-center bg-white/[0.06] text-zinc-400">
                  {m.media === "video" ? <Play size={20} /> : <Music size={18} />}
                </div>
              )}
              <div className="truncate px-1.5 py-1 text-[10px] text-zinc-500" title={m.filename}>
                {m.filename}
              </div>
            </Link>
          ))}
        </div>
      )}
    </Card>
  );
}

const BTN_PILL =
  "inline-flex items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/[0.08] px-2.5 py-1 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14] disabled:opacity-50";
const BTN_GHOST =
  "inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-xs font-medium text-zinc-400 transition-colors hover:border-white/20 hover:text-zinc-200 disabled:opacity-50";

function errText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

function splitChoice(choice: string): { provider?: string; model?: string } {
  const i = choice.indexOf("::");
  if (i === -1) return {};
  const provider = choice.slice(0, i);
  const model = choice.slice(i + 2);
  return provider && model ? { provider, model } : {};
}

/** The glowing "Active" badge — the focus marker for the project you're working
 *  on. It doesn't auto-inject context everywhere; each surface attaches this
 *  project's context itself (e.g. chats and tasks here run grounded in it). */
function ActiveBadge() {
  return (
    <span
      title="Your current focus. Context is attached per surface — e.g. chats and tasks here run grounded in this project."
      className="inline-flex items-center gap-1.5 rounded-full border border-accent/40 bg-accent/[0.12] px-2.5 py-0.5 text-[11px] font-medium text-accent-soft shadow-[0_0_14px_rgb(var(--accent-rgb)/0.35)]"
    >
      <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse-glow shadow-[0_0_8px_2px_rgb(var(--accent-rgb)/0.55)]" />
      Active
    </span>
  );
}

/** Track page visibility so a hidden tab stops polling /sessions — cost should
 *  scale with attention, not wall-clock. SSR-safe: assumes visible until mounted. */
function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const sync = () => setVisible(!document.hidden);
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);
  return visible;
}

/** The per-project Kanban — mounted ONLY while the Board tab is active, so a
 * hidden tab never 4s-polls /sessions. */
function ProjectBoard({ projectId }: { projectId: string }) {
  // Scoped to this project (cost scales with the project, not all history) and
  // paused while the tab is hidden. Null path stops the poll but keeps the last
  // data, so the board renders identically on return.
  const visible = useDocumentVisible();
  const { data, error, reload } = usePolledApi<{ sessions: SessionView[] }>(
    visible ? `/sessions?project_id=${encodeURIComponent(projectId)}` : null,
    4000,
  );
  const sessions = data?.sessions;
  const reviewsState = useReviews(sessions);
  const list = sessions ?? [];
  const mine = list.filter((s) => s.project_id === projectId);
  const offline = error && error.status === 0;

  function refreshAll() {
    reload();
    reviewsState.reload();
  }

  if (offline && list.length === 0)
    return (
      <Card title="Board" icon={<SquareKanban size={15} />}>
        <p className="py-2 text-sm text-zinc-500">Board unavailable — the daemon looks offline.</p>
      </Card>
    );
  if (mine.length === 0)
    return (
      <Card title="Board" icon={<SquareKanban size={15} />}>
        <Empty icon={<SquareKanban size={22} />}>
          No sessions in this project yet — run a task, or start a chat.
        </Empty>
      </Card>
    );
  // KanbanBoard filters to projectId itself; pass the full list so its lane math
  // stays identical to the standalone page.
  return (
    <KanbanBoard
      sessions={list}
      reviews={reviewsState.reviews}
      reload={refreshAll}
      projectId={projectId}
    />
  );
}

/** Live recent activity for the project — polls /sessions (like the Board) so
 *  it never diverges from the Board's freshness or count. */
function ActivityList({ projectId }: { projectId: string }) {
  // Same scoped + visibility-paused poll as the Board so the two never diverge.
  const visible = useDocumentVisible();
  const { data } = usePolledApi<{ sessions: SessionView[] }>(
    visible ? `/sessions?project_id=${encodeURIComponent(projectId)}` : null,
    4000,
  );
  const sessions = (data?.sessions ?? [])
    .filter((s) => s.project_id === projectId)
    .slice(0, 40);
  return (
    <Card
      title={sessions.length ? `Recent activity · ${sessions.length}` : "Recent activity"}
      icon={<History size={15} />}
    >
      {sessions.length === 0 ? (
        <Empty icon={<History size={22} />}>
          No sessions in this project yet — run a task or start a chat.
        </Empty>
      ) : (
        <ul className="space-y-1.5">
          {sessions.map((s) => (
            <li key={s.id}>
              <Link
                href={`/sessions/${encodeURIComponent(s.id)}`}
                className="flex items-center justify-between gap-3 rounded-lg border border-white/[0.05] bg-white/[0.02] px-3 py-2 transition-colors hover:border-accent/25 hover:bg-white/[0.04]"
              >
                <span className="min-w-0 truncate text-xs text-zinc-300">{s.task || s.id}</span>
                <span className="flex shrink-0 items-center gap-2">
                  <Badge value={s.status} />
                  <span className="text-[11px] text-zinc-600">{timeAgo(s.created_at)}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export default function ProjectWorkspacePage(props: {
  params: Promise<{ id: string }>;
}) {
  // The inner view reads useSearchParams (?tab=), which would deopt this whole
  // route out of static prerendering unless it sits inside a Suspense boundary.
  return (
    <Suspense fallback={null}>
      <ProjectWorkspaceInner {...props} />
    </Suspense>
  );
}

function ProjectWorkspaceInner({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const detail = useApi<ProjectDetail>(`/projects/${encodeURIComponent(id)}`);
  const models = useApi<{ models: ModelOption[] }>("/models");

  const project = detail.data?.project;
  const offline = detail.error && detail.error.status === 0;
  const notFound = detail.error && detail.error.status === 404;
  // Anything else (500, malformed, etc.) — don't leave a blank page. Show the
  // shared error affordance with a reload, mirroring the Projects list page.
  const genericError = detail.error && !offline && !notFound;

  const availableModels = (models.data?.models ?? []).filter((m) => m.available !== false);

  /* --- Active tab: ?tab= is the source of truth (deep-linkable + bookmarkable),
     falling back to the per-project localStorage, then the default. Reading the
     param synchronously seeds initial state, so there's no first-paint flash of
     the default tab when arriving on a deep link. ------------------------------ */
  const TAB_KEY = `ij.project.${id}.tab`;
  const urlTab = searchParams.get("tab");
  const paramTab =
    urlTab && TABS.some((t) => t.id === urlTab) ? (urlTab as TabId) : null;
  const [tab, setTab] = useState<TabId>(paramTab ?? "chat");
  useEffect(() => {
    if (paramTab) {
      setTab(paramTab);
      return;
    }
    try {
      const s = window.localStorage.getItem(TAB_KEY);
      if (s && TABS.some((t) => t.id === s)) setTab(s as TabId);
    } catch {
      /* ignore */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, urlTab]);
  function chooseTab(t: TabId) {
    setTab(t);
    try {
      window.localStorage.setItem(TAB_KEY, t); // fallback when there's no ?tab=
    } catch {
      /* ignore */
    }
    const next = new URLSearchParams(searchParams.toString());
    next.set("tab", t);
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  }

  /* --- Header / inline edits ---------------------------------------------- */
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [briefEditing, setBriefEditing] = useState(false);
  const [briefDraft, setBriefDraft] = useState("");
  const [instrOpen, setInstrOpen] = useState(false);
  const [instrDraft, setInstrDraft] = useState("");
  const [savingField, setSavingField] = useState<string | null>(null);
  const [headerError, setHeaderError] = useState<string | null>(null);
  const [rootPickerOpen, setRootPickerOpen] = useState(false);

  // Seed drafts once per loaded project (display uses the live project fields
  // when not editing, so a post-save reload won't clobber an open editor).
  useEffect(() => {
    if (!project) return;
    setNameDraft(project.name);
    setBriefDraft(project.brief ?? "");
    setInstrDraft(project.instructions ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id]);

  async function savePatch(body: Record<string, unknown>, field: string): Promise<boolean> {
    setSavingField(field);
    setHeaderError(null);
    try {
      await patch(`/projects/${encodeURIComponent(id)}`, body);
      detail.reload();
      return true;
    } catch (err) {
      setHeaderError(errText(err));
      return false;
    } finally {
      setSavingField(null);
    }
  }

  async function saveName() {
    const name = nameDraft.trim();
    if (!name || name === project?.name) {
      setEditingName(false);
      return;
    }
    if (await savePatch({ name }, "name")) setEditingName(false);
  }

  async function saveBrief() {
    if (await savePatch({ brief: briefDraft.trim() }, "brief")) setBriefEditing(false);
  }

  function chooseModel(v: string) {
    const { provider, model } = splitChoice(v);
    void savePatch(
      { default_provider: provider ?? "", default_model: model ?? "" },
      "model",
    );
  }

  async function activate() {
    setSavingField("active");
    setHeaderError(null);
    try {
      await post(`/projects/${encodeURIComponent(id)}/activate`);
      detail.reload();
    } catch (err) {
      setHeaderError(errText(err));
    } finally {
      setSavingField(null);
    }
  }
  async function deactivate() {
    setSavingField("active");
    setHeaderError(null);
    try {
      await post("/projects/deactivate");
      detail.reload();
    } catch (err) {
      setHeaderError(errText(err));
    } finally {
      setSavingField(null);
    }
  }
  async function setStatus(status: "active" | "archived") {
    setSavingField("status");
    setHeaderError(null);
    try {
      await patch(`/projects/${encodeURIComponent(id)}`, { status });
      detail.reload();
    } catch (err) {
      setHeaderError(errText(err));
    } finally {
      setSavingField(null);
    }
  }
  async function removeProject() {
    setSavingField("delete");
    setHeaderError(null);
    try {
      await del(`/projects/${encodeURIComponent(id)}`);
      router.push("/projects"); // the workspace no longer exists
    } catch (err) {
      setHeaderError(errText(err));
      setSavingField(null);
    }
  }
  // Set/change the project FOLDER — the one edit the workspace lacked, which
  // stranded every file deliverable ("set one on the Projects page first").
  async function saveRoot(path: string) {
    setRootPickerOpen(false);
    if (path === (project?.root ?? "")) return;
    void savePatch({ root: path }, "root");
  }

  const modelValue =
    project?.default_provider && project?.default_model
      ? `${project.default_provider}::${project.default_model}`
      : "";
  const archived = project?.status === "archived";
  const showRail = tab === "chat" || tab === "tasks";

  return (
    <PageShell>
      <Reveal>
        <Link
          href="/projects"
          className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1 text-xs text-zinc-300 transition-colors hover:border-white/20 hover:text-zinc-100"
        >
          <ArrowLeft size={13} /> Projects
        </Link>
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}
      {notFound && (
        <Reveal>
          <Card>
            <Empty icon={<Folder size={22} />}>Project not found.</Empty>
          </Card>
        </Reveal>
      )}
      {genericError && (
        <Reveal>
          <Card>
            <div className="space-y-3">
              <ErrorNote>
                {detail.error?.message || "Couldn't load this project."}
              </ErrorNote>
              <button
                type="button"
                onClick={() => detail.reload()}
                className="btn-ghost"
              >
                <RotateCw size={13} /> Reload
              </button>
            </div>
          </Card>
        </Reveal>
      )}

      {detail.loading && !detail.data ? (
        <Reveal>
          <Card>
            <SkeletonRows rows={5} />
          </Card>
        </Reveal>
      ) : project ? (
        <>
          {/* -------------------------------------------------- header */}
          <Reveal>
            <div className="flex flex-col gap-4">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  {editingName ? (
                    <div className="flex items-center gap-2">
                      <input
                        value={nameDraft}
                        onChange={(e) => setNameDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            void saveName();
                          } else if (e.key === "Escape") {
                            setEditingName(false);
                            setNameDraft(project.name);
                          }
                        }}
                        aria-label="Project name"
                        autoFocus
                        className="field max-w-md text-lg font-semibold"
                      />
                      <button
                        type="button"
                        onClick={() => void saveName()}
                        disabled={savingField === "name"}
                        className="text-accent-soft hover:text-accent"
                        title="Save"
                      >
                        {savingField === "name" ? (
                          <LoaderInline label="" />
                        ) : (
                          <Check size={16} />
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setEditingName(false);
                          setNameDraft(project.name);
                        }}
                        className="text-zinc-500 hover:text-zinc-200"
                        title="Cancel"
                      >
                        <X size={16} />
                      </button>
                    </div>
                  ) : (
                    <div className="flex flex-wrap items-center gap-2">
                      <h1 className="min-w-0 truncate text-2xl font-semibold tracking-tight text-zinc-50">
                        {project.name}
                      </h1>
                      {project.active && <ActiveBadge />}
                      {archived && <Badge value="archived" tone="slate" />}
                      <button
                        type="button"
                        onClick={() => {
                          setNameDraft(project.name);
                          setEditingName(true);
                        }}
                        className="text-zinc-500 transition-colors hover:text-zinc-200"
                        title="Rename project"
                      >
                        <Pencil size={14} />
                      </button>
                    </div>
                  )}

                  {/* Brief line (click to edit) */}
                  {briefEditing ? (
                    <div className="mt-2 space-y-2">
                      <textarea
                        value={briefDraft}
                        onChange={(e) => setBriefDraft(e.target.value)}
                        rows={3}
                        aria-label="Project brief"
                        placeholder="The goal and key facts every chat and task should know…"
                        className="field max-w-2xl resize-y text-sm"
                      />
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => void saveBrief()}
                          disabled={savingField === "brief"}
                          className="btn-accent"
                        >
                          {savingField === "brief" ? <LoaderInline label="Saving…" /> : "Save"}
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setBriefEditing(false);
                            setBriefDraft(project.brief ?? "");
                          }}
                          className="btn-ghost"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => {
                        setBriefDraft(project.brief ?? "");
                        setBriefEditing(true);
                      }}
                      className="mt-1.5 block max-w-2xl text-left text-sm text-zinc-500 transition-colors hover:text-zinc-300"
                    >
                      {project.brief ? (
                        project.brief
                      ) : (
                        <span className="italic text-zinc-600">
                          Add a brief — the goal and key facts every chat and task should know…
                        </span>
                      )}
                    </button>
                  )}

                  {/* Folder root — editable (was read-only, which stranded file
                      deliverables). Click to browse + change; set one if none. */}
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-zinc-500">
                    <Folder
                      size={11}
                      className={`shrink-0 ${
                        project.root && project.root_exists === false
                          ? "text-amber-500"
                          : ""
                      }`}
                    />
                    {project.root ? (
                      <span
                        className={`min-w-0 truncate font-mono ${
                          project.root_exists === false ? "text-amber-400" : ""
                        }`}
                        title={project.root}
                      >
                        {project.root}
                      </span>
                    ) : (
                      <span className="italic text-zinc-600">No folder — file tasks need one</span>
                    )}
                    {project.root && project.root_exists === false && (
                      <span
                        title="This folder no longer exists on disk — file tasks will fail until you update it"
                        className="inline-flex shrink-0 items-center gap-1 rounded-md border border-amber-400/30 bg-amber-400/[0.12] px-1.5 py-0.5 font-medium text-amber-500"
                      >
                        <AlertTriangle size={10} className="shrink-0" /> folder missing / moved —
                        update it
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => setRootPickerOpen(true)}
                      disabled={savingField === "root"}
                      className="inline-flex shrink-0 items-center gap-1 rounded-md border border-white/10 px-1.5 py-0.5 text-[10px] font-medium text-zinc-400 transition-colors hover:border-accent/30 hover:text-accent-soft disabled:opacity-50"
                      title={project.root ? "Change the project folder" : "Set a project folder"}
                    >
                      {savingField === "root" ? (
                        <LoaderInline label="" />
                      ) : (
                        <>
                          <FolderOpen size={11} /> {project.root ? "Change" : "Set folder"}
                        </>
                      )}
                    </button>
                  </div>
                  <FilePickerModal
                    open={rootPickerOpen}
                    onClose={() => setRootPickerOpen(false)}
                    onPick={(path: string) => void saveRoot(path)}
                    pickFolders
                    title="Choose the project folder"
                  />
                </div>

                {/* Controls */}
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    value={modelValue}
                    onChange={(e) => chooseModel(e.target.value)}
                    disabled={savingField === "model"}
                    aria-label="Project default model"
                    title="Default model for this project's chats and tasks"
                    className="field text-sm"
                  >
                    <option value="">Project default</option>
                    {/* A pinned model that isn't currently available must still
                        SHOW (otherwise the picker reads blank and the pin looks
                        lost) — surface it as an explicit "unavailable" option. */}
                    {modelValue &&
                      !availableModels.some(
                        (m) => `${m.provider}::${m.model}` === modelValue,
                      ) && (
                        <option value={modelValue}>
                          {project.default_provider} / {project.default_model} (unavailable)
                        </option>
                      )}
                    {availableModels.map((m) => (
                      <option key={`${m.provider}::${m.model}`} value={`${m.provider}::${m.model}`}>
                        {m.provider} / {m.model}
                      </option>
                    ))}
                  </select>

                  {!archived &&
                    (project.active ? (
                      <button
                        type="button"
                        onClick={() => void deactivate()}
                        disabled={savingField === "active"}
                        title="Clear the focus marker — surfaces stop defaulting to this project"
                        className={BTN_GHOST}
                      >
                        {savingField === "active" ? (
                          <LoaderInline label="…" />
                        ) : (
                          <>
                            <ZapOff size={13} /> Deactivate
                          </>
                        )}
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => void activate()}
                        disabled={savingField === "active"}
                        title="Make this project your focus — surfaces like Chat attach its context per session"
                        className={BTN_PILL}
                      >
                        {savingField === "active" ? (
                          <LoaderInline label="…" />
                        ) : (
                          <>
                            <Zap size={13} /> Make active
                          </>
                        )}
                      </button>
                    ))}

                  {/* Lifecycle: archive / unarchive / delete — previously you
                      had to bounce back to the list page to reach these. */}
                  {archived ? (
                    <button
                      type="button"
                      onClick={() => void setStatus("active")}
                      disabled={savingField === "status"}
                      title="Restore this project as an active workspace"
                      className={BTN_GHOST}
                    >
                      {savingField === "status" ? (
                        <LoaderInline label="…" />
                      ) : (
                        <>
                          <ArchiveRestore size={13} /> Unarchive
                        </>
                      )}
                    </button>
                  ) : (
                    <ConfirmButton
                      onConfirm={() => setStatus("archived")}
                      label="Archive"
                      confirmLabel="Archive?"
                      title={`Archive "${project.name}" — it stops appearing as an active workspace but nothing is deleted`}
                    />
                  )}
                  <ConfirmButton
                    onConfirm={removeProject}
                    label="Delete"
                    confirmLabel="Delete from app?"
                    title={`Remove "${project.name}" from Iron Jarvis only — your files and folders on this computer are NOT touched`}
                  />
                </div>
              </div>

              {headerError && <ErrorNote>{headerError}</ErrorNote>}

              {/* Custom instructions (collapsible) */}
              <div className="rounded-2xl border hairline bg-white/[0.02]">
                <button
                  type="button"
                  onClick={() => setInstrOpen((o) => !o)}
                  aria-expanded={instrOpen}
                  className="flex w-full items-center gap-2 px-4 py-3 text-sm font-medium text-zinc-200"
                >
                  {instrOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                  <Sparkles size={14} className="text-accent-soft/80" />
                  Custom instructions
                  {project.instructions && !instrOpen && (
                    <span className="ml-1 text-[11px] font-normal text-zinc-500">· set</span>
                  )}
                </button>
                {instrOpen && (
                  <div className="space-y-2 border-t hairline px-4 py-3">
                    <p className="text-[12px] text-zinc-500">
                      Given to every chat and task in this project — set the tone, rules, and
                      preferences the AI should always follow.
                    </p>
                    <textarea
                      value={instrDraft}
                      onChange={(e) => setInstrDraft(e.target.value)}
                      rows={5}
                      aria-label="Custom instructions"
                      placeholder="e.g. Always cite sources. Prefer concise bullet points. Never touch files outside this folder."
                      className="field resize-y text-sm"
                    />
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => void savePatch({ instructions: instrDraft }, "instructions")}
                        disabled={savingField === "instructions"}
                        className="btn-accent"
                      >
                        {savingField === "instructions" ? (
                          <LoaderInline label="Saving…" />
                        ) : (
                          "Save instructions"
                        )}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </Reveal>

          {/* -------------------------------------------------- tab bar */}
          <Reveal>
            <div className="flex items-center gap-1 border-b hairline">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => chooseTab(t.id)}
                  aria-pressed={tab === t.id}
                  className={`-mb-px inline-flex items-center gap-1.5 border-b-2 px-3.5 py-2 text-sm font-medium transition-colors ${
                    tab === t.id
                      ? "border-accent text-accent-soft"
                      : "border-transparent text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  {t.icon}
                  {t.label}
                </button>
              ))}
            </div>
          </Reveal>

          {/* -------------------------------------------------- content */}
          <Reveal>
            <div className={showRail ? "grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]" : ""}>
              <div className="min-w-0">
                {/* An archived project is closed out — don't quietly spawn NEW
                    chats/tasks into it. Its past work (Board/Activity) stays
                    readable; running work needs an explicit unarchive. */}
                {archived && (tab === "chat" || tab === "tasks") ? (
                  <Card>
                    <Empty icon={<Archive size={22} />}>
                      <div className="space-y-3 text-center">
                        <p>
                          This project is archived — unarchive it to start new{" "}
                          {tab === "chat" ? "chats" : "tasks"} here.
                        </p>
                        <button
                          type="button"
                          onClick={() => void setStatus("active")}
                          disabled={savingField === "status"}
                          className={BTN_PILL}
                        >
                          {savingField === "status" ? (
                            <LoaderInline label="…" />
                          ) : (
                            <>
                              <ArchiveRestore size={13} /> Unarchive project
                            </>
                          )}
                        </button>
                      </div>
                    </Empty>
                  </Card>
                ) : (
                  <>
                    {tab === "chat" && (
                      <ProjectChat
                        projectId={id}
                        defaultProvider={project.default_provider}
                        defaultModel={project.default_model}
                        hasRoot={!!project.root}
                      />
                    )}
                    {tab === "tasks" && (
                      <ProjectTasks
                        projectId={id}
                        hasRoot={!!project.root}
                        sessions={detail.data?.sessions ?? []}
                        reloadSessions={detail.reload}
                      />
                    )}
                  </>
                )}
                {tab === "board" && <ProjectBoard projectId={id} />}
                {tab === "media" && <ProjectMedia projectId={id} />}
                {tab === "activity" && <ActivityList projectId={id} />}
              </div>
              {showRail && (
                <aside className="min-w-0">
                  <KnowledgePanel projectId={id} />
                </aside>
              )}
            </div>
          </Reveal>
        </>
      ) : null}
    </PageShell>
  );
}
