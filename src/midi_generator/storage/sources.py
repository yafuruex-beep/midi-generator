"""Instrument-organized MIDI source discovery, migration, and channel calibration."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Mapping

import mido

from midi_generator.core.instruments import (
    GENRES,
    INSTRUMENTS,
    classify_source_path,
    load_instrument_config,
    source_channel_metadata,
)
from midi_generator.core.midi_io import convert_midi_channel
from midi_generator.storage.catalog import _load_catalog, _write_catalog, analyze_midi

MIDI_SUFFIXES = {".mid", ".midi"}


def _rooted(path: str | Path, root: Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else root / value


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _source_files(source_dir: str | Path, root: Path) -> list[Path]:
    directory = _rooted(source_dir, root)
    if not directory.is_dir():
        raise FileNotFoundError(f"MIDI source directory not found: {directory}")
    return sorted((path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in MIDI_SUFFIXES),
                  key=lambda path: path.as_posix().casefold())


def _catalog_record_map(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item.get("path"): item for item in catalog.get("files", []) if isinstance(item, dict) and item.get("path")}


def _metadata_for(path: Path, root: Path, config: dict[str, Any], tags: dict[str, Any] | None = None) -> dict[str, Any]:
    midi = mido.MidiFile(path)
    return source_channel_metadata(
        midi, _relative(path, root), tags=tags,
        instrument_channels=config["standard_channels"],
    )


def list_sources(
    *, source_dir: str | Path = "data/sources", project_root: str | Path = ".",
    config_path: str | Path = "config/instrument_channels.json",
) -> list[dict[str, Any]]:
    """List MIDI assets and derived directory/channel metadata without writing."""
    root = Path(project_root).resolve()
    config_file = _rooted(config_path, root)
    config = load_instrument_config(config_file)
    catalog = _load_catalog(_rooted("data/catalog/midi_catalog.json", root))
    records = _catalog_record_map(catalog)
    result = []
    for path in _source_files(source_dir, root):
        relative = _relative(path, root)
        record = records.get(relative, {})
        try:
            item = _metadata_for(path, root, config, record.get("tags"))
            item.update({"file_id": record.get("file_id", record.get("path", relative)), "file_path": relative, "error": None})
        except Exception as exc:
            classified = classify_source_path(relative)
            item = {"file_id": record.get("file_id", relative), "file_path": relative,
                    **classified,
                    "expected_midi_channel": config["standard_channels"].get(classified.get("instrument")),
                    "actual_midi_channels": [],
                    "channel_status": "unknown", "tracks": [], "warnings": [],
                    "error": {"type": type(exc).__name__, "message": str(exc)}}
        result.append(item)
    return result


def inspect_sources(
    instrument: str | None = None, *, source_dir: str | Path = "data/sources",
    project_root: str | Path = ".", config_path: str | Path = "config/instrument_channels.json",
) -> dict[str, Any]:
    """Inspect source MIDI files, optionally filtering by directory instrument."""
    if instrument is not None and instrument not in INSTRUMENTS - {"other"}:
        raise ValueError(f"Unsupported instrument: {instrument}")
    sources = list_sources(source_dir=source_dir, project_root=project_root, config_path=config_path)
    selected = [item for item in sources if instrument is None or item.get("instrument") == instrument]
    return {"instrument": instrument, "source_count": len(selected), "sources": selected}


def validate_source_channels(
    *, source_dir: str | Path = "data/sources", project_root: str | Path = ".",
    config_path: str | Path = "config/instrument_channels.json",
) -> dict[str, Any]:
    """Report channel conformity; mixed and unknown sources are never auto-fixed."""
    sources = list_sources(source_dir=source_dir, project_root=project_root, config_path=config_path)
    counts = {status: sum(item["channel_status"] == status for item in sources)
              for status in ("valid", "needs_correction", "mixed", "unknown")}
    by_instrument: dict[str, dict[str, int]] = {}
    for source in sources:
        instrument = source.get("instrument") or "unclassified"
        summary = by_instrument.setdefault(instrument, {
            "total_files": 0, "normal_files": 0, "correction_needed_files": 0,
            "multi_channel_files": 0, "read_errors": 0,
        })
        summary["total_files"] += 1
        summary["normal_files"] += source["channel_status"] == "valid"
        summary["correction_needed_files"] += source["channel_status"] == "needs_correction"
        summary["multi_channel_files"] += len(source.get("actual_midi_channels", [])) > 1
        summary["read_errors"] += bool(source.get("error"))
    return {"source_count": len(sources), "status_counts": counts,
            "instrument_summary": by_instrument,
            "read_error_count": sum(bool(item.get("error")) for item in sources),
            "multi_channel_sources": [item for item in sources if len(item.get("actual_midi_channels", [])) > 1],
            "read_errors": [item for item in sources if item.get("error")],
            "correction_candidates": [item for item in sources if item["channel_status"] == "needs_correction"],
            "sources": sources}


def _new_backup_dir(root: Path, base: str | Path, label: str) -> Path:
    backup_root = _rooted(base, root)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = backup_root / f"{label}-{timestamp}"
    destination.mkdir(parents=True, exist_ok=False)
    return destination


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def organize_sources(
    *, source_dir: str | Path = "data/sources", catalog_path: str | Path = "data/catalog/midi_catalog.json",
    project_root: str | Path = ".", config_path: str | Path = "config/instrument_channels.json",
    backup_directory: str | Path = "data/backups",
    instrument: str | None = None, genre: str | None = None,
    genre_by_series: Mapping[str, str] | None = None,
    genre_source: str = "manual", genre_method: str | None = None,
    genre_parameters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Back up and move flat MIDI sources into classified folders or unclassified/.

    Existing instrument/style tags are the default migration classification input. An
    explicit ``instrument``/``genre`` override is a user-verified classification for
    currently unclassified files. Heuristic reports are never treated as verified labels.
    Catalog paths are updated while a stable ``file_id`` and all annotations are retained.
    """
    root = Path(project_root).resolve()
    source_root = _rooted(source_dir, root).resolve()
    catalog_file = _rooted(catalog_path, root)
    config = load_instrument_config(_rooted(config_path, root))
    if instrument is not None and instrument not in INSTRUMENTS - {"other"}:
        raise ValueError(f"Unsupported source instrument: {instrument}")
    if genre is not None and genre not in GENRES:
        raise ValueError(f"Unsupported source genre: {genre}")
    if instrument in {"bass", "synth"} and genre == "hardcore":
        raise ValueError(f"Genre directory {genre!r} is not defined for {instrument}")
    if genre_by_series and instrument is None:
        raise ValueError("instrument is required when genre_by_series is provided")
    if genre_source not in {"manual", "automatic", "user_confirmed"}:
        raise ValueError("genre_source must be manual, automatic, or user_confirmed")
    normalized_series_genres = {str(key).upper(): value for key, value in (genre_by_series or {}).items()}
    if any(value not in GENRES for value in normalized_series_genres.values()):
        raise ValueError("genre_by_series values must be metalcore, hardcore, or other")
    if instrument in {"bass", "synth"} and "hardcore" in normalized_series_genres.values():
        raise ValueError(f"Genre directory 'hardcore' is not defined for {instrument}")
    catalog = _load_catalog(catalog_file)
    records = _catalog_record_map(catalog)
    files = _source_files(source_root, root)
    jobs: list[tuple[Path, Path]] = []
    for source in files:
        relative = source.relative_to(source_root).as_posix()
        parts = Path(relative).parts
        is_unclassified = bool(parts and parts[0] == "unclassified")
        series_match = re.search(r"-(S\d+)-", source.name, flags=re.IGNORECASE)
        series = series_match.group(1).upper() if series_match else None
        series_genre = normalized_series_genres.get(series) if series else None
        already_in_requested_instrument = bool(parts and parts[0] == instrument)
        if len(parts) >= 2 and parts[0] in INSTRUMENTS | {"unclassified"}:
            if is_unclassified and instrument:
                pass
            elif series_genre and already_in_requested_instrument:
                pass
            else:
                continue
        record = records.get(_relative(source, root), {})
        tags = record.get("tags") or {}
        instruments = tags.get("instrument", []) if isinstance(tags, dict) else []
        styles = tags.get("style", []) if isinstance(tags, dict) else []
        target_instrument = (
            instrument if series_genre and already_in_requested_instrument
            else instrument if is_unclassified and instrument
            else next((value for value in instruments if value in INSTRUMENTS - {"other"}), None)
        )
        if target_instrument:
            target_genre = series_genre or genre or next((value for value in styles if value in GENRES - {"other"}), "other")
            if target_instrument in {"bass", "synth"} and target_genre == "hardcore":
                target_genre = "other"
            target = source_root / target_instrument / target_genre / source.name
        else:
            target = source_root / "unclassified" / source.name
        if target.resolve() == source.resolve():
            continue
        if target.exists():
            raise FileExistsError(f"Migration destination already exists: {target}")
        jobs.append((source, target))
    if not jobs:
        return {"moved_count": 0, "backup_directory": None, "catalog_path": str(catalog_file), "sources": []}

    backup = _new_backup_dir(root, backup_directory, "source-migration")
    for path in files:
        relative = path.relative_to(root).as_posix()
        backup_path = backup / relative
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_path)
        if _sha256(path) != _sha256(backup_path):
            raise OSError(f"Source backup verification failed: {path}")
    catalog_backup = backup / catalog_file.relative_to(root)
    catalog_backup.parent.mkdir(parents=True, exist_ok=True)
    if catalog_file.exists():
        shutil.copy2(catalog_file, catalog_backup)

    moved: list[tuple[Path, Path]] = []
    try:
        for source, target in jobs:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if _sha256(source) != _sha256(target):
                target.unlink(missing_ok=True)
                raise OSError(f"Migration content verification failed: {source}")
            source.unlink()
            moved.append((source, target))

        for old_path, new_path in moved:
            old_relative = _relative(old_path, root)
            new_relative = _relative(new_path, root)
            record = records.get(old_relative)
            if record is None:
                continue
            record.setdefault("file_id", old_relative)
            record["path"] = new_relative
            if isinstance(record.get("analysis"), dict):
                record["analysis"]["path"] = new_relative
            destination_classification = classify_source_path(new_relative)
            midi = mido.MidiFile(new_path)
            record.update(source_channel_metadata(
                midi, new_relative, tags=record.get("tags"),
                instrument_channels=config["standard_channels"],
            ))
            metadata = dict(record.get("classification_metadata") or {})
            if instrument is not None:
                metadata["instrument"] = {"value": destination_classification["instrument"], "source": "user_confirmed"}
            actual_genre = destination_classification["genre_directory"]
            destination_series_match = re.search(r"-(S\d+)-", new_path.name, flags=re.IGNORECASE)
            destination_series = destination_series_match.group(1).upper() if destination_series_match else None
            if destination_series in normalized_series_genres or genre is not None:
                genre_detail: dict[str, Any] = {"value": actual_genre, "source": genre_source}
                if genre_method:
                    genre_detail["method"] = genre_method
                if genre_parameters:
                    genre_detail["parameters"] = dict(genre_parameters)
                if destination_series:
                    genre_detail["series"] = destination_series
                tempo_values = [
                    event.get("bpm") for event in (record.get("analysis") or {}).get("tempo_events", [])
                    if event.get("bpm") is not None
                ]
                if tempo_values:
                    genre_detail["tempo_bpm"] = tempo_values
                metadata["genre"] = genre_detail
            if metadata:
                record["classification_metadata"] = metadata
        if catalog_file.exists():
            _write_catalog(catalog_file, catalog)
    except Exception:
        for old_path, new_path in reversed(moved):
            old_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(new_path, old_path)
            new_path.unlink(missing_ok=True)
        if catalog_backup.exists():
            shutil.copy2(catalog_backup, catalog_file)
        raise

    # Ensure the requested taxonomy exists even if some branches are not yet populated.
    for relative in ("drums/metalcore", "drums/hardcore", "drums/other",
                     "guitar/metalcore", "guitar/hardcore", "guitar/other",
                     "bass/metalcore", "bass/other", "synth/metalcore", "synth/other", "unclassified"):
        folder = source_root / relative
        folder.mkdir(parents=True, exist_ok=True)
        marker = folder / ".gitkeep"
        if not any(folder.iterdir()) and not marker.exists():
            marker.touch()
    return {
        "moved_count": len(moved), "backup_directory": str(backup),
        "catalog_path": str(catalog_file),
        "sources": [{"from": _relative(old, root), "to": _relative(new, root), "sha256": _sha256(new)} for old, new in moved],
    }


