"""Read-only drum/guitar rhythm comparison in quarter-beat coordinates."""
from copy import deepcopy
import json
import math
from pathlib import Path

import mido

from midi_generator.modules.guitar.analysis import onset_groups
from midi_generator.modules.guitar.timeline import read_timeline

ALGORITHM_VERSION = "6.2-A.1"
DEFAULT_CONFIG = {
    "kick_notes": [35, 36], "snare_notes": [38, 40],
    "onset_tolerance_beats": 0.04, "tail_tolerance_beats": 0.04,
    "minimum_grid_alignment": 0.85, "seed_score_window": 0.02,
    "sync_weights": {"kick_alignment": 0.50, "grid_match": 0.12,
                     "syncopation_match": 0.12, "rest_compatibility": 0.14,
                     "snare_accent_relation": 0.05, "repetition_match": 0.07},
    "selection_weights": {"rhythm_sync": 0.70, "phrase_fit": 0.15,
                          "backing_suitability": 0.10, "riff_support": 0.05},
}


def load_sync_config(path=None):
    config = deepcopy(DEFAULT_CONFIG)
    if path is not None:
        supplied = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(supplied, dict) or set(supplied) - set(config):
            raise ValueError("Invalid guitar sync configuration keys")
        for key, value in supplied.items():
            if key.endswith("weights"):
                if not isinstance(value, dict) or set(value) - set(config[key]):
                    raise ValueError(f"Invalid {key}")
                config[key].update(value)
            else:
                config[key] = value
    for key in ("kick_notes", "snare_notes"):
        values = config[key]
        if not isinstance(values, list) or not values or any(type(n) is not int or not 0 <= n <= 127 for n in values):
            raise ValueError(f"{key} must contain MIDI note numbers 0–127")
        config[key] = sorted(set(values))
    if set(config["kick_notes"]) & set(config["snare_notes"]):
        raise ValueError("Kick and snare note maps must not overlap")
    for key in ("onset_tolerance_beats", "tail_tolerance_beats", "minimum_grid_alignment", "seed_score_window"):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"Invalid {key}")
    if not 0 < config["onset_tolerance_beats"] < 0.125 or config["tail_tolerance_beats"] >= 0.25:
        raise ValueError("Timing tolerances must be smaller than half a sixteenth beat")
    for key in ("sync_weights", "selection_weights"):
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in config[key].values()) or not math.isfinite(sum(config[key].values())) or sum(config[key].values()) <= 0:
            raise ValueError(f"{key} must contain finite nonnegative weights with a positive total")
    return config


def constant_context(data, signature, *, tempo=None):
    context = data["context"]
    meters = context["time_signature_events"]
    effective = {(e["numerator"], e["denominator"]) for e in meters}
    if not meters or meters[0]["tick"] > 0:
        effective.add((4, 4))
    if effective != {tuple(signature)}:
        raise ValueError(f"Time-signature mismatch/change is unsupported: source {sorted(effective)}, requested {tuple(signature)}")
    tempos = context["tempo_events"]
    values = {e["microseconds_per_beat"] for e in tempos}
    if not tempos or tempos[0]["tick"] > 0:
        values.add(500000)
    if len(values) > 1:
        raise ValueError("Variable/conflicting MIDI tempo is unsupported for backing synchronization")
    if tempo is not None and any(abs(value - mido.bpm2tempo(tempo)) > 1 for value in values):
        raise ValueError(f"Drum BPM mismatch: source {mido.tempo2bpm(next(iter(values))):g}, requested {tempo:g}; regenerate drums or change the request")
    if not tempos:
        data["warnings"].append("No tempo event: MIDI default 120 BPM was assumed explicitly.")
    if not meters:
        data["warnings"].append("No time-signature event: MIDI default 4/4 was assumed explicitly.")
    steps_per_bar = signature[0] * 16 / signature[1]
    if not steps_per_bar.is_integer():
        raise ValueError("Requested meter cannot be represented by complete sixteenth-grid steps")
    return int(steps_per_bar)


def attack_pattern(data, *, length_beats, source_beats=None, notes=None, tolerance=0.04):
    groups = onset_groups(data["notes"] if notes is None else notes, tolerance * data["midi"].ticks_per_beat)
    tpb = data["midi"].ticks_per_beat
    source_beats = source_beats or length_beats
    attacks = []
    for repetition in range(math.ceil(length_beats / source_beats)):
        offset = repetition * source_beats
        for group in groups:
            start = group["start_tick"] / tpb
            if start >= source_beats or start + offset >= length_beats:
                continue
            beat = start + offset
            error = abs(beat - round(beat * 4) / 4)
            attacks.append({"beat": beat, "velocity": max(n["velocity"] for n in group["notes"]),
                            "step_index": round(beat * 4) if error <= tolerance else None,
                            "grid_error_beats": error,
                            "end_beat": min(length_beats, offset + source_beats,
                                            offset + max(n["end_tick"] for n in group["notes"]) / tpb),
                            "note_count": len(group["notes"])})
    return sorted(attacks, key=lambda event: event["beat"])


