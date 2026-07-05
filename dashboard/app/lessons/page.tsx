// Deep-link wrapper: /lessons renders the unified memory surface with the
// "What I've learned" scope preselected (old links keep working).
import { MemorySurface } from "@/components/memory/MemorySurface";

export default function LessonsPage() {
  return <MemorySurface initialScope="lessons" />;
}
