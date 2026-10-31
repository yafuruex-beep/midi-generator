"""Deterministic template-based drum MIDI generation from catalog patterns."""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import re
from typing import Any, Mapping

import mido

from midi_generator import __version__
from midi_generator.core.models import GenerationRequest, GenerationResult
from midi_generator.storage.catalog import _load_catalog
from midi_generator.storage.patterns import search_patterns
from midi_generator.modules.drums.groove import validate_groove_preferences


DRUM_NAME_RE = re.compile(r"drum|perc|kick|snare|hi.?hat|cymbal|tom", re.IGNORECASE)


def _style_values(style: dict[str, Any] | tuple[str, ...]) -> list[str]:
    if isinstance(style, tuple):
        return list(dict.fromkeys(value.strip().lower() for value in style if value.strip()))
    genres = style.get("genre", style.get("genres", style.get("style", [])))
    if isinstance(genres, str):
        genres = [genres]
    if isinstance(genres, (list, tuple)):
        return list(dict.fromkeys(str(value).strip().lower() for value in genres if str(value).strip()))
    return []


def _select_pattern(request: GenerationRequest, catalog_path: Path, groove_analysis_path: Path | None = None) -> dict[str, Any]:
    preferences = validate_groove_preferences(request.groove)
    styles = _style_values(request.style)
    candidates: dict[str, dict[str, Any]] = {}
    for style in styles or [None]:
        found = search_patterns(
            catalog_path=catalog_path,
            instrument="drums",
            section=request.section.lower(),
            style=style,
            required_tags=request.tags,
            bpm_target=request.tempo,
            time_signature=f"{request.time_signature[0]}/{request.time_signature[1]}",
            bars_target=request.bars,
            groove_analysis_path=groove_analysis_path,
            **preferences,
        )
        for result in found:
            path = str(result["file_path"])
            if path not in candidates or result["score"] > candidates[path]["score"]:
                candidates[path] = result
    ranked = sorted(candidates.values(), key=lambda item: (-item["score"], str(item["file_path"]).casefold()))
    if not ranked:
        criteria = [f"instrument=drums", f"section={request.section}"]
        if styles:
            criteria.append(f"style={'|'.join(styles)}")
        if request.tags:
            criteria.append(f"required_tags={','.join(request.tags)}")
        criteria.append(f"time_signature={request.time_signature[0]}/{request.time_signature[1]}")
        raise ValueError(
            "No catalog MIDI pattern matches " + ", ".join(criteria)
            + ". Tag a suitable source MIDI and rescan the catalog; no substitute was generated."
        )
    if preferences:
        ranked = [item for item in ranked if item["groove_selection"]["analysis_status"] == "available"]
        if not ranked:
            raise ValueError("No usable groove analysis for section-matching sources; run catalog analyze-grooves. No substitute was generated.")
    # Weighted choice is reproducible for the same candidate order, request, and seed.
    rng = random.Random(request.seed)
    weights = [max(float(item["score"]), 0.001) ** 3 for item in ranked]
    return rng.choices(ranked, weights=weights, k=1)[0]


def _resolve_source(file_id: str, catalog: Mapping[str, Any], project_root: Path) -> Path:
    identifier = Path(file_id)
    candidates = [identifier] if identifier.is_absolute() else [project_root / identifier]
    source_directory = catalog.get("source_directory")
    if source_directory:
        source_root = Path(source_directory)
        if not source_root.is_absolute():
            source_root = project_root / source_root
        if not identifier.is_absolute():
            candidates.append(source_root / identifier)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"Catalog MIDI source is missing: {file_id}")


def _absolute_track(track: mido.MidiTrack) -> list[tuple[int, int, mido.Message | mido.MetaMessage]]:
    tick = 0
    events = []
    for order, message in enumerate(track):
        tick += message.time
        events.append((tick, order, message))
    return events


def _select_drum_tracks(
    midi: mido.MidiFile,
) -> tuple[list[tuple[int, list[tuple[int, int, mido.Message | mido.MetaMessage]], set[int] | None]], list[str]]:
    tracks = [_absolute_track(track) for track in midi.tracks]
    populated: list[int] = []
    channel_nine: list[int] = []
    named: list[int] = []
    channels_by_track: dict[int, set[int]] = {}
    for index, events in enumerate(tracks):
        notes = [message for _, _, message in events if message.type == "note_on" and message.velocity > 0]
        if not notes:
            continue
        populated.append(index)
        channels = {message.channel for message in notes}
        channels_by_track[index] = channels
        if 9 in channels:
            channel_nine.append(index)
        track_name = next((message.name for _, _, message in events if message.type == "track_name"), "")
        if DRUM_NAME_RE.search(track_name):
            named.append(index)

    selected = list(dict.fromkeys(channel_nine + named))
    reasons: list[str] = []
    if channel_nine:
        reasons.append("Drum track(s) identified from MIDI channel 10.")
    if named:
        reasons.append("Drum track(s) identified from explicit track names.")
    if not selected and len(populated) == 1:
        selected = populated
        reasons.append("Single note-bearing track accepted because its catalog entry is manually tagged as drums.")
    if not selected:
        raise ValueError(
            "Could not identify a drum track from channel 10 or an explicit drum/percussion track name; "
            "multi-track material is not guessed from note pitches."
        )
    selected_tracks = []
    for index in selected:
        # A channel-10 track can contain other channel parts; retain only its explicit drum channel.
        channel_filter = {9} if 9 in channels_by_track[index] else None
        selected_tracks.append((index, tracks[index], channel_filter))
    return selected_tracks, reasons


