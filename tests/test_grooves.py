import hashlib
import json
from pathlib import Path
import sys

import mido
import pytest

from midi_generator.api import (
    analyze_catalog_grooves, analyze_drum_groove, generate,
    get_drum_groove_analysis, search_patterns,
)
from midi_generator.cli import main
from midi_generator.storage.catalog import scan_midi_catalog
from midi_generator.storage.patterns import add_tags


def make_groove(path, *, hats="eighth", kick="low", backbeat="standard", tpb=480, jitter=False, bars=4, extra=None, meter=(4, 4)):
    path.parent.mkdir(parents=True, exist_ok=True)
    quarter_count = bars * 4
    events = []
    offsets = {"quarter": [0], "eighth": [0, 0.5], "sixteenth": [0, 0.25, 0.5, 0.75], "swung": [0, 2 / 3]}
    for quarter in range(quarter_count):
        events.extend((quarter + offset, 42, 90) for offset in offsets[hats])
    if kick == "high":
        events.extend((quarter + offset, 36, 110) for quarter in range(quarter_count) for offset in [0.25, 0.75])
    elif kick == "roll":
        events.extend((step / 4, 36, 110) for step in range(quarter_count * 4))
    elif kick == "low":
        events.extend((bar * 4 + offset, 36, 110) for bar in range(bars) for offset in [0, 2])
    positions = {"standard": [1, 3], "half_time": [2], "double_time": [0.5, 1.5, 2.5, 3.5], "none": []}[backbeat]
    events.extend((bar * 4 + offset, 38, 115) for bar in range(bars) for offset in positions)
    events.extend(extra or [])
    midi = mido.MidiFile(type=0, ticks_per_beat=tpb)
    track = mido.MidiTrack([
        mido.MetaMessage("track_name", name="Drums"),
        mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(160)),
        mido.MetaMessage("time_signature", numerator=meter[0], denominator=meter[1]),
    ])
    timed = []
    for index, (beat, note, velocity) in enumerate(events):
        if jitter:
            beat = max(0, beat + (0.008 if index % 2 else -0.008))
        tick = round(beat * tpb)
        timed.append((tick, 1, mido.Message("note_on", note=note, velocity=velocity, channel=9)))
        timed.append((tick + max(1, round(tpb * 0.08)), 0, mido.Message("note_off", note=note, velocity=0, channel=9)))
    previous = 0
    for tick, _, message in sorted(timed, key=lambda value: (value[0], value[1])):
        track.append(message.copy(time=tick - previous))
        previous = tick
    track.append(mido.MetaMessage("end_of_track", time=max(0, quarter_count * tpb - previous)))
    midi.tracks.append(track)
    midi.save(path)
    return path


@pytest.mark.parametrize("hats,expected", [("eighth", "eighth"), ("sixteenth", "sixteenth")])
def test_subdivision_and_straight_grid(tmp_path, hats, expected):
    result = analyze_drum_groove(make_groove(tmp_path / "groove.mid", hats=hats))
    assert result["groove"]["subdivision"] == expected
    assert result["groove"]["timing_feel"] == "straight"
    assert result["groove"]["tightness_score"] == 1


def test_one_sixteenth_onset_does_not_reclassify_an_eighth_pattern(tmp_path):
    path = make_groove(tmp_path / "one.mid", extra=[(0.25, 36, 110)])
    result = analyze_drum_groove(path)
    assert result["metrics"]["sixteenth_off_count"] == 1
    assert result["groove"]["subdivision"] == "eighth"


def test_microtiming_is_straight_and_repeated_swing_is_distinguished(tmp_path):
    straight = analyze_drum_groove(make_groove(tmp_path / "tight.mid", hats="sixteenth", jitter=True))
    swung = analyze_drum_groove(make_groove(tmp_path / "swing.mid", hats="swung"))
    assert straight["groove"]["timing_feel"] == "straight"
    assert straight["groove"]["tightness_score"] > 0.9
    assert swung["groove"]["timing_feel"] == "swung"
    assert swung["metrics"]["swing"]["pairs"] >= 4
    assert swung["groove"]["tightness_score"] < straight["groove"]["tightness_score"]


def test_syncopation_is_not_just_kick_density(tmp_path):
    low = analyze_drum_groove(make_groove(tmp_path / "low.mid"))
    high = analyze_drum_groove(make_groove(tmp_path / "high.mid", kick="high"))
    dense = analyze_drum_groove(make_groove(tmp_path / "dense.mid", kick="roll"))
    assert low["groove"]["syncopation"] == "low"
    assert high["groove"]["syncopation"] == "high"
    assert dense["metrics"]["role_note_counts"]["kick"] > high["metrics"]["role_note_counts"]["kick"]
    assert dense["metrics"]["syncopation_score"] < high["metrics"]["syncopation_score"]


def test_local_triplet_fill_does_not_make_the_whole_pattern_swung(tmp_path):
    path = make_groove(tmp_path / "fill.mid", hats="quarter", extra=[(q + 1 / 3, 36, 100) for q in range(4)])
    result = analyze_drum_groove(path)
    assert result["groove"]["timing_feel"] != "swung"
    assert not result["metrics"]["swing"]["detected"]


def test_analysis_thresholds_can_be_overridden(tmp_path):
    path = make_groove(tmp_path / "configurable.mid", hats="sixteenth")
    config = tmp_path / "thresholds.json"
    config.write_text(json.dumps({"thresholds": {"sixteenth_min_count": 999}}))
    assert analyze_drum_groove(path)["groove"]["subdivision"] == "sixteenth"
    assert analyze_drum_groove(path, config_path=config)["groove"]["subdivision"] == "mixed"


@pytest.mark.parametrize("backbeat", ["standard", "half_time", "double_time"])
def test_snare_backbeat_positions(tmp_path, backbeat):
    result = analyze_drum_groove(make_groove(tmp_path / "snare.mid", backbeat=backbeat))
    assert result["groove"]["backbeat"] == backbeat


def test_double_kick_runs_and_role_density(tmp_path):
    basic = analyze_drum_groove(make_groove(tmp_path / "basic.mid"))
    heavy = analyze_drum_groove(make_groove(tmp_path / "heavy.mid", hats="sixteenth", kick="roll"))
    assert basic["groove"]["double_kick"] == "none"
    assert heavy["groove"]["double_kick"] == "heavy"
    assert heavy["metrics"]["double_kick_longest_run"] == 64
    assert heavy["metrics"]["double_kick_run_note_share"] == 1
    assert basic["metrics"]["role_notes_per_quarter"]["hihat"] == 2
    assert basic["metrics"]["role_notes_per_quarter"]["snare"] == 0.5
    assert heavy["groove"]["density"] == "high"
    assert basic["groove"]["density"] == "medium"


@pytest.mark.parametrize("tpb", [96, 480, 960])
def test_tick_resolution_invariance_read_only_and_determinism(tmp_path, tpb):
    path = make_groove(tmp_path / "resolution.mid", hats="sixteenth", kick="high", tpb=tpb)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    first = analyze_drum_groove(path)
    assert first == analyze_drum_groove(path)
    assert first["groove"]["subdivision"] == "sixteenth"
    assert first["groove"]["syncopation"] == "high"
    assert 0 <= first["groove"]["tightness_score"] <= 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_unknown_roles_ignored_notes_and_custom_drum_map(tmp_path):
    path = tmp_path / "unknown.mid"
    midi = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    for _ in range(16):
        track.extend([mido.Message("note_on", note=90, velocity=100), mido.Message("note_off", note=90, time=480)])
    midi.tracks.append(track)
    midi.save(path)
    unknown = analyze_drum_groove(path)
    assert unknown["groove"]["syncopation"] == "unknown"
    assert unknown["groove"]["backbeat"] == "unknown"
    assert unknown["groove"]["tightness_score"] is None
    assert unknown["metrics"]["outside_gm_notes"] == [90]
    config = tmp_path / "custom.json"
    config.write_text(json.dumps({"drum_map": {"kick": [90]}}))
    custom = analyze_drum_groove(path, config_path=config)
    assert custom["metrics"]["role_note_counts"]["kick"] == 16
    assert custom["metrics"]["ignored_note_counts"] == []


