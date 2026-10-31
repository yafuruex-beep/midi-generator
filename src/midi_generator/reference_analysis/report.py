"""Reference analysis orchestration and separate, atomic JSON persistence."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from .audio_loader import AudioAnalysisError, audio_dependencies, load_audio
from .energy_analysis import analyze_energy
from .onset_analysis import analyze_onsets
from .rhythm_features import analyze_rhythm
from .tempo_analysis import analyze_tempo

SCHEMA_VERSION = "1.0"
ALGORITHM_VERSION = "1.0"


def analyze_reference_audio(audio_path, start_seconds=0.0, end_seconds=None):
    """Return JSON-compatible audio features without writing audio, reports, or MIDI."""
    audio = load_audio(audio_path, start_seconds=start_seconds, end_seconds=end_seconds)
    np, sf, librosa = audio_dependencies()
    start, duration = audio.analysis_range["start_seconds"], audio.analysis_range["duration_seconds"]
    hop, n_fft = 256, 1024
    onsets, envelope = analyze_onsets(audio.samples, audio.sample_rate, start, duration, hop_length=hop, n_fft=n_fft)
    tempo = analyze_tempo(envelope, onsets, audio.sample_rate, hop, duration)
    activity, accent, grid = analyze_rhythm(onsets, tempo, start, duration)
    energy = analyze_energy(audio.samples, audio.sample_rate, start)
    warnings = list(audio.warnings)
    warnings.extend([
        "Onsets and accents include every audible instrument; they are not drum notes.",
        "Tempo, half/double interpretations, and beat phase are estimates. Confidence is null; relative scores are not probabilities.",
        "Grid/activity estimates depend on the selected tempo and phase; no bar downbeat is known.",
        "Onsets at segment boundaries may be missed; preceding audio outside the selected range is not analyzed.",
    ])
    if duration < 2:
        warnings.append("Analysis range is shorter than 2 seconds; no tempo or grid estimate is reported.")
    if float(np.max(np.abs(audio.samples), initial=0)) < 1e-8:
        warnings.append("Analysis range is silent; rhythmic estimates are unavailable.")
    elif tempo["estimated_bpm"] is None:
        warnings.append("Insufficient periodic onset evidence for a supported BPM estimate.")
    if duration < 8 and tempo["estimated_bpm"] is not None:
        warnings.append("Short analysis range: treat tempo and grid estimates as preliminary.")
    result = {
        "schema_version": SCHEMA_VERSION, "source": audio.source, "analysis_range": audio.analysis_range,
        "tempo": tempo, "onsets": onsets, "rhythmic_activity": activity, "accent_pattern": accent,
        "energy": energy, "grid_alignment": grid,
        "analysis_metadata": {"algorithm_version": ALGORITHM_VERSION,
            "analyzed_at": datetime.now(timezone.utc).isoformat(), "warnings": warnings,
            "analysis_sample_rate": audio.sample_rate, "analysis_channels": 1,
            "hop_length": hop, "n_fft": n_fft,
            "library_versions": {"numpy": np.__version__, "librosa": librosa.__version__,
                                 "soundfile": sf.__version__, "libsndfile": sf.__libsndfile_version__},
            "field_kinds": {"source": "decoded_metadata", "energy": "signal_measurement",
                            "onsets": "estimated_events", "tempo": "estimated_periodicity",
                            "rhythmic_activity": "onset_measurement_and_estimated_grid",
                            "accent_pattern": "estimated_events_and_grid", "grid_alignment": "estimated_grid"},
            "input_modified": False, "midi_generated": False},
    }
    try:
        json.dumps(result, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise AudioAnalysisError(f"Analysis produced a non-JSON value: {exc}") from exc
    return result


def save_reference_analysis(result, output_path, *, overwrite=False):
    """Atomically save UTF-8 JSON. Overwriting and input replacement are explicit guards."""
    destination = Path(output_path)
    if destination.suffix.lower() != ".json":
        raise AudioAnalysisError("Reference analysis output must use the .json extension")
    source = result.get("source", {})
    source_path = source.get("absolute_path", source.get("path"))
    if source_path and destination.resolve() == Path(source_path).resolve():
        raise AudioAnalysisError("The output must not replace the input audio file")
    # Serialize before opening anything, so invalid values never damage an existing report.
    payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Reference report already exists: {destination}; use --overwrite to replace it")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, destination)
        else:
            os.link(temporary, destination)  # Atomic exclusive creation, including races.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination
