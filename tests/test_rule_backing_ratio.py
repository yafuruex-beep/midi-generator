"""Event-count ratios change only chord membership."""
from io import BytesIO
import math

import pytest

from midi_generator.api import BackingRules, RhythmBlueprint, generate_backing_steps, render_backing_midi
from midi_generator.modules.guitar.timeline import read_timeline


def steps_for(bp, ratio, bar=0, duration=1):
    return generate_backing_steps(bp, note=52, rules=BackingRules(voicing='mixed', power_chord_ratio=ratio,
                                   duration_steps=duration), phrase_bar_index=bar)


@pytest.mark.parametrize('ratio', [.5, .75, 1])
@pytest.mark.parametrize('kicks', [(0,), (15,), (0, 3, 8, 15), tuple(range(16)), (12, 13, 14)])
def test_ratio_counts_and_unchanged_events(tmp_path, ratio, kicks):
    bp = RhythmBlueprint(kicks, (4, 12), (0,))
    single = generate_backing_steps(bp, note=52, rules=BackingRules(duration_steps=1))
    for bar in range(4):
        steps = steps_for(bp, ratio, bar)
        assert steps == steps_for(bp, ratio, bar) == steps_for(bp, ratio, bar + 4)
        assert sum(s.voicing == 'power5' for s in steps) == math.floor(len(single) * ratio + .5)
        assert [(s.step, s.note, s.duration_steps, s.velocity) for s in steps] == [(s.step, s.note, s.duration_steps, s.velocity) for s in single]
        assert [s.voicing for s in steps] == [s.voicing for s in steps_for(bp, ratio, bar, 2)]
        path = tmp_path / f'{bar}.mid'
        render_backing_midi(steps, export_bpm=105, output_path=path)
        data = read_timeline(path, set())
        assert sorted((n['start_tick'], n['end_tick'], n['note'], n['velocity']) for n in data['notes']) == sorted(
            (s.step * 120, (s.step + s.duration_steps) * 120, n, s.velocity) for s in steps for n in s.notes)
        assert data['end_tick'] == 1920
        assert data['pairing']['unmatched_note_offs'] == data['pairing']['inferred_note_ends'] == 0


def test_eight_bar_ratios_and_all_power_bytes():
    bp = RhythmBlueprint((0,), (), ())
    for ratio, expected in ((.5, 32), (.75, 48), (1, 64)):
        bars = [steps_for(bp, ratio, bar) for bar in range(8)]
        assert sum(len(s) for s in bars) == 64
        assert sum(s.voicing == 'power5' for bar in bars for s in bar) == expected
    def encoded(steps):
        stream = BytesIO(); render_backing_midi(steps, export_bpm=105).save(file=stream)
        return stream.getvalue()
    assert encoded(steps_for(bp, 1)) == encoded(generate_backing_steps(bp, note=52, rules=BackingRules(voicing='power5', duration_steps=1)))
    assert encoded(steps_for(bp, .75, 2)) == encoded(steps_for(bp, .75, 2))


@pytest.mark.parametrize('bad', [True, False, 0, -.5, .6, 50, '0.5', [], float('nan'), float('inf')])
def test_invalid_ratio(bad):
    with pytest.raises(ValueError, match='power_chord_ratio'):
        BackingRules(voicing='mixed', power_chord_ratio=bad)


@pytest.mark.parametrize('mode', ['single', 'power5', 'power8'])
def test_ratio_requires_mixed(mode):
    with pytest.raises(ValueError, match='requires'):
        BackingRules(voicing=mode, power_chord_ratio=.75)


def test_default_ratio_keeps_old_mixed():
    bp = RhythmBlueprint((0,), (), ())
    for bar in range(8):
        old = generate_backing_steps(bp, note=52, rules=BackingRules(voicing='mixed'), phrase_bar_index=bar)
        assert old == steps_for(bp, None, bar, .5)
        assert sum(s.voicing == 'power5' for s in old) == 1