def correct_source_channels(
    *, apply: bool = False, in_place: bool | None = None,
    source_dir: str | Path = "data/sources", project_root: str | Path = ".",
    catalog_path: str | Path = "data/catalog/midi_catalog.json",
    config_path: str | Path = "config/instrument_channels.json",
    backup_directory: str | Path | None = None,
    output_directory: str | Path = "output/corrected_sources",
) -> dict[str, Any]:
    """Preview channel corrections, or apply them with verified backups.

    Applying in place always makes a byte-for-byte backup first. Mixed/unknown sources
    are skipped. When ``in_place`` is false, corrected copies are written separately.
    """
    root = Path(project_root).resolve()
    config = load_instrument_config(_rooted(config_path, root))
    correction = config.get("correction", {})
    if in_place is None:
        in_place = bool(correction.get("in_place", True))
    backup_directory = backup_directory or correction.get("backup_directory", "data/backups")
    validation = validate_source_channels(source_dir=source_dir, project_root=root, config_path=_rooted(config_path, root))
    candidates = validation["correction_candidates"]
    previews = [{
        "file_id": item["file_id"],
        "file_path": item["file_path"],
        "instrument": item.get("instrument"),
        "before_channels": item["actual_midi_channels"],
        "after_channels": [item["expected_midi_channel"]],
        "expected_midi_channel": item["expected_midi_channel"],
    } for item in candidates]
    if not apply:
        return {
            "dry_run": True, "applied_count": 0, "candidate_count": len(candidates),
            "candidates": previews,
            "skipped_mixed": validation["status_counts"]["mixed"],
            "skipped_unknown": validation["status_counts"]["unknown"],
            "read_error_count": validation["read_error_count"],
            "backup_directory": None,
        }
    backup = _new_backup_dir(root, backup_directory, "channel-correction") if in_place and candidates else None
    source_root = _rooted(source_dir, root)
    updated: list[tuple[Path, Path | None]] = []
    results = []
    errors = []
    try:
        for item in candidates:
            source = root / item["file_path"]
            relative_source = source.relative_to(source_root).as_posix()
            backup_file = None
            temp_path: Path | None = None
            try:
                if in_place:
                    assert backup is not None
                    backup_file = backup / item["file_path"]
                    backup_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, backup_file)
                    if _sha256(source) != _sha256(backup_file):
                        raise OSError(f"Channel correction backup verification failed: {source}")
                    fd, temp_name = tempfile.mkstemp(prefix=f".{source.name}.", suffix=source.suffix, dir=source.parent)
                    os.close(fd)
                    temp_path = Path(temp_name)
                    temp_path.unlink()
                    conversion = convert_midi_channel(source, temp_path, target_channel=item["expected_midi_channel"])
                    os.replace(temp_path, source)
                    updated.append((source, backup_file))
                    output_path = source
                else:
                    output_path = _rooted(output_directory, root) / relative_source
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    conversion = convert_midi_channel(source, output_path, target_channel=item["expected_midi_channel"])
                results.append({
                    "file_id": item["file_id"], "output_file": _relative(output_path, root),
                    "backup_file": _relative(backup_file, root) if backup_file else None,
                    "before_channels": item["actual_midi_channels"],
                    "after_channels": [item["expected_midi_channel"]],
                    "target_channel": item["expected_midi_channel"],
                    "changed_channel_messages": conversion["changed_channel_messages"],
                })
            except Exception as exc:
                errors.append({"file_id": item["file_id"], "file_path": item["file_path"],
                               "error": {"type": type(exc).__name__, "message": str(exc)}})
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)
        if in_place and results:
            catalog_file = _rooted(catalog_path, root)
            catalog = _load_catalog(catalog_file)
            records = _catalog_record_map(catalog)
            for result in results:
                record = records.get(result["output_file"])
                if record is None:
                    continue
                path = root / result["output_file"]
                record["analysis"] = analyze_midi(path, relative_path=result["output_file"])
                record.update(_metadata_for(path, root, config, record.get("tags")))
            if catalog_file.exists():
                _write_catalog(catalog_file, catalog)
    except Exception:
        for source, backup_file in reversed(updated):
            shutil.copy2(backup_file, source)
        raise
    return {
        "dry_run": False, "in_place": in_place, "candidate_count": len(candidates),
        "applied_count": len(results), "error_count": len(errors), "errors": errors,
        "backup_directory": str(backup) if backup else None, "results": results,
        "skipped_mixed": validation["status_counts"]["mixed"],
        "skipped_unknown": validation["status_counts"]["unknown"],
    }
