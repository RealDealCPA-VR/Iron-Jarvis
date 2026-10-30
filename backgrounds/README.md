# Animated backgrounds

Five small, self-contained animated pages the desktop app can show behind its
windows: `aurora`, `particles` (Drift), `waves`, `grid-beams` (Grid) and
`reactor`. They are NOT shipped in the installer. The app downloads a file on
demand, only when the user picks it, from the release tag of the version that
is running, and checks its size and sha256 before using it.

## The contract (every `<id>/index.html`)

- One HTML file: inline `<style>` and `<script>` only, Canvas 2D, no network
  and no outside resources of any kind (no links, no `src`/`href` attributes,
  no CSS `url(...)`, no imports, no fetch / sockets / workers / eval).
- Parameters come only from `location.hash`:
  `#accent=22d3ee&second=0891b2&bg=070809&mode=dark` (six hex digits each, an
  optional leading `#` / `%23` tolerated; defaults accent `22d3ee`, second =
  accent, bg `070809`, mode `dark`). Re-read on `hashchange`.
- Colours: only `bg`, `accent` and `second`, normal `source-over` alpha
  blending, so every pixel is a mix of those three. Draw the key shapes
  strongly (alpha 0.6-1.0): the app shows the frame at 60% opacity under a
  page-colour scrim.
- Motion: one `requestAnimationFrame` loop capped at about 30 fps, rendered at
  CSS pixels (soft effects at half resolution). The parent controls it with
  `postMessage({type: "ij-bg", run: true|false})` (pause = no work at all) and
  `{type: "ij-bg", still: true|false}` (one still frame). Reduced motion and a
  hidden tab also stop the loop. `document.documentElement.dataset.state` is
  `running`, `paused` or `still`; after the first frame the page posts
  `{type: "ij-bg-ready", id}` to its parent once.
- Randomness is seeded, so the still frame is always the same picture.

## Editing a file

Editing any file here means updating its `size` and `sha256` in
`dashboard/lib/backgroundCatalog.ts` and `src/iron_jarvis/backgrounds/catalog.py`
(a test fails otherwise). The app downloads by tag, so a change reaches users
only with the release that carries it.
