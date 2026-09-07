# The Iron Jarvis browser add-on

This folder is the Chrome/Edge half of Iron Jarvis's Browser feature. Loading it
lets Iron Jarvis, running on this computer, work in the tabs you are already
signed in to — no second browser, no re-logging in, no copied cookies.

It is called **the Iron Jarvis browser add-on** everywhere a user can read it.
Never "extension": inside Iron Jarvis that word already means an MCP server.

**If you just want to load it, read the next section and stop there.** Everything
after *Site access* is for people working on the add-on's source.

## Load it into Chrome

You are most likely reading this file from inside your Iron Jarvis installation,
because the installer ships this whole folder with the app. That is the folder to
load — the one this README is sitting in, beside `manifest.json`. On a default
Windows install it is:

```
%LOCALAPPDATA%\Programs\Iron Jarvis\resources\browser-addon
```

If you chose a different location when you installed Iron Jarvis, it is
`resources\browser-addon` underneath the folder you chose.

1. In Chrome or Edge, open `chrome://extensions`.
2. Turn on **Developer mode**, top right.
3. Press **Load unpacked** and select this folder — the one holding
   `manifest.json`, not `dist/`.
4. Open Iron Jarvis, go to the **Browser** page, and press **Pair**.
5. Press **Grant site access**. A tab opens with one button; press it and accept
   Chrome's prompt.

**Chrome or Edge 120 or newer.** `manifest.json` declares 120 as the floor, so an
older browser refuses the folder and says why.

There is no Web Store listing yet, which is why this is loaded unpacked. Chrome
may warn you about developer-mode add-ons and may ask again each time it starts;
that is Chrome being careful about software it did not distribute, and it cannot
be switched off from this side.

The add-on's id is pinned by the `key` field in `manifest.json`, so it is the
same on every machine and the daemon admits exactly that one origin. If
`chrome://extensions` shows an id other than `lgihfomaieifpnemakmpadmggjnoojmm`,
the folder you loaded is not the one that shipped with your Iron Jarvis.

## What it can do

The add-on is the transport. What Iron Jarvis may actually do through it is set
by the **Browser access** control on the Browser page, which ships **off**:

- **Off** — nothing at all. No browser tool exists.
- **Read only** — report whether it is connected and has site access, list your
  open tabs, name the tab you are looking at, read the text and structure of a
  page, and screenshot the visible part of it.
- **Interactive** — all of the above, plus acting on the page: click, type, press
  a key, scroll, navigate, and activate, open or close a tab. **Every one of
  those asks you first**, and the four that change a page ask again whenever the
  target looks destructive or transactional, is marked sensitive, or cannot be
  identified well enough to judge.

No field's value is ever collected, password or not, and the stripping happens
inside the page before anything is sent. Whatever Iron Jarvis types is redacted
in its own records.

When Iron Jarvis clicks something that starts a download, Chrome saves it exactly
where your own Chrome settings say, and the add-on reports the completed file's
real path back so Iron Jarvis can read it or copy it into a project. Where Chrome
saves a download cannot be changed from here — that is Chrome's own setting.

`docs/BROWSER.md`, which the Iron Jarvis Guide answers from, covers all of this
at length. Ask the Guide about the browser and it reads from that file.

## Site access, and why it is a separate page

The add-on installs with **no** access to any site. `manifest.json` declares
`http://*/*` and `https://*/*` as *optional* host permissions, and nothing is
granted until you press the button on the setup page.

That page exists because of a hard Chrome rule: `chrome.permissions.request()`
must be called inside a user gesture and cannot run in a service worker. Iron
Jarvis is a page on another origin and cannot call it either. So the daemon asks
the add-on to open the setup page, and the button there makes the call. The popup
gains nothing from this: it stays status-only.

Without the grant, Chrome hands the add-on tabs whose title and URL are empty
strings. The add-on reports those as `null` with `needs_host_permission: true`
rather than as empty text, because "this tab has no title" is a lie and "not
readable yet, and here is why" is not.

You can narrow the grant afterwards in Chrome's own controls
(`chrome://extensions` → this add-on → **Site access**). Iron Jarvis cannot grant
or narrow it per site from its own screens.

