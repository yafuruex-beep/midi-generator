"""Voicing changes pitches only; gates and retrigger ordering stay intact."""
from io import BytesIO

import mido
import pytest

from midi_generator.api import BackingRules, BackingStep, RhythmBlueprint, generate_backing_steps, render_backing_midi
from midi_generator.modules.guitar.timeline import read_timeline


@pytest.mark.parametrize('voicing,notes', [('single', (52,)), ('power5', (52, 59)), ('power8', (52, 59, 64))])
@pytest.mark.parametrize('duration', [1, 2])
def test_voicing_preserves_rhythm_and_pairs(tmp_path, voicing, notes, duration):
    bp = RhythmBlueprint((0, 1, 8, 15), (4, 12), (0, 8))
    baseline = generate_backing_steps(bp, note=52, rules=BackingRules(duration_steps=duration))
    steps = generate_backing_steps(bp, note=52, rules=BackingRules(duration_steps=duration, voicing=voicing))
    assert [(s.step, s.duration_steps, s.velocity) for s in steps] == [(s.step, s.duration_steps, s.velocity) for s in baseline]
    assert all(s.notes == notes for s in steps)
    path = tmp_path / 'voiced.mid'
    render_backing_midi(steps, export_bpm=105, output_path=path)
    data = read_timeline(path, set())
    expected = sorted((s.step * 120, s.step * 120 + s.duration_steps * 120, n, s.velocity) for s in steps for n in notes)
    assert sorted((n['start_tick'], n['end_tick'], n['note'], n['velocity']) for n in data['notes']) == expected
    assert data['end_tick'] == 1920
    assert data['pairing']['unmatched_note_offs'] == 0
    assert data['pairing']['inferred_note_ends'] == 0


@pytest.mark.parametrize('voicing,count', [('single', 1), ('power5', 2), ('power8', 3)])
def test_all_releases_before_retrigger_and_bar_boundary(tmp_path, voicing, count):
    steps = generate_backing_steps(RhythmBlueprint(tuple(range(16)), (), ()), note=52,
                                   rules=BackingRules(duration_steps=2, voicing=voicing))
    clip = render_backing_midi(steps, export_bpm=105)
    # Assemble two exact clips to exercise the release/retrigger at a bar edge.
    track = mido.MidiTrack()
    for _ in range(2):
        track.extend(m.copy() for m in clip.tracks[0] if not m.is_meta)
    track.append(mido.MetaMessage('end_of_track'))
    midi = mido.MidiFile(ticks_per_beat=480); midi.tracks.append(track)
    path = tmp_path / 'two.mid'; midi.save(path)
    events = {}; tick = 0
    for msg in track:
        tick += msg.time
        if msg.type in {'note_on', 'note_off'}:
            events.setdefault(tick, []).append(msg.type)
    for tick in range(120, 3840, 120):
        assert events[tick] == ['note_off'] * count + ['note_on'] * count
    assert events[3840] == ['note_off'] * count
    data = read_timeline(path, set())
    assert len(data['notes']) == 32 * count
    assert data['pairing']['unmatched_note_offs'] == 0
    assert data['pairing']['inferred_note_ends'] == 0


@pytest.mark.parametrize('voicing,highest', [('single', 127), ('power5', 120), ('power8', 115)])
def test_pitch_bounds(voicing, highest):
    assert BackingStep(0, highest, 1, 100, voicing).notes[-1] == 127
    assert BackingStep(0, 0, 1, 100, voicing).notes[0] == 0
    if highest < 127:
        with pytest.raises(ValueError, match='MIDI note range'):
            BackingStep(0, highest + 1, 1, 100, voicing)
        with pytest.raises(ValueError, match='MIDI note range'):
            generate_backing_steps(RhythmBlueprint((0,), (), ()), note=highest + 1, rules=BackingRules(voicing=voicing))


@pytest.mark.parametrize('invalid', ['', 'power', None, True, [], 5])
def test_invalid_voicing(invalid):
    with pytest.raises(ValueError, match='voicing'):
        BackingRules(voicing=invalid)
    with pytest.raises(ValueError, match='voicing'):
        BackingStep(0, 52, 1, 100, invalid)


def test_explicit_single_matches_default_bytes():
    bp = RhythmBlueprint((0, 3, 8, 15), (4, 12), (0, 8))
    outputs = []
    for rules in (BackingRules(), BackingRules(voicing='single')):
        stream = BytesIO()
        render_backing_midi(generate_backing_steps(bp, note=52, rules=rules), export_bpm=105, program=31).save(file=stream)
        outputs.append(stream.getvalue())
    assert outputs[0] == outputs[1]
