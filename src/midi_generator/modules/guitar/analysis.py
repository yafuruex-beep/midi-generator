"""Deterministic guitar MIDI features; labels describe notes, not physical technique."""
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import statistics

from .timeline import bar_segments, read_timeline

ANALYSIS_VERSION = "1.0"
RIFF_CHARACTERISTICS = ("chug", "syncopated_riff", "gallop", "stop_and_go", "power_chord_riff",
                        "single_note_riff", "open_chord_riff", "tremolo_like", "mixed", "unknown")
DEFAULT_CONFIG = {
    "excluded_notes": [],
    "thresholds": {
        "onset_tolerance_beats": 0.04, "legato_overlap_beats": 0.08, "min_chord_overlap_beats": 0.03,
        "mostly_monophonic_ratio": 0.85, "chord_based_multi_ratio": 0.60, "chord_attack_time_ratio": 0.50,
        "grid_tolerance_beats": 0.04, "minimum_onsets": 8, "sixteenth_min_count": 4,
        "sixteenth_min_share": 0.20, "eighth_max_share": 0.05, "grid_alignment_min": 0.85,
        "syncopation_medium": 0.25, "syncopation_high": 0.55,
        "density_medium_per_quarter": 1.0, "density_high_per_quarter": 3.0,
        "short_note_beats": 0.25, "long_note_beats": 1.0, "rest_min_beats": 0.125,
        "tail_tolerance_beats": 0.04, "repetition_tolerance_beats": 0.03, "repetition_similar_ratio": 0.85,
        "power_chord_ratio": 0.60, "minimum_chord_groups": 4,
        "chug_pitch_ceiling": 48, "chug_low_share": 0.60, "chug_short_share": 0.60, "chug_repeated_pitch_share": 0.30,
        "stop_gap_beats": 0.50, "stop_min_gaps": 2, "stop_rest_share": 0.15,
        "open_chord_long_share": 0.50, "tremolo_short_share": 0.75, "tremolo_interval_share": 0.70,
        "tremolo_min_onsets": 16, "gallop_min_patterns": 3, "gallop_min_share": 0.50,
        "possible_control_note_ceiling": 24, "possible_control_min_count": 4,
        "possible_control_co_onset_share": 0.80,
    },
}
PITCH_CLASSES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def load_guitar_config(path=None):
    config = deepcopy(DEFAULT_CONFIG)
    if path is not None:
        overrides = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(overrides, dict):
            raise ValueError("Guitar configuration must be an object")
        if set(overrides) - {"excluded_notes", "thresholds"}:
            raise ValueError("Guitar config supports excluded_notes and thresholds")
        if not isinstance(overrides.get("thresholds", {}), dict) or set(overrides.get("thresholds", {})) - set(config["thresholds"]):
            raise ValueError("Unknown or invalid guitar thresholds")
        config["thresholds"].update(overrides.get("thresholds", {}))
        config["excluded_notes"] = overrides.get("excluded_notes", [])
    if not isinstance(config["excluded_notes"], list) or any(isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 127 for n in config["excluded_notes"]):
        raise ValueError("excluded_notes must be a list of MIDI note numbers")
    t = config["thresholds"]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in t.values()):
        raise ValueError("Guitar thresholds must be finite positive numbers")
    for key, value in t.items():
        if (key.endswith(("_ratio", "_share")) or key == "grid_alignment_min") and value > 1:
            raise ValueError(f"{key} must not exceed 1")
    for key in ("minimum_onsets", "sixteenth_min_count", "minimum_chord_groups", "stop_min_gaps", "tremolo_min_onsets", "gallop_min_patterns", "possible_control_min_count"):
        if not isinstance(t[key], int):
            raise ValueError(f"{key} must be an integer")
    if t["onset_tolerance_beats"] >= 0.125 or t["grid_tolerance_beats"] >= 0.125:
        raise ValueError("Onset/grid tolerances must be smaller than half a sixteenth note")
    for lower, upper in (("syncopation_medium", "syncopation_high"), ("density_medium_per_quarter", "density_high_per_quarter"), ("short_note_beats", "long_note_beats")):
        if t[lower] >= t[upper]:
            raise ValueError(f"{lower} must be less than {upper}")
    return config


