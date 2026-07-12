"""Epic Tech AI product identity.

All user-facing product strings live here. Upstream Iron Jarvis attribution is
preserved in LICENSE / NOTICE; runtime branding is Epic Tech AI.
"""

from __future__ import annotations

PRODUCT_NAME = "Epic Tech AI"
PRODUCT_SHORT = "Epic"
PRODUCT_TAGLINE = "Your local-first AI operating system — agents, workflows, and commerce."
CONTACT_EMAIL = "epictechai@gmail.com"
CONTACT_X = "https://x.com/EpicTechAI"
CONTACT_X_HANDLE = "@EpicTechAI"
GITHUB_REPO = "https://github.com/Sm0k367/epic-iron-jarvis"
REPLY_PREFIX = "Epic Tech AI: "
CLI_NAME = "epic"  # dual entrypoint; ironjarvis still works
HOME_ENV = "EPIC_HOME"  # preferred; IRONJARVIS_HOME still honored
LEGACY_HOME_ENV = "IRONJARVIS_HOME"
DEFAULT_HOME_DIRNAME = ".epic-tech"  # preferred state dir name when using EPIC_HOME
LEGACY_HOME_DIRNAME = ".ironjarvis"
