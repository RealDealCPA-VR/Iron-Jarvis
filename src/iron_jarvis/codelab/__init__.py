"""Code Lab (v1.95.0) — the code and mini-apps agents build, kept.

``run_code`` is deliberately disposable: it writes into the SESSION workspace,
which the orchestrator deletes when the session ends — so even ``keep=true``
scripts vanished. Anything an agent figured out how to do in code was lost the
moment the task finished.

This package is the durable half: every ``run_code`` execution is recorded as a
:class:`~iron_jarvis.codelab.models.CodeArtifactRecord` (source + language +
outcome), browsable and RE-RUNNABLE from the Artifacts page long after its
session is gone. The disposable-scratch behavior is unchanged — persistence is
a side-record, not a change to how scripts execute.
"""

from .models import CodeArtifactRecord
from .store import CodeArtifactStore

__all__ = ["CodeArtifactRecord", "CodeArtifactStore"]
