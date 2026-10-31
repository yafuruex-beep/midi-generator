import hashlib
import json
import sys
from pathlib import Path

import mido
import pytest

from midi_generator.api import generate
from midi_generator.storage.catalog import scan_midi_catalog
from midi_generator.storage.patterns import add_tags


TPB = 480
TICKS_PER_BAR = TPB * 4


def make_drum_midi(path, *, bars=1, notes=None, tempo=120, channel=9, name="Drums", meter=(4, 4)):
    if notes is None:
        notes = [(bar * TICKS_PER_BAR, 36 + bar % 4, 120) for bar in range(bars)]
    midi = mido.MidiFile(type=1, ticks_per_beat=TPB)
    conductor = mido.MidiTrack([
        mido.MetaMessage("track_name", name="Conductor", time=0),
        mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo), time=0),
        mido.MetaMessage("time_signature", numerator=meter[0], denominator=meter[1], time=0),
        mido.MetaMessage("end_of_track", time=bars * TICKS_PER_BAR),
    ])
    drum_track = mido.MidiTrack([mido.MetaMessage("track_name", name=name, time=0)])
    events = []
    for tick, note, duration in notes:
        events.extend([
            (tick, 1, mido.Message("note_on", note=note, velocity=100, channel=channel, time=0)),
            (tick + duration, 0, mido.Message("note_off", note=note, velocity=0, channel=channel, time=0)),
        ])
    events.sort(key=lambda event: (event[0], event[1]))
    previous_tick = 0
    for tick, _, message in events:
        drum_track.append(message.copy(time=tick - previous_tick))
        previous_tick = tick
    drum_track.append(mido.MetaMessage("end_of_track", time=max(0, bars * TICKS_PER_BAR - previous_tick)))
    midi.tracks.extend([conductor, drum_track])
    midi.save(path)
    return path


def make_generation_catalog(tmp_path, *, patterns=None):
    root = tmp_path / "project"
    sources = root / "data" / "sources"
    sources.mkdir(parents=True)
    if patterns is None:
        patterns = {
            "drums_a.mid": {"notes": [(0, 36, 120), (480, 38, 120)]},
            "drums_b.mid": {"notes": [(0, 42, 120), (480, 44, 120)]},
        }
    for filename, options in patterns.items():
        make_drum_midi(sources / filename, **options)
    scan_midi_catalog(project_root=root)
    catalog = root / "data" / "catalog" / "midi_catalog.json"
    for filename in patterns:
        add_tags(filename, instrument="drums", section="breakdown", style="metalcore,hardcore",
                 tags="half_time,double_kick", catalog_path=catalog)
    return root, catalog, sources


def request(*, bars=3, seed=42, **overrides):
    value = {
        "instrument": "drums",
        "section": "breakdown",
        "tempo": 100,
        "bars": bars,
        "time_signature": [4, 4],
        "style": ["metalcore", "hardcore"],
        "tags": ["half_time", "double_kick"],
        "seed": seed,
    }
    value.update(overrides)
    return value


def _note_messages(midi):
    return [message for track in midi.tracks for message in track if message.type in {"note_on", "note_off"}]


def test_generate_selects_ranked_source_sets_output_metadata_and_is_reproducible(tmp_path):
    root, catalog, sources = make_generation_catalog(tmp_path)
    source_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources.glob("*.mid")}

    first = generate(request(), catalog_path=catalog, project_root=root)
    second = generate(request(), catalog_path=catalog, project_root=root)

    assert first.metadata["catalog_file_id"] == second.metadata["catalog_file_id"]
    assert first.metadata["seed"] == 42
    assert first.metadata["selection_score_breakdown"]
    assert len(first.output_files) == 1
    assert first.output_files[0].exists()
    assert first.output_files[0].name == "drums_breakdown_100bpm_3bars.mid"
    assert second.output_files[0].stem.endswith("_1")
    assert Path(first.metadata["metadata_file"]).exists()
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources.glob("*.mid")} == source_hashes

    generated = mido.MidiFile(first.output_files[0])
    assert generated.type == 0
    assert generated.ticks_per_beat == TPB
    assert generated.tracks[0][0].type == "track_name"
    assert generated.tracks[0][0].name == "Drums"
    assert next(message for message in generated.tracks[0] if message.type == "set_tempo").tempo == mido.bpm2tempo(100)
    signature = next(message for message in generated.tracks[0] if message.type == "time_signature")
    assert (signature.numerator, signature.denominator) == (4, 4)
    assert sum(message.time for message in generated.tracks[0]) == 3 * TICKS_PER_BAR
    assert {message.channel for message in _note_messages(generated)} == {9}
    assert len([message for message in generated.tracks[0] if message.type == "note_on" and message.velocity > 0]) == 6
    assert json.loads(Path(first.metadata["metadata_file"]).read_text())["catalog_file_id"] == first.metadata["catalog_file_id"]


def test_short_pattern_loops_and_preserves_note_pairs_and_pitch(tmp_path):
    root, catalog, _ = make_generation_catalog(tmp_path)
    result = generate(request(bars=3), catalog_path=catalog, project_root=root)
    midi = mido.MidiFile(result.output_files[0])
    ons = [message for message in midi.tracks[0] if message.type == "note_on" and message.velocity > 0]
    offs = [message for message in midi.tracks[0] if message.type == "note_off"]

    assert result.metadata["processing"]["source_bars"] == 1
    assert result.metadata["processing"]["loop_count"] == 3
    assert len(ons) == len(offs) == 6
    assert set(message.note for message in ons) in ({36, 38}, {42, 44})


