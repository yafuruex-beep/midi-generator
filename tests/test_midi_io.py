import mido
import pytest
from midi_generator.core.midi_io import copy_midi, inspect_midi, load_midi, save_midi


def make_test_midi(path):
    midi = mido.MidiFile()
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0))
    track.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    track.append(mido.Message("note_on", note=36, velocity=100, time=0))
    track.append(mido.Message("note_off", note=36, velocity=0, time=480))
    midi.save(path)


def test_inspect_midi(tmp_path):
    source = tmp_path / "test.mid"
    make_test_midi(source)
    info = inspect_midi(source)
    assert info["track_count"] == 1
    assert info["note_on_count"] == 1
    assert info["tempos_bpm"][0] == pytest.approx(120)
    assert info["time_signatures"] == [[4, 4]]


def test_round_trip_preserves_note_events(tmp_path):
    source = tmp_path / "source.mid"
    destination = tmp_path / "output" / "copy.mid"
    make_test_midi(source)
    copy_midi(source, destination)
    original = mido.MidiFile(source)
    copied = mido.MidiFile(destination)
    assert [msg.dict() for msg in original.tracks[0]] == [msg.dict() for msg in copied.tracks[0]]


def test_round_trip_refuses_to_overwrite_source(tmp_path):
    source = tmp_path / "source.mid"
    make_test_midi(source)
    with pytest.raises(ValueError):
        copy_midi(source, source)


def test_save_and_load_midi(tmp_path):
    path = tmp_path / "exported.mid"
    midi = mido.MidiFile()
    midi.tracks.append(mido.MidiTrack([mido.Message("note_on", note=60, velocity=80)]))

    assert save_midi(midi, path) == path
    loaded = load_midi(path)
    assert loaded.tracks[0][0].note == 60
    with pytest.raises(FileExistsError):
        save_midi(midi, path)
