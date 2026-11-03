"""Explicit reference block plans without importing any MIDI generator."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from midi_generator.reference_analysis.pattern_map import load_reference_pattern_map
from midi_generator.reference_analysis.report import save_reference_analysis


def _hash(data):
    return "sha256:" + hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                                allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _range(value, name):
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or any(isinstance(n, bool) or not isinstance(n, int) for n in value)
            or not 1 <= value[0] <= value[1]):
        raise ValueError(f"{name} must be an inclusive [first, last] positive bar range")
    return tuple(value)


def create_reference_generation_plan(pattern_map_path, *, export_bpm, bar_range, section,
                                     time_signature, repeat_blocks=(),
                                     reference_bpm_confirmed=False, bar_start_confirmed=False):
    """Build a plan. Confirmation flags acknowledge this exact map's BPM/origin."""
    if (isinstance(export_bpm, bool) or not isinstance(export_bpm, (int, float))
            or not math.isfinite(export_bpm) or export_bpm <= 0):
        raise ValueError("export_bpm must be finite and positive")
    if not isinstance(section, str) or not section.strip():
        raise ValueError("section must be an explicit nonempty generation section")
    if any(type(value) is not bool for value in (reference_bpm_confirmed, bar_start_confirmed)):
        raise ValueError("Confirmation flags must be booleans")
    if (not isinstance(time_signature, (list, tuple)) or len(time_signature) != 2
            or any(type(n) is not int or n < 1 for n in time_signature)
            or time_signature[1] & (time_signature[1] - 1)):
        raise ValueError("time_signature must have positive integer numerator and power-of-two denominator")
    first, last = _range(bar_range, "bar_range")
    data = load_reference_pattern_map(pattern_map_path)
    if list(time_signature) != data["time_signature"]["value"]:
        raise ValueError("Reference/export meter conversion is unsupported")
    all_bars = {b["bar_index"]: b for b in data["bars"]}
    if any(i not in all_bars for i in range(first, last + 1)):
        raise ValueError("Requested bar range is outside the available Reference grid (possibly unknown BPM)")
    excluded = [i for i in range(first, last + 1) if all_bars[i]["partial"]]
    included = [i for i in range(first, last + 1) if i not in excluded]
    if not included:
        raise ValueError("No complete bars remain; partial bars are excluded")
    if excluded and any(included[0] < i < included[-1] for i in excluded):
        raise ValueError("An interior partial bar cannot be silently removed")
    if not isinstance(repeat_blocks, (list, tuple)):
        raise ValueError("repeat_blocks must be a list")
    groups = set()
    reuse = {}
    for item in repeat_blocks:
        if not isinstance(item, dict) or set(item) != {"source", "reuse"}:
            raise ValueError("Each repetition must contain only source and reuse ranges")
        source, target = _range(item["source"], "source"), _range(item["reuse"], "reuse")
        for left, right in (source, target):
            if not first <= left <= right <= last or any(i not in included for i in range(left, right + 1)):
                raise ValueError("Repetition references an excluded or out-of-range bar")
        if source[1] >= target[0] or source[1] - source[0] != target[1] - target[0]:
            raise ValueError("Reuse requires an earlier, nonoverlapping source with the same length")
        if target in reuse:
            raise ValueError("Duplicate reuse destination")
        patterns = lambda span: [all_bars[i]["pattern_id"] for i in range(span[0], span[1] + 1)]
        if None in patterns(source) or patterns(source) != patterns(target):
            raise ValueError("Explicit reuse requires the same known Pattern ID sequence")
        groups.update((source, target)); reuse[target] = source
    ordered = sorted(groups)
    if any(a[1] >= b[0] for a, b in zip(ordered, ordered[1:])):
        raise ValueError("Explicit blocks overlap")
    if any(source in reuse for source in reuse.values()):
        raise ValueError("Reuse chains are unsupported; point directly to the original block")
    conditions = {"section": section, "export_bpm": float(export_bpm), "time_signature": list(time_signature)}
    starts = {span[0]: span for span in groups}
    beats_per_bar = time_signature[0] * 4 / time_signature[1]
    blocks, block_ids, cursor = [], {}, 0.0
    index = included[0]
    while index <= included[-1]:
        span = starts.get(index, (index, index))
        source = reuse.get(span, span)
        bars = [all_bars[i] for i in range(span[0], span[1] + 1)]
        key = _hash({"map_id": data["map_id"], "source_bar_range": list(source), "conditions": conditions})
        identity = f"block_{len(blocks)+1:03d}"
        block = {"block_id": identity, "reference_bars": list(range(span[0], span[1] + 1)),
                 "reference_time_range": [bars[0]["start_time"], bars[-1]["end_time"]],
                 "export_start_beat": cursor, "export_length_beats": len(bars) * beats_per_bar,
                 "pattern_sequence": [b["pattern_id"] for b in bars], "generation_conditions": deepcopy(conditions),
                 "action": "reuse_pair" if span in reuse else "generate_pair",
                 "reuse_from": block_ids[source] if span in reuse else None, "reuse_key": key,
                 "reference_features": [{"bar_index": b["bar_index"], "activity_score": b["activity_score"],
                                          "onset_density": b["onset_density"], "confidence": deepcopy(b["confidence"]),
                                          "silence_candidates": deepcopy(b["silence_candidates"])} for b in bars],
                 "warnings": ["Reference features are informational; no velocity, fill, rest or note processing is planned."]}
        blocks.append(block);block_ids[span] = identity;cursor += block["export_length_beats"];index = span[1] + 1
    reference = deepcopy(data.get("reference_tempo", {"bpm": data["effective_tempo"]["bpm"],
                              "source": "legacy_unverified", "confidence": None}))
    blockers = []
    if reference["bpm"] is None:
        blockers.append("reference_bpm_unknown")
    if not reference_bpm_confirmed:
        blockers.append("reference_bpm_unconfirmed")
    if not bar_start_confirmed:
        blockers.append("bar_start_unconfirmed")
    if data["bar_grid"]["origin_seconds"] is None:
        blockers.append("bar_start_unknown")
    if data["schema_version"] == "1.0":
        blockers.append("legacy_map_requires_reference_reanalysis")
    warnings = list(data["warnings"]) + [
        "No MIDI is generated. Eligibility is only readiness for a future executor, not source-library or musical validation.",
        "Null confidence does not imply reliability; confirmations acknowledge this exact map's BPM and bar origin.",
        "Generation section is user-specified for all selected bars; Reference section identity is not inferred.",
        "Independent one-bar blocks may cut guitar phrases; a future executor must validate source phrase fit.",
    ]
    if excluded:
        warnings.append("Partial bars excluded; export begins at the first retained complete bar.")
    result = {"schema_version": "1.0", "planner_version": "7.2-plan.1",
              "reference_map": {"path": str(Path(pattern_map_path)), "map_id": data["map_id"],
                                "schema_version": data["schema_version"], "reference_id": data["reference_id"]},
              "reference_tempo": reference, "bar_grid": deepcopy(data["bar_grid"]),
              "export_bpm": float(export_bpm), "time_signature": list(time_signature), "section": section,
              "requested_bar_range": [first, last], "included_reference_bars": included,
              "excluded_partial_bars": excluded, "explicit_repeat_blocks": deepcopy(list(repeat_blocks)),
              "confirmations": {"reference_bpm": reference_bpm_confirmed, "bar_start": bar_start_confirmed},
              "blocks": blocks, "total_export_beats": cursor,
              "reference_warnings": list(data["warnings"]), "warnings": warnings,
              "execution": {"eligible": not blockers, "implemented": False, "blockers": blockers}}
    result["plan_id"] = _hash(result)
    return validate_reference_generation_plan(result)


