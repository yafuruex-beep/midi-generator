"""Opt-in pitch-density comparison; no changes to generator defaults."""
from collections import Counter
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import struct

import mido
from midi_generator.api import generate

_helper = Path(__file__).with_name('create_pitch_variation_auditions.py')
if not _helper.exists():  # pre-install verification from a temporary staging area
    _helper = Path.cwd()/'scripts/create_pitch_variation_auditions.py'
_spec = importlib.util.spec_from_file_location('pitch_audition_helpers', _helper)
pitch = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(pitch)


def motion_metrics(roots, baseline_roots, *, starts=None, ends=None, tpb=480):
    if not roots or len(roots) != len(baseline_roots):
        raise ValueError('Nonempty equal-length root and baseline sequences required')
    if any(type(n) is not int or not 0 <= n <= 127 for n in list(roots)+list(baseline_roots)):
        raise ValueError('MIDI roots must be integers in 0..127')
    deltas = [b-a for a,b in zip(roots, roots[1:])]
    moved = [d for d in deltas if d]
    up, down = sum(d>0 for d in deltas), sum(d<0 for d in deltas)
    runs=[]; first=0
    for stop in range(1,len(roots)+1):
        if stop==len(roots) or roots[stop]!=roots[first]:
            run={'root':roots[first],'start_group':first,'group_count':stop-first}
            if starts is not None and ends is not None:
                run['elapsed_beats_including_gaps']=(ends[stop-1]-starts[first])/tpb
            runs.append(run);first=stop
    changed=sum(a!=b for a,b in zip(roots, baseline_roots))
    return {'groups':len(roots),'changed_groups_vs_Step3B':changed,
            'change_rate_vs_Step3B':changed/len(roots),
            'moving_transitions':len(moved),'transition_denominator':len(deltas),
            'actual_motion_rate':len(moved)/len(deltas) if deltas else 0,
            'up_transitions':up,'down_transitions':down,'same_root_transitions':len(deltas)-len(moved),
            'up_share_of_moves':up/len(moved) if moved else None,
            'down_share_of_moves':down/len(moved) if moved else None,
            'signed_semitone_counts':dict(sorted(Counter(deltas).items())),
            'absolute_moving_semitone_counts':dict(sorted(Counter(abs(d) for d in moved).items())),
            'maximum_jump_semitones':max(map(abs,deltas),default=0),
            'same_root_runs':runs,'run_length_histogram_groups':dict(sorted(Counter(r['group_count'] for r in runs).items())),
            'maximum_same_root_run_groups':max(r['group_count'] for r in runs),
            'lowest_root':min(roots),'highest_root':max(roots)}


def track_chunks(path):
    data=Path(path).read_bytes()
    if data[:4]!=b'MThd':raise ValueError('Expected MIDI header')
    offset=8+struct.unpack('>I',data[4:8])[0];chunks=[]
    while offset<len(data):
        if data[offset:offset+4]!=b'MTrk':raise ValueError('Expected MIDI track chunk')
        size=struct.unpack('>I',data[offset+4:offset+8])[0]
        chunk=data[offset:offset+8+size]
        if len(chunk)!=8+size:raise ValueError('Truncated MIDI track')
        chunks.append(chunk);offset+=8+size
    return chunks


