"""Create Step 7 A/B/C auditions and record every gate/onset change.

Usage: python scripts/create_groove_backing_auditions.py DRUMS.mid NEW_DIRECTORY
"""
import argparse
import json
from pathlib import Path

import mido

from create_power_chord_auditions import note_rows, save_new, sha
from midi_generator.api import BackingRules, extract_rhythm_blueprint, generate_backing_phrase, render_backing_midi
from midi_generator.modules.guitar.sync import load_sync_config


def render_phrase(bars, bpm, tpb):
    midi = mido.MidiFile(type=0, ticks_per_beat=tpb)
    track = mido.MidiTrack(); midi.tracks.append(track)
    expected = []
    for bar_index, bar in enumerate(bars):
        clip = render_backing_midi(bar, export_bpm=bpm, ticks_per_beat=tpb, program=31)
        for msg in clip.tracks[0]:
            track.append(mido.MetaMessage('marker', text='bar end', time=msg.time)
                         if msg.type == 'end_of_track' else msg.copy())
        for step in bar:
            start = (bar_index * 16 + step.step) * tpb // 4
            end = start + int(step.duration_steps * tpb / 4)
            expected.extend((start, end, pitch, step.velocity, 1) for pitch in step.notes)
    track.append(mido.MetaMessage('end_of_track'))
    # Strict pairing/order check, including adjacent retriggers and bar ends.
    tick = 0; active = set()
    for msg in track:
        tick += msg.time
        if msg.type == 'note_on' and msg.velocity:
            assert msg.note not in active
            active.add(msg.note)
        elif msg.type == 'note_off':
            assert msg.note in active
            active.remove(msg.note)
        elif msg.type == 'marker':
            assert tick % (4 * tpb) == 0 and not active
    assert not active and tick == len(bars) * 4 * tpb
    return midi, sorted(expected)