def _source_meter(midi: mido.MidiFile) -> tuple[set[tuple[int, int]], bool]:
    meters: set[tuple[int, int]] = set()
    for track in midi.tracks:
        for message in track:
            if message.type == "time_signature":
                meters.add((message.numerator, message.denominator))
    return meters, bool(meters)


def _midi_channel(request: GenerationRequest) -> int:
    channel = request.options.get("midi_channel", 10)
    if isinstance(channel, bool) or not isinstance(channel, int) or not 1 <= channel <= 16:
        raise ValueError("options.midi_channel must be an integer from 1 to 16")
    return channel - 1


def _collect_pattern_events(
    selected_tracks: list[tuple[int, list[tuple[int, int, mido.Message | mido.MetaMessage]], set[int] | None]],
    output_channel: int,
    loop_ticks: int,
    total_ticks: int,
) -> tuple[list[tuple[int, int, int, mido.Message | mido.MetaMessage]], list[str]]:
    output_events: list[tuple[int, int, int, mido.Message | mido.MetaMessage]] = []
    warnings: list[str] = []
    sequence = 0
    truncated_note = False
    unmatched_on = False
    unmatched_off = False
    for _, events, channel_filter in selected_tracks:
        open_notes: dict[tuple[int, int], deque[tuple[int, mido.Message]]] = defaultdict(deque)
        intervals: list[tuple[int, int, mido.Message, mido.Message]] = []
        auxiliary: list[tuple[int, mido.Message | mido.MetaMessage]] = []
        for tick, _, message in events:
            if message.type == "end_of_track" or message.type == "track_name" or message.type in {"set_tempo", "time_signature"}:
                continue
            if hasattr(message, "channel") and channel_filter is not None and message.channel not in channel_filter:
                continue
            if message.type == "note_on" and message.velocity > 0:
                open_notes[(message.channel, message.note)].append((tick, message))
            elif message.type == "note_off" or (message.type == "note_on" and message.velocity == 0):
                key = (message.channel, message.note)
                if open_notes[key]:
                    start_tick, note_on = open_notes[key].popleft()
                    note_off = message if message.type == "note_off" else mido.Message(
                        "note_off", note=message.note, velocity=0, channel=message.channel,
                    )
                    intervals.append((start_tick, tick, note_on, note_off))
                else:
                    unmatched_off = True
            else:
                auxiliary.append((tick, message))
        for (_, note), pending in open_notes.items():
            while pending:
                start_tick, note_on = pending.popleft()
                intervals.append((start_tick, loop_ticks, note_on, mido.Message(
                    "note_off", note=note, velocity=0, channel=note_on.channel,
                )))
                unmatched_on = True

        repetitions = math.ceil(total_ticks / loop_ticks)
        for repetition in range(repetitions):
            offset = repetition * loop_ticks
            for tick, message in auxiliary:
                if tick >= loop_ticks or offset + tick >= total_ticks:
                    continue
                copied = message.copy(time=0, channel=output_channel) if hasattr(message, "channel") else message.copy(time=0)
                priority = 4 if not copied.is_meta else 3
                output_events.append((offset + tick, priority, sequence, copied))
                sequence += 1
            for start_tick, end_tick, note_on, note_off in intervals:
                if start_tick >= loop_ticks or offset + start_tick >= total_ticks:
                    continue
                start = offset + start_tick
                if end_tick > loop_ticks:
                    warnings.append("A source note crossing the loop boundary was closed at the loop boundary.")
                end = offset + min(end_tick, loop_ticks)
                if end > total_ticks:
                    end = total_ticks
                    truncated_note = True
                if end < start:
                    continue
                on_copy = note_on.copy(time=0, channel=output_channel)
                off_copy = note_off.copy(time=0, channel=output_channel)
                output_events.append((start, 5, sequence, on_copy))
                sequence += 1
                output_events.append((end, 4, sequence, off_copy))
                sequence += 1

    if truncated_note:
        warnings.append("A note crossing the requested end boundary was closed with a Note Off at the boundary.")
    if unmatched_on:
        warnings.append("An unmatched Note On was closed at its source loop boundary.")
    if unmatched_off:
        warnings.append("Unmatched source Note Off events were ignored.")
    return output_events, warnings


