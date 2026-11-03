"""Strict guitar-follow bass: exact events, explicit pitch/program, shared CLI."""
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

import mido
import pytest

from midi_generator.api import generate
from midi_generator.core.models import GenerationRequest
from midi_generator.modules.guitar.timeline import read_timeline


def source_midi(path, notes=None, extra=(), end=1920):
    notes = notes if notes is not None else [(0, 120, 52, 96), (240, 480, 52, 108),
                                            (240, 480, 59, 108), (480, 600, 52, 76)]
    events = [(0, 0, mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(105))),
              (0, 0, mido.MetaMessage('time_signature', numerator=4, denominator=4)),
              (0, 0, mido.Message('program_change', program=30, channel=0))]
    for start, stop, pitch, velocity in notes:
        events.append((start, 2, mido.Message('note_on', note=pitch, velocity=velocity)))
        if stop is not None:
            events.append((stop, 1, mido.Message('note_off', note=pitch)))
    events.extend(extra)
    track = mido.MidiTrack(); previous = 0
    for tick, _, msg in sorted(events, key=lambda e: (e[0], e[1])):
        track.append(msg.copy(time=tick - previous)); previous = tick
    track.append(mido.MetaMessage('end_of_track', time=max(0, end - previous)))
    midi = mido.MidiFile(type=0, ticks_per_beat=480); midi.tracks.append(track); midi.save(path)
    return path


def request(path, shift=-12, **options):
    return {'instrument': 'bass', 'section': 'validation', 'tempo': 105, 'bars': 1,
            'options': {'guitar_midi': str(path), 'transpose_semitones': shift, 'program': 34, **options}}


def rows(path):
    data = read_timeline(path, set())
    return data, [(n['start_tick'], n['end_tick'], n['note'], n['velocity'], n['channel']) for n in data['notes']]


@pytest.mark.parametrize('shift,note', [(-12, 40), (-24, 28)])
def test_exact_single_root_ticks_gates_velocity_channel_and_program(tmp_path, shift, note):
    source = source_midi(tmp_path / 'guitar.mid'); before = source.read_bytes()
    output = tmp_path / 'bass.mid'
    result = generate(request(source, shift), output_path=output)
    data, notes = rows(output)
    assert notes == [(0, 120, note, 96, 2), (240, 480, note, 108, 2), (480, 600, note, 76, 2)]
    assert data['end_tick'] == 1920 and data['channels'] == [2]
    assert data['pairing']['unmatched_note_offs'] == data['pairing']['inferred_note_ends'] == 0
    programs = [m for t in data['midi'].tracks for m in t if m.type == 'program_change']
    assert len(programs) == 1 and programs[0].program == 33 and programs[0].channel == 1
    assert source.read_bytes() == before
    assert result.metadata['attack_events'] == 3 and result.metadata['output_notes'] == [note]
    second = tmp_path / 'second.mid'; generate(request(source, shift), output_path=second)
    assert output.read_bytes() == second.read_bytes()
    at_tick = {}; tick = 0
    for msg in data['midi'].tracks[0]:
        tick += msg.time
        if msg.type in ('note_on', 'note_off'):
            at_tick.setdefault(tick, []).append(msg.type)
    assert at_tick[480] == ['note_off', 'note_on']


@pytest.mark.parametrize('notes,error', [
    ([(0, None, 52, 100)], 'Missing'),
    ([(0, 120, 52, 100), (0, 120, 56, 100)], 'root\\+7'),
    ([(0, 120, 52, 100), (0, 120, 59, 100), (0, 120, 64, 100)], 'root\\+7'),
    ([(0, 120, 52, 100), (0, 240, 59, 100)], 'share'),
    ([(0, 120, 52, 100), (0, 120, 59, 99)], 'share'),
    ([(0, 240, 52, 100), (120, 360, 53, 100)], 'overlap'),
    ([(0, 240, 52, 100), (120, 360, 52, 100)], 'duplicate'),
    ([(0, 0, 52, 100)], 'Unmatched'),
    ([], 'channel'),
])
def test_rejects_bad_pairing_chords_and_overlap_without_export(tmp_path, notes, error):
    source = source_midi(tmp_path / 'bad.mid', notes); output = tmp_path / 'none.mid'
    with pytest.raises(ValueError, match=error):
        generate(request(source), output_path=output)
    assert not output.exists()


@pytest.mark.parametrize('msg,error', [
    (mido.Message('note_off', note=53), 'Unmatched'),
    (mido.Message('pitchwheel', pitch=1), 'Unsupported'),
    (mido.Message('control_change', control=64, value=127), 'Unsupported'),
    (mido.Message('sysex', data=(1, 2)), 'Unsupported'),
    (mido.MetaMessage('set_tempo', tempo=600000), 'constant tempo'),
    (mido.MetaMessage('time_signature', numerator=3, denominator=4), '4/4'),
])
def test_rejects_unsupported_performance_and_changing_context(tmp_path, msg, error):
    source = source_midi(tmp_path / 'guitar.mid', extra=[(960, 1, msg)])
    with pytest.raises(ValueError, match=error):
        generate(request(source), output_path=tmp_path / 'bass.mid')