def stats(values):
    return {"count": len(values), "minimum": min(values) if values else None,
            "maximum": max(values) if values else None,
            "mean": statistics.fmean(values) if values else None,
            "median": statistics.median(values) if values else None}


def onset_groups(notes, tolerance_ticks):
    groups = []
    for index, note in enumerate(notes):
        if not groups or note["start_tick"] - groups[-1]["start_tick"] > tolerance_ticks:
            groups.append({"start_tick": note["start_tick"], "indices": [], "notes": []})
        groups[-1]["indices"].append(index)
        groups[-1]["notes"].append(note)
    return groups


def chord_features(groups, tpb, thresholds):
    structures, supported = [], []
    rejected = 0
    for group in groups:
        pitches = sorted({note["note"] for note in group["notes"]})
        if len(pitches) < 2:
            continue
        start = max(note["start_tick"] for note in group["notes"])
        end = min(note["end_tick"] for note in group["notes"])
        if (end - start) / tpb < thresholds["min_chord_overlap_beats"]:
            rejected += 1
            continue
        intervals = [pitch - pitches[0] for pitch in pitches]
        classes = sorted({interval % 12 for interval in intervals})
        pcs = {pitch % 12 for pitch in pitches}
        third = any((right - left) % 12 in {3, 4, 8, 9} for left in pitches for right in pitches if left != right)
        triad = any({(pc - root) % 12 for pc in pcs} in ({0, 4, 7}, {0, 3, 7}, {0, 3, 6}, {0, 4, 8}) for root in pcs)
        if set(classes) <= {0, 7} and 7 in classes:
            classification = "power_chord"
        elif len(pcs) == 3 and triad:
            classification = "triad"
        elif len(pcs) >= 4 and third:
            classification = "extended_chord"
        else:
            classification = "other"
        structures.append({"start_beats": group["start_tick"] / tpb, "notes": pitches,
                           "intervals_from_lowest": intervals, "interval_classes": classes,
                           "third_present": third, "classification": classification,
                           "common_overlap_beats": (end - start) / tpb})
        supported.append((start, sorted(note["end_tick"] for note in group["notes"])[-2]))
    counts = Counter(item["classification"] for item in structures)
    ratios = {name + "_ratio": counts[name] / len(structures) if structures else None
              for name in ("power_chord", "triad", "extended_chord", "other")}
    classification = "unknown"
    if structures:
        classification = next((name for name in ("power_chord", "triad", "extended_chord")
                               if ratios[name + "_ratio"] >= thresholds["power_chord_ratio"]), "other")
    return {"classification": classification, "group_count": len(structures), **ratios,
            "rejected_short_overlap_groups": rejected, "structures": structures,
            "root_or_key_inferred": False}, supported