def test_other_meter_keeps_backbeat_unknown_and_sparse_timing_unknown(tmp_path):
    result = analyze_drum_groove(make_groove(tmp_path / "meter.mid", meter=(6, 8)))
    assert result["groove"]["backbeat"] == "unknown"
    sparse = make_groove(tmp_path / "sparse.mid", bars=0, kick="none", backbeat="none", extra=[(0, 36, 100)])
    assert analyze_drum_groove(sparse)["groove"]["timing_feel"] == "unknown"


def catalog_fixture(tmp_path):
    root = tmp_path / "project"
    source = root / "data/sources/drums/metalcore"
    make_groove(source / "a_eighth.mid")
    make_groove(source / "b_sixteenth.mid", hats="sixteenth", kick="high")
    make_groove(source / "c_bridge.mid", hats="sixteenth", kick="high")
    scan_midi_catalog(project_root=root)
    catalog = root / "data/catalog/midi_catalog.json"
    for name in ("a_eighth.mid", "b_sixteenth.mid", "c_bridge.mid"):
        add_tags(name, section="bridge" if name.startswith("c") else "intro", tags="keep_me", catalog_path=catalog)
    return root, catalog, source


def test_batch_report_error_isolation_and_catalog_tags_preserved(tmp_path):
    root, catalog, sources = catalog_fixture(tmp_path)
    (sources / "broken.mid").write_bytes(b"not MIDI")
    scan_midi_catalog(project_root=root)
    before = catalog.read_bytes()
    report = analyze_catalog_grooves(project_root=root)
    assert report["target_count"] == 4
    assert report["success_count"] == 3
    assert report["error_count"] == 1
    assert report["classification_counts"]["timing_feel"]["straight"] == 3
    assert catalog.read_bytes() == before
    entry = get_drum_groove_analysis("b_sixteenth.mid", analysis_path=root / "data/catalog/drum_groove_analysis.json")
    assert entry["groove"]["subdivision"] == "sixteenth"
    with pytest.raises(ValueError, match="separate"):
        analyze_catalog_grooves(project_root=root, output_path=catalog)


def test_soft_search_ranking_near_matches_and_mandatory_filters(tmp_path):
    root, catalog, sources = catalog_fixture(tmp_path)
    analyze_catalog_grooves(project_root=root)
    first = search_patterns(catalog_path=catalog, instrument="drums", section="intro", subdivision="sixteenth", timing_feel="straight", syncopation="high")
    assert len(first) == 2
    assert first[0]["file_path"].endswith("b_sixteenth.mid")
    assert first[0]["groove_match_score"] == 100
    assert first[1]["groove_selection"]["differences"]
    assert not any(item["file_path"].endswith("c_bridge.mid") for item in first)
    eighth = search_patterns(catalog_path=catalog, instrument="drums", section="intro", subdivision="eighth", syncopation="low")
    assert eighth[0]["file_path"].endswith("a_eighth.mid")
    assert search_patterns(catalog_path=catalog, required_tags="nonexistent", subdivision="sixteenth") == []
    assert search_patterns(catalog_path=catalog, section="intro", subdivision="sixteenth", timing_feel="straight", syncopation="high", instrument="drums") == first
    # Changing the source makes the stored evidence stale, without silently reanalyzing.
    path = sources / "b_sixteenth.mid"
    midi = mido.MidiFile(path)
    midi.tracks[0].insert(0, mido.MetaMessage("text", text="modified source"))
    midi.save(path)
    stale = search_patterns(catalog_path=catalog, subdivision="sixteenth")
    assert next(item for item in stale if item["file_path"].endswith("b_sixteenth.mid"))["groove_selection"]["analysis_status"] == "stale"


