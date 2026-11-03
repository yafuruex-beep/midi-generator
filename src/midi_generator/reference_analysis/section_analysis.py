"""Bounded full-mix section features; no persistence or MIDI integration."""
import math
import warnings as python_warnings

_UNSET = object()

from .audio_loader import AudioAnalysisError, audio_dependencies, load_audio
from .energy_analysis import analyze_energy
from .onset_analysis import analyze_onsets
from .rhythm_features import analyze_rhythm
from .tempo_analysis import analyze_tempo


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise AudioAnalysisError(f"{name} must be a finite positive number")


def _pauses(samples, sr, start, np):
    size = max(1, round(sr * .02))
    levels = [float(np.sqrt(np.mean(samples[i:i + size] ** 2))) for i in range(0, len(samples), size)]
    threshold = max(1e-4, max(levels, default=0) * .08)
    result = []
    first = None
    for index in range(len(levels) + 1):
        quiet = index < len(levels) and levels[index] <= threshold
        if quiet and first is None:
            first = index
        if not quiet and first is not None:
            left, right = first * size / sr, min(index * size, len(samples)) / sr
            if right - left >= .18:
                mean = sum(levels[first:index]) / (index - first)
                result.append({"start_seconds": start + left, "end_seconds": start + right,
                               "duration_seconds": right - left,
                               "confidence": max(0.0, 1 - mean / threshold),
                               "confidence_kind": "heuristic_quietness_not_probability"})
            first = None
    return result, threshold


