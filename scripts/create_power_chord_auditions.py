"""Create and mechanically verify new audition clips without altering inputs.

Assembly only: every bar goes through the shared B step generator and renderer.
Usage: python scripts/create_power_chord_auditions.py DRUMS.mid NEW_DIRECTORY
"""
import argparse
import hashlib
import json
from pathlib import Path

import mido

from midi_generator.api import BackingRules, extract_rhythm_blueprint, generate_backing_steps, render_backing_midi
from midi_generator.modules.guitar.timeline import read_timeline


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_new(midi, path):
    with path.open('xb') as stream:
        midi.save(file=stream)


def note_rows(path):
    data = read_timeline(path, set())
    assert data['pairing']['unmatched_note_offs'] == 0
    assert data['pairing']['inferred_note_ends'] == 0
    return data, sorted((n['start_tick'], n['end_tick'], n['note'], n['velocity'], n['channel']) for n in data['notes'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('drums', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--bpm', type=float, default=105)
    parser.add_argument('--root', type=int, default=52)
    parser.add_argument('--bars', type=int, default=8)
    args = parser.parse_args()
    source_hash = sha(args.drums)
    drums, drum_rows = note_rows(args.drums)
    assert args.bars > 0 and drums['end_tick'] == args.bars * 4 * drums['midi'].ticks_per_beat
    assert {r[4] for r in drum_rows} == {10}
    assert drums['context']['tempo_events'][0]['microseconds_per_beat'] == mido.bpm2tempo(args.bpm)
    blueprints = [extract_rhythm_blueprint(args.drums, bar_index=i) for i in range(args.bars)]
    # Snapshot existing MIDI assets before exclusive creation of the new directory.
    protected = {p: sha(p) for root in (Path('data/sources'), Path('output')) for p in root.rglob('*.mid')}
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'drums_8bars.mid').write_bytes(args.drums.read_bytes())
    report = {'source': str(args.drums), 'source_sha256': source_hash,
              'bpm': args.bpm, 'bars': args.bars, 'root': args.root,
              'provenance': 'Existing generated drum MIDI reused unchanged; no external guitar MIDI used. See source directory for original provenance/usage notes.',
              'files': {}}
    for duration, label in ((1, '0p25'), (2, '0p5')):
        baseline = None
        for letter, voicing, intervals in (('A', 'single', (0,)), ('B', 'power5', (0, 7)), ('C', 'power8', (0, 7, 12))):
            guitar = mido.MidiFile(type=0, ticks_per_beat=drums['midi'].ticks_per_beat)
            track = mido.MidiTrack(); guitar.tracks.append(track)
            expected = []
            for bar, bp in enumerate(blueprints):
                steps = generate_backing_steps(bp, note=args.root, rules=BackingRules(duration_steps=duration, voicing=voicing))
                clip = render_backing_midi(steps, export_bpm=args.bpm, ticks_per_beat=guitar.ticks_per_beat, program=31)
                # Keep the end-of-bar delta but remove intermediate EOT markers.
                for msg in clip.tracks[0]:
                    if msg.type == 'end_of_track':
                        track.append(mido.MetaMessage('marker', text='bar end', time=msg.time))
                    else:
                        track.append(msg.copy())
                for step in steps:
                    start = (bar * 4 + step.step / 4) * guitar.ticks_per_beat
                    end = start + step.duration_steps * guitar.ticks_per_beat / 4
                    expected.extend((int(start), int(end), args.root + i, step.velocity, 1) for i in intervals)
            track.append(mido.MetaMessage('end_of_track'))
            name = f'{letter}_{voicing}_{label}beats_8bars'
            guitar_path = args.output / f'guitar_{name}.mid'
            save_new(guitar, guitar_path)
            data, rows = note_rows(guitar_path)
            assert rows == sorted(expected)
            assert data['end_tick'] == drums['end_tick']
            roots = [r for r in rows if r[2] == args.root]
            if baseline is None:
                baseline = roots
            assert roots == baseline
            # Strict stream pairing also verifies Off precedes repeated On.
            active = set()
            for msg in track:
                if msg.type == 'note_on' and msg.velocity:
                    assert msg.note not in active
                    active.add(msg.note)
                elif msg.type == 'note_off':
                    assert msg.note in active
                    active.remove(msg.note)
            assert not active
            combined = mido.MidiFile(type=1, ticks_per_beat=guitar.ticks_per_beat)
            combined.tracks.extend(mido.MidiTrack(m.copy() for m in t) for t in drums['midi'].tracks)
            combined.tracks.append(mido.MidiTrack(m.copy() for m in track))
            pair_path = args.output / f'drums_guitar_{name}.mid'
            save_new(combined, pair_path)
            pair_data, pair_rows = note_rows(pair_path)
            assert pair_rows == sorted(drum_rows + rows)
            assert pair_data['end_tick'] == drums['end_tick']
            for path in (guitar_path, pair_path):
                report['files'][path.name] = {'sha256': sha(path), 'guitar_notes': len(rows), 'attacks': len(roots),
                                            'chord_notes': [args.root + i for i in intervals], 'verified': True}
    assert sha(args.drums) == source_hash
    assert all(sha(p) == digest for p, digest in protected.items())
    report['checks'] = {'chord_pitches_onsets_gates_velocities': True, 'note_pairing': True,
                        'same_drums_all_variants': True, 'same_rhythm_across_voicings': True,
                        'existing_source_and_output_midi_hashes_unchanged': True,
                        'protected_midi_files': len(protected)}
    (args.output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    (args.output / 'README.txt').write_text(
        '105 BPM / 8 bars / root 52 / GM Distortion Guitar (program 31).\n'
        'A=single (52), B=power5 (52,59), C=power8 (52,59,64).\n'
        'Compare A/B/C within each 0p25 or 0p5 beats group. Gates are capped at next onset/bar end.\n'
        'drums_guitar_*.mid includes identical drums; guitar_*.mid is guitar only.\n'
        'No humanization or new velocity processing. Existing rule accents preserved.\n'
        'verification.json records mechanical checks and source provenance.\n')
    print(f'Created and verified {len(report["files"])} MIDI files plus unchanged drum copy in {args.output}')


if __name__ == '__main__':
    main()
