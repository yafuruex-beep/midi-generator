"""Read-only, explainable drum-rhythm heuristics in quarter-note beat units."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Any, Mapping

import mido

from midi_generator.core.midi_io import GM_DRUM_MAP
from midi_generator.storage.catalog import analyze_midi

ANALYSIS_VERSION = "1.0"
GROOVE_VALUES = {
    "subdivision": ("eighth", "mixed", "sixteenth", "unknown"),
    "timing_feel": ("straight", "swung", "unknown"),
    "syncopation": ("low", "medium", "high", "unknown"),
    "backbeat": ("standard", "half_time", "double_time", "other", "unknown"),
    "double_kick": ("none", "light", "medium", "heavy", "unknown"),
    "density": ("low", "medium", "high", "unknown"),
}
DEFAULT_CONFIG = {
    "drum_map": {"kick": [35, 36], "snare": [38, 40], "hihat": [42, 44, 46],
                 "cymbal": [49, 51, 52, 53, 55, 57, 59], "toms": [41, 43, 45, 47, 48, 50]},
    "thresholds": {
        "grid_tolerance_beats": 0.04, "minimum_onsets": 8,
        "straight_alignment": 0.85, "sixteenth_min_count": 4,
        "sixteenth_min_share": 0.20, "eighth_max_share": 0.05,
        "kick_sixteenth_min_share": 0.35,
        "swing_min_pairs": 4, "swing_min_share": 0.65,
        "swing_min_cell_coverage": 0.50,
        "swing_ratio_min": 0.58, "swing_ratio_max": 0.75, "swing_max_deviation": 0.04,
        "syncopation_medium": 0.25, "syncopation_high": 0.55,
        "backbeat_min_snares": 4, "backbeat_match_share": 0.70,
        "snare_accent_velocity_share": 0.70,
        "backbeat_bar_coverage": 0.40,
        "double_kick_min_run": 3, "double_kick_medium_share": 0.25,
        "double_kick_heavy_share": 0.60, "double_kick_heavy_notes_per_bar": 4,
        "density_medium": 0.30, "density_high": 0.70,
        "density_kick_high_per_quarter": 2, "density_snare_high_per_quarter": 1,
        "density_cymbal_high_per_quarter": 3,
    },
}


def validate_groove_preferences(values: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(values, Mapping):
        raise ValueError("groove preferences must be an object")
    result = {key: value for key, value in values.items() if value is not None}
    for key, value in result.items():
        if key == "tightness":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("tightness must be a number from 0 to 1")
        elif key not in GROOVE_VALUES or value not in GROOVE_VALUES[key]:
            raise ValueError(f"Unsupported groove preference: {key}={value}")
    return result


def load_groove_config(path: str | Path | None = None) -> dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    if path is not None:
        with Path(path).open(encoding="utf-8") as source:
            overrides = json.load(source)
        if set(overrides) - {"drum_map", "thresholds"}:
            raise ValueError("Groove config supports drum_map and thresholds only")
        for category in ("drum_map", "thresholds"):
            if not isinstance(overrides.get(category, {}), dict):
                raise ValueError(f"{category} must be an object")
            if set(overrides.get(category, {})) - set(config[category]):
                raise ValueError(f"Unknown groove configuration key in {category}")
            config[category].update(overrides.get(category, {}))
    assigned = []
    for notes in config["drum_map"].values():
        if not isinstance(notes, list) or any(isinstance(note, bool) or not isinstance(note, int) or not 0 <= note <= 127 for note in notes):
            raise ValueError("Drum map values must be lists of MIDI note numbers")
        assigned.extend(notes)
    if len(set(assigned)) != len(assigned):
        raise ValueError("A MIDI note cannot belong to multiple drum roles")
    t = config["thresholds"]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in t.values()):
        raise ValueError("Groove thresholds must be finite positive numbers")
    if not 0 < t["grid_tolerance_beats"] < 0.125:
        raise ValueError("grid_tolerance_beats must be below half a sixteenth note (0.125)")
    for key in ("minimum_onsets", "sixteenth_min_count", "swing_min_pairs", "backbeat_min_snares", "double_kick_min_run"):
        if not isinstance(t[key], int) or t[key] < 2:
            raise ValueError(f"{key} must be an integer >= 2")
    for key in ("straight_alignment", "sixteenth_min_share", "eighth_max_share", "swing_min_share",
                "swing_ratio_min", "swing_ratio_max", "snare_accent_velocity_share", "double_kick_medium_share",
                "double_kick_heavy_share", "density_medium", "density_high", "syncopation_medium", "syncopation_high",
                "swing_min_cell_coverage", "kick_sixteenth_min_share", "backbeat_match_share", "backbeat_bar_coverage"):
        if t[key] > 1:
            raise ValueError(f"{key} must not exceed 1")
    for lower, upper in (("density_medium", "density_high"), ("syncopation_medium", "syncopation_high"),
                         ("swing_ratio_min", "swing_ratio_max"), ("double_kick_medium_share", "double_kick_heavy_share")):
        if t[lower] >= t[upper]:
            raise ValueError(f"{lower} must be less than {upper}")
    return config


def _level(value: float, medium: float, high: float) -> str:
    return "high" if value >= high else "medium" if value >= medium else "low"


def _swing_evidence(onsets: list[float], t: Mapping[str, float]) -> dict[str, Any]:
    best = {"pairs": 0, "pair_share": 0.0, "cell_coverage": 0.0, "ratio": None, "unit_beats": None, "deviation": None, "detected": False}
    for unit in (1.0, 0.5):
        cells: dict[int, list[float]] = defaultdict(list)
        for beat in onsets:
            cell = round(beat / unit) if abs(beat / unit - round(beat / unit)) < t["grid_tolerance_beats"] / unit else math.floor(beat / unit)
            cells[cell].append(beat / unit - cell)
        ratios = []
        for phases in cells.values():
            off = [phase for phase in phases if 0.4 <= phase <= 0.85]
            if any(abs(phase) <= t["grid_tolerance_beats"] / unit for phase in phases) and off:
                ratios.append(min(off))
        if not ratios:
            continue
        ratio = statistics.median(ratios)
        consistent = [value for value in ratios if abs(value - ratio) <= t["swing_max_deviation"]]
        share = len(consistent) / len(ratios)
        cell_coverage = len(consistent) / len(cells)
        detected = (len(consistent) >= t["swing_min_pairs"] and share >= t["swing_min_share"]
                    and cell_coverage >= t["swing_min_cell_coverage"]
                    and t["swing_ratio_min"] <= ratio <= t["swing_ratio_max"]
                    and abs(ratio * unit - round(ratio * unit * 4) / 4) > t["grid_tolerance_beats"])
        evidence = {"pairs": len(consistent), "pair_share": round(share, 6), "cell_coverage": round(cell_coverage, 6), "ratio": round(ratio, 6),
                    "unit_beats": unit, "deviation": round(statistics.median(abs(v - ratio) for v in ratios), 6),
                    "detected": detected}
        if detected or (not best["detected"] and evidence["pairs"] > best["pairs"]):
            best = evidence
        if detected:
            break
    return best


def analyze_drum_groove(
    path: str | Path, *, config_path: str | Path | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Analyze one confirmed drum MIDI. No timestamps or source writes; deterministic."""
    source = Path(path)
    effective = load_groove_config(config_path) if config is None else deepcopy(dict(config))
    t = effective["thresholds"]
    roles = {note: role for role, notes in effective["drum_map"].items() for note in notes}
    midi = mido.MidiFile(source)
    if midi.type == 2:
        raise ValueError("Independent MIDI format 2 sequences are not combined for groove analysis")
    if midi.ticks_per_beat <= 0:
        raise ValueError("Groove analysis requires positive ticks_per_beat")
    context = analyze_midi(source)
    duration = max((sum(event.time for event in track) for track in midi.tracks), default=0) / midi.ticks_per_beat
    bars = float(context["estimated_bars"])
    notes = []
    ignored = Counter()
    role_events: dict[str, list[tuple[float, int]]] = {role: [] for role in effective["drum_map"]}
    for track in midi.tracks:
        tick = 0
        for event in track:
            tick += event.time
            if event.type != "note_on" or event.velocity <= 0:
                continue
            if event.note not in roles:
                ignored[event.note] += 1
                continue
            beat = tick / midi.ticks_per_beat
            role_events[roles[event.note]].append((beat, event.velocity))
            notes.append((beat, event.velocity))
    for events in role_events.values():
        events.sort()
    onsets = sorted({beat for beat, _ in notes})
    tol = t["grid_tolerance_beats"]
    errors = [abs(beat - round(beat * 4) / 4) for beat, _ in notes]
    aligned = [(beat, velocity) for beat, velocity in notes if abs(beat - round(beat * 4) / 4) <= tol]
    alignment = len(aligned) / len(notes) if notes else 0.0
    tightness = statistics.fmean(max(0.0, 1 - error / 0.125) for error in errors) if errors else None
    swing = _swing_evidence(onsets, t)
    enough = len(onsets) >= t["minimum_onsets"]
    timing = ("swung" if swing["detected"] else "straight" if alignment >= t["straight_alignment"] else "unknown") if enough else "unknown"
    aligned_cells = {round(beat * 4) for beat, _ in aligned}
    odd = sum(cell % 2 == 1 for cell in aligned_cells)
    odd_share = odd / len(aligned_cells) if aligned_cells else 0.0
    kick_events = role_events["kick"]
    snare_events = role_events["snare"]
    kick_odd = sum(abs(beat - round(beat * 4) / 4) <= tol and round(beat * 4) % 2 for beat, _ in kick_events)
    kick_odd_share = kick_odd / len(kick_events) if kick_events else None
    if not enough:
        subdivision = "unknown"
    elif timing == "swung":
        subdivision = "eighth" if swing["unit_beats"] == 1 else "sixteenth"
    elif odd >= t["sixteenth_min_count"] and (odd_share >= t["sixteenth_min_share"] or
            (kick_odd >= t["sixteenth_min_count"] and (kick_odd_share or 0) >= t["kick_sixteenth_min_share"])):
        subdivision = "sixteenth"
    elif alignment >= t["straight_alignment"] and (odd <= 1 or odd_share <= t["eighth_max_share"]):
        subdivision = "eighth"
    else:
        subdivision = "mixed"

    # Velocity-weighted weak-position accents and a following stronger-beat rest.
    kicks_on_grid = [(round(beat * 4), velocity) for beat, velocity in kick_events if abs(beat - round(beat * 4) / 4) <= tol]
    kick_cells = {cell for cell, _ in kicks_on_grid}
    weak = [(cell, velocity) for cell, velocity in kicks_on_grid if cell % 4]
    rest_anticipations = sum((cell // 4 + 1) * 4 not in kick_cells for cell, _ in weak)
    velocity_total = sum(velocity for _, velocity in kicks_on_grid)
    weak_accent = sum(velocity for _, velocity in weak) / velocity_total if velocity_total else 0.0
    eighth_off = sum(cell % 4 == 2 for cell, _ in kicks_on_grid) / len(kicks_on_grid) if kicks_on_grid else 0.0
    anticipation_share = rest_anticipations / len(kicks_on_grid) if kicks_on_grid else 0.0
    sync_score = (0.4 * (kick_odd_share or 0) + 0.2 * eighth_off + 0.25 * weak_accent + 0.15 * anticipation_share) if len(kicks_on_grid) >= 4 else None
    syncopation = _level(sync_score, t["syncopation_medium"], t["syncopation_high"]) if sync_score is not None else "unknown"

    signatures = {(event["numerator"], event["denominator"]) for event in context["time_signature_events"]} or {(4, 4)}
    accented = [(beat, velocity) for beat, velocity in snare_events if velocity >= max((v for _, v in snare_events), default=0) * t["snare_accent_velocity_share"]]
    backbeat_shares = {}
    for name, positions in {"standard": [1, 3], "half_time": [2], "double_time": [0.5, 1.5, 2.5, 3.5]}.items():
        hits = sum(any(min(abs(beat % 4 - position), 4 - abs(beat % 4 - position)) <= tol for position in positions) for beat, _ in accented)
        backbeat_shares[name] = hits / len(accented) if accented else 0.0
    backbeat = "unknown"
    if signatures == {(4, 4)} and len(accented) >= t["backbeat_min_snares"]:
        backbeat = "other"
        for name in ("standard", "half_time", "double_time"):
            positions = {"standard": [1, 3], "half_time": [2], "double_time": [0.5, 1.5, 2.5, 3.5]}[name]
            coverage = all(sum(abs(beat % 4 - pos) <= tol for beat, _ in accented) >= max(1, math.floor(bars * t["backbeat_bar_coverage"])) for pos in positions)
            if backbeat_shares[name] >= t["backbeat_match_share"] and coverage:
                backbeat = name
                break

    kick_times = sorted({beat for beat, _ in kick_events})
    runs, current_run = [], 1
    for left, right in zip(kick_times, kick_times[1:]):
        if abs(right - left - 0.25) <= tol:
            current_run += 1
        else:
            if current_run >= t["double_kick_min_run"]:
                runs.append(current_run)
            current_run = 1
    if kick_times and current_run >= t["double_kick_min_run"]:
        runs.append(current_run)
    run_share = sum(runs) / len(kick_times) if kick_times else None
    run_density = sum(runs) / bars if bars else 0.0
    double_kick = "unknown" if not kick_times else "none"
    if runs:
        double_kick = "light"
        if run_share >= t["double_kick_medium_share"]:
            double_kick = "medium"
        if run_share >= t["double_kick_heavy_share"] and run_density >= t["double_kick_heavy_notes_per_bar"]:
            double_kick = "heavy"

    counts = {role: len(events) for role, events in role_events.items()}
    rates = {role: count / duration if duration else 0.0 for role, count in counts.items()}
    cymbal_rate = rates["hihat"] + rates["cymbal"]
    density_index = (0.4 * min(rates["kick"] / t["density_kick_high_per_quarter"], 1)
                     + 0.2 * min(rates["snare"] / t["density_snare_high_per_quarter"], 1)
                     + 0.4 * min(cymbal_rate / t["density_cymbal_high_per_quarter"], 1))
    density = _level(density_index, t["density_medium"], t["density_high"]) if notes and duration else "unknown"
    groove = {"subdivision": subdivision, "timing_feel": timing, "syncopation": syncopation,
              "backbeat": backbeat, "double_kick": double_kick, "density": density,
              "tightness_score": round(tightness, 6) if tightness is not None else None}
    raw_count = len(notes) + sum(ignored.values())
    coverage = len(notes) / raw_count if raw_count else 0.0
    evidence_confidence = min(1, len(onsets) / 16) * coverage
    warnings = []
    if ignored:
        warnings.append("Unassigned notes were excluded from groove metrics; GM names are only mapping assumptions. Review the configured drum map.")
    if not kick_events:
        warnings.append("No configured kick notes: syncopation and double-kick classifications are unknown.")
    if not snare_events or signatures != {(4, 4)}:
        warnings.append("Backbeat interpretation requires configured snares and a consistent 4/4 meter.")
    if not context["time_signature_events"]:
        warnings.append("No explicit meter: 4/4 is assumed for the estimates.")
    if not enough:
        warnings.append("Insufficient onset evidence for subdivision/timing classification.")
    return {
        "analysis_version": ANALYSIS_VERSION, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "groove": groove, "confidence": {key: round(evidence_confidence if value not in ("unknown", None) else 0, 4) for key, value in groove.items()},
        "metrics": {
            "ticks_per_beat": midi.ticks_per_beat, "duration_quarter_beats": duration,
            "estimated_bars": bars, "tempo_events": context["tempo_events"],
            "time_signature_events": context["time_signature_events"],
            "raw_note_on_count": raw_count, "analyzed_note_on_count": len(notes),
            "mapping_coverage": round(coverage, 6), "role_note_counts": counts,
            "ignored_note_counts": [{"note": note, "count": count, "gm_name": GM_DRUM_MAP.get(note)} for note, count in sorted(ignored.items())],
            "outside_gm_notes": sorted(note for note in ignored if note not in GM_DRUM_MAP),
            "unique_onset_count": len(onsets), "grid_alignment_share": round(alignment, 6),
            "mean_grid_error_beats": round(statistics.fmean(errors), 6) if errors else None,
            "sixteenth_off_count": odd, "sixteenth_off_share": round(odd_share, 6),
            "kick_sixteenth_off_share": round(kick_odd_share, 6) if kick_odd_share is not None else None,
            "swing": swing, "syncopation_score": round(sync_score, 6) if sync_score is not None else None,
            "kick_weak_accent_share": round(weak_accent, 6), "kick_rest_anticipation_share": round(anticipation_share, 6),
            "backbeat_accented_snare_count": len(accented), "backbeat_position_shares": backbeat_shares,
            "backbeat_context": "Quarter-note pulse and accented snares; BPM half/double interpretations are not inferred.",
            "double_kick_run_lengths": runs, "double_kick_longest_run": max(runs, default=0),
            "double_kick_run_note_share": round(run_share, 6) if run_share is not None else None,
            "double_kick_runs_per_bar": round(len(runs) / bars, 6) if bars else None,
            "role_notes_per_quarter": {key: round(value, 6) for key, value in rates.items()},
            "role_notes_per_bar": {key: round(count / bars, 6) if bars else None for key, count in counts.items()},
            "density_index": round(density_index, 6),
            "overall_notes_per_quarter": round(raw_count / duration, 6) if duration else None,
            "analyzed_notes_per_quarter": round(len(notes) / duration, 6) if duration else None,
            "role_density": {"kick": _level(rates["kick"], t["density_kick_high_per_quarter"] / 2, t["density_kick_high_per_quarter"]) if kick_events else "unknown",
                             "snare": _level(rates["snare"], t["density_snare_high_per_quarter"] / 2, t["density_snare_high_per_quarter"]) if snare_events else "unknown",
                             "cymbal_hihat": _level(cymbal_rate, t["density_cymbal_high_per_quarter"] / 2, t["density_cymbal_high_per_quarter"]) if cymbal_rate else "unknown"},
        },
        "warnings": warnings,
    }