def run(request_path, output, *, project_root='.'):
    root=Path(project_root).resolve();req=json.loads(Path(request_path).read_text())
    sources={k:root/v['path'] for k,v in req['sources'].items()}
    for key,path in sources.items():
        if pitch.sha(path)!=req['sources'][key]['sha256']:raise ValueError(f'Source hash mismatch: {key}')
    guitar=mido.MidiFile(sources['guitar']);bass0=mido.MidiFile(sources['bass']);band0=mido.MidiFile(sources['band'])
    drum=mido.MidiFile(sources['drums']);baseline=mido.MidiFile(sources['metric_baseline'])
    original,end=pitch.groups(guitar);base_groups,base_end=pitch.groups(baseline)
    assert end==base_end==32*guitar.ticks_per_beat and len(original)==len(base_groups)==60
    assert band0.tracks==[guitar.tracks[0],bass0.tracks[0],drum.tracks[0]]
    assert pitch.rhythm_signature(guitar)==pitch.rhythm_signature(baseline)
    baseline_roots=[g['root'] for g in base_groups]
    assert req['root_plans']['A']==[g['root'] for g in original]
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    work=out/'generation_work';work.mkdir()
    for name in ['drums_shared.mid']+[f'drums_{x}.mid' for x in 'ABCD']:
        with (out/name).open('xb') as stream:stream.write(sources['drums'].read_bytes())
    report={'request':req,'preference_record':{'source':'Current user explicitly rated Step 6-C easiest to listen to, especially frequent pitch motion.',
            'confirmed':'Step 6-C is preferred among Step 6 candidates; not proof that motion rate alone causes quality.'},
            'candidates':{},'files':{},'automatic_adoption':False,'defaults_changed':False}
    names={'A':'step6C','B':'60percent','C':'90percent','D':'alternative_motion'}
    for label,name in names.items():
        roots=req['root_plans'][label]
        candidate=pitch.transpose_groups(guitar,roots,allowed_roots=req['allowed_roots'],
                   guitar_range=req['guitar_note_range'],bass_range=req['bass_note_range'],max_jump=req['max_adjacent_root_jump'])
        assert all(m.note%12 in req['scale_pitch_classes'] for t in candidate.tracks for m in t if m.type in {'note_on','note_off'})
        assert pitch.local_pitch_signature(candidate,0)==pitch.local_pitch_signature(candidate,4)
        assert pitch.rhythm_signature(candidate)==pitch.rhythm_signature(guitar)
        gp=out/f'guitar_{label}_{name}.mid'
        if label=='A':
            assert pitch.midi_bytes(candidate)==sources['guitar'].read_bytes()
            with gp.open('xb') as f:f.write(sources['guitar'].read_bytes())
        else:pitch.save_new(candidate,gp)
        raw_bass=work/f'bass_{label}_generated.mid'
        result=generate({'instrument':'bass','section':'chorus','tempo':req['export_bpm'],'bars':8,
                        'options':{'guitar_midi':str(gp.resolve()),'transpose_semitones':-24,
                                   'program':req['bass_program'],'midi_channel':2}},
                         output_path=raw_bass,project_root=root)
        bg,_=pitch.groups(mido.MidiFile(raw_bass));cg,_=pitch.groups(candidate)
        assert [(g['start'],g['end'],g['root'],g['velocity']) for g in bg]==[(g['start'],g['end'],g['root']-24,g['velocity']) for g in cg]
        bass=pitch.retain_bass_context(bass0,mido.MidiFile(raw_bass))
        bp=out/f'bass_{label}_{name}.mid';main=out/f'chorus_{label}_{name}.mid'
        band=mido.MidiFile(type=1,ticks_per_beat=candidate.ticks_per_beat)
        band.tracks.extend([deepcopy(candidate.tracks[0]),deepcopy(bass.tracks[0]),deepcopy(band0.tracks[2])])
        if label=='A':
            assert pitch.midi_bytes(bass)==sources['bass'].read_bytes()
            with bp.open('xb') as stream:stream.write(sources['bass'].read_bytes())
            with main.open('xb') as stream:stream.write(sources['band'].read_bytes())
        else:
            pitch.save_new(bass,bp);pitch.save_new(band,main)
        assert track_chunks(main)[2]==track_chunks(sources['band'])[2]
        loaded=mido.MidiFile(main)
        assert loaded.tracks==[candidate.tracks[0],bass.tracks[0],band0.tracks[2]]
        metric=motion_metrics(roots,baseline_roots,starts=[g['start'] for g in cg],ends=[g['end'] for g in cg],tpb=candidate.ticks_per_beat)
        notes=[n['pitch'] for g in cg for n in g['members']]
        metric.update({'name':name,'guitar_notes':len(notes),'lowest_guitar_note':min(notes),'highest_guitar_note':max(notes),
                       'lowest_bass_note':min(g['root'] for g in bg),'highest_bass_note':max(g['root'] for g in bg),
                       'guitar_range_violations':sum(not req['guitar_note_range'][0]<=n<=req['guitar_note_range'][1] for n in notes),
                       'bass_range_violations':sum(not req['bass_note_range'][0]<=g['root']<=req['bass_note_range'][1] for g in bg),
                       'motif_1_2_vs_5_6':{'exact_pitch_gate_velocity_and_interval_match':True,'matched_groups':16,'group_denominator':16,'similarity':1.0},
                       'roots':roots,'root_change_positions':[{ 'group':i,'bar':g['start']//(4*candidate.ticks_per_beat)+1,
                             'step':(g['start']%(4*candidate.ticks_per_beat))/(candidate.ticks_per_beat/4),
                             'from':roots[i-1],'to':roots[i],'delta':roots[i]-roots[i-1]}
                             for i,g in enumerate(cg) if i and roots[i]!=roots[i-1]],
                       'root_plan_by_bar':[[g['root'] for g in cg if b*4*candidate.ticks_per_beat<=g['start']<(b+1)*4*candidate.ticks_per_beat] for b in range(8)],
                       'rhythm_signature_preserved':True,'drum_actual_track_bytes_match':True,
                       'bass_follows_minus24':True,'bass_metadata':result.metadata})
        report['candidates'][label]=metric
        for path in [gp,bp,main]:report['files'][path.name]=pitch.sha(path)
    assert report['candidates']['B']['changed_groups_vs_Step3B']==36
    assert report['candidates']['C']['changed_groups_vs_Step3B']==54
    assert report['candidates']['D']['changed_groups_vs_Step3B']==report['candidates']['A']['changed_groups_vs_Step3B']==48
    assert req['root_plans']['D']!=req['root_plans']['A']
    assert pitch.sha(out/'chorus_A_step6C.mid')==pitch.sha(sources['band'])
    for key,path in sources.items():assert pitch.sha(path)==req['sources'][key]['sha256']
    for path in out.glob('drums*.mid'):report['files'][path.name]=pitch.sha(path)
    with (out/'verification.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    with (out/'request.json').open('x') as f:json.dump(req,f,ensure_ascii=False,indent=2);f.write('\n')
    return report


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('request');parser.add_argument('output');parser.add_argument('--project-root',default='.')
    a=parser.parse_args();r=run(a.request,a.output,project_root=a.project_root)
    print({k:{n:v[n] for n in ('change_rate_vs_Step3B','actual_motion_rate','maximum_jump_semitones')} for k,v in r['candidates'].items()})
