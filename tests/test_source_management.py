import hashlib
import json
from pathlib import Path
import sys

import mido

from midi_generator.api import (
    correct_source_channels,
    inspect_sources,
    list_sources,
    organize_sources,
    validate_source_channels,
)
from midi_generator.cli import _parse_series_genres, main
from midi_generator.storage.catalog import scan_midi_catalog
from midi_generator.storage.patterns import add_tags, get_tags
from midi_generator.storage.patterns import search_patterns


def _save_midi(path: Path, *, channels=(0,), names=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    names = names or ["Drums"] * len(channels)
    for index, (channel, name) in enumerate(zip(channels, names)):
        track = mido.MidiTrack([
            mido.MetaMessage("track_name", name=name, time=0),
            mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(100), time=0),
            mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0),
            mido.Message("note_on", note=36 + index, velocity=91 + index, channel=channel, time=13),
            mido.Message("note_off", note=36 + index, velocity=0, channel=channel, time=211),
            mido.MetaMessage("end_of_track", time=17),
        ])
        midi.tracks.append(track)
    midi.save(path)
    return path


def _channels(path: Path):
    midi = mido.MidiFile(path)
    return [message.channel + 1 for track in midi.tracks for message in track if hasattr(message, "channel")]


def _semantic_events(path: Path):
    midi = mido.MidiFile(path)
    return midi.ticks_per_beat, [[message.dict() for message in track] for track in midi.tracks]


def test_source_directories_provide_instrument_genre_and_channel_state(tmp_path):
    root = tmp_path / "project"
    source = _save_midi(root / "data/sources/drums/metalcore/kit.mid", channels=(0,))
    scan_midi_catalog(project_root=root)

    items = list_sources(project_root=root)
    assert len(items) == 1
    assert items[0]["instrument"] == "drums"
    assert items[0]["genre_directory"] == "metalcore"
    assert items[0]["actual_midi_channels"] == [1]
    assert items[0]["expected_midi_channel"] == 10
    assert items[0]["channel_status"] == "needs_correction"
    assert inspect_sources("drums", project_root=root)["source_count"] == 1
    assert validate_source_channels(project_root=root)["status_counts"]["needs_correction"] == 1
    assert source.exists()


def test_dry_run_does_not_change_source_and_apply_backs_up_and_calibrates(tmp_path):
    root = tmp_path / "project"
    source = _save_midi(root / "data/sources/drums/metalcore/kit.mid", channels=(0,))
    scan_midi_catalog(project_root=root)
    original_bytes = source.read_bytes()
    original_events = _semantic_events(source)

    preview = correct_source_channels(project_root=root)
    assert preview["dry_run"] is True
    assert preview["candidate_count"] == 1
    assert preview["candidates"][0]["before_channels"] == [1]
    assert preview["candidates"][0]["after_channels"] == [10]
    assert source.read_bytes() == original_bytes

    corrected = correct_source_channels(apply=True, project_root=root)
    backup_file = Path(corrected["backup_directory"]) / "data/sources/drums/metalcore/kit.mid"
    assert corrected["applied_count"] == 1
    assert backup_file.read_bytes() == original_bytes
    assert hashlib.sha256(backup_file.read_bytes()).digest() == hashlib.sha256(original_bytes).digest()
    assert _channels(source) == [10, 10]
    ticks, events = _semantic_events(source)
    assert ticks == original_events[0]
    original_note_events = [m for track in original_events[1] for m in track if m["type"] in {"note_on", "note_off"}]
    corrected_note_events = [m for track in events for m in track if m["type"] in {"note_on", "note_off"}]
    assert [(m["note"], m["velocity"], m["time"], m["type"]) for m in corrected_note_events] == [
        (m["note"], m["velocity"], m["time"], m["type"]) for m in original_note_events
    ]
    assert [m["name"] for track in events for m in track if m["type"] == "track_name"] == ["Drums"]
    assert [m["tempo"] for track in events for m in track if m["type"] == "set_tempo"] == [600000]
    assert [(m["numerator"], m["denominator"]) for track in events for m in track if m["type"] == "time_signature"] == [(4, 4)]
    record = json.loads((root / "data/catalog/midi_catalog.json").read_text())["files"][0]
    assert record["channel_status"] == "valid"
    assert record["actual_midi_channels"] == [10]


def test_guitar_channel_10_corrects_to_channel_1_and_mixed_is_skipped(tmp_path):
    root = tmp_path / "project"
    guitar = _save_midi(root / "data/sources/guitar/hardcore/guitar.mid", channels=(9,), names=["Guitar"])
    mixed = _save_midi(root / "data/sources/drums/metalcore/mixed.mid", channels=(0, 1), names=["Drums", "Guitar"])
    scan_midi_catalog(project_root=root)

    validation = validate_source_channels(project_root=root)
    assert validation["status_counts"]["mixed"] == 1
    assert validation["status_counts"]["needs_correction"] == 1
    result = correct_source_channels(apply=True, project_root=root)
    assert result["applied_count"] == 1
    assert _channels(guitar) == [1, 1]
    assert _channels(mixed) == [1, 1, 2, 2]
    assert result["skipped_mixed"] == 1


