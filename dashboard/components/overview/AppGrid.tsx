"use client";

/**
 * The desktop (v1.151.0) — Iron Jarvis's modules as app icons.
 *
 * Requested as "app icons with hover-over extra detail and names, macOS desktop
 * style", ordered most-used first and rearrangeable. Three decisions are worth
 * stating because they are what keeps it feeling like a desktop rather than a
 * grid of buttons:
 *
 * * **The icon plate is the object.** A rounded-square plate with the glyph
 *   inside, name underneath — so the tile reads as a thing you can pick up,
 *   which is what makes dragging it discoverable without a hint.
 * * **Hover reveals, it does not reflow.** The blurb appears in an overlay
 *   ABOVE the tile; nothing below it moves. A grid that reflows on hover is
 *   unusable at speed.
 * * **Dragging needs intent.** A 6px activation distance (the same constraint
 *   the Kanban board uses) means a click opens the module and only a deliberate
 *   drag picks it up — otherwise every mis-click becomes an accidental
 *   rearrangement.
 *
 * THREE DOORS (v1.294.0). Thirty tiles on one screen was a wall; v1.293.0
 * made it three swipeable screens; the user then asked for something simpler
 * still: three large icons — Office, Operations, System — and picking one
 * hides the other two and reveals that group's ten modules in place. No
 * strip, no chevrons, no swipe. A back control (or Esc on the grid) closes
 * the group and the three icons return. lib/appTiles.ts says which tile lives
 * where and why. Two decisions matter here:
 *
 * * **Only the open group's tiles are in the DOM.** The clamp, the edge hint
 *   and the pop-out (v1.289.0) run exactly as before, and dnd-kit's sortable
 *   list is simply the ten tiles you can see.
 * * **A rearrangement is still ONE saved list.** `reorderWithinGroup` puts
 *   the group's tiles back into the slots they already hold in the flat
 *   order, so the storage key, its shape and an arrangement saved before any
 *   of this are untouched.
 *
 * The catalogue is `lib/nav.ts`, so a page added there appears here with its
 * icon and hover text already correct — see lib/appTiles.ts.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import Link from "next/link";
import { m } from "framer-motion";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragMoveEvent,
  type Modifier,
} from "@dnd-kit/core";
import {
  SortableContext,
  rectSortingStrategy,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { RotateCcw, GripHorizontal, ChevronLeft } from "lucide-react";
import {
  clearOrder,
  groupedTiles,
  orderedTiles,
  readOpenGroup,
  readOrder,
  readUsage,
  reorderWithinGroup,
  writeOpenGroup,
  writeOrder,
  type AppTile,
  type TileGroup,
} from "@/lib/appTiles";
import { popoutBridge, type PopoutBridge } from "@/lib/desktopShell";
import { clampToWindow, offscreenEdge, type Edge, type Translate } from "@/lib/tileDrag";

const EASE = [0.22, 1, 0.36, 1] as const;

/** Where the hover card should sit, in viewport coordinates. */
interface HoverAt {
  tile: AppTile;
  x: number;
  y: number;
}

