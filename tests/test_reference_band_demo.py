"""Task harness clipping and phrase-to-band integration, without changing cores."""
import importlib.util
import json
from pathlib import Path

import mido
import pytest

spec = importlib.util.spec_from_file_location('reference_band_demo', Path(__file__).parents[1] / 'scripts/run_reference_band_demo.py')
demo = importlib.util.module_from_spec(spec); spec.loader.exec_module(demo)


def test_half_open_section_clip_preserves_velocity_and_closes_notes():
    source = [(0, 240, 52, 96, 1), (240, 720, 52, 100, 1), (480, 600, 59, 76, 1)]
    before = list(source)
    assert demo.clip_intervals(source, 480, 120) == [(120, 360, 52, 96, 1), (360, 600, 52, 100, 1)]
    assert source == before


def test_section_edge_off_precedes_same_pitch_retrigger(tmp_path):
    path = tmp_path / 'joined.mid'
    demo.write_part(path, 'Guitar', [(0, 120, 52, 96, 1), (120, 240, 52, 100, 1)], 480,
                    mido.bpm2tempo(105), markers=[(120, 'section')], program=31, channel=1)
    midi = mido.MidiFile(path); tick = 0; group = []
    for msg in midi.tracks[0]:
        tick += msg.time
        if tick == 120:group.append(msg.type)
    assert group == ['note_off', 'marker', 'note_on']
    with pytest.raises(FileExistsError):
        demo.write_part(path, 'Bass', [], 480, mido.bpm2tempo(105))


def test_band_demo_reproducible_and_half_bar_endpoint(tmp_path):
    config_path = Path('examples/requests/not_my_time_first_chorus.json')
    config = json.loads(config_path.read_text())
    if not Path(config['reference_audio']).exists() or not Path(config['master_catalog']).exists():
        pytest.skip('Local reference/audio/catalog assets are not distributed')
    first = tmp_path / 'first'; second = tmp_path / 'second'
    a = demo.build(config, first); b = demo.build(config, second)
    for name in ('guitar', 'bass', 'drums', 'band'):
        assert (first / f'{name}.mid').read_bytes() == (second / f'{name}.mid').read_bytes()
    assert a == b
    assert a['end_tick'] == 378 + 106 * 480
    assert a['bass_notes'] == [28] and a['bass_root_follow_exact']
    assert a['kick_matches_with_existing_tolerance'] == a['kick_events']
    band = mido.MidiFile(first / 'band.mid')
    for track in band.tracks:
        tick = 0; marker_ticks = {}
        for msg in track:
            tick += msg.time
            if msg.type == 'marker':marker_ticks[msg.text] = tick
        assert marker_ticks['chorus [user_supplied_boundary]'] == 378 + 58 * 480
        assert marker_ticks['End: user boundary 27.3'] == 378 + 106 * 480
    with pytest.raises(FileExistsError):demo.build(config, first)
