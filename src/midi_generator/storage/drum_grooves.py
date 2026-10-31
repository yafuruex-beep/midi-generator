"""Independent groove reports and soft preference ranking; never edits the catalog."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from midi_generator.modules.drums.groove import (
    ANALYSIS_VERSION, GROOVE_VALUES, analyze_drum_groove, load_groove_config,
)
from midi_generator.storage.catalog import _load_catalog, _write_catalog

DEFAULT_GROOVE_REPORT = "data/catalog/drum_groove_analysis.json"


def analyze_catalog_grooves(
    *, catalog_path: str | Path = "data/catalog/midi_catalog.json",
    output_path: str | Path = DEFAULT_GROOVE_REPORT,
    project_root: str | Path = ".", config_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    catalog_file = Path(catalog_path)
    if not catalog_file.is_absolute():
        catalog_file = root / catalog_file
    output = Path(output_path)
    if not output.is_absolute():
        output = root / output
    if output.resolve() == catalog_file.resolve():
        raise ValueError("Groove report must be separate from the MIDI catalog")
    if output.suffix.lower() != ".json":
        raise ValueError("Groove report output must be a .json file")
    if config_path is None and (root / "config/drum_groove.json").is_file():
        config_path = root / "config/drum_groove.json"
    elif config_path is not None and not Path(config_path).is_absolute():
        config_path = root / config_path
    config = load_groove_config(config_path)
    catalog = _load_catalog(catalog_file)
    records = [item for item in catalog["files"] if item.get("present", True) and
               (item.get("instrument") == "drums" or
                (isinstance(item.get("tags"), dict) and "drums" in item["tags"].get("instrument", [])))]
    results = []
    for record in records:
        base = {"source_id": record.get("file_id", record["path"]), "file_path": record["path"], "instrument": "drums"}
        try:
            source = root / record["path"]
            if not source.is_file() and catalog.get("source_directory"):
                source = root / catalog["source_directory"] / record["path"]
            result = analyze_drum_groove(source, config=config)
            results.append({**base, **result, "error": None})
        except Exception as exc:
            results.append({**base, "analysis_version": ANALYSIS_VERSION, "groove": None,
                            "error": {"type": type(exc).__name__, "message": str(exc)}})
    successful = [item for item in results if item["error"] is None]
    counts = {}
    for key, values in GROOVE_VALUES.items():
        observed = Counter(item["groove"][key] for item in successful)
        counts[key] = {value: observed[value] for value in values}
    report = {
        "analysis_version": ANALYSIS_VERSION, "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(root), "catalog_file": str(catalog_file), "analysis_config": config,
        "target_count": len(records), "success_count": len(successful), "error_count": len(results) - len(successful),
        "classification_counts": counts, "results": results,
        "policy": "Heuristic source-selection features only; no note edits or automatic section tags.",
    }
    _write_catalog(output, report)
    return report


def get_drum_groove_analysis(source_id: str, *, analysis_path: str | Path = DEFAULT_GROOVE_REPORT) -> dict[str, Any]:
    report = json.loads(Path(analysis_path).read_text(encoding="utf-8"))
    matches = [item for item in report["results"] if source_id in {item["source_id"], item["file_path"]}]
    if not matches:
        matches = [item for item in report["results"] if Path(item["file_path"]).name == Path(source_id).name]
    if len(matches) != 1:
        raise ValueError(f"Groove analysis source ID not found or ambiguous: {source_id}")
    return matches[0]


def load_groove_index(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if not path.exists():
        return {}, {}
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("analysis_version") != ANALYSIS_VERSION or not isinstance(report.get("results"), list):
        raise ValueError("Unsupported groove report; run catalog analyze-grooves again")
    index = {}
    for item in report["results"]:
        index[item["source_id"]] = item
        index[item["file_path"]] = item
    return index, report


def apply_groove_preferences(
    result: dict[str, Any], preferences: Mapping[str, Any], index: Mapping[str, Any],
    report: Mapping[str, Any], weights: Mapping[str, float],
) -> dict[str, Any]:
    entry = index.get(result.get("file_id")) or index.get(result["file_path"])
    status = "missing"
    groove = None
    if entry and not entry.get("error"):
        # Stable IDs survive moves; current content must still match the analysis.
        source = Path(str(report.get("project_root", "."))) / result["file_path"]
        try:
            current_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        except OSError:
            current_hash = None
        if current_hash is not None and current_hash == entry.get("source_sha256"):
            groove, status = entry["groove"], "available"
        else:
            status = "stale"
    elif entry:
        status = "error"
    matches, differences, reasons = {}, [], []
    for key, wanted in preferences.items():
        actual = (groove or {}).get("tightness_score" if key == "tightness" else key)
        score = 0.0
        if actual is not None and actual != "unknown":
            if key == "tightness":
                score = min(1.0, actual / wanted) if wanted else 1.0
            elif actual == wanted:
                score = 1.0
            elif key in {"syncopation", "density", "double_kick"}:
                order = list(GROOVE_VALUES[key][:-1])
                if wanted in order and actual in order:
                    score = 1 - abs(order.index(actual) - order.index(wanted)) / (len(order) - 1)
            elif key == "subdivision":
                score = 0.5 if "mixed" in (actual, wanted) else 0.25
        elif actual == wanted == "unknown":
            score = 1.0
        matches[key] = score
        met = score == 1.0
        reasons.append(f"{key}: requested {wanted}, analyzed {actual}, preference fit {score:.3f}")
        if not met:
            differences.append({"field": key, "requested": wanted, "actual": actual})
    breakdown = result["score_breakdown"]
    raw = {name: {"weight": item["weight"], "match": item["match"]} for name, item in breakdown.items()}
    for key, match in matches.items():
        raw[f"groove_{key}"] = {"weight": weights[f"groove_{key}"], "match": match}
    total = sum(item["weight"] for item in raw.values())
    result["score_breakdown"] = {key: {**item, "contribution": round(item["weight"] * item["match"] / total * 100, 6) if total else 0.0} for key, item in raw.items()}
    result["score"] = round(sum(item["contribution"] for item in result["score_breakdown"].values()), 6)
    result["groove"] = groove
    result["groove_confidence"] = entry.get("confidence") if groove and entry else None
    result["groove_warnings"] = entry.get("warnings", []) if entry else []
    result["groove_match_score"] = round(sum(matches.values()) / len(matches) * 100, 6)
    result["groove_selection"] = {"analysis_status": status, "requested": dict(preferences),
                                 "matches": matches, "differences": differences, "reasons": reasons}
    return result
