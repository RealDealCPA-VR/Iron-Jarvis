"""Artifact System (SPEC §26).

Versioned agent outputs stored under ``.ironjarvis/artifacts``. Importing this
package registers :class:`ArtifactRecord` on the SQLModel metadata so the table
is created by ``init_db``.
"""

from __future__ import annotations

from .models import ArtifactRecord
from .store import Artifact, ArtifactStore

__all__ = ["ArtifactRecord", "Artifact", "ArtifactStore"]
