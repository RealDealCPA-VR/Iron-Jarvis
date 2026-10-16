#!/usr/bin/env node
// Render raw terminal bytes the way a Build pane does and print the result as
// JSON (v1.338.0, the update rehearsal). Uses @xterm/headless at the SAME
// version as the @xterm/xterm the pane runs, so "what the user would see" is
// read off the real parser, not a guess.
//
//   node scripts/render-terminal.cjs <bytes-file> <cols> <rows>
//
// Prints {cursor: {row, col}, screen: [visible rows], lines: [the visible
// rows with soft-wrapped ones joined back into the line the user typed],
// scrollback: [rows above the screen]} — rows and cols 1-based, right-trimmed.
const fs = require("node:fs");
const { Terminal } = require("@xterm/headless");

const [file, cols, rows] = [process.argv[2], Number(process.argv[3]), Number(process.argv[4])];
const term = new Terminal({ cols, rows, scrollback: 10000, allowProposedApi: true });
term.write(fs.readFileSync(file), () => {
  const buf = term.buffer.active;
  const line = (i) => {
    const l = buf.getLine(i);
    return l ? l.translateToString(true) : "";
  };
  const screen = [];
  for (let i = 0; i < rows; i++) screen.push(line(buf.viewportY + i));
  // A row the terminal soft-wrapped continues the one above it: join them
  // (untrimmed, so a space at the wrap point survives).
  const lines = [];
  for (let i = 0; i < rows; i++) {
    const row = buf.getLine(buf.viewportY + i);
    if (!row) continue;
    if (row.isWrapped && lines.length) {
      lines[lines.length - 1] = lines[lines.length - 1].padEnd(cols) + row.translateToString(true);
    } else {
      lines.push(row.translateToString(true));
    }
  }
  const scrollback = [];
  for (let i = 0; i < buf.viewportY; i++) scrollback.push(line(i));
  process.stdout.write(
    JSON.stringify({ cursor: { row: buf.cursorY + 1, col: buf.cursorX + 1 }, screen, lines, scrollback }),
  );
});
