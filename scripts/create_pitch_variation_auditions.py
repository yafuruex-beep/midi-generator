"""Opt-in, config-driven pitch-only auditions. Existing generators are untouched."""
from collections import defaultdict
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path

import mido
from midi_generator.api import generate


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def groups(midi):
    """Exact-tick groups with original message locations; reject broken voices."""
    events = []
    ends = []
    for track_index, track in enumerate(midi.tracks):
        tick = 0
        for index, message in enumerate(track):
            if type(message.time) is not int or message.time < 0:
                raise ValueError('Expected nonnegative integer delta ticks')
            tick += message.time
            if message.type in {'note_on', 'note_off'}:
                off = message.type == 'note_off' or message.velocity == 0
                events.append((tick, 0 if off else 1, track_index, index, message))
        ends.append(tick)
    pending = {}; rows = []
    for tick, off_priority, ti, index, message in sorted(events, key=lambda e: e[:4]):
        key = message.channel, message.note
        if off_priority == 0:
            if key not in pending:
                raise ValueError('Unmatched Note Off')
            start, velocity, on_location = pending.pop(key)
            if tick <= start:
                raise ValueError('Nonpositive note gate')
            rows.append({'start': start, 'end': tick, 'pitch': message.note,
                         'velocity': velocity, 'channel': message.channel,
                         'on': on_location, 'off': (ti, index)})
        else:
            if key in pending:
                raise ValueError('Overlapping duplicate note')
            pending[key] = tick, message.velocity, (ti, index)
    if pending:
        raise ValueError('Missing Note Off')
    by_start = defaultdict(list)
    for row in rows:
        by_start[row['start']].append(row)
    result = []
    previous_end = 0
    for tick, members in sorted(by_start.items()):
        members.sort(key=lambda n: n['pitch'])
        if tick < previous_end:
            raise ValueError('Attack groups overlap')
        if len({(n['end'], n['velocity'], n['channel']) for n in members}) != 1:
            raise ValueError('Chord gates, velocities and channels must agree')
        root = members[0]['pitch']
        intervals = tuple(n['pitch'] - root for n in members)
        if intervals not in {(0,), (0, 7)}:
            raise ValueError('This experiment requires baseline single/root+7 groups')
        result.append({'start': tick, 'end': members[0]['end'], 'root': root,
                       'velocity': members[0]['velocity'], 'channel': members[0]['channel'],
                       'intervals': intervals, 'members': members})
        previous_end = members[0]['end']
    if not result:
        raise ValueError('No note groups')
    return result, max(ends)


def rhythm_signature(midi):
    events, end = groups(midi)
    return (midi.ticks_per_beat, end, tuple((g['start'], g['end'], g['velocity'],
            g['channel'], g['intervals']) for g in events))


def transpose_groups(midi, roots, *, allowed_roots, guitar_range, bass_range, max_jump):
    source, end = groups(midi)
    if len(roots) != len(source):
        raise ValueError('One root required per original attack group')
    if type(max_jump) is not int or max_jump < 0:
        raise ValueError('max_jump must be a nonnegative integer')
    result = deepcopy(midi)
    for index, (group, root) in enumerate(zip(source, roots)):
        if type(root) is not int or root not in allowed_roots:
            raise ValueError('Root is not an explicitly allowed MIDI note')
        if index and abs(root - roots[index - 1]) > max_jump:
            raise ValueError('Adjacent root jump exceeds configured limit')
        if not bass_range[0] <= root - 24 <= bass_range[1]:
            raise ValueError('Bass root is outside explicitly configured range')
        shift = root - group['root']
        for row in group['members']:
            new_pitch = row['pitch'] + shift
            if not 0 <= new_pitch <= 127 or not guitar_range[0] <= new_pitch <= guitar_range[1]:
                raise ValueError('Guitar note outside explicitly configured MIDI range')
            for ti, mi in (row['on'], row['off']):
                result.tracks[ti][mi] = result.tracks[ti][mi].copy(note=new_pitch)
    assert rhythm_signature(result) == rhythm_signature(midi)
    # Every non-pitch field, auxiliary event and ordering must remain untouched.
    for old, new in zip(midi.tracks, result.tracks):
        assert len(old) == len(new)
        for a, b in zip(old, new):
            if a.type in {'note_on', 'note_off'}:
                assert a.copy(note=0) == b.copy(note=0)
            else:
                assert a == b
    return result