function Tile({
  tile,
  dragging,
  armed,
  onHover,
}: {
  tile: AppTile;
  dragging: boolean;
  /** v1.289.0: this tile is being pushed past the screen edge — a drop pops
   *  the module out instead of rearranging. */
  armed: boolean;
  onHover: (at: HoverAt | null) => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: tile.href });
  const Icon = tile.icon;

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`group/tile relative ${isDragging ? "z-30 opacity-90" : ""}`}
      data-testid={`tile-${tile.href.slice(1)}`}
      data-armed={isDragging && armed ? "true" : undefined}
      onPointerEnter={(e) => {
        if (isDragging) return;
        const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
        onHover({ tile, x: r.left + r.width / 2, y: r.top });
      }}
      onPointerLeave={() => onHover(null)}
      {...attributes}
      {...listeners}
    >
      <Link
        href={tile.href}
        // THE ACTUAL FIX (v1.158.1). An <a href> is NATIVELY DRAGGABLE, so
        // pressing a tile and moving it started the BROWSER's link-drag, which
        // swallows the pointermove stream dnd-kit needs. Its 6px threshold was
        // therefore never crossed, onDragStart never fired, nothing suppressed
        // anything — and releasing near where you started landed an ordinary
        // click that opened the module. Found by tracing real events
        // (pointerdown → dragstart → pointerup → click, prevented=false), not
        // by reading the code: the guard below looked sufficient and was never
        // reached.
        draggable={false}
        className="flex flex-col items-center gap-3 rounded-2xl px-2 py-3 outline-none transition-transform duration-200 focus-visible:ring-2 focus-visible:ring-accent/40 group-hover/tile:-translate-y-0.5"
      >
        <span
          className={`relative flex h-28 w-28 items-center justify-center rounded-[1.75rem] border border-white/[0.08] bg-white/[0.04] text-zinc-300 shadow-sm transition-all duration-200 group-hover/tile:border-accent/30 group-hover/tile:bg-accent/[0.08] group-hover/tile:text-accent-soft group-hover/tile:shadow-glow-sm ${
            isDragging ? "border-accent/40 bg-accent/[0.12]" : ""
          } ${isDragging && armed ? "ring-2 ring-accent/60 shadow-glow-sm" : ""}`}
        >
          <Icon size={44} strokeWidth={1.6} />
          {/* Opened-often marker. Deliberately a dot, not a number: the count
              is not information the user needs, only the fact that this is
              somewhere they live. */}
          {tile.opens >= 5 && (
            <span className="absolute -right-1 -top-1 h-3 w-3 rounded-full bg-accent/70 ring-2 ring-ink-950" />
          )}
        </span>
        <span className="max-w-[11rem] truncate text-center text-[13.5px] text-zinc-400 transition-colors group-hover/tile:text-zinc-200">
          {tile.label}
        </span>
      </Link>

    </div>
  );
}

/**
 * The hover detail, rendered ONCE at the grid level in viewport coordinates.
 *
 * It began as an absolutely-positioned child of each tile and was clipped off
 * the left edge of the window on the first column — a 224px card centred on a
 * 90px tile overflows by ~67px, and the leftmost tile has nowhere to overflow
 * TO. Clamping needs the real geometry, so the card is positioned from the
 * tile's measured rect and pinned inside the viewport. Same class of fix as the
 * v1.114.0 thread-menu portal, for the same reason: a parent cannot lay out a
 * child that must escape it.
 */
function HoverCard({ at }: { at: HoverAt | null }) {
  if (!at) return null;
  const WIDTH = 224;
  const MARGIN = 12;
  const half = WIDTH / 2;
  const max =
    (typeof window !== "undefined" ? window.innerWidth : 1440) - half - MARGIN;
  const x = Math.min(Math.max(at.x, half + MARGIN), max);
  return (
    <div
      className="pointer-events-none fixed z-50 rounded-xl border border-white/10 bg-zinc-900/95 px-3 py-2 shadow-lg shadow-black/40 backdrop-blur-sm"
      style={{
        width: WIDTH,
        left: x,
        top: at.y,
        transform: "translate(-50%, calc(-100% - 8px))",
      }}
    >
      <div className="text-[12px] font-medium text-zinc-100">{at.tile.label}</div>
      <div className="mt-0.5 text-[11.5px] leading-relaxed text-zinc-400">
        {at.tile.blurb}
      </div>
      <div className="mt-1 text-[10.5px] uppercase tracking-wide text-zinc-600">
        {at.tile.section}
        {at.tile.opens > 0 && ` · opened ${at.tile.opens}×`}
      </div>
    </div>
  );
}

/**
 * One of the three doors (v1.294.0): a large plate with the group's icon, its
 * name, what it holds and how many modules are behind it. A press opens it.
 */
