"""Independent guitar riff reports and guitar-only search filtering."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from midi_generator.modules.guitar.analysis import (
    ANALYSIS_VERSION, RIFF_CHARACTERISTICS, analyze_guitar_midi, load_guitar_config,
)
from midi_generator.storage.catalog import _assert_reference_unlocked, _load_catalog, _write_catalog
from midi_generator.core.instruments import classify_source_path

DEFAULT_REPORT = "data/catalog/guitar_riff_analysis.json"


def analyze_catalog_guitars(*, catalog_path="data/catalog/midi_catalog.json", output_path=DEFAULT_REPORT,
                            project_root=".", config_path=None):
    root = Path(project_root).resolve()
    catalog_path, output_path = Path(catalog_path), Path(output_path)
    if not catalog_path.is_absolute():
        catalog_path = root / catalog_path
    if not output_path.is_absolute():
        output_path = root / output_path
    if output_path.resolve() == catalog_path.resolve() or output_path.suffix.lower() != ".json":
        raise ValueError("Guitar analysis must be a separate .json report, not the catalog or a source MIDI")
    if config_path is None and (root / "config/guitar_analysis.json").is_file():
        config_path = root / "config/guitar_analysis.json"
    elif config_path is not None and not Path(config_path).is_absolute():
        config_path = root / config_path
    config = load_guitar_config(config_path)
    catalog = _load_catalog(catalog_path)
    records = [record for record in catalog["files"] if record.get("present", True) and record.get("instrument") == "guitar"]
    sources = []
    for record in records:
        base = {"source_id": record.get("file_id", record["path"]), "file_path": record["path"], "instrument": "guitar",
                "guitar_role": classify_source_path(record["path"]).get("guitar_role")}
        try:
            path = root / record["path"]
            if not path.is_file() and catalog.get("source_directory"):
                path = root / catalog["source_directory"] / record["path"]
            tags = record.get("tags") or {}
            style = tags.get("style", []) if isinstance(tags, dict) else []
            result = analyze_guitar_midi(path, source_id=base["source_id"], style=style, config=config)
            result.update(base)
            result["source_context"]["expected_midi_channel"] = record.get("expected_midi_channel", 1)
            sources.append(result)
        except Exception as exc:
            sources.append({**base, "schema_version": "1.0", "analysis_version": ANALYSIS_VERSION,
                            "status": "error", "error": {"type": type(exc).__name__, "message": str(exc)}, "warnings": []})
    good = [source for source in sources if source["status"] == "ok"]
    labels = Counter(label for source in good for label in source["riff_characteristics"])
    poly = Counter(source["polyphony"]["classification"] for source in good)
    pitch_bins = Counter()
    for source in good:
        for note, count in source["pitch"]["note_counts"].items():
            pitch_bins[f"{int(note) // 12 * 12:03d}-{min(127, int(note) // 12 * 12 + 11):03d}"] += count
    summary = {
        "guitar_total": len(records), "success_count": len(good), "error_count": len(sources) - len(good),
        "monophonic_count": poly["monophonic"] + poly["mostly_monophonic"], "chord_based_count": poly["chord_based"],
        "sixteenth_count": sum(source["rhythm"]["subdivision"] == "sixteenth" for source in good),
        "high_syncopation_count": sum(source["rhythm"]["syncopation"] == "high" for source in good),
        "power_chord_count": sum(source["chords"]["classification"] == "power_chord" for source in good),
        "polyphony_counts": {key: poly[key] for key in ("monophonic", "mostly_monophonic", "mixed", "chord_based", "unknown")},
        "subdivision_counts": {key: sum(source["rhythm"]["subdivision"] == key for source in good) for key in ("eighth", "sixteenth", "mixed", "unknown")},
        "syncopation_counts": {key: sum(source["rhythm"]["syncopation"] == key for source in good) for key in ("low", "medium", "high", "unknown")},
        "riff_characteristic_counts": {key: labels[key] for key in RIFF_CHARACTERISTICS},
        "pitch_note_event_distribution": dict(sorted(pitch_bins.items())),
        "min_note": min((source["pitch"]["min_note"] for source in good if source["pitch"]["min_note"] is not None), default=None),
        "max_note": max((source["pitch"]["max_note"] for source in good if source["pitch"]["max_note"] is not None), default=None),
        "sources_with_possible_control_notes": sum(bool(source["possible_control_notes"]) for source in good),
    }
    report = {"schema_version": "1.0", "analysis_version": ANALYSIS_VERSION,
              "analyzed_at": datetime.now(timezone.utc).isoformat(), "project_root": str(root),
              "catalog_file": str(catalog_path), "analysis_config": config, "summary": summary, "sources": sources,
              "policy": "Read-only MIDI-feature estimates; no tuning, key, playing technique or keyswitch assignment is inferred."}
    json.dumps(report, allow_nan=False)
    _write_catalog(output_path, report)
    return report


def get_guitar_riff_analysis(source_id, *, analysis_path=DEFAULT_REPORT):
    _assert_reference_unlocked(Path(analysis_path))
    report = json.loads(Path(analysis_path).read_text(encoding="utf-8"))
    matches = [entry for entry in report["sources"] if source_id in {entry["source_id"], entry["file_path"]}]
    if not matches:
        matches = [entry for entry in report["sources"] if Path(entry["file_path"]).name == Path(source_id).name]
    if len(matches) != 1:
        raise ValueError(f"Guitar analysis source ID not found or ambiguous: {source_id}")
    return matches[0]


def filter_guitar_results(results, query, analysis_path):
    """Guitar feature conditions are exact filters; existing drum ranking is untouched."""
    unsupported = {key for key in ("timing_feel", "backbeat", "double_kick") if getattr(query, key) is not None}
    if unsupported:
        raise ValueError("Unsupported guitar search condition(s): " + ", ".join(sorted(unsupported)))
    criteria = {key: getattr(query, key) for key in ("subdivision", "syncopation", "density", "tightness") if getattr(query, key) is not None}
    path = Path(analysis_path)
    if not path.exists():
        if criteria or query.riff_characteristics:
            raise ValueError("Guitar feature report is missing; run catalog analyze-guitars")
        return [result for result in results if "guitar" in result["instrument"]]
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("analysis_version") != ANALYSIS_VERSION:
        raise ValueError("Unsupported guitar report version; run catalog analyze-guitars")
    index = {}
    for entry in report["sources"]:
        index[entry["source_id"]] = entry
        index[entry["file_path"]] = entry
    selected = []
    for result in results:
        if "guitar" not in result["instrument"]:
            continue
        entry = index.get(result["file_id"]) or index.get(result["file_path"])
        status = "missing"
        if entry and entry["status"] == "ok":
            source = Path(report["project_root"]) / result["file_path"]
            try:
                current = hashlib.sha256(source.read_bytes()).hexdigest()
            except OSError:
                current = None
            status = "available" if current == entry["source_sha256"] else "stale"
        elif entry:
            status = "error"
        if status != "available":
            if not criteria and not query.riff_characteristics:
                selected.append({**result, "guitar_analysis_status": status})
            continue
        matched = True
        for key, value in criteria.items():
            actual = entry["rhythm"]["tightness_score" if key == "tightness" else key]
            if actual is None or (key == "tightness" and actual < value) or (key != "tightness" and actual != value):
                matched = False
        if not matched or any(label not in entry["riff_characteristics"] for label in query.riff_characteristics):
            continue
        selected.append({**result, "guitar_analysis_status": status,
                         "guitar_riff": {"pitch": entry["pitch"], "polyphony": entry["polyphony"],
                                         "rhythm": {key: value for key, value in entry["rhythm"].items() if key not in {"per_bar", "onset_positions_beats"}},
                                         "chords": {key: value for key, value in entry["chords"].items() if key != "structures"}},
                         "riff_characteristics": entry["riff_characteristics"], "riff_evidence": entry["riff_evidence"],
                         "guitar_analysis_warnings": entry["warnings"],
                         "guitar_filter_match": {**criteria, "riff_characteristics": list(query.riff_characteristics)}})
    return sorted(selected, key=lambda item: (-item["score"], item["file_path"].casefold()))


def search_guitar_riffs(**kwargs):
    from midi_generator.storage.patterns import search_patterns
    if kwargs.pop("instrument", "guitar") != "guitar":
        raise ValueError("search_guitar_riffs only supports instrument='guitar'")
    return search_patterns(instrument="guitar", **kwargs)
