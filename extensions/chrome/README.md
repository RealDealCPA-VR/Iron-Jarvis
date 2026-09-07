# The Iron Jarvis browser add-on

The Chrome/Edge half of the Browser Bridge. It lets Iron Jarvis, running on this
computer, work in the tabs you are already signed in to — no second browser, no
re-logging in, no copied cookies.

It is called **the Iron Jarvis browser add-on** everywhere a user can read it.
Never "extension": inside Iron Jarvis that word already means an MCP server, and
`VOCABULARY.md` is enforced by a test.

## What it can do in this version (v1.235.0)

Read-only, and only the outside of a page:

- report whether it is connected and whether it has site access,
- list your open tabs,
- name the tab you are looking at.

Reading page content, screenshots, clicking and typing arrive in later versions
with the content script that makes them possible. Until then the add-on answers
any other request with an error naming the method, so the daemon learns which half
is missing instead of waiting for a timeout.

## Install it (unpacked, for now)

Web Store distribution is deliberately deferred, so the add-on is loaded from this
directory.

1. Build it: `pnpm install && pnpm run check` in this folder. That verifies the
   pinned id, type-checks, and writes `dist/`.
2. In Chrome open `chrome://extensions`.
3. Turn on **Developer mode** (top right).
4. Press **Load unpacked** and select this folder — `extensions/chrome`, the one
   containing `manifest.json`, not `dist/`.
5. Open Iron Jarvis, go to the **Browser** page, and press **Pair**.
6. Press **Grant site access**. A tab opens with one button; press it and accept
   Chrome's prompt.

The id is pinned by the `key` field in `manifest.json`, so it is the same on every
machine and the daemon can admit exactly that one origin. If the id you see in
`chrome://extensions` is not `lgihfomaieifpnemakmpadmggjnoojmm`, run
`node scripts/verify-id.mjs` — it will say which of the manifest and the daemon
have parted.

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

## The popup

Status only, by design: connection state, access mode, the current tab, **Open
Jarvis**, and one button that disconnects or reconnects this browser. No chat, no
model picker, no automation controls, no tool settings, no approvals. Jarvis is
the product; the popup is a light on its dashboard.

## Layout

```
manifest.json          MV3. permissions: tabs, scripting, downloads, storage.
                       optional_host_permissions only; no host_permissions;
                       no content_scripts. "key" pins the id.
esbuild.config.mjs     background + popup + setup -> dist/, IIFE, chrome120
scripts/verify-id.mjs  recomputes the id from the manifest key; exits 1 on drift
src/protocol.ts        GENERATED from Python. Never hand-edit; see below.
src/bridge/socket.ts   the one WebSocket, pairing, backoff, token storage
src/bridge/dispatch.ts request id -> handler, concurrent in-flight commands
src/bridge/errors.ts   BrowserErrorCode -> {code, message} envelopes
src/background/        the service worker, tabs, and site access
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

## Known limits, named rather than discovered later

- **The service worker sleeps.** Manifest V3 evicts an idle worker. WebSocket
  traffic keeps an active bridge alive, but a long-silent one is unloaded and its
  socket closes; it reconnects when anything wakes the worker — browser startup,
  opening the popup, switching tabs. A timer-based keepalive would need the
  `alarms` permission, which the permission set deliberately excludes.
- **Access mode is only as fresh as the daemon's last word.** The add-on cannot
  read Iron Jarvis's `browser_access` setting itself, so the popup shows "Set in
  Jarvis" until the daemon reports a mode. It never guesses one.
- **Where Chrome saves a download cannot be changed** from here. Later versions
  report the real absolute path of a completed download; redirecting it is the
  browser's own setting.