def validate_reference_generation_plan(plan):
    """Validate finite content identity, block geometry, reuse and execution gates."""
    try:
        if plan["schema_version"] != "1.0" or plan["planner_version"] != "7.2-plan.1":
            raise ValueError("Unsupported plan version")
        if plan["plan_id"] != _hash({k: v for k, v in plan.items() if k != "plan_id"}):
            raise ValueError("Plan content identity mismatch")
        if (isinstance(plan["export_bpm"], bool) or not isinstance(plan["export_bpm"], (int, float))
                or not math.isfinite(plan["export_bpm"]) or plan["export_bpm"] <= 0):
            raise ValueError("Invalid Export BPM")
        reference_bpm = plan["reference_tempo"]["bpm"]
        if reference_bpm is not None and (isinstance(reference_bpm, bool)
                or not isinstance(reference_bpm, (int, float)) or not math.isfinite(reference_bpm) or reference_bpm <= 0):
            raise ValueError("Invalid Reference BPM")
        if any(type(value) is not bool for value in plan["confirmations"].values()):
            raise ValueError("Invalid confirmations")
        first, last = _range(plan["requested_bar_range"], "requested_bar_range")
        if plan["included_reference_bars"] != [i for i in range(first, last + 1) if i not in plan["excluded_partial_bars"]]:
            raise ValueError("Selected bar coverage mismatch")
        cursor, known, flattened = 0.0, {}, []
        for block in plan["blocks"]:
            if block["export_start_beat"] != cursor or block["export_length_beats"] <= 0:
                raise ValueError("Invalid export beat layout")
            if block["generation_conditions"] != {"section": plan["section"], "export_bpm": plan["export_bpm"], "time_signature": plan["time_signature"]}:
                raise ValueError("Inconsistent block generation conditions")
            if block["export_length_beats"] != len(block["reference_bars"]) * plan["time_signature"][0] * 4 / plan["time_signature"][1]:
                raise ValueError("Block length/meter mismatch")
            if block["action"] == "reuse_pair":
                source = known[block["reuse_from"]]
                for key in ("reuse_key", "export_length_beats", "generation_conditions", "pattern_sequence"):
                    if source[key] != block[key]:
                        raise ValueError("Reuse condition mismatch")
            elif block["action"] != "generate_pair" or block["reuse_from"] is not None:
                raise ValueError("Invalid block action")
            if block["block_id"] in known:
                raise ValueError("Duplicate block ID")
            known[block["block_id"]] = block;flattened.extend(block["reference_bars"]);cursor += block["export_length_beats"]
        if cursor != plan["total_export_beats"] or flattened != plan["included_reference_bars"]:
            raise ValueError("Invalid plan coverage")
        if plan["execution"]["implemented"] is not False or plan["execution"]["eligible"] != (not plan["execution"]["blockers"]):
            raise ValueError("Invalid execution status")
        if plan["execution"]["eligible"] and (plan["reference_tempo"]["bpm"] is None or not all(plan["confirmations"].values()) or plan["bar_grid"]["origin_seconds"] is None or plan["reference_map"]["schema_version"] == "1.0"):
            raise ValueError("Unconfirmed/unknown Reference cannot execute")
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Invalid Reference generation plan: {error}") from error
    return plan


def load_reference_generation_plan(path):
    def invalid(value):
        raise ValueError(f"Nonfinite JSON constant: {value}")
    with Path(path).open(encoding="utf-8") as stream:
        return validate_reference_generation_plan(json.load(stream, parse_constant=invalid))


def save_reference_generation_plan(plan, output_path):
    validate_reference_generation_plan(plan)
    destination = Path(output_path)
    if destination.resolve() == Path(plan["reference_map"]["path"]).resolve():
        raise ValueError("A plan must not replace its Reference Pattern Map")
    return save_reference_analysis(plan, destination)