def test_groove_generation_reproducibility_and_missing_section_errors(tmp_path):
    root, catalog, sources = catalog_fixture(tmp_path)
    analyze_catalog_grooves(project_root=root)
    before = {p.name: p.read_bytes() for p in sources.glob("*.mid")}
    request = {"instrument": "drums", "section": "intro", "tempo": 160, "bars": 4, "seed": 42,
               "groove": {"subdivision": "sixteenth", "timing_feel": "straight", "syncopation": "high"}}
    a = generate(request, project_root=root, catalog_path=catalog)
    b = generate(request, project_root=root, catalog_path=catalog)
    assert a.output_files[0].read_bytes() == b.output_files[0].read_bytes()
    assert a.metadata["request"]["groove"] == request["groove"]
    assert a.metadata["groove_selection"]["reasons"]
    assert a.metadata["groove_selection"] == b.metadata["groove_selection"]
    assert {p.name: p.read_bytes() for p in sources.glob("*.mid")} == before
    # Soft preferences remain soft, but section and required tags remain mandatory.
    approximate = generate({**request, "section": "bridge", "groove": {"subdivision": "eighth"}}, project_root=root, catalog_path=catalog)
    assert approximate.metadata["groove_selection"]["differences"]
    with pytest.raises(ValueError, match="No catalog MIDI pattern matches"):
        generate({**request, "section": "verse"}, project_root=root, catalog_path=catalog)
    with pytest.raises(ValueError, match="No catalog MIDI pattern matches"):
        generate({**request, "tags": ["absent"]}, project_root=root, catalog_path=catalog)
    with pytest.raises(ValueError, match="Unsupported groove preference"):
        generate({**request, "groove": {"timing_feel": "invented"}}, project_root=root, catalog_path=catalog)


def test_missing_report_does_not_claim_a_groove_match_or_generate_blindly(tmp_path):
    root, catalog, _ = catalog_fixture(tmp_path)
    results = search_patterns(catalog_path=catalog, subdivision="sixteenth")
    assert all(item["groove_selection"]["analysis_status"] == "missing" for item in results)
    assert all(item["groove"] is None for item in results)
    with pytest.raises(ValueError, match="No usable groove analysis"):
        generate({"instrument": "drums", "section": "intro", "tempo": 160, "bars": 4,
                  "groove": {"subdivision": "sixteenth"}}, project_root=root, catalog_path=catalog)


def test_cli_analysis_search_and_generation_use_shared_groove_logic(tmp_path, monkeypatch, capsys):
    root, catalog, _ = catalog_fixture(tmp_path)
    monkeypatch.setattr(sys, "argv", ["midi-generator", "catalog", "analyze-grooves", "--project-root", str(root)])
    main()
    assert json.loads(capsys.readouterr().out)["success_count"] == 3
    monkeypatch.setattr(sys, "argv", ["midi-generator", "search", "--catalog", str(catalog), "--subdivision", "sixteenth", "--syncopation", "high"])
    main()
    assert json.loads(capsys.readouterr().out)[0]["groove_match_score"] == 100
    monkeypatch.setattr(sys, "argv", ["midi-generator", "generate", "--catalog", str(catalog), "--project-root", str(root),
                        "--instrument", "drums", "--section", "intro", "--tempo", "160", "--bars", "4", "--seed", "42",
                        "--subdivision", "sixteenth", "--timing-feel", "straight", "--syncopation", "high"])
    main()
    output = json.loads(capsys.readouterr().out)
    assert output["metadata"]["groove_selection"]["requested"]["syncopation"] == "high"
