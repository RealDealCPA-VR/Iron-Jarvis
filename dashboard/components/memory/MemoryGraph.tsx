"use client";

// 3D graph view of memory (v1.115.0) — rewrite of the 2D @xyflow canvas.
//
// REQUESTED: "very 3d and visually appealing with simple nodes that when
// hovered over will provide the text instead of the text just showing the
// entire time. Additionally it should be easy to delete a node that is
// irrelevant or connect it to another node."
//
// Shape of the answer: WebGL force graph (react-force-graph-3d, loaded
// client-only in Graph3DCanvas — three touches `window` at import) with plain
// glowing spheres, text ONLY on hover, and a right rail that carries what a
// 3D canvas is bad at: finding a node by name, seeing the selection's full
// text, and the delete/connect actions. Connect works two ways — click the
// second node in space, or pick it from the Find list (dense clusters make
// 3D picking miss; the list never does).
//
// Backend contract:
//   GET  /memory/graph?threshold=0.45     -> { nodes, edges, embedder, note? }
//   POST /memory/graph/link {a,b}         -> user-drawn edge (lifts blocks)
//   POST /memory/graph/unlink {a,b}       -> manual: deleted · auto: BLOCKED
//   POST /memory/graph/node/delete {id}   -> lessons + working memory only;
//        ltm:* is refused server-side — long-term notes are files in the
//        user's own memory base, and a canvas click must never reach those.

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BrainCircuit,
  Database,
  GraduationCap,
  Link2,
  Loader2,
  RefreshCw,
  Search,
  X,
} from "lucide-react";

import { get, post, ApiError } from "@/lib/api";
import {
  Card,
  ConfirmButton,
  Empty,
  ErrorNote,
  LoaderInline,
  OfflineHint,
} from "@/components/ui";
import { Reveal } from "@/components/motion";
import {
  GROUP_3D,
  deletableKind,
  toGraphData,
  type GraphDto,
  type Link3D,
  type Node3D,
} from "./graph3d";
import type { Graph3DApi } from "./Graph3DCanvas";

const Graph3DCanvas = dynamic(() => import("./Graph3DCanvas"), {
  ssr: false,
  loading: () => (
    <div className="grid h-full w-full place-items-center text-zinc-500">
      <LoaderInline label="Preparing the 3D view…" />
    </div>
  ),
});

const GROUP_ICON = {
  lesson: GraduationCap,
  memory: BrainCircuit,
  note: Database,
} as const;

