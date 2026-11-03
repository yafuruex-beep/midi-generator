"""Opt-in gates and ending space preserve event identity and legacy output."""
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys

import mido
import pytest

from midi_generator.api import BackingRules, RhythmBlueprint, generate_backing_phrase, render_backing_midi
from midi_generator.modules.guitar.timeline import read_timeline
from test_guitar_analysis import write_midi


def settings(**extra):
    return BackingRules(rhythm_mode='phrase4', voicing='mixed', power_chord_ratio=.75,
                        duration_steps=1, **extra)


def base():
    return RhythmBlueprint((0,), (8,), (0, 8))


def encode(bar):
    stream = BytesIO()
    render_backing_midi(bar, export_bpm=105, program=31).save(file=stream)
    return stream.getvalue()


def test_exact_gate_locations_counts_and_rest_preservation():
    a = generate_backing_phrase(base(), note=52, rules=settings())
    b = generate_backing_phrase(base(), note=52, rules=settings(), duration_mode='contrast')
    changes = []
    for bar_index, (old, new) in enumerate(zip(a, b)):
        assert [(s.step, s.notes, s.velocity) for s in old] == [(s.step, s.notes, s.velocity) for s in new]
        for index, (before, after) in enumerate(zip(old, new)):
            if before.duration_steps != after.duration_steps:
                changes.append((bar_index + 1, before.step))
                stop = old[index + 1].step if index + 1 < len(old) else 16
                assert after.duration_steps == 2
                assert after.step + after.duration_steps <= stop - 1
            else:
                assert after.duration_steps == 1
    assert changes == [(2, 12), (4, 8), (4, 13), (6, 12), (8, 8), (8, 13)]
    assert sum(s.duration_steps == 2 for bar in b for s in bar) / sum(map(len, b)) == .1
    assert b[:4] == b[4:]
    assert b[0] == a[0] and b[2] == a[2]  # Continuous chugs never lengthened.


def test_C_only_removes_ending_pre_snare_events():
    b = generate_backing_phrase(base(), note=52, rules=settings(), duration_mode='contrast')
    c = generate_backing_phrase(base(), note=52, rules=settings(), duration_mode='contrast', drum_mode='ending_space')
    removed = []
    for i, (before, after) in enumerate(zip(b, c)):
        previous = {s.step: s for s in before}
        assert all(s == previous[s.step] for s in after)
        removed.extend((i + 1, s.step) for s in before if s not in after)
        assert 0 in {s.step for s in after}
        assert after[-1] == before[-1]
    assert removed == [(4, 6), (8, 6)]
    assert sum(map(len, c)) == 58
    assert sum(s.voicing == 'power5' for bar in c for s in bar) == 44
    assert sum(s.duration_steps == 2 for bar in c for s in bar) == 6
    assert c[:4] == c[4:]
    for i in (3, 7):
        preceding = [s for s in c[i] if s.step < 8]
        assert 8 - max(s.step + s.duration_steps for s in preceding) == 3


@pytest.mark.parametrize('bp', [RhythmBlueprint((0,), (), ()),
    RhythmBlueprint((0, 6), (8,), ()), RhythmBlueprint(tuple(range(16)), (8,), ())])
def test_no_unjustified_deletion_and_no_kick_removal(bp):
    a = generate_backing_phrase(bp, note=52, rules=settings())
    c = generate_backing_phrase(bp, note=52, rules=settings(), drum_mode='ending_space')
    assert a == c  # No pre-snare candidate, or the candidate itself is a kick.


def test_target_is_configurable_and_does_not_force_unsafe_long_notes():
    zero = generate_backing_phrase(base(), note=52, rules=settings(), duration_mode='contrast', long_note_ratio=0)
    assert zero == generate_backing_phrase(base(), note=52, rules=settings())
    high = generate_backing_phrase(base(), note=52, rules=settings(), duration_mode='contrast', long_note_ratio=1)
    assert sum(s.duration_steps == 2 for bar in high for s in bar) == 6  # Safe candidates only.
    dense = RhythmBlueprint(tuple(range(16)), (), ())
    a = generate_backing_phrase(dense, note=52, rules=settings())
    b = generate_backing_phrase(dense, note=52, rules=settings(), duration_mode='contrast', long_note_ratio=1)
    assert a == b
    final = RhythmBlueprint((15,), (), ())
    assert all(s.duration_steps == 1 for bar in generate_backing_phrase(final, note=52,
               rules=settings(), duration_mode='contrast') for s in bar)


@pytest.mark.parametrize('kwargs', [{}, {'duration_mode': 'contrast'},
                                   {'duration_mode': 'contrast', 'drum_mode': 'ending_space'}])
