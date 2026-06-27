import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Sidebar, MobileNav } from "@/components/Sidebar";
import { DaemonBanner } from "@/components/DaemonBanner";
import { CommandPalette } from "@/components/CommandPalette";
import { NotificationBell } from "@/components/NotificationBell";
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
    <html lang="en">
      <body>
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
                  <div className="ml-auto">
                    <NotificationBell />
                  </div>
                </header>
                <div className="mx-auto w-full max-w-7xl px-6 py-8 lg:px-10">{children}</div>
              </main>
            </div>
          </div>
          {/* ⌘K command palette — navigate, new session, connect a model. */}
          <CommandPalette />
        </DaemonProvider>
      </body>
    </html>
  );
}