## The popup

Status only, by design: connection state, access mode, the current tab, **Open
Jarvis**, and one button that disconnects or reconnects this browser. No chat, no
model picker, no automation controls, no tool settings, no approvals. Jarvis is
the product; the popup is a light on its dashboard.

## Known limits, named rather than discovered later

- **The service worker sleeps.** Manifest V3 evicts an idle worker. WebSocket
  traffic keeps an active bridge alive, but a long-silent one is unloaded and its
  socket closes, so the Browser card reads **Paired — not running** with the
  browser plainly open. It is not broken and needs no re-pairing: it reconnects
  when anything wakes the worker — browser startup, opening the popup, switching
  tabs. A timer-based keepalive would need the `alarms` permission, which the
  permission set deliberately excludes.
- **Access mode is only as fresh as the daemon's last word.** The add-on cannot
  read Iron Jarvis's `browser_access` setting itself, so the popup shows "Set in
  Jarvis" until the daemon reports a mode. It never guesses one.
- **Where Chrome saves a download cannot be changed** from here. The completed
  file's real path is reported; redirecting it is the browser's own setting.
- **One browser at a time.** A newer connection replaces the older one, and the
  older one is told why.
- **Only the top document of a tab is read.** Nothing inside an `<iframe>` is in
  the snapshot, this site's or another's. An open shadow root is walked; a closed
  one cannot even be counted.

---

# Working on the add-on's source

*The rest of this file applies to the `extensions/chrome` folder of the Iron
Jarvis repository. The copy bundled into the installer carries only
`manifest.json`, this README and `dist/`, so none of the commands below exist
there — and none of them are needed there, because `dist/` is already built.*

## Build it

`dist/` is deliberately not committed, and `manifest.json` points at
`dist/background.js`, so **Load unpacked** on a fresh checkout fails with
Chrome's "Could not load background script" until you build:

```bash
pnpm install && pnpm run check
```

in `extensions/chrome`. That verifies the pinned id, type-checks, and writes
`dist/`. Then load `extensions/chrome` itself — the folder holding
`manifest.json`, not `dist/`.

If `node scripts/verify-id.mjs` reports a mismatch, it will say which of the
manifest and the daemon have parted.

## Layout

```
manifest.json          MV3. permissions: tabs, scripting, downloads, storage.
                       optional_host_permissions only; no host_permissions;
                       no content_scripts. "key" pins the id.
esbuild.config.mjs     background + content + popup + setup -> dist/, IIFE, chrome120
scripts/build.mjs      verify-id, typecheck, build, then verify dist/ is complete
scripts/verify-id.mjs  recomputes the id from the manifest key; exits 1 on drift
src/protocol.ts        GENERATED from Python. Never hand-edit; see below.
src/bridge/socket.ts   the one WebSocket, pairing, backoff, token storage
src/bridge/dispatch.ts request id -> handler, concurrent in-flight commands
src/bridge/errors.ts   BrowserErrorCode -> {code, message} envelopes
src/background/        the service worker, tabs, downloads, and site access
src/content/           the page snapshot, injected per read
src/setup/             the grant surface: one button
src/popup/             status only
```

## `src/protocol.ts` is generated

`src/iron_jarvis/browser/protocol.py` is the single source of truth for the wire:
every frame type, method name, error code and remedy string. The TypeScript is
written from it by

```bash
uv run python -m iron_jarvis.browser.gen_protocol
```

and `tests/test_browser_protocol_v1235.py` regenerates it into a buffer and
compares byte for byte. A hand edit here is reverted by the next generation and
fails the release gate first. Change the Python, regenerate, commit both.

## Commands

```bash
pnpm install          # esbuild + typescript + @types/chrome, nothing at runtime
pnpm run verify-id    # the pinned-id check
pnpm run typecheck    # tsc --noEmit
pnpm run build        # writes dist/
pnpm run check        # all three, in that order
```

`dist/`, `node_modules/` and `*.pem` are ignored. The signing key's public half
lives in `manifest.json` on purpose; its private half is never in this repository
and is needed only to produce a `.crx` for Web Store distribution.
