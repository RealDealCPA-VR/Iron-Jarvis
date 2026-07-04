"use client";

import { useState } from "react";
import Link from "next/link";
import {
  FolderKanban,
  Plus,
  Zap,
  ZapOff,
  Pencil,
  ArchiveRestore,
  ChevronDown,
  ChevronUp,
  Folder,
  History,
} from "lucide-react";
import { api, get, post, ApiError } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import type { Project, SessionView } from "@/lib/types";
import {
  Card,
  Badge,
  OfflineHint,
  Empty,
  SkeletonRows,
  ErrorNote,
  SuccessNote,
  LoaderInline,
  ConfirmButton,
  Spinner,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { timeAgo } from "@/lib/format";

/** api.ts exports no PATCH helper, so build one on the exported generic `api`. */
const patch = <T,>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });

/** GET /projects/{id} → the project plus its recent sessions (last 20). */
interface ProjectDetail {
  project: Project;
  sessions: SessionView[];
}

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

/** The glowing "Active" badge — this project is the context spine right now. */
function ActiveBadge() {
  return (
    <span
      title="New chats, sessions, and workflows automatically carry this project's context"
      className="inline-flex items-center gap-1.5 rounded-full border border-accent/40 bg-accent/[0.12] px-2.5 py-0.5 text-[11px] font-medium text-accent-soft shadow-[0_0_14px_rgba(34,211,238,0.35)]"
    >
      <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse-glow shadow-[0_0_8px_2px_rgba(34,211,238,0.55)]" />
      Active
    </span>
  );
}

