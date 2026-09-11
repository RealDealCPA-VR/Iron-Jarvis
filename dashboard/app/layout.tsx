import type { Metadata, Viewport } from "next";
import "./globals.css";
import { DesktopNotifyBridge } from "@/components/DesktopNotifyBridge";
import { TitleBar } from "@/components/TitleBar";
import { DaemonBanner } from "@/components/DaemonBanner";
import { NotificationBell } from "@/components/NotificationBell";
import { MoodOrb } from "@/components/MoodOrb";
import { ModelSwitcher } from "@/components/ModelSwitcher";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import { SimulatedBanner } from "@/components/SimulatedBanner";
import { MainContent } from "@/components/MainContent";
import { DaemonProvider } from "@/lib/daemon";
// ONE /events socket per window (v1.230.0, FP6): every useEvents hook in
// the tree fans out of this provider instead of opening its own.
import { EventsProvider } from "@/lib/useEvents";
// Face overrides are read ONCE here so every AgentFace in the app draws the
// user's chosen shape/colour/eyes — not only the picker that sets them
// (v1.180.0 review finding). Silent + best-effort: no route, no overrides,
// derived faces exactly as before.
import { FaceStylesProvider } from "@/components/agents/FaceStyles";
// Animation features for every `m.*` in the app (v1.250.0, S-08).
import { MotionProvider } from "@/components/MotionProvider";
// The three OVERLAYS are loaded on demand (v1.250.0, S-08). Each one is closed
// on arrival — the drawer, the palette and the first-run wizard render nothing
// until something opens them — but their code (and framer-motion, and the
// palette's whole search surface) sat in the chunk every one of the 43 routes
// downloads before first paint. `Overlays` imports them through next/dynamic
// and PREFETCHES on idle, so Ctrl+K is still instant: by the time a user can
// press it the chunk is already in memory, and a press that beats the idle
// callback simply awaits the same import.
import { Overlays } from "@/components/Overlays";

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
          <EventsProvider>
          <FaceStylesProvider>
          <MotionProvider>
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
          {/* The navigation drawer (ij:toggle-nav), the global search palette
              (ij:open-palette / Ctrl+K) and the blocking first-run overlay —
              all three on demand, prefetched on idle. */}
          <Overlays />
          {/* "This PC" notifications: comm.desktop events → native OS toast
              via the Electron preload (no-op in a plain browser). */}
          <DesktopNotifyBridge />
          </MotionProvider>
          </FaceStylesProvider>
          </EventsProvider>
        </DaemonProvider>
      </body>
    </html>
  );
}
