"""Minimal Standard MIDI File read/write helpers."""
from collections import Counter
from pathlib import Path
from typing import Any
import mido


# General MIDI Level 1 percussion note candidates (MIDI note numbers 35–81).
# A match is only a mapping candidate; it does not classify the source as drums.
GM_DRUM_MAP = {
    35: "Acoustic Bass Drum", 36: "Bass Drum 1", 37: "Side Stick", 38: "Acoustic Snare",
    39: "Hand Clap", 40: "Electric Snare", 41: "Low Floor Tom", 42: "Closed Hi-Hat",
    43: "High Floor Tom", 44: "Pedal Hi-Hat", 45: "Low Tom", 46: "Open Hi-Hat",
    47: "Low-Mid Tom", 48: "Hi-Mid Tom", 49: "Crash Cymbal 1", 50: "High Tom",
    51: "Ride Cymbal 1", 52: "Chinese Cymbal", 53: "Ride Bell", 54: "Tambourine",
    55: "Splash Cymbal", 56: "Cowbell", 57: "Crash Cymbal 2", 58: "Vibraslap",
    59: "Ride Cymbal 2", 60: "Hi Bongo", 61: "Low Bongo", 62: "Mute Hi Conga",
    63: "Open Hi Conga", 64: "Low Conga", 65: "High Timbale", 66: "Low Timbale",
    67: "High Agogo", 68: "Low Agogo", 69: "Cabasa", 70: "Maracas",
    71: "Short Whistle", 72: "Long Whistle", 73: "Short Guiro", 74: "Long Guiro",
    75: "Claves", 76: "Hi Wood Block", 77: "Low Wood Block", 78: "Mute Cuica",
    79: "Open Cuica", 80: "Mute Triangle", 81: "Open Triangle",
}


def load_midi(path: str | Path) -> mido.MidiFile:
    """Load a Standard MIDI File without changing the source file."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"MIDI source not found: {source}")
    return mido.MidiFile(source)


def save_midi(midi: mido.MidiFile, destination: str | Path, *, overwrite: bool = False) -> Path:
    """Export a MIDI file, refusing to replace an existing file by default."""
    destination_path = Path(destination)
    if destination_path.exists() and not overwrite:
        raise FileExistsError(f"MIDI destination already exists: {destination_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    midi.save(destination_path)
    return destination_path


def inspect_midi(path: str | Path) -> dict:
    """Return basic metadata for a MIDI file."""
    source = Path(path)
    midi = load_midi(source)
    tempos = []
    time_signatures = []
    note_on_count = 0
    for track in midi.tracks:
        for message in track:
            if message.type == "set_tempo":
                tempos.append(mido.tempo2bpm(message.tempo))
            elif message.type == "time_signature":
                time_signatures.append([message.numerator, message.denominator])
            elif message.type == "note_on" and message.velocity > 0:
                note_on_count += 1
    return {
        "path": str(source),
        "ticks_per_beat": midi.ticks_per_beat,
        "track_count": len(midi.tracks),
        "duration_seconds": midi.length,
        "tempos_bpm": tempos,
        "time_signatures": time_signatures,
        "note_on_count": note_on_count,
    }


def analyze_midi_events(midi: mido.MidiFile) -> dict[str, Any]:
    """Summarize note-number and channel usage; GM drum labels are candidates only."""
    note_counts: Counter[int] = Counter()
    channel_message_counts: Counter[int] = Counter()
    channel_note_counts: Counter[int] = Counter()
    for track in midi.tracks:
        for message in track:
            if hasattr(message, "channel"):
                channel_message_counts[message.channel] += 1
            if message.type == "note_on" and message.velocity > 0:
                note_counts[message.note] += 1
                channel_note_counts[message.channel] += 1

    used_notes = sorted(note_counts)
    note_entries = [
        {"note_number": note, "count": note_counts[note], "gm_drum_candidate": GM_DRUM_MAP.get(note)}
        for note in used_notes
    ]
    return {
        "channel_analysis": {
            "channels": [
                {
                    "midi_channel": channel + 1,
                    "mido_channel": channel,
                    "channel_message_count": channel_message_counts[channel],
                    "note_on_count": channel_note_counts[channel],
                }
                for channel in sorted(channel_message_counts)
            ],
            "note_on_count": sum(note_counts.values()),
        },
        "note_number_analysis": {
            "used_note_numbers": used_notes,
            "note_counts": note_entries,
            "minimum_note_number": min(used_notes) if used_notes else None,
            "maximum_note_number": max(used_notes) if used_notes else None,
            "gm_drum_candidates": [entry for entry in note_entries if entry["gm_drum_candidate"] is not None],
            "outside_gm_drum_map": [note for note in used_notes if note not in GM_DRUM_MAP],
            "classification_note": "GM mapping matches are candidates only and do not prove that a MIDI file contains drums.",
        },
    }


def convert_midi_channel(
    source: str | Path,
    destination: str | Path | None = None,
    *,
    target_channel: int = 10,
    source_channel: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Copy a MIDI file while remapping channel-bearing messages to a new channel.

    Channel arguments use human numbering (1–16); Mido's internal value is one less.
    Meta events and SysEx messages are left untouched. The source file is never saved.
    """
    for name, channel in (("target_channel", target_channel), ("source_channel", source_channel)):
        if channel is not None and (isinstance(channel, bool) or not isinstance(channel, int) or not 1 <= channel <= 16):
            raise ValueError(f"{name} must be an integer from 1 to 16")
    if not dry_run and destination is None:
        raise ValueError("destination is required unless dry_run=True")

    source_path = Path(source)
    midi = load_midi(source_path)
    destination_path = Path(destination) if destination is not None else None
    if destination_path is not None and source_path.resolve() == destination_path.resolve():
        raise ValueError("Source and destination must be different paths")

    source_internal = source_channel - 1 if source_channel is not None else None
    target_internal = target_channel - 1
    matched_messages = changed_messages = changed_note_ons = changed_note_offs = 0
    for track in midi.tracks:
        for index, message in enumerate(track):
            if not hasattr(message, "channel"):
                continue
            if source_internal is not None and message.channel != source_internal:
                continue
            matched_messages += 1
            if message.channel == target_internal:
                continue
            changed_messages += 1
            if message.type == "note_on":
                changed_note_ons += 1
            elif message.type == "note_off":
                changed_note_offs += 1
            track[index] = message.copy(channel=target_internal)

    saved_path: Path | None = None
    if not dry_run:
        assert destination_path is not None
        saved_path = save_midi(midi, destination_path)
    return {
        "source_file": str(source_path),
        "destination_file": str(saved_path) if saved_path is not None else None,
        "would_write": str(destination_path) if dry_run and destination_path is not None else None,
        "source_channel": source_channel,
        "target_channel": target_channel,
        "dry_run": dry_run,
        "matched_channel_messages": matched_messages,
        "changed_channel_messages": changed_messages,
        "changed_note_on_events": changed_note_ons,
        "changed_note_off_events": changed_note_offs,
        "channels_are_human_numbered": True,
    }


def copy_midi(source: str | Path, destination: str | Path, *, overwrite: bool = False) -> Path:
    """Read and write a MIDI file to a new destination without modifying source."""
    source_path = Path(source)
    destination_path = Path(destination)
    if not source_path.is_file():
        raise FileNotFoundError(f"MIDI source not found: {source_path}")
    if source_path.resolve() == destination_path.resolve():
        raise ValueError("Source and destination must be different paths")
    midi = load_midi(source_path)
    return save_midi(midi, destination_path, overwrite=overwrite)
