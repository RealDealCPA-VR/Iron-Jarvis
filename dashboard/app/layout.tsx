import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Sidebar, MobileNav } from "@/components/Sidebar";
import { DaemonBanner } from "@/components/DaemonBanner";
import { CommandPalette } from "@/components/CommandPalette";
import { NotificationBell } from "@/components/NotificationBell";
import { MoodOrb } from "@/components/MoodOrb";
import { ModelSwitcher } from "@/components/ModelSwitcher";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import { SimulatedBanner } from "@/components/SimulatedBanner";
import { FirstRunWizard } from "@/components/FirstRunWizard";
import { MainContent } from "@/components/MainContent";
import { DaemonProvider } from "@/lib/daemon";

export const metadata: Metadata = {
  // Base title; NotificationBell mutates document.title at runtime to surface
  // pending review/approval counts.
  title: "Iron Jarvis",
  description: "Dashboard for the Iron Jarvis daemon.",
  manifest: "/manifest.webmanifest",
  applicationName: "Iron Jarvis",
  appleWebApp: {
    capable: true,
    title: "Iron Jarvis",
    statusBarStyle: "black-translucent",
  },
};

export const viewport: Viewport = {
  themeColor: "#070809",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Apply the saved arc-reactor theme BEFORE paint (no flash of the
            default palette). The ThemeSwitcher writes localStorage.ij_theme. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "try{var t=localStorage.getItem('ij_theme');if(t)document.documentElement.dataset.theme=t}catch(e){}",
          }}
        />
      </head>
      <body>
        {/* Skip past the ~34 sidebar nav links straight to page content (WCAG 2.4.1).
            Visually hidden until focused. */}
        <a
          href="#main-content"
          className="sr-only rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100]"
        >
          Skip to main content
        </a>
        <DaemonProvider>
          <div className="flex h-screen flex-col overflow-hidden">
            {/* App-wide daemon-offline banner (shared /health source). */}
            <DaemonBanner />
            <div className="relative flex flex-1 overflow-hidden">
              {/* Ambient arc-reactor glow behind everything. */}
              <div className="app-aura pointer-events-none absolute inset-0 -z-10" />
              <Sidebar />
              <main className="flex flex-1 flex-col overflow-y-auto">
                {/* Slim top bar: mobile hamburger (md:hidden) + the always-on
                    notification bell, top-right on every screen size. */}
                <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-white/[0.06] bg-ink-950/70 px-4 py-2.5 backdrop-blur-xl lg:px-10">
                  <MobileNav />
                  <div className="ml-auto flex items-center gap-2">
                    {/* Arc-reactor theme switcher (the "Marks"). */}
                    <div className="hidden sm:block">
                      <ThemeSwitcher />
                    </div>
                    {/* One-click switcher for the active provider/model. */}
                    <ModelSwitcher />
                    {/* Live "mood" orb — reflects idle / thinking / alert. */}
                    <MoodOrb />
                    <NotificationBell />
                  </div>
                </header>
                {/* Persistent "simulated mode" strip — top of the content
                    area (not over the sidebar) while no real provider is
                    connected. Deliberately non-dismissable. */}
                <SimulatedBanner />
                <MainContent>{children}</MainContent>
              </main>
            </div>
          </div>
          {/* ⌘K command palette — navigate, new session, connect a model. */}
          <CommandPalette />
          {/* Blocking first-run overlay (skips /connections + /settings so
              the user can actually go wire a model). */}
          <FirstRunWizard />
        </DaemonProvider>
      </body>
    </html>
  );
}
