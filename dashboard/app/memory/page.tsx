// The unified memory surface: Working / What I've learned / Long-term scopes
// on one page. `?scope=` picks the tab (working default | lessons | longterm).
//
// Memory housekeeping (v1.143.0) sits BELOW the scopes because it is about the
// memory rather than a slice of it — and because it is a queue, not a store:
// most days it is empty, and on an older daemon (no /memory/review) it renders
// nothing at all. It mounts here rather than inside MemorySurface so the
// /lessons and /ltm wrappers keep their single-scope focus.
import { MemorySurface } from "@/components/memory/MemorySurface";
import { MemoryReview } from "@/components/memory/MemoryReview";

export default function MemoryPage() {
  return (
    <>
      <MemorySurface />
      {/* PageShell's own space-y-6 stops at its children, so re-create the gap. */}
      <div className="mt-6">
        <MemoryReview />
      </div>
    </>
  );
}
