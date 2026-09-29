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
 * SLIDES (v1.293.0). Thirty tiles on one screen was a wall; the desktop is now
 * three screens of ten — Office, Operations, System (lib/appTiles.ts says
 * which tile lives where and why) — with a tab strip naming each group by its
 * icon, chevrons at both sides, and a swipe. Two decisions matter here:
 *
 * * **Only the current slide is in the DOM.** Not a scrolling track: an
 *   overflow container clips whatever is transformed outside it, and the
 *   v1.289.0 gesture drags a tile to the WINDOW edge, well outside any track.
 *   With one grid on screen at a time the clamp, the edge hint and the pop-out
 *   behave exactly as before, and dnd-kit's sortable list is simply the ten
 *   tiles you can see.
 * * **A swipe is read from the background, never from a tile.** Pressing a
 *   tile and moving is a rearrange (dnd-kit takes it at 6px, as ever); a
 *   horizontal pull that started on empty space, a horizontal wheel/trackpad
 *   gesture, the arrow keys on the focused strip, and the tabs/chevrons all
 *   change slides. A tile drag that has started blocks the swipe for that
 *   press, so releasing a dragged tile can never also flip the screen.
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
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
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
import { RotateCcw, GripHorizontal, ChevronLeft, ChevronRight } from "lucide-react";
import {
  clearOrder,
  orderedTiles,
  readOrder,
  readSlide,
  readUsage,
  reorderWithinSlide,
  slides as toSlides,
  writeOrder,
  writeSlide,
  type AppTile,
} from "@/lib/appTiles";
import { popoutBridge, type PopoutBridge } from "@/lib/desktopShell";
import { clampToWindow, offscreenEdge, type Edge, type Translate } from "@/lib/tileDrag";

