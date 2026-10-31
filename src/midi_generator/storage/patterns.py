"""Manual MIDI tagging and deterministic catalog search/ranking."""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from midi_generator.storage.catalog import _load_catalog, _write_catalog
from midi_generator.core.instruments import classify_source_path
from midi_generator.modules.drums.groove import validate_groove_preferences
from midi_generator.storage.drum_grooves import apply_groove_preferences, load_groove_index


DEFAULT_CATALOG = Path("data/catalog/midi_catalog.json")
INSTRUMENTS = {"drums", "guitar", "bass", "synth", "other"}
SECTIONS = {
    "intro", "verse", "pre_chorus", "chorus", "breakdown", "bridge", "outro",
    "fill", "transition", "unknown",
}
STYLES = {
    "metalcore", "hardcore", "post_hardcore", "deathcore", "alternative_metal",
    "electronic", "other",
}

# Weights are caller-overridable, keeping ranking policy separate from analysis.
DEFAULT_SCORE_WEIGHTS = {
    "instrument": 15.0,
    "section": 15.0,
    "style": 10.0,
    "tags": 20.0,
    "bpm": 20.0,
    "time_signature": 5.0,
    "bars": 5.0,
    "notes": 5.0,
    "pitch_range": 5.0,
    "groove_subdivision": 15.0,
    "groove_timing_feel": 20.0,
    "groove_syncopation": 15.0,
    "groove_backbeat": 10.0,
    "groove_double_kick": 10.0,
    "groove_density": 10.0,
    "groove_tightness": 15.0,
}


