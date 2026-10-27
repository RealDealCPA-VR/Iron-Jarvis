# toolbox — specification

Three small modules for our billing and release tools. They do not depend on
each other. Each section below is the whole contract of its module: what is
written here is what the module must do, including every error case.

Python 3.12, standard library only. Errors are raised as the section says
(`ValueError` for a value that is wrong, `TypeError` for an argument of the
wrong type); the messages are up to you.

---

## 1. Durations — `durations.py`

### 1.1 `parse_duration(text) -> int`

Reads a duration and returns it in whole seconds.

* A duration is one or more **parts**; a part is a number followed directly by
  a unit: `w` (week, 604800 s), `d` (day, 86400 s), `h` (hour, 3600 s), `m`
  (minute, 60 s), `s` (second). Units may be upper or lower case.
* Parts are written together or separated by spaces: `"1h30m"`, `"1h 30m"`.
  Spaces before the first part and after the last are ignored.
* Each unit appears at most once, and the units go from the largest to the
  smallest: `"30m 1h"` and `"1h 1h"` are errors.
* A number is one or more digits. Only the **last** part may have a fraction,
  written with a `.` and at least one digit on each side: `"1.5h"` is 5400,
  `"1h 1.5m"` is 3690, `".5h"`, `"5.h"` and `"1.5h 30m"` are errors. The total
  must come to a whole number of seconds: `"0.25m"` is 15 and `"0.1m"` is 6,
  but `"1.5s"` and `"0.01m"` (0.6 s) are errors.
* A text that is only a whole number, with no unit, is that many seconds:
  `"90"` is 90 (`"1.5"` with no unit is an error).
* A `-` directly before the first number makes the whole duration negative:
  `"-1h30m"` is -5400. No other sign is allowed (`"+5m"`, `"1h -30m"` are
  errors).
* A part may be zero: `"0s"` is 0, `"1h 0m"` is 3600.
* `ValueError` for anything else: an empty or blank text, an unknown unit, a
  unit without a number, a number without a unit (apart from the whole-number
  form), anything between the parts other than spaces.
* `TypeError` when `text` is not a `str`.

### 1.2 `format_duration(seconds, largest=2) -> str`

Writes a number of seconds in the units of 1.1.

* The absolute value is split into weeks, days, hours, minutes and seconds
  (each as large as it can be, in that order). The parts that are not zero are
  written from the largest unit to the smallest, each as the number followed by
  its unit, separated by one space: `5400` is `"1h 30m"`, `93600` is
  `"1d 2h"`, `604861` with `largest=3` is `"1w 1m 1s"`.
* At most `largest` parts are written. When there are more, the value is
  first ROUNDED to the unit of the last part that is written: half or more of
  that unit rounds up (`5430` with `largest=2`: 1h 30m 30s rounds to
  `"1h 31m"`). Rounding up carries into the larger units and the parts are
  written again from the rounded value: `3599` with `largest=1` is `"1h"`,
  `86370` with `largest=2` (23h 59m 30s) is `"1d"`.
* `0` is `"0s"`. A negative value is written as `-` followed by the
  absolute value's text: `-5400` is `"-1h 30m"`.
* `largest` must be an `int` from 1 to 5 (`ValueError` otherwise).
* `TypeError` when `seconds` is not an `int` (a `bool` is not accepted either).

---

## 2. Versions — `versions.py`

Semantic Versioning 2.0.0, with npm-style ranges.

### 2.1 `parse_version(text) -> Version`

* The form is `MAJOR.MINOR.PATCH`, then optionally `-` and a PRE-RELEASE,
  then optionally `+` and BUILD metadata: `1.4.0`, `2.0.0-rc.1`,
  `1.0.0-alpha+exp.sha.5114f85`. One leading `v` or `V` is allowed and
  dropped. Spaces before and after are ignored.
* MAJOR, MINOR and PATCH are digits, without a leading zero (`0` itself is
  fine, `01` is an error).
* The pre-release is one or more identifiers separated by `.`; an
  identifier is one or more of `0-9 A-Z a-z -`. An identifier made only of
  digits must not have a leading zero and is a number; any other identifier
  is text.
* The build metadata is one or more identifiers separated by `.`, each one or
  more of `0-9 A-Z a-z -` (leading zeros allowed); it is kept as text.
* `ValueError` for anything else; `TypeError` when `text` is not a `str`.

### 2.2 `Version`

* Attributes `major`, `minor`, `patch` (`int`), `prerelease` (a `tuple`: an
  `int` for each numeric identifier, a `str` for each other one; empty when
  there is none) and `build` (a `tuple` of `str`; empty when there is none).
