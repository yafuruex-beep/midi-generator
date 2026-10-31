import hashlib
import json
from pathlib import Path
import sys

import jsonschema
import mido
import pytest

from midi_generator.api import (
    analyze_guitar_midi, analyze_catalog_guitars, get_guitar_riff_analysis,
    search_guitar_riffs, search_patterns, generate,
)
from midi_generator.cli import main
from midi_generator.storage.catalog import scan_midi_catalog


def write_midi(path, notes, *, tpb=480, end_beats=16, tracks=1, tempos=None, meters=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    midi = mido.MidiFile(type=1, ticks_per_beat=tpb)
    conductor_events = []
    for beat, tempo in tempos or [(0, 500000)]:
        conductor_events.append((round(beat * tpb), mido.MetaMessage("set_tempo", tempo=tempo)))
    for beat, numerator, denominator in meters or [(0, 4, 4)]:
        conductor_events.append((round(beat * tpb), mido.MetaMessage("time_signature", numerator=numerator, denominator=denominator)))
    conductor = mido.MidiTrack()
    previous = 0
    for tick, message in sorted(conductor_events, key=lambda item: item[0]):
        conductor.append(message.copy(time=tick - previous))
        previous = tick
    conductor.append(mido.MetaMessage("end_of_track", time=max(0, round(end_beats * tpb) - previous)))
    midi.tracks.append(conductor)
    event_lists = [[] for _ in range(tracks)]
    for item in notes:
        start, pitch, duration = item[:3]
        velocity = item[3] if len(item) > 3 else 100
        track_index = item[4] if len(item) > 4 else 0
        channel = item[5] if len(item) > 5 else 0
        event_lists[track_index].extend([
            (round(start * tpb), 1, mido.Message("note_on", note=pitch, velocity=velocity, channel=channel)),
            (round((start + duration) * tpb), 0, mido.Message("note_off", note=pitch, velocity=64, channel=channel)),
        ])
    for index, events in enumerate(event_lists):
        track = mido.MidiTrack([mido.MetaMessage("track_name", name=f"Guitar {index + 1}")])
        previous = 0
        for tick, _, message in sorted(events, key=lambda item: (item[0], item[1])):
            track.append(message.copy(time=tick - previous))
            previous = tick
        track.append(mido.MetaMessage("end_of_track", time=max(0, round(end_beats * tpb) - previous)))
        midi.tracks.append(track)
    midi.save(path)
    return path


def repeated(step=0.25, duration=0.125, pitch=36, beats=16):
    return [(index * step, pitch, duration) for index in range(round(beats / step))]


def validate_source(source):
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/guitar_riff_analysis.schema.json").read_text())
    jsonschema.Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/success"}).validate(source)
    json.dumps(source, allow_nan=False)


def test_pitch_statistics_classes_and_power_chord_intervals(tmp_path):
    notes = [(beat, note, 1.5) for beat in range(0, 16, 2) for note in (36, 43, 48)]
    source = analyze_guitar_midi(write_midi(tmp_path / "power.mid", notes))
    validate_source(source)
    assert source["pitch"]["min_note"] == 36
    assert source["pitch"]["max_note"] == 48
    assert source["pitch"]["mean_note"] == pytest.approx((36 + 43 + 48) / 3)
    assert source["pitch"]["median_note"] == 43
    assert source["pitch"]["used_note_count"] == 3
    assert source["pitch"]["pitch_classes"]["C"] == 16
    assert source["chords"]["power_chord_ratio"] == 1
    assert source["chords"]["structures"][0]["intervals_from_lowest"] == [0, 7, 12]
    assert source["polyphony"]["classification"] == "chord_based"
    assert source["polyphony"]["three_plus_ratio"] == 1
    assert "power_chord_riff" in source["riff_characteristics"]
    assert "open_chord_riff" in source["riff_characteristics"]
    assert source["source_context"]["tuning"] is None


@pytest.mark.parametrize("step,label", [(0.5, "eighth"), (0.25, "sixteenth")])
def test_monophonic_subdivision_density_duration_and_chug(tmp_path, step, label):
    source = analyze_guitar_midi(write_midi(tmp_path / "mono.mid", repeated(step)))
    validate_source(source)
    assert source["polyphony"]["classification"] == "monophonic"
    assert source["polyphony"]["monophonic_ratio"] == 1
    assert source["rhythm"]["subdivision"] == label
    assert source["rhythm"]["notes_per_quarter"] == pytest.approx(1 / step)
    assert source["rhythm"]["notes_per_second"] == pytest.approx(2 / step)
    assert source["note_duration"]["beats"]["median"] == pytest.approx(0.125)
    assert source["note_duration"]["seconds"]["median"] == pytest.approx(0.0625)
    assert source["note_duration"]["rests_beats"]["count"] > 0
    assert "chug" in source["riff_characteristics"]
    assert "single_note_riff" in source["riff_characteristics"]


def test_one_sixteenth_position_is_not_sixteenth_based(tmp_path):
    notes = repeated(step=0.5) + [(0.25, 40, 0.1)]
    assert analyze_guitar_midi(write_midi(tmp_path / "one.mid", notes))["rhythm"]["subdivision"] == "eighth"


def test_syncopation_uses_positions_accents_rests_and_held_notes(tmp_path):
    dense = analyze_guitar_midi(write_midi(tmp_path / "dense.mid", repeated()))
    offbeat = [(quarter + offset, 36, 0.125) for quarter in range(16) for offset in (0.25, 0.75)]
    sync = analyze_guitar_midi(write_midi(tmp_path / "sync.mid", offbeat))
    assert sync["rhythm"]["syncopation"] == "high"
    assert sync["rhythm"]["notes_per_quarter"] < dense["rhythm"]["notes_per_quarter"]
    assert sync["rhythm"]["syncopation_score"] > dense["rhythm"]["syncopation_score"]
    assert "syncopated_riff" in sync["riff_characteristics"]
    held = analyze_guitar_midi(write_midi(tmp_path / "held.mid", [(q + 0.75, 36, 0.5) for q in range(15)]))
    assert held["rhythm"]["held_over_strong_beat_share"] > 0


def test_short_legato_overlap_is_not_a_chord(tmp_path):
    notes = [(q, 36 + q % 4, 1.03 if q < 15 else 1) for q in range(16)]
    source = analyze_guitar_midi(write_midi(tmp_path / "legato.mid", notes))
    assert source["polyphony"]["max_simultaneous_notes"] == 2
    assert source["polyphony"]["two_note_ratio"] > 0
    assert source["polyphony"]["classification"] == "mostly_monophonic"
    assert source["polyphony"]["effective_ratios"]["monophonic_ratio"] == 1
    assert source["chords"]["group_count"] == 0


def test_strummed_chords_and_arpeggios_are_not_conflated(tmp_path):
    strum = [(bar * 4 + offset, pitch, 2) for bar in range(4) for offset, pitch in [(0, 36), (0.02, 43), (0.03, 48)]]
    source = analyze_guitar_midi(write_midi(tmp_path / "strum.mid", strum))
    assert source["chords"]["group_count"] == 4
    assert source["chords"]["power_chord_ratio"] == 1
    arpeggio = [(bar * 4 + offset, pitch, 1) for bar in range(4) for offset, pitch in [(0, 36), (0.2, 43), (0.4, 48)]]
    result = analyze_guitar_midi(write_midi(tmp_path / "arpeggio.mid", arpeggio))
    assert result["polyphony"]["max_simultaneous_notes"] >= 2
    assert result["chords"]["group_count"] == 0
    assert result["chords"]["classification"] == "unknown"


@pytest.mark.parametrize("pitches,label", [([36, 43], "power_chord"), ([36, 40, 43], "triad"), ([40, 43, 48], "triad"), ([36, 40, 43, 46], "extended_chord"), ([36, 37], "other")])
def test_chord_structure_categories_without_key_guessing(tmp_path, pitches, label):
    source = analyze_guitar_midi(write_midi(tmp_path / "chord.mid", [(0, pitch, 1) for pitch in pitches]))
    assert source["chords"]["classification"] == label
    assert source["chords"]["root_or_key_inferred"] is False


def test_gallop_stop_go_and_tremolo_candidates(tmp_path):
    gallop = [(quarter + offset, 36, 0.1) for quarter in range(16) for offset in (0, 0.5, 0.75)]
    result = analyze_guitar_midi(write_midi(tmp_path / "gallop.mid", gallop))
    assert "gallop" in result["riff_characteristics"]
    assert result["rhythm"]["gallop_pattern_count"] >= 3
    stops = analyze_guitar_midi(write_midi(tmp_path / "stops.mid", repeated(step=2)))
    assert "stop_and_go" in stops["riff_characteristics"]
    tremolo = analyze_guitar_midi(write_midi(tmp_path / "fast.mid", repeated()))
    assert "tremolo_like" in tremolo["riff_characteristics"]
    assert tremolo["riff_evidence"]["tremolo_like"]["confidence"] is None


def test_four_bar_repetition_and_rhythm_only_period(tmp_path):
    notes = [(bar * 4 + offset, [36, 38, 41, 43][bar % 4], 0.25) for bar in range(8) for offset in (0, 2)]
    source = analyze_guitar_midi(write_midi(tmp_path / "repeat.mid", notes, end_beats=32))
    assert source["repetition"]["pitch_rhythm_period_bars"] == 4
    assert source["repetition"]["rhythm_period_bars"] == 1
    candidate = next(item for item in source["repetition"]["candidates"] if item["period_bars"] == 4)
    assert candidate["pitch_rhythm_exact_ratio"] == 1


def test_similar_repetition_is_distinct_from_exact(tmp_path):
    notes = [(bar * 4 + offset + (0.01 if bar >= 4 else 0), [36, 38, 41, 43][bar % 4], 0.25)
             for bar in range(8) for offset in (0, 2)]
    source = analyze_guitar_midi(write_midi(tmp_path / "similar.mid", notes, end_beats=32))
    candidate = next(item for item in source["repetition"]["candidates"] if item["period_bars"] == 4)
    assert candidate["pitch_rhythm_exact_ratio"] == 0
    assert candidate["pitch_rhythm_similar_ratio"] == 1


@pytest.mark.parametrize("tpb", [96, 384, 480, 960])
def test_resolution_determinism_and_no_source_changes(tmp_path, tpb):
    path = write_midi(tmp_path / "resolution.mid", repeated(), tpb=tpb)
    before = path.read_bytes()
    result = analyze_guitar_midi(path)
    assert result == analyze_guitar_midi(path)
    assert result["rhythm"]["subdivision"] == "sixteenth"
    assert result["note_duration"]["beats"]["median"] == pytest.approx(0.125)
    assert path.read_bytes() == before
    validate_source(result)


def test_tempo_and_meter_changes_affect_seconds_and_bars(tmp_path):
    path = write_midi(tmp_path / "changes.mid", [(1, 36, 3), (4, 43, 1)], end_beats=7,
                      tempos=[(0, 500000), (2, 1000000)], meters=[(0, 4, 4), (4, 3, 4)])
    source = analyze_guitar_midi(path)
    assert source["source_context"]["duration_seconds"] == pytest.approx(6)
    assert source["note_duration"]["seconds"]["maximum"] == pytest.approx(2.5)
    assert len(source["rhythm"]["per_bar"]) == 2
    assert source["rhythm"]["per_bar"][1]["time_signature"] == [3, 4]
    assert source["repetition"]["status"] == "unknown_across_meter_changes"


def test_compound_meter_uses_dotted_quarter_pulses(tmp_path):
    path = write_midi(tmp_path / "compound.mid", [(beat, 36, 0.25) for beat in (0, 1.5, 3, 4.5)],
                      end_beats=6, meters=[(0, 6, 8)])
    source = analyze_guitar_midi(path)
    assert source["rhythm"]["weak_accent_share"] == 0
    assert source["rhythm"]["syncopation"] == "low"


def test_note_release_can_be_in_another_track_on_same_channel(tmp_path):
    path = tmp_path / "cross_track.mid"
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    midi.tracks.extend([
        mido.MidiTrack([mido.Message("note_on", note=36, velocity=100), mido.MetaMessage("end_of_track", time=960)]),
        mido.MidiTrack([mido.Message("note_off", note=36, time=480), mido.MetaMessage("end_of_track", time=480)]),
    ])
    midi.save(path)
    result = analyze_guitar_midi(path)
    assert result["note_pairing"]["inferred_note_ends"] == 0
    assert result["note_pairing"]["unmatched_note_offs"] == 0
    assert result["note_duration"]["beats"]["median"] == 1


def test_same_tick_retrigger_zero_velocity_release_and_missing_pairs(tmp_path):
    path = tmp_path / "pairing.mid"
    midi = mido.MidiFile(type=0, ticks_per_beat=480)
    midi.tracks.append(mido.MidiTrack([
        mido.Message("note_on", note=36, velocity=100),
        mido.Message("note_off", note=36, time=480),
        mido.Message("note_on", note=36, velocity=100),
        mido.Message("note_on", note=36, velocity=0, time=480),
        mido.Message("note_on", note=40, velocity=100),
        mido.Message("note_off", note=40),
        mido.Message("note_off", note=45),
        mido.Message("note_on", note=41, velocity=100, time=240),
        mido.MetaMessage("end_of_track", time=720),
    ]))
    midi.save(path)
    before = path.read_bytes()
    source = analyze_guitar_midi(path)
    assert source["note_pairing"]["positive_note_ons"] == 4
    assert source["note_pairing"]["paired_note_count"] == 4
    assert source["note_pairing"]["unmatched_note_offs"] == 1
    assert source["note_pairing"]["inferred_note_ends"] == 1
    assert source["note_duration"]["beats"]["minimum"] == 0
    assert path.read_bytes() == before
    validate_source(source)


def test_multitrack_merging_and_channel_independent_instrument(tmp_path):
    notes = [(beat, pitch, 0.5, 100, index, index + 5) for beat in range(8) for index, pitch in enumerate([36, 43])]
    source = analyze_guitar_midi(write_midi(tmp_path / "tracks.mid", notes, tracks=2))
    assert source["source_context"]["track_count"] == 3
    assert source["source_context"]["actual_midi_channels"] == [6, 7]
    assert source["instrument"] == "guitar"
    assert source["chords"]["power_chord_ratio"] == 1


def test_possible_control_notes_retained_unless_explicitly_excluded(tmp_path):
    path = write_midi(tmp_path / "controls.mid", [(q, note, 0.25) for q in range(16) for note in (14, 36)])
    before = path.read_bytes()
    source = analyze_guitar_midi(path)
    assert source["pitch"]["min_note"] == 14
    assert source["possible_control_notes"][0]["note"] == 14
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"excluded_notes": [14]}))
    filtered = analyze_guitar_midi(path, config_path=config)
    assert filtered["pitch"]["min_note"] == 36
    assert filtered["polyphony"]["classification"] == "monophonic"
    assert filtered["note_pairing"]["excluded_note_counts"]["14"] == 16
    assert path.read_bytes() == before


