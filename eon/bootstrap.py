"""Install EON extensions into OpenJarvis.

The one seam: `jarvis serve` imports MultiEngine from openjarvis.engine.multi
at call time, so rebinding that name before the CLI starts adds failover
without editing the framework. Re-check this seam on OpenJarvis upgrades.
"""

from __future__ import annotations

import openjarvis.engine.multi as _multi

from eon.engines import register_engines
from eon.failover import EonMultiEngine


def install() -> None:
    register_engines()
    _multi.MultiEngine = EonMultiEngine
