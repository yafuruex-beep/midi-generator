"""Mixed voicing hypothesis: sparse deterministic accents, unchanged rhythm."""
from io import BytesIO

import mido
import pytest

from midi_generator.api import BackingRules, RhythmBlueprint, generate_backing_steps, render_backing_midi
from midi_generator.modules.guitar.timeline import read_timeline


def mixed(bp, bar, duration=2, note=52):
    return generate_backing_steps(bp, note=note, rules=BackingRules(voicing='mixed', duration_steps=duration), phrase_bar_index=bar)


@pytest.mark.parametrize('kicks,snares', [((0,), ()), (tuple(range(16)), (4, 12)), ((0, 5, 9, 15), (4, 12)), ((15,), ())])
@pytest.mark.parametrize('duration', [.5, 1, 2])
def test_mixed_rhythm_pitches_pairing_and_boundary(tmp_path, kicks, snares, duration):
    bp = RhythmBlueprint(kicks, snares, ())
    baseline = generate_backing_steps(bp, note=52, rules=BackingRules(duration_steps=duration))
    for bar in range(8):
        steps = mixed(bp, bar, duration)
        assert [(s.step, s.duration_steps, s.velocity) for s in steps] == [(s.step, s.duration_steps, s.velocity) for s in baseline]
        assert all(s.notes in ((52,), (52, 59)) for s in steps)
        assert steps == mixed(bp, bar, duration) == mixed(bp, bar + 4, duration)
        if len(steps) > 1:
            assert sum(s.voicing == 'power5' for s in steps) == 1
        path = tmp_path / f'{bar}.mid'
        render_backing_midi(steps, export_bpm=105, output_path=path)
        data = read_timeline(path, set())
        assert sorted((n['start_tick'], n['end_tick'], n['note'], n['velocity']) for n in data['notes']) == sorted(
            (s.step * 120, (s.step + s.duration_steps) * 120, n, s.velocity) for s in steps for n in s.notes)
        assert data['end_tick'] == 1920
        assert data['pairing']['unmatched_note_offs'] == data['pairing']['inferred_note_ends'] == 0


def test_unbroken_eighth_run_has_sparse_varying_accents_independent_of_gate():
    bp = RhythmBlueprint((0,), (), ())
    patterns = []
    for bar in range(8):
        patterns.append(tuple(s.step for s in mixed(bp, bar) if s.voicing == 'power5'))
        assert patterns[-1] == tuple(s.step for s in mixed(bp, bar, 1) if s.voicing == 'power5')
    assert patterns[:4] == [(0,), (8,), (4,), (14,)]
    assert patterns[:4] == patterns[4:]


def test_gap_is_candidate_not_mandatory():
    # No supplements: 2->5 and 5->9 are structural gaps, but only one chord.
    bp = RhythmBlueprint((0, 2, 5, 9, 12, 15), (), ())
    steps = mixed(bp, 0)
    assert steps[0].voicing == 'power5'
    assert next(s for s in steps if s.step == 5).voicing == 'single'
    assert next(s for s in mixed(bp, 1) if s.step == 9).voicing == 'power5'


def test_retrigger_order_across_mixed_bar_boundaries():
    bp = RhythmBlueprint(tuple(range(16)), (), ())
    active = set()
    events = {}; offset = 0
    for bar in range(8):
        midi = render_backing_midi(mixed(bp, bar), export_bpm=105)
        tick = offset
        for msg in midi.tracks[0]:
            tick += msg.time
            if msg.type == 'note_on':
                assert msg.note not in active
                active.add(msg.note)
                events.setdefault(tick, []).append('on')
            elif msg.type == 'note_off':
                assert msg.note in active
                active.remove(msg.note)
                events.setdefault(tick, []).append('off')
        assert not active
        offset = tick
    for tick in range(1920, 15360, 1920):
        assert events[tick] == sorted(events[tick], key=lambda kind: kind == 'on')


@pytest.mark.parametrize('bad', [-1, True, 1.5, '1', None])
def test_phrase_index_validation(bad):
    with pytest.raises(ValueError, match='phrase_bar_index'):
        mixed(RhythmBlueprint((0,), (), ()), bad)


def test_mixed_pitch_limit_even_on_single_only_cycle_bar():
    bp = RhythmBlueprint((15,), (), ())
    with pytest.raises(ValueError, match='MIDI note range'):
        mixed(bp, 1, note=121)
    assert mixed(bp, 0, note=120)[0].notes == (120, 127)


def test_rendered_reproducibility():
    bp = RhythmBlueprint((0, 3, 8, 15), (4, 12), (0, 8))
    def output():
        stream = BytesIO()
        render_backing_midi(mixed(bp, 2), export_bpm=105).save(file=stream)
        return stream.getvalue()
    assert output() == output()