def polyphony_features(notes, end_tick, tpb, supported, thresholds):
    changes = defaultdict(lambda: {"start": [], "end": []})
    changes[0]; changes[end_tick]
    for index, note in enumerate(notes):
        if note["end_tick"] > note["start_tick"]:
            changes[note["start_tick"]]["start"].append(index)
            changes[note["end_tick"]]["end"].append(index)
    active, previous, segments = set(), 0, []
    for tick, change in sorted(changes.items()):
        if tick > previous:
            segments.append({"start": previous, "end": tick, "voices": len(active)})
        active.difference_update(change["end"])
        active.update(change["start"])
        previous = tick
    # Suppress short contiguous overlap regions only when no simultaneous chord
    # attack supports them; never suppress a real short staccato chord.
    corrected = [segment["voices"] for segment in segments]
    position = 0
    while position < len(segments):
        if segments[position]["voices"] < 2:
            position += 1
            continue
        stop = position + 1
        while stop < len(segments) and segments[stop]["voices"] >= 2:
            stop += 1
        left, right = segments[position]["start"], segments[stop - 1]["end"]
        has_chord = any(start < right and end > left for start, end in supported)
        if (right - left) / tpb <= thresholds["legato_overlap_beats"] and not has_chord:
            for index in range(position, stop):
                corrected[index] = 1
        position = stop
    durations = Counter()
    effective = Counter()
    chord_time = 0
    for segment, voices in zip(segments, corrected):
        length = segment["end"] - segment["start"]
        durations[segment["voices"]] += length
        effective[voices] += length
        midpoint = (segment["start"] + segment["end"]) / 2
        if segment["voices"] >= 2 and any(start <= midpoint < end for start, end in supported):
            chord_time += length
    sounding = sum(length for voices, length in durations.items() if voices)
    def ratios(histogram):
        return {"monophonic_ratio": histogram[1] / sounding if sounding else None,
                "two_note_ratio": histogram[2] / sounding if sounding else None,
                "three_plus_ratio": sum(length for voices, length in histogram.items() if voices >= 3) / sounding if sounding else None}
    raw_ratios, adjusted = ratios(durations), ratios(effective)
    multi_ratio = 1 - adjusted["monophonic_ratio"] if sounding else None
    chord_share = chord_time / sounding if sounding else None
    maximum = max((segment["voices"] for segment in segments), default=0)
    if not sounding:
        classification = "unknown"
    elif maximum <= 1:
        classification = "monophonic"
    elif adjusted["monophonic_ratio"] >= thresholds["mostly_monophonic_ratio"]:
        classification = "mostly_monophonic"
    elif multi_ratio >= thresholds["chord_based_multi_ratio"] and chord_share >= thresholds["chord_attack_time_ratio"]:
        classification = "chord_based"
    else:
        classification = "mixed"
    rests = [(segment["start"], segment["end"]) for segment in segments if segment["voices"] == 0]
    return {"classification": classification, **raw_ratios, "effective_ratios": adjusted,
            "max_simultaneous_notes": maximum,
            "mean_simultaneous_notes": sum(voices * length for voices, length in durations.items()) / sounding if sounding else None,
            "mean_including_silence": sum(voices * length for voices, length in durations.items()) / end_tick if end_tick else None,
            "sounding_beats": sounding / tpb, "rest_ratio": durations[0] / end_tick if end_tick else None,
            "supported_chord_time_ratio": chord_share,
            "ratio_basis": "Duration in quarter-note beats during sounding time; short unsupported legato overlaps are separate effective ratios."}, rests