def test_multiple_channels_are_mixed_and_never_auto_corrected(tmp_path):
    root = tmp_path / "project"
    drums = _save_midi(root / "data/sources/drums/other/multi.mid", channels=(0, 1))
    scan_midi_catalog(project_root=root)
    before = drums.read_bytes()

    validation = validate_source_channels(project_root=root)
    assert validation["status_counts"]["mixed"] == 1
    assert validation["multi_channel_sources"][0]["actual_midi_channels"] == [1, 2]
    result = correct_source_channels(apply=True, project_root=root)
    assert result["applied_count"] == 0
    assert result["candidate_count"] == 0
    assert not result["backup_directory"]
    assert drums.read_bytes() == before


def test_validate_summary_groups_normal_correction_mixed_and_read_errors(tmp_path):
    root = tmp_path / "project"
    _save_midi(root / "data/sources/drums/metalcore/channel1.mid", channels=(0,))
    _save_midi(root / "data/sources/drums/metalcore/channel10.mid", channels=(9,))
    _save_midi(root / "data/sources/drums/metalcore/multi.mid", channels=(0, 1))
    broken = root / "data/sources/drums/metalcore/broken.mid"
    broken.write_bytes(b"broken MIDI")
    scan_midi_catalog(project_root=root)

    validation = validate_source_channels(project_root=root)
    summary = validation["instrument_summary"]["drums"]
    assert summary == {
        "total_files": 4,
        "normal_files": 1,
        "correction_needed_files": 1,
        "multi_channel_files": 1,
        "read_errors": 1,
    }
    assert validation["read_error_count"] == 1
    assert validation["status_counts"]["mixed"] == 1


def test_organize_sources_backs_up_moves_preserves_stable_ids_tags_and_catalog(tmp_path):
    root = tmp_path / "project"
    sources = root / "data/sources"
    guitar = _save_midi(sources / "lead.mid", channels=(0,), names=["Guitar"])
    unknown = _save_midi(sources / "mystery.midi", channels=(0,), names=["Track"])
    guitar_bytes, unknown_bytes = guitar.read_bytes(), unknown.read_bytes()
    scan_midi_catalog(project_root=root)
    add_tags("data/sources/lead.mid", instrument="guitar", style="metalcore", tags="manual_keep",
             catalog_path=root / "data/catalog/midi_catalog.json")
    before = json.loads((root / "data/catalog/midi_catalog.json").read_text())

    result = organize_sources(project_root=root)

    new_guitar = root / "data/sources/guitar/metalcore/lead.mid"
    new_unknown = root / "data/sources/unclassified/mystery.midi"
    assert result["moved_count"] == 2
    assert new_guitar.read_bytes() == guitar_bytes
    assert new_unknown.read_bytes() == unknown_bytes
    assert (Path(result["backup_directory"]) / "data/sources/lead.mid").read_bytes() == guitar_bytes
    assert (Path(result["backup_directory"]) / "data/sources/mystery.midi").read_bytes() == unknown_bytes
    after = json.loads((root / "data/catalog/midi_catalog.json").read_text())
    moved = {item["path"]: item for item in after["files"]}
    guitar_record = moved["data/sources/guitar/metalcore/lead.mid"]
    unknown_record = moved["data/sources/unclassified/mystery.midi"]
    assert guitar_record["file_id"] == "data/sources/lead.mid"
    assert guitar_record["instrument"] == "guitar"
    assert guitar_record["genre_directory"] == "metalcore"
    assert guitar_record["tags"] == next(item["tags"] for item in before["files"] if item["path"].endswith("lead.mid"))
    assert "manual_keep" in get_tags("data/sources/lead.mid", catalog_path=root / "data/catalog/midi_catalog.json")["tags"]["custom"]
    assert unknown_record["instrument"] is None
    assert unknown_record["channel_status"] == "unknown"
    assert len(list_sources(project_root=root)) == 2


def test_directory_instrument_is_used_for_search_without_creating_manual_tag(tmp_path):
    root = tmp_path / "project"
    _save_midi(root / "data/sources/drums/metalcore/kit.mid", channels=(9,))
    scan_midi_catalog(project_root=root)

    results = search_patterns(catalog_path=root / "data/catalog/midi_catalog.json", instrument="drums")
    assert len(results) == 1
    assert results[0]["instrument"] == ["drums"]
    record = json.loads((root / "data/catalog/midi_catalog.json").read_text())["files"][0]
    assert record["tags"]["instrument"] == []


def test_source_cli_correct_channels_defaults_to_preview(tmp_path, monkeypatch, capsys):
    root = tmp_path / "project"
    source = _save_midi(root / "data/sources/drums/metalcore/kit.mid", channels=(0,))
    scan_midi_catalog(project_root=root)
    before = source.read_bytes()
    monkeypatch.setattr(sys, "argv", [
        "midi-generator", "source", "correct-channels", "--project-root", str(root), "--dry-run",
    ])

    main()

    output = json.loads(capsys.readouterr().out)
    assert output["dry_run"] is True
    assert output["candidate_count"] == 1
    assert source.read_bytes() == before


