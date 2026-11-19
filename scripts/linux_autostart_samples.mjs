#!/usr/bin/env node
// Sample "start at login" entries for the REAL Linux parsers (v1.540.0,
// fourth review LX4).
//
// WHY. The Linux app's autostart entry (~/.config/autostart/iron-jarvis.desktop)
// names the app's own path inside Exec=. The desktop-entry rules read that
// value through three layers (string escape, then Exec quoting, then %
// field codes). The node pins in tests/test_fourth_review_linux_desktop_v1540.py
// check the writer against a reader written from the spec; this script makes
// the files so the readers the desktops actually use can be run on Linux:
// desktop-file-validate, GLib (GNOME, Cinnamon, MATE, XFCE) and systemd's
// autostart generator (Plasma, and GNOME under systemd).
//
// WHAT. The entry text is made by the SHIPPED functions, lifted from
// desktop/main.js (desktopEntryExecArg, linuxAutostartEntry), never a copy.
// For each odd path (spaces, quotes, $, a backtick, a backslash, %, an
// apostrophe, letters outside ASCII, and all of them at once) it writes:
//   <out>/cases/<id>/autostart/iron-jarvis.desktop   the entry (XDG_CONFIG_HOME=<out>/cases/<id>)
//   <out>/targets/<id>/<odd folder>/Iron-Jarvis.AppImage
//       a recorder: a /bin/sh script AT the odd path that writes its own path
//       ($0) and each argument, one per line, to <out>/markers/<id>.txt
// plus two controls:
//   missing     an entry naming a path that does not exist (no recorder):
//               the systemd generator must make NO unit for it, which proves
//               its "does the program exist" check is live on that machine
//   line_break  a path with a line break: the writer refuses it (no entry)
// and <out>/cases.json (what each case expects) and <out>/glib_launch.py.
//
// USAGE
//   node scripts/linux_autostart_samples.mjs --out <dir> [--no-files]
// Recorders are made on Linux only (Windows cannot name a file with a quote);
// --no-files skips them anywhere.
//
// ON A LINUX RUNNER (Ubuntu), after
//   sudo apt-get install -y --no-install-recommends desktop-file-utils python3-gi gir1.2-glib-2.0
// for each case in cases.json with expect "starts":
//   desktop-file-validate <entry>                     exit 0, no "error:" line
//   /usr/bin/python3 -I <out>/glib_launch.py <entry>  exit 0; then the marker
//                                                     reads exactly <target>, then --hidden
//   g=$(mktemp -d); XDG_CONFIG_HOME=<out>/cases/<id> XDG_CONFIG_DIRS=$(mktemp -d) \
//     /usr/lib/systemd/user-generators/systemd-xdg-autostart-generator "$g" "$g" "$g"
//   -> "$g/app-iron\x2djarvis@autostart.service" exists and holds --hidden
// and for "missing" the same generator run makes NO such unit.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const MAIN = path.join(here, "..", "desktop", "main.js");

function liftFunction(src, name) {
  const at = src.search(new RegExp(`^function ${name}\\(`, "m"));
  if (at < 0) throw new Error(`desktop/main.js has no function ${name}`);
  return src.slice(at, src.indexOf("\n}\n", at) + 3);
}

function shipped() {
  const src = fs.readFileSync(MAIN, "utf8").replace(/\r\n/g, "\n");
  const code = liftFunction(src, "desktopEntryExecArg") + liftFunction(src, "linuxAutostartEntry");
  return new Function(`${code}\nreturn { desktopEntryExecArg, linuxAutostartEntry };`)();
}

function args(argv) {
  const out = { out: "", files: process.platform === "linux" };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--out") out.out = argv[++i] || "";
    else if (argv[i] === "--no-files") out.files = false;
    else throw new Error(`unknown argument ${argv[i]}`);
  }
  if (!out.out) throw new Error("usage: node scripts/linux_autostart_samples.mjs --out <dir> [--no-files]");
  return out;
}

// The odd folder each case's program sits in.
const ODD = [
  ["spaces", "My Apps"],
  ["quotes", 'My "Apps"'],
  ["dollar", "$HOME"],
  ["backtick", "`x`"],
  ["backslash", "back\\slash"],
  ["percent", "100%f"],
  ["apostrophe", "it's"],
  ["non_ascii", "Ünï"],
  ["everything", "a \"b\" $c `d` e\\f 100%g it's Ü"],
];

const sh1 = (s) => "'" + s.replace(/'/g, "'\\''") + "'"; // one single-quoted sh word

const GLIB_LAUNCH = `#!/usr/bin/python3
# Launch one desktop entry the way GLib does (GNOME, Cinnamon, MATE, XFCE)
# and wait for the program it starts. Written by linux_autostart_samples.mjs.
# Usage: /usr/bin/python3 -I glib_launch.py <entry.desktop>
import os
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

info = Gio.DesktopAppInfo.new_from_filename(sys.argv[1])
if info is None:
    print("GLib could not read the entry: " + sys.argv[1])
    sys.exit(2)
pids = []


def got_pid(_appinfo, pid, *_data):
    pids.append(pid)


info.launch_uris_as_manager([], None, GLib.SpawnFlags.DO_NOT_REAP_CHILD, None, None, got_pid, None)
if len(pids) != 1:
    print("GLib started %d programs" % len(pids))
    sys.exit(3)
_, status = os.waitpid(pids[0], 0)
sys.exit(os.waitstatus_to_exitcode(status))
`;

function main() {
  const opts = args(process.argv.slice(2));
  const { linuxAutostartEntry } = shipped();
  const out = path.resolve(opts.out);
  fs.mkdirSync(path.join(out, "markers"), { recursive: true });
  const cases = [];
  const rel = (p) => path.relative(out, p).split(path.sep).join("/");

  const writeEntry = (id, target) => {
    const text = linuxAutostartEntry(target);
    if (text === null) return null;
    const entry = path.join(out, "cases", id, "autostart", "iron-jarvis.desktop");
    fs.mkdirSync(path.dirname(entry), { recursive: true });
    fs.writeFileSync(entry, text);
    return rel(entry);
  };

  for (const [id, folder] of ODD) {
    // Built with "/" whatever runs this: the entry names a Linux path.
    const target = `${out.split(path.sep).join("/")}/targets/${id}/${folder}/Iron-Jarvis.AppImage`;
    const marker = path.join(out, "markers", `${id}.txt`);
    let recorder = null;
    if (opts.files) {
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, `#!/bin/sh\nprintf '%s\\n' "$0" "$@" > ${sh1(marker)}\n`, { mode: 0o755 });
      recorder = rel(target);
    }
    cases.push({ id, target, entry: writeEntry(id, target), recorder, marker: rel(marker), expect: "starts", args: ["--hidden"] });
  }

  const missing = `${out.split(path.sep).join("/")}/targets/missing/not here/Iron-Jarvis.AppImage`;
  cases.push({ id: "missing", target: missing, entry: writeEntry("missing", missing), recorder: null, marker: null, expect: "no-unit" });

  const broken = `${out.split(path.sep).join("/")}/targets/line_break/two\nlines/Iron-Jarvis.AppImage`;
  cases.push({ id: "line_break", target: broken, entry: writeEntry("line_break", broken), recorder: null, marker: null, expect: "refused" });

  fs.writeFileSync(path.join(out, "glib_launch.py"), GLIB_LAUNCH);
  fs.writeFileSync(path.join(out, "cases.json"), JSON.stringify({ cases }, null, 2) + "\n");
  process.stdout.write(`wrote ${cases.length} cases to ${out} (recorders: ${opts.files ? "yes" : "no"})\n`);
}

main();
