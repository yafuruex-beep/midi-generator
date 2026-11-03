import hashlib
from io import BytesIO

import mido
import pytest

from midi_generator.api import (
    RhythmBlueprint, BackingRules, BackingStep,
    extract_rhythm_blueprint, generate_backing_steps, render_backing_midi,
)
from midi_generator.modules.guitar.timeline import read_timeline
from test_guitar_analysis import write_midi


def drums(tmp_path, *, kicks=(0, 2), snares=(1, 3), tpb=480, **kwargs):
    notes = [(b, 36, .125, 100, 0, 9) for b in kicks]
    notes += [(b, 38, .125, 110, 1, 9) for b in snares]
    return write_midi(tmp_path / 'drums.mid', notes, tpb=tpb, end_beats=4, tracks=2, **kwargs)


@pytest.mark.parametrize('tpb', [96, 480, 960])
def test_multitrack_blueprint_and_deterministic_rules(tmp_path, tpb):
    path = drums(tmp_path, tpb=tpb)
    before = path.read_bytes()
    bp = extract_rhythm_blueprint(path)
    assert bp.kick_steps == (0, 8)
    assert bp.snare_steps == (4, 12)
    assert bp.strong_steps == (0, 4, 8, 12)
    assert bp.accent_steps == (0, 4, 8, 12)
    assert bp.source_sha256 == hashlib.sha256(before).hexdigest()
    assert bp.source_ticks_per_beat == tpb
    steps = generate_backing_steps(bp, note=40)
    assert steps == generate_backing_steps(extract_rhythm_blueprint(path), note=40)
    cells = {s.step for s in steps}
    assert cells == {0, 2, 4, 6, 8, 10, 12, 14}
    assert set(bp.kick_steps) < cells
    assert len(steps) < 16
    assert steps[0].velocity > steps[1].velocity
    assert steps[2].velocity > steps[1].velocity
    assert path.read_bytes() == before


def test_pre_snare_rest_and_kick_priority():
    bp = RhythmBlueprint((0, 6, 8), (3, 7), ())
    steps = generate_backing_steps(bp, note=40)
    cells = {s.step for s in steps}
    assert 2 not in cells
    assert 6 in cells  # kick wins over pre-snare rest
    assert 2 in {s.step for s in generate_backing_steps(bp, note=40, rules=BackingRules(rest_before_snare=False))}
    assert all(s.duration_steps <= .5 for s in steps)


def test_render_tempo_pairing_no_overlap_and_overwrite(tmp_path):
    bp = extract_rhythm_blueprint(drums(tmp_path, kicks=(0, .75, 2, 3.75)))
    steps = generate_backing_steps(bp, note=40)
    path = tmp_path / 'new/guitar.mid'
    midi = render_backing_midi(steps, export_bpm=105, output_path=path)
    data = read_timeline(path, set())
    assert data['end_tick'] == 1920
    assert data['context']['tempo_events'][0]['microseconds_per_beat'] == mido.bpm2tempo(105)
    assert data['context']['time_signature_events'][0]['numerator'] == 4
    assert not data['pairing']['inferred_note_ends']
    assert not data['pairing']['unmatched_note_offs']
    assert len(data['notes']) == len(steps)
    assert {n['note'] for n in data['notes']} == {40}
    assert all(n['channel'] == 1 for n in data['notes'])
    ends = [n['end_tick'] for n in data['notes']]
    starts = [n['start_tick'] for n in data['notes']]
    assert all(0 <= a < b <= 1920 for a, b in zip(starts, ends))
    assert all(b <= a for b, a in zip(ends, starts[1:]))
    assert all(s * 120 in starts for s in bp.kick_steps)
    b1, b2 = BytesIO(), BytesIO()
    midi.save(file=b1)
    render_backing_midi(steps, export_bpm=105).save(file=b2)
    assert b1.getvalue() == b2.getvalue() == path.read_bytes()
    with pytest.raises(FileExistsError):
        render_backing_midi(steps, export_bpm=105, output_path=path)
    assert path.read_bytes() == b1.getvalue()
    assert bp.source_bpm == 120  # Export BPM never relabels input tempo


