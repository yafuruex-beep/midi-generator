"""Opt-in deterministic candidates; accepted motifs and defaults stay unchanged."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from midi_generator.api import (BackingRules, BackingStep, RhythmBlueprint,
                                develop_backing_motif, generate_backing_phrase)


def motif():
    return generate_backing_phrase(RhythmBlueprint((0,), (8,), (0, 8)), note=52,
        rules=BackingRules(rhythm_mode='phrase4', voicing='mixed', power_chord_ratio=.75, duration_steps=1),
        duration_mode='contrast')


def test_default_identity_and_existing_generator_unchanged():
    current = motif(); snapshot = deepcopy(current)
    assert develop_backing_motif(current) == current
    assert develop_backing_motif(current, mode='current') == current
    assert current == snapshot == motif()


def test_repetition_only_shortens_bars_four_eight_before_pause():
    current = motif(); variant = develop_backing_motif(current, mode='repetition')
    changes = [(i + 1, a.step, a.duration_steps, b.duration_steps)
               for i, (old, new) in enumerate(zip(current, variant)) for a, b in zip(old, new) if a != b]
    assert changes == [(4, 8, 2, 1), (8, 8, 2, 1)]
    assert [len(bar) for bar in variant] == [len(bar) for bar in current]
    assert variant[:4] == variant[4:]
    assert variant[:2] == current[:2] and variant[4:6] == current[:2]


def test_call_response_limited_removals_return_and_ending():
    current = motif(); variant = develop_backing_motif(current, mode='call_response')
    assert variant[:2] == variant[4:6] == current[:2]
    assert {s.step for s in current[2]} - {s.step for s in variant[2]} == {6}
    assert {s.step for s in current[6]} - {s.step for s in variant[6]} == {10}
    assert variant[7][:-1] == current[7][:-1]
    assert variant[7][-1].step == 13 and variant[7][-1].duration_steps == 1
    assert sum(map(len, variant)) == 58
    assert sum(s.voicing == 'power5' for bar in variant for s in bar) == 45


@pytest.mark.parametrize('mode', ['current', 'repetition', 'call_response'])
def test_no_new_onsets_primary_accents_or_rest_occupancy(mode):
    current = motif(); variant = develop_backing_motif(current, mode=mode)
    assert variant == develop_backing_motif(current, mode=mode)
    for old, new in zip(current, variant):
        before = {s.step: s for s in old}
        assert set(s.step for s in new) <= set(before)
        maximum = max(s.velocity for s in old)
        assert {s.step for s in old if s.velocity >= .9 * maximum} == {s.step for s in new if s.velocity >= .9 * maximum}
        for s in new:
            a = before[s.step]
            assert (s.note, s.voicing, s.velocity) == (a.note, a.voicing, a.velocity)
            assert s.duration_steps <= a.duration_steps
        sound = lambda bar: {cell for s in bar for cell in range(16) if cell < s.step+s.duration_steps and cell+1 > s.step}
        assert sound(new) <= sound(old)
        assert all(a.step + a.duration_steps <= b.step for a, b in zip(new, new[1:]))
        assert new[-1].step + new[-1].duration_steps <= 16
    assert current == motif()


def test_no_safe_candidate_is_a_no_op():
    # One short high-velocity attack: no long gate/interior weak event to edit.
    sparse = tuple((BackingStep(0, 52, 1, 108),) for _ in range(8))
    assert develop_backing_motif(sparse, mode='repetition') == sparse
    assert develop_backing_motif(sparse, mode='call_response') == sparse


@pytest.mark.parametrize('mode', ['', None, [], True, 'random'])
def test_invalid_mode(mode):
    with pytest.raises(ValueError, match='mode'):develop_backing_motif(motif(), mode=mode)


@pytest.mark.parametrize('bars', [None, (), ((),)*8, ((BackingStep(0, 52, 1, 100),),)*7,
                                   ((123,),)*8, ((BackingStep(1, 52, 2, 100), BackingStep(2, 52, 1, 100)),)*8])
def test_invalid_motif(bars):
    with pytest.raises(ValueError):develop_backing_motif(bars)


def test_audition_outputs_reference_drum_identity_bass_and_byte_reproduction(tmp_path):
    request = Path('examples/requests/chorus_motif_variations.json')
    config = json.loads(request.read_text())
    if not Path(config['source_chorus_midi']).is_file():pytest.skip('Local adopted MIDI is not distributed')
    spec = importlib.util.spec_from_file_location('motif_auditions', Path('scripts/create_motif_variation_auditions.py'))
    tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
    first = tmp_path/'first'; second = tmp_path/'second'
    a = tool.run(request, first); b = tool.run(request, second)
    assert a['validation']['A_band_byte_identical_to_Step2']
    assert len(list(first.glob('*.mid'))) == 10
    assert all(p.read_bytes() == (second/p.name).read_bytes() for p in first.glob('*.mid'))
    for candidate, events in (('A', 60), ('B', 60), ('C', 58)):
        c = a['candidates'][candidate]
        assert c['event_count'] == events and c['bass_follow_exact']
        assert c['rhythm']['major_rest_retention'] == 1
        assert c['repetition']['exact_return_pairs'] == 1
        assert c['rhythm']['pair_similarities']['bars1_2_vs5_6'] == 1
    with pytest.raises(FileExistsError):tool.run(request, first)