def guitar_catalog(tmp_path):
    root = tmp_path / "project"
    source = root / "data/sources/guitar"
    write_midi(source / "eighth.mid", repeated(step=0.5))
    write_midi(source / "sixteenth.mid", repeated())
    write_midi(source / "sync.mid", [(q + offset, 36, 0.125) for q in range(16) for offset in (0.25, 0.75)])
    write_midi(root / "data/sources/drums/other/drums.mid", [(0, 36, 0.1, 100, 0, 9)])
    scan_midi_catalog(project_root=root)
    return root, root / "data/catalog/midi_catalog.json", source


def test_catalog_batch_schema_tags_ids_and_error_isolation(tmp_path):
    root, catalog, directory = guitar_catalog(tmp_path)
    (directory / "broken.mid").write_bytes(b"not MIDI")
    scan_midi_catalog(project_root=root)
    before = catalog.read_bytes()
    report = analyze_catalog_guitars(project_root=root)
    assert report["summary"]["guitar_total"] == 4
    assert report["summary"]["success_count"] == 3
    assert report["summary"]["error_count"] == 1
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/guitar_riff_analysis.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(report)
    json.dumps(report, allow_nan=False)
    assert catalog.read_bytes() == before
    got = get_guitar_riff_analysis("sixteenth.mid", analysis_path=root / "data/catalog/guitar_riff_analysis.json")
    assert got["source_id"] == "data/sources/guitar/sixteenth.mid"
    with pytest.raises(ValueError, match="separate"):
        analyze_catalog_guitars(project_root=root, output_path=catalog)


