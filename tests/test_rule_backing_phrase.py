"""Explicit phrase rhythm, unchanged legacy mode, and complete MIDI gates."""
from io import BytesIO
import math

import mido
import pytest

from midi_generator.api import (BackingRules, RhythmBlueprint, generate_backing_phrase,
                                generate_backing_steps, render_backing_midi)
from midi_generator.modules.guitar.timeline import read_timeline


def rules(**extra):
    return BackingRules(voicing='mixed', power_chord_ratio=.75, duration_steps=1, **extra)


def encoded(steps):
    stream = BytesIO()
    render_backing_midi(steps, export_bpm=105, program=31).save(file=stream)
    return stream.getvalue()


def test_four_bar_structure_and_small_changes():
    bp = RhythmBlueprint((0,), (8,), (0, 8))
    bars = generate_backing_phrase(bp, note=52, rules=rules(rhythm_mode='phrase4'))
    expected = [list(range(0, 16, 2)), [0, 2, 4, 6, 8, 10, 12, 15],
                list(range(0, 16, 2)), [0, 2, 4, 6, 8, 13]]
    assert [[s.step for s in bar] for bar in bars] == expected * 2
    assert bars[:4] == bars[4:]
    rhythm = lambda bar: [(s.step, s.duration_steps, s.velocity) for s in bar]
    assert rhythm(bars[0]) == rhythm(bars[2]) == rhythm(bars[4]) == rhythm(bars[6])
    assert [len(bar) for bar in bars] == [8, 8, 8, 6] * 2
    assert sum(s.voicing == 'power5' for bar in bars for s in bar) == 46
    assert sum(map(len, bars)) == 60
    # Stop after step 8, restart at step 13, and leave the final two cells silent.
    assert bars[3][-2].step + bars[3][-2].duration_steps == 9
    assert bars[3][-1].step == 13
    assert bars[3][-1].step + bars[3][-1].duration_steps == 14


@pytest.mark.parametrize('mode', ['single', 'power5', 'power8', 'mixed'])
def test_default_and_explicit_repeat_bytes(mode):
    bp = RhythmBlueprint((0, 3, 8, 15), (4, 12), (0, 8))
    default = generate_backing_phrase(bp, note=52, rules=BackingRules(voicing=mode))
    explicit = generate_backing_phrase(bp, note=52, rules=BackingRules(voicing=mode, rhythm_mode='repeat'))
    for i, (a, b) in enumerate(zip(default, explicit)):
        old = generate_backing_steps(bp, note=52, rules=BackingRules(voicing=mode), phrase_bar_index=i)
        assert encoded(a) == encoded(b) == encoded(old)


@pytest.mark.parametrize('kicks,snares', [((0,), (8,)), ((0, 3, 8, 15), (4, 12)),
                                         (tuple(range(16)), ()), ((15,), ()), ((0, 14), ())])
def test_phrase_properties_pairing_reproducibility(tmp_path, kicks, snares):
    bp = RhythmBlueprint(kicks, snares, ())
    settings = rules(rhythm_mode='phrase4')
    bars = generate_backing_phrase(bp, note=52, rules=settings)
    assert bars == generate_backing_phrase(bp, note=52, rules=settings)
    source = generate_backing_steps(bp, note=52, rules=rules())
    source_positions = {s.step for s in source}
    track = mido.MidiTrack()
    expected = []
    for bar_index, bar in enumerate(bars):
        positions = {s.step for s in bar}
        assert set(kicks) <= positions
        assert len(source_positions - positions) <= 3
        assert len(positions - source_positions) <= 1
        assert sum(s.voicing == 'power5' for s in bar) == math.floor(len(bar) * .75 + .5)
        assert all(type(s.step) is int and 0 <= s.step < 16 for s in bar)
        assert all(s.duration_steps == 1 and s.notes in ((52,), (52, 59)) for s in bar)
        # Retained events keep their original velocity.
        old = {s.step: s.velocity for s in source}
        assert all(s.velocity == old[s.step] for s in bar if s.step in old)
        midi = render_backing_midi(bar, export_bpm=105)
        assert encoded(bar) == encoded(generate_backing_steps(bp, note=52, rules=settings, phrase_bar_index=bar_index))
        for msg in midi.tracks[0]:
            track.append(mido.MetaMessage('marker', text='bar end', time=msg.time)
                         if msg.type == 'end_of_track' else msg.copy())
        expected.extend(((bar_index * 16 + s.step) * 120, (bar_index * 16 + s.step + 1) * 120, n, s.velocity)
                        for s in bar for n in s.notes)
    track.append(mido.MetaMessage('end_of_track'))
    result = mido.MidiFile(ticks_per_beat=480); result.tracks.append(track)
    path = tmp_path / 'phrase.mid'; result.save(path)
    data = read_timeline(path, set())
    assert data['end_tick'] == 15360
    assert data['pairing']['unmatched_note_offs'] == data['pairing']['inferred_note_ends'] == 0
    assert sorted((n['start_tick'], n['end_tick'], n['note'], n['velocity']) for n in data['notes']) == sorted(expected)
    active = set(); tick = 0
    for msg in track:
        tick += msg.time
        if msg.type == 'note_on' and msg.velocity:
            assert msg.note not in active
            active.add(msg.note)
        elif msg.type == 'note_off':
            assert msg.note in active
            active.remove(msg.note)
        if msg.type == 'marker':
            assert tick % 1920 == 0 and not active
    assert not active


def test_moved_strong_event_keeps_velocity_not_new_position_strength():
    # Ending moves the supplement at 12 to 11, keeping the source velocity 96.
    bp = RhythmBlueprint((0, 14), (), ())
    bar = generate_backing_steps(bp, note=52, rules=rules(rhythm_mode='phrase4'), phrase_bar_index=3)
    assert next(s for s in bar if s.step == 11).velocity == 96
    assert 14 in {s.step for s in bar}  # Kick is never shifted/removed.


def test_half_beat_gate_caps_at_last_sixteenth():
    bp = RhythmBlueprint((0,), (8,), ())
    bar = generate_backing_steps(bp, note=52, phrase_bar_index=1,
        rules=BackingRules(rhythm_mode='phrase4', duration_steps=2))
    assert bar[-1].step == 15 and bar[-1].duration_steps == 1
    assert all(s.step + s.duration_steps <= 16 for s in bar)


@pytest.mark.parametrize('invalid', ['', 'phrase', None, True, [], 4])
def test_bad_mode(invalid):
    with pytest.raises(ValueError, match='rhythm_mode'):
        BackingRules(rhythm_mode=invalid)


@pytest.mark.parametrize('invalid', [0, -1, True, 1.5, '8', None])
def test_bad_length(invalid):
    with pytest.raises(ValueError, match='bars'):
        generate_backing_phrase(RhythmBlueprint((0,), (), ()), note=52, bars=invalid)