/** A horizontal pull shorter than this is a wobble, not a swipe. */
const SWIPE_MIN_PX = 48;
/** A horizontal wheel/trackpad gesture below this is scroll noise. */
const WHEEL_MIN_PX = 24;
/** One wheel gesture = one slide: a trackpad emits dozens of events per swipe. */
const WHEEL_COOLDOWN_MS = 450;
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
        className="flex flex-col items-center gap-2 rounded-xl px-1 py-2 outline-none transition-transform duration-200 focus-visible:ring-2 focus-visible:ring-accent/40 group-hover/tile:-translate-y-0.5"
      >
        <span
          className={`relative flex h-14 w-14 items-center justify-center rounded-2xl border border-white/[0.08] bg-white/[0.04] text-zinc-300 shadow-sm transition-all duration-200 group-hover/tile:border-accent/30 group-hover/tile:bg-accent/[0.08] group-hover/tile:text-accent-soft group-hover/tile:shadow-glow-sm ${
            isDragging ? "border-accent/40 bg-accent/[0.12]" : ""
          } ${isDragging && armed ? "ring-2 ring-accent/60 shadow-glow-sm" : ""}`}
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
  // v1.289.0: the edge the dragged tile is being pushed past, if any.
  const [edge, setEdge] = useState<Edge | null>(null);
  const [activeHref, setActiveHref] = useState<string>("");
  const [popout, setPopout] = useState<PopoutBridge | null>(null);
  // v1.293.0: which slide is on screen, and which way the last change went
  // (0 = none yet, so the first paint does not slide in from anywhere).
  const [slide, setSlide] = useState(0);
  const [dir, setDir] = useState(0);

  const tiles = useMemo(() => orderedTiles(usage, order), [usage, order]);
  const flatIds = useMemo(() => tiles.map((t) => t.href), [tiles]);
  const allSlides = useMemo(() => toSlides(tiles), [tiles]);
  const current = allSlides[Math.min(slide, allSlides.length - 1)] ?? allSlides[0];
  const ids = useMemo(() => (current ? current.tiles.map((t) => t.href) : []), [current]);

  useEffect(() => {
    setUsage(readUsage());
    setOrder(readOrder());
    // The screen you left the desktop on is the one it reopens to. A key
    // that no longer names a group (or was never written) is the first slide.
    const saved = readSlide();
    if (saved) {
      const i = toSlides(orderedTiles()).findIndex((s) => s.group.key === saved);
      if (i > 0) setSlide(i);
    }
    setReady(true);
    setPopout(popoutBridge());
  }, []);

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

  // --- slides (v1.293.0) ----------------------------------------------------

  const goTo = useCallback(
    (index: number) => {
      const max = allSlides.length - 1;
      const next = Math.min(Math.max(index, 0), max);
      if (next === slide) return;
      setDir(next > slide ? 1 : -1);
      setSlide(next);
      // A change of screen ends any hover: the card would name a tile that is
      // no longer there.
      setHover(null);
      const key = allSlides[next]?.group.key;
      if (key) writeSlide(key);
    },
    [allSlides, slide],
  );
  const step = useCallback((delta: number) => goTo(slide + delta), [goTo, slide]);

  // The swipe. Recorded on pointerdown anywhere in the strip (a tile included —
  // the press is not the gesture, the release is), judged on the document's
  // pointerup so a pull that leaves the strip still counts. A tile drag that
  // STARTED during this press blocks it: dnd-kit picked the tile up at 6px,
  // so the release is a drop, not a swipe. The block is set on drag start and
  // cleared by the next press, because drag-end runs before this listener on
  // the same pointerup and a "currently dragging" flag would already be false.
  const swipeStart = useRef<{ x: number; y: number; id: number } | null>(null);
  const swipeBlocked = useRef(false);
  const onSlidePointerDown = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    swipeBlocked.current = false;
    swipeStart.current = { x: e.clientX, y: e.clientY, id: e.pointerId };
  }, []);
  const stepRef = useRef(step);
  stepRef.current = step;
  useEffect(() => {
    const release = (e: PointerEvent) => {
      const start = swipeStart.current;
      if (!start || e.pointerId !== start.id) return;
      swipeStart.current = null;
      if (swipeBlocked.current) return;
      const dx = e.clientX - start.x;
      const dy = e.clientY - start.y;
      // Mostly horizontal, and far enough to be meant.
      if (Math.abs(dx) < SWIPE_MIN_PX || Math.abs(dx) < Math.abs(dy) * 1.5) return;
      stepRef.current(dx < 0 ? 1 : -1);
    };
    const cancel = () => {
      swipeStart.current = null;
    };
    // On the DOCUMENT, like the click guard above: it is on the path of every
    // release, wherever the pointer ends up, and it runs before dnd-kit's own
    // drag-end (registered later, at activation) — which is why the block is
    // read from a ref set at drag START and not from the dragging state.
    document.addEventListener("pointerup", release);
    document.addEventListener("pointercancel", cancel);
    return () => {
      document.removeEventListener("pointerup", release);
      document.removeEventListener("pointercancel", cancel);
    };
  }, []);

  // A horizontal wheel (a trackpad two-finger swipe, a tilt wheel) is the
  // desktop's swipe. One gesture is one slide, hence the cooldown.
  const lastWheel = useRef(0);
  const onSlideWheel = useCallback(
    (e: ReactWheelEvent<HTMLDivElement>) => {
      if (Math.abs(e.deltaX) <= Math.abs(e.deltaY) || Math.abs(e.deltaX) < WHEEL_MIN_PX) return;
      const now = Date.now();
      if (now - lastWheel.current < WHEEL_COOLDOWN_MS) return;
      lastWheel.current = now;
      step(e.deltaX > 0 ? 1 : -1);
    },
    [step],
  );

  // Arrow keys change slides only when the STRIP ITSELF has focus. A focused
  // tile keeps its keys: dnd-kit's KeyboardSensor moves a picked-up tile with
  // the same arrows, and flipping the screen under it would unmount the tile
  // mid-sort.
  const onSlideKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLDivElement>) => {
      if (e.target !== e.currentTarget) return;
      if (e.key === "ArrowRight") {
        e.preventDefault();
        step(1);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        step(-1);
      } else if (e.key === "Home") {
        e.preventDefault();
        goTo(0);
      } else if (e.key === "End") {
        e.preventDefault();
        goTo(allSlides.length - 1);
      }
    },
    [step, goTo, allSlides.length],
  );

  // The tab strip: arrows move between tabs (and screens) and carry focus.
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const onTabsKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLDivElement>) => {
      const delta = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!delta) return;
      e.preventDefault();
      const next = Math.min(Math.max(slide + delta, 0), allSlides.length - 1);
      goTo(next);
      tabRefs.current[next]?.focus();
    },
    [slide, goTo, allSlides.length],
  );

  // --- drag ------------------------------------------------------------------

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
      // Still ONE saved list (v1.293.0): this slide's tiles in their new
      // sequence, every other slide's tile exactly where it was.
      const next = reorderWithinSlide(flatIds, ids, from, to);
      setOrder(next);
      writeOrder(next);
    },
    [ids, flatIds, edge, popout],
  );

  const customised = order.length > 0;
  const activeTile = edge && dragging ? tiles.find((t) => t.href === activeHref) : null;
  const atFirst = slide <= 0;
  const atLast = slide >= allSlides.length - 1;

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        {/* The groups, each by its icon. The current one is lit. */}
        <div
          role="tablist"
          aria-label="Module groups"
          onKeyDown={onTabsKeyDown}
          className="flex items-center gap-0.5 rounded-full border border-white/[0.06] bg-white/[0.03] p-0.5"
        >
          {allSlides.map((s, i) => {
            const GroupIcon = s.group.icon;
            const selected = i === slide;
            return (
              <button
                key={s.group.key}
                ref={(el) => {
                  tabRefs.current[i] = el;
                }}
                type="button"
                role="tab"
                aria-selected={selected}
                tabIndex={selected ? 0 : -1}
                data-testid={`tile-group-${s.group.key}`}
                onClick={() => goTo(i)}
                className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11.5px] transition-colors ${
                  selected
                    ? "bg-accent/[0.14] text-accent-soft"
                    : "text-zinc-500 hover:text-zinc-300"
                }`}
              >
                <GroupIcon size={12} />
                {s.group.label}
                <span className={`text-[10px] ${selected ? "text-accent-soft/70" : "text-zinc-600"}`}>
                  {s.tiles.length}
                </span>
              </button>
            );
          })}
        </div>
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
          swipeBlocked.current = true;
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
        <div className="flex items-stretch gap-1">
          <button
            type="button"
            aria-label="Previous group"
            data-testid="slides-prev"
            disabled={atFirst}
            onClick={() => step(-1)}
            className="flex w-6 shrink-0 items-center justify-center rounded-lg text-zinc-600 transition-colors hover:bg-white/[0.04] hover:text-zinc-300 disabled:opacity-0"
          >
            <ChevronLeft size={16} />
          </button>
          <div
            ref={gridRef}
            role="region"
            aria-roledescription="carousel"
            aria-label={`Modules — ${current?.group.label ?? ""}`}
            tabIndex={0}
            data-testid="tile-slides"
            data-slide={current?.group.key}
            data-slide-index={slide}
            data-slide-count={allSlides.length}
            onPointerDown={onSlidePointerDown}
            onWheel={onSlideWheel}
            onKeyDown={onSlideKeyDown}
            className="min-w-0 flex-1 rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-accent/30"
            // Until localStorage is read the order is the catalogue's; fading
            // in avoids a visible re-sort on every load.
            style={{ opacity: ready ? 1 : 0, transition: "opacity 150ms" }}
          >
            <SortableContext items={ids} strategy={rectSortingStrategy}>
              {/* Keyed on the group: a change of slide mounts a fresh grid
                  that slides in from the side it came from. */}
              <m.div
                key={current?.group.key ?? "none"}
                initial={dir === 0 ? false : { x: dir * 28, opacity: 0 }}
                animate={{ x: 0, opacity: 1 }}
                transition={{ duration: 0.18, ease: EASE }}
                className="grid grid-cols-4 gap-x-2 gap-y-3 sm:grid-cols-5 lg:grid-cols-10"
              >
                {(current?.tiles ?? []).map((t) => (
                  <Tile
                    key={t.href}
                    tile={t}
                    dragging={dragging}
                    armed={edge !== null}
                    onHover={setHover}
                  />
                ))}
              </m.div>
            </SortableContext>
          </div>
          <button
            type="button"
            aria-label="Next group"
            data-testid="slides-next"
            disabled={atLast}
            onClick={() => step(1)}
            className="flex w-6 shrink-0 items-center justify-center rounded-lg text-zinc-600 transition-colors hover:bg-white/[0.04] hover:text-zinc-300 disabled:opacity-0"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </DndContext>
      {/* What this screen is for, and where you are. The dots are decoration:
          the tabs above are the control. */}
      <div className="mt-2 flex items-center justify-center gap-3">
        <div className="flex items-center gap-1" aria-hidden="true">
          {allSlides.map((s, i) => (
            <span
              key={s.group.key}
              className={`h-1.5 rounded-full transition-all ${
                i === slide ? "w-4 bg-accent/70" : "w-1.5 bg-zinc-700"
              }`}
            />
          ))}
        </div>
        <div
          data-testid="slide-hint"
          aria-live="polite"
          className="min-w-0 truncate text-[11px] text-zinc-500"
        >
          {current?.group.hint}
        </div>
      </div>
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
