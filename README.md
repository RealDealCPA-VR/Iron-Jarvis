# updates-stable

Machine-read by the installed Iron Jarvis desktop app when its update channel is
**Stable** (Settings › System › Updates). `latest.yml` names the version Stable
offers and its installer (absolute URLs on the GitHub release).

Written only by the **Stable channel** workflow (`.github/workflows/stable.yml`),
every hour. A release reaches Stable once it has been published for a day, no
newer patch release of it (same first two numbers) has come out, its commit's
Tests workflow passed, and it is not held back. Stable never moves to an older
version.

* `holds.txt`: versions kept off Stable, one per line. Add or remove one with the
  workflow's `hold` / `unhold` inputs (Actions › Stable channel › Run workflow).
* `promoted.json`: every promotion, newest last.

The wait is time only: nothing here can see whether another PC ran a version.
Do not edit `latest.yml` by hand.
