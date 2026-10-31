"""Shared instrument/channel conventions and source-path classification."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import mido


DEFAULT_INSTRUMENT_CHANNELS = {"drums": 10, "guitar": 1, "bass": 2, "synth": 3}
INSTRUMENTS = frozenset((*DEFAULT_INSTRUMENT_CHANNELS, "other"))
GENRES = frozenset({"metalcore", "hardcore", "other"})


def load_instrument_config(path: str | Path = "config/instrument_channels.json") -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {"standard_channels": dict(DEFAULT_INSTRUMENT_CHANNELS), "correction": {"in_place": True, "backup_directory": "data/backups"}}
    with config_path.open(encoding="utf-8") as source:
        config = json.load(source)
    channels = config.get("standard_channels")
    if not isinstance(channels, dict):
        raise ValueError("instrument config must contain standard_channels")
    merged = dict(DEFAULT_INSTRUMENT_CHANNELS)
    for instrument, channel in channels.items():
        if instrument not in INSTRUMENTS - {"other"}:
            raise ValueError(f"Unsupported instrument in channel config: {instrument}")
        if isinstance(channel, bool) or not isinstance(channel, int) or not 1 <= channel <= 16:
            raise ValueError(f"Channel for {instrument} must be an integer from 1 to 16")
        merged[instrument] = channel
    return {**config, "standard_channels": merged}


def classify_source_path(relative_path: str | Path) -> dict[str, str | None]:
    """Get instrument/genre only from the standardized source directory path."""
    parts = Path(relative_path).as_posix().split("/")
    if len(parts) < 3 or parts[:2] != ["data", "sources"]:
        return {"instrument": None, "source_directory": None, "genre_directory": None}
    instrument = parts[2].lower()
    if instrument == "unclassified":
        return {"instrument": None, "source_directory": "data/sources/unclassified", "genre_directory": None}
    if instrument not in INSTRUMENTS - {"other"}:
        return {"instrument": None, "source_directory": f"data/sources/{parts[2]}", "genre_directory": None}
    genre = parts[3].lower() if len(parts) > 3 else None
    return {
        "instrument": instrument,
        "source_directory": f"data/sources/{parts[2]}",
        "genre_directory": genre if genre in GENRES else None,
    }


def midi_channel_inventory(midi: mido.MidiFile) -> tuple[list[int], list[dict[str, Any]]]:
    """Return one-based channel inventory, including details for each track."""
    all_channels: set[int] = set()
    per_track: list[dict[str, Any]] = []
    for index, track in enumerate(midi.tracks):
        name = next((message.name for message in track if message.type == "track_name"), None)
        channels = sorted({message.channel + 1 for message in track if hasattr(message, "channel")})
        all_channels.update(channels)
        per_track.append({"index": index, "name": name, "midi_channels": channels})
    return sorted(all_channels), per_track


def source_channel_metadata(
    midi: mido.MidiFile,
    relative_path: str,
    *,
    tags: Mapping[str, Any] | None = None,
    instrument_channels: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    classified = classify_source_path(relative_path)
    channels, tracks = midi_channel_inventory(midi)
    instrument = classified["instrument"]
    expected = (instrument_channels or DEFAULT_INSTRUMENT_CHANNELS).get(instrument) if instrument else None
    manual_instruments = (tags or {}).get("instrument", [])
    manual_instruments = {str(value).lower() for value in manual_instruments} if isinstance(manual_instruments, list) else set()
    named_instruments: set[str] = set()
    for track in tracks:
        name = (track.get("name") or "").lower().replace("_", " ").replace("-", " ")
        if any(word in name for word in ("drum", "perc", "kick", "snare", "cymbal", "hi hat", "hihat")):
            named_instruments.add("drums")
        if any(word in name for word in ("guitar", "gtr")):
            named_instruments.add("guitar")
        if "bass" in name and "bass drum" not in name:
            named_instruments.add("bass")
        if any(word in name for word in ("synth", "keyboard", "keys")):
            named_instruments.add("synth")
    mixed = len(channels) > 1
    mixed = mixed or len(manual_instruments - ({instrument} if instrument else set())) > 0
    mixed = mixed or bool(named_instruments - ({instrument} if instrument else set()))
    if not channels or not instrument:
        status = "unknown"
    elif mixed:
        status = "mixed"
    elif channels == [expected]:
        status = "valid"
    else:
        status = "needs_correction"
    warnings = []
    if not channels:
        warnings.append("No channel-bearing MIDI messages were found.")
    if not instrument:
        warnings.append("Instrument is not classified by the source directory.")
    if mixed:
        warnings.append("Multiple MIDI channels or conflicting instrument evidence were found; automatic correction is skipped.")
    if status == "needs_correction":
        warnings.append(f"Actual channel(s) {channels} differ from the configured standard channel {expected}.")
    return {
        **classified,
        "actual_midi_channels": channels,
        "expected_midi_channel": expected,
        "channel_status": status,
        "tracks": tracks,
        "warnings": warnings,
    }
