import json

import mido
import pytest

from midi_generator.storage.catalog import analyze_midi, scan_midi_catalog


def make_catalog_midi(path):
    midi = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.extend([
        mido.MetaMessage("track_name", name="Lead", time=0),
        mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0),
        mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0),
        mido.Message("note_on", note=40, velocity=80, time=0),
        mido.Message("note_off", note=40, velocity=0, time=120),
        mido.Message("note_on", note=64, velocity=100, time=360),
        mido.Message("note_off", note=64, velocity=0, time=480),
        mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(60), time=0),
        mido.MetaMessage("time_signature", numerator=3, denominator=4, time=960),
        mido.Message("note_on", note=72, velocity=0, time=0),
        mido.Message("note_on", note=72, velocity=70, time=0),
        mido.Message("note_off", note=72, velocity=0, time=480),
        mido.MetaMessage("end_of_track", time=1440),
    ])
    midi.save(path)
    return midi


def _entry(result, suffix):
    return next(item for item in result["catalog"]["files"] if item["path"].endswith(suffix))


def test_analyze_one_file_extracts_timing_and_track_data(tmp_path):
    midi_path = tmp_path / "one.mid"
    make_catalog_midi(midi_path)

    analysis = analyze_midi(midi_path, relative_path="data/sources/one.mid")

    assert analysis["path"] == "data/sources/one.mid"
    assert analysis["file_format"] == {"extension": ".mid", "smf_type": 0}
    assert analysis["track_count"] == 1
    assert analysis["tracks"][0] == {
        "index": 0,
        "name": "Lead",
        "note_count": 3,
        "pitch_range": {"minimum": 40, "maximum": 72},
        "velocity": {"average": 83.3333, "minimum": 70, "maximum": 100},
    }
    assert analysis["note_on_count"] == 3
    assert analysis["ticks_per_beat"] == 480
    assert analysis["tempo_events"] == [
        {"tick": 0, "bpm": 120.0, "microseconds_per_beat": 500000, "track_index": 0},
        {"tick": 960, "bpm": 60.0, "microseconds_per_beat": 1000000, "track_index": 0},
    ]
    assert analysis["time_signature_events"] == [
        {"tick": 0, "numerator": 4, "denominator": 4, "track_index": 0},
        {"tick": 1920, "numerator": 3, "denominator": 4, "track_index": 0},
    ]
    assert analysis["estimated_duration_seconds"] == 7.0
    assert analysis["estimated_bars"] == pytest.approx(7 / 3)
    assert analysis["analyzed_at"]


def test_scan_multiple_files_skips_other_extensions_and_continues_after_error(tmp_path):
    root = tmp_path / "project"
    source_dir = root / "data" / "sources"
    source_dir.mkdir(parents=True)
    make_catalog_midi(source_dir / "good.mid")
    nested = source_dir / "nested"
    nested.mkdir()
    make_catalog_midi(nested / "good.midi")
    (source_dir / "ignore.txt").write_text("not midi")
    (source_dir / "broken.mid").write_bytes(b"not a MIDI file")

    result = scan_midi_catalog(project_root=root)

    assert result["scanned"] == 3
    assert result["succeeded"] == 2
    assert result["failed"] == 1
    assert {item["path"] for item in result["catalog"]["files"]} == {
        "data/sources/good.mid",
        "data/sources/nested/good.midi",
        "data/sources/broken.mid",
    }
    broken = _entry(result, "broken.mid")
    assert broken["analysis"] is None
    assert broken["error"]["type"]
    assert broken["tags"] == {"instrument": [], "section": [], "style": []}
    assert json.loads((root / "data/catalog/midi_catalog.json").read_text())


def test_rescan_updates_in_place_preserves_manual_tags_and_retains_removed_entries(tmp_path):
    root = tmp_path / "project"
    source_dir = root / "data" / "sources"
    source_dir.mkdir(parents=True)
    make_catalog_midi(source_dir / "tagged.mid")
    (source_dir / "removed.midi").write_bytes(b"bad file")

    first = scan_midi_catalog(project_root=root)
    catalog_path = root / "data" / "catalog" / "midi_catalog.json"
    saved = json.loads(catalog_path.read_text())
    tagged = next(item for item in saved["files"] if item["path"].endswith("tagged.mid"))
    tagged["tags"] = {"instrument": ["guitar"], "section": ["verse"], "style": ["hardcore"]}
    tagged["provenance"] = {"source": "test pack", "license": "test license"}
    catalog_path.write_text(json.dumps(saved))

    (source_dir / "removed.midi").unlink()
    second = scan_midi_catalog(project_root=root)

    assert len(second["catalog"]["files"]) == 2
    assert _entry(second, "tagged.mid")["tags"]["section"] == ["verse"]
    assert _entry(second, "tagged.mid")["provenance"]["license"] == "test license"
    assert _entry(second, "removed.midi")["present"] is False
    assert first["scanned"] == 2
    assert second["scanned"] == 1