function GroupDoor({ group, onOpen }: { group: TileGroup; onOpen: () => void }) {
  const Icon = group.group.icon;
  return (
    <button
      type="button"
      data-testid={`group-${group.group.key}`}
      onClick={onOpen}
      className="group/door flex flex-col items-center gap-4 rounded-3xl px-4 py-4 outline-none transition-transform duration-200 hover:-translate-y-0.5 focus-visible:ring-2 focus-visible:ring-accent/40"
    >
      <span className="relative flex h-40 w-40 items-center justify-center rounded-[2.5rem] border border-white/[0.08] bg-white/[0.04] text-zinc-200 shadow-sm transition-all duration-200 group-hover/door:border-accent/30 group-hover/door:bg-accent/[0.08] group-hover/door:text-accent-soft group-hover/door:shadow-glow-sm">
        <Icon size={64} strokeWidth={1.4} />
        <span className="absolute -right-1.5 -top-1.5 rounded-full border border-white/10 bg-zinc-900 px-2 py-0.5 text-[11px] tabular-nums text-zinc-400 ring-2 ring-ink-950">
          {group.tiles.length}
        </span>
      </span>
      <span className="text-[17px] font-medium text-zinc-200 transition-colors group-hover/door:text-zinc-50">
        {group.group.label}
      </span>
      <span className="max-w-[18rem] text-center text-[12.5px] leading-relaxed text-zinc-500">
        {group.group.hint}
      </span>
    </button>
  );
}

