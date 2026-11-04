"""Compare a fixed accepted eight-bar guitar motif against the legacy band demo.

No new rhythm algorithm: uses section_motif/build from the original task harness.
Metrics use exact sixteenth onset sets, interval occupancy, and documented Jaccard.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import mido

_SPEC = importlib.util.spec_from_file_location('band_demo', Path(__file__).with_name('run_reference_band_demo.py'))
demo = importlib.util.module_from_spec(_SPEC); _SPEC.loader.exec_module(demo)
TPB = demo.TPB
CELL = TPB // 4


def jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a or b else 1.0


def quiet_runs(active, minimum=2):
    """Major rest = contiguous >=2 silent sixteenths inside an observed bar."""
    result = set(); first = None
    for i in range(len(active) + 1):
        quiet = i < len(active) and not active[i]
        if quiet and first is None:first = i
        if not quiet and first is not None:
            if i - first >= minimum:result.update(range(first, i))
            first = None
    return result


def pair_onsets(bars, start):
    if start + 2 > len(bars) or any(len(b['active']) < 16 for b in bars[start:start + 2]):
        return None
    return {i * 16 + s for i, bar in enumerate(bars[start:start + 2]) for s in bar['onsets']}


def roots(path, root=52):
    _, rows = demo.read_notes(path)
    return [r for r in rows if r[2] == root and r[4] == 1]


def local_bars(events, left, right):
    """Exact local-bar grid, with an explicit partial final bar (never padded)."""
    bars = []
    for start in range(left, right, TPB * 4):
        stop = min(start + TPB * 4, right)
        assert (stop - start) % CELL == 0
        active = [any(s < start + (i + 1) * CELL and e > start + i * CELL for s, e, *_ in events)
                  for i in range((stop - start) // CELL)]
        selected = [(s, min(e, stop), p, v, c) for s, e, p, v, c in events if start <= s < stop]
        assert all((s - start) % CELL == 0 for s, *_ in selected)
        maximum = max((v for _, _, _, v, _ in selected), default=0)
        bars.append({'onsets': [(s - start) // CELL for s, *_ in selected],
                     'gates_steps': [(e - s) / CELL for s, e, *_ in selected],
                     'velocities': [v for _, _, _, v, _ in selected],
                     'major_accents': [(s - start) // CELL for s, _, _, v, _ in selected if v >= .9 * maximum],
                     'active': active, 'major_quiet_cells': sorted(quiet_runs(active))})
    return bars


def measure_section(events, drum_rows, reference, section, lead):
    left = lead + section['start_beat'] * TPB; right = lead + section['end_beat'] * TPB
    bars = local_bars(events, left, right)
    repeated = []
    for i, bar in enumerate(bars):
        canonical = reference[i % 8]; count = len(bar['active'])
        repeated.append({'onsets': [s for s in canonical['onsets'] if s < count],
            'gates_steps': [min(g, count - s) for s, g in zip(canonical['onsets'], canonical['gates_steps']) if s < count],
            'major_accents': [s for s in canonical['major_accents'] if s < count],
            'major_quiet_cells': [s for s in canonical['major_quiet_cells'] if s < count],
            'active': canonical['active'][:count]})
    matches = []; primary = []; expected_rest = preserved_rest = 0
    expected_tail = preserved_tail = 0; ref_events = shared = correct_gates = 0
    all_actual = set(); all_expected = set()
    for i, (bar, ref) in enumerate(zip(bars, repeated)):
        actual = set(bar['onsets']); expected = set(ref['onsets'])
        ref_events += len(expected); shared += len(actual & expected)
        all_actual.update(i * 16 + s for s in actual); all_expected.update(i * 16 + s for s in expected)
        gates = dict(zip(bar['onsets'], bar['gates_steps'])); desired = dict(zip(ref['onsets'], ref['gates_steps']))
        correct_gates += sum(gates[s] == desired[s] for s in actual & expected)
        expected_rest += len(ref['major_quiet_cells'])
        preserved_rest += sum(not bar['active'][s] for s in ref['major_quiet_cells'])
        if len(ref['active']) == 16 and not ref['active'][-1]:
            expected_tail += 1; preserved_tail += not bar['active'][-1]
        anchor_tick = left + i * 4 * TPB
        kick_ticks = [s for s, _, p, *_ in drum_rows if p in (35, 36) and anchor_tick <= s < min(anchor_tick + 4 * TPB, right)]
        for cell in bar['major_accents']:
            tick = anchor_tick + cell * CELL
            matches.append({'bar': i + 1, 'step': cell, 'kick_within_tolerance': any(abs(tick - k) <= .04 * TPB for k in kick_ticks)})
        primary.append({'bar': i + 1, 'guitar_at_section_downbeat': 0 in actual,
                        'kick_at_section_downbeat': any(abs(k - anchor_tick) <= .04 * TPB for k in kick_ticks)})
        bar['added_vs_reference'] = sorted(actual - expected)
        bar['removed_vs_reference'] = sorted(expected - actual)
        bar['reference_major_rest_retained'] = sum(not bar['active'][s] for s in ref['major_quiet_cells'])
    base = pair_onsets(bars, 0)
    similarities = {}
    for offset, name in ((2, 'bars1_2_vs3_4'), (4, 'bars1_2_vs5_6'), (6, 'bars1_2_vs7_8')):
        other = pair_onsets(bars, offset)
        similarities[name] = jaccard(base, other) if base is not None and other is not None else None
    chosen = [n for n in events if left <= n[0] < right]
    histogram = Counter((e - s) / TPB for s, e, *_ in chosen)
    return {'section': section['id'], 'length_bars': (right - left) / (TPB * 4),
            'bars': bars, 'onset_jaccard_vs_reference': jaccard(all_actual, all_expected),
            'added_onsets': len(all_actual - all_expected), 'missing_onsets': len(all_expected - all_actual),
            'event_count': len(chosen), 'pair_similarities': similarities,
            'major_rest_cells_expected': expected_rest, 'major_rest_cells_retained': preserved_rest,
            'major_rest_retention': preserved_rest / expected_rest if expected_rest else None,
            'tail_rests_expected': expected_tail, 'tail_rests_retained': preserved_tail,
            'gate_accuracy_on_shared_onsets': correct_gates / shared if shared else None,
            'gate_distribution_beats': dict(histogram), 'primary_downbeats': primary,
            'major_accent_kick_matches': sum(r['kick_within_tolerance'] for r in matches),
            'major_accent_count': len(matches), 'major_accent_details': matches}


def crop_band(source, destination, left, right, config):
    """An eight-bar listening crop: keep all parts, including clipped endpoints."""
    original = demo.load_midi(source); tempo = mido.bpm2tempo(config['export_bpm'])
    _, all_rows = demo.read_notes(source)
    result = mido.MidiFile(type=1, ticks_per_beat=TPB)
    for name, channel, program in (('Guitar', 1, config['guitar']['program']),
                                    ('Bass', 2, config['bass']['program']), ('Drums', 10, None)):
        notes = [(max(s, left) - left, min(e, right) - left, p, v, c)
                 for s, e, p, v, c in all_rows if c == channel and s < right and e > left]
        auxiliary = []
        for track in original.tracks:
            tick = 0
            for msg in track:
                tick += msg.time
                if hasattr(msg, 'channel') and msg.channel + 1 == channel and msg.type not in ('note_on', 'note_off', 'program_change') and left <= tick < right:
                    auxiliary.append((tick - left, msg.copy()))
        # A temporary part file uses the shared tested assembly helper.
        with tempfile.TemporaryDirectory() as temp:
            midi = demo.write_part(Path(temp) / 'part.mid', name, notes, right - left, tempo,
                                    [(0, 'Same chorus eight bars')], auxiliary, program, channel)
        result.tracks.append(midi.tracks[0])
    demo.save_midi(result, destination)
    return demo.read_notes(destination)


def run(config_path, original_dir, output):
    config = json.loads(Path(config_path).read_text()); original = Path(original_dir)
    target = Path(output); target.mkdir(parents=True, exist_ok=False)
    spec = config['guitar_motif']; ref = Path(spec['reference_midi'])
    assert demo.sha(ref) == spec['reference_sha256']
    protected = {}
    for base in (Path('data'), Path('output'), Path('src')):
        for p in base.rglob('*'):
            if p.is_file() and target not in p.parents and p.suffix.lower() in ('.mid', '.midi', '.mp3', '.wav', '.flac', '.json', '.py'):
                protected[str(p)] = demo.sha(p)
    # Existing before result must actually reproduce with the unchanged default path.
    legacy_config = deepcopy(config); del legacy_config['guitar_motif']
    with tempfile.TemporaryDirectory() as temp:
        before_dir = Path(temp) / 'legacy'
        demo.build(legacy_config, before_dir)
        assert all((before_dir / f'{name}.mid').read_bytes() == (original / f'{name}.mid').read_bytes()
                   for name in ('guitar', 'bass', 'drums', 'band'))
    after_dir = target / 'after_song'
    after_result = demo.build(config, after_dir)
    assert (after_dir / 'drums.mid').read_bytes() == (original / 'drums.mid').read_bytes()
    with tempfile.TemporaryDirectory() as temp:
        again = Path(temp) / 'regenerated'; demo.build(config, again)
        assert all((again / f'{name}.mid').read_bytes() == (after_dir / f'{name}.mid').read_bytes()
                   for name in ('guitar', 'bass', 'drums', 'band'))
    # Confirm the explicit blueprint regenerates every reference pitch/gate/velocity.
    reference_rows = demo.read_notes(ref)[1]
    normal_section = {'velocities': {}}
    motif = demo.section_motif(config, normal_section)
    regenerated_rows = sorted((i * 1920 + s.step * CELL, i * 1920 + int((s.step + s.duration_steps) * CELL), p, s.velocity, 1)
                              for i, bar in enumerate(motif) for s in bar for p in s.notes)
    assert regenerated_rows == sorted(reference_rows)
    shutil.copyfile(ref, target / 'reference_step7_B_8bars.mid')
    if spec.get('reference_pair_midi'):
        assert demo.sha(spec['reference_pair_midi']) == spec['reference_pair_sha256']
        shutil.copyfile(spec['reference_pair_midi'], target / 'reference_step7_B_drums_guitar_8bars.mid')
    shutil.copyfile(original / 'band.mid', target / 'before_band.mid')
    for name in ('band', 'guitar', 'bass', 'drums'):
        shutil.copyfile(after_dir / f'{name}.mid', target / f'after_{name}.mid')
    reference = local_bars(roots(ref), 0, 32 * TPB)
    lead = after_result['lead_tick']
    _, drums = demo.read_notes(original / 'drums.mid')
    before_roots = roots(original / 'guitar.mid'); after_roots = roots(after_dir / 'guitar.mid')
    metrics = {'definition': {'onset_similarity': 'Jaccard |intersection|/|union| on exact 16th onset sets; no pitches/chord tones counted twice',
        'major_rest': 'At least two consecutive silent sixteenth cells inside each reference bar; denominator is reference rest cells within the observed section',
        'major_accent': 'Note On velocity >=90% of that bar maximum; raw velocity lists retained; intro has its original macro velocity settings',
        'kick_match': 'Within existing 0.04 quarter-beat tolerance; drum microtiming unchanged',
        'short_sections': 'No padding for metrics. Pair comparisons requiring absent full bars are null, not scored as perfect.',
        'reference_structure': 'Actual Step7-B is a four-bar A/A variation/A/ending repeated twice, not eight independent random bars.'},
        'reference': reference, 'before': [], 'after': []}
    for section in config['sections']:
        metrics['before'].append(measure_section(before_roots, drums, reference, section, lead))
        metrics['after'].append(measure_section(after_roots, drums, reference, section, lead))
    for item in metrics['after']:
        assert item['onset_jaccard_vs_reference'] == 1 and item['gate_accuracy_on_shared_onsets'] == 1
        assert item['major_rest_retention'] in (None, 1)
        assert item['tail_rests_expected'] == item['tail_rests_retained']
        assert all(r['guitar_at_section_downbeat'] and r['kick_at_section_downbeat'] for r in item['primary_downbeats'])
    section = next(s for s in config['sections'] if s['id'] == 'chorus')
    assert section['end_beat'] - section['start_beat'] >= 32
    left = lead + section['start_beat'] * TPB; right = left + 32 * TPB
    crop_band(original / 'band.mid', target / 'before_chorus_8bars.mid', left, right, config)
    crop_band(after_dir / 'band.mid', target / 'after_chorus_8bars.mid', left, right, config)
    for phase in ('before', 'after'):
        crop = roots(target / f'{phase}_chorus_8bars.mid')
        metrics[f'{phase}_chorus8'] = measure_section(crop, demo.read_notes(target / f'{phase}_chorus_8bars.mid')[1], reference,
                                                      {'id': 'chorus8', 'start_beat': 0, 'end_beat': 32}, 0)
    assert all(demo.sha(p) == h for p, h in protected.items())
    metrics['validation'] = {'legacy_four_midis_byte_identical': True, 'after_four_midis_reproducible': True,
        'drums_byte_identical_before_after': True, 'reference_regenerated_event_identical': True,
        'existing_files_unchanged': len(protected), 'after': after_result,
        'configuration_and_boundaries_unchanged_except_motif_policy': True}
    demo.dump(target / 'rhythm_metrics.json', metrics)
    demo.dump(target / 'request.json', config)
    report(target, metrics, config)
    return metrics


def report(target, metrics, config):
    lines = ['# Phase 9 Step 2 groove consistency', '',
      'User-approved Step7-B motif retained; existing section/drum arrangement preserved.', '',
      '## Comparison metrics (each section uses its own local bar origin)', '',
      '| Section | Before motif Jaccard | After | Before major-rest retention | After | Before gates | After |',
      '|---|---:|---:|---:|---:|---:|---:|']
    def fmt(v):return 'N/A' if v is None else f'{v:.3f}'
    for a, b in zip(metrics['before'], metrics['after']):
        lines.append(f"| {a['section']} | {fmt(a['onset_jaccard_vs_reference'])} | {fmt(b['onset_jaccard_vs_reference'])} | {fmt(a['major_rest_retention'])} | {fmt(b['major_rest_retention'])} | {fmt(a['gate_accuracy_on_shared_onsets'])} | {fmt(b['gate_accuracy_on_shared_onsets'])} |")
    lines += ['', '## Same chorus eight bars: pair-onset similarity', '',
      '| Pair | Before | After |', '|---|---:|---:|']
    for key, value in metrics['after_chorus8']['pair_similarities'].items():
        lines.append(f"| {key} | {fmt(metrics['before_chorus8']['pair_similarities'][key])} | {fmt(value)} |")
    lines += ['', 'A four-bar ending intentionally differs from the first pair; the target is reference structure, not maximal similarity.',
      '', '## Every chorus bar (onset steps zero-based, gates in sixteenths)', '',
      '| Bar | Before onsets | After onsets | Before gates | After gates |', '|---|---|---|---|---|']
    for i, (a, b) in enumerate(zip(metrics['before_chorus8']['bars'], metrics['after_chorus8']['bars'])):
        lines.append(f"| {i+1} | {a['onsets']} | {b['onsets']} | {a['gates_steps']} | {b['gates_steps']} |")
    lines += ['', '## Accent/kick relationship', '',
      '| Section | Before major accents matching kick | After | Section-local downbeat anchors after |', '|---|---:|---:|---:|']
    for a, b in zip(metrics['before'], metrics['after']):
        anchors=b['primary_downbeats']; good=sum(r['guitar_at_section_downbeat'] and r['kick_at_section_downbeat'] for r in anchors)
        lines.append(f"| {a['section']} | {a['major_accent_kick_matches']}/{a['major_accent_count']} | {b['major_accent_kick_matches']}/{b['major_accent_count']} | {good}/{len(anchors)} |")
    lines += ['', 'All source drums remain byte-identical. Secondary kick/guitar mismatches are intentional; do not fill the preserved motif rests to force full kick recall.',
      'Per-bar raw velocities, major accents, added/missing onsets, rest masks and full pair scores are in rhythm_metrics.json.',
      'Before/after eight-bar excerpts are the same first eight chorus bars, with no leading silence; full songs retain 0.45s.',
      'Section names before the user-specified chorus remain estimated. Section lengths and chorus 15.3–27.3 are unchanged.',
      'After rhythmic motifs are intentionally shared across sections; intro velocity and unchanged drum choices retain atmosphere differences.',
      'No new melodic/chord progressions, independent bass rhythm, extra density or random patterns were introduced.']
    with (target / 'comparison.md').open('x') as stream:stream.write('\n'.join(lines)+'\n')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path); parser.add_argument('before', type=Path); parser.add_argument('output', type=Path)
    args = parser.parse_args()
    data = run(args.config, args.before, args.output)
    print(json.dumps(data['validation'], indent=2))