def test_guitar_search_exact_features_multiple_labels_and_stale_reports(tmp_path):
    root, catalog, directory = guitar_catalog(tmp_path)
    analyze_catalog_guitars(project_root=root)
    assert len(search_patterns(catalog_path=catalog, instrument="guitar")) == 3
    sixteenth = search_patterns(catalog_path=catalog, instrument="guitar", subdivision="sixteenth")
    assert {Path(item["file_path"]).name for item in sixteenth} == {"sixteenth.mid", "sync.mid"}
    high = search_guitar_riffs(catalog_path=catalog, syncopation="high")
    assert len(high) == 1 and high[0]["file_path"].endswith("sync.mid")
    chug = search_guitar_riffs(catalog_path=catalog, riff_characteristics=["chug", "single_note_riff"])
    assert len(chug) == 3
    assert search_patterns(catalog_path=catalog, instrument="drums")[0]["file_path"].endswith("drums.mid")
    path = directory / "sync.mid"
    midi = mido.MidiFile(path);midi.tracks[0].insert(0, mido.MetaMessage("text", text="changed"));midi.save(path)
    assert search_guitar_riffs(catalog_path=catalog, syncopation="high") == []
    with pytest.raises(ValueError, match="Unsupported riff"):
        search_guitar_riffs(catalog_path=catalog, riff_characteristics="invented")