* `str(version)` is the canonical text, without a `v`: `"1.0.0-rc.1+b.7"`.
* Versions compare with `==`, `!=`, `<`, `<=`, `>`, `>=` by PRECEDENCE, and
  can be dictionary keys:
  1. major, then minor, then patch, as numbers;
  2. a version with a pre-release is LOWER than the same version without one
     (`1.0.0-rc.1 < 1.0.0`);
  3. two pre-releases are compared identifier by identifier from the left: two
     numbers by value, two texts by ASCII order, and a number is lower than a
     text; when all the identifiers of the shorter one are equal to the
     start of the longer one, the shorter is lower
     (`1.0.0-alpha < 1.0.0-alpha.1 < 1.0.0-alpha.beta < 1.0.0-beta <
     1.0.0-beta.2 < 1.0.0-beta.11 < 1.0.0-rc.1 < 1.0.0`);
  4. build metadata is IGNORED: `1.0.0+a == 1.0.0+b`, and both have the same
     hash.

### 2.3 `matches(version, range_text) -> bool`

`version` is a `Version` or a text `parse_version` accepts. A RANGE is one or
more comparator SETS separated by `||` (spaces around `||` are allowed); the
range matches when ANY set matches. A set is one or more comparators
separated by spaces; the set matches when EVERY comparator holds.

| Comparator | Means |
|---|---|
| `1.2.3` or `=1.2.3` | exactly that precedence |
| `>1.2.3`, `>=1.2.3`, `<1.2.3`, `<=1.2.3` | that comparison |
| `*`, `x`, `X` | any version |
| `1`, `1.x`, `1.*` | `>=1.0.0 <2.0.0` |
| `1.2`, `1.2.x`, `1.2.*` | `>=1.2.0 <1.3.0` |
| `~1.2.3` | `>=1.2.3 <1.3.0` |
| `~1.2` | `>=1.2.0 <1.3.0` |
| `~1` | `>=1.0.0 <2.0.0` |
| `^1.2.3` | `>=1.2.3 <2.0.0` |
| `^0.2.3` | `>=0.2.3 <0.3.0` |
| `^0.0.3` | `>=0.0.3 <0.0.4` |
| `^1.2` | `>=1.2.0 <2.0.0` |
| `^0.2` | `>=0.2.0 <0.3.0` |
| `^0.0` | `>=0.0.0 <0.1.0` |
| `^1` | `>=1.0.0 <2.0.0` |
| `^0` | `>=0.0.0 <1.0.0` |

The version after `=`, `>`, `>=`, `<`, `<=`, `~` and `^` is written directly
after the operator and may carry a pre-release (`>=1.2.3-rc.1`,
`^1.2.3-beta.2`, which is `>=1.2.3-beta.2 <2.0.0`); after `=`, `>`, `>=`, `<`
and `<=` it must be complete (`MAJOR.MINOR.PATCH`). Build metadata in a range
is ignored.

**Pre-releases.** A version WITH a pre-release matches a set only when, besides
every comparator holding, at least one comparator OF THAT SET was written with a
pre-release on the same `MAJOR.MINOR.PATCH`: `1.2.4-rc.1` matches
`>=1.2.4-rc.0 <1.3.0` but not `>=1.2.0 <1.3.0`; `1.3.0-beta` does not match
`^1.2.3`; `1.2.3-beta.5` matches `^1.2.3-beta.2`. (A version without a
pre-release only has to satisfy the comparators.)

`ValueError` when the range or the version is malformed; `TypeError` when
`range_text` is not a `str`.

---

## 3. Allocation — `allocate.py`

Money is an `int` number of cents everywhere in this module (`TypeError` for
anything else, `bool` included).

### 3.1 `allocate(total_cents, weights) -> list[int]`

Splits `total_cents` into one part per weight, in proportion to the weights.

* Weights are `int` or `decimal.Decimal` (`TypeError` for any other type —
  never a `float`), none negative (`ValueError`), at least one weight
  (`ValueError` for an empty list) and not all zero (`ValueError`).
* Each part first gets its exact share of the ABSOLUTE total, rounded down to
  a whole cent. The cents still missing then go one at a time to the parts
  whose exact share had the LARGEST remainder (the fraction cut off); when two
  remainders are equal, the earlier part goes first. A part with weight 0 gets
  0.
* The parts always add up to the total exactly.
* A negative total is split as its absolute value, and then every part is
  made negative: `allocate(-100, [1, 2])` is `[-33, -67]`.

### 3.2 `split_evenly(total_cents, n) -> list[int]`

`allocate` with `n` equal weights: the cents that do not divide evenly go to
the first parts. `n` is an `int` of at least 1 (`ValueError` otherwise).

### 3.3 `allocate_percent(total_cents, percents) -> list[int]`

`allocate` with the percentages as weights. Each percentage is an `int`, a
`Decimal`, or a `str` that `Decimal` reads (`"33.3"`); they must add up to
exactly 100 (`ValueError` otherwise).

### 3.4 `format_cents(cents) -> str`

Dollars with a thousands separator and two decimals: `123456` is
`"$1,234.56"`, `5` is `"$0.05"`, `0` is `"$0.00"`, `-123456` is
`"-$1,234.56"`.
