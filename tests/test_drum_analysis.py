import json
import sys

import mido

from midi_generator.cli import main
from midi_generator.storage.catalog import scan_midi_catalog
from midi_generator.storage.drum_analysis import analyze_catalog_drums
from midi_generator.storage.patterns import add_tags


TPB = 480


def make_midi(path, *, notes, name="Track 1", channel=0, ticks_per_onset=480):
    midi = mido.MidiFile(type=0, ticks_per_beat=TPB)
    track = mido.MidiTrack([
        mido.MetaMessage("track_name", name=name, time=0),
        mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0),
        mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0),
    ])
    events = []
    for index, note in enumerate(notes):
        start = index * ticks_per_onset
        events.append((start, 1, mido.Message("note_on", note=note, velocity=90, channel=channel, time=0)))
        events.append((start + ticks_per_onset * 2, 0, mido.Message("note_off", note=note, velocity=0, channel=channel, time=0)))
    events.sort(key=lambda event: (event[0], event[1]))
    previous = 0
    for tick, _, message in events:
        track.append(message.copy(time=tick - previous))
        previous = tick
    track.append(mido.MetaMessage("end_of_track", time=max(0, len(notes) * ticks_per_onset * 2 - previous)))
    midi.tracks.append(track)
    midi.save(path)


def make_catalog(tmp_path):
    root = tmp_path / "project"
    sources = root / "data" / "sources"
    sources.mkdir(parents=True)
    make_midi(sources / "gm_candidate.mid", notes=[36, 38, 42, 46, 49, 51] * 2, channel=0, ticks_per_onset=120)
    make_midi(sources / "melodic.mid", notes=[24, 26, 28, 29, 31, 33, 35, 36], ticks_per_onset=480)
    make_midi(sources / "sparse.mid", notes=[36, 38], ticks_per_onset=960)
    make_midi(sources / "named_drums.mid", notes=[90, 92], name="Drum Kit", ticks_per_onset=480)
    make_midi(sources / "mixed.mid", notes=[36, 38, 24, 26, 28, 29, 31, 33], ticks_per_onset=480)
    scan_midi_catalog(project_root=root)
    return root, root / "data" / "catalog" / "midi_catalog.json"


def _by_name(report, name):
    return next(item for item in report["results"] if item["file_id"].endswith(name))


def test_candidate_grades_use_multiple_features_and_channel_one_is_allowed(tmp_path):
    root, catalog = make_catalog(tmp_path)
    report = analyze_catalog_drums(catalog_path=catalog, project_root=root)

    gm = _by_name(report, "gm_candidate.mid")
    melodic = _by_name(report, "melodic.mid")
    sparse = _by_name(report, "sparse.mid")
    named = _by_name(report, "named_drums.mid")
    mixed = _by_name(report, "mixed.mid")

    assert gm["classification"]["grade"] == "A"
    assert gm["tracks"][0]["channels"][0]["midi_channel"] == 1
    assert melodic["classification"]["grade"] == "C"
    assert sparse["classification"]["grade"] == "D"
    assert named["classification"]["grade"] in {"A", "B"}
    assert named["classification"]["features"]["track_name_hint"] is True
    assert any("track name" in reason.lower() for reason in named["classification"]["reasons"])
    assert mixed["classification"]["grade"] != "A"
    assert all(0 <= item["classification"]["confidence"] <= 1 for item in report["results"])


def test_report_contains_file_and_track_metadata_and_is_saved_without_catalog_mutation(tmp_path):
    root, catalog = make_catalog(tmp_path)
    add_tags("gm_candidate.mid", instrument="drums", section="breakdown", tags="half_time", catalog_path=catalog)
    catalog_before = catalog.read_bytes()
    output = root / "data" / "catalog" / "drum_analysis.json"

    report = analyze_catalog_drums(catalog_path=catalog, project_root=root, output_path=output)

    assert output.exists()
    stored = json.loads(output.read_text())
    assert stored["analyzed_entry_count"] == 5
    assert stored["classification_counts"]["A"] >= 1
    result = _by_name(stored, "gm_candidate.mid")
    assert result["file_path"] == "data/sources/gm_candidate.mid"
    assert result["track_count"] == 1
    assert result["tracks"][0]["name"] == "Track 1"
    assert result["tracks"][0]["onset_interval_ticks"]["median"] == 120
    assert result["tracks"][0]["max_simultaneous_notes"] >= 2
    assert result["time_signature_events"][0]["numerator"] == 4
    assert result["tempo_events"][0]["bpm"] == 120
    assert result["estimated_bars"] > 0
    assert catalog.read_bytes() == catalog_before
    assert _by_name(report, "gm_candidate.mid")["classification"]["grade"] == "A"
    assert stored["results"][0].get("tags") is None


def test_file_id_filter_and_cli_human_report_and_csv(tmp_path, monkeypatch, capsys):
    root, catalog = make_catalog(tmp_path)
    output = root / "drum_analysis.json"
    csv_path = root / "drum_analysis.csv"
    report = analyze_catalog_drums(catalog_path=catalog, project_root=root, output_path=output,
                                   file_id="data/sources/sparse.mid")
    assert report["analyzed_entry_count"] == 1
    assert report["classification_counts"]["D"] == 1

    monkeypatch.setattr(sys, "argv", [
        "midi-generator", "catalog", "analyze-drums", "--catalog", str(catalog),
        "--project-root", str(root), "--output", str(output), "--csv", str(csv_path), "--report",
    ])
    main()
    stdout = capsys.readouterr().out
    assert "MIDI Drum Candidate Analysis" in stdout
    assert "A/B candidate files" in stdout
    assert "Per-file details" in stdout
    assert csv_path.exists()
    assert "grade,confidence" in csv_path.read_text(encoding="utf-8-sig")
