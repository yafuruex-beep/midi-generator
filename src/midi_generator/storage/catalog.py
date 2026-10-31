"""Batch MIDI analysis and a small, editable JSON catalog."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

import mido
from midi_generator.core.instruments import classify_source_path, load_instrument_config, source_channel_metadata
from midi_generator.core.midi_io import analyze_midi_events


CATALOG_VERSION = 1
MIDI_SUFFIXES = {".mid", ".midi"}
REFERENCE_LOCK = ".guitar-reference-update.lock"


def _absolute_events(track: mido.MidiTrack) -> list[tuple[int, mido.Message | mido.MetaMessage]]:
    tick = 0
    events = []
    for message in track:
        tick += message.time
        events.append((tick, message))
    return events


def _format_duration(tick_end: int, tempo_events: list[dict[str, Any]], ticks_per_beat: int) -> float:
    """Estimate elapsed seconds using the tempo map (default MIDI tempo is 120 BPM)."""
    changes = sorted(tempo_events, key=lambda event: event["tick"])
    seconds = 0.0
    previous_tick = 0
    tempo = 500_000
    for change in changes:
        tick = min(max(change["tick"], previous_tick), tick_end)
        seconds += mido.tick2second(tick - previous_tick, ticks_per_beat, tempo)
        previous_tick = tick
        tempo = change["microseconds_per_beat"]
    if tick_end > previous_tick:
        seconds += mido.tick2second(tick_end - previous_tick, ticks_per_beat, tempo)
    return seconds


def _estimate_bars(tick_end: int, signatures: list[dict[str, Any]], ticks_per_beat: int) -> float:
    changes = sorted(signatures, key=lambda event: event["tick"])
    tick = 0
    numerator, denominator = 4, 4
    bars = 0.0
    for change in changes:
        change_tick = min(max(change["tick"], tick), tick_end)
        ticks_per_bar = ticks_per_beat * numerator * 4 / denominator
        if ticks_per_bar > 0:
            bars += (change_tick - tick) / ticks_per_bar
        tick = change_tick
        numerator = change["numerator"]
        denominator = change["denominator"]
    ticks_per_bar = ticks_per_beat * numerator * 4 / denominator
    if ticks_per_bar > 0 and tick_end > tick:
        bars += (tick_end - tick) / ticks_per_bar
    return bars


def analyze_midi(path: str | Path, *, relative_path: str | None = None) -> dict[str, Any]:
    """Analyze a MIDI file without modifying it. Raises on unreadable files."""
    source = Path(path)
    midi = mido.MidiFile(source)
    all_events = [_absolute_events(track) for track in midi.tracks]
    track_ends = [events[-1][0] if events else 0 for events in all_events]
    tick_end = max(track_ends, default=0)
    tempo_events: list[dict[str, Any]] = []
    signature_events: list[dict[str, Any]] = []
    track_metadata: list[dict[str, Any]] = []
    note_on_count = 0

    for track_index, (track, events) in enumerate(zip(midi.tracks, all_events)):
        track_name = None
        notes: list[int] = []
        velocities: list[int] = []
        track_note_count = 0
        for tick, message in events:
            if message.type == "track_name" and track_name is None:
                track_name = message.name
            elif message.type == "set_tempo":
                tempo_events.append({
                    "tick": tick,
                    "bpm": round(mido.tempo2bpm(message.tempo), 6),
                    "microseconds_per_beat": message.tempo,
                    "track_index": track_index,
                })
            elif message.type == "time_signature":
                signature_events.append({
                    "tick": tick,
                    "numerator": message.numerator,
                    "denominator": message.denominator,
                    "track_index": track_index,
                })
            elif message.type == "note_on" and message.velocity > 0:
                track_note_count += 1
                note_on_count += 1
                notes.append(message.note)
                velocities.append(message.velocity)

        track_metadata.append({
            "index": track_index,
            "name": track_name,
            "note_count": track_note_count,
            "pitch_range": {"minimum": min(notes), "maximum": max(notes)} if notes else None,
            "velocity": {
                "average": round(sum(velocities) / len(velocities), 4),
                "minimum": min(velocities),
                "maximum": max(velocities),
            } if velocities else None,
        })

    tempo_events.sort(key=lambda event: (event["tick"], event["track_index"]))
    signature_events.sort(key=lambda event: (event["tick"], event["track_index"]))
    warnings: list[str] = []
    if not tempo_events:
        warnings.append("No tempo event found; duration estimate assumes 120 BPM.")
    if not signature_events:
        warnings.append("No time signature event found; bar estimate assumes 4/4.")
    if midi.type == 2:
        warnings.append("MIDI format 2 contains independent sequences; duration and bars use the longest track timeline.")

    if midi.type == 2 and all_events:
        duration = max(
            (_format_duration(end, [event for event in tempo_events if event["track_index"] == index], midi.ticks_per_beat)
             for index, end in enumerate(track_ends)),
            default=0.0,
        )
        estimated_bars = max(
            (_estimate_bars(end, [event for event in signature_events if event["track_index"] == index], midi.ticks_per_beat)
             for index, end in enumerate(track_ends)),
            default=0.0,
        )
    else:
        duration = _format_duration(tick_end, tempo_events, midi.ticks_per_beat)
        estimated_bars = _estimate_bars(tick_end, signature_events, midi.ticks_per_beat)
    relative = relative_path if relative_path is not None else source.name
    return {
        "path": relative,
        "file_format": {"extension": source.suffix.lower(), "smf_type": midi.type},
        "track_count": len(midi.tracks),
        "tracks": track_metadata,
        "tempo_events": tempo_events,
        "time_signature_events": signature_events,
        "ticks_per_beat": midi.ticks_per_beat,
        "estimated_duration_seconds": round(duration, 6),
        "estimated_bars": round(estimated_bars, 6),
        "note_on_count": note_on_count,
        **analyze_midi_events(midi),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "warnings": warnings,
    }


def _load_catalog(path: Path) -> dict[str, Any]:
    _assert_reference_unlocked(path)
    if not path.exists():
        return {"catalog_version": CATALOG_VERSION, "scanned_at": None, "files": []}
    with path.open(encoding="utf-8") as catalog_file:
        catalog = json.load(catalog_file)
    if not isinstance(catalog, dict) or not isinstance(catalog.get("files"), list):
        raise ValueError(f"Invalid MIDI catalog structure: {path}")
    return catalog


def _write_catalog(path: Path, catalog: dict[str, Any]) -> None:
    _assert_reference_unlocked(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary_path = Path(output.name)
            json.dump(catalog, output, ensure_ascii=False, indent=2)
            output.write("\n")
        _assert_reference_unlocked(path)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _assert_reference_unlocked(path: Path) -> None:
    if (path.parent / REFERENCE_LOCK).exists():
        raise ValueError("Guitar reference update is locked/incomplete; finish the update or use source reconcile-guitars --recover BACKUP_DIRECTORY")


def _catalog_path(source: Path, source_root: Path, project_root: Path) -> str:
    resolved = source.resolve()
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        # For external source roots, retain a useful path relative to that root.
        try:
            return source.relative_to(source_root).as_posix()
        except ValueError:
            return resolved.as_posix()


def scan_midi_catalog(
    source_dir: str | Path = "data/sources",
    output_path: str | Path = "data/catalog/midi_catalog.json",
    *,
    project_root: str | Path = ".",
) -> dict[str, Any]:
    """Scan MIDI files and merge results into a catalog, preserving tags/provenance.

    Entries for files no longer present are retained with ``present: false``.
    This prevents accidental loss of metadata and manually entered tags.
    """
    root = Path(project_root).resolve()
    source_root = Path(source_dir)
    if not source_root.is_absolute():
        source_root = root / source_root
    source_root = source_root.resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(f"MIDI source directory not found: {source_root}")
    catalog_path = Path(output_path)
    if not catalog_path.is_absolute():
        catalog_path = root / catalog_path

    catalog = _load_catalog(catalog_path)
    instrument_config = load_instrument_config(root / "config/instrument_channels.json")
    old_records = {item["path"]: item for item in catalog["files"] if isinstance(item, dict) and isinstance(item.get("path"), str)}
    discovered = sorted(
        (path for path in source_root.rglob("*") if path.is_file() and path.suffix.lower() in MIDI_SUFFIXES),
        key=lambda path: path.as_posix().casefold(),
    )
    missing_guitars = [record for record in old_records.values()
                       if record.get("present", True) and classify_source_path(record["path"]).get("instrument") == "guitar"
                       and not (root / record["path"]).is_file()]
    new_guitars = [path for path in discovered if _catalog_path(path, source_root, root) not in old_records
                   and classify_source_path(_catalog_path(path, source_root, root)).get("instrument") == "guitar"]
    if missing_guitars and new_guitars:
        report_path = catalog_path.parent / "guitar_riff_analysis.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {"sources": []}
        report_hashes = {entry.get("source_id"): entry.get("source_sha256") for entry in report.get("sources", [])}
        missing_hashes = {record.get("source_sha256") or report_hashes.get(record.get("file_id", record["path"])) for record in missing_guitars}
        if any(hashlib.sha256(path.read_bytes()).hexdigest() in missing_hashes for path in new_guitars):
            raise ValueError("Possible manually relocated guitar sources: run source reconcile-guitars --dry-run before catalog scan; no IDs were reassigned")
    records = dict(old_records)
    for record in records.values():
        record["present"] = False

    successful = 0
    failed = 0
    for midi_path in discovered:
        path_key = _catalog_path(midi_path, source_root, root)
        previous = records.get(path_key, {})
        try:
            analysis = analyze_midi(midi_path, relative_path=path_key)
            source_metadata = source_channel_metadata(
                mido.MidiFile(midi_path), path_key, tags=previous.get("tags"),
                instrument_channels=instrument_config["standard_channels"],
            )
            records[path_key] = {
                **previous,
                "path": path_key,
                "file_id": previous.get("file_id", path_key),
                "present": True,
                "analysis": analysis,
                "error": None,
                "tags": previous.get("tags", {"instrument": [], "section": [], "style": []}),
                "tag_metadata": previous.get("tag_metadata", []),
                "provenance": previous.get("provenance", {}),
                "classification_metadata": previous.get("classification_metadata", {}),
                **source_metadata,
                **({"source_sha256": hashlib.sha256(midi_path.read_bytes()).hexdigest()} if source_metadata.get("instrument") == "guitar" else {}),
            }
            successful += 1
        except Exception as exc:  # per-file isolation is required for batch scans
            records[path_key] = {
                **previous,
                "path": path_key,
                "file_id": previous.get("file_id", path_key),
                "present": True,
                "analysis": None,
                "error": {"type": type(exc).__name__, "message": str(exc)},
                "tags": previous.get("tags", {"instrument": [], "section": [], "style": []}),
                "tag_metadata": previous.get("tag_metadata", []),
                "provenance": previous.get("provenance", {}),
                "classification_metadata": previous.get("classification_metadata", {}),
                **source_channel_metadata(
                    mido.MidiFile(type=0), path_key, tags=previous.get("tags"),
                    instrument_channels=instrument_config["standard_channels"],
                ),
            }
            failed += 1

    try:
        relative_source_directory = source_root.relative_to(root).as_posix()
    except ValueError:
        relative_source_directory = source_root.as_posix()
    result = {
        **catalog,
        "catalog_version": CATALOG_VERSION,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "source_directory": relative_source_directory,
        "files": sorted(records.values(), key=lambda item: item["path"].casefold()),
    }
    identities = [record.get("file_id", record["path"]) for record in result["files"]]
    if len(identities) != len(set(identities)):
        raise ValueError("Source ID collision detected; catalog was not written. Reconcile guitar references before scanning")
    _write_catalog(catalog_path, result)
    return {"catalog_path": str(catalog_path), "scanned": len(discovered), "succeeded": successful, "failed": failed, "catalog": result}
