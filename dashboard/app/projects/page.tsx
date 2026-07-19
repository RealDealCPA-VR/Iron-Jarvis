"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  FolderKanban,
  Plus,
  Zap,
  ZapOff,
  ArchiveRestore,
  Folder,
  FolderOpen,
  ArrowRight,
  MessageSquare,
  SquareKanban,
  ListChecks,
  BookOpen,
  Search,
  SearchX,
} from "lucide-react";
import { del, patch, post, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import type { Project } from "@/lib/types";
import {
  Card,
  Badge,
  OfflineHint,
  Empty,
  SkeletonRows,
  ErrorNote,
  LoaderInline,
  ConfirmButton,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { FilePickerModal } from "@/components/FilePickerModal";
import { timeAgo } from "@/lib/format";

/** POST /projects/{id}/activate & /projects/deactivate response. */
interface ActivateResult {
  active_project_id: string | null;
  name?: string;
}

function errText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

/* Small action-button styles (match the Templates "Use" pill + ghost rows). */
const BTN_PILL =
  "inline-flex items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/[0.08] px-2.5 py-1 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14] disabled:opacity-50";
const BTN_GHOST =
  "inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1 text-xs font-medium text-zinc-400 transition-colors hover:border-white/20 hover:text-zinc-200 disabled:opacity-50";

/**
 * Honest copy for the focus-project marker. "Active" is only a lightweight flag
 * that highlights one project around the app (e.g. the Overview) — it does NOT
 * auto-carry context into new work. You attach a project per surface yourself
 * (e.g. picking it in Chat). Keep every mention on this page consistent with this.
 */
const FOCUS_HINT =
  "Marks your current focus project — a marker only, used to highlight it around the app. It does NOT auto-carry context; you attach a project per surface (e.g. pick it in Chat).";

/** The glowing "Active" badge — the current focus marker (not a context spine). */
function ActiveBadge() {
  return (
    <span
      title={FOCUS_HINT}
      className="inline-flex items-center gap-1.5 rounded-full border border-accent/40 bg-accent/[0.12] px-2.5 py-0.5 text-[11px] font-medium text-accent-soft shadow-[0_0_14px_rgb(var(--accent-rgb)/0.35)]"
    >
      <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse-glow shadow-[0_0_8px_2px_rgb(var(--accent-rgb)/0.55)]" />
      Active
    </span>
  );
}

/**
 * A lightweight project tile. The whole card is a link into the project's
 * workspace (`/projects/{id}`) via a stretched overlay; the lifecycle buttons
 * sit above it and act without navigating. All the heavy machinery — task
 * composer, board, activity hub, artifacts — now lives in that workspace route.
 */
function ProjectTile({
  project: p,
  onChanged,
}: {
  project: Project;
  onChanged: () => void;
}) {
  const archived = p.status === "archived";
  const sessions = p.session_count ?? 0;
  const knowledge = p.knowledge_count ?? 0;
  const knowledgeNoun = knowledge === 1 ? "knowledge item" : "knowledge items";

  /** Which action is in flight ("activate" | "status" | "delete" | null). */
  const [busy, setBusy] = useState<string | null>(null);
  const [cardError, setCardError] = useState<string | null>(null);

  async function run(action: string, fn: () => Promise<unknown>): Promise<void> {
    setBusy(action);
    setCardError(null);
    try {
      await fn();
      onChanged();
    } catch (err) {
      setCardError(errText(err));
    } finally {
      setBusy(null);
    }
  }

  const activate = () =>
    void run("activate", () =>
      post<ActivateResult>(`/projects/${encodeURIComponent(p.id)}/activate`),
    );
  const deactivate = () =>
    void run("activate", () => post<ActivateResult>("/projects/deactivate"));
  const setStatus = (status: "active" | "archived") =>
    void run("status", () =>
      patch<Project>(`/projects/${encodeURIComponent(p.id)}`, { status }),
    );

  return (
    <div
      className={`card-surface group relative flex flex-col gap-2 p-5 transition-all duration-300 hover:-translate-y-0.5 hover:shadow-card-hover ${
        archived ? "opacity-70" : ""
      }`}
    >
      {/* Stretched link — a click anywhere on the card opens the workspace. */}
      <Link
        href={`/projects/${encodeURIComponent(p.id)}`}
        aria-label={`Open ${p.name} workspace`}
        className="absolute inset-0 z-10 rounded-2xl focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
      />

      {/* Info block sits above the link but passes clicks through to it. */}
      <div className="pointer-events-none relative z-20 flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="min-w-0 truncate font-medium text-zinc-100">
            {p.name}
          </span>
          {p.active && <ActiveBadge />}
          {archived && <Badge value="archived" tone="slate" />}
          {/* An unmistakable "this opens" cue that slides on hover. */}
          <ArrowRight
            size={15}
            className="ml-auto shrink-0 text-zinc-600 transition-all group-hover:translate-x-0.5 group-hover:text-accent-soft"
          />
        </div>

        {p.brief ? (
          <p className="line-clamp-2 text-sm text-zinc-400">{p.brief}</p>
        ) : (
          <p className="text-sm italic text-zinc-600">
            No brief yet — open the workspace to add one.
          </p>
        )}

        {p.root && (
          <div
            className={`flex items-center gap-1.5 text-[11px] ${
              p.root_exists === false ? "text-amber-300/90" : "text-zinc-500"
            }`}
            title={
              p.root_exists === false
                ? "This folder no longer exists on disk — file tasks will fail until you fix it"
                : p.root
            }
          >
            <Folder size={11} className="shrink-0" />
            <span className="truncate font-mono">{p.root}</span>
            {p.root_exists === false && (
              <span className="shrink-0 font-sans font-medium">· folder missing</span>
            )}
          </div>
        )}

        {/* What's inside — makes the tile read as a doorway, not a static card. */}
        <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-zinc-500">
          <span className="inline-flex items-center gap-1"><MessageSquare size={11} /> Chat</span>
          <span className="inline-flex items-center gap-1"><ListChecks size={11} /> Tasks</span>
          <span className="inline-flex items-center gap-1"><SquareKanban size={11} /> Board</span>
          <span className="inline-flex items-center gap-1">
            <BookOpen size={11} />{" "}
            {p.knowledge_count ? `${p.knowledge_count} knowledge` : "Knowledge"}
          </span>
        </div>

        <div className="text-[11px] text-zinc-600">
          {sessions} {sessions === 1 ? "session" : "sessions"} · created{" "}
          {timeAgo(p.created_at)}
        </div>

        {/* Primary CTA — visually a button; the stretched link handles the click. */}
        <div className="mt-1">
          <span className="inline-flex items-center gap-1.5 rounded-lg border border-accent/25 bg-accent/[0.08] px-2.5 py-1 text-xs font-medium text-accent-soft transition-colors group-hover:border-accent/40 group-hover:bg-accent/[0.14]">
            Open workspace <ArrowRight size={12} />
          </span>
        </div>
      </div>

      {/* Lifecycle actions — re-enable pointer events so they don't navigate. */}
      <div className="pointer-events-none relative z-20 mt-2 flex flex-wrap items-center gap-1.5">
        {!archived && (
          <Link
            href={`/chat?project=${encodeURIComponent(p.id)}`}
            title={`Chat inside "${p.name}" — the main chat scoped to this project`}
            className={`${BTN_GHOST} pointer-events-auto`}
          >
            <MessageSquare size={13} /> Chat
          </Link>
        )}
        {!archived &&
          (p.active ? (
            <button
              type="button"
              onClick={deactivate}
              disabled={busy !== null}
              title="Clear the focus marker on this project (a marker only — nothing auto-carries context)."
              className={`${BTN_GHOST} pointer-events-auto`}
            >
              {busy === "activate" ? (
                <LoaderInline label="Deactivating…" />
              ) : (
                <>
                  <ZapOff size={13} /> Deactivate
                </>
              )}
            </button>
          ) : (
            <button
              type="button"
              onClick={activate}
              disabled={busy !== null}
              title={`Set as your focus project. ${FOCUS_HINT}`}
              className={`${BTN_PILL} pointer-events-auto`}
            >
              {busy === "activate" ? (
                <LoaderInline label="Activating…" />
              ) : (
                <>
                  <Zap size={13} /> Make active
                </>
              )}
            </button>
          ))}

        {archived ? (
          <button
            type="button"
            onClick={() => setStatus("active")}
            disabled={busy !== null}
            className={`${BTN_GHOST} pointer-events-auto`}
          >
            {busy === "status" ? (
              <LoaderInline label="Restoring…" />
            ) : (
              <>
                <ArchiveRestore size={13} /> Unarchive
              </>
            )}
          </button>
        ) : (
          <ConfirmButton
            className="pointer-events-auto"
            onConfirm={() => setStatus("archived")}
            label="Archive"
            confirmLabel="Archive?"
            title={`Archive "${p.name}" — it stops appearing as a workspace but nothing is deleted`}
          />
        )}

        <ConfirmButton
          className="pointer-events-auto"
          onConfirm={() =>
            run("delete", () => del(`/projects/${encodeURIComponent(p.id)}`))
          }
          label="Delete"
          confirmLabel={
            knowledge > 0 ? `Delete + ${knowledge} knowledge?` : "Delete from app?"
          }
          title={
            knowledge > 0
              ? `Permanently delete "${p.name}" and its ${knowledge} ${knowledgeNoun} from Iron Jarvis. Your files and folders on this computer are NOT touched.`
              : `Remove "${p.name}" from Iron Jarvis only — your files and folders on this computer are NOT touched`
          }
        />
      </div>

      {cardError && (
        <div className="pointer-events-none relative z-20 mt-1">
          <div className="pointer-events-auto">
            <ErrorNote>{cardError}</ErrorNote>
          </div>
        </div>
      )}
    </div>
  );
}

export default function ProjectsPage() {
  const router = useRouter();
  const { data, error, loading, reload } = useApi<{ projects: Project[] }>(
    "/projects",
  );
  const offline = error && error.status === 0;

  /* --- Filter + sort ------------------------------------------------------- */
  const [filter, setFilter] = useState("");
  const [hideArchived, setHideArchived] = useState(false);

  const totalCount = data?.projects?.length ?? 0;

  // Filter by name (case-insensitive) + optional archived toggle, THEN sort:
  // active first, archived last, newest first within each group. Memoized so it
  // only recomputes when the data or the filters change — not on every render.
  const projects = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return (data?.projects ?? [])
      .filter((p) => (q ? p.name.toLowerCase().includes(q) : true))
      .filter((p) => (hideArchived ? p.status !== "archived" : true))
      .sort((a, b) => {
        if (!!a.active !== !!b.active) return a.active ? -1 : 1;
        const aArch = a.status === "archived" ? 1 : 0;
        const bArch = b.status === "archived" ? 1 : 0;
        if (aArch !== bArch) return aArch - bArch;
        return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
      });
  }, [data?.projects, filter, hideArchived]);

  /* --- New project form ---------------------------------------------------- */
  const [name, setName] = useState("");
  const [brief, setBrief] = useState("");
  const [root, setRoot] = useState("");
  const [rootPickerOpen, setRootPickerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setFormError(null);
    const body: Record<string, string> = { name: name.trim() };
    if (brief.trim()) body.brief = brief.trim();
    if (root.trim()) body.root = root.trim();
    try {
      const created = await post<Project>("/projects", body);
      // Land the user straight in the new project's workspace.
      router.push(`/projects/${encodeURIComponent(created.id)}`);
    } catch (err) {
      setFormError(errText(err));
      setBusy(false); // keep the form usable on failure (success navigates away)
    }
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Projects"
          subtitle="Open a project to its workspace — chat, run tasks, track work on a board, and add knowledge, all grounded in that project."
        />
      </Reveal>
      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <Reveal>
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-1">
            <Card title="New project" icon={<Plus size={15} />}>
              <form onSubmit={submit} className="space-y-3.5">
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Name
                  </label>
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Q3 tax season"
                    aria-label="Project name"
                    className="field"
                  />
                </div>

                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Brief <span className="text-zinc-600">(optional)</span>
                  </label>
                  <textarea
                    value={brief}
                    onChange={(e) => setBrief(e.target.value)}
                    placeholder="Goal + key facts the AI should always know…"
                    rows={4}
                    aria-label="Project brief"
                    className="field resize-y"
                  />
                </div>

                <div>
                  <label className="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    <Folder size={12} /> Folder root{" "}
                    <span className="text-zinc-600">(optional)</span>
                  </label>
                  <div className="flex items-stretch gap-2">
                    <input
                      value={root}
                      onChange={(e) => setRoot(e.target.value)}
                      placeholder="C:\Users\me\Projects\q3-taxes"
                      aria-label="Project folder root"
                      className="field min-w-0 flex-1 font-mono text-sm"
                    />
                    <button
                      type="button"
                      onClick={() => setRootPickerOpen(true)}
                      title="Browse folders on this machine"
                      aria-label="Browse for a project folder"
                      className="btn-ghost shrink-0"
                    >
                      <FolderOpen size={14} /> Browse…
                    </button>
                  </div>
                  <FilePickerModal
                    open={rootPickerOpen}
                    onClose={() => setRootPickerOpen(false)}
                    onPick={(path: string) => {
                      setRoot(path);
                      setRootPickerOpen(false);
                    }}
                    pickFolders
                    title="Choose the project folder"
                  />
                </div>

                <button
                  type="submit"
                  disabled={busy || !name.trim()}
                  className="btn-accent w-full"
                >
                  {busy ? (
                    <LoaderInline label="Creating…" />
                  ) : (
                    <>
                      <Plus size={14} /> Create project
                    </>
                  )}
                </button>
                {formError && <ErrorNote>{formError}</ErrorNote>}
              </form>
            </Card>
          </div>

          <div className="lg:col-span-2">
            {loading && !data ? (
              <SkeletonRows rows={4} />
            ) : offline && !data ? (
              // Offline is NOT "no projects" — don't tell a user with projects
              // that they have none just because the daemon is unreachable.
              <Card>
                <OfflineHint />
              </Card>
            ) : error && !offline ? (
              <Card>
                <ErrorNote>{error.message}</ErrorNote>
              </Card>
            ) : totalCount === 0 ? (
              <Card>
                <Empty icon={<FolderKanban size={24} />}>
                  No projects yet — create one to get a workspace.
                </Empty>
              </Card>
            ) : (
              <div className="space-y-4">
                {/* Filter bar — narrow the grid by name; toggle archived clutter. */}
                <div className="flex flex-wrap items-center gap-2.5">
                  <div className="relative min-w-0 flex-1">
                    <Search
                      size={14}
                      className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500"
                    />
                    <input
                      value={filter}
                      onChange={(e) => setFilter(e.target.value)}
                      placeholder="Filter projects by name…"
                      aria-label="Filter projects by name"
                      className="field pl-9"
                    />
                  </div>
                  <button
                    type="button"
                    onClick={() => setHideArchived((v) => !v)}
                    aria-pressed={hideArchived}
                    title={
                      hideArchived
                        ? "Show archived projects too"
                        : "Hide archived projects from this list"
                    }
                    className={hideArchived ? BTN_PILL : BTN_GHOST}
                  >
                    <ArchiveRestore size={13} /> Hide archived
                  </button>
                  <span className="shrink-0 text-[11px] tabular-nums text-zinc-500">
                    {projects.length === totalCount
                      ? `${totalCount} ${totalCount === 1 ? "project" : "projects"}`
                      : `${projects.length} of ${totalCount}`}
                  </span>
                </div>

                {projects.length === 0 ? (
                  <Card>
                    <Empty icon={<SearchX size={24} />}>
                      {filter.trim()
                        ? `No projects match “${filter.trim()}”.`
                        : "No projects match the current filters."}
                    </Empty>
                  </Card>
                ) : (
                  <div className="grid gap-4 xl:grid-cols-2">
                    {projects.map((p) => (
                      <ProjectTile key={p.id} project={p} onChanged={reload} />
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </Reveal>
    </PageShell>
  );
}
