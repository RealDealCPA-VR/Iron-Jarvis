"""Generate the light-tone override block in dashboard/app/globals.css (v1.313.0).

Scans app/, components/, lib/ exactly like __tests__/ux-wave1-design-v1313.test.tsx
(pale text <= 400 always; border/ring 300-500 alpha <= .5; bg 300-500 alpha < .5)
and also emits the hover:/group-hover: variants in use, so a hover that changes
the hue still changes it on every light theme.

Usage (from the repo root): uv run python scripts/gen_light_tones.py
It rewrites ONLY the text between the light-tone-overrides markers, keeps the
file's own line endings, and prints the lowest text contrast it produced.
"""
import os
import re
import sys


def _lum(c):
    def f(v):
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = c
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    x, y = _lum(a), _lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


def over(fg, a, bg):
    return tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3))


# v1.317.0: one selector for EVERY light theme — the light Marks and a light
# palette of the user's own (lib/palette.ts) — keyed on the scheme attribute
# lib/theme.ts sets, never on a list of theme ids.
LIGHT = ':root[data-scheme="light"]'

# The light Marks' surfaces (page, recessed panel, card) from globals.css.
# A custom light palette keeps Daylight's surface LIGHTNESS (lib/palette.ts),
# so these bound it too; ux-wave1-design-v1313 measures generated palettes.
SURF = {
    "mark1": {"ink-950": (237, 240, 245), "ink-900": (244, 246, 250), "ink-850": (252, 253, 255)},
    "mark8": {"ink-950": (235, 240, 248), "ink-900": (245, 248, 252), "ink-850": (252, 254, 255)},
}

HUES = "emerald|green|rose|red|sky|blue|violet|purple|fuchsia|pink|indigo|cyan|teal|lime"

# Tailwind v3 palette rows used here.
P500 = {
    "emerald": (16, 185, 129), "green": (34, 197, 94), "rose": (244, 63, 94), "red": (239, 68, 68),
    "sky": (14, 165, 233), "blue": (59, 130, 246), "violet": (139, 92, 246), "purple": (168, 85, 247),
    "fuchsia": (217, 70, 239), "pink": (236, 72, 153), "indigo": (99, 102, 241), "cyan": (6, 182, 212),
    "teal": (20, 184, 166), "lime": (132, 204, 22),
}
P600 = {
    "emerald": (5, 150, 105), "green": (22, 163, 74), "rose": (225, 29, 72), "red": (220, 38, 38),
    "sky": (2, 132, 199), "blue": (37, 99, 235), "violet": (124, 58, 237), "purple": (147, 51, 234),
    "fuchsia": (192, 38, 211), "pink": (219, 39, 119), "indigo": (79, 70, 229), "cyan": (8, 145, 178),
    "teal": (13, 148, 136), "lime": (101, 163, 13),
}
P800 = {
    "emerald": (6, 95, 70), "green": (22, 101, 52), "rose": (159, 18, 57), "red": (153, 27, 27),
    "sky": (7, 89, 133), "blue": (30, 64, 175), "violet": (91, 33, 182), "purple": (107, 33, 168),
    "fuchsia": (134, 25, 143), "pink": (157, 23, 77), "indigo": (55, 48, 163), "cyan": (21, 94, 117),
    "teal": (17, 94, 89), "lime": (63, 98, 18),
}

RE = re.compile(
    r"(?:\b((?:group-)?hover):)?\b(text|border|ring|bg)-(" + HUES + r")-(\d{2,3})(/(?:\d{1,3}|\[[0-9.]+\]))?"
)


def alpha_of(suffix):
    if not suffix:
        return None
    s = suffix[1:]
    if s.startswith("["):
        return float(s[1:-1])
    return int(s) / 100


