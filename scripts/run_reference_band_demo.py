"""Task-scoped integration harness: existing drums, phrase4 guitar, follow bass.

Song boundaries/settings belong to the JSON config. No generator defaults change.
Usage: python scripts/run_reference_band_demo.py CONFIG.json OUTPUT_DIRECTORY
Existing reports may coexist; existing generated MIDI/work directories are refused.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

import mido

from midi_generator.api import (BackingRules, analyze_midi, extract_rhythm_blueprint,
                                generate, generate_backing_phrase, render_backing_midi)
from midi_generator.core.midi_io import load_midi, save_midi
from midi_generator.modules.guitar.timeline import read_timeline

TPB = 480


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, data):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2); stream.write('\n')


def clip_intervals(notes, stop, offset=0):
    """Half-open crop, explicitly close sounding notes at section/file ends."""
    return [(start + offset, min(end, stop) + offset, pitch, velocity, channel)
            for start, end, pitch, velocity, channel in notes if start < stop and min(end, stop) > start]


def read_notes(path):
    data = read_timeline(path, set())
    if data['pairing']['unmatched_note_offs'] or data['pairing']['inferred_note_ends']:
        raise ValueError(f'Unpaired notes in {path}')
    return data, [(n['start_tick'], n['end_tick'], n['note'], n['velocity'], n['channel']) for n in data['notes']]


def write_part(path, name, notes, stop, tempo, markers=(), auxiliary=(), program=None, channel=None):
    """Assembly only; note selection/rhythm stays in the existing generators."""
    events = []
    for start, end, pitch, velocity, note_channel in notes:
        assert 0 <= start < end <= stop
        events.append((end, 0, mido.Message('note_off', note=pitch, velocity=0, channel=note_channel - 1)))
        events.append((start, 2, mido.Message('note_on', note=pitch, velocity=velocity, channel=note_channel - 1)))
    events.extend((tick, 1, msg.copy(time=0)) for tick, msg in auxiliary)
    events.extend((tick, 1, mido.MetaMessage('marker', text=text)) for tick, text in markers)
    midi = mido.MidiFile(type=0, ticks_per_beat=TPB)
    track = mido.MidiTrack([mido.MetaMessage('track_name', name=name),
        mido.MetaMessage('set_tempo', tempo=tempo), mido.MetaMessage('time_signature', numerator=4, denominator=4)])
    if program is not None:
        track.append(mido.Message('program_change', channel=channel - 1, program=program - 1))
    previous = 0
    for tick, _, message in sorted(events, key=lambda e: (e[0], e[1])):
        assert previous <= tick <= stop
        track.append(message.copy(time=tick - previous)); previous = tick
    track.append(mido.MetaMessage('end_of_track', time=stop - previous)); midi.tracks.append(track)
    save_midi(midi, path)
    return midi


def make_job_catalog(master, record, path, section, source_override=None):
    """Isolated arrangement assignment, never write to the master catalog."""
    job = deepcopy(master); entry = deepcopy(record)
    entry['tags']['section'] = [section]
    entry['tag_metadata'] = [{'category': 'section', 'value': section,
                              'source': 'task_arrangement_assignment', 'confidence': None,
                              'scope': 'generation condition, not verified source section'}]
    if source_override is not None:
        entry['path'] = str(Path(source_override).resolve()); entry['file_id'] = entry['path']
        entry['analysis'] = analyze_midi(source_override)
    job['files'] = [entry]
    dump(path, job)


def build(config, output):
    out = Path(output); out.mkdir(parents=True, exist_ok=True)
    for name in ('guitar.mid', 'bass.mid', 'drums.mid', 'band.mid', 'generation_work'):
        if (out / name).exists():raise FileExistsError(out / name)
    work = out / 'generation_work'; work.mkdir()
    assert sha(config['reference_audio']) == config['reference_sha256']
    ref_map = json.loads(Path(config['reference_map']).read_text())
    assert ref_map['source']['sha256'] == config['reference_sha256']
    assert config['chorus_user_annotation']['end_beat'] == config['end_beat']
    sections = config['sections']
    assert sections[0]['start_beat'] == 0 and sections[-1]['end_beat'] == config['end_beat']
    assert all(a['end_beat'] == b['start_beat'] for a, b in zip(sections, sections[1:]))
    master = json.loads(Path(config['master_catalog']).read_text())
    records = {e['path']: e for e in master['files']}
    tempo = mido.bpm2tempo(config['export_bpm'])
    stop = round(config['end_beat'] * TPB)
    lead = round(config['leading_silence_seconds'] * 1_000_000 * TPB / tempo)
    assert abs(lead * tempo / 1e6 / TPB - config['leading_silence_seconds']) <= tempo / 1e6 / TPB / 2
    guitar_notes, drum_notes, drum_aux = [], [], []
    blocks, section_stats = [], []
    for section in sections:
        section_start, section_end = section['start_beat'], section['end_beat']
        first_g, first_d = len(guitar_notes), len(drum_notes)
        for block_index, start_beat in enumerate(range(section_start, section_end, 16)):
            length_beats = min(16, section_end - start_beat); block_stop = length_beats * TPB
            source_path = section['drum_sources'][block_index % len(section['drum_sources'])]
            record = records[source_path]
            ident = f'{section["id"]}_{block_index + 1}'
            tag = section['id'] if section['id'] in ('intro', 'verse', 'chorus') else 'transition'
            source_job = work / f'{ident}_source_catalog.json'
            make_job_catalog(master, record, source_job, tag)
            # Reuse the drum engine to crop one grid-compatible bar, then reuse
            # it again to loop that original material to the requested block.
            one_bar = work / f'{ident}_one_bar.mid'
            req = {'instrument': 'drums', 'section': tag, 'tempo': config['export_bpm'],
                   'bars': 1, 'time_signature': [4, 4], 'seed': 0}
            first_result = generate(req, output_path=one_bar, catalog_path=source_job)
            bp = extract_rhythm_blueprint(one_bar)
            loop_job = work / f'{ident}_loop_catalog.json'
            make_job_catalog(master, record, loop_job, tag, one_bar)
            block_midi = work / f'{ident}_drums.mid'
            req['bars'] = math.ceil(length_beats / 4)
            drum_result = generate(req, output_path=block_midi, catalog_path=loop_job)
            drums, notes = read_notes(block_midi)
            assert drums['midi'].ticks_per_beat == TPB
            clipped = clip_intervals(notes, block_stop, start_beat * TPB)
            drum_notes.extend(clipped)
            for track in drums['midi'].tracks:
                tick = 0
                for msg in track:
                    tick += msg.time
                    if not msg.is_meta and msg.type not in ('note_on', 'note_off') and tick < block_stop:
                        drum_aux.append((start_beat * TPB + tick, msg.copy()))
            guitar_config = dict(config['guitar']); duration_mode = guitar_config.pop('duration_mode')
            root = guitar_config.pop('root'); program = guitar_config.pop('program')
            settings = BackingRules(**guitar_config, **section.get('velocities', {}))
            bars = generate_backing_phrase(bp, note=root, bars=req['bars'], rules=settings, duration_mode=duration_mode)
            for i, bar in enumerate(bars):
                # Reuse the accepted renderer and inspect its concrete notes.
                clip = work / f'{ident}_guitar_bar{i + 1}.mid'
                render_backing_midi(bar, export_bpm=config['export_bpm'], program=program, output_path=clip)
                _, rows = read_notes(clip)
                shifted = [(s + i * 4 * TPB, e + i * 4 * TPB, p, v, c) for s, e, p, v, c in rows]
                guitar_notes.extend(clip_intervals(shifted, block_stop, start_beat * TPB))
            blocks.append({'section': section['id'], 'start_beat': start_beat, 'length_beats': length_beats,
                           'drum_source': source_path, 'source_sha256': sha(source_path),
                           'source_provenance': record.get('provenance'),
                           'drum_seed': 0, 'kick_steps': list(bp.kick_steps), 'snare_steps': list(bp.snare_steps),
                           'blueprint_warnings': list(bp.warnings), 'guitar_rules': guitar_config,
                           'velocities': section.get('velocities', 'Step7 defaults'),
                           'processing': drum_result.metadata['processing']})
        relevant = [b for b in ref_map['bars'] if b['start_time'] < config['reference_origin_seconds'] + section_end * 60 / config['reference_bpm']
                    and b['end_time'] > config['reference_origin_seconds'] + section_start * 60 / config['reference_bpm']]
        section_stats.append({**section, 'length_bars': (section_end - section_start) / 4,
            'reference_start_seconds': config['reference_origin_seconds'] + section_start * 60 / config['reference_bpm'],
            'reference_end_seconds': config['reference_origin_seconds'] + section_end * 60 / config['reference_bpm'],
            'export_start_seconds': config['leading_silence_seconds'] + section_start * tempo / 1e6,
            'export_end_seconds': config['leading_silence_seconds'] + section_end * tempo / 1e6,
            'reference_mean_rms': sum(b['rms'] for b in relevant) / len(relevant),
            'reference_mean_onsets_per_second': sum(b['onset_density'] for b in relevant) / len(relevant),
            'guitar_events': sum(n[2] == config['guitar']['root'] for n in guitar_notes[first_g:]),
            'drum_notes': len(drum_notes) - first_d})
    # Bass core requires full-bar input: pad only silent working tail to 27 bars.
    # Final exports are all clipped to the requested 27.3 endpoint (106 beats).
    padded_bars = math.ceil(config['end_beat'] / 4)
    padded_guitar = work / 'guitar_full_bar_context.mid'
    write_part(padded_guitar, 'Guitar', guitar_notes, padded_bars * 4 * TPB, tempo,
               program=config['guitar']['program'], channel=1)
    padded_bass = work / 'bass_full_bar_context.mid'
    bass_request = {'instrument': 'bass', 'section': 'validation', 'tempo': config['export_bpm'],
        'bars': padded_bars, 'time_signature': [4, 4], 'options': {**config['bass'], 'guitar_midi': str(padded_guitar.resolve())}}
    bass_result = generate(bass_request, output_path=padded_bass)
    _, bass_notes = read_notes(padded_bass)
    bass_notes = clip_intervals(bass_notes, stop)
    markers = [(0, 'Reference opening silence'), (lead, 'Music bar 1.1')]
    markers += [(lead + s['start_beat'] * TPB, f'{s["id"]} [{s["status"]}]') for s in sections]
    markers.append((lead + stop, f'End: user boundary {config["chorus_user_annotation"]["end"]}'))
    outputs = {}
    for name, notes, channel, program, aux in (
        ('guitar', guitar_notes, 1, config['guitar']['program'], []),
        ('bass', bass_notes, 2, config['bass']['program'], []),
        ('drums', drum_notes, 10, None, drum_aux)):
        final_notes = clip_intervals(notes, stop, lead)
        outputs[name] = write_part(out / f'{name}.mid', name.title(), final_notes, stop + lead, tempo, markers,
                                   [(t + lead, m) for t, m in aux], program, channel)
    band = mido.MidiFile(type=1, ticks_per_beat=TPB)
    for name in ('guitar', 'bass', 'drums'):
        band.tracks.extend(mido.MidiTrack(m.copy() for m in t) for t in outputs[name].tracks)
    save_midi(band, out / 'band.mid')
    dump(out / 'generation_manifest.json', {'config': config, 'sections': section_stats, 'blocks': blocks,
        'lead_tick': lead, 'end_tick': stop + lead, 'bass_working_padding_beats': padded_bars * 4 - config['end_beat'],
        'bass_metadata': bass_result.metadata})
    verification = verify(out, config, section_stats, stop + lead, lead)
    dump(out / 'generation_verification.json', verification)
    return verification


def verify(out, config, sections, end_tick, lead):
    parts = {name: read_notes(out / f'{name}.mid') for name in ('guitar', 'bass', 'drums')}
    groups = {}
    for note in parts['guitar'][1]:groups.setdefault(note[0], []).append(note)
    roots = []
    for _, group in sorted(groups.items()):
        ordered = sorted(group, key=lambda n: n[2])
        root = config['guitar']['root']
        assert [n[2] for n in ordered] in ([root], [root, root + 7])
        assert len({(n[1], n[3]) for n in ordered}) == 1
        roots.append(ordered[0])
    assert parts['bass'][1] == [(s, e, p + config['bass']['transpose_semitones'], v, 2) for s, e, p, v, _ in roots]
    assert all(a[1] <= b[0] for a, b in zip(roots, roots[1:]))
    assert all((s - lead) % (TPB // 4) == 0 and e - s in (120, 240) for s, e, *_ in roots)
    band = load_midi(out / 'band.mid')
    assert len(band.tracks) == 3
    assert band.tracks == [parts[n][0]['midi'].tracks[0] for n in ('guitar', 'bass', 'drums')]
    assert all(len(t) > 0 for t in band.tracks)
    results = {}
    for name, (data, rows) in parts.items():
        assert data['end_tick'] == end_tick and data['channels'] == [{'guitar': 1, 'bass': 2, 'drums': 10}[name]]
        active = set(); tick = 0
        for track in data['midi'].tracks:
            for msg in track:
                tick += msg.time
                if msg.type == 'note_on' and msg.velocity:
                    assert (msg.channel, msg.note) not in active
                    active.add((msg.channel, msg.note))
                elif msg.type == 'note_off':
                    assert (msg.channel, msg.note) in active
                    active.remove((msg.channel, msg.note))
            assert not active
        results[name] = {'midi_notes': len(rows), 'end_tick': end_tick, 'sha256': sha(out / f'{name}.mid'),
                         'note_pairing_and_retriggers': True}
    kicks = [n for n in parts['drums'][1] if n[2] in (35, 36)]
    exact = sum(any(k[0] == r[0] for r in roots) for k in kicks)
    tolerance = sum(any(abs(k[0] - r[0]) <= .04 * TPB for r in roots) for k in kicks)
    local_signatures = []
    for s in sections:
        left, right = lead + s['start_beat'] * TPB, lead + s['end_beat'] * TPB
        local_signatures.append(tuple((a - left, b - a) for a, b, *_ in roots if left <= a < right))
    assert len(set(local_signatures)) > 1
    assert len({tuple(b - a for a, b, *_ in roots if lead + i * 8 * 4 * TPB <= a < lead + (i + 1) * 8 * 4 * TPB)
                for i in range(3)}) > 1
    return {'parts': results, 'band_sha256': sha(out / 'band.mid'), 'guitar_attack_events': len(roots),
            'power5_events': sum(len(g) == 2 for g in groups.values()),
            'power5_ratio': sum(len(g) == 2 for g in groups.values()) / len(roots),
            'half_beat_events': sum(e - s == 240 for s, e, *_ in roots),
            'kick_events': len(kicks), 'exact_kick_coincidences': exact, 'kick_matches_with_existing_tolerance': tolerance,
            'bass_root_follow_exact': True, 'tracks_separate': True, 'section_variation': True, 'non_identical_8bar_windows': True,
            'root_notes': [config['guitar']['root']],
            'bass_notes': [config['guitar']['root'] + config['bass']['transpose_semitones']], 'reference_chorus_boundary_source': 'current user messages',
            'end_boundary_music_beats': config['end_beat'], 'lead_tick': lead, 'end_tick': end_tick}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path); parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = build(json.loads(args.config.read_text()), args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
