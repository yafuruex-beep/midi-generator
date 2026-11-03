"""Optional, read-only reference audio features; imports require no audio extras."""
from .audio_loader import AudioAnalysisError
from .report import analyze_reference_audio, save_reference_analysis
from .section_analysis import analyze_reference_section

__all__ = ["AudioAnalysisError", "analyze_reference_audio", "save_reference_analysis", "analyze_reference_section"]

from .pattern_map import (analyze_reference_pattern_map, build_reference_pattern_map,
                          save_reference_pattern_map, load_reference_pattern_map,
                          validate_reference_pattern_map)

__all__ += ["analyze_reference_pattern_map", "build_reference_pattern_map",
            "save_reference_pattern_map", "load_reference_pattern_map",
            "validate_reference_pattern_map"]
