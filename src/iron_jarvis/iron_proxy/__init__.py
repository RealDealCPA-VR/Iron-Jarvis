"""Iron-Proxy in Iron Jarvis (v1.301.0) — shared subscription accounts.

Iron-Proxy (RealDealCPA-VR/Iron-Proxy, MIT) is the ACCOUNT MANAGER: which
accounts exist, their order, who is parked until when, sign-in and usage.
Iron Jarvis keeps its own provider adapters and runs each CLI call AS the
account Iron-Proxy picks, then reports the outcome back.

* ``client``   — ``IronProxyClient`` / ``IronProxyError``: the ``/iron/*`` HTTP API.
* ``service``  — ``IronProxyService`` (``platform.iron_proxy``): locate / start /
  stop / status / client; ``service.current()`` for code with no platform.
* ``accounts`` — the adapter-facing lease/report helper.

Nothing is imported eagerly beyond the client types, so importing the package
never touches the network, the disk, or a subprocess.
"""

from .client import IronProxyClient, IronProxyError

__all__ = ["IronProxyClient", "IronProxyError"]
