"""Render engine -- Editing Blueprint plus assets to MP4.

Deterministic by contract: render(blueprint, assets, renderer_version) produces
a bit-identical file every time. That rules out any sampling model in the
render path. See docs/10-rendering-engine.md.
"""

from .graph import ExecutionGraph, plan
from .determinism import effect_seed, source_manifest_ok

__version__ = "0.1.0"
__all__ = ["ExecutionGraph", "effect_seed", "plan", "source_manifest_ok", "__version__"]
