"""Selectable note gates without changing attacks, pitches or velocities."""
import hashlib
from io import BytesIO

import mido
import pytest

from midi_generator.api import (RhythmBlueprint, BackingRules, generate_backing_steps,
                                render_backing_midi)
from midi_generator.modules.guitar.timeline import read_timeline


def midi_bytes(steps):
    out = BytesIO()
    render_backing_midi(steps, export_bpm=105, program=31).save(file=out)
    return out.getvalue()


def test_default_output_matches_pre_step3_bytes():
    # Captured from the accepted Step 2 implementation before duration changes.
    bp = RhythmBlueprint((0, 3, 8, 15), (4, 12), (0, 8))
    steps = generate_backing_steps(bp, note=52)
    assert hashlib.sha256(midi_bytes(steps)).hexdigest() == '75ea64b4f4c0499f827d17fa927d47cbdc9804b363b02e229cb6e653f837b08c'
    assert steps == generate_backing_steps(bp, note=52, rules=BackingRules(duration_steps=.5))


@pytest.mark.parametrize('duration_steps,beats', [(.5, .125), (1, .25), (2, .5)])
def test_three_gate_lengths_change_only_ends(tmp_path, duration_steps, beats):
    bp = RhythmBlueprint((0,), (8,), (0, 8))
    default = generate_backing_steps(bp, note=52)
    steps = generate_backing_steps(bp, note=52, rules=BackingRules(duration_steps=duration_steps))
    assert [(s.step, s.note, s.velocity) for s in steps] == [(s.step, s.note, s.velocity) for s in default]
    assert [s.step for s in steps] == list(range(0, 16, 2))
    path = tmp_path / 'guitar.mid'
    render_backing_midi(steps, export_bpm=105, program=31, output_path=path)
    data = read_timeline(path, set())
    assert len(data['notes']) == 8
    assert [(n['start_tick'], n['note'], n['velocity']) for n in data['notes']] == [(s.step * 120, 52, s.velocity) for s in steps]
    assert [(n['end_tick'] - n['start_tick']) / 480 for n in data['notes']] == [beats] * 8
    assert data['end_tick'] == 1920
    assert data['context']['tempo_events'][0]['microseconds_per_beat'] == mido.bpm2tempo(105)
    assert not data['pairing']['inferred_note_ends']
    assert not data['pairing']['unmatched_note_offs']
    assert all(a['end_tick'] <= b['start_tick'] for a, b in zip(data['notes'], data['notes'][1:]))


def test_fast_retrigger_clipped_and_note_off_precedes_note_on(tmp_path):
    bp = RhythmBlueprint(tuple(range(16)), (), ())
    steps = generate_backing_steps(bp, note=52, rules=BackingRules(duration_steps=2))
    assert [s.step for s in steps] == list(range(16))
    assert all(s.duration_steps == 1 for s in steps)
    path = tmp_path / 'retrigger.mid'
    render_backing_midi(steps, export_bpm=105, output_path=path)
    midi = mido.MidiFile(path)
    at_tick = {}; tick = 0
    for msg in midi.tracks[0]:
        tick += msg.time
        if msg.type in {'note_on', 'note_off'}:
            at_tick.setdefault(tick, []).append(msg.type)
    assert all(at_tick[t] == ['note_off', 'note_on'] for t in range(120, 1920, 120))
    assert at_tick[1920] == ['note_off']
    data = read_timeline(path, set())
    assert len(data['notes']) == 16
    assert not data['pairing']['inferred_note_ends']
    assert not data['pairing']['unmatched_note_offs']


@pytest.mark.parametrize('requested,actual', [(.5, .5), (1, 1), (2, 1)])
def test_last_sixteenth_shortens_at_bar_end(tmp_path, requested, actual):
    steps = generate_backing_steps(RhythmBlueprint((15,), (), ()), note=52,
                                   rules=BackingRules(duration_steps=requested))
    assert len(steps) == 1
    assert steps[0].step == 15
    assert steps[0].duration_steps == actual
    path = tmp_path / 'last.mid'
    render_backing_midi(steps, export_bpm=105, output_path=path)
    data = read_timeline(path, set())
    assert data['notes'][0]['start_tick'] == 1800
    assert data['notes'][0]['end_tick'] == 1800 + actual * 120
    assert data['end_tick'] == 1920


def test_half_beat_caps_at_next_sixteenth_not_just_bar():
    bp = RhythmBlueprint((0, 1, 15), (), ())
    short = generate_backing_steps(bp, note=52)
    long = generate_backing_steps(bp, note=52, rules=BackingRules(duration_steps=2))
    assert [(s.step, s.note, s.velocity) for s in long] == [(s.step, s.note, s.velocity) for s in short]
    assert long[0].duration_steps == 1
    assert long[1].duration_steps == 2
    assert long[-1].duration_steps == 1


@pytest.mark.parametrize('invalid', [0, -.5, 2.0001, 4, float('nan'), float('inf'),
                                     -float('inf'), True, False, None, '2'])
def test_invalid_gate_is_clear_error(invalid):
    with pytest.raises(ValueError, match='duration_steps'):
        generate_backing_steps(RhythmBlueprint((0,), (), ()), note=52,
                               rules=BackingRules(duration_steps=invalid))
