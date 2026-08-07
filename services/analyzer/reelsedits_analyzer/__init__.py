"""Reference analysis -- video to Editing Blueprint.

v0 runs on CPU with librosa and OpenCV so the whole pipeline is testable today;
docs/07 describes the production models that replace the internals without
changing these signatures.
"""

from .audio import AudioAnalysis, analyze_audio
from .fusion import ANALYZER_VERSION, build_blueprint
from .visual import MediaProfile, Shot, VisualAnalysis, analyze_visual, probe
from .worker import AdmissionController, JobEnvelope, Worker

__version__ = "0.2.0"
__all__ = [
    "ANALYZER_VERSION",
    "AdmissionController",
    "AudioAnalysis",
    "JobEnvelope",
    "MediaProfile",
    "Shot",
    "VisualAnalysis",
    "Worker",
    "__version__",
    "analyze_audio",
    "analyze_visual",
    "build_blueprint",
    "probe",
]
