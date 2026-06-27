"""URL safety validation for outbound webhooks (SSRF defense).

Outbound webhook targets are agent/user-supplied, so before we ever make a real
network call we resolve the host and refuse to talk to internal, loopback,
link-local, or otherwise non-public addresses (e.g. the cloud metadata endpoint
``169.254.169.254`` or RFC1918 ranges). Pass ``allow_internal=True`` to bypass
the check for trusted/offline deployments.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

_ALLOWED_SCHEMES = {"http", "https"}


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for any address an outbound webhook must never reach."""
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_safe_webhook_url(url: str, *, allow_internal: bool = False) -> None:
    """Raise ``ValueError`` if ``url`` is unsafe to POST to.

    Always rejects non-http(s) schemes and a missing host. When
    ``allow_internal`` is False (the default), resolves every A/AAAA record for
    the host via :func:`socket.getaddrinfo` and rejects the URL if *any*
    resolved address is private, loopback, link-local, reserved, multicast, or
    unspecified -- defeating SSRF to RFC1918 ranges and the metadata service.
    Re-running this immediately before each delivery also defeats DNS rebinding.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"unsupported webhook url scheme: {scheme or '(none)'!r}"
        )

    try:
        host = parts.hostname
    except ValueError as exc:  # malformed host literal
        raise ValueError(f"invalid webhook url host: {exc}") from exc
    if not host:
        raise ValueError("webhook url has no host")

    if allow_internal:
        return

    try:
        infos = socket.getaddrinfo(
            host, parts.port or None, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror:
        # Unresolvable host cannot reach an internal address; the real POST
        # would fail to connect anyway, so this is not an SSRF block.
        return
    except ValueError as exc:  # e.g. out-of-range port
        raise ValueError(f"invalid webhook url: {exc}") from exc

    for info in infos:
        raw_ip = info[4][0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError as exc:
            raise ValueError(
                f"webhook host resolved to a non-ip address: {raw_ip!r}"
            ) from exc
        if _is_blocked_ip(ip):
            raise ValueError(
                f"webhook url resolves to a non-public address ({ip}); "
                "refusing to deliver"
            )