export function AppGrid() {
  // Usage + saved order are read AFTER mount: they live in localStorage, so
  // reading them during render would diverge from the server-rendered HTML and
  // trip hydration. The first frame is the catalogue order.
  const [usage, setUsage] = useState<Record<string, number>>({});
  const [order, setOrder] = useState<string[]>([]);
  const [ready, setReady] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [hover, setHover] = useState<HoverAt | null>(null);
  // v1.289.0: the edge the dragged tile is being pushed past, if any.
  const [edge, setEdge] = useState<Edge | null>(null);
  const [activeHref, setActiveHref] = useState<string>("");
  const [popout, setPopout] = useState<PopoutBridge | null>(null);
  // v1.294.0: the open group's key, or null = the three doors.
  const [openKey, setOpenKey] = useState<string | null>(null);

  const tiles = useMemo(() => orderedTiles(usage, order), [usage, order]);
  const flatIds = useMemo(() => tiles.map((t) => t.href), [tiles]);
  const groups = useMemo(() => groupedTiles(tiles), [tiles]);
  const open = useMemo(
    () => (openKey ? groups.find((g) => g.group.key === openKey) ?? null : null),
    [groups, openKey],
  );
  const ids = useMemo(() => (open ? open.tiles.map((t) => t.href) : []), [open]);

  useEffect(() => {
    setUsage(readUsage());
    setOrder(readOrder());
    // The desktop reopens the way it was left: on a group, or on the doors.
    // A key that no longer names a group is the doors BY CONSTRUCTION: `open`
    // is looked up in the groups at render, and no match renders the doors.
    const saved = readOpenGroup();
    if (saved) setOpenKey(saved);
    setReady(true);
    setPopout(popoutBridge());
  }, []);

  const openGroup = useCallback((key: string | null) => {
    setOpenKey(key);
    // A change of view ends any hover: the card would name a tile that is
    // no longer there.
    setHover(null);
    writeOpenGroup(key);
  }, []);

  // Esc on the grid closes the group — but never mid-drag, where dnd-kit's
  // own Esc cancels the drag (its document listener runs after this one, so
  // the check here is what keeps the two apart).
  const onGridKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLDivElement>) => {
      if (e.key !== "Escape" || dragging) return;
      e.preventDefault();
      openGroup(null);
    },
    [dragging, openGroup],
  );

  // A TILE NEVER LEAVES THE SCREEN (v1.289.0). Sortable's transform followed
  // the pointer without limit, so a tile could be dragged clean off the window
  // and the auto-scroller chased it — the Overview was wrecked until a reload.
  // The modifier clamps the tile's rectangle to the viewport. It also keeps the
  // RAW translate (dnd-kit reports only the modified one on drag-move), which
  // is where the "push it off the screen" intent is read from.
  const rawTranslate = useRef<Translate>({ x: 0, y: 0 });
  const clampModifier = useCallback<Modifier>(({ transform, draggingNodeRect, windowRect }) => {
    rawTranslate.current = { x: transform.x, y: transform.y };
    if (!draggingNodeRect) return transform;
    const win = windowRect ?? {
      width: typeof window !== "undefined" ? window.innerWidth : 1440,
      height: typeof window !== "undefined" ? window.innerHeight : 900,
    };
    const c = clampToWindow(transform, draggingNodeRect, win);
    return { ...transform, x: c.x, y: c.y };
  }, []);
  const modifiers = useMemo(() => [clampModifier], [clampModifier]);

  // Survives the render that drag-end triggers.
  const suppressClick = useRef(false);

  // A DRAG MUST NOT ALSO NAVIGATE (v1.158.1).
  //
  // On the DOCUMENT, in the capture phase. Three narrower fixes were tried and
  // measured first, and each failed for its own reason: React's `onClick` on
  // the tile DOES NOT FIRE after a drop (traced — a plain click fires it every
  // time, a post-drag click never does, yet Next still routes); guarding on the
  // `dragging` STATE loses a race, because drag-end sets it false before the
  // browser dispatches click; and a native listener on the tile's own wrapper
  // never received the event either. Document capture is the one place the
  // click provably passes through — the traced chain is
  // SPAN > A[/chat] > DIV > … > MAIN — and it runs before anything downstream
  // can act on it.
  //
  // Scoped to the grid, so this can never swallow a click anywhere else on the
  // page, and cleared by the next pointerdown so a deliberate click still opens.
  const gridRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const swallow = (e: MouseEvent) => {
      if (!suppressClick.current) return;
      const grid = gridRef.current;
      if (!grid || !(e.target instanceof Node) || !grid.contains(e.target)) return;
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      suppressClick.current = false;
    };
    const rearm = () => {
      suppressClick.current = false;
    };
    document.addEventListener("click", swallow, true);
    document.addEventListener("pointerdown", rearm, true);
    return () => {
      document.removeEventListener("click", swallow, true);
      document.removeEventListener("pointerdown", rearm, true);
    };
  }, []);

  const sensors = useSensors(
    // Same 6px intent threshold as the Kanban board — a click opens, only a
    // deliberate drag picks up.
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor),
  );

  // PUSHING A TILE PAST THE EDGE IS A GESTURE (v1.289.0): the intent to put
  // the module somewhere else. Read on every move from the raw translate and
  // the tile's initial rectangle; more than half the tile beyond an edge arms
  // the drop. Only ARMS: nothing opens until the tile is released.
  const onDragMove = useCallback(
    (e: DragMoveEvent) => {
      const rect = e.active.rect.current.initial;
      if (!rect || typeof window === "undefined") return;
      const next = offscreenEdge(rawTranslate.current, rect, {
        width: window.innerWidth,
        height: window.innerHeight,
      });
      setEdge((prev) => (prev === next ? prev : next));
    },
    [],
  );

  const onDragEnd = useCallback(
    (e: DragEndEvent) => {
      setDragging(false);
      const { active, over } = e;
      const pushed = edge;
      setEdge(null);
      if (pushed) {
        // Off the screen means "in its own window" — the v1.283.0 pop-out,
        // desktop only ("/" never pops out, and no tile is "/"). The
        // arrangement is left exactly as it was: this was not a rearrange.
        // In a browser there is no other window to open, so the tile simply
        // stays where the clamp held it.
        if (popout) void popout.open(String(active.id));
        return;
      }
      if (!over || active.id === over.id) return;
      const from = ids.indexOf(String(active.id));
      const to = ids.indexOf(String(over.id));
      if (from < 0 || to < 0) return;
      // Still ONE saved list: this group's tiles in their new sequence,
      // every other group's tile exactly where it was.
      const next = reorderWithinGroup(flatIds, ids, from, to);
      setOrder(next);
      writeOrder(next);
    },
    [ids, flatIds, edge, popout],
  );

  const customised = order.length > 0;
  const activeTile = edge && dragging ? tiles.find((t) => t.href === activeHref) : null;
  const OpenIcon = open?.group.icon;

  return (
    <div
      data-testid="app-desk"
      data-open={open?.group.key ?? "none"}
      // Until localStorage is read the order is the catalogue's; fading in
      // avoids a visible re-sort on every load.
      style={{ opacity: ready ? 1 : 0, transition: "opacity 150ms" }}
    >
      {!open ? (
        // THE DOORS: three large icons. Nothing else — the caption and Reset
        // belong to an open group, where the arrangement is.
        <m.div
          key="doors"
          initial={ready ? { opacity: 0, scale: 0.98 } : false}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.18, ease: EASE }}
          data-testid="group-doors"
          className="grid grid-cols-1 justify-items-center gap-4 py-2 sm:grid-cols-3"
        >
          {groups.map((g) => (
            <GroupDoor key={g.group.key} group={g} onOpen={() => openGroup(g.group.key)} />
          ))}
        </m.div>
      ) : (
        <m.div
          key={open.group.key}
          initial={{ opacity: 0, scale: 0.98 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.18, ease: EASE }}
        >
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            {/* Back to the doors: the group's own icon and name, with the
                chevron saying where the press goes. */}
            <button
              type="button"
              data-testid="group-back"
              onClick={() => openGroup(null)}
              className="inline-flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.03] py-1 pl-2 pr-3 text-[13px] text-zinc-200 transition-colors hover:border-accent/30 hover:text-accent-soft"
            >
              <ChevronLeft size={16} />
              {OpenIcon && <OpenIcon size={15} />}
              <span className="font-medium">{open.group.label}</span>
              <span className="text-[11px] text-zinc-500">{open.tiles.length} modules</span>
            </button>
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.12em] text-zinc-500">
                <GripHorizontal size={12} />
                {customised ? "Your arrangement" : "Most used first"}
              </div>
              {customised && (
                <button
                  type="button"
                  onClick={() => {
                    clearOrder();
                    setOrder([]);
                  }}
                  className="inline-flex items-center gap-1 text-[11px] text-zinc-500 transition-colors hover:text-zinc-300"
                >
                  <RotateCcw size={11} /> Reset to most-used
                </button>
              )}
            </div>
          </div>
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            modifiers={modifiers}
            onDragStart={(e) => {
              suppressClick.current = true;
              setDragging(true);
              setEdge(null);
              setActiveHref(String(e.active.id));
            }}
            onDragMove={onDragMove}
            onDragCancel={() => {
              setDragging(false);
              setEdge(null);
            }}
            onDragEnd={onDragEnd}
          >
            <SortableContext items={ids} strategy={rectSortingStrategy}>
              <div
                ref={gridRef}
                role="region"
                aria-label={`${open.group.label} modules`}
                tabIndex={-1}
                data-testid="module-grid"
                onKeyDown={onGridKeyDown}
                className="grid grid-cols-2 gap-x-3 gap-y-4 outline-none sm:grid-cols-5 2xl:grid-cols-10"
              >
                {open.tiles.map((t) => (
                  <Tile
                    key={t.href}
                    tile={t}
                    dragging={dragging}
                    armed={edge !== null}
                    onHover={setHover}
                  />
                ))}
              </div>
            </SortableContext>
          </DndContext>
          <div className="mt-3 text-center text-[11.5px] text-zinc-500">{open.group.hint}</div>
        </m.div>
      )}
      {/* Suppressed while dragging: a card following the cursor during a
          rearrange is noise on top of the thing you are actually doing. */}
      <HoverCard at={dragging ? null : hover} />
      {/* v1.289.0: the tile is being pushed off the screen. Said once, at the
          bottom, so the user knows what the drop will do before letting go. */}
      {dragging && edge && (
        <div
          data-testid="tile-edge-hint"
          data-edge={edge}
          className="pointer-events-none fixed inset-x-0 bottom-6 z-50 flex justify-center"
        >
          <div className="rounded-full border border-accent/40 bg-zinc-900/95 px-4 py-1.5 text-[12px] text-zinc-100 shadow-lg shadow-black/40 backdrop-blur-sm">
            {popout
              ? `Release to open ${activeTile?.label ?? "this module"} in its own window`
              : "Tiles stay on this screen — the desktop app can open a module in its own window"}
          </div>
        </div>
      )}
    </div>
  );
}
