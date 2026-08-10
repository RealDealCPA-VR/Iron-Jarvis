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
 * The catalogue is `lib/nav.ts`, so a page added there appears here with its
 * icon and hover text already correct — see lib/appTiles.ts.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import Link from "next/link";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  rectSortingStrategy,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { RotateCcw, GripHorizontal } from "lucide-react";
import {
  clearOrder,
  orderedTiles,
  readOrder,
  readUsage,
  writeOrder,
  type AppTile,
} from "@/lib/appTiles";

/** Where the hover card should sit, in viewport coordinates. */
interface HoverAt {
  tile: AppTile;
  x: number;
  y: number;
}

function Tile({
  tile,
  dragging,
  onHover,
}: {
  tile: AppTile;
  dragging: boolean;
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
        className="flex flex-col items-center gap-2 rounded-xl px-1 py-2 outline-none transition-transform duration-200 focus-visible:ring-2 focus-visible:ring-accent/40 group-hover/tile:-translate-y-0.5"
      >
        <span
          className={`relative flex h-14 w-14 items-center justify-center rounded-2xl border border-white/[0.08] bg-white/[0.04] text-zinc-300 shadow-sm transition-all duration-200 group-hover/tile:border-accent/30 group-hover/tile:bg-accent/[0.08] group-hover/tile:text-accent-soft group-hover/tile:shadow-glow-sm ${
            isDragging ? "border-accent/40 bg-accent/[0.12]" : ""
          }`}
        >
          <Icon size={22} />
          {/* Opened-often marker. Deliberately a dot, not a number: the count
              is not information the user needs, only the fact that this is
              somewhere they live. */}
          {tile.opens >= 5 && (
            <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-accent/70 ring-2 ring-ink-950" />
          )}
        </span>
        <span className="max-w-[5.5rem] truncate text-center text-[11.5px] text-zinc-400 transition-colors group-hover/tile:text-zinc-200">
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

export function AppGrid() {
  // Usage + saved order are read AFTER mount: they live in localStorage, so
  // reading them during render would diverge from the server-rendered HTML and
  // trip hydration. The first frame is the catalogue order.
  const [usage, setUsage] = useState<Record<string, number>>({});
  const [order, setOrder] = useState<string[]>([]);
  const [ready, setReady] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [hover, setHover] = useState<HoverAt | null>(null);

  useEffect(() => {
    setUsage(readUsage());
    setOrder(readOrder());
    setReady(true);
  }, []);

  const tiles = useMemo(() => orderedTiles(usage, order), [usage, order]);
  const ids = useMemo(() => tiles.map((t) => t.href), [tiles]);

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

  const onDragEnd = useCallback(
    (e: DragEndEvent) => {
      setDragging(false);
      const { active, over } = e;
      if (!over || active.id === over.id) return;
      const from = ids.indexOf(String(active.id));
      const to = ids.indexOf(String(over.id));
      if (from < 0 || to < 0) return;
      const next = arrayMove(ids, from, to);
      setOrder(next);
      writeOrder(next);
    },
    [ids],
  );

  const customised = order.length > 0;

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
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
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={() => {
          suppressClick.current = true;
          setDragging(true);
        }}
        onDragCancel={() => setDragging(false)}
        onDragEnd={onDragEnd}
      >
        <SortableContext items={ids} strategy={rectSortingStrategy}>
          <div
            ref={gridRef}
            className="grid grid-cols-4 gap-x-2 gap-y-3 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-10"
            // Until localStorage is read the order is the catalogue's; fading
            // in avoids a visible re-sort on every load.
            style={{ opacity: ready ? 1 : 0, transition: "opacity 150ms" }}
          >
            {tiles.map((t) => (
              <Tile key={t.href} tile={t} dragging={dragging} onHover={setHover} />
            ))}
          </div>
        </SortableContext>
      </DndContext>
      {/* Suppressed while dragging: a card following the cursor during a
          rearrange is noise on top of the thing you are actually doing. */}
      <HoverCard at={dragging ? null : hover} />
    </div>
  );
}
