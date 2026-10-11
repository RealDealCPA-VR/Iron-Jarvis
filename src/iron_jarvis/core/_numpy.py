"""The one door numpy comes into the daemon through (v1.333.0).

THE PROBLEM: numpy ships OpenBLAS, and OpenBLAS sizes its thread pool to the
machine the moment it loads. On a 24-core PC a bare ``import numpy``
committed ~760 MB of private memory and parked 23 idle threads for the
daemon's whole life, while the only numpy work the daemon ever does is tiny
vector math (a 2000x384 normalise, a cosine over a few thousand rows) that
runs just as fast on one thread.

THE FIX: import numpy here, once, with ``OPENBLAS_NUM_THREADS=1`` set ONLY
for the import (OpenBLAS reads it when its library loads, never again), then
put the environment back exactly as it was. The variable is never left set:
every child the daemon starts afterwards (a Build pane, the shell tool,
run_code, the user's own numpy script) inherits the user's environment, not
ours. A value the user set themselves is respected and left alone.

Every module that needs numpy imports it from here
(``from ..core._numpy import np``); a source pin keeps it that way.
"""

from __future__ import annotations

import os
import sys
from types import ModuleType

#: The variable OpenBLAS reads at load time to size its thread pool.
_BLAS_THREADS_VAR = "OPENBLAS_NUM_THREADS"


def _import_numpy() -> ModuleType:
    """Import numpy with a one-thread OpenBLAS unless the user chose a size."""
    if "numpy" in sys.modules:  # already loaded (a test, an embedder): nothing to size
        # An import statement, not the sys.modules entry: if another thread is
        # still initialising numpy, this waits for it to finish.
        import numpy

        return numpy
    set_here = _BLAS_THREADS_VAR not in os.environ
    if set_here:
        os.environ[_BLAS_THREADS_VAR] = "1"
    try:
        import numpy
    finally:
        if set_here:
            os.environ.pop(_BLAS_THREADS_VAR, None)
    return numpy


np = _import_numpy()

__all__ = ["np"]
