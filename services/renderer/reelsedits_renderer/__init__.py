"""Render engine -- Editing Blueprint plus assets to MP4.

Deterministic by contract: render(blueprint, assets, renderer_version) produces
a bit-identical file every time. See docs/10-rendering-engine.md.
"""

from .determinism import effect_seed, render_cache_key, source_manifest_ok
from .ffmpeg_render import RENDERER_VERSION, RenderError, RenderResult, render
from .graph import ExecutionGraph, plan

__version__ = "0.2.0"
__all__ = [
    "RENDERER_VERSION",
    "ExecutionGraph",
    "RenderError",
    "RenderResult",
    "__version__",
    "effect_seed",
    "plan",
    "render",
    "render_cache_key",
    "source_manifest_ok",
]