def test_cli_validation_prints_instrument_totals(tmp_path, monkeypatch, capsys):
    root = tmp_path / "project"
    _save_midi(root / "data/sources/drums/metalcore/channel1.mid", channels=(0,))
    _save_midi(root / "data/sources/drums/metalcore/channel10.mid", channels=(9,))
    scan_midi_catalog(project_root=root)
    monkeypatch.setattr(sys, "argv", ["midi-generator", "source", "validate-channels", "--project-root", str(root)])

    main()

    output = json.loads(capsys.readouterr().out)
    assert output["instrument_summary"]["drums"]["total_files"] == 2
    assert output["instrument_summary"]["drums"]["normal_files"] == 1
    assert output["instrument_summary"]["drums"]["correction_needed_files"] == 1


def test_apply_records_failure_without_changing_source(tmp_path, monkeypatch):
    root = tmp_path / "project"
    source = _save_midi(root / "data/sources/drums/metalcore/channel1.mid", channels=(0,))
    scan_midi_catalog(project_root=root)
    before = source.read_bytes()

    def fail_conversion(*args, **kwargs):
        raise OSError("simulated conversion failure")

    monkeypatch.setattr("midi_generator.storage.sources.convert_midi_channel", fail_conversion)
    result = correct_source_channels(apply=True, project_root=root)

    assert result["applied_count"] == 0
    assert result["error_count"] == 1
    assert result["errors"][0]["error"]["message"] == "simulated conversion failure"
    assert source.read_bytes() == before
    assert Path(result["backup_directory"], "data/sources/drums/metalcore/channel1.mid").read_bytes() == before


def test_user_verified_unclassified_instrument_override_migrates_without_manual_tags(tmp_path):
    root = tmp_path / "project"
    source = _save_midi(root / "data/sources/unclassified/confirmed.mid", channels=(0,))
    scan_midi_catalog(project_root=root)
    original = source.read_bytes()

    result = organize_sources(project_root=root, instrument="drums")

    target = root / "data/sources/drums/other/confirmed.mid"
    assert result["moved_count"] == 1
    assert target.read_bytes() == original
    backup = Path(result["backup_directory"]) / "data/sources/unclassified/confirmed.mid"
    assert backup.read_bytes() == original
    record = json.loads((root / "data/catalog/midi_catalog.json").read_text())["files"][0]
    assert record["instrument"] == "drums"
    assert record["genre_directory"] == "other"
    assert record["channel_status"] == "needs_correction"
    assert record["tags"]["instrument"] == []


def test_series_genre_reorganization_preserves_midi_and_catalog_ids(tmp_path):
    root = tmp_path / "project"
    first = _save_midi(root / "data/sources/drums/other/RoseNuMetal-S01-G01.mid", channels=(9,))
    second = _save_midi(root / "data/sources/drums/other/RoseNuMetal-S02-G01.mid", channels=(9,))
    before = {path.name: path.read_bytes() for path in (first, second)}
    scan_midi_catalog(project_root=root)

    result = organize_sources(
        project_root=root,
        instrument="drums",
        genre_by_series={"S01": "metalcore", "S02": "hardcore"},
        genre_source="automatic",
        genre_method="tempo_threshold",
        genre_parameters={"threshold_bpm": 160},
    )

    metalcore = root / "data/sources/drums/metalcore/RoseNuMetal-S01-G01.mid"
    hardcore = root / "data/sources/drums/hardcore/RoseNuMetal-S02-G01.mid"
    assert result["moved_count"] == 2
    assert metalcore.read_bytes() == before[metalcore.name]
    assert hardcore.read_bytes() == before[hardcore.name]
    catalog = json.loads((root / "data/catalog/midi_catalog.json").read_text())
    records = {item["path"]: item for item in catalog["files"]}
    assert records["data/sources/drums/metalcore/RoseNuMetal-S01-G01.mid"]["file_id"] == "data/sources/drums/other/RoseNuMetal-S01-G01.mid"
    assert records["data/sources/drums/hardcore/RoseNuMetal-S02-G01.mid"]["genre_directory"] == "hardcore"
    assert records["data/sources/drums/metalcore/RoseNuMetal-S01-G01.mid"]["classification_metadata"]["genre"]["source"] == "automatic"
    scan_midi_catalog(project_root=root)
    rescanned = json.loads((root / "data/catalog/midi_catalog.json").read_text())
    rescanned_records = {item["path"]: item for item in rescanned["files"]}
    assert rescanned_records["data/sources/drums/metalcore/RoseNuMetal-S01-G01.mid"]["classification_metadata"]["genre"]["source"] == "automatic"
    assert _parse_series_genres("S01-S05:metalcore,S06-S15:hardcore")["S06"] == "hardcore"