def _values(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return []
    raw = [value] if isinstance(value, str) else list(value)
    values: list[str] = []
    for item in raw:
        values.extend(part.strip().lower() for part in str(item).split(",") if part.strip())
    return list(dict.fromkeys(values))


def _empty_tags() -> dict[str, list[str]]:
    return {"instrument": [], "section": [], "style": [], "custom": []}


def _tag_values(record: Mapping[str, Any]) -> dict[str, list[str]]:
    raw = record.get("tags") or {}
    tags = _empty_tags()
    if isinstance(raw, dict):
        for category in tags:
            tags[category] = _values(raw.get(category))
        # Legacy/custom tags may have been stored under a field named `tags`.
        tags["custom"] = list(dict.fromkeys(tags["custom"] + _values(raw.get("tags"))))
    elif isinstance(raw, list):
        tags["custom"] = _values(raw)
    return tags


def _validate_tags(tags: Mapping[str, list[str]]) -> None:
    for category, allowed in (("instrument", INSTRUMENTS), ("section", SECTIONS), ("style", STYLES)):
        invalid = sorted(set(tags[category]) - allowed)
        if invalid:
            raise ValueError(f"Unsupported {category} tag(s): {', '.join(invalid)}")


def _resolve_record(catalog: dict[str, Any], file_id: str) -> dict[str, Any]:
    identifier = file_id.strip().replace("\\", "/")
    records = catalog["files"]
    for record in records:
        if identifier in {record.get("path"), record.get("file_id")}:
            return record
    normalized = identifier.removeprefix("./")
    for record in records:
        if str(record.get("path", "")).removeprefix("./") == normalized or str(record.get("file_id", "")).removeprefix("./") == normalized:
            return record
    basename_matches = [record for record in records if Path(str(record.get("path", ""))).name == Path(identifier).name
                        or Path(str(record.get("file_id", ""))).name == Path(identifier).name]
    if len(basename_matches) == 1:
        return basename_matches[0]
    if len(basename_matches) > 1:
        raise ValueError(f"File ID is ambiguous; use the catalog path: {file_id}")
    raise KeyError(f"MIDI file ID not found in catalog: {file_id}")


def _tag_mutation(
    file_id: str,
    *,
    catalog_path: str | Path,
    instrument: str | Iterable[str] | None,
    section: str | Iterable[str] | None,
    style: str | Iterable[str] | None,
    tags: str | Iterable[str] | None,
    confidence: float | None,
    replace: bool,
) -> dict[str, Any]:
    supplied = (instrument, section, style, tags)
    if not any(value is not None for value in supplied):
        raise ValueError("Specify at least one tag category to change")
    if confidence is not None and not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    path = Path(catalog_path)
    catalog = _load_catalog(path)
    record = _resolve_record(catalog, file_id)
    current = _tag_values(record)
    requested = {
        "instrument": _values(instrument),
        "section": _values(section),
        "style": _values(style),
        "custom": _values(tags),
    }
    if replace:
        for category, values in requested.items():
            if values or {"instrument": instrument, "section": section, "style": style, "custom": tags}[category] is not None:
                current[category] = values
    else:
        for category, values in requested.items():
            current[category] = list(dict.fromkeys(current[category] + values))
    _validate_tags(current)

    previous_metadata = record.get("tag_metadata", [])
    if not isinstance(previous_metadata, list):
        previous_metadata = []
    active = {(category, value) for category, values in current.items() for value in values}
    metadata = [
        item for item in previous_metadata
        if isinstance(item, dict) and (item.get("category"), item.get("value")) in active
    ]
    known = {(item.get("category"), item.get("value")) for item in metadata}
    source = "manual"
    for category, values in requested.items():
        for value in values:
            key = (category, value)
            if key in active and key not in known:
                detail: dict[str, Any] = {"category": category, "value": value, "source": source}
                if confidence is not None:
                    detail["confidence"] = confidence
                metadata.append(detail)
                known.add(key)
            elif key in known:
                for detail in metadata:
                    if (detail.get("category"), detail.get("value")) == key and detail.get("source") != "manual":
                        detail["source"] = "manual"
                        if confidence is not None:
                            detail["confidence"] = confidence
    record["tags"] = current
    record["tag_metadata"] = metadata
    _write_catalog(path, catalog)
    return {"file_id": record.get("file_id", record["path"]), "file_path": record["path"], "tags": current, "tag_metadata": metadata}


def add_tags(
    file_id: str,
    *,
    instrument: str | Iterable[str] | None = None,
    section: str | Iterable[str] | None = None,
    style: str | Iterable[str] | None = None,
    tags: str | Iterable[str] | None = None,
    confidence: float | None = None,
    catalog_path: str | Path = DEFAULT_CATALOG,
) -> dict[str, Any]:
    """Add tags without duplicating existing values. Tag source is recorded as manual."""
    return _tag_mutation(file_id, catalog_path=catalog_path, instrument=instrument, section=section,
                         style=style, tags=tags, confidence=confidence, replace=False)


def update_tags(
    file_id: str,
    *,
    instrument: str | Iterable[str] | None = None,
    section: str | Iterable[str] | None = None,
    style: str | Iterable[str] | None = None,
    tags: str | Iterable[str] | None = None,
    confidence: float | None = None,
    catalog_path: str | Path = DEFAULT_CATALOG,
) -> dict[str, Any]:
    """Replace only the supplied tag categories; omitted categories are preserved."""
    return _tag_mutation(file_id, catalog_path=catalog_path, instrument=instrument, section=section,
                         style=style, tags=tags, confidence=confidence, replace=True)


def remove_tags(
    file_id: str,
    *,
    instrument: str | Iterable[str] | None = None,
    section: str | Iterable[str] | None = None,
    style: str | Iterable[str] | None = None,
    tags: str | Iterable[str] | None = None,
    catalog_path: str | Path = DEFAULT_CATALOG,
) -> dict[str, Any]:
    if not any(value is not None for value in (instrument, section, style, tags)):
        raise ValueError("Specify at least one tag category to remove")
    path = Path(catalog_path)
    catalog = _load_catalog(path)
    record = _resolve_record(catalog, file_id)
    current = _tag_values(record)
    requested = {
        "instrument": _values(instrument), "section": _values(section),
        "style": _values(style), "custom": _values(tags),
    }
    for category, values in requested.items():
        if values:
            current[category] = [value for value in current[category] if value not in values]
    active = {(category, value) for category, values in current.items() for value in values}
    record["tags"] = current
    previous_metadata = record.get("tag_metadata", [])
    if not isinstance(previous_metadata, list):
        previous_metadata = []
    record["tag_metadata"] = [
        item for item in previous_metadata
        if isinstance(item, dict) and (item.get("category"), item.get("value")) in active
    ]
    _write_catalog(path, catalog)
    return {"file_id": record.get("file_id", record["path"]), "file_path": record["path"], "tags": current, "tag_metadata": record["tag_metadata"]}


def get_tags(file_id: str, *, catalog_path: str | Path = DEFAULT_CATALOG) -> dict[str, Any]:
    catalog = _load_catalog(Path(catalog_path))
    record = _resolve_record(catalog, file_id)
    return {"file_id": record.get("file_id", record["path"]), "file_path": record["path"], "tags": _tag_values(record), "tag_metadata": record.get("tag_metadata", [])}


@dataclass(frozen=True)
class PatternSearchQuery:
    instrument: str | None = None
    section: str | None = None
    style: str | None = None
    tags: tuple[str, ...] = ()
    required_tags: tuple[str, ...] = ()
    bpm_target: float | None = None
    bpm_min: float | None = None
    bpm_max: float | None = None
    time_signature: str | None = None
    bars_min: float | None = None
    bars_max: float | None = None
    bars_target: float | None = None
    notes_min: int | None = None
    notes_max: int | None = None
    pitch_min: int | None = None
    pitch_max: int | None = None
    subdivision: str | None = None
    timing_feel: str | None = None
    syncopation: str | None = None
    backbeat: str | None = None
    double_kick: str | None = None
    density: str | None = None
    tightness: float | None = None
    riff_characteristics: tuple[str, ...] = ()
    guitar_role: str | None = None

    def __post_init__(self) -> None:
        if self.guitar_role is not None:
            if not isinstance(self.guitar_role, str) or self.guitar_role.lower() not in {"backing", "lead"}:
                raise ValueError("guitar_role must be backing or lead")
            if self.instrument is not None and self.instrument.lower() != "guitar":
                raise ValueError("guitar_role filtering requires instrument='guitar'")
            object.__setattr__(self, "guitar_role", self.guitar_role.lower())
            object.__setattr__(self, "instrument", "guitar")
        validate_groove_preferences(self.groove_preferences())
        object.__setattr__(self, "riff_characteristics", tuple(_values(self.riff_characteristics)))
        if self.riff_characteristics:
            from midi_generator.modules.guitar.analysis import RIFF_CHARACTERISTICS
            if set(self.riff_characteristics) - set(RIFF_CHARACTERISTICS):
                raise ValueError("Unsupported riff characteristic")
            if self.instrument is not None and self.instrument.lower() != "guitar":
                raise ValueError("Riff-characteristic filtering requires instrument='guitar'")
        for name, allowed in (("instrument", INSTRUMENTS), ("section", SECTIONS), ("style", STYLES)):
            value = getattr(self, name)
            if value is not None and value.lower() not in allowed:
                raise ValueError(f"Unsupported {name}: {value}")
            if value is not None:
                object.__setattr__(self, name, value.lower())
        object.__setattr__(self, "tags", tuple(_values(self.tags)))
        object.__setattr__(self, "required_tags", tuple(_values(self.required_tags)))
        if self.bpm_min is not None and self.bpm_max is not None and self.bpm_min > self.bpm_max:
            raise ValueError("bpm_min must be less than or equal to bpm_max")
        if self.bpm_target is not None and (not math.isfinite(self.bpm_target) or self.bpm_target <= 0):
            raise ValueError("bpm_target must be greater than zero")
        if self.bars_min is not None and self.bars_max is not None and self.bars_min > self.bars_max:
            raise ValueError("bars_min must be less than or equal to bars_max")
        if self.bars_target is not None and (not math.isfinite(self.bars_target) or self.bars_target <= 0):
            raise ValueError("bars_target must be greater than zero")
        if self.notes_min is not None and self.notes_max is not None and self.notes_min > self.notes_max:
            raise ValueError("notes_min must be less than or equal to notes_max")
        if self.pitch_min is not None and self.pitch_max is not None and self.pitch_min > self.pitch_max:
            raise ValueError("pitch_min must be less than or equal to pitch_max")
        if self.time_signature is not None and not re.fullmatch(r"\d+/\d+", self.time_signature):
            raise ValueError("time_signature must use numerator/denominator notation, for example 4/4")
        if self.time_signature is not None and any(int(part) < 1 for part in self.time_signature.split("/")):
            raise ValueError("time_signature numerator and denominator must be positive")
        if any(value is not None and not 0 <= value <= 127 for value in (self.pitch_min, self.pitch_max)):
            raise ValueError("pitch bounds must be MIDI note numbers from 0 to 127")

    def groove_preferences(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in
                ("subdivision", "timing_feel", "syncopation", "backbeat", "double_kick", "density", "tightness")
                if getattr(self, name) is not None}


def _numeric_match(value: float, minimum: float | None, maximum: float | None) -> bool:
    return (minimum is None or value >= minimum) and (maximum is None or value <= maximum)


def _range_score(value: float, minimum: float | None, maximum: float | None) -> float:
    if minimum is not None and maximum is not None:
        midpoint = (minimum + maximum) / 2
        half_width = (maximum - minimum) / 2
        return 1.0 if half_width == 0 else max(0.0, 1.0 - abs(value - midpoint) / half_width)
    if minimum is not None:
        distance = max(0.0, value - minimum)
        return 1.0 / (1.0 + distance / max(abs(minimum), 1.0))
    if maximum is not None:
        distance = max(0.0, maximum - value)
        return 1.0 / (1.0 + distance / max(abs(maximum), 1.0))
    return 1.0


def _search_record(
    record: Mapping[str, Any], query: PatternSearchQuery, weights: Mapping[str, float],
) -> dict[str, Any] | None:
    analysis = record.get("analysis")
    if not record.get("present", True) or not isinstance(analysis, dict) or record.get("error"):
        return None
    tags = _tag_values(record)
    classified_path = classify_source_path(record.get("path", ""))
    formal_role = classified_path.get("guitar_role")
    if query.guitar_role is not None and formal_role != query.guitar_role:
        return None
    # Directory-derived instrument is the source-of-truth classification, not a manual tag.
    directory_instrument = classified_path.get("instrument") if query.guitar_role is not None else record.get("instrument")
    if directory_instrument in INSTRUMENTS and directory_instrument not in tags["instrument"]:
        tags["instrument"].append(directory_instrument)
    flat_tags = {tag for values in tags.values() for tag in values}
    for category in ("instrument", "section", "style"):
        wanted = getattr(query, category)
        if wanted is not None and wanted.lower() not in tags[category]:
            return None
    if any(value not in flat_tags for value in query.required_tags):
        return None
    if query.tags and not (set(query.tags) & flat_tags):
        return None

    tempo_events = analysis.get("tempo_events") or []
    bpms = [float(event["bpm"]) for event in tempo_events if "bpm" in event]
    if (query.bpm_min is not None or query.bpm_max is not None) and not bpms:
        return None
    if bpms and (query.bpm_min is not None or query.bpm_max is not None):
        if not any(_numeric_match(bpm, query.bpm_min, query.bpm_max) for bpm in bpms):
            return None

    signatures = analysis.get("time_signature_events") or []
    if not signatures:
        signatures = [{"numerator": 4, "denominator": 4}]
    if query.time_signature is not None:
        numerator, denominator = (int(part) for part in query.time_signature.split("/"))
        if not any(event.get("numerator") == numerator and event.get("denominator") == denominator for event in signatures):
            return None

    bars = analysis.get("estimated_bars")
    if (query.bars_min is not None or query.bars_max is not None) and (bars is None or not _numeric_match(float(bars), query.bars_min, query.bars_max)):
        return None
    if query.bars_target is not None and bars is None:
        return None
    note_count = analysis.get("note_on_count")
    if (query.notes_min is not None or query.notes_max is not None) and (note_count is None or not _numeric_match(float(note_count), query.notes_min, query.notes_max)):
        return None

    track_ranges = [track.get("pitch_range") for track in analysis.get("tracks", []) if track.get("pitch_range")]
    pitch_low = min((item["minimum"] for item in track_ranges), default=None)
    pitch_high = max((item["maximum"] for item in track_ranges), default=None)
    if query.pitch_min is not None or query.pitch_max is not None:
        if pitch_low is None or pitch_high is None:
            return None
        requested_low = query.pitch_min if query.pitch_min is not None else pitch_low
        requested_high = query.pitch_max if query.pitch_max is not None else pitch_high
        if pitch_high < requested_low or pitch_low > requested_high:
            return None

    matched_preferences = set(query.tags) & flat_tags
    active: dict[str, float] = {}
    matches: dict[str, float] = {}
    for category in ("instrument", "section", "style"):
        if getattr(query, category) is not None:
            active[category] = weights[category]
            matches[category] = 1.0
    if query.tags or query.required_tags:
        active["tags"] = weights["tags"]
        matches["tags"] = len(matched_preferences | set(query.required_tags)) / max(len(set(query.tags) | set(query.required_tags)), 1)
    if query.bpm_min is not None or query.bpm_max is not None or query.bpm_target is not None:
        active["bpm"] = weights["bpm"]
        if query.bpm_target is not None:
            scoring_bpms = bpms or [120.0]
            matches["bpm"] = max(1.0 / (1.0 + abs(bpm - query.bpm_target) / max(query.bpm_target * 0.1, 1.0)) for bpm in scoring_bpms)
        else:
            matches["bpm"] = max(_range_score(bpm, query.bpm_min, query.bpm_max) for bpm in bpms)
    if query.time_signature is not None:
        active["time_signature"] = weights["time_signature"]
        matches["time_signature"] = 1.0
    if query.bars_min is not None or query.bars_max is not None or query.bars_target is not None:
        active["bars"] = weights["bars"]
        matches["bars"] = (
            1.0 / (1.0 + abs(float(bars) - query.bars_target) / max(query.bars_target, 1.0))
            if query.bars_target is not None else _range_score(float(bars), query.bars_min, query.bars_max)
        )
    if query.notes_min is not None or query.notes_max is not None:
        active["notes"] = weights["notes"]
        matches["notes"] = _range_score(float(note_count), query.notes_min, query.notes_max)
    if query.pitch_min is not None or query.pitch_max is not None:
        active["pitch_range"] = weights["pitch_range"]
        overlap = max(0, min(pitch_high, requested_high) - max(pitch_low, requested_low) + 1)
        requested_width = max(1, requested_high - requested_low + 1)
        matches["pitch_range"] = min(1.0, overlap / requested_width)

    total_weight = sum(active.values())
    breakdown = {
        name: {"weight": weight, "match": round(matches[name], 6), "contribution": round(weight * matches[name] / total_weight * 100, 6)}
        for name, weight in active.items()
    } if total_weight else {}
    score = sum(item["contribution"] for item in breakdown.values()) if breakdown else 0.0
    preferred_tempo = round(sum(bpms) / len(bpms), 3) if bpms else None
    preferred_meter = f"{signatures[0].get('numerator', 4)}/{signatures[0].get('denominator', 4)}"
    return {
        "file_id": record.get("file_id", record.get("path")),
        **({"guitar_role": formal_role} if "guitar" in tags["instrument"] else {}),
        "file_path": record.get("path", analysis.get("path")),
        "instrument": tags["instrument"],
        "section": tags["section"],
        "style": tags["style"],
        "bpm": preferred_tempo,
        "tempo_events": tempo_events,
        "time_signature": preferred_meter,
        "time_signature_events": analysis.get("time_signature_events", []),
        "bars": bars,
        "note_count": note_count,
        "pitch_range": {"minimum": pitch_low, "maximum": pitch_high} if pitch_low is not None else None,
        "tags": tags,
        "score": round(score, 6),
        "score_breakdown": breakdown,
    }


def search_patterns(
    *,
    catalog_path: str | Path = DEFAULT_CATALOG,
    query: PatternSearchQuery | None = None,
    instrument: str | None = None,
    section: str | None = None,
    style: str | None = None,
    tags: str | Iterable[str] | None = None,
    required_tags: str | Iterable[str] | None = None,
    bpm_target: float | None = None,
    bpm_min: float | None = None,
    bpm_max: float | None = None,
    time_signature: str | None = None,
    bars_min: float | None = None,
    bars_max: float | None = None,
    bars_target: float | None = None,
    notes_min: int | None = None,
    notes_max: int | None = None,
    pitch_min: int | None = None,
    pitch_max: int | None = None,
    score_weights: Mapping[str, float] | None = None,
    subdivision: str | None = None,
    timing_feel: str | None = None,
    syncopation: str | None = None,
    backbeat: str | None = None,
    double_kick: str | None = None,
    density: str | None = None,
    tightness: float | None = None,
    groove_analysis_path: str | Path | None = None,
    riff_characteristics: str | Iterable[str] | None = None,
    guitar_analysis_path: str | Path | None = None,
    guitar_role: str | None = None,
) -> list[dict[str, Any]]:
    """Filter and rank catalog records. `tags` are OR preferences; `required_tags` are AND filters."""
    if query is None:
        query = PatternSearchQuery(
            instrument=instrument.lower() if instrument else None,
            section=section.lower() if section else None,
            style=style.lower() if style else None,
            tags=tuple(_values(tags)), required_tags=tuple(_values(required_tags)),
            bpm_target=bpm_target, bpm_min=bpm_min, bpm_max=bpm_max, time_signature=time_signature,
            bars_min=bars_min, bars_max=bars_max, bars_target=bars_target, notes_min=notes_min, notes_max=notes_max,
            pitch_min=pitch_min, pitch_max=pitch_max,
            subdivision=subdivision, timing_feel=timing_feel, syncopation=syncopation,
            backbeat=backbeat, double_kick=double_kick, density=density, tightness=tightness,
            riff_characteristics=tuple(_values(riff_characteristics)),
            guitar_role=guitar_role,
        )
    weights = dict(DEFAULT_SCORE_WEIGHTS)
    if score_weights:
        unknown = set(score_weights) - set(weights)
        if unknown:
            raise ValueError(f"Unknown score weight(s): {', '.join(sorted(unknown))}")
        weights.update({key: float(value) for key, value in score_weights.items()})
    if any(not math.isfinite(weight) or weight < 0 for weight in weights.values()) or not any(weights.values()):
        raise ValueError("Score weights must be non-negative and at least one must be positive")
    catalog = _load_catalog(Path(catalog_path))
    results = [
        result for record in catalog["files"]
        if (result := _search_record(record, query, weights)) is not None
    ]
    preferences = query.groove_preferences()
    if query.instrument == "guitar" or query.riff_characteristics:
        from midi_generator.storage.guitar_riffs import filter_guitar_results
        analysis_path = Path(guitar_analysis_path) if guitar_analysis_path is not None else Path(catalog_path).parent / "guitar_riff_analysis.json"
        return filter_guitar_results(results, query, analysis_path)
    if preferences:
        analysis_path = Path(groove_analysis_path) if groove_analysis_path is not None else Path(catalog_path).parent / "drum_groove_analysis.json"
        index, report = load_groove_index(analysis_path)
        results = [apply_groove_preferences(result, preferences, index, report, weights) for result in results]
    return sorted(results, key=lambda item: (-item["score"], str(item["file_path"]).casefold()))