def test_rendered_phrase_pairing_retrigger_grid_and_determinism(tmp_path, kwargs):
    for bp in (base(), RhythmBlueprint(tuple(range(16)), (8,), ())):
        bars = generate_backing_phrase(bp, note=52, rules=settings(), **kwargs)
        again = generate_backing_phrase(bp, note=52, rules=settings(), **kwargs)
        assert [encode(b) for b in bars] == [encode(b) for b in again]
        track = mido.MidiTrack(); expected = []
        for i, bar in enumerate(bars):
            midi = render_backing_midi(bar, export_bpm=105)
            for msg in midi.tracks[0]:
                track.append(mido.MetaMessage('marker', text='bar end', time=msg.time)
                             if msg.type == 'end_of_track' else msg.copy())
            expected.extend(((i * 16 + s.step) * 120, (i * 16 + s.step + s.duration_steps) * 120,
                             n, s.velocity) for s in bar for n in s.notes)
        track.append(mido.MetaMessage('end_of_track'))
        midi = mido.MidiFile(ticks_per_beat=480); midi.tracks.append(track)
        path = tmp_path / 'result.mid'; midi.save(path)
        data = read_timeline(path, set())
        assert sorted((n['start_tick'], n['end_tick'], n['note'], n['velocity']) for n in data['notes']) == sorted(expected)
        assert data['pairing']['unmatched_note_offs'] == data['pairing']['inferred_note_ends'] == 0
        active = set(); tick = 0
        for msg in track:
            tick += msg.time
            if msg.type == 'note_on' and msg.velocity:
                assert msg.note not in active
                active.add(msg.note)
            elif msg.type == 'note_off':
                assert msg.note in active
                active.remove(msg.note)
            elif msg.type == 'marker':
                assert tick % 1920 == 0 and not active
        assert tick == 15360 and not active


@pytest.mark.parametrize('mode', ['repeat', 'phrase4'])
def test_explicit_preserve_equals_default_bytes(mode):
    rules = BackingRules(rhythm_mode=mode)
    a = generate_backing_phrase(base(), note=52, rules=rules)
    b = generate_backing_phrase(base(), note=52, rules=rules, duration_mode='fixed', drum_mode='preserve')
    assert [encode(s) for s in a] == [encode(s) for s in b]


@pytest.mark.parametrize('kwargs', [
    {'duration_mode': 'random'}, {'duration_mode': None}, {'duration_mode': []},
    {'drum_mode': 'random'}, {'drum_mode': True}, {'drum_mode': []},
    {'long_note_ratio': True}, {'long_note_ratio': -.1}, {'long_note_ratio': 1.1},
    {'long_note_ratio': float('nan')}, {'long_note_ratio': float('inf')},
    {'long_note_ratio': '0.15'}, {'long_note_ratio': None},
])
def test_invalid_refinement_settings(kwargs):
    with pytest.raises(ValueError):
        generate_backing_phrase(base(), note=52, rules=settings(), **kwargs)


def test_refinement_requires_explicit_phrase_and_quarter_beat_base():
    with pytest.raises(ValueError, match='phrase4'):
        generate_backing_phrase(base(), note=52, duration_mode='contrast')
    with pytest.raises(ValueError, match='duration_steps=1'):
        generate_backing_phrase(base(), note=52, rules=BackingRules(rhythm_mode='phrase4'), duration_mode='contrast')


def test_audition_tool_complete_verification(tmp_path):
    notes = [(i * 4 + beat, pitch, .125, 110, 0, 9)
             for i in range(8) for beat, pitch in ((0, 36), (2, 38))]
    source = write_midi(tmp_path / 'drums.mid', notes, end_beats=32,
                        tempos=[(0, mido.bpm2tempo(105))])
    before = source.read_bytes()
    baseline = tmp_path / 'baseline'; output = tmp_path / 'polished'
    subprocess.run([sys.executable, 'scripts/create_phrase_backing_auditions.py', str(source), str(baseline)], check=True, capture_output=True)
    command = [sys.executable, 'scripts/create_groove_backing_auditions.py', str(source), str(output), '--baseline-directory', str(baseline)]
    subprocess.run(command, check=True, capture_output=True)
    report = json.loads((output / 'verification.json').read_text())
    assert len(list(output.glob('*.mid'))) == 6
    assert report['variants']['B']['long_ratio'] == .1
    assert report['variants']['C']['power5_events'] == 44
    assert report['checks']['A_equals_step6_bytes']
    assert report['checks']['drums_all_messages_identical']
    second = tmp_path / 'polished_again'
    command[3] = str(second)
    subprocess.run(command, check=True, capture_output=True)
    assert all(p.read_bytes() == (second / p.name).read_bytes() for p in output.glob('*.mid'))
    assert (output / 'comparison.md').read_bytes() == (second / 'comparison.md').read_bytes()
    refusal = subprocess.run(command, capture_output=True)
    assert refusal.returncode != 0 and b'FileExistsError' in refusal.stderr
    assert source.read_bytes() == before