def test_long_pattern_trims_at_requested_bar_boundary(tmp_path):
    root, catalog, _ = make_generation_catalog(tmp_path, patterns={
        "long.mid": {"bars": 4, "notes": [(0, 36, 120), (1900, 38, 180), (2100, 40, 120), (6000, 42, 120)]},
    })
    result = generate(request(bars=1, tags=[]), catalog_path=catalog, project_root=root)
    midi = mido.MidiFile(result.output_files[0])
    ons = [message for message in midi.tracks[0] if message.type == "note_on" and message.velocity > 0]
    offs = [message for message in midi.tracks[0] if message.type == "note_off"]

    assert result.metadata["processing"]["source_bars"] == 4
    assert result.metadata["processing"]["trimmed"] is True
    assert [message.note for message in ons] == [36, 38]
    assert [message.note for message in offs] == [36, 38]
    assert any("requested end boundary" in warning for warning in result.warnings)


def test_midi_channel_can_be_configured_and_original_pitch_is_not_mapped(tmp_path):
    root, catalog, _ = make_generation_catalog(tmp_path)
    result = generate(request(bars=1, options={"midi_channel": 2}), catalog_path=catalog, project_root=root)
    midi = mido.MidiFile(result.output_files[0])
    messages = _note_messages(midi)

    assert {message.channel for message in messages} == {1}
    assert {message.note for message in messages} in ({36, 38}, {42, 44})


def test_no_matching_pattern_returns_clear_error_and_does_not_fake_content(tmp_path):
    root, catalog, _ = make_generation_catalog(tmp_path)
    with pytest.raises(ValueError, match="No catalog MIDI pattern matches"):
        generate(request(tags=["blast_beat"]), catalog_path=catalog, project_root=root)


def test_unidentifiable_multitrack_drum_source_is_not_guessed_from_pitch(tmp_path):
    root, catalog, sources = make_generation_catalog(tmp_path, patterns={"ambiguous.mid": {"channel": 0, "name": "Part A"}})
    source = sources / "ambiguous.mid"
    midi = mido.MidiFile(source)
    second = mido.MidiTrack([
        mido.MetaMessage("track_name", name="Part B", time=0),
        mido.Message("note_on", note=36, velocity=90, channel=0, time=0),
        mido.Message("note_off", note=36, velocity=0, channel=0, time=120),
    ])
    midi.tracks.append(second)
    midi.save(source)
    scan_midi_catalog(project_root=root)

    with pytest.raises(ValueError, match="not guessed from note pitches"):
        generate(request(bars=1, tags=[]), catalog_path=catalog, project_root=root)


def test_json_request_validation_and_future_option_fields(tmp_path):
    root, catalog, _ = make_generation_catalog(tmp_path)
    with pytest.raises(ValueError, match="Missing required"):
        generate({"instrument": "drums", "tempo": 100}, catalog_path=catalog, project_root=root)
    with pytest.raises(ValueError, match="time_signature"):
        generate(request(time_signature=[4]), catalog_path=catalog, project_root=root)

    result = generate(request(bars=1, tags=[], reference_artists=["future-reference"]),
                      catalog_path=catalog, project_root=root)
    assert result.metadata["request"]["options"]["reference_artists"] == ["future-reference"]


def test_existing_output_is_never_overwritten(tmp_path):
    root, catalog, _ = make_generation_catalog(tmp_path)
    requested_path = root / "output" / "same.mid"
    requested_path.parent.mkdir(parents=True)
    requested_path.write_bytes(b"keep existing file")

    result = generate(request(bars=1), output_path=requested_path, catalog_path=catalog, project_root=root)

    assert requested_path.read_bytes() == b"keep existing file"
    assert result.output_files[0] == requested_path.with_name("same_1.mid")


def test_mismatched_meter_does_not_silently_convert(tmp_path):
    root, catalog, _ = make_generation_catalog(tmp_path)
    with pytest.raises(ValueError, match="No catalog MIDI pattern matches"):
        generate(request(bars=1, tags=[], time_signature=[3, 4]), catalog_path=catalog, project_root=root)


def test_cli_generate_uses_the_shared_api(tmp_path, monkeypatch, capsys):
    root, catalog, _ = make_generation_catalog(tmp_path)
    from midi_generator.cli import main

    monkeypatch.setattr(sys, "argv", [
        "midi-generator", "generate", "--instrument", "drums", "--section", "breakdown",
        "--tempo", "100", "--bars", "1", "--style", "metalcore", "--tags", "half_time,double_kick",
        "--seed", "42", "--catalog", str(catalog), "--project-root", str(root),
    ])
    main()
    output = json.loads(capsys.readouterr().out)
    assert Path(output["output_files"][0]).exists()
    assert output["metadata"]["seed"] == 42

    config = tmp_path / "drum_request.json"
    config.write_text(json.dumps(request(bars=1)), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "midi-generator", "generate", "--config", str(config), "--catalog", str(catalog),
        "--project-root", str(root), "--output", "output/from_config.mid",
    ])
    main()
    config_output = json.loads(capsys.readouterr().out)
    assert Path(config_output["output_files"][0]).name == "from_config.mid"
