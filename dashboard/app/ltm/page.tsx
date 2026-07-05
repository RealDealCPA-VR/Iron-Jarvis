// Deep-link wrapper: /ltm renders the unified memory surface with the
// Long-term scope preselected (old links keep working).
import { MemorySurface } from "@/components/memory/MemorySurface";

export default function LtmPage() {
  return <MemorySurface initialScope="longterm" />;
}
