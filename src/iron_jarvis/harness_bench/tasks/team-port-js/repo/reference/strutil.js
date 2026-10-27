// strutil.js - the string helpers of our web front end.
//
// This file is the REFERENCE: the Python package `strutil` must give exactly
// the same results for every input (see PORTING.md). It is kept here to read;
// nothing in this folder runs it.
"use strict";

// ---------------------------------------------------------------------------
// formatBytes
// ---------------------------------------------------------------------------

const SIZE_UNITS = {
  decimal: ["B", "kB", "MB", "GB", "TB", "PB"],
  binary: ["B", "KiB", "MiB", "GiB", "TiB", "PiB"],
};

/**
 * A byte count in the largest unit that keeps the number at 1 or more:
 * formatBytes(1500) -> "1.5 kB", formatBytes(1536, { binary: true }) -> "1.5 KiB".
 * Options: decimals (default 1, 0 to 6), binary (default false: units of 1000).
 */
function formatBytes(bytes, options = {}) {
  const decimals = options.decimals === undefined ? 1 : options.decimals;
  const binary = Boolean(options.binary);
  if (typeof bytes !== "number" || !Number.isFinite(bytes)) {
    throw new TypeError("bytes must be a finite number");
  }
  if (!Number.isInteger(decimals) || decimals < 0 || decimals > 6) {
    throw new RangeError("decimals must be an integer from 0 to 6");
  }
  const base = binary ? 1024 : 1000;
  const units = binary ? SIZE_UNITS.binary : SIZE_UNITS.decimal;
  const sign = bytes < 0 ? "-" : "";
  let value = Math.abs(bytes);
  if (value < base) {
    return sign + String(value) + " B";
  }
  let unit = 0;
  while (value >= base && unit < units.length - 1) {
    value /= base;
    unit += 1;
  }
  let text = value.toFixed(decimals);
  // Rounding can reach the next unit ("1000.0 kB"): move up once more.
  if (Number(text) >= base && unit < units.length - 1) {
    value /= base;
    unit += 1;
    text = value.toFixed(decimals);
  }
  return sign + text + " " + units[unit];
}

// ---------------------------------------------------------------------------
// parseQuery
// ---------------------------------------------------------------------------

function decodeComponent(text) {
  const spaced = text.replace(/\+/g, " ");
  try {
    return decodeURIComponent(spaced);
  } catch (err) {
    // A malformed escape: the component is kept as it was written.
    return spaced;
  }
}

/**
 * The parameters of a query string: parseQuery("?a=1&b=x+y") -> { a: "1", b: "x y" }.
 * A name given more than once, or written "name[]", collects its values in a list.
 */
