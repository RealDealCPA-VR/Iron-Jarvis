import type { Metadata, Viewport } from "next";
import "./globals.css";
import { NavDrawer } from "@/components/Sidebar";
import { DesktopNotifyBridge } from "@/components/DesktopNotifyBridge";
import { TitleBar } from "@/components/TitleBar";
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
// Face overrides are read ONCE here so every AgentFace in the app draws the
// user's chosen shape/colour/eyes — not only the picker that sets them
// (v1.180.0 review finding). Silent + best-effort: no route, no overrides,
// derived faces exactly as before.
import { FaceStylesProvider } from "@/components/agents/FaceStyles";

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
          <FaceStylesProvider>
          <div className="flex h-screen flex-col overflow-hidden">
            {/* Frontier-desktop chrome (v1.111.0): the TitleBar is the FIRST
                child on purpose — in the frameless Electron window its drag
                region must sit at the very top edge or the window cannot be
                dragged, and the native close/max/min overlay would float over
                whatever else rendered up here. Everything the old header held
                (theme, model, mood, bell) rides in its right slot; the
                hamburger inside it opens the NavDrawer; the search button is
                the app's front door. */}
            <TitleBar
              right={
                <>
                  {/* Arc-reactor theme switcher (the "Marks"). */}
                  <div className="hidden sm:block">
                    <ThemeSwitcher />
                  </div>
                  {/* One-click switcher for the active provider/model. */}
                  <ModelSwitcher />
                  {/* Live "mood" orb — reflects idle / thinking / alert. */}
                  <MoodOrb />
                  <NotificationBell />
                </>
              }
            />
            {/* App-wide daemon-offline banner (below the chrome, above work). */}
            <DaemonBanner />
            <div className="relative flex flex-1 overflow-hidden">
              {/* Ambient arc-reactor glow behind everything. */}
              <div className="app-aura pointer-events-none absolute inset-0 -z-10" />
              <main className="flex flex-1 flex-col overflow-y-auto">
                {/* Persistent "simulated mode" strip — top of the content
                    area while no real provider is connected. Deliberately
                    non-dismissable. */}
                <SimulatedBanner />
                <MainContent>{children}</MainContent>
              </main>
            </div>
          </div>
          {/* Navigation drawer — opened by the TitleBar hamburger
              (ij:toggle-nav); the persistent rail is gone. */}
          <NavDrawer />
          {/* Global search / command palette — the TitleBar search button
              (ij:open-palette) and Ctrl+K both open it. */}
          <CommandPalette />
          {/* "This PC" notifications: comm.desktop events → native OS toast
              via the Electron preload (no-op in a plain browser). */}
          <DesktopNotifyBridge />
          {/* Blocking first-run overlay (skips /connections + /settings so
              the user can actually go wire a model). */}
          <FirstRunWizard />
          </FaceStylesProvider>
        </DaemonProvider>
      </body>
    </html>
  );
}
