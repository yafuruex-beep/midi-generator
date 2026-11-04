"""Reference motif retention, block phase continuity, and explainable metrics."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('groove_comparison', ROOT / 'scripts/run_groove_consistency_demo.py')
comparison = importlib.util.module_from_spec(spec); spec.loader.exec_module(comparison)
demo = comparison.demo


def config():
    return {'guitar': {'root': 52, 'program': 31, 'voicing': 'mixed', 'power_chord_ratio': .75,
                      'duration_steps': 1, 'rhythm_mode': 'phrase4', 'duration_mode': 'contrast'},
            'guitar_motif': {'phrase_bars': 8, 'blueprint': {'kick_steps': [0], 'snare_steps': [8], 'accent_steps': [0, 8]}}}


def test_accepted_structure_and_gate_motif_once_per_section():
    motif = demo.section_motif(config(), {})
    assert len(motif) == 8 and sum(map(len, motif)) == 60
    assert motif[:4] == motif[4:]
    assert [[s.step for s in bar] for bar in motif] == [
        [0, 2, 4, 6, 8, 10, 12, 14], [0, 2, 4, 6, 8, 10, 12, 15],
        [0, 2, 4, 6, 8, 10, 12, 14], [0, 2, 4, 6, 8, 13]] * 2
    assert [(i + 1, s.step) for i, bar in enumerate(motif) for s in bar if s.duration_steps == 2] == [
        (2, 12), (4, 8), (4, 13), (6, 12), (8, 8), (8, 13)]
    assert sum(s.voicing == 'power5' for bar in motif for s in bar) == 46


def test_phase_is_not_reset_at_four_bar_drum_boundaries():
    # Distinct sentinels expose phase bugs that a four-bar-periodic reference hides.
    motif = tuple(range(8))
    assert demo.motif_slice(motif, 0, 4) == (0, 1, 2, 3)
    assert demo.motif_slice(motif, 4, 4) == (4, 5, 6, 7)
    assert demo.motif_slice(motif, 8, 4) == (0, 1, 2, 3)
    with pytest.raises(ValueError):demo.motif_slice((), 0, 1)


def test_short_section_is_prefix_and_drum_input_does_not_mutate_motif():
    c = config(); before = deepcopy(c)
    a = demo.section_motif(c, {'drum_sources': ['one']})
    b = demo.section_motif(c, {'drum_sources': ['different']})
    assert a == b and c == before
    assert sum(map(len, demo.motif_slice(a, 0, 2))) == 16
    assert demo.section_motif({'guitar': c['guitar']}, {}) is None
    c['guitar_motif']['phrase_bars'] = True
    with pytest.raises(ValueError, match='phrase_bars'):demo.section_motif(c, {})


def test_major_rest_metric_detects_a_filled_reference_hole():
    motif = demo.section_motif(config(), {})
    events = [(i * 1920 + s.step * 120, i * 1920 + int((s.step + s.duration_steps) * 120), 52, s.velocity, 1)
              for i, bar in enumerate(motif) for s in bar]
    reference = comparison.local_bars(events, 0, 32 * 480)
    section = {'id': 'test', 'start_beat': 0, 'end_beat': 32}
    full = comparison.measure_section(events, [], reference, section, 0)
    assert full['major_rest_cells_expected'] == 6 and full['major_rest_retention'] == 1
    assert full['pair_similarities'] == {'bars1_2_vs3_4': 13/17, 'bars1_2_vs5_6': 1, 'bars1_2_vs7_8': 13/17}
    # Add one chug into each three-cell ending pause, without moving other events.
    altered = events + [(i * 1920 + 10 * 120, i * 1920 + 11 * 120, 52, 108, 1) for i in (3, 7)]
    bad = comparison.measure_section(altered, [], reference, section, 0)
    assert bad['major_rest_retention'] == 4/6 and bad['added_onsets'] == 2
    assert comparison.jaccard({0, 2}, {0, 3}) == 1/3
    partial = comparison.measure_section(events, [], reference, {'id': 'short', 'start_beat': 0, 'end_beat': 10}, 0)
    assert partial['pair_similarities']['bars1_2_vs3_4'] is None
    assert len(partial['bars'][-1]['active']) == 8


def test_opt_in_full_song_preserves_drums_boundaries_and_bass(tmp_path):
    path = ROOT / 'examples/requests/not_my_time_groove_consistency.json'
    c = json.loads(path.read_text())
    if not Path(c['reference_audio']).is_file() or not Path(c['master_catalog']).is_file():
        pytest.skip('Local reference assets are not distributed')
    legacy = deepcopy(c); del legacy['guitar_motif']
    old = tmp_path / 'old'; new = tmp_path / 'new'
    before = demo.build(legacy, old); after = demo.build(c, new)
    assert before['end_tick'] == after['end_tick'] == 51258
    assert (old / 'drums.mid').read_bytes() == (new / 'drums.mid').read_bytes()
    assert after['guitar_attack_events'] == 200 and after['half_beat_events'] == 19
    assert after['bass_root_follow_exact'] and after['bass_notes'] == [28]
    manifest = json.loads((new / 'generation_manifest.json').read_text())
    chorus = [b for b in manifest['blocks'] if b['section'] == 'chorus']
    assert [b['guitar_motif_bar_indices'] for b in chorus] == [[0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 2, 3]]
