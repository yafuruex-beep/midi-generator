"""Onset activity and accents relative to an explicitly estimated beat grid."""
import math
from .audio_loader import audio_dependencies


def _grid(onsets, bpm, duration):
    np, _, _ = audio_dependencies()
    period = 60 / bpm
    times = np.array([event["relative_time_seconds"] for event in onsets], dtype=float)
    weights = np.array([event["strength"] ** 2 for event in onsets], dtype=float)
    if not len(times):
        return None
    # Highest weighted onset-phase cluster, not a musical downbeat detector.
    phases = np.remainder(times, period)
    bins = 128
    histogram, _ = np.histogram(phases, bins=bins, range=(0, period), weights=weights)
    smooth = sum(np.roll(histogram, shift) for shift in range(-2, 3))
    phase = (int(np.argmax(smooth)) + 0.5) / bins * period
    circular = (phases - phase + period / 2) % period - period / 2
    near = np.abs(circular) <= period * 0.1
    if np.any(near) and np.sum(weights[near]) > 0:
        phase = float((phase + np.average(circular[near], weights=weights[near])) % period)
    scales = {}
    for name, divisor in (("beat", 1), ("eighth", 2), ("sixteenth", 4)):
        step = period / divisor
        indices = np.rint((times - phase) / step).astype(int)
        errors = np.abs(times - (phase + indices * step))
        tolerance = min(0.025, 0.2 * step)
        aligned = errors <= tolerance
        first_cell = math.ceil(-phase / step)
        last_cell = math.ceil((duration - phase) / step)
        total_cells = max(0, last_cell - first_cell)
        occupied = {int(index) for index, ok in zip(indices, aligned) if ok and first_cell <= index < last_cell}
        scales[name] = {"alignment_score": float(np.mean(np.maximum(0, 1 - errors / (step / 2)))),
                        "aligned_onset_ratio": float(np.mean(aligned)),
                        "occupied_grid_ratio": len(occupied) / total_cells if total_cells else None,
                        "tolerance_seconds": tolerance}
    positions = []
    for index, time in enumerate(times):
        beat = (float(time) - phase) / period
        category = "unknown"
        for label, divisor in (("on_beat", 1), ("offbeat_eighth", 2), ("offbeat_sixteenth", 4)):
            distance = abs(beat * divisor - round(beat * divisor)) * period / divisor
            if distance <= min(0.025, 0.2 * period / divisor):
                category = label
                break
        positions.append({"onset_index": index, "beat_position": beat, "category": category,
                          "sixteenth_error_seconds": abs(beat * 4 - round(beat * 4)) * period / 4})
    return {"phase_seconds_within_beat": phase, "scales": scales, "positions": positions}


def analyze_rhythm(onsets, tempo, start_seconds, duration_seconds):
    np, _, _ = audio_dependencies()
    density = len(onsets) / duration_seconds
    windows = []
    for index in range(max(1, math.ceil(duration_seconds))):
        left, right = float(index), min(float(index + 1), duration_seconds)
        count = sum(left <= event["relative_time_seconds"] < right for event in onsets)
        windows.append({"time_seconds": start_seconds + left, "relative_time_seconds": left,
                        "duration_seconds": right - left, "onset_count": count,
                        "onsets_per_second": count / (right - left)})
    bpm = tempo["estimated_bpm"]
    grid = _grid(onsets, bpm, duration_seconds) if bpm is not None else None
    activity = {"onsets_per_second": density, "eighth_note_activity": None, "sixteenth_note_activity": None,
                "off_sixteenth_onset_ratio": None, "over_time": windows,
                "tempo_dependent": True, "confidence": None,
                "activity_definition": "Fraction of estimated grid cells occupied by at least one detected onset."}
    alignment = {"sixteenth_alignment_score": None, "eighth_alignment_score": None, "beat_alignment_score": None,
                 "confidence": None, "phase_seconds_within_beat": None, "phase_time_seconds": None,
                 "phase_method": "weighted_onset_phase_cluster", "downbeat_known": False,
                 "onset_positions": [], "candidate_grids": [], "grid_metrics": None,
                 "interpretation": "Estimated audio-onset grid alignment; not drum tightness."}
    if grid is not None:
        scales = grid["scales"]
        activity.update({"eighth_note_activity": scales["eighth"]["occupied_grid_ratio"],
                         "sixteenth_note_activity": scales["sixteenth"]["occupied_grid_ratio"],
                         "off_sixteenth_onset_ratio": sum(pos["category"] == "offbeat_sixteenth" for pos in grid["positions"]) / len(onsets)})
        alignment.update({"sixteenth_alignment_score": scales["sixteenth"]["alignment_score"],
                          "eighth_alignment_score": scales["eighth"]["alignment_score"],
                          "beat_alignment_score": scales["beat"]["alignment_score"],
                          "phase_seconds_within_beat": grid["phase_seconds_within_beat"],
                          "phase_time_seconds": start_seconds + grid["phase_seconds_within_beat"],
                          "onset_positions": grid["positions"], "grid_metrics": scales})
        for candidate in tempo["candidates"]:
            alternative = _grid(onsets, candidate["bpm"], duration_seconds)
            alignment["candidate_grids"].append({"bpm": candidate["bpm"],
                "sixteenth_alignment_score": alternative["scales"]["sixteenth"]["alignment_score"]})
    strengths = [event["strength"] for event in onsets]
    threshold = max(0.5, float(np.quantile(strengths, 0.75))) if strengths else None
    strong = []
    counts = {"on_beat": 0, "offbeat_eighth": 0, "offbeat_sixteenth": 0, "unknown": 0}
    for index, event in enumerate(onsets):
        if event["strength"] < threshold:
            continue
        category = grid["positions"][index]["category"] if grid is not None else "unknown"
        counts[category] += 1
        strong.append({"onset_index": index, "time_seconds": event["time_seconds"],
                       "relative_time_seconds": event["relative_time_seconds"],
                       "strength": event["strength"], "grid_category": category})
    known = len(strong) - counts["unknown"]
    accent = {"strong_onset_count": len(strong), "strength_threshold": threshold,
              "offbeat_accent_ratio": (counts["offbeat_eighth"] + counts["offbeat_sixteenth"]) / known if known else None,
              "category_counts": counts, "strong_onsets": strong,
              "interpretation": "On-beat refers to an inferred quarter-note pulse; bar downbeats and instrument roles are unknown."}
    return activity, accent, alignment