@pytest.mark.parametrize('kwargs', [
    {'kicks': (0, .1)}, {'kicks': ()}, {'meters': [(0, 3, 4)]},
    {'tempos': [(0, 500000), (1, 600000)]},
    {'meters': [(0, 4, 4), (2, 3, 4)]},
    {'kicks': (0, 4)},
])
def test_unsupported_input(tmp_path, kwargs):
    with pytest.raises(ValueError):
        extract_rhythm_blueprint(drums(tmp_path, **kwargs))


def test_long_short_malformed_and_missing_releases(tmp_path):
    for length in (3, 8):
        path = write_midi(tmp_path / f'{length}.mid', [(0, 36, .1, 100, 0, 9)], end_beats=length)
        with pytest.raises(ValueError, match='one complete bar'):
            extract_rhythm_blueprint(path)
    path = tmp_path / 'broken.mid'
    path.write_bytes(b'not midi')
    with pytest.raises(ValueError, match='Could not read'):
        extract_rhythm_blueprint(path)
    midi = mido.MidiFile()
    midi.tracks.append(mido.MidiTrack([mido.Message('note_on', note=36, channel=9),
                                      mido.MetaMessage('end_of_track', time=1920)]))
    midi.save(path)
    with pytest.raises(ValueError, match='unmatched'):
        extract_rhythm_blueprint(path)


def test_tolerance_warning_and_custom_mapping(tmp_path):
    bp = extract_rhythm_blueprint(drums(tmp_path, kicks=(.02, 2)))
    assert bp.kick_steps == (0, 8)
    assert any('mapped to nearest' in w for w in bp.warnings)
    path = write_midi(tmp_path / 'custom.mid', [(0, 60, .125, 100, 0, 9)], end_beats=4)
    with pytest.raises(ValueError, match='No mapped kick'):
        extract_rhythm_blueprint(path)
    config = tmp_path / 'sync.json'
    config.write_text('{"kick_notes": [60]}')
    assert extract_rhythm_blueprint(path, sync_config_path=config).kick_steps == (0,)


@pytest.mark.parametrize('kwargs', [
    {'export_bpm': 0}, {'export_bpm': float('nan')}, {'export_bpm': True},
    {'export_bpm': .01}, {'ticks_per_beat': 97}, {'ticks_per_beat': -24},
    {'midi_channel': 10}, {'midi_channel': 0}, {'midi_channel': True},
])
def test_invalid_render_settings(kwargs):
    with pytest.raises(ValueError):
        render_backing_midi((BackingStep(0, 40, .5, 100),), **{'export_bpm': 105, **kwargs})


@pytest.mark.parametrize('steps', [
    (), (BackingStep(1, 40, 1, 100), BackingStep(0, 40, .5, 100)),
    (BackingStep(0, 40, 2, 100), BackingStep(1, 40, .5, 100)),
    (BackingStep(0, 40, .5, 100), BackingStep(0, 40, .5, 100)),
    (BackingStep(0, 40, .5, 100), BackingStep(2, 41, .5, 100)),
    (BackingStep(0, 40, .001, 100),),
])
def test_invalid_intermediate_data(steps):
    with pytest.raises(ValueError):
        render_backing_midi(steps, export_bpm=105)


@pytest.mark.parametrize('factory', [
    lambda: RhythmBlueprint((0,), (), (), steps_per_bar=8),
    lambda: RhythmBlueprint((0,), (), (), time_signature=(3, 4)),
    lambda: RhythmBlueprint((16,), (), ()),
    lambda: RhythmBlueprint((0, 0), (), ()),
    lambda: RhythmBlueprint((), (), ()),
    lambda: BackingStep(15, 40, 2, 100),
    lambda: BackingStep(0, 128, .5, 100),
    lambda: BackingRules(duration_steps=0),
    lambda: BackingRules(base_velocity=127),
])
def test_invalid_blueprint_rules_notes(factory):
    with pytest.raises(ValueError):
        factory()


def test_program_number_is_separate_from_pitch():
    midi = render_backing_midi((BackingStep(0, 52, .5, 108),), export_bpm=105, program=31)
    assert [m.program for m in midi.tracks[0] if m.type == 'program_change'] == [30]
    assert [m.note for m in midi.tracks[0] if m.type == 'note_on'] == [52]
    for invalid in (0, 129, True):
        with pytest.raises(ValueError):
            render_backing_midi((BackingStep(0, 52, .5, 108),), export_bpm=105, program=invalid)