def stats(path, root, drums, blueprints):
    data, rows = note_rows(path)
    roots = [r for r in rows if r[2] == root and r[4] == 1]
    groups = {}
    for start, end, pitch, velocity, channel in rows:
        if channel == 1:
            groups.setdefault(start, []).append((end, pitch, velocity))
    for group in groups.values():
        assert sorted(n[1] for n in group) in ([root], [root, root + 7])
        assert len({(n[0], n[2]) for n in group}) == 1
    tpb = data['midi'].ticks_per_beat
    kick_notes = load_sync_config()['kick_notes']
    exact_kicks = {n['start_tick'] for n in drums['notes'] if n['channel'] == 10 and n['note'] in kick_notes}
    per_bar = []
    for i, bp in enumerate(blueprints):
        local = [((start - i * 4 * tpb) * 4 // tpb, (end - start) * 4 / tpb, velocity)
                 for start, end, _, velocity, _ in roots if i * 4 * tpb <= start < (i + 1) * 4 * tpb]
        positions = {s for s, _, _ in local}
        snare_space = {}
        for snare in bp.snare_steps:
            earlier = [(s, length) for s, length, _ in local if s < snare]
            snare_space[str(snare)] = snare - max((s + length for s, length in earlier), default=0)
        per_bar.append({'bar': i + 1, 'kick_steps': list(bp.kick_steps), 'snare_steps': list(bp.snare_steps),
                        'guitar_steps': sorted(positions), 'kick_matches': sorted(positions & set(bp.kick_steps)),
                        'nonkick_offbeats': sorted(s for s in positions if s % 4 and s not in bp.kick_steps),
                        'pre_snare_silence_steps': snare_space,
                        'tail_silence_steps': 16 - max(s + length for s, length, _ in local)})
    chords = sum(len(g) == 2 for g in groups.values())
    long_count = sum(end - start == tpb / 2 for start, end, *_ in roots)
    return {'attack_events': len(groups), 'power5_events': chords, 'power5_ratio': chords / len(groups),
            'long_events': long_count, 'long_ratio': long_count / len(groups),
            'kick_matches_exact_ticks': sum(start in exact_kicks for start in groups),
            'kick_onsets_exact_ticks': len(exact_kicks),
            'per_bar': per_bar}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('drums', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--baseline-directory', type=Path, default=Path('output/archive/phase7/phase74_step6_phrase_105bpm_8bars'))
    args = parser.parse_args()
    drums_data, drum_rows = note_rows(args.drums)
    drums = drums_data['midi']; tpb = drums.ticks_per_beat
    assert drums_data['end_tick'] == 32 * tpb
    assert drums_data['context']['tempo_events'][0]['microseconds_per_beat'] == mido.bpm2tempo(105)
    assert {r[4] for r in drum_rows} == {10}
    blueprints = [extract_rhythm_blueprint(args.drums, bar_index=i) for i in range(8)]
    bp = blueprints[0]
    assert all((b.kick_steps, b.snare_steps, b.accent_steps) == (bp.kick_steps, bp.snare_steps, bp.accent_steps) for b in blueprints)
    settings = BackingRules(rhythm_mode='phrase4', voicing='mixed', power_chord_ratio=.75, duration_steps=1)
    variants = {
        'A': generate_backing_phrase(bp, note=52, rules=settings),
        'B': generate_backing_phrase(bp, note=52, rules=settings, duration_mode='contrast'),
        'C': generate_backing_phrase(bp, note=52, rules=settings, duration_mode='contrast', drum_mode='ending_space'),
    }
    changes = {'duration': [], 'onset': []}
    for i, (a, b, c) in enumerate(zip(variants['A'], variants['B'], variants['C'])):
        assert [(s.step, s.notes, s.velocity) for s in a] == [(s.step, s.notes, s.velocity) for s in b]
        b_by_step = {s.step: s for s in b}
        c_by_step = {s.step: s for s in c}
        for old, new in zip(a, b):
            if old.duration_steps != new.duration_steps:
                changes['duration'].append({'bar': i + 1, 'step': old.step, 'old_beats': old.duration_steps / 4,
                    'new_beats': new.duration_steps / 4,
                    'reason': 'Before a structural pause or bar end; retain at least one silent sixteenth.'})
                stop = next((s.step for s in a if s.step > old.step), 16)
                assert new.step + new.duration_steps <= stop - 1
        for step in sorted(set(b_by_step) - set(c_by_step)):
            removed = b_by_step[step]
            assert step not in bp.kick_steps and i % 4 == 3
            changes['onset'].append({'bar': i + 1, 'step': step, 'action': 'remove',
                'notes': list(removed.notes), 'velocity': removed.velocity,
                'reason': 'Ending-only non-kick chug before snare: widen backbeat space; preserve kick coverage and final offbeat pickup.'})
        assert all(s == b_by_step[s.step] for s in c)
        assert set(c_by_step) <= set(b_by_step)
    protected = {p: sha(p) for root in (Path('data/sources'), Path('output')) for p in root.rglob('*.mid')}
    source_hash = sha(args.drums)
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'source': str(args.drums), 'source_sha256': source_hash,
              'provenance': 'Existing generated drum tracks copied exactly; no external guitar MIDI. Original usage/provenance notes remain at source directory.',
              'settings': {'bpm': 105, 'meter': '4/4', 'bars': 8, 'root': 52, 'fifth': 59,
                           'voicing': 'mixed', 'power_chord_ratio': .75, 'base_beats': .25,
                           'long_note_ratio_target': .15, 'rhythm_mode': 'phrase4'},
              'hypothesis': '10–20% long gates is an initial experiment, limited by rest-safe candidates. C tests backbeat space; kick matches already cover all source kicks, so no kick-match improvement is claimed.',
              'changes': changes, 'variants': {}, 'files': {}}
    names = {'A': 'step6', 'B': 'contrast', 'C': 'ending_space'}
    for letter, bars in variants.items():
        guitar, expected = render_phrase(bars, 105, tpb)
        # In-process deterministic MIDI regeneration.
        again, _ = render_phrase(bars, 105, tpb)
        from io import BytesIO
        first_stream = BytesIO(); second_stream = BytesIO()
        guitar.save(file=first_stream); again.save(file=second_stream)
        assert first_stream.getvalue() == second_stream.getvalue()
        name = f'{letter}_{names[letter]}_105bpm_8bars'
        guitar_path = args.output / f'guitar_{name}.mid'
        save_new(guitar, guitar_path)
        guitar_data, rows = note_rows(guitar_path)
        assert rows == expected and guitar_data['end_tick'] == 32 * tpb
        assert all(start % (tpb // 4) == 0 and end - start in (tpb // 4, tpb // 2) for start, end, *_ in rows)
        combined = mido.MidiFile(type=1, ticks_per_beat=tpb)
        combined.tracks.extend(mido.MidiTrack(m.copy() for m in t) for t in drums.tracks)
        combined.tracks.append(mido.MidiTrack(m.copy() for m in guitar.tracks[0]))
        pair_path = args.output / f'drums_guitar_{name}.mid'
        save_new(combined, pair_path)
        reloaded = mido.MidiFile(pair_path)
        assert reloaded.tracks[:-1] == drums.tracks
        pair_data, pair_rows = note_rows(pair_path)
        assert pair_rows == sorted(drum_rows + expected) and pair_data['end_tick'] == 32 * tpb
        if letter == 'A':
            for prefix, path in (('guitar', guitar_path), ('drums_guitar', pair_path)):
                baseline = args.baseline_directory / f'{prefix}_B_phrase4_power75_0p25beats_8bars.mid'
                assert path.read_bytes() == baseline.read_bytes()
        report['variants'][letter] = stats(guitar_path, 52, drums_data, blueprints)
        for path in (guitar_path, pair_path):
            report['files'][path.name] = {'sha256': sha(path), 'verified': True}
    assert sha(args.drums) == source_hash and all(sha(p) == digest for p, digest in protected.items())
    assert all(v['kick_matches_exact_ticks'] == v['kick_onsets_exact_ticks'] for v in report['variants'].values())
    assert .10 <= report['variants']['B']['long_ratio'] <= .20
    assert .10 <= report['variants']['C']['long_ratio'] <= .20
    report['checks'] = {'A_equals_step6_bytes': True, 'B_same_onsets_voicing_velocity_as_A': True,
                        'C_surviving_event_identity_preserved': True, 'drums_all_messages_identical': True,
                        'note_pairing_retrigger_and_bar_boundaries': True, 'existing_midi_hashes_unchanged': True,
                        'protected_midi_files': len(protected)}
    (args.output / 'verification.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    lines = ['# Step 7 groove comparison', '',
             'Positions below are zero-based sixteenth steps; divide by four for beats.', '',
             '| Variant | Attacks | Power5 | Power5 % | Long notes | Long % | Exact kick matches |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for letter, v in report['variants'].items():
        lines.append(f"| {letter} | {v['attack_events']} | {v['power5_events']} | {v['power5_ratio']*100:.2f} | {v['long_events']} | {v['long_ratio']*100:.2f} | {v['kick_matches_exact_ticks']}/{v['kick_onsets_exact_ticks']} |")
    lines.extend(['', '## Gate changes (A → B; retained in C)', '', '| Bar | Step | Beats |', '|---|---|---|'])
    for row in changes['duration']:
        lines.append(f"| {row['bar']} | {row['step']} | {row['old_beats']} → {row['new_beats']} |")
    lines.extend(['', '## Onset changes (B → C)', '', '| Bar | Step | Change |', '|---|---|---|'])
    for row in changes['onset']:
        lines.append(f"| {row['bar']} | {row['step']} | Remove non-kick pre-snare chug |")
    lines.extend(['', 'C preserves all kick coincidences and the final offbeat pickup. It does not increase kick matches.',
        'On ending bars C increases pre-snare silence from 1 to 3 sixteenths (0.25 → 0.75 beats).',
        'Short chugs stay 0.25 beats. Long gates consume one cell of a larger gap, never the entire pause;',
        'at least one silent sixteenth remains. Large pauses and bar-end stops remain audible.',
        'Voicings and velocities are not reassigned in C. Removing two power5 events changes the measured ratio slightly.',
        'Original snare onsets are slightly off grid (e.g. tick 958 vs guitar 960 at 480 TPB);',
        'grid-based backbeat observations use the existing extractor tolerance. Drum timing is never quantized.',
        'These are listening hypotheses, not verified improvements. Full per-bar sync/space data is in verification.json.',
        '', 'guitar_*.mid = guitar only; drums_guitar_*.mid = original drums + guitar.'])
    (args.output / 'comparison.md').write_text('\n'.join(lines) + '\n')
    print(f'Created and verified six MIDI files in {args.output}')


if __name__ == '__main__':
    main()