function parseQuery(query) {
  if (typeof query !== "string") {
    throw new TypeError("query must be a string");
  }
  let text = query;
  if (text.startsWith("?")) text = text.slice(1);
  const hash = text.indexOf("#");
  if (hash !== -1) text = text.slice(0, hash);
  const result = {};
  for (const part of text.split("&")) {
    if (part === "") continue;
    const eq = part.indexOf("=");
    const key = decodeComponent(eq === -1 ? part : part.slice(0, eq));
    const value = eq === -1 ? "" : decodeComponent(part.slice(eq + 1));
    if (key === "") continue;
    if (key.endsWith("[]")) {
      const name = key.slice(0, -2);
      if (!Object.prototype.hasOwnProperty.call(result, name)) result[name] = [];
      else if (!Array.isArray(result[name])) result[name] = [result[name]];
      result[name].push(value);
    } else if (Object.prototype.hasOwnProperty.call(result, key)) {
      if (!Array.isArray(result[key])) result[key] = [result[key]];
      result[key].push(value);
    } else {
      result[key] = value;
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// naturalCompare / naturalSort
// ---------------------------------------------------------------------------

function chunks(text) {
  return text.match(/[0-9]+|[^0-9]+/g) || [];
}

/**
 * Compares two strings the way people sort file names: runs of digits by
 * their value ("file9" before "file10"), letters without regard to case.
 * Returns -1, 0 or 1.
 */
function naturalCompare(a, b) {
  if (typeof a !== "string" || typeof b !== "string") {
    throw new TypeError("naturalCompare compares strings");
  }
  const left = chunks(a);
  const right = chunks(b);
  const n = Math.min(left.length, right.length);
  for (let i = 0; i < n; i++) {
    const x = left[i];
    const y = right[i];
    const xNum = /^[0-9]/.test(x);
    const yNum = /^[0-9]/.test(y);
    if (xNum && yNum) {
      const xs = x.replace(/^0+(?=.)/, "");
      const ys = y.replace(/^0+(?=.)/, "");
      if (xs.length !== ys.length) return xs.length < ys.length ? -1 : 1;
      if (xs !== ys) return xs < ys ? -1 : 1;
      // Same value: fewer leading zeros first ("7" before "007").
      if (x.length !== y.length) return x.length < y.length ? -1 : 1;
    } else if (xNum !== yNum) {
      return xNum ? -1 : 1;
    } else {
      const xl = x.toLowerCase();
      const yl = y.toLowerCase();
      if (xl !== yl) return xl < yl ? -1 : 1;
    }
  }
  if (left.length !== right.length) return left.length < right.length ? -1 : 1;
  if (a === b) return 0;
  return a < b ? -1 : 1;
}

/** A new array of the items in natural order (the sort is stable). */
function naturalSort(items) {
  return [...items].sort(naturalCompare);
}

// ---------------------------------------------------------------------------
// wrapText
// ---------------------------------------------------------------------------

/**
 * Wraps text to lines of at most `width` characters. Words are separated by
 * spaces; a line break in the text starts a new paragraph. A word longer than
 * the width is cut, starting on the current line when there is room.
 */
function wrapText(text, width) {
  if (typeof text !== "string") {
    throw new TypeError("text must be a string");
  }
  if (!Number.isInteger(width) || width < 1) {
    throw new RangeError("width must be a positive integer");
  }
  const lines = [];
  for (const paragraph of text.split("\n")) {
    const words = paragraph.split(" ").filter((w) => w !== "");
    let line = "";
    for (let word of words) {
      while (word.length > width) {
        const room = line === "" ? width : width - line.length - 1;
        if (room > 0) {
          lines.push(line === "" ? word.slice(0, room) : line + " " + word.slice(0, room));
          word = word.slice(room);
        } else {
          lines.push(line);
        }
        line = "";
      }
      if (word === "") continue;
      if (line === "") line = word;
      else if (line.length + 1 + word.length <= width) line += " " + word;
      else {
        lines.push(line);
        line = word;
      }
    }
    lines.push(line);
  }
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// titleCase
// ---------------------------------------------------------------------------

const SMALL_WORDS = new Set([
  "a", "an", "and", "as", "at", "but", "by", "en", "for", "if", "in", "nor",
  "of", "on", "or", "per", "the", "to", "v", "vs", "via",
]);

function capitalize(part) {
  return part.charAt(0).toUpperCase() + part.slice(1);
}

/**
 * Title Case for headlines: "the lord of the rings" -> "The Lord of the Rings".
 * Small words stay lower case except at the start of a phrase and at the end;
 * a word that is already mixed case or holds a digit ("iPhone", "x86") is
 * kept as written; the spacing between words is kept.
 */
function titleCase(text) {
  if (typeof text !== "string") {
    throw new TypeError("text must be a string");
  }
  const tokens = text.split(/(\s+)/);
  let last = -1;
  tokens.forEach((t, i) => {
    if (t !== "" && !/^\s+$/.test(t)) last = i;
  });
  let startsPhrase = true;
  return tokens
    .map((token, i) => {
      if (token === "" || /^\s+$/.test(token)) return token;
      let out;
      if (/[A-Z]/.test(token.slice(1)) || /[0-9]/.test(token)) {
        out = token;
      } else {
        const lower = token.toLowerCase();
        const bare = lower.replace(/^[^a-z]+|[^a-z]+$/g, "");
        if (!startsPhrase && i !== last && SMALL_WORDS.has(bare)) out = lower;
        else out = lower.split("-").map(capitalize).join("-");
      }
      startsPhrase = /[:.!?]$/.test(token);
      return out;
    })
    .join("");
}

module.exports = { formatBytes, parseQuery, naturalCompare, naturalSort, wrapText, titleCase };
