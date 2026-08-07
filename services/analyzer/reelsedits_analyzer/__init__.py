"""Reference analysis worker -- reference video to Editing Blueprint.

See docs/04-ai-pipeline.md part A.
"""

from .pipeline import AnalysisContext, MediaProfile, Proxies, StageResult, analyze
from .worker import AdmissionController, JobEnvelope, Worker

__version__ = "0.1.0"
__all__ = [
    "AdmissionController", "AnalysisContext", "JobEnvelope", "MediaProfile",
    "Proxies", "StageResult", "Worker", "analyze", "__version__",
]
