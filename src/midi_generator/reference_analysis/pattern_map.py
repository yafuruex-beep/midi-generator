"""Versioned, deterministic section maps and exclusive JSON persistence."""
from copy import deepcopy
import hashlib
from importlib.metadata import version
import json
import warnings as python_warnings
from pathlib import Path

from .audio_loader import AudioAnalysisError
from .report import save_reference_analysis
from .section_analysis import analyze_reference_section, _UNSET

SCHEMA_VERSION = "1.1"
ANALYZER_VERSION = "7.1-bpm.1"


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _normalized(value):
    if isinstance(value, dict):
        return {key: _normalized(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _identity(result):
    body = {k: v for k, v in result.items() if k != "map_id"}
    return "sha256:" + hashlib.sha256(_encoded(_normalized(body)).encode("utf-8")).hexdigest()


def build_reference_pattern_map(section, *, section_name=None):
    """Adapt an existing Step 2 result without decoding audio or changing it."""
    if section_name is not None and (not isinstance(section_name, str) or not section_name.strip()):
        raise AudioAnalysisError("section_name must be a nonempty string or null")
    source = section["source"]
    start, end = section["analysis_range"]["start_seconds"], section["analysis_range"]["end_seconds"]
    pauses = [{"start_time": p["start_seconds"], "end_time": p["end_seconds"],
               "confidence": p["confidence"], "confidence_kind": p["confidence_kind"]}
              for p in section["pause_candidates"]]
    peak = max((bar["rms"] for bar in section["bars"]), default=0)
    bars = []
    for bar in section["bars"]:
        left, right = bar["start_seconds"], bar["end_seconds"]
        local_pauses = [{**p, "start_time": max(left, p["start_time"]), "end_time": min(right, p["end_time"])}
                        for p in pauses if p["start_time"] < right and p["end_time"] > left]
        bars.append({"bar_index": bar["bar_index"], "start_time": left, "end_time": right,
                     "pattern_id": bar["pattern_id"], "partial": bar["partial"],
                     "activity_score": bar["rms"] / peak if peak else None,
                     "rms": bar["rms"], "onset_count": bar["onset_count"],
                     "onset_density": bar["onsets_per_second"], "silence_candidates": local_pauses,
                     "confidence": {"bar_grid": section["bar_grid"]["confidence"],
                                    "repetition": bar["repetition_confidence"]},
                     "density_change": bar["density_change"], "rms_change": bar["rms_change"]})
    warnings = list(section["analysis"]["warnings"])
    reference_tempo = section.get("reference_tempo")
    if reference_tempo is None:
        effective = section["effective_tempo"]
        reference_tempo = {"bpm": effective["bpm"],
                           "source": "manual" if effective["source"] == "user" else "estimated",
                           "confidence": effective["confidence"]}
        warnings.append("Legacy section tempo is an unverified analysis override/estimate, not confirmed Reference BPM; reanalysis is recommended.")
    if not bars:
        warnings.append("No bar grid available; pattern_sequence is empty, not an inferred sequence.")
    if not peak:
        warnings.append("No positive bar RMS; normalized bar activity scores are null.")
    result = {
        "schema_version": SCHEMA_VERSION, "reference_id": source["source_id"],
        "source_audio_path": source["path"], "source": deepcopy(source),
        "section_name": section_name, "start_time": start, "end_time": end,
        "analysis_range": deepcopy(section["analysis_range"]),
        "estimated_tempo": deepcopy(section["tempo"]),
        "effective_tempo": deepcopy(section["effective_tempo"]),
        "reference_tempo": deepcopy(reference_tempo),
        "time_signature": deepcopy(section["meter"]), "bar_grid": deepcopy(section["bar_grid"]),
        "beat_positions": [b["time_seconds"] for b in section["beats"]],
        "bars": bars, "pattern_sequence": [b["pattern_id"] for b in bars],
        "activity": deepcopy(section["energy"]), "onset_density": deepcopy(section["activity"]),
        "silence_candidates": pauses,
        "confidence": {"tempo": section["tempo"]["confidence"], "meter": section["meter"]["confidence"],
                       "bar_grid": section["bar_grid"]["confidence"]}, "warnings": warnings,
        "analysis_parameters": {**deepcopy(section["analysis"]["parameters"]),
                                "activity_score_definition": "bar RMS / maximum bar RMS in this section; null without positive bar RMS"},
        "analyzer_version": ANALYZER_VERSION,
        "section_analyzer_version": section["analysis"]["algorithm_version"],
        "library_versions": {name: version(name) for name in ("numpy", "librosa", "soundfile")},
        "interpretation": section["analysis"]["repetition_definition"],
    }
    # Clamp floating-point cosine roundoff to the documented [0, 1] score domain.
    for bar in bars:
        if bar["confidence"]["repetition"] is not None:
            bar["confidence"]["repetition"] = min(1.0, max(0.0, bar["confidence"]["repetition"]))
    result = _normalized(result)
    result["map_id"] = _identity(result)
    validate_reference_pattern_map(result)
    return result


def analyze_reference_pattern_map(audio_path, *, section_name=None, reference_tempo_bpm=None,
                                  tempo_bpm=_UNSET, **section_options):
    """Analyze Reference tempo only; Export BPM is not an accepted option."""
    options = dict(section_options, reference_tempo_bpm=reference_tempo_bpm)
    if tempo_bpm is not _UNSET:
        options["tempo_bpm"] = tempo_bpm
    return build_reference_pattern_map(analyze_reference_section(audio_path, **options), section_name=section_name)


def validate_reference_pattern_map(result):
    """Runtime integrity/relationship checks without a new runtime dependency.

    A full JSON Schema for external validation is supplied in schemas/.
    """
    try:
        if not isinstance(result, dict):
            raise ValueError("Pattern Map must be an object")
        _encoded(result)
        supported = {("1.0", "7.1-step3.1"), (SCHEMA_VERSION, ANALYZER_VERSION)}
        if (result["schema_version"], result["analyzer_version"]) not in supported:
            raise ValueError("Unsupported Pattern Map version")
        if result["schema_version"] == SCHEMA_VERSION:
            reference = result["reference_tempo"]
            expected_source = "manual" if result["effective_tempo"]["source"] == "user" else "estimated"
            if reference["source"] != expected_source or reference["bpm"] != result["effective_tempo"]["bpm"]:
                raise ValueError("Reference tempo and compatibility alias disagree")
            if reference["confidence"] is not None and not 0 <= reference["confidence"] <= 1:
                raise ValueError("Invalid Reference tempo confidence")
        if result["map_id"] != _identity(result):
            raise ValueError("Pattern Map content hash does not match")
        if result["reference_id"] != "sha256:" + result["source"]["sha256"]:
            raise ValueError("Reference identity does not match source hash")
        start, end = result["start_time"], result["end_time"]
        if not 0 <= start < end:
            raise ValueError("Invalid section interval")
        if result["pattern_sequence"] != [b["pattern_id"] for b in result["bars"]]:
            raise ValueError("Pattern sequence does not match bars")
        previous = start
        for index, bar in enumerate(result["bars"], 1):
            if bar["bar_index"] != index or not previous <= bar["start_time"] < bar["end_time"] <= end:
                raise ValueError("Invalid bar interval/index")
            previous = bar["end_time"]
            score = bar["activity_score"]
            if score is not None and not 0 <= score <= 1:
                raise ValueError("Invalid activity score")
            if bar["onset_density"] < 0:
                raise ValueError("Invalid onset density")
            for pause in bar["silence_candidates"]:
                if not bar["start_time"] <= pause["start_time"] < pause["end_time"] <= bar["end_time"]:
                    raise ValueError("Pause lies outside its bar")
        if any(not start <= beat < end for beat in result["beat_positions"]):
            raise ValueError("Beat lies outside section")
        for pause in result["silence_candidates"]:
            if not start <= pause["start_time"] < pause["end_time"] <= end or not 0 <= pause["confidence"] <= 1:
                raise ValueError("Invalid pause")
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise AudioAnalysisError(f"Invalid Reference Pattern Map: {error}") from error
    return result


def load_reference_pattern_map(path):
    """Read and validate a saved map, including content identity."""
    def invalid_constant(value):
        raise AudioAnalysisError(f"Invalid JSON numeric constant: {value}")
    with Path(path).open(encoding="utf-8") as stream:
        result = validate_reference_pattern_map(json.load(stream, parse_constant=invalid_constant))
    if result["schema_version"] == "1.0":
        python_warnings.warn("Legacy Pattern Map 1.0: effective_tempo is an unverified analysis tempo, not confirmed Reference BPM. Reanalyze with explicit Reference tempo semantics.", FutureWarning, stacklevel=2)
    return result


def save_reference_pattern_map(result, output_path=None):
    """Save exclusively; reuse identical content, never replace a different file."""
    validate_reference_pattern_map(result)
    destination = Path(output_path) if output_path is not None else (
        Path("data/cache/reference_patterns") / (result["map_id"].split(":")[1] + ".json"))
    if destination.suffix.lower() != ".json":
        raise AudioAnalysisError("Pattern Map output must use the .json extension")
    # Legacy report/cache destinations are deliberately reserved even when absent.
    if destination.resolve().name == "reference_analysis.json":
        raise AudioAnalysisError("reference_analysis.json is reserved for the existing reference cache")
    if destination.exists():
        if load_reference_pattern_map(destination) == result:
            return destination
        raise FileExistsError(f"Different Pattern Map already exists: {destination}")
    try:
        # Reuse the existing UTF-8, finite-JSON, atomic exclusive persistence.
        return save_reference_analysis(result, destination)
    except FileExistsError:
        if load_reference_pattern_map(destination) == result:
            return destination
        raise
