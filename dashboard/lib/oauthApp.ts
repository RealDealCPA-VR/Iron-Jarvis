/**
 * The app a Drive / OneDrive / Dropbox sign-in needs (v1.449.0, readiness
 * Wave C item 22).
 *
 * Those three sign in through an OAuth app the USER registers in the vendor's
 * own console (there is no public client to ride for storage). Until this
 * file the cards told the user to open Secrets and save the app's id under
 * the exact name `google_drive_oauth_client_id` — one typo and the sign-in
 * kept failing. The card now holds the boxes itself and saves under the
 * names the daemon reads, so nobody types an internal name.
 *
 * THE NAMES ARE THE DAEMON'S: `platform._oauth_app` reads
 * `<provider>_oauth_client_id` / `<provider>_oauth_client_secret` and, with
 * a client id saved, redirects to `http://localhost:8787/oauth/<provider>/callback`
 * (the daemon's own callback on the packaged port). A Python pin
 * (`tests/test_oauth_app_boxes_v14490.py`) holds the three equal to this
 * file — change them together.
 *
 * Import-free on purpose (the two pages and the tests read it).
 */

/** The vault name the daemon reads the app's id from. */
export function oauthClientIdName(provider: string): string {
  return `${provider}_oauth_client_id`;
}

/** The vault name the daemon reads the app's secret from. */
export function oauthClientSecretName(provider: string): string {
  return `${provider}_oauth_client_secret`;
}

/** The redirect address the user must allow in the vendor's console: the
 *  daemon's own callback, on the port the packaged app listens on. */
export function oauthRedirectAddress(provider: string): string {
  return `http://localhost:8787/oauth/${provider}/callback`;
}

/** The vendor's console (and its name) for each app's registration. */
export const VENDOR_CONSOLES: Record<string, { name: string; url: string; where: string }> = {
  google_drive: {
    name: "Google",
    url: "https://console.cloud.google.com/apis/credentials",
    where: "Google Cloud Console, as a “Desktop app” OAuth client",
  },
  onedrive: {
    name: "Microsoft",
    url: "https://portal.azure.com",
    where: "Microsoft Entra (the Azure portal), with a “Mobile and desktop” platform",
  },
  dropbox: {
    name: "Dropbox",
    url: "https://www.dropbox.com/developers/apps",
    where: "the Dropbox App Console",
  },
};

/** The one sentence that says the vendor-console step in plain words. */
export function vendorConsoleSentence(provider: string, appName: string): string {
  const c = VENDOR_CONSOLES[provider];
  const who = c?.name ?? appName;
  const where = c ? ` in ${c.where}` : " in its developer console";
  return (
    `${who} needs an app of your own before it lets Iron Jarvis sign in: make one${where}, ` +
    "allow the redirect address below, then paste the app’s ID and secret here. About 5 minutes, once."
  );
}