def repetition_features(notes, groups, bars, tpb, thresholds):
    empty = {"pitch_rhythm_period_bars": None, "rhythm_period_bars": None, "candidates": [], "status": "insufficient_bars"}
    complete = [bar for bar in bars if bar["complete"]]
    if len(complete) < 2:
        return empty
    if len({tuple(bar["time_signature"]) for bar in bars}) > 1:
        return {**empty, "status": "unknown_across_meter_changes"}
    fingerprints, rhythms = [], []
    for bar in complete:
        left, right = bar["start_tick"], bar["end_tick"]
        fingerprints.append(sorted((note["start_tick"] - left, note["note"], note["end_tick"] - note["start_tick"])
                                   for note in notes if left <= note["start_tick"] < right))
        rhythms.append([(group["start_tick"] - left, max(note["end_tick"] for note in group["notes"]) - group["start_tick"])
                        for group in groups if left <= group["start_tick"] < right])
    tolerance = thresholds["repetition_tolerance_beats"] * tpb
    def similar(first, second, pitches):
        if len(first) != len(second) or not first:
            return False
        for left, right in zip(first, second):
            if pitches and left[1] != right[1]:
                return False
            if abs(left[0] - right[0]) > tolerance or abs(left[-1] - right[-1]) > tolerance:
                return False
        return True
    candidates = []
    for period in range(1, len(complete) // 2 + 1):
        pairs = [(index - period, index) for index in range(period, len(complete))]
        pairs = [(a, b) for a, b in pairs if fingerprints[a] or fingerprints[b]]
        count = len(pairs)
        if not count:
            continue
        exact = sum(fingerprints[a] == fingerprints[b] for a, b in pairs) / count
        rhythm_exact = sum(rhythms[a] == rhythms[b] for a, b in pairs) / count
        near = sum(similar(fingerprints[a], fingerprints[b], True) for a, b in pairs) / count
        rhythm_near = sum(similar(rhythms[a], rhythms[b], False) for a, b in pairs) / count
        if max(exact, rhythm_exact, near, rhythm_near) >= thresholds["repetition_similar_ratio"]:
            candidates.append({"period_bars": period, "compared_bar_pairs": count,
                               "pitch_rhythm_exact_ratio": exact, "rhythm_exact_ratio": rhythm_exact,
                               "pitch_rhythm_similar_ratio": near, "rhythm_similar_ratio": rhythm_near})
    def preferred(kind):
        for key, threshold in ((kind + "_exact_ratio", 1), (kind + "_similar_ratio", thresholds["repetition_similar_ratio"])):
            hits = [item["period_bars"] for item in candidates if item[key] >= threshold]
            if hits:
                return min(hits)
        return None
    return {"pitch_rhythm_period_bars": preferred("pitch_rhythm"), "rhythm_period_bars": preferred("rhythm"),
            "candidates": candidates, "status": "analyzed", "complete_bar_count": len(complete),
            "exact_definition": "Identical MIDI onset ticks, pitches and durations, ignoring velocity; rhythm-only uses grouped attacks.",
            "similar_tolerance_beats": thresholds["repetition_tolerance_beats"]}


def analyze_guitar_midi(path, *, source_id=None, style=None, config_path=None, config=None):
    config = load_guitar_config(config_path) if config is None else deepcopy(config)
    t = config["thresholds"]
    data = read_timeline(path, set(config["excluded_notes"]))
    notes, tpb = data["notes"], data["midi"].ticks_per_beat
    timeline, context = data["timeline"], data["context"]
    duration_beats = data["end_tick"] / tpb
    duration_seconds = timeline.seconds(data["end_tick"])
    groups = onset_groups(notes, t["onset_tolerance_beats"] * tpb)
    bars = bar_segments(data, t["tail_tolerance_beats"])
    chords, supported = chord_features(groups, tpb, t)
    polyphony, rests = polyphony_features(notes, data["end_tick"], tpb, supported, t)
    pitches = [note["note"] for note in notes]
    pitch_counts = Counter(pitches)
    pitch = {"min_note": min(pitches) if pitches else None, "max_note": max(pitches) if pitches else None,
             "mean_note": statistics.fmean(pitches) if pitches else None,
             "median_note": statistics.median(pitches) if pitches else None,
             "used_note_count": len(pitch_counts), "note_on_count": len(notes),
             "note_counts": {str(note): count for note, count in sorted(pitch_counts.items())},
             "pitch_classes": {name: sum(count for note, count in pitch_counts.items() if note % 12 == index) for index, name in enumerate(PITCH_CLASSES)}}
    durations = [(note["end_tick"] - note["start_tick"]) / tpb for note in notes]
    seconds = [timeline.seconds(note["end_tick"]) - timeline.seconds(note["start_tick"]) for note in notes]
    short_share = sum(value <= t["short_note_beats"] for value in durations) / len(notes) if notes else None
    long_share = sum(value >= t["long_note_beats"] for value in durations) / len(notes) if notes else None
    rest_beats = [(end - start) / tpb for start, end in rests]
    rest_seconds = [timeline.seconds(end) - timeline.seconds(start) for start, end in rests]
    note_duration = {"beats": stats(durations), "seconds": stats(seconds), "short_note_ratio": short_share,
                     "long_note_ratio": long_share, "rests_beats": stats(rest_beats), "rests_seconds": stats(rest_seconds),
                     "rest_intervals": [{"start_beats": start / tpb, "end_beats": end / tpb,
                                         "duration_beats": (end - start) / tpb,
                                         "duration_seconds": timeline.seconds(end) - timeline.seconds(start)} for start, end in rests]}
    onsets = [group["start_tick"] / tpb for group in groups]
    intervals = [right - left for left, right in zip(onsets, onsets[1:])]
    tol = t["grid_tolerance_beats"]
    errors = [abs(beat - round(beat * 4) / 4) for beat in onsets]
    aligned = [(round(group["start_tick"] / tpb * 4), max(note["velocity"] for note in group["notes"]), group)
               for group in groups if abs(group["start_tick"] / tpb - round(group["start_tick"] / tpb * 4) / 4) <= tol]
    cells = {cell for cell, _, _ in aligned}
    odd = sum(cell % 2 for cell in cells)
    odd_share = odd / len(cells) if cells else 0.0
    alignment = len(aligned) / len(groups) if groups else None
    if len(groups) < t["minimum_onsets"]:
        subdivision = "unknown"
    elif odd >= t["sixteenth_min_count"] and odd_share >= t["sixteenth_min_share"]:
        subdivision = "sixteenth"
    elif alignment >= t["grid_alignment_min"] and (odd <= 1 or odd_share <= t["eighth_max_share"]):
        subdivision = "eighth"
    else:
        subdivision = "mixed"
    def metrical_position(group):
        tick = group["start_tick"]
        bar = next((item for item in bars if item["start_tick"] <= tick < item["end_tick"]), None)
        numerator, denominator = bar["time_signature"] if bar else (4, 4)
        pulse = 4 / denominator * (3 if denominator == 8 and numerator > 3 and numerator % 3 == 0 else 1)
        start = bar["start_tick"] / tpb if bar else 0
        offset = tick / tpb - start
        is_weak = abs(offset / pulse - round(offset / pulse)) * pulse > tol
        next_pulse = start + (math.floor(offset / pulse) + 1) * pulse
        if bar:
            next_pulse = min(next_pulse, bar["end_tick"] / tpb)
        return is_weak, next_pulse
    weak = [(cell, velocity, group) for cell, velocity, group in aligned if metrical_position(group)[0]]
    velocity_sum = sum(velocity for _, velocity, _ in aligned)
    accent_share = sum(velocity for _, velocity, _ in weak) / velocity_sum if velocity_sum else 0.0
    eighth_share = sum(cell % 4 == 2 for cell, _, _ in weak) / len(aligned) if aligned else 0.0
    weak_after_rest = sum(any(0 <= group["start_tick"] - end <= tol * tpb and (end - start) / tpb >= t["rest_min_beats"] for start, end in rests)
                          for _, _, group in weak) / len(aligned) if aligned else 0.0
    held_over = sum(not any(abs(onset - metrical_position(group)[1]) <= tol for onset in onsets)
                    and max(note["end_tick"] for note in group["notes"]) / tpb > metrical_position(group)[1] + tol
                    for _, _, group in weak) / len(aligned) if aligned else 0.0
    score = 0.35 * odd_share + 0.20 * eighth_share + 0.20 * accent_share + 0.15 * weak_after_rest + 0.10 * held_over if len(aligned) >= 4 else None
    if any(bar["time_signature"][1] not in {4, 8} for bar in bars):
        score = None
        data["warnings"].append("Syncopation pulse interpretation is unknown for denominators other than 4 or 8.")
    syncopation = "unknown" if score is None else "high" if score >= t["syncopation_high"] else "medium" if score >= t["syncopation_medium"] else "low"
    rate = len(groups) / duration_beats if duration_beats else None
    density = "unknown" if rate is None or not notes else "high" if rate >= t["density_high_per_quarter"] else "medium" if rate >= t["density_medium_per_quarter"] else "low"
    per_bar = []
    for bar in bars:
        count = sum(bar["start_tick"] <= note["start_tick"] < bar["end_tick"] for note in notes)
        attacks = sum(bar["start_tick"] <= group["start_tick"] < bar["end_tick"] for group in groups)
        elapsed = timeline.seconds(bar["end_tick"]) - timeline.seconds(bar["start_tick"])
        per_bar.append({**bar, "start_beats": bar["start_tick"] / tpb, "end_beats": bar["end_tick"] / tpb,
                        "duration_seconds": elapsed, "note_count": count, "onset_group_count": attacks,
                        "notes_per_second": count / elapsed if elapsed else None,
                        "notes_per_quarter": count / bar["quarter_beats"] if bar["quarter_beats"] else None})
    rhythm = {"subdivision": subdivision, "syncopation": syncopation, "density": density,
              "onset_group_count": len(groups), "notes_per_second": len(notes) / duration_seconds if duration_seconds else None,
              "notes_per_quarter": len(notes) / duration_beats if duration_beats else None,
              "notes_per_bar": len(notes) / context["estimated_bars"] if context["estimated_bars"] else None,
              "onset_groups_per_quarter": rate, "per_bar": per_bar, "inter_onset_beats": stats(intervals),
              "sixteenth_alignment_share": alignment, "sixteenth_off_count": odd, "sixteenth_off_share": odd_share,
              "tightness_score": statistics.fmean(max(0, 1 - error / 0.125) for error in errors) if errors else None,
              "syncopation_score": score, "weak_accent_share": accent_share,
              "weak_after_rest_share": weak_after_rest, "held_over_strong_beat_share": held_over,
              "onset_positions_beats": onsets}
    # Detect repeated .5/.25/.25 or reversed gallop groups, without calling triplets gallops.
    gallop_matches = gallop_total = 0
    for offset in range(3):
        triples = [intervals[index:index + 3] for index in range(offset, len(intervals) - 2, 3)]
        matched = sum(any(all(abs(a - b) <= tol for a, b in zip(values, template)) for template in ([0.5, 0.25, 0.25], [0.25, 0.25, 0.5], [0.25, 0.5, 0.25])) for values in triples)
        if matched > gallop_matches:
            gallop_matches, gallop_total = matched, len(triples)
    rhythm["gallop_pattern_count"] = gallop_matches
    rhythm["gallop_pattern_share"] = gallop_matches / gallop_total if gallop_total else 0.0
    repetition = repetition_features(notes, groups, bars, tpb, t)
    evidence = {}
    def label(name, condition, metrics, interpretation):
        if condition:
            evidence[name] = {"confidence": None, "metrics": metrics, "interpretation": interpretation}
    enough = len(groups) >= t["minimum_onsets"]
    low_share = sum(note <= t["chug_pitch_ceiling"] for note in pitches) / len(notes) if notes else 0.0
    repeated_share = max(pitch_counts.values(), default=0) / len(notes) if notes else 0.0
    label("chug", enough and low_share >= t["chug_low_share"] and (short_share or 0) >= t["chug_short_share"] and repeated_share >= t["chug_repeated_pitch_share"],
          {"low_pitch_share": low_share, "short_note_share": short_share, "most_frequent_pitch_share": repeated_share}, "Low, short repeated MIDI pitches; palm muting is not established.")
    label("syncopated_riff", enough and subdivision in {"sixteenth", "mixed"} and syncopation == "high" and odd >= t["sixteenth_min_count"],
          {"syncopation_score": score, "sixteenth_off_count": odd}, "Position/accent/rest evidence, not note density alone.")
    label("gallop", gallop_matches >= t["gallop_min_patterns"] and rhythm["gallop_pattern_share"] >= t["gallop_min_share"],
          {"pattern_count": gallop_matches, "pattern_share": rhythm["gallop_pattern_share"]}, "Repeated long/short/short onset intervals.")
    large_rests = sum((end - start) / tpb >= t["stop_gap_beats"] and start > 0 and end < data["end_tick"] for start, end in rests)
    label("stop_and_go", enough and large_rests >= t["stop_min_gaps"] and (polyphony["rest_ratio"] or 0) >= t["stop_rest_share"],
          {"large_rest_count": large_rests, "rest_ratio": polyphony["rest_ratio"]}, "Contrasting MIDI sounding intervals and silence.")
    label("power_chord_riff", chords["group_count"] >= t["minimum_chord_groups"] and (chords["power_chord_ratio"] or 0) >= t["power_chord_ratio"],
          {"power_chord_ratio": chords["power_chord_ratio"], "chord_group_count": chords["group_count"]}, "Observed fifth/octave pitch structure; no tuning or fret position inferred.")
    label("single_note_riff", enough and polyphony["classification"] in {"monophonic", "mostly_monophonic"},
          {"polyphony": polyphony["classification"]}, "Single-note texture after short legato-overlap handling.")
    label("open_chord_riff", chords["group_count"] >= t["minimum_chord_groups"] and polyphony["classification"] == "chord_based" and (long_share or 0) >= t["open_chord_long_share"],
          {"long_note_share": long_share}, "Long chord texture; open strings and playing position are not established.")
    fast_share = sum(any(abs(value - target) <= tol for target in (0.125, 0.25)) for value in intervals) / len(intervals) if intervals else 0.0
    label("tremolo_like", len(groups) >= t["tremolo_min_onsets"] and (short_share or 0) >= t["tremolo_short_share"] and fast_share >= t["tremolo_interval_share"],
          {"short_note_share": short_share, "fast_interval_share": fast_share}, "Repeated short note events; picking direction/technique is unknown.")
    labels = list(evidence)
    if len(labels) > 1:
        evidence["mixed"] = {"confidence": None, "metrics": {"features": list(labels)}, "interpretation": "Multiple observed feature categories coexist."}
        labels.append("mixed")
    if not labels:
        labels = ["unknown"]
        evidence["unknown"] = {"confidence": None, "metrics": {"onset_group_count": len(groups)}, "interpretation": "No requested characteristic met its evidence thresholds."}
    possible_control = []
    for note, count in sorted(pitch_counts.items()):
        if note >= t["possible_control_note_ceiling"] or count < t["possible_control_min_count"]:
            continue
        events = [event for event in notes if event["note"] == note]
        co = sum(any(other["note"] >= t["possible_control_note_ceiling"] and abs(other["start_tick"] - event["start_tick"]) <= t["onset_tolerance_beats"] * tpb for other in notes) for event in events) / count
        if co >= t["possible_control_co_onset_share"]:
            possible_control.append({"note": note, "event_count": count, "higher_pitch_co_onset_share": co})
    warnings = data["warnings"]
    if possible_control:
        warnings.append("Very low notes repeatedly coincide with higher notes and may be articulation/control keys. They were retained; pitch/polyphony/chord labels may include control events until a source map is confirmed.")
    if not notes:
        warnings.append("No positive-velocity notes remain for guitar feature analysis.")
    if len({tuple(bar["time_signature"]) for bar in bars}) > 1:
        warnings.append("Meter changes were used for durations/density; cross-meter repetition is unknown.")
    warnings.append("Technique labels are MIDI-feature estimates; tuning, palm muting, picking direction, acoustic sustain and keys are not inferred.")
    result = {
        "schema_version": "1.0", "analysis_version": ANALYSIS_VERSION,
        "source_id": source_id or Path(path).as_posix(), "file_path": Path(path).as_posix(),
        "source_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "instrument": "guitar", "style": [style] if isinstance(style, str) else list(style or []), "status": "ok", "error": None,
        "source_context": {"file_format": data["midi"].type, "ticks_per_beat": tpb, "track_count": len(data["tracks"]),
                           "tracks": data["tracks"], "actual_midi_channels": data["channels"], "expected_midi_channel": 1,
                           "tempo_events": context["tempo_events"], "time_signature_events": context["time_signature_events"],
                           "duration_seconds": duration_seconds, "duration_quarter_beats": duration_beats,
                           "estimated_bars": context["estimated_bars"], "tuning": None,
                           "note_policy": "All positive note events are included unless explicitly excluded by configuration."},
        "note_pairing": data["pairing"], "pitch": pitch, "polyphony": polyphony, "rhythm": rhythm,
        "note_duration": note_duration, "chords": chords, "repetition": repetition,
        "riff_characteristics": labels, "riff_evidence": evidence,
        "possible_control_notes": possible_control, "warnings": warnings,
    }
    json.dumps(result, allow_nan=False)
    return result
