import { SettingsHome } from "@/components/settings/SettingsHome";

/**
 * Settings (calm UI redesign S10): seven groups drawn from the one settings
 * schema — the declaration the chat's settings tools come from. The page that
 * lived here before is components/settings/pages/LegacySettingsForm.tsx, kept
 * for a daemon with no schema.
 */
export default function SettingsPage() {
  return <SettingsHome />;
}
