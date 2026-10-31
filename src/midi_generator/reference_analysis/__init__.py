"""Optional, read-only reference audio features; imports require no audio extras."""
from .audio_loader import AudioAnalysisError
from .report import analyze_reference_audio, save_reference_analysis

__all__ = ["AudioAnalysisError", "analyze_reference_audio", "save_reference_analysis"]