function ProjectCard({
  project: p,
  onChanged,
}: {
  project: Project;
  onChanged: () => void;
}) {
  const archived = p.status === "archived";
  const sessions = p.session_count ?? 0;

  /** Which card action is in flight ("activate" | "brief" | "status" | null). */
  const [busy, setBusy] = useState<string | null>(null);
  const [cardError, setCardError] = useState<string | null>(null);

  /* Inline brief editor */
  const [editing, setEditing] = useState(false);
  const [briefDraft, setBriefDraft] = useState(p.brief);

  /* Expandable recent sessions (fetched lazily on first expand) */
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  async function run(action: string, fn: () => Promise<unknown>): Promise<boolean> {
    setBusy(action);
    setCardError(null);
    try {
      await fn();
      onChanged();
      return true;
    } catch (err) {
      setCardError(errText(err));
      return false;
    } finally {
      setBusy(null);
    }
  }

  const activate = () =>
    run("activate", () =>
      post<ActivateResult>(`/projects/${encodeURIComponent(p.id)}/activate`),
    );
  const deactivate = () =>
    run("activate", () => post<ActivateResult>("/projects/deactivate"));
  const setStatus = (status: "active" | "archived") =>
    run("status", () =>
      patch<Project>(`/projects/${encodeURIComponent(p.id)}`, { status }),
    );

  async function saveBrief() {
    const ok = await run("brief", () =>
      patch<Project>(`/projects/${encodeURIComponent(p.id)}`, {
        brief: briefDraft.trim(),
      }),
    );
    if (ok) setEditing(false); // keep the editor open on failure so nothing is lost
  }

  async function toggleSessions() {
    const next = !expanded;
    setExpanded(next);
    if (!next || detail || detailLoading) return;
    setDetailLoading(true);
    setDetailError(null);
    try {
      setDetail(await get<ProjectDetail>(`/projects/${encodeURIComponent(p.id)}`));
    } catch (err) {
      setDetailError(errText(err));
    } finally {
      setDetailLoading(false);
    }
  }

  return (
    <Card hover className={archived ? "opacity-70" : ""}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 truncate font-medium text-zinc-100">{p.name}</span>
        {p.active && <ActiveBadge />}
        {archived && <Badge value="archived" tone="slate" />}
      </div>

      {editing ? (
        <div className="mt-3 space-y-2">
          <textarea
            value={briefDraft}
            onChange={(e) => setBriefDraft(e.target.value)}
            rows={4}
            aria-label={`Brief for ${p.name}`}
            placeholder="Goal + key facts the AI should always know…"
            className="field resize-y text-sm"
          />
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={saveBrief}
              disabled={busy !== null}
              className="btn-accent"
            >
              {busy === "brief" ? <LoaderInline label="Saving…" /> : "Save brief"}
            </button>
            <button
              type="button"
              onClick={() => {
                setEditing(false);
                setBriefDraft(p.brief);
              }}
              className="btn-ghost"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : p.brief ? (
        <p className="mt-2 line-clamp-3 text-sm text-zinc-400">{p.brief}</p>
      ) : (
        <p className="mt-2 text-sm italic text-zinc-600">
          No brief yet — add one so every chat starts with the right context.
        </p>
      )}

      {p.root && (
        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-zinc-500">
          <Folder size={11} className="shrink-0" />
          <span className="truncate font-mono">{p.root}</span>
        </div>
      )}

      <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-zinc-600">
        <span>
          {sessions} {sessions === 1 ? "session" : "sessions"}
        </span>
        <span>created {timeAgo(p.created_at)}</span>
      </div>

      <div className="mt-3.5 flex flex-wrap items-center gap-1.5">
        {!archived &&
          (p.active ? (
            <button
              type="button"
              onClick={deactivate}
              disabled={busy !== null}
              title="Stop feeding this project's context into new sessions"
              className={BTN_GHOST}
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
              title="New chats, sessions, and workflows will carry this project's context"
              className={BTN_PILL}
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

        {!editing && (
          <button
            type="button"
            onClick={() => {
              setBriefDraft(p.brief);
              setEditing(true);
              setCardError(null);
            }}
            disabled={busy !== null}
            className={BTN_GHOST}
          >
            <Pencil size={13} /> Edit brief
          </button>
        )}

        {archived ? (
          <button
            type="button"
            onClick={() => setStatus("active")}
            disabled={busy !== null}
            className={BTN_GHOST}
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
            onConfirm={() => {
              void setStatus("archived");
            }}
            label="Archive"
            confirmLabel="Archive?"
            title={`Archive "${p.name}" — it stops appearing as a workspace but nothing is deleted`}
          />
        )}

        <button
          type="button"
          onClick={toggleSessions}
          aria-expanded={expanded}
          className={`${BTN_GHOST} ml-auto`}
        >
          <History size={13} /> Recent sessions
          {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        </button>
      </div>

      {cardError && (
        <div className="mt-3">
          <ErrorNote>{cardError}</ErrorNote>
        </div>
      )}

      {expanded && (
        <div className="mt-3.5 border-t hairline pt-3.5">
          {detailLoading ? (
            <Spinner label="Loading sessions…" />
          ) : detailError ? (
            <ErrorNote>{detailError}</ErrorNote>
          ) : !detail || detail.sessions.length === 0 ? (
            <div className="py-1 text-xs text-zinc-500">
              No sessions in this project yet — make it active and start a chat.
            </div>
          ) : (
            <ul className="space-y-1.5">
              {detail.sessions.map((s) => (
                <li key={s.id}>
                  <Link
                    href={`/sessions/${encodeURIComponent(s.id)}`}
                    className="flex items-center justify-between gap-3 rounded-lg border border-white/[0.05] bg-white/[0.02] px-3 py-2 transition-colors hover:border-accent/25 hover:bg-white/[0.04]"
                  >
                    <span className="min-w-0 truncate text-xs text-zinc-300">
                      {s.task || s.id}
                    </span>
                    <span className="flex shrink-0 items-center gap-2">
                      <Badge value={s.status} />
                      <span className="text-[11px] text-zinc-600">
                        {timeAgo(s.created_at)}
                      </span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  );
}

export default function ProjectsPage() {
  const { data, error, loading, reload } = useApi<{ projects: Project[] }>(
    "/projects",
  );
  const offline = error && error.status === 0;

  // Active first, archived last, newest first within each group.
  const projects = [...(data?.projects ?? [])].sort((a, b) => {
    if (!!a.active !== !!b.active) return a.active ? -1 : 1;
    const aArch = a.status === "archived" ? 1 : 0;
    const bArch = b.status === "archived" ? 1 : 0;
    if (aArch !== bArch) return aArch - bArch;
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });

  /* --- New project form ---------------------------------------------------- */
  const [name, setName] = useState("");
  const [brief, setBrief] = useState("");
  const [root, setRoot] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setFormError(null);
    setOk(null);
    const body: Record<string, string> = { name: name.trim() };
    if (brief.trim()) body.brief = brief.trim();
    if (root.trim()) body.root = root.trim();
    try {
      const created = await post<Project>("/projects", body);
      setOk(
        created.active
          ? `"${created.name}" created and set active — new chats now carry its context.`
          : `Project "${created.name}" created.`,
      );
      setName("");
      setBrief("");
      setRoot("");
      reload();
    } catch (err) {
      setFormError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Projects"
          subtitle="Each project is a workspace with its own brief and history. The active project's brief and recent activity are automatically given to every chat, session, and workflow, so everything stays on the same page."
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
                  <input
                    value={root}
                    onChange={(e) => setRoot(e.target.value)}
                    placeholder="C:\Users\me\Projects\q3-taxes"
                    aria-label="Project folder root"
                    className="field font-mono text-sm"
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
                {ok && <SuccessNote>{ok}</SuccessNote>}
                {formError && <ErrorNote>{formError}</ErrorNote>}
              </form>
            </Card>
          </div>

          <div className="lg:col-span-2">
            {loading && !data ? (
              <SkeletonRows rows={4} />
            ) : projects.length === 0 ? (
              <Card>
                <Empty icon={<FolderKanban size={24} />}>
                  No projects yet — create one and every chat, session, and
                  workflow will share its context.
                </Empty>
              </Card>
            ) : (
              <div className="grid gap-4 xl:grid-cols-2">
                {projects.map((p) => (
                  <ProjectCard key={p.id} project={p} onChanged={reload} />
                ))}
              </div>
            )}
          </div>
        </div>
      </Reveal>
    </PageShell>
  );
}
