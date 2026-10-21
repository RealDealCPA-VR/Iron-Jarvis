# History

Each of these was a bug report. The fix is in `versions.py`, and the example
must keep working — every one of them broke again at least once when someone
"simplified" the comparison.

* #12 — `compare("1.2.10", "1.2.10")` must be 0 (identical strings).
* #31 — a pre-release comes BEFORE its release: `1.0.0-rc1` < `1.0.0`.
* #33 — pre-releases order alpha < beta < rc, and `a`/`b` are short for
  alpha/beta: `1.0-a1` == `1.0-alpha1`, `2.0-b` < `2.0-rc`.
* #35 — the pre-release number is a NUMBER: `1.0.0-rc2` < `1.0.0-rc10`, and
  `rc.3` == `rc3`.
* #40 — a leading `v` or `V` and surrounding spaces are ignored:
  `" V2.0 "` == `"2.0"`.
* #44 — trailing zero parts do not matter: `1.0` == `1.0.0` == `1`.
* #47 — build metadata after `+` is ignored: `1.0.0+build.5` == `1.0.0`.
* #51 — a version that is not a version raises `ValueError` (for example
  `"1.x"`, `""`, `"1..2"` or `"1.0-gamma"`) — never a wrong answer.