def analyze_reference_section(audio_path, *, start_seconds=0.0, end_seconds=None,
                              reference_tempo_bpm=None, tempo_bpm=_UNSET,
                              time_signature=None, bar_start_seconds=None):
    """Return deterministic section measurements and uncertain regular-grid estimates.

    Meter defaults to a provisional 4/4 analysis grouping, never an inferred meter.
    A bar origin supplied by the caller is an absolute source-audio time.
    """
    if tempo_bpm is not _UNSET:
        if reference_tempo_bpm is not None:
            raise AudioAnalysisError("Specify only reference_tempo_bpm or deprecated tempo_bpm, not both")
        python_warnings.warn(
            "tempo_bpm is deprecated; use reference_tempo_bpm for the Reference audio tempo, not Export BPM",
            FutureWarning, stacklevel=2,
        )
        reference_tempo_bpm = tempo_bpm
    if reference_tempo_bpm is not None:
        _positive(reference_tempo_bpm, "reference_tempo_bpm")
        if not 30 <= reference_tempo_bpm <= 360:
            raise AudioAnalysisError("reference_tempo_bpm must be between 30 and 360")
    np, sf, _ = audio_dependencies()
    meter = [4, 4] if time_signature is None else time_signature
    if (not isinstance(meter, (list, tuple)) or len(meter) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) for v in meter)
            or not 1 <= meter[0] <= 32 or meter[1] not in {1, 2, 4, 8, 16, 32}):
        raise AudioAnalysisError("time_signature must be [numerator 1..32, power-of-two denominator 1..32]")
    if bar_start_seconds is not None:
        if (isinstance(bar_start_seconds, bool) or not isinstance(bar_start_seconds, (int, float))
                or not math.isfinite(bar_start_seconds) or bar_start_seconds < 0):
            raise AudioAnalysisError("bar_start_seconds must be finite and non-negative")
    for name, value in (("start_seconds", start_seconds), ("end_seconds", end_seconds)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise AudioAnalysisError(f"{name} must be finite seconds")
    if start_seconds is None or start_seconds < 0:
        raise AudioAnalysisError("start_seconds must be non-negative")
    try:
        info = sf.info(audio_path)
    except (RuntimeError, OSError) as error:
        raise AudioAnalysisError(f"Cannot inspect reference audio: {error}") from error
    end = info.duration if end_seconds is None else end_seconds
    duration = end - start_seconds
    if duration <= 0 or end > info.duration + 1e-9:
        raise AudioAnalysisError("Analysis range must be nonempty and inside the audio")
    if duration > 120:
        raise AudioAnalysisError("Section analysis is limited to 120 seconds; select a shorter range")
    if (math.ceil(duration * info.samplerate) + 2) * info.channels * 8 > 128 * 1024 ** 2:
        raise AudioAnalysisError("Decoded section exceeds the 128 MiB input budget; select a shorter range")
    audio = load_audio(audio_path, start_seconds=start_seconds, end_seconds=end_seconds)
    start = audio.analysis_range["start_seconds"]
    duration = audio.analysis_range["duration_seconds"]
    end = start + duration
    if bar_start_seconds is not None and not start <= bar_start_seconds < end:
        raise AudioAnalysisError("bar_start_seconds must be inside the selected section")
    onsets, envelope = analyze_onsets(audio.samples, audio.sample_rate, start, duration)
    tempo = analyze_tempo(envelope, onsets, audio.sample_rate, 256, duration)
    bpm = float(reference_tempo_bpm) if reference_tempo_bpm is not None else tempo["estimated_bpm"]
    activity, _, grid = analyze_rhythm(onsets, {**tempo, "estimated_bpm": bpm}, start, duration)
    energy = analyze_energy(audio.samples, audio.sample_rate, start)
    pauses, threshold = _pauses(audio.samples, audio.sample_rate, start, np)
    warnings = list(audio.warnings) + [
        "Full-mix onsets, quiet intervals and repetition are not instrument-specific notes or rests.",
        "Regular beat/bar positions are hypotheses, not tracked tempo changes or verified downbeats.",
        "Confidence values are heuristic support, not calibrated probabilities.",
        "Boundary transients may be missed; partial bars are excluded from repetition grouping.",
    ]
    if time_signature is None:
        warnings.append("4/4 is a provisional grouping assumption; meter is unknown, not automatically detected.")
    if duration < 2:
        warnings.append("Section is shorter than two seconds; automatic tempo evidence is insufficient.")
    if bpm is None:
        warnings.append("No supported tempo: beat/bar grid and bar-level repetition are unavailable.")
    if any(c.get("relationship") in {"half_tempo", "double_tempo"} for c in tempo["candidates"]):
        warnings.append("Reference tempo has half/double-time candidates; periodicity scores are not probabilities and the selected pulse interpretation is unverified.")
    warnings.append("Reference BPM support and bar/downbeat support are separate; neither verifies the other. No Export BPM is used for analysis.")
    phase = grid["phase_time_seconds"]
    origin = bar_start_seconds if bar_start_seconds is not None else phase
    bars, beats, representatives = [], [], []
    if bpm is not None and origin is not None:
        period = 60 / bpm
        bar_length = period * meter[0] * 4 / meter[1]
        count = math.ceil((end - start) / bar_length) + 1
        if count > 512:
            raise AudioAnalysisError("Section grid exceeds 512 bars; shorten the section or use a coarser meter")
        first = math.floor((start - origin) / bar_length)
        last = math.ceil((end - origin) / bar_length)
        for index in range(math.ceil((start - origin) / period), math.ceil((end - origin) / period)):
            beats.append({"time_seconds": origin + index * period, "kind": "estimated_regular_quarter_pulse"})
        previous = None
        for index in range(first, last):
            nominal_left, nominal_right = origin + index * bar_length, origin + (index + 1) * bar_length
            left, right = max(start, nominal_left), min(end, nominal_right)
            if right <= left:
                continue
            partial = abs(left - nominal_left) > 1e-8 or abs(right - nominal_right) > 1e-8
            selected = [e for e in onsets if left <= e["time_seconds"] < right]
            block = audio.samples[round((left - start) * audio.sample_rate):round((right - start) * audio.sample_rate)]
            rms = float(np.sqrt(np.mean(block ** 2))) if len(block) else 0.0
            density = len(selected) / (right - left)
            vector = np.zeros(32)
            for event in selected:
                cell = min(15, max(0, int((event["time_seconds"] - nominal_left) / bar_length * 16)))
                vector[cell] += event["strength"]
            for cell in range(16):
                a, b = round(cell * len(block) / 16), round((cell + 1) * len(block) / 16)
                vector[16 + cell] = float(np.sqrt(np.mean(block[a:b] ** 2))) if b > a else 0
            for half in (vector[:16], vector[16:]):
                norm = np.linalg.norm(half)
                if norm > 0:
                    half /= norm
            norm = np.linalg.norm(vector)
            if norm > 0:
                vector /= norm
            pattern, similarity = None, None
            if not partial and selected and rms > threshold:
                best = max(((float(np.dot(vector, representative)), j) for j, representative in enumerate(representatives)), default=(-1, -1))
                if best[0] >= .92:
                    similarity, j = best
                else:
                    j = len(representatives)
                    representatives.append(vector)
                pattern = f"P{j + 1:03d}"
            bars.append({"bar_index": len(bars) + 1, "start_seconds": left, "end_seconds": right,
                         "nominal_start_seconds": nominal_left, "partial": partial,
                         "onset_count": len(selected), "onsets_per_second": density, "rms": rms,
                         "density_change": None if previous is None else density - previous[0],
                         "rms_change": None if previous is None else rms - previous[1],
                         "pattern_id": pattern, "repetition_similarity": similarity,
                         "repetition_confidence": similarity,
                         "quiet_fraction": sum(max(0, min(right, p["end_seconds"]) - max(left, p["start_seconds"])) for p in pauses) / (right - left)})
            previous = density, rms
    return {"source": audio.source, "analysis_range": audio.analysis_range,
            "tempo": tempo, "effective_tempo": {"bpm": bpm, "source": "user" if reference_tempo_bpm is not None else "estimated", "confidence": None},
            "reference_tempo": {"bpm": bpm, "source": "manual" if reference_tempo_bpm is not None else "estimated",
                                "confidence": None if reference_tempo_bpm is not None else tempo["confidence"]},
            "meter": {"value": list(meter), "source": "user" if time_signature is not None else "provisional_assumption", "confidence": None},
            "bar_grid": {"origin_seconds": origin, "source": "user" if bar_start_seconds is not None else "estimated_pulse_assumed_downbeat", "downbeat_verified": False, "confidence": None},
            "beats": beats, "bars": bars, "onsets": onsets, "activity": activity, "energy": energy,
            "pause_candidates": pauses,
            "analysis": {"algorithm": "full_mix_section_features", "algorithm_version": "7.1-bpm.1",
                         "parameters": {"sample_rate": 22050, "hop_length": 256, "n_fft": 1024,
                                        "pause_window_seconds": .02, "pause_min_seconds": .18,
                                        "pause_relative_threshold": .08, "pause_absolute_floor": 1e-4,
                                        "effective_pause_threshold": threshold, "repetition_cosine_threshold": .92,
                                        "repetition_bins": 16, "max_seconds": 120, "decoded_input_budget_mib": 128},
                         "warnings": warnings, "repetition_definition": "Onset-strength and RMS shape similarity; not pitch, transcription or musical phrase identity."}}
