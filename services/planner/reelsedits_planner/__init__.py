"""Blueprint synthesis and re-planning.

The only non-deterministic component in the system, and it sits upstream of
the blueprint. Output is schema-validated and invariant-checked, then frozen.
See docs/04-ai-pipeline.md stage 8.
"""

from .synthesis import PlannerConfig, synthesise_blueprint

__version__ = "0.1.0"
__all__ = ["PlannerConfig", "synthesise_blueprint", "__version__"]