def test_cli_guitar_analysis_and_multiple_riff_search(tmp_path, monkeypatch, capsys):
    root, catalog, _ = guitar_catalog(tmp_path)
    monkeypatch.setattr(sys, "argv", ["midi-generator", "catalog", "analyze-guitars", "--project-root", str(root)])
    main()
    assert json.loads(capsys.readouterr().out)["success_count"] == 3
    monkeypatch.setattr(sys, "argv", ["midi-generator", "search", "--instrument", "guitar", "--catalog", str(catalog),
                                     "--subdivision", "sixteenth", "--riff-characteristic", "chug", "--riff-characteristic", "single_note_riff"])
    main()
    results = json.loads(capsys.readouterr().out)
    assert len(results) == 2
    assert all("chug" in item["riff_characteristics"] for item in results)


def test_empty_source_and_no_guitar_generation(tmp_path):
    source = analyze_guitar_midi(write_midi(tmp_path / "empty.mid", [], end_beats=0))
    validate_source(source)
    assert source["riff_characteristics"] == ["unknown"]
    assert source["pitch"]["min_note"] is None
    assert source["rhythm"]["notes_per_second"] is None
    with pytest.raises(ValueError, match="drums.*only"):
        generate({"instrument": "guitar", "section": "intro", "tempo": 120, "bars": 4})
