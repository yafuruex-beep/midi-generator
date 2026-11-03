"""Create/verify E2/E1 bass comparisons without changing adopted drum/guitar tracks.

Usage: python scripts/create_bass_follow_auditions.py REQUEST.json DRUMS_GUITAR.mid NEW_DIRECTORY
"""
from collections import Counter, defaultdict
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path

import mido

from midi_generator.api import generate
from midi_generator.core.midi_io import load_midi, save_midi
from midi_generator.modules.guitar.timeline import read_timeline


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_notes(path):
    data = read_timeline(path, set())
    assert data['pairing']['unmatched_note_offs'] == data['pairing']['inferred_note_ends'] == 0
    rows = [(n['start_tick'], n['end_tick'], n['note'], n['velocity'], n['channel']) for n in data['notes']]
    return data, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path)
    parser.add_argument('drums_guitar', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    config = json.loads(args.request.read_text())
    assert config['tempo'] == 105 and config['bars'] == 8 and config['time_signature'] == [4, 4]
    source = Path(config['options']['guitar_midi'])
    guitar, guitar_rows = checked_notes(source)
    pair = load_midi(args.drums_guitar)
    assert pair.ticks_per_beat == guitar['midi'].ticks_per_beat == 480
    assert pair.tracks[-1:] == guitar['midi'].tracks
    assert {m.channel + 1 for t in pair.tracks[:-1] for m in t if hasattr(m, 'channel')} == {10}
    groups = defaultdict(list)
    for row in guitar_rows:
        groups[row[0]].append(row)
    roots = [min(group, key=lambda n: n[2]) for _, group in sorted(groups.items())]
    assert len(roots) == 60 and {r[2] for r in roots} == {52}
    assert guitar['end_tick'] == 15360
    protected = {p: sha(p) for root in (Path('data/sources'), Path('output')) for p in root.rglob('*.mid')}
    source_hash, pair_hash = sha(source), sha(args.drums_guitar)
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'guitar_source': str(source), 'guitar_sha256': source_hash,
              'drums_guitar_source': str(args.drums_guitar), 'drums_guitar_sha256': pair_hash,
              'program': config['options']['program'], 'midi_channel': 2,
              'audio_playable_range_verified': False,
              'provenance': 'Derived from adopted generated Step 7-B; original drum/guitar provenance and usage notes remain at source directory.',
              'versions': {}, 'files': {}}
    for label, shift, note in (('A1', -12, 40), ('A2', -24, 28)):
        request = deepcopy(config); request['options']['transpose_semitones'] = shift
        bass_path = args.output / f'bass_{label}_105bpm_8bars.mid'
        result = generate(request, output_path=bass_path)
        data, rows = checked_notes(bass_path)
        expected = [(s, e, p + shift, v, 2) for s, e, p, v, _ in roots]
        assert rows == expected and len(rows) == 60 and {r[2] for r in rows} == {note}
        assert Counter(e - s for s, e, *_ in rows) == {120: 54, 240: 6}
        assert all(a[1] <= b[0] for a, b in zip(rows, rows[1:]))
        assert data['end_tick'] == 15360
        programs = [m for t in data['midi'].tracks for m in t if m.type == 'program_change']
        assert len(programs) == 1 and programs[0].program == request['options']['program'] - 1 and programs[0].channel == 1
        active = set(); tick = 0
        for message in data['midi'].tracks[0]:
            tick += message.time
            if message.type == 'note_on' and message.velocity:
                assert not active
                active.add(message.note)
            elif message.type == 'note_off':
                assert message.note in active
                active.remove(message.note)
        assert not active and tick == 15360
        combined = mido.MidiFile(type=1, ticks_per_beat=480)
        combined.tracks.extend(mido.MidiTrack(m.copy() for m in t) for t in pair.tracks)
        combined.tracks.extend(mido.MidiTrack(m.copy() for m in t) for t in data['midi'].tracks)
        combo_path = args.output / f'drums_guitar_bass_{label}_105bpm_8bars.mid'
        save_midi(combined, combo_path)
        loaded = load_midi(combo_path)
        assert loaded.tracks[:-1] == pair.tracks
        combo_data, combo_rows = checked_notes(combo_path)
        assert [r for r in combo_rows if r[4] == 2] == expected
        assert combo_data['end_tick'] == 15360
        (args.output / f'{label}_request.json').write_text(json.dumps(request, indent=2) + '\n')
        report['versions'][label] = {**result.metadata, 'long_events': 6, 'monophonic': True,
            'onsets_ends_rests_velocities_identical': True, 'original_drum_guitar_tracks_identical': True}
        for path in (bass_path, combo_path):
            report['files'][path.name] = sha(path)
    assert sha(source) == source_hash and sha(args.drums_guitar) == pair_hash
    assert all(sha(path) == digest for path, digest in protected.items())
    report['checks'] = {'all_existing_midi_hashes_unchanged': True, 'protected_midi_files': len(protected)}
    (args.output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'Created and verified four MIDI files in {args.output}')


if __name__ == '__main__':
    main()