def retain_bass_context(reference, generated):
    """Use A2's generated pitches while retaining baseline auxiliary messages."""
    before, _ = groups(reference)
    after, _ = groups(generated)
    if rhythm_signature(reference) != rhythm_signature(generated):
        raise ValueError('A2 bass changed a non-pitch performance condition')
    result = deepcopy(reference)
    for old, new in zip(before, after):
        if old['intervals'] != (0,) or new['intervals'] != (0,):
            raise ValueError('Expected monophonic A2 bass')
        for ti, mi in (old['members'][0]['on'], old['members'][0]['off']):
            result.tracks[ti][mi] = result.tracks[ti][mi].copy(note=new['root'])
    return result


def midi_bytes(midi):
    stream = BytesIO(); midi.save(file=stream)
    return stream.getvalue()


def save_new(midi, path):
    with Path(path).open('xb') as stream:
        midi.save(file=stream)


def local_pitch_signature(midi, first_bar, count=2):
    events, _ = groups(midi); length = 4 * midi.ticks_per_beat
    origin = first_bar * length
    return [(g['start'] - origin, g['end'] - g['start'], g['root'], g['intervals'], g['velocity'])
            for g in events if origin <= g['start'] < origin + count * length]


def run(request_path, output, *, project_root='.'):
    root = Path(project_root).resolve()
    request_path = Path(request_path).resolve()
    request = json.loads(request_path.read_text())
    sources = {kind: root / record['path'] for kind, record in request['sources'].items()}
    for kind, path in sources.items():
        if sha(path) != request['sources'][kind]['sha256']:
            raise ValueError(f'Source hash mismatch: {kind}')
    g0 = mido.MidiFile(sources['guitar']); band0 = mido.MidiFile(sources['band'])
    b0 = mido.MidiFile(sources['bass']); drum = mido.MidiFile(sources['drums'])
    source_groups, end = groups(g0)
    assert end == 32 * g0.ticks_per_beat and len(source_groups) == 60
    assert all(g['root'] == request['baseline_root'] for g in source_groups)
    assert band0.tracks == [g0.tracks[0], b0.tracks[0], drum.tracks[0]]
    out = Path(output); out.mkdir(parents=True, exist_ok=False)
    # Shared drum and three candidate-specific drum files are exact byte copies.
    for name in ['drums_shared.mid', 'drums_A.mid', 'drums_B.mid', 'drums_C.mid']:
        with (out / name).open('xb') as stream:
            stream.write(sources['drums'].read_bytes())
    work = out / 'generation_work'; work.mkdir()
    report = {'request': request, 'request_sha256': sha(request_path), 'candidates': {},
              'automatic_adoption': False, 'defaults_changed': False, 'files': {}}
    names = {'A': 'baseline', 'B': 'limited_pitch', 'C': 'expanded_pitch'}
    for label, name in names.items():
        roots = request['root_plans'][label]
        guitar = transpose_groups(g0, roots, allowed_roots=request['allowed_roots'],
                    guitar_range=request['guitar_note_range'], bass_range=request['bass_note_range'],
                    max_jump=request['max_adjacent_root_jump'])
        assert all(msg.note % 12 in request['scale_pitch_classes'] for track in guitar.tracks for msg in track if msg.type in {'note_on','note_off'})
        assert local_pitch_signature(guitar, 0) == local_pitch_signature(guitar, 4)
        gp = out / f'guitar_{label}_{name}.mid'
        save_new(guitar, gp)
        if label == 'A':
            assert gp.read_bytes() == sources['guitar'].read_bytes()
        bp = out / f'bass_{label}_{name}.mid'
        generated_bass = work / f'bass_{label}_generated.mid'
        bass = generate({'instrument': 'bass', 'section': 'chorus', 'tempo': request['export_bpm'],
                         'bars': 8, 'options': {'guitar_midi': str(gp.resolve()),
                         'transpose_semitones': -24, 'program': request['bass_program'], 'midi_channel': 2}},
                         output_path=generated_bass, project_root=root)
        bgroups, bend = groups(mido.MidiFile(generated_bass))
        current_groups, _ = groups(guitar)
        assert bend == end and len(bgroups) == len(current_groups)
        assert [(g['start'],g['end'],g['root'],g['velocity']) for g in bgroups] == [
            (g['start'],g['end'],g['root']-24,g['velocity']) for g in current_groups]
        assert all(g['intervals'] == (0,) and g['channel'] == 1 for g in bgroups)
        final_bass = retain_bass_context(b0, mido.MidiFile(generated_bass))
        band = mido.MidiFile(type=1, ticks_per_beat=guitar.ticks_per_beat)
        band.tracks.extend([deepcopy(guitar.tracks[0]), deepcopy(final_bass.tracks[0]), deepcopy(band0.tracks[2])])
        main = out / f'chorus_{label}_{name}.mid'
        if label == 'A':
            # Preserve the exact Step 3-B band, including bass auxiliary metadata.
            old_bgroups, _ = groups(b0)
            simple = lambda gs: [(g['start'],g['end'],g['root'],g['velocity'],g['intervals'],g['channel']) for g in gs]
            assert simple(old_bgroups) == simple(bgroups)
            with bp.open('xb') as stream:
                stream.write(sources['bass'].read_bytes())
            with main.open('xb') as stream:
                stream.write(sources['band'].read_bytes())
        else:
            save_new(final_bass, bp)
            save_new(band, main)
        loaded = mido.MidiFile(main)
        assert loaded.tracks[2] == band0.tracks[2]
        assert loaded.tracks[0] == guitar.tracks[0]
        assert loaded.tracks[1] == mido.MidiFile(bp).tracks[0]
        # Standalone serialization makes track-byte identity measurable.
        drum_a = mido.MidiFile(type=0, ticks_per_beat=loaded.ticks_per_beat)
        drum_a.tracks.append(deepcopy(loaded.tracks[2]))
        assert midi_bytes(drum_a) == sources['drums'].read_bytes()
        moves = [b-a for a,b in zip(roots,roots[1:])]
        report['candidates'][label] = {'name':name, 'roots':roots,
            'changed_groups_from_A':sum(a!=b['root'] for a,b in zip(roots,source_groups)),
            'moving_transitions':sum(d!=0 for d in moves),'maximum_jump':max(map(abs,moves)),
            'root_histogram':{str(n):roots.count(n) for n in sorted(set(roots))},
            'guitar_groups':len(current_groups),'guitar_notes':sum(len(g['members']) for g in current_groups),
            'bass_exact_minus_24':True,'rhythm_signature_matches_A':True,
            'first_two_bars_return_at_five_six':True,'drum_bytes_match':True,
            'bass_metadata':bass.metadata,
            'per_group':[{'group_index':i,'bar':g['start']//(4*guitar.ticks_per_beat)+1,
               'step':g['start']%(4*guitar.ticks_per_beat)/(guitar.ticks_per_beat/4),
               'root':g['root'],'intervals':list(g['intervals']),'start_tick':g['start'],
               'end_tick':g['end'],'velocity':g['velocity']} for i,g in enumerate(current_groups)]}
        for p in [gp,bp,main]:report['files'][p.name]=sha(p)
    assert report['candidates']['C']['moving_transitions'] > report['candidates']['B']['moving_transitions'] > 0
    assert report['candidates']['C']['changed_groups_from_A'] > report['candidates']['B']['changed_groups_from_A']
    assert sha(out/'chorus_A_baseline.mid') == sha(sources['band'])
    for kind,path in sources.items():assert sha(path)==request['sources'][kind]['sha256']
    report['checks']={'A_band_guitar_bass_byte_identical':True,'all_pitch_independent_messages_identical':True,
                      'rhythm_gates_rests_velocity_counts_intervals_fixed':True,'drums_byte_identical':True,
                      'bass_auxiliary_messages_preserved':True, 'existing_A2_bass_api_used':True,'source_outputs_unchanged':True}
    for p in out.glob('drums*.mid'):report['files'][p.name]=sha(p)
    with (out/'verification.json').open('x') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2);stream.write('\n')
    with (out/'request.json').open('x') as stream:
        json.dump(request,stream,ensure_ascii=False,indent=2);stream.write('\n')
    return report


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('request');parser.add_argument('output');parser.add_argument('--project-root',default='.')
    args=parser.parse_args()
    result=run(args.request,args.output,project_root=args.project_root)
    print({key:{k:v[k] for k in ('changed_groups_from_A','moving_transitions','maximum_jump')} for key,v in result['candidates'].items()})