@pytest.mark.parametrize('change', ['multi_channel', 'drum_channel', 'type2', 'missing_tempo', 'missing_meter'])
def test_rejects_ambiguous_input_or_missing_context(tmp_path, change):
    source = source_midi(tmp_path / 'guitar.mid'); midi = mido.MidiFile(source)
    if change == 'multi_channel':
        midi.tracks[0].insert(0, mido.Message('note_on', note=48, channel=1))
    elif change == 'drum_channel':
        for msg in midi.tracks[0]:
            if hasattr(msg, 'channel'):msg.channel = 9
    elif change == 'type2':midi.type = 2
    else:
        typ = 'set_tempo' if change == 'missing_tempo' else 'time_signature'
        midi.tracks[0] = mido.MidiTrack(m for m in midi.tracks[0] if m.type != typ)
    midi.save(source)
    with pytest.raises(ValueError):generate(request(source), output_path=tmp_path / 'bass.mid')


@pytest.mark.parametrize('option,value', [('transpose_semitones', True), ('transpose_semitones', 1.5),
    ('transpose_semitones', -128), ('program', True), ('program', 0), ('program', 129),
    ('midi_channel', 10), ('midi_channel', True), ('guitar_midi', ''), ('unexpected', 1)])
def test_options_validated(tmp_path, option, value):
    source = source_midi(tmp_path / 'guitar.mid')
    data = request(source); data['options'][option] = value
    with pytest.raises(ValueError):generate(data, output_path=tmp_path / 'bass.mid')


@pytest.mark.parametrize('missing', ['guitar_midi', 'transpose_semitones', 'program'])
def test_explicit_options_required(tmp_path, missing):
    data = request(source_midi(tmp_path / 'guitar.mid')); del data['options'][missing]
    with pytest.raises(ValueError, match='Missing bass options'):
        generate(data, output_path=tmp_path / 'bass.mid')


@pytest.mark.parametrize('notes,shift', [([(0, 120, 0, 100)], -1), ([(0, 120, 127, 100)], 1)])
def test_transposed_pitch_range(tmp_path, notes, shift):
    source = source_midi(tmp_path / 'guitar.mid', notes)
    with pytest.raises(ValueError, match='transposed MIDI note'):
        generate(request(source, shift), output_path=tmp_path / 'bass.mid')


def test_source_existing_output_and_context_protection(tmp_path):
    source = source_midi(tmp_path / 'guitar.mid'); original = source.read_bytes()
    with pytest.raises(ValueError, match='different paths'):generate(request(source), output_path=source)
    output = tmp_path / 'existing.mid'; output.write_bytes(b'preserve')
    with pytest.raises(FileExistsError):generate(request(source), output_path=output)
    assert output.read_bytes() == b'preserve' and source.read_bytes() == original
    for key, value in [('tempo', 100), ('bars', 2), ('time_signature', [3, 4]), ('chords', ['E'])]:
        data = request(source); data[key] = value
        with pytest.raises(ValueError):generate(data, output_path=tmp_path / 'none.mid')
    with pytest.raises(ValueError, match='output_path'):generate(request(source))
    corrupt = tmp_path / 'invalid.mid'; corrupt.write_bytes(b'not a MIDI')
    with pytest.raises(ValueError, match='Could not read'):generate(request(corrupt), output_path=tmp_path / 'none.mid')


def test_cli_and_api_share_core_and_relative_project_paths(tmp_path):
    source_midi(tmp_path / 'guitar.mid')
    data = request(Path('guitar.mid')); config = tmp_path / 'request.json'; config.write_text(json.dumps(data))
    via_api = tmp_path / 'api.mid'; via_cli = tmp_path / 'cli.mid'
    generate(GenerationRequest.from_dict(data), output_path='api.mid', project_root=tmp_path)
    result = subprocess.run([sys.executable, '-m', 'midi_generator.cli', 'generate', '--config', str(config),
                             '--project-root', str(tmp_path), '--output', 'cli.mid'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['metadata']['attack_events'] == 3
    assert via_api.read_bytes() == via_cli.read_bytes()


def test_adopted_step7_B_sixty_events_and_six_long_gates(tmp_path):
    source = Path('output/phase74_step7_groove_105bpm_8bars/guitar_B_contrast_105bpm_8bars.mid')
    # Local regression asset is not required for source distributions.
    if not source.is_file():pytest.skip('Local audition asset is not distributed')
    guitar, original = rows(source)
    roots = [n for n in original if n[2] == 52]
    assert len(roots) == 60
    for shift in (-12, -24):
        data = request(source, shift); data['bars'] = 8
        output = tmp_path / f'bass{shift}.mid'; generate(data, output_path=output)
        bass, notes = rows(output)
        assert notes == [(s, e, p + shift, v, 2) for s, e, p, v, _ in roots]
        assert Counter(e - s for s, e, *_ in notes) == {120: 54, 240: 6}
        assert bass['end_tick'] == guitar['end_tick'] == 15360
