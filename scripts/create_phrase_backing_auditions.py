"""Create repeat/phrase4 rhythm auditions using the shared B core.

Usage: python scripts/create_phrase_backing_auditions.py DRUMS.mid NEW_DIRECTORY
"""
import argparse
import json
from pathlib import Path

import mido

from create_power_chord_auditions import note_rows, save_new, sha
from midi_generator.api import BackingRules, extract_rhythm_blueprint, generate_backing_phrase, render_backing_midi


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('drums', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--bpm', type=float, default=105)
    parser.add_argument('--root', type=int, default=52)
    parser.add_argument('--bars', type=int, default=8)
    args = parser.parse_args()
    drums, drum_rows = note_rows(args.drums)
    tpb = drums['midi'].ticks_per_beat
    assert args.bars > 0 and drums['end_tick'] == args.bars * 4 * tpb
    assert {r[4] for r in drum_rows} == {10}
    assert drums['context']['tempo_events'][0]['microseconds_per_beat'] == mido.bpm2tempo(args.bpm)
    blueprint = extract_rhythm_blueprint(args.drums, bar_index=0)
    protected = {p: sha(p) for root in (Path('data/sources'), Path('output')) for p in root.rglob('*.mid')}
    source_hash = sha(args.drums)
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'source': str(args.drums), 'source_sha256': source_hash, 'bpm': args.bpm,
              'bars': args.bars, 'root': args.root, 'duration_beats': .25,
              'provenance': 'Existing generated drums reused without editing; no external guitar MIDI. Original provenance/usage notes remain in source directory.',
              'count_policy': 'Nearest integer, ties up, per bar; denominator is attack events, not MIDI note count.',
              'comparison': [], 'files': {}}
    baseline = None
    for letter, mode in (('A', 'repeat'), ('B', 'phrase4')):
        ratio = .75
        guitar = mido.MidiFile(type=0, ticks_per_beat=tpb)
        track = mido.MidiTrack(); guitar.tracks.append(track)
        expected = []; placements = []
        bars = generate_backing_phrase(blueprint, note=args.root, bars=args.bars,
            rules=BackingRules(voicing='mixed', power_chord_ratio=ratio, duration_steps=1, rhythm_mode=mode))
        for bar, steps in enumerate(bars):
            clip = render_backing_midi(steps, export_bpm=args.bpm, ticks_per_beat=tpb, program=31)
            for msg in clip.tracks[0]:
                track.append(mido.MetaMessage('marker', text='bar end', time=msg.time)
                             if msg.type == 'end_of_track' else msg.copy())
            for step in steps:
                start = (bar * 4 + step.step / 4) * tpb
                end = start + step.duration_steps * tpb / 4
                expected.extend((int(start), int(end), pitch, step.velocity, 1) for pitch in step.notes)
            placements.append({'bar': bar + 1, 'events': len(steps), 'steps': [s.step for s in steps],
                               'power5_steps': [s.step for s in steps if s.voicing == 'power5']})
        track.append(mido.MetaMessage('end_of_track'))
        name = f'{letter}_{mode}_power75_0p25beats_8bars'
        guitar_path = args.output / f'guitar_{name}.mid'
        save_new(guitar, guitar_path)
        data, rows = note_rows(guitar_path)
        assert rows == sorted(expected) and data['end_tick'] == drums['end_tick']
        # Inspect rendered notes by attack, independently of the step metadata.
        groups = {}
        for start, end, pitch, velocity, channel in rows:
            groups.setdefault(start, []).append((end, pitch, velocity, channel))
        chords = 0
        for group in groups.values():
            assert sorted(n[1] for n in group) in ([args.root], [args.root, args.root + 7])
            assert len({(n[0], n[2], n[3]) for n in group}) == 1
            chords += len(group) == 2
        actual = chords / len(groups)
        assert abs(chords - len(groups) * ratio) <= args.bars * .5
        roots = [r for r in rows if r[2] == args.root]
        if baseline is None:
            baseline = placements
        else:
            for old, new in zip(baseline, placements):
                report['comparison'].append({'bar': old['bar'], 'A': old['steps'], 'B': new['steps'],
                    'removed': sorted(set(old['steps']) - set(new['steps'])),
                    'added': sorted(set(new['steps']) - set(old['steps']))})
        assert all((end - start) == tpb / 4 for start, end, *_ in rows)
        assert all(start % (tpb // 4) == 0 for start, *_ in rows)
        active = set()
        for msg in track:
            if msg.type == 'note_on' and msg.velocity:
                assert msg.note not in active; active.add(msg.note)
            elif msg.type == 'note_off':
                assert msg.note in active; active.remove(msg.note)
        assert not active
        combined = mido.MidiFile(type=1, ticks_per_beat=tpb)
        combined.tracks.extend(mido.MidiTrack(m.copy() for m in t) for t in drums['midi'].tracks)
        combined.tracks.append(mido.MidiTrack(m.copy() for m in track))
        pair_path = args.output / f'drums_guitar_{name}.mid'
        save_new(combined, pair_path)
        saved_pair = mido.MidiFile(pair_path)
        assert saved_pair.tracks[:-1] == drums['midi'].tracks
        pair_data, pair_rows = note_rows(pair_path)
        assert pair_rows == sorted(drum_rows + rows) and pair_data['end_tick'] == drums['end_tick']
        for path in (guitar_path, pair_path):
            report['files'][path.name] = {'sha256': sha(path), 'attack_events': len(groups),
                'power5_events': chords, 'single_events': len(groups) - chords,
                'actual_ratio': actual, 'placement': placements}
    assert sha(args.drums) == source_hash and all(sha(p) == digest for p, digest in protected.items())
    report['checks'] = {'pitches_pairing_onsets_gates_velocities': True, 'same_drum_tracks': True, 'sixteenth_grid_and_quarter_beat_gates': True,
                        'existing_midi_unchanged': True, 'protected_midi_files': len(protected)}
    table = ['# Rhythm comparison (0-based sixteenth steps)', '',
             '| Bar | A: repeat | B: phrase4 | Removed | Added |', '|---|---|---|---|---|']
    for row in report['comparison']:
        table.append(f"| {row['bar']} | {row['A']} | {row['B']} | {row['removed']} | {row['added']} |")
    (args.output / 'comparison.md').write_text('\n'.join(table) + '\n')
    (args.output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    (args.output / 'README.txt').write_text(
        f'{args.bpm:g} BPM / {args.bars} bars / root {args.root} / requested gate 0.25 beats.\n'
        'A=repeat, B=phrase4; both mixed / power_chord_ratio=0.75 (rounded per bar).\n'
        'Identical drums; only rhythm structure changes. Original event velocities retained on moved events.\n'
        'B repeats A / small variation / A / ending twice. Gates remain 0.25 beats.\n'
        'guitar_*.mid: guitar only; drums_guitar_*.mid: drums + guitar.\n'
        'comparison.md: onset changes; verification.json: measured ratios, checks, hashes and provenance.\n')
    print(f'Created and verified four MIDI files in {args.output}')


if __name__ == '__main__':
    main()