def _make_output_midi(
    source: mido.MidiFile,
    selected_tracks: list[tuple[int, list[tuple[int, int, mido.Message | mido.MetaMessage]], set[int] | None]],
    request: GenerationRequest,
    output_channel: int,
    source_bars: int,
) -> tuple[mido.MidiFile, dict[str, Any], list[str]]:
    numerator, denominator = request.time_signature
    ticks_per_bar = source.ticks_per_beat * numerator * 4 / denominator
    if not ticks_per_bar.is_integer():
        raise ValueError("Requested meter does not map to an integer tick count at this MIDI resolution")
    ticks_per_bar = int(ticks_per_bar)
    loop_ticks = source_bars * ticks_per_bar
    total_ticks = request.bars * ticks_per_bar
    output_events, warnings = _collect_pattern_events(selected_tracks, output_channel, loop_ticks, total_ticks)

    tempo_microseconds = mido.bpm2tempo(request.tempo)
    if not 1 <= tempo_microseconds <= 0xFFFFFF:
        raise ValueError("Requested tempo cannot be represented by a Standard MIDI tempo event")
    fixed_events: list[tuple[int, int, int, mido.Message | mido.MetaMessage]] = [
        (0, 0, -3, mido.MetaMessage("track_name", name="Drums", time=0)),
        (0, 1, -2, mido.MetaMessage("set_tempo", tempo=tempo_microseconds, time=0)),
        (0, 2, -1, mido.MetaMessage("time_signature", numerator=numerator, denominator=denominator, time=0)),
    ]
    output_events.extend(fixed_events)
    output_events.sort(key=lambda event: (event[0], event[1], event[2]))

    track = mido.MidiTrack()
    previous_tick = 0
    for tick, _, _, message in output_events:
        if tick < previous_tick:
            raise RuntimeError("Internal event ordering error")
        track.append(message.copy(time=tick - previous_tick))
        previous_tick = tick
    track.append(mido.MetaMessage("end_of_track", time=max(0, total_ticks - previous_tick)))
    generated = mido.MidiFile(type=0, ticks_per_beat=source.ticks_per_beat)
    generated.tracks.append(track)
    operations = {
        "source_bars": source_bars,
        "target_bars": request.bars,
        "loop_count": math.ceil(request.bars / source_bars),
        "trimmed": source_bars > request.bars,
        "tempo_set_bpm": request.tempo,
        "tick_positions_scaled": False,
        "output_midi_channel": output_channel + 1,
        "selected_source_track_indices": [index for index, _, _ in selected_tracks],
        "merged_source_track_count": len(selected_tracks),
    }
    if len(selected_tracks) > 1:
        warnings.append("Multiple identified drum tracks were merged into the output Drums track.")
    return generated, operations, warnings


def _safe_stem(request: GenerationRequest) -> str:
    section = re.sub(r"[^A-Za-z0-9_-]+", "_", request.section.strip()).strip("_") or "pattern"
    tempo = f"{request.tempo:g}"
    return f"drums_{section}_{tempo}bpm_{request.bars}bars"


def _available_output(output_path: str | Path | None, root: Path, request: GenerationRequest) -> tuple[Path, Path]:
    if output_path is None:
        desired = root / "output" / f"{_safe_stem(request)}.mid"
    else:
        desired = Path(output_path)
        if not desired.is_absolute():
            desired = root / desired
        if desired.suffix.lower() not in {".mid", ".midi"}:
            desired = desired.with_suffix(".mid")
    desired.parent.mkdir(parents=True, exist_ok=True)
    for suffix in range(100_000):
        if suffix == 0:
            midi_path = desired
        else:
            midi_path = desired.with_name(f"{desired.stem}_{suffix}{desired.suffix}")
        metadata_path = Path(f"{midi_path}.json")
        if not midi_path.exists() and not metadata_path.exists():
            return midi_path, metadata_path
    raise FileExistsError(f"Could not find an unused output name for {desired}")


