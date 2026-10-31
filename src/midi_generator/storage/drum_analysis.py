"""Heuristic drum-candidate analysis for catalog MIDI files (never auto-tags)."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
import statistics
import tempfile
from typing import Any, Mapping

import mido

from midi_generator.core.midi_io import GM_DRUM_MAP
from midi_generator.storage.catalog import _load_catalog, analyze_midi


DRUM_NAME_HINTS = ("drum", "perc", "kick", "snare", "hat", "cymbal", "tom", "kit")
MAJOR_SCALE_PITCH_CLASSES = (
    {0, 2, 4, 5, 7, 9, 11}, {1, 3, 5, 6, 8, 10, 0},
    {2, 4, 6, 7, 9, 11, 1}, {3, 5, 7, 8, 10, 0, 2},
    {4, 6, 8, 9, 11, 1, 3}, {5, 7, 9, 10, 0, 2, 4},
    {6, 8, 10, 11, 1, 3, 5}, {7, 9, 11, 0, 2, 4, 6},
    {8, 10, 0, 1, 3, 5, 7}, {9, 11, 1, 2, 4, 6, 8},
    {10, 0, 2, 3, 5, 7, 9}, {11, 1, 3, 4, 6, 8, 10},
)


def _resolve_file(file_id: str, catalog: Mapping[str, Any], root: Path) -> Path:
    catalog_path = Path(file_id)
    candidates = [catalog_path] if catalog_path.is_absolute() else [root / catalog_path]
    source_directory = catalog.get("source_directory")
    if source_directory and not catalog_path.is_absolute():
        source_root = Path(source_directory)
        if not source_root.is_absolute():
            source_root = root / source_root
        candidates.append(source_root / catalog_path)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"Catalog MIDI file not found: {file_id}")


def _summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "minimum": None, "median": None, "mean": None, "maximum": None}
    return {
        "count": len(values),
        "minimum": round(min(values), 6),
        "median": round(statistics.median(values), 6),
        "mean": round(statistics.fmean(values), 6),
        "maximum": round(max(values), 6),
    }


def _track_analysis(track: mido.MidiTrack, track_index: int, ticks_per_beat: int, file_end_tick: int) -> dict[str, Any]:
    absolute_events: list[tuple[int, mido.Message | mido.MetaMessage]] = []
    tick = 0
    for message in track:
        tick += message.time
        absolute_events.append((tick, message))
    end_tick = max((event_tick for event_tick, _ in absolute_events), default=0)
    track_name = next((message.name for _, message in absolute_events if message.type == "track_name"), None)
    note_counts: Counter[int] = Counter()
    channel_note_counts: Counter[int] = Counter()
    channel_message_counts: Counter[int] = Counter()
    onsets: list[int] = []
    open_notes: dict[tuple[int, int], list[int]] = defaultdict(list)
    intervals: list[tuple[int, int]] = []
    orphan_note_offs = 0

    for event_tick, message in absolute_events:
        if hasattr(message, "channel"):
            channel_message_counts[message.channel] += 1
        if message.type == "note_on" and message.velocity > 0:
            note_counts[message.note] += 1
            channel_note_counts[message.channel] += 1
            onsets.append(event_tick)
            open_notes[(message.channel, message.note)].append(event_tick)
        elif message.type == "note_off" or (message.type == "note_on" and message.velocity == 0):
            note = message.note
            key = (message.channel, note)
            if open_notes[key]:
                start_tick = open_notes[key].pop(0)
                intervals.append((start_tick, max(start_tick, event_tick)))
            else:
                orphan_note_offs += 1

    for pending in open_notes.values():
        intervals.extend((start_tick, max(start_tick, file_end_tick)) for start_tick in pending)

    sweep: list[tuple[int, int]] = []
    for start_tick, end_tick in intervals:
        sweep.append((start_tick, 1))
        sweep.append((end_tick, -1))
    active = maximum_polyphony = 0
    for _, change in sorted(sweep, key=lambda item: (item[0], item[1])):
        active += change
        maximum_polyphony = max(maximum_polyphony, active)

    unique_notes = sorted(note_counts)
    gm_notes = [note for note in unique_notes if note in GM_DRUM_MAP]
    intervals_ticks = [float(right - left) for left, right in zip(sorted(onsets), sorted(onsets)[1:])]
    interval_summary = _summary(intervals_ticks)
    interval_summary["median_beats"] = (
        round(float(interval_summary["median"]) / ticks_per_beat, 6)
        if interval_summary["median"] is not None and ticks_per_beat > 0 else None
    )
    durations_beats = [(end - start) / ticks_per_beat for start, end in intervals if ticks_per_beat > 0]
    pitch_classes = {note % 12 for note in unique_notes}
    scale_fit = (
        max(len(pitch_classes & scale) for scale in MAJOR_SCALE_PITCH_CLASSES) / len(pitch_classes)
        if len(pitch_classes) >= 4 else None
    )
    note_on_count = sum(note_counts.values())
    return {
        "index": track_index,
        "name": track_name,
        "note_on_count": note_on_count,
        "channels": [
            {
                "midi_channel": channel + 1,
                "mido_channel": channel,
                "channel_message_count": channel_message_counts[channel],
                "note_on_count": channel_note_counts[channel],
            }
            for channel in sorted(channel_message_counts)
        ],
        "used_note_numbers": unique_notes,
        "note_counts": [
            {"note_number": note, "count": note_counts[note], "gm_drum_candidate": GM_DRUM_MAP.get(note)}
            for note in unique_notes
        ],
        "minimum_note_number": min(unique_notes) if unique_notes else None,
        "maximum_note_number": max(unique_notes) if unique_notes else None,
        "gm_drum_candidates": [
            {"note_number": note, "name": GM_DRUM_MAP[note], "count": note_counts[note]}
            for note in gm_notes
        ],
        "outside_gm_drum_map": [note for note in unique_notes if note not in GM_DRUM_MAP],
        "gm_note_on_share": round(sum(note_counts[note] for note in gm_notes) / note_on_count, 6) if note_on_count else 0.0,
        "max_simultaneous_notes": maximum_polyphony,
        "onset_interval_ticks": interval_summary,
        "note_duration_beats": _summary(durations_beats),
        "pitch_class_scale_fit": round(scale_fit, 6) if scale_fit is not None else None,
        "unmatched_note_off_count": orphan_note_offs,
    }


def _classify(tracks: list[dict[str, Any]]) -> dict[str, Any]:
    note_total = sum(track["note_on_count"] for track in tracks)
    if note_total == 0:
        return {
            "grade": "D", "confidence": 0.3,
            "reasons": ["No positive-velocity Note On events were available for classification."],
            "features": {"note_on_count": 0},
        }

    note_weights = Counter()
    onset_interval_values: list[float] = []
    max_polyphony = 0
    drum_named = False
    channel10_notes = 0
    all_notes = 0
    scale_fits = []
    durations = []
    for track in tracks:
        note_weights.update({int(item["note_number"]): int(item["count"]) for item in track["note_counts"]})
        all_notes += track["note_on_count"]
        name = (track.get("name") or "").lower()
        drum_named = drum_named or any(hint in name for hint in DRUM_NAME_HINTS)
        channel10_notes += sum(item["note_on_count"] for item in track["channels"] if item["midi_channel"] == 10)
        max_polyphony = max(max_polyphony, track["max_simultaneous_notes"])
        interval_median = track["onset_interval_ticks"]["median_beats"]
        if interval_median is not None and track["note_on_count"] > 1:
            onset_interval_values.extend([float(interval_median)] * (track["note_on_count"] - 1))
        if track["pitch_class_scale_fit"] is not None:
            scale_fits.append(float(track["pitch_class_scale_fit"]))
        duration_median = track["note_duration_beats"]["median"]
        if duration_median is not None:
            durations.append(float(duration_median))

    gm_count = sum(count for note, count in note_weights.items() if note in GM_DRUM_MAP)
    gm_share = gm_count / max(all_notes, 1)
    gm_diversity = len([note for note, count in note_weights.items() if count and note in GM_DRUM_MAP])
    outside_share = 1.0 - gm_share
    interval_median = statistics.median(onset_interval_values) if onset_interval_values else None
    scale_fit = max(scale_fits) if scale_fits else None
    duration_median = statistics.median(durations) if durations else None
    channel10_share = channel10_notes / max(all_notes, 1)
    rhythmic_cue = max_polyphony >= 3 or (interval_median is not None and interval_median <= 0.75)
    features = {
        "note_on_count": all_notes,
        "gm_note_on_share": round(gm_share, 4),
        "distinct_gm_notes": gm_diversity,
        "channel_10_note_share": round(channel10_share, 4),
        "track_name_hint": drum_named,
        "max_simultaneous_notes": max_polyphony,
        "median_onset_interval_beats": round(interval_median, 4) if interval_median is not None else None,
        "median_note_duration_beats": round(duration_median, 4) if duration_median is not None else None,
        "pitch_class_scale_fit": round(scale_fit, 4) if scale_fit is not None else None,
        "outside_gm_note_share": round(outside_share, 4),
    }
    reasons: list[str] = []

    high_named_support = drum_named and (gm_share >= 0.35 or max_polyphony >= 2 or rhythmic_cue)
    high_gm_support = gm_share >= 0.75 and gm_diversity >= 4 and all_notes >= 8 and rhythmic_cue
    channel_plus_other_support = channel10_share >= 0.8 and gm_share >= 0.4 and gm_diversity >= 3 and rhythmic_cue and all_notes >= 8
    if high_named_support or high_gm_support or channel_plus_other_support:
        grade = "A"
        if drum_named:
            reasons.append("Track name contains an explicit drum/percussion hint.")
        if gm_share >= 0.75 and gm_diversity >= 4:
            reasons.append("Most note events use several distinct GM percussion-map notes.")
        if rhythmic_cue:
            reasons.append("Rhythmic evidence includes dense onsets or multiple simultaneous notes.")
        if channel10_share >= 0.8:
            reasons.append("Most note events are on MIDI channel 10; this supports, but does not decide, the classification.")
        cue_count = sum((drum_named, gm_share >= 0.75 and gm_diversity >= 4, rhythmic_cue, channel10_share >= 0.8))
        confidence = round(min(0.96, 0.68 + 0.06 * max(0, cue_count - 2)), 2)
    elif (
        (drum_named and all_notes >= 2)
        or (gm_share >= 0.35 and gm_diversity >= 2 and (rhythmic_cue or all_notes >= 8))
        or (channel10_share >= 0.5 and gm_share >= 0.25 and gm_diversity >= 2 and all_notes >= 6)
    ):
        grade = "B"
        if drum_named:
            reasons.append("Track name suggests drums or percussion, but other evidence is incomplete.")
        if gm_share >= 0.25:
            reasons.append("Some note events map to multiple GM percussion candidates.")
        if rhythmic_cue:
            reasons.append("Onset spacing or polyphony is compatible with percussive material.")
        if channel10_share >= 0.5:
            reasons.append("Channel 10 usage is supporting evidence only.")
        confidence = round(min(0.78, 0.48 + 0.07 * len(reasons)), 2)
    elif (
        all_notes >= 8 and gm_share <= 0.3 and not drum_named
        and scale_fit is not None and scale_fit >= 0.8
        and len(note_weights) >= 6 and max_polyphony <= 2
        and (duration_median is None or duration_median >= 0.35)
    ):
        grade = "C"
        reasons.extend([
            "Most notes fall outside the GM percussion map.",
            "Pitch classes strongly fit a diatonic scale and span several distinct notes.",
            "The track is mostly monophonic/low-polyphony with sustained note lengths.",
            "No explicit drum/percussion track-name hint was found.",
        ])
        confidence = 0.76
    else:
        grade = "D"
        reasons.append("Available note, naming, channel, and rhythmic features do not provide enough combined evidence for A/B/C.")
        confidence = 0.4 if all_notes < 8 else 0.5
    return {"grade": grade, "confidence": confidence, "reasons": reasons, "features": features}


def _analyze_entry(record: Mapping[str, Any], catalog: Mapping[str, Any], root: Path) -> dict[str, Any]:
    file_path = str(record.get("path", ""))
    file_id = str(record.get("file_id", file_path))
    base: dict[str, Any] = {"file_id": file_id, "file_path": file_path}
    if not record.get("present", True):
        return {**base, "classification": {"grade": "D", "confidence": 0.4, "reasons": ["Catalog entry is marked absent."], "features": {}}, "error": None}
    try:
        source = _resolve_file(file_path, catalog, root)
        midi = mido.MidiFile(source)
        metadata = analyze_midi(source, relative_path=file_id)
        tracks = [
            _track_analysis(track, index, midi.ticks_per_beat, sum(message.time for message in track))
            for index, track in enumerate(midi.tracks)
        ]
        classification = _classify(tracks)
        return {
            **base,
            "track_count": len(midi.tracks),
            "tracks": tracks,
            "midi_channels": metadata["channel_analysis"]["channels"],
            "used_note_numbers": metadata["note_number_analysis"]["used_note_numbers"],
            "note_counts": metadata["note_number_analysis"]["note_counts"],
            "minimum_note_number": metadata["note_number_analysis"]["minimum_note_number"],
            "maximum_note_number": metadata["note_number_analysis"]["maximum_note_number"],
            "gm_drum_candidates": metadata["note_number_analysis"]["gm_drum_candidates"],
            "outside_gm_drum_map": metadata["note_number_analysis"]["outside_gm_drum_map"],
            "time_signature_events": metadata["time_signature_events"],
            "tempo_events": metadata["tempo_events"],
            "estimated_bars": metadata["estimated_bars"],
            "estimated_duration_seconds": metadata["estimated_duration_seconds"],
            "classification": classification,
            "error": None,
        }
    except Exception as exc:
        return {
            **base,
            "track_count": None,
            "tracks": [],
            "classification": {
                "grade": "D", "confidence": 0.4,
                "reasons": ["MIDI could not be analyzed; no drum classification was attempted."],
                "features": {},
            },
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }


def analyze_catalog_drums(
    *,
    catalog_path: str | Path = "data/catalog/midi_catalog.json",
    output_path: str | Path = "data/catalog/drum_analysis.json",
    project_root: str | Path = ".",
    file_id: str | None = None,
) -> dict[str, Any]:
    """Analyze catalog MIDI files and save a separate, non-tagging candidate report."""
    root = Path(project_root).resolve()
    catalog_file = Path(catalog_path)
    if not catalog_file.is_absolute():
        catalog_file = root / catalog_file
    catalog = _load_catalog(catalog_file)
    records = catalog["files"]
    if file_id is not None:
        normalized = file_id.replace("\\", "/").removeprefix("./")
        matches = [item for item in records if normalized in {
            str(item.get("path", "")).replace("\\", "/").removeprefix("./"),
            str(item.get("file_id", "")).replace("\\", "/").removeprefix("./"),
        }]
        if not matches:
            matches = [item for item in records if Path(str(item.get("path", ""))).name == Path(file_id).name]
        if len(matches) != 1:
            raise ValueError(f"Catalog file ID not found or ambiguous: {file_id}")
        records = matches

    results = [_analyze_entry(record, catalog, root) for record in records]
    grades = {grade: sum(item["classification"]["grade"] == grade for item in results) for grade in "ABCD"}
    report = {
        "report_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "catalog_file": str(catalog_file),
        "catalog_entry_count": len(catalog["files"]),
        "analyzed_entry_count": len(results),
        "successful_entry_count": sum(item.get("error") is None and item.get("track_count") is not None for item in results),
        "error_count": sum(item.get("error") is not None for item in results),
        "classification_counts": grades,
        "candidate_file_ids": [item["file_id"] for item in results if item["classification"]["grade"] in {"A", "B"}],
        "results": results,
        "classification_policy": "Heuristic candidate extraction only. No instrument tags are created or changed.",
    }
    destination = Path(output_path)
    if not destination.is_absolute():
        destination = root / destination
    report["output_file"] = str(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as output:
            temp_path = Path(output.name)
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write("\n")
        temp_path.replace(destination)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return report


def render_drum_analysis_report(report: Mapping[str, Any]) -> str:
    lines = [
        "MIDI Drum Candidate Analysis",
        f"Catalog entries: {report['catalog_entry_count']}",
        f"Analyzed in this run: {report['analyzed_entry_count']}",
        f"Successful: {report['successful_entry_count']}; errors: {report['error_count']}",
        "Grades: " + ", ".join(f"{grade}={report['classification_counts'][grade]}" for grade in "ABCD"),
        "A/B candidate files:",
    ]
    candidates = [item for item in report["results"] if item["classification"]["grade"] in {"A", "B"}]
    if not candidates:
        lines.append("  (none)")
    for item in candidates:
        classification = item["classification"]
        lines.append(f"  [{classification['grade']}] {item['file_id']} confidence={classification['confidence']:.2f}")
    lines.append("Per-file details:")
    for item in report["results"]:
        classification = item["classification"]
        lines.append(
            f"\n[{classification['grade']}] {item['file_id']} "
            f"confidence={classification['confidence']:.2f} tracks={item.get('track_count')}"
        )
        if item.get("error"):
            lines.append(f"  Error: {item['error']['type']}: {item['error']['message']}")
        for track in item.get("tracks", []):
            channels = ",".join(str(channel["midi_channel"]) for channel in track["channels"]) or "none"
            notes = ",".join(str(note) for note in track["used_note_numbers"]) or "none"
            gm = ", ".join(f"{entry['note_number']} {entry['name']}" for entry in track["gm_drum_candidates"]) or "none"
            lines.append(f"  Track {track['index']} {track.get('name') or '(unnamed)'}: channels={channels}; notes={notes}")
            lines.append(f"    GM candidates: {gm}; polyphony max={track['max_simultaneous_notes']}; onset interval median={track['onset_interval_ticks']['median_beats']} beats")
        for reason in classification["reasons"]:
            lines.append(f"  Reason: {reason}")
    return "\n".join(lines)


def export_drum_analysis_csv(report: Mapping[str, Any], destination: str | Path) -> Path:
    """Write a compact row per file while keeping the detailed JSON as canonical output."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["file_id", "grade", "confidence", "track_count", "track_names", "midi_channels",
               "used_note_numbers", "gm_drum_candidates", "reasons", "error"]
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for item in report["results"]:
            classification = item["classification"]
            tracks = item.get("tracks", [])
            writer.writerow({
                "file_id": item["file_id"],
                "grade": classification["grade"],
                "confidence": classification["confidence"],
                "track_count": item.get("track_count"),
                "track_names": "; ".join(track.get("name") or "" for track in tracks),
                "midi_channels": "; ".join(str(channel["midi_channel"]) for track in tracks for channel in track["channels"]),
                "used_note_numbers": ";".join(str(note) for note in item.get("used_note_numbers", [])),
                "gm_drum_candidates": "; ".join(
                    f"{entry['note_number']}:{entry['gm_drum_candidate']}"
                    for entry in item.get("gm_drum_candidates", [])
                ),
                "reasons": " | ".join(classification["reasons"]),
                "error": json.dumps(item.get("error"), ensure_ascii=False) if item.get("error") else "",
            })
    return path