export default function MemoryGraph() {
  const [dto, setDto] = useState<GraphDto | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [linkFrom, setLinkFrom] = useState<string | null>(null);
  const [busy, setBusy] = useState(false); // link/unlink/delete in flight
  const [find, setFind] = useState("");
  const apiRef = useRef<Graph3DApi | null>(null);

  const load = useCallback(async () => {
    setError(null);
    setOffline(false);
    try {
      const res = await get<GraphDto>("/memory/graph?threshold=0.45");
      setDto(res);
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) setOffline(true);
      else setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const data = useMemo(
    () => (dto ? toGraphData(dto) : { nodes: [], links: [] }),
    [dto],
  );
  const selected = useMemo(
    () => data.nodes.find((n) => n.id === selectedId) ?? null,
    [data.nodes, selectedId],
  );
  const counts = useMemo(() => {
    const c = { lesson: 0, memory: 0, note: 0 };
    for (const n of data.nodes) c[n.group] += 1;
    return c;
  }, [data.nodes]);
  const findMatches = useMemo(() => {
    const q = find.trim().toLowerCase();
    if (!q) return [];
    return data.nodes
      .filter(
        (n) =>
          n.label.toLowerCase().includes(q) || n.snippet.toLowerCase().includes(q),
      )
      .slice(0, 30);
  }, [data.nodes, find]);

  /** Complete (or start) a connection. Used by BOTH the canvas click and the
   *  Find list, so dense clusters never block linking. */
  const connectTo = useCallback(
    async (targetId: string) => {
      if (!linkFrom || linkFrom === targetId || busy) return;
      setBusy(true);
      try {
        await post("/memory/graph/link", { a: linkFrom, b: targetId });
        setLinkFrom(null);
        await load();
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [linkFrom, busy, load],
  );

  const pickNode = useCallback(
    (n: Node3D) => {
      if (linkFrom && linkFrom !== n.id) {
        void connectTo(n.id);
        return;
      }
      setSelectedId(n.id);
      apiRef.current?.flyTo(n.id);
    },
    [linkFrom, connectTo],
  );

  const clickLink = useCallback(
    async (l: Link3D) => {
      const a = typeof l.source === "string" ? l.source : l.source.id;
      const b = typeof l.target === "string" ? l.target : l.target.id;
      const what =
        l.kind === "manual"
          ? "Remove this link?"
          : "Hide this similarity link? It stays hidden (blocked) until you re-link them.";
      if (!window.confirm(what)) return;
      setBusy(true);
      try {
        await post("/memory/graph/unlink", { a, b });
        await load();
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const deleteSelected = useCallback(async () => {
    if (!selectedId) return;
    setBusy(true);
    try {
      await post("/memory/graph/node/delete", { id: selectedId });
      setSelectedId(null);
      setLinkFrom(null);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [selectedId, load]);

  // Esc backs out of linking mode, then out of the selection.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (linkFrom) setLinkFrom(null);
      else setSelectedId(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [linkFrom]);

  const del = selectedId ? deletableKind(selectedId) : { ok: false as const };

  return (
    <Reveal>
      <Card pad={false} className="overflow-hidden">
        <div className="flex flex-col lg:flex-row">
          {/* ---- the 3D canvas ------------------------------------------- */}
          <div className="relative h-[560px] min-w-0 flex-1">
            {loading ? (
              <div className="grid h-full place-items-center">
                <LoaderInline label="Mapping memory…" />
              </div>
            ) : data.nodes.length === 0 ? (
              <div className="grid h-full place-items-center px-6">
                <Empty icon={<BrainCircuit size={24} />}>
                  Nothing remembered yet — lessons, working memory, and
                  long-term notes will appear here as they accumulate.
                </Empty>
              </div>
            ) : (
              <Graph3DCanvas
                nodes={data.nodes}
                links={data.links}
                selectedId={selectedId}
                linkFromId={linkFrom}
                onNodeClick={pickNode}
                onLinkClick={(l) => void clickLink(l)}
                onBackgroundClick={() => {
                  if (linkFrom) setLinkFrom(null);
                  else setSelectedId(null);
                }}
                apiRef={apiRef}
              />
            )}

            {/* Linking-mode banner: the one moment the canvas has a MODE, so
                it says so on screen instead of relying on memory. */}
            {linkFrom && (
              <div className="pointer-events-none absolute inset-x-0 top-3 flex justify-center">
                <span className="pointer-events-auto inline-flex items-center gap-2 rounded-full border border-accent/40 bg-ink-950/90 px-3 py-1.5 text-[12px] text-accent-soft shadow-lg">
                  <Link2 size={13} />
                  Click another node to connect
                  <button
                    type="button"
                    onClick={() => setLinkFrom(null)}
                    aria-label="Cancel connecting"
                    className="text-zinc-500 transition-colors hover:text-zinc-200"
                  >
                    <X size={13} />
                  </button>
                </span>
              </div>
            )}

            <p className="pointer-events-none absolute bottom-2 left-3 text-[11px] text-zinc-600">
              drag to orbit · scroll to zoom · hover a node for its text
            </p>
          </div>

          {/* ---- the rail: find · selection · legend ---------------------- */}
          <div className="w-full shrink-0 space-y-4 border-t hairline p-4 lg:w-72 lg:border-l lg:border-t-0">
            <div className="relative">
              <Search
                size={13}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500"
              />
              <input
                value={find}
                onChange={(e) => setFind(e.target.value)}
                placeholder="Find a memory…"
                aria-label="Find a node"
                className="field w-full pl-8 text-[13px]"
              />
            </div>
            {findMatches.length > 0 && (
              <ul className="max-h-44 space-y-0.5 overflow-y-auto">
                {findMatches.map((n) => {
                  const Icon = GROUP_ICON[n.group];
                  return (
                    <li key={n.id}>
                      <button
                        type="button"
                        onClick={() => pickNode(n)}
                        className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[12.5px] text-zinc-300 transition-colors hover:bg-white/[0.06]"
                      >
                        <Icon
                          size={13}
                          className="shrink-0"
                          style={{ color: GROUP_3D[n.group].hex }}
                        />
                        <span className="min-w-0 truncate">{n.label}</span>
                        {linkFrom && linkFrom !== n.id && (
                          <span className="ml-auto shrink-0 text-[10px] uppercase tracking-wide text-accent-soft">
                            link
                          </span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}

            {selected ? (
              <div className="space-y-3 rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
                <div>
                  <span
                    className="text-[10px] font-semibold uppercase tracking-wide"
                    style={{ color: GROUP_3D[selected.group].hex }}
                  >
                    {GROUP_3D[selected.group].label}
                  </span>
                  <p className="mt-0.5 break-words text-[13px] font-medium text-zinc-100">
                    {selected.label}
                  </p>
                  {selected.snippet && (
                    <p className="mt-1 max-h-28 overflow-y-auto whitespace-pre-wrap text-[12px] leading-relaxed text-zinc-400">
                      {selected.snippet}
                    </p>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setLinkFrom(selected.id)}
                    disabled={busy || linkFrom === selected.id}
                    className="btn-ghost px-2.5 py-1.5 text-[12px]"
                  >
                    {busy && linkFrom ? (
                      <Loader2 size={13} className="animate-spin" />
                    ) : (
                      <Link2 size={13} />
                    )}
                    Connect…
                  </button>
                  {del.ok ? (
                    <ConfirmButton
                      onConfirm={deleteSelected}
                      label="Delete"
                      confirmLabel="Really delete?"
                      title="Delete this memory — the node and its links go with it"
                      className="px-2.5 py-1.5 text-[12px]"
                    />
                  ) : (
                    <p className="text-[11px] leading-snug text-zinc-500">
                      Lives in the{" "}
                      <span className="text-zinc-300">
                        {"base" in del ? del.base : "long-term"}
                      </span>{" "}
                      memory base — manage it there.
                    </p>
                  )}
                </div>
              </div>
            ) : (
              <p className="text-[12px] leading-relaxed text-zinc-500">
                Click a node to see its text and act on it — or search above to
                jump straight to one.
              </p>
            )}

            <div className="space-y-1.5 border-t hairline pt-3">
              {(Object.keys(GROUP_3D) as Array<keyof typeof GROUP_3D>).map((g) => (
                <div key={g} className="flex items-center gap-2 text-[12px] text-zinc-400">
                  <span
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{ background: GROUP_3D[g].hex }}
                  />
                  {GROUP_3D[g].label}
                  <span className="ml-auto tabular-nums text-zinc-600">{counts[g]}</span>
                </div>
              ))}
              <div className="flex items-center gap-2 pt-1 text-[12px] text-zinc-400">
                <span className="h-px w-4 shrink-0 bg-accent" />
                your links
                <span className="mx-1 h-px w-4 shrink-0 bg-zinc-600" />
                similar
              </div>
            </div>

            <div className="flex items-center justify-between border-t hairline pt-3">
              <p className="min-w-0 truncate text-[11px] text-zinc-600" title={dto?.note}>
                {dto?.embedder ? `similarity: ${dto.embedder}` : ""}
              </p>
              <button
                type="button"
                onClick={() => void load()}
                aria-label="Refresh the graph"
                title="Refresh"
                className="btn-ghost shrink-0 px-2 py-1"
              >
                <RefreshCw size={13} />
              </button>
            </div>

            {error && <ErrorNote>{error}</ErrorNote>}
            {offline && <OfflineHint />}
          </div>
        </div>
      </Card>
    </Reveal>
  );
}
