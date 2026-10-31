import hashlib
import json
import sys

import mido
import pytest

from midi_generator.api import analyze_midi, convert_midi_channel
from midi_generator.cli import main


def make_channel_test_midi(path):
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    track = mido.MidiTrack([
        mido.MetaMessage("track_name", name="Percussion Source", time=0),
        mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(123), time=0),
        mido.MetaMessage("time_signature", numerator=7, denominator=8, time=0),
        mido.Message("note_on", note=36, velocity=91, channel=0, time=0),
        mido.Message("note_off", note=36, velocity=0, channel=0, time=120),
        mido.Message("control_change", control=1, value=64, channel=0, time=0),
        mido.Message("note_on", note=49, velocity=75, channel=1, time=120),
        mido.Message("note_off", note=49, velocity=23, channel=1, time=120),
        mido.Message("note_on", note=90, velocity=80, channel=0, time=120),
        mido.Message("note_off", note=90, velocity=0, channel=0, time=120),
        mido.Message("note_on", note=36, velocity=72, channel=0, time=0),
        mido.Message("note_off", note=36, velocity=0, channel=0, time=120),
        mido.Message("sysex", data=[1, 2, 3], time=0),
        mido.MetaMessage("end_of_track", time=0),
    ])
    midi.tracks.append(track)
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Other Track", time=0),
        mido.Message("program_change", program=12, channel=2, time=0),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    midi.save(path)
    return midi


def _absolute_events(track):
    tick = 0
    events = []
    for message in track:
        tick += message.time
        events.append((tick, message))
    return events


def test_convert_channel_preserves_notes_velocities_timing_meta_and_source(tmp_path):
    source = tmp_path / "source.mid"
    destination = tmp_path / "output" / "converted.mid"
    make_channel_test_midi(source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    before = mido.MidiFile(source)

    result = convert_midi_channel(source, destination)
    after = mido.MidiFile(destination)

    assert result["target_channel"] == 10
    assert result["changed_note_on_events"] == 4
    assert result["changed_note_off_events"] == 4
    assert [message.type for message in before.tracks[0]] == [message.type for message in after.tracks[0]]
    before_events = _absolute_events(before.tracks[0])
    after_events = _absolute_events(after.tracks[0])
    assert [tick for tick, _ in before_events] == [tick for tick, _ in after_events]
    for (_, original), (_, converted) in zip(before_events, after_events):
        if hasattr(original, "channel"):
            assert converted.channel == 9
            assert converted.dict() | {"channel": original.channel} == original.dict()
        else:
            assert converted.dict() == original.dict()
    assert after.tracks[0][0].name == "Percussion Source"
    assert after.tracks[1][0].name == "Other Track"
    assert next(message for message in after.tracks[0] if message.type == "set_tempo").tempo == mido.bpm2tempo(123)
    meter = next(message for message in after.tracks[0] if message.type == "time_signature")
    assert (meter.numerator, meter.denominator) == (7, 8)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash


def test_only_selected_source_channel_is_changed(tmp_path):
    source = tmp_path / "source.mid"
    destination = tmp_path / "channel_one_only.mid"
    make_channel_test_midi(source)
    convert_midi_channel(source, destination, source_channel=1, target_channel=10)
    converted = mido.MidiFile(destination)
    note_channels = {
        message.channel for track in converted.tracks for message in track
        if message.type in {"note_on", "note_off"}
    }
    assert note_channels == {1, 9}
    assert any(message.type == "control_change" and message.channel == 9
               for track in converted.tracks for message in track)


def test_convert_does_not_overwrite_and_dry_run_writes_nothing(tmp_path):
    source = tmp_path / "source.mid"
    destination = tmp_path / "existing.mid"
    make_channel_test_midi(source)
    destination.write_bytes(b"keep this")

    with pytest.raises(FileExistsError):
        convert_midi_channel(source, destination)
    assert destination.read_bytes() == b"keep this"

    preview = convert_midi_channel(source, dry_run=True, target_channel=10)
    assert preview["dry_run"] is True
    assert preview["destination_file"] is None
    assert preview["changed_channel_messages"] == 10
    assert not (tmp_path / "converted.mid").exists()


def test_analyze_midi_reports_note_counts_gm_candidates_and_non_gm_notes(tmp_path):
    source = tmp_path / "notes.mid"
    make_channel_test_midi(source)

    analysis = analyze_midi(source)

    notes = analysis["note_number_analysis"]
    assert notes["used_note_numbers"] == [36, 49, 90]
    assert [(item["note_number"], item["count"]) for item in notes["note_counts"]] == [
        (36, 2), (49, 1), (90, 1),
    ]
    assert (notes["minimum_note_number"], notes["maximum_note_number"]) == (36, 90)
    assert [(item["note_number"], item["gm_drum_candidate"]) for item in notes["gm_drum_candidates"]] == [
        (36, "Bass Drum 1"), (49, "Crash Cymbal 1"),
    ]
    assert notes["outside_gm_drum_map"] == [90]
    assert "do not prove" in notes["classification_note"]
    assert analysis["channel_analysis"]["channels"][0]["midi_channel"] == 1


def test_cli_analyze_convert_and_dry_run(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source.mid"
    destination = tmp_path / "converted.mid"
    make_channel_test_midi(source)

    monkeypatch.setattr(sys, "argv", ["midi-generator", "midi", "analyze", str(source)])
    main()
    analysis = json.loads(capsys.readouterr().out)
    assert analysis["note_number_analysis"]["outside_gm_drum_map"] == [90]

    monkeypatch.setattr(sys, "argv", [
        "midi-generator", "midi", "convert-channel", str(source), "--from", "1", "--to", "10",
        "--output", str(destination),
    ])
    main()
    converted = json.loads(capsys.readouterr().out)
    assert converted["destination_file"] == str(destination)
    assert destination.exists()

    preview_destination = tmp_path / "preview.mid"
    monkeypatch.setattr(sys, "argv", [
        "midi-generator", "midi", "convert-channel", str(source), "--to", "10", "--dry-run",
        "--output", str(preview_destination),
    ])
    main()
    preview = json.loads(capsys.readouterr().out)
    assert preview["dry_run"] is True
    assert preview["would_write"] == str(preview_destination)
    assert not preview_destination.exists()