def _grid(attacks, length_beats, tolerance):
    return {round(a["beat"] * 4) for a in attacks if a["beat"] < length_beats
            and 0 <= round(a["beat"] * 4) < round(length_beats * 4)
            and abs(a["beat"] - round(a["beat"] * 4) / 4) <= tolerance}


def _profile(attacks, bars, steps, tolerance):
    cells = _grid(attacks, bars * steps / 4, tolerance)
    return [sorted(cell - index * steps for cell in cells if index * steps <= cell < (index + 1) * steps) for index in range(bars)]


def analyze_drum_sync(path, *, tempo, bars, time_signature=(4, 4), config=None):
    config = config or load_sync_config()
    try:
        data = read_timeline(path, set())
    except Exception as exc:
        raise ValueError(f"Could not read drum MIDI {path}: {exc}") from exc
    steps = constant_context(data, time_signature, tempo=tempo)
    length = bars * steps / 4
    if data["end_tick"] / data["midi"].ticks_per_beat < length - config["tail_tolerance_beats"]:
        raise ValueError("Drum MIDI is shorter than the requested bars; drum looping is not implicit")
    if data["end_tick"] / data["midi"].ticks_per_beat > length + config["tail_tolerance_beats"]:
        data["warnings"].append("Only the requested opening bars of the longer drum reference were compared.")
    channels = {n["channel"] for n in data["notes"]}
    if not channels:
        raise ValueError("Drum MIDI contains no positive-velocity notes")
    if 10 in channels:
        notes = [n for n in data["notes"] if n["channel"] == 10]
        if len(channels) > 1:
            data["warnings"].append("Only channel 10 notes were used from the multi-channel drum reference.")
    elif len(channels) == 1:
        notes = data["notes"]
        data["warnings"].append("Explicit drum reference has a single nonstandard channel; GM note mapping was used without changing it.")
    else:
        raise ValueError("Ambiguous multi-channel drum reference without channel 10")
    kick = attack_pattern(data, length_beats=length, notes=[n for n in notes if n["note"] in config["kick_notes"]], tolerance=config["onset_tolerance_beats"])
    snare = attack_pattern(data, length_beats=length, notes=[n for n in notes if n["note"] in config["snare_notes"]], tolerance=config["onset_tolerance_beats"])
    if not kick:
        raise ValueError("No mapped kick notes in requested drum window; configure kick_notes instead of guessing")
    for attacks in (kick, snare):
        off_grid = sum(abs(a["beat"] - round(a["beat"] * 4) / 4) > config["onset_tolerance_beats"] for a in attacks)
        if attacks and 1 - off_grid / len(attacks) < config["minimum_grid_alignment"]:
            raise ValueError("Drum kick/snare attacks are off the straight sixteenth grid; no quantization was performed")
        if off_grid:
            data["warnings"].append(f"{off_grid} off-grid drum attack(s), possibly ornaments, were retained at their actual positions; grid step is null for those events.")
        for a in attacks:
            error = abs(a["beat"] - round(a["beat"] * 4) / 4)
            a.update(bar=int(a["beat"] // (steps / 4)) + 1,
                     position_beats=a["beat"] % (steps / 4),
                     grid_bar=round(a["beat"] * 4) // steps + 1 if error <= config["onset_tolerance_beats"] else None,
                     step=round(a["beat"] * 4) % steps if error <= config["onset_tolerance_beats"] else None,
                     nearest_step=round(a["beat"] * 4) % steps, grid_error_beats=error)
    kick_bars = _profile(kick, bars, steps, config["onset_tolerance_beats"])
    snare_bars = _profile(snare, bars, steps, config["onset_tolerance_beats"])
    snare_cells = [a["step"] for a in snare if a["step"] is not None]
    if tuple(time_signature) == (4, 4) and len(snare_cells) >= 2:
        half = sum(s == 8 for s in snare_cells) / len(snare_cells)
        standard = sum(s in {4, 12} for s in snare_cells) / len(snare_cells)
        backbeat = "half_time" if half >= .7 else "standard" if standard >= .7 else "other"
    else:
        backbeat = "unknown"
    period = next((p for p in range(1, bars // 2 + 1) if all(kick_bars[i] == kick_bars[i % p] for i in range(p, bars))), None)
    max_snare = max((a["velocity"] for a in snare), default=0)
    accents = [a["beat"] for a in snare if a["velocity"] >= .7 * max_snare]
    return {"kick": kick, "snare": snare, "bars": bars, "steps_per_bar": steps,
            "length_beats": length, "kick_period_bars": period, "backbeat": backbeat,
            "snare_accent_beats": accents,
            "bar_patterns": [{"bar": i + 1, "kick_steps": kick_bars[i], "snare_steps": snare_bars[i]} for i in range(bars)],
            "tempo_events": data["context"]["tempo_events"], "time_signature_events": data["context"]["time_signature_events"],
            "warnings": data["warnings"], "origin_policy": "MIDI tick 0 is bar 1; musical pickup/downbeat is not inferred."}


def _f1(left, right):
    if not left and not right:
        return 1.0
    return 2 * len(left & right) / (len(left) + len(right))


def _matches(left, right, tolerance):
    pairs, i, j = [], 0, 0
    while i < len(left) and j < len(right):
        delta = left[i]["beat"] - right[j]["beat"]
        if abs(delta) <= tolerance:
            pairs.append((left[i], right[j])); i += 1; j += 1
        elif delta < 0:
            i += 1
        else:
            j += 1
    return pairs


def rhythm_sync_score(drum, guitar_attacks, *, time_signature=(4, 4), config=None):
    config = config or load_sync_config()
    kick, snare = drum["kick"], drum["snare"]
    length, tolerance = drum["length_beats"], config["onset_tolerance_beats"]
    pairs = _matches(kick, guitar_attacks, tolerance)
    recall = len(pairs) / len(kick) if kick else 0.0
    precision = len(pairs) / len(guitar_attacks) if guitar_attacks else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accents = sum(k["velocity"] for k, _ in pairs) / sum(k["velocity"] for k in kick) if kick else 0.0
    kick_grid = _grid(kick, length, tolerance)
    guitar_grid = _grid(guitar_attacks, length, tolerance)
    alignment = sum(abs(a["beat"] - round(a["beat"] * 4) / 4) <= tolerance for a in guitar_attacks) / len(guitar_attacks) if guitar_attacks else 0.0
    numerator, denominator = time_signature
    pulse_steps = 16 / denominator * (3 if denominator == 8 and numerator > 3 and numerator % 3 == 0 else 1)
    weak = lambda cell: abs((cell % drum["steps_per_bar"]) / pulse_steps - round((cell % drum["steps_per_bar"]) / pulse_steps)) > 1e-9
    off_kick, off_guitar = {s for s in kick_grid if weak(s)}, {s for s in guitar_grid if weak(s)}
    cells = set(range(round(length * 4)))
    sounding = {cell for cell in cells if any(a["beat"] < (cell + 1) / 4 and a["end_beat"] > cell / 4 for a in guitar_attacks)}
    drum_rest, guitar_rest = cells - kick_grid, cells - sounding
    rest = len(drum_rest & guitar_rest) / len(drum_rest | guitar_rest) if drum_rest | guitar_rest else 1.0
    snare_pairs = _matches(snare, guitar_attacks, tolerance)
    snare_relation = (.5 + .5 * sum(min(g["velocity"] / max(s["velocity"], 1), 1) for s, g in snare_pairs) / len(snare)) if snare else None
    kick_bars = _profile(kick, drum["bars"], drum["steps_per_bar"], tolerance)
    guitar_bars = _profile(guitar_attacks, drum["bars"], drum["steps_per_bar"], tolerance)
    relationships = [(kick_bars[i] == kick_bars[j]) == (guitar_bars[i] == guitar_bars[j])
                     for i in range(drum["bars"]) for j in range(i + 1, drum["bars"])
                     if kick_bars[i] or kick_bars[j] or guitar_bars[i] or guitar_bars[j]]
    metrics = {"kick_alignment": .8 * f1 + .2 * accents,
               "grid_match": .5 * alignment + .5 * _f1(kick_grid, guitar_grid),
               "syncopation_match": _f1(off_kick, off_guitar), "rest_compatibility": rest,
               "snare_accent_relation": snare_relation,
               "repetition_match": sum(relationships) / len(relationships) if relationships else None}
    active = {key: weight for key, weight in config["sync_weights"].items() if metrics[key] is not None}
    total = sum(active.values())
    if total <= 0:
        raise ValueError("No active synchronization score weights")
    breakdown = {key: {"value": metrics[key], "weight": weight,
                       "contribution": metrics[key] * weight / total} for key, weight in active.items()}
    return {"overall_score": min(1.0, max(0.0, sum(item["contribution"] for item in breakdown.values()))), **metrics,
            "kick_recall": recall, "guitar_precision": precision, "kick_f1": f1,
            "kick_accent_coverage": accents, "matched_attack_count": len(pairs),
            "kick_attack_count": len(kick), "guitar_attack_count": len(guitar_attacks),
            "unnecessary_attack_count": len(guitar_attacks) - len(pairs),
            "grid_alignment": alignment, "score_breakdown": breakdown,
            "guitar_bar_patterns": guitar_bars,
            "guitar_rest_steps_by_bar": [sorted(cell - i * drum["steps_per_bar"] for cell in guitar_rest
                                                if i * drum["steps_per_bar"] <= cell < (i + 1) * drum["steps_per_bar"])
                                          for i in range(drum["bars"])],
            "policy": "Fit score, not musical quality; missing snare coincidences are neutral, not mandatory."}
