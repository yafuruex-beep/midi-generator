import json
import sys

import mido

from midi_generator.storage.catalog import scan_midi_catalog
from midi_generator.storage.patterns import (
    add_tags,
    get_tags,
    remove_tags,
    search_patterns,
    update_tags,
)


def make_midi(path, bpm=100, notes=(36, 48, 40, 43)):
    midi = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.extend([
        mido.MetaMessage("track_name", name="Pattern", time=0),
        mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0),
        mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0),
    ])
    for note in notes:
        track.append(mido.Message("note_on", note=note, velocity=90, time=0))
        track.append(mido.Message("note_off", note=note, velocity=0, time=120))
    midi.save(path)


def make_catalog(tmp_path):
    root = tmp_path / "project"
    sources = root / "data" / "sources"
    sources.mkdir(parents=True)
    make_midi(sources / "01_drums.mid", bpm=100)
    make_midi(sources / "02_drums.mid", bpm=108, notes=(36, 42, 48))
    make_midi(sources / "03_guitar.mid", bpm=100, notes=(40, 52, 64))
    make_midi(sources / "04_untagged.mid", bpm=90, notes=(38, 41))
    catalog_path = root / "data" / "catalog" / "midi_catalog.json"
    scan_midi_catalog(project_root=root)
    return root, catalog_path


def test_add_get_update_remove_tags_and_deduplicate(tmp_path):
    _, catalog = make_catalog(tmp_path)
    file_id = "data/sources/01_drums.mid"

    added = add_tags(file_id, instrument="drums", section="breakdown", style="metalcore",
                     tags=["half_time", "double_kick", "half_time"], confidence=0.95,
                     catalog_path=catalog)
    assert added["tags"] == {
        "instrument": ["drums"], "section": ["breakdown"], "style": ["metalcore"],
        "custom": ["half_time", "double_kick"],
    }
    assert {item["source"] for item in added["tag_metadata"]} == {"manual"}
    assert all(item["confidence"] == 0.95 for item in added["tag_metadata"])
    assert get_tags(file_id, catalog_path=catalog)["tags"] == added["tags"]

    updated = update_tags(file_id, section="verse", tags="chug", catalog_path=catalog)
    assert updated["tags"]["instrument"] == ["drums"]
    assert updated["tags"]["section"] == ["verse"]
    assert updated["tags"]["custom"] == ["chug"]
    assert updated["tags"]["style"] == ["metalcore"]

    removed = remove_tags(file_id, section="verse", tags="chug", catalog_path=catalog)
    assert removed["tags"]["section"] == []
    assert removed["tags"]["custom"] == []
    assert removed["tags"]["instrument"] == ["drums"]


def test_search_with_no_tags_and_basic_filters(tmp_path):
    _, catalog = make_catalog(tmp_path)

    all_results = search_patterns(catalog_path=catalog)
    assert len(all_results) == 4
    assert all_results[0]["tags"] == {"instrument": [], "section": [], "style": [], "custom": []}

    add_tags("01_drums.mid", instrument="drums", section="breakdown", catalog_path=catalog)
    add_tags("02_drums.mid", instrument="drums", section="verse", catalog_path=catalog)
    add_tags("03_guitar.mid", instrument="guitar", section="breakdown", catalog_path=catalog)
    by_instrument = search_patterns(catalog_path=catalog, instrument="drums")
    assert [item["file_path"] for item in by_instrument] == [
        "data/sources/01_drums.mid", "data/sources/02_drums.mid",
    ]
    by_section = search_patterns(catalog_path=catalog, section="breakdown")
    assert {item["file_path"] for item in by_section} == {
        "data/sources/01_drums.mid", "data/sources/03_guitar.mid",
    }


def test_bpm_range_multi_condition_soft_tag_scoring_and_stable_order(tmp_path):
    _, catalog = make_catalog(tmp_path)
    add_tags("01_drums.mid", instrument="drums", section="breakdown", style="metalcore",
             tags="half_time,double_kick", catalog_path=catalog)
    add_tags("02_drums.mid", instrument="drums", section="breakdown", style="metalcore",
             tags="half_time", catalog_path=catalog)
    add_tags("03_guitar.mid", instrument="guitar", section="verse", style="metalcore",
             tags="half_time,double_kick", catalog_path=catalog)

    results = search_patterns(catalog_path=catalog, instrument="drums", section="breakdown",
                              style="metalcore", bpm_min=90, bpm_max=110,
                              tags="half_time,double_kick")
    assert [item["file_path"] for item in results] == [
        "data/sources/01_drums.mid", "data/sources/02_drums.mid",
    ]
    assert results[0]["bpm"] == 100
    assert results[0]["score"] > results[1]["score"]
    assert results[0]["score_breakdown"]["bpm"]["match"] == 1
    assert results[1]["score_breakdown"]["tags"]["match"] == 0.5
    assert search_patterns(catalog_path=catalog, tags="nonexistent") == []
    assert search_patterns(catalog_path=catalog, instrument="drums", section="breakdown",
                           style="metalcore", bpm_min=90, bpm_max=110,
                           tags="half_time,double_kick") == results
    assert search_patterns(catalog_path=catalog, required_tags="double_kick",
                           bpm_min=90, bpm_max=110)[0]["file_path"] == "data/sources/01_drums.mid"


def test_search_supports_meter_bars_notes_and_pitch_range(tmp_path):
    _, catalog = make_catalog(tmp_path)
    result = search_patterns(catalog_path=catalog, time_signature="4/4", bars_min=0.2,
                             bars_max=0.3, notes_min=4, notes_max=4,
                             pitch_min=40, pitch_max=48)
    assert [item["file_path"] for item in result] == ["data/sources/01_drums.mid"]
    assert result[0]["time_signature"] == "4/4"
    assert result[0]["note_count"] == 4
    assert result[0]["pitch_range"] == {"minimum": 36, "maximum": 48}


def test_rescan_preserves_manual_tags_and_legacy_catalog_without_tag_field(tmp_path):
    root, catalog = make_catalog(tmp_path)
    records = json.loads(catalog.read_text())
    target = next(item for item in records["files"] if item["path"].endswith("01_drums.mid"))
    target.pop("tags")
    target.pop("tag_metadata")
    catalog.write_text(json.dumps(records))
    add_tags("01_drums.mid", instrument="drums", section="breakdown", tags="half_time", catalog_path=catalog)

    scan_midi_catalog(project_root=root)
    after = get_tags("01_drums.mid", catalog_path=catalog)
    assert after["tags"]["instrument"] == ["drums"]
    assert after["tags"]["section"] == ["breakdown"]
    assert after["tags"]["custom"] == ["half_time"]
    assert {item["source"] for item in after["tag_metadata"]} == {"manual"}


def test_cli_tag_and_search_commands_use_catalog_core(tmp_path, monkeypatch, capsys):
    _, catalog = make_catalog(tmp_path)
    from midi_generator.cli import main

    monkeypatch.setattr(sys, "argv", [
        "midi-generator", "tag", "add", "01_drums.mid", "--instrument", "drums",
        "--section", "breakdown", "--tags", "half_time", "--catalog", str(catalog),
    ])
    main()
    tag_output = json.loads(capsys.readouterr().out)
    assert tag_output["tags"]["custom"] == ["half_time"]

    monkeypatch.setattr(sys, "argv", [
        "midi-generator", "search", "--instrument", "drums", "--section", "breakdown",
        "--tags", "half_time", "--catalog", str(catalog),
    ])
    main()
    search_output = json.loads(capsys.readouterr().out)
    assert [item["file_path"] for item in search_output] == ["data/sources/01_drums.mid"]