def generate_drums(
    request: GenerationRequest,
    *,
    output_path: str | Path | None = None,
    catalog_path: str | Path = "data/catalog/midi_catalog.json",
    project_root: str | Path = ".",
    groove_analysis_path: str | Path | None = None,
) -> GenerationResult:
    """Select a tagged drum pattern, adjust tempo/length, and export a new MIDI file."""
    if request.instrument.lower() != "drums":
        raise ValueError("The current generator supports instrument='drums' only")
    root = Path(project_root).resolve()
    catalog_file = Path(catalog_path)
    if not catalog_file.is_absolute():
        catalog_file = root / catalog_file
    groove_file = Path(groove_analysis_path) if groove_analysis_path is not None else None
    if groove_file is not None and not groove_file.is_absolute():
        groove_file = root / groove_file
    selected = _select_pattern(request, catalog_file, groove_file)
    catalog = _load_catalog(catalog_file)
    source_path = _resolve_source(selected["file_path"], catalog, root)
    source = mido.MidiFile(source_path)
    selected_tracks, warnings = _select_drum_tracks(source)
    requested_meter = tuple(request.time_signature)
    source_meters, meter_was_explicit = _source_meter(source)
    if source_meters and source_meters != {requested_meter}:
        source_text = ", ".join(f"{n}/{d}" for n, d in sorted(source_meters))
        raise ValueError(
            f"Time-signature conversion is not supported: source meter(s) {source_text}, "
            f"requested {requested_meter[0]}/{requested_meter[1]}"
        )
    if not meter_was_explicit:
        warnings.append("Source has no time-signature event; treating it as 4/4 for pattern-length estimation.")
    if not any(message.type == "set_tempo" for track in source.tracks for message in track):
        warnings.append("Source has no tempo event; catalog analysis assumes 120 BPM. Output tempo was set to the requested BPM.")
    if source.type == 2 and len(selected_tracks) > 1:
        raise ValueError("Merging multiple independent MIDI format 2 sequences is not supported")

    all_track_ends = [sum(message.time for message in track) for track in source.tracks]
    source_ticks = max(all_track_ends, default=0)
    if source_ticks <= 0:
        raise ValueError("Selected drum track has no MIDI duration")
    numerator, denominator = requested_meter
    ticks_per_bar = source.ticks_per_beat * numerator * 4 / denominator
    if not ticks_per_bar.is_integer():
        raise ValueError("Requested meter does not map to an integer tick count at this MIDI resolution")
    bar_ratio = float(selected.get("bars") or (source_ticks / int(ticks_per_bar)))
    nearest_bars = round(bar_ratio)
    source_bars = max(1, nearest_bars if abs(bar_ratio - nearest_bars) <= 0.05 else math.ceil(bar_ratio))
    if abs(bar_ratio - source_bars) > 0.05:
        warnings.append(f"Source duration is approximately {bar_ratio:.3f} bars; rounded up to {source_bars} complete bar(s).")

    output_channel = _midi_channel(request)
    generated_midi, operations, processing_warnings = _make_output_midi(
        source, selected_tracks, request, output_channel, source_bars,
    )
    warnings.extend(processing_warnings)
    midi_path, metadata_path = _available_output(output_path, root, request)
    generated_at = datetime.now(timezone.utc).isoformat()
    style = list(request.style) if isinstance(request.style, tuple) else request.style
    request_data = {
        "instrument": request.instrument,
        "section": request.section,
        "tempo": request.tempo,
        "bars": request.bars,
        "time_signature": list(request.time_signature),
        "style": style,
        "tags": list(request.tags),
        "key": request.key,
        "chords": list(request.chords),
        "seed": request.seed,
        "options": request.options,
        "groove": request.groove,
    }
    metadata = {
        "request": request_data,
        "source_midi": str(source_path),
        "source_id": selected.get("file_id", selected["file_path"]),
        "catalog_file_id": selected["file_path"],
        "selected_pattern_tags": selected["tags"],
        "selection_score": selected["score"],
        "selection_score_breakdown": selected["score_breakdown"],
        "processing": operations,
        "output_file": str(midi_path),
        "metadata_file": str(metadata_path),
        "seed": request.seed,
        "generated_at": generated_at,
        "generator_version": __version__,
        "warnings": warnings,
    }
    if request.groove:
        metadata["selected_groove"] = selected["groove"]
        metadata["groove_confidence"] = selected["groove_confidence"]
        metadata["groove_analysis_warnings"] = selected["groove_warnings"]
        metadata["groove_selection"] = selected["groove_selection"]
        metadata["groove_match_score"] = selected["groove_match_score"]
        if selected["groove_selection"]["differences"]:
            warnings.append("Selected source is an approximate groove match; see groove_selection.differences.")
        warnings.extend(selected["groove_warnings"])
    created: list[Path] = []
    try:
        with midi_path.open("xb") as output_file:
            created.append(midi_path)
            generated_midi.save(file=output_file)
        with metadata_path.open("x", encoding="utf-8") as metadata_file:
            created.append(metadata_path)
            json.dump(metadata, metadata_file, ensure_ascii=False, indent=2)
            metadata_file.write("\n")
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return GenerationResult(output_files=[midi_path], warnings=warnings, metadata=metadata)
