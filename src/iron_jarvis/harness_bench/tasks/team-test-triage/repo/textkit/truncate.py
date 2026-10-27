"""Shortening text for previews."""


def truncate(text, limit, ellipsis="..."):
    """``text`` cut to at most ``limit`` characters, ending in ``ellipsis`` when
    cut, and cut at a space when there is one in the kept part."""
    if len(text) <= limit:
        return text
    room = limit - len(ellipsis)
    if room <= 0:
        return ellipsis[:limit]
    kept = text[:room]
    if " " in kept.strip():
        kept = kept[: kept.rstrip().rindex(" ")]
    return kept.rstrip() + ellipsis