def walk(root):
    for d in ("app", "components", "lib"):
        for dp, dns, fns in os.walk(os.path.join(root, d)):
            dns[:] = [n for n in dns if n not in ("__tests__", "node_modules", ".next")]
            for fn in fns:
                if re.search(r"\.(tsx?|jsx?)$", fn):
                    with open(os.path.join(dp, fn), encoding="utf-8") as fh:
                        yield fh.read().replace("\r\n", "\n")


def needed(kind, shade, a):
    if kind == "text":
        return shade <= 400
    if kind in ("border", "ring"):
        return 300 <= shade <= 500 and a is not None and a <= 0.5
    return 300 <= shade <= 500 and a is not None and a < 0.5


def esc(cls):
    return re.sub(r"([/\[\].:])", r"\\\1", cls)


def fmt_a(a):
    s = f"{a:.2f}".rstrip("0").rstrip(".")
    return s or "0"


def decl(kind, hue, a):
    if kind == "text":
        r, g, b = P800[hue]
        return f"color: rgb({r} {g} {b} / 1);", (P800[hue], 1.0)
    if kind == "bg":
        na = min(0.4, round(a + (0.06 if a <= 0.12 else 0.05), 2))
        r, g, b = P500[hue]
        return f"background-color: rgb({r} {g} {b} / {fmt_a(na)});", (P500[hue], na)
    na = min(1.0, round(a + 0.15, 2))
    r, g, b = P600[hue]
    prop = "border-color" if kind == "border" else "--tw-ring-color"
    return f"{prop}: rgb({r} {g} {b} / {fmt_a(na)});", (P600[hue], na)


def main(root):
    base = {}  # cls -> (kind, hue, shade, a)
    variants = set()  # (variant, cls)
    for text in walk(root):
        for m in RE.finditer(text):
            variant, kind, hue, shade, suffix = m.groups()
            shade = int(shade)
            a = alpha_of(suffix)
            if not needed(kind, shade, a):
                continue
            cls = f"{kind}-{hue}-{shade}{suffix or ''}"
            base[cls] = (kind, hue, shade, a)
            if variant:
                variants.add((variant, cls))

    order = {"bg": 0, "border": 1, "ring": 2, "text": 3}

    def key(c):
        k, h, s, a = base[c]
        return (order[k], h, s, a or 0, c)

    lines = []
    worst = []
    for cls in sorted(base, key=key):
        kind, hue, shade, a = base[cls]
        d, (rgb, na) = decl(kind, hue, a if a is not None else 1)
        sel = esc(cls)
        lines.append(f'{LIGHT} .{sel} {{ {d} }}')
        if kind == "text":
            for th in ("mark1", "mark8"):
                for s in SURF[th].values():
                    worst.append((contrast(rgb, s), th, cls, "surface"))
                tint = over(P500[hue], min(0.4, 0.1 + 0.06), SURF[th]["ink-950"])
                worst.append((contrast(rgb, tint), th, cls, "tint"))
    for variant, cls in sorted(variants, key=lambda v: (v[0], key(v[1]))):
        kind, hue, shade, a = base[cls]
        d, _ = decl(kind, hue, a if a is not None else 1)
        vcls = esc(f"{variant}:{cls}")
        if variant == "hover":
            sel = f"{LIGHT} .{vcls}:hover"
        else:
            sel = f"{LIGHT} .group:hover .{vcls}"
        lines.append(f"{sel} {{ {d} }}")

    worst.sort()
    print("classes:", len(base), "variants:", len(variants), "lowest text contrasts:", worst[:4])

    css_path = os.path.join(root, "app", "globals.css")
    with open(css_path, encoding="utf-8", newline="") as fh:
        css = fh.read()
    nl = "\r\n" if "\r\n" in css else "\n"
    start = "/* light-tone-overrides:start */"
    end = "/* light-tone-overrides:end */"
    i, j = css.find(start), css.find(end)
    if i < 0 or j < i:
        raise SystemExit("markers missing")
    block = start + nl + nl.join(lines) + nl
    css = css[:i] + block + css[j:]
    with open(css_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(css)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "..", "dashboard"))
