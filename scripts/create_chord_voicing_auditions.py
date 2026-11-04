"""Opt-in single/power5 comparison, preserving every original root event."""
from collections import Counter
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import mido
from midi_generator.modules.guitar.sync import load_sync_config
from midi_generator.modules.guitar.timeline import read_timeline


def _helper(name):
    path=Path(__file__).with_name(name+'.py')
    if not path.exists():path=Path.cwd()/'scripts'/(name+'.py')
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

pitch=_helper('create_pitch_variation_auditions')
density=_helper('create_pitch_density_auditions')


def root_signature(midi):
    gs,end=pitch.groups(midi)
    return midi.ticks_per_beat,end,tuple((g['start'],g['end'],g['root'],g['velocity'],g['channel']) for g in gs)


def set_voicings(midi, chord_indices, *, scale_pitch_classes, note_range):
    gs,end=pitch.groups(midi); selected=set(chord_indices)
    if any(type(i) is not int or not 0<=i<len(gs) for i in selected):raise ValueError('Invalid chord group index')
    remove=set();insert={}
    for i,g in enumerate(gs):
        existing=len(g['members'])==2
        if i in selected:
            upper=g['root']+7
            if not 0<=upper<=127 or not note_range[0]<=upper<=note_range[1]:raise ValueError('Power5 upper note outside configured range')
            if upper%12 not in scale_pitch_classes:raise ValueError('Power5 upper note outside selected scale')
            if not existing:
                root=g['members'][0]
                for location in (root['on'],root['off']):insert[location]=upper
        elif existing:
            upper=g['members'][1]
            remove.update([upper['on'],upper['off']])
    result=deepcopy(midi)
    for ti,track in enumerate(midi.tracks):
        events=[];tick=0
        for index,msg in enumerate(track):
            tick+=msg.time
            if (ti,index) not in remove:
                events.append((tick,index,0,msg))
            if (ti,index) in insert:
                events.append((tick,index,1,msg.copy(note=insert[(ti,index)])))
        new=mido.MidiTrack();previous=0
        for tick,index,sub,msg in sorted(events,key=lambda e:e[:3]):
            new.append(msg.copy(time=tick-previous));previous=tick
        result.tracks[ti]=new
    assert root_signature(result)==root_signature(midi)
    changed,_=pitch.groups(result)
    assert {i for i,g in enumerate(changed) if len(g['members'])==2}==selected
    return result


def choose_exact(units, scores, count):
    """Maximize explicit placement priority at an exact count, stable on ties."""
    if type(count) is not int or count<0:raise ValueError('Nonnegative integer chord count required')
    if len({i for u in units for i in u})!=sum(map(len,units)):raise ValueError('Selection units must not overlap')
    dp={0:(0,())}
    for unit in units:
        weight=len(unit);value=sum(scores[i] for i in unit)
        for total,(score,chosen) in list(dp.items())[::-1]:
            new=total+weight
            if new>count:continue
            candidate=(score+value,tuple(sorted(chosen+tuple(unit))))
            old=dp.get(new)
            if old is None or candidate[0]>old[0] or (candidate[0]==old[0] and candidate[1]<old[1]):dp[new]=candidate
    if count not in dp:raise ValueError('Exact chord count conflicts with repetition units')
    return set(dp[count][1])


def plans(gs,tpb,kick_ticks,req):
    if len(gs)!=60:raise ValueError('Expected measured 60-group baseline')
    units=[(i,i+30) for i in range(30)]
    for a,b in units:
        assert gs[b]['start']-gs[a]['start']==16*tpb
        assert gs[b]['end']-gs[a]['end']==16*tpb
    scores={};roles={}
    for i,g in enumerate(gs):
        bar=g['start']//(4*tpb);local=g['start']%(4*tpb)
        samebar=[x for x in gs if x['start']//(4*tpb)==bar]
        ending=bar%4==3 and g is samebar[-1]
        kick=any(abs(g['start']-t)<=req['kick_tolerance_beats']*tpb for t in kick_ticks)
        strong=local%tpb==0
        accent=g['velocity']>=req['strong_velocity_threshold']
        following=gs[i+1] if i+1<len(gs) and gs[i+1]['start']//(4*tpb)==bar else None
        pickup=following is not None and following['velocity']>=req['major_velocity_threshold'] and 0<following['start']-g['start']<=req['pickup_max_gap_steps']*tpb/4
        # This is a placement priority, not a learned or musical quality score.
        w=req['role_score_weights']
        scores[i]=w['phrase_end']*ending+w['velocity_at_least_96']*accent+w['bar_start']*(local==0)+w['pickup_before_major_accent']*pickup+w['kick_match']*kick+w['quarter_beat']*strong
        roles[i]={'strong_velocity':accent,'quarter_beat':strong,'phrase_end':ending,'kick_coincidence':kick,'pickup_before_major_accent':pickup}
    existing={i for i,g in enumerate(gs) if len(g['members'])==2}
    eligible={i for i,g in enumerate(gs) if g['root']+7<=req['guitar_note_range'][1] and (g['root']+7)%12 in req['scale_pitch_classes']}
    oldunits=[u for u in units if set(u)<=existing]
    allunits=[u for u in units if set(u)<=eligible]
    B=choose_exact(oldunits,scores,req['chord_counts']['B'])
    C=set()
    for bar,count in enumerate(req['C_first_four_bar_chord_counts']):
        own=[u for u in oldunits if gs[u[0]]['start']//(4*tpb)==bar]
        C.update(choose_exact(own,scores,2*count))
    D=choose_exact(allunits,scores,len(existing))
    assert D!=existing
    return {'A':existing,'B':B,'C':C,'D':D},roles


def run(request_path,output,*,project_root='.'):
    root=Path(project_root).resolve();req=json.loads(Path(request_path).read_text())
    src={k:root/v['path'] for k,v in req['sources'].items()}
    for key,path in src.items():
        if pitch.sha(path)!=req['sources'][key]['sha256']:raise ValueError('Source hash mismatch: '+key)
    guitar=mido.MidiFile(src['guitar']);bass=mido.MidiFile(src['bass']);band=mido.MidiFile(src['band']);drum=mido.MidiFile(src['drums'])
    gs,end=pitch.groups(guitar);bs,bend=pitch.groups(bass)
    assert end==bend==15360 and guitar.ticks_per_beat==480
    assert band.tracks==[guitar.tracks[0],bass.tracks[0],drum.tracks[0]]
    assert [(g['start'],g['end'],g['root']-24,g['velocity']) for g in gs]==[(g['start'],g['end'],g['root'],g['velocity']) for g in bs]
    config=load_sync_config(root/req['drum_mapping_config'])
    data=read_timeline(src['drums'],set())
    kicks=[n['start_tick'] for n in data['notes'] if n['channel']==10 and n['note'] in config['kick_notes']]
    masks,roles=plans(gs,guitar.ticks_per_beat,kicks,req)
    assert len(masks['A'])==req['expected_original_power5_count']==46
    assert len(masks['C'])==30 and len(masks['D'])==len(masks['A'])
    assert masks['B']<=masks['A'] and masks['C']<=masks['A']
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    for n in ['drums_shared.mid']+[f'drums_{k}.mid' for k in 'ABCD']:
        with (out/n).open('xb') as f:f.write(src['drums'].read_bytes())
    report={'request':req,'measured_baseline':{'groups':60,'guitar_notes':sum(len(g['members']) for g in gs),
        'single_groups':60-len(masks['A']),'power5_groups':len(masks['A'])},
        'automatic_adoption':False,'defaults_modified':False,'candidates':{},'files':{}}
    names={'A':'baseline','B':'single_focus','C':'balanced','D':'accent_chords'}
    originalroots=[g['root'] for g in gs]
    rate=sum(a!=b for a,b in zip(originalroots,originalroots[1:]))/59
    for label,name in names.items():
        candidate=set_voicings(guitar,masks[label],scale_pitch_classes=req['scale_pitch_classes'],note_range=req['guitar_note_range'])
        cg,_=pitch.groups(candidate)
        assert pitch.local_pitch_signature(candidate,0)==pitch.local_pitch_signature(candidate,4)
        for i in range(30):assert (i in masks[label])==(i+30 in masks[label])
        gp=out/f'guitar_{label}_{name}.mid';bp=out/f'bass_{label}_{name}.mid';main=out/f'chorus_{label}_{name}.mid'
        with bp.open('xb') as f:f.write(src['bass'].read_bytes())
        if label=='A':
            assert pitch.midi_bytes(candidate)==src['guitar'].read_bytes()
            with gp.open('xb') as f:f.write(src['guitar'].read_bytes())
            with main.open('xb') as f:f.write(src['band'].read_bytes())
        else:
            pitch.save_new(candidate,gp)
            new=mido.MidiFile(type=1,ticks_per_beat=candidate.ticks_per_beat)
            new.tracks.extend([deepcopy(candidate.tracks[0]),deepcopy(band.tracks[1]),deepcopy(band.tracks[2])]);pitch.save_new(new,main)
        assert density.track_chunks(main)[1:]==density.track_chunks(src['band'])[1:]
        assert bp.read_bytes()==src['bass'].read_bytes()
        roots=[g['root'] for g in cg];assert roots==originalroots
        assert all(n['pitch']%12 in req['scale_pitch_classes'] for g in cg for n in g['members'])
        power=[i for i,g in enumerate(cg) if len(g['members'])==2]
        runs=[];run=0
        for i in range(61):
            if i<60 and i in masks[label]:run+=1
            elif run:runs.append(run);run=0
        positions=[{'group':i,'bar':g['start']//1920+1,'step':g['start']%1920/120,'root':g['root'],'velocity':g['velocity'],**roles[i]} for i,g in enumerate(cg) if i in masks[label]]
        report['candidates'][label]={'name':name,'groups':60,'single_groups':60-len(power),'power5_groups':len(power),
         'power5_ratio':len(power)/60,'guitar_note_count':sum(len(g['members']) for g in cg),
         'power5_indices':power,'chord_positions':positions,'chord_run_lengths_groups':runs,
         'chord_run_length_histogram':dict(Counter(runs)),'max_chord_run':max(runs,default=0),
         'chord_groups_on_kick':sum(roles[i]['kick_coincidence'] for i in power),
         'mapped_kick_events_covered_by_chord':sum(any(abs(t-cg[i]['start'])<=req['kick_tolerance_beats']*480 for i in power) for t in kicks),
         'mapped_kick_events':len(kicks),'chord_strong_velocity_groups':sum(roles[i]['strong_velocity'] for i in power),
         'upper_voices_added_vs_A':sorted(masks[label]-masks['A']),'upper_voices_removed_vs_A':sorted(masks['A']-masks[label]),
         'root_signature_exact':True,'bass_bytes_exact':True,'drum_bytes_exact':True,
         'root_motion_rate':rate,'root_moves':sum(a!=b for a,b in zip(roots,roots[1:])),'root_transition_denominator':59,
         'motif_1_2_vs_5_6_composition_matches':True,'composition_first4_matches_last4':True,
         'per_bar_chords':[sum(i in masks[label] for i,g in enumerate(cg) if g['start']//1920==bar) for bar in range(8)]}
        for p in [gp,bp,main]:report['files'][p.name]=pitch.sha(p)
    for key,path in src.items():assert pitch.sha(path)==req['sources'][key]['sha256']
    for p in out.glob('drums*.mid'):report['files'][p.name]=pitch.sha(p)
    with (out/'verification.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    with (out/'request.json').open('x') as f:json.dump(req,f,ensure_ascii=False,indent=2);f.write('\n')
    return report


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('request');parser.add_argument('output');parser.add_argument('--project-root',default='.')
    a=parser.parse_args();r=run(a.request,a.output,project_root=a.project_root)
    print({k:{n:v[n] for n in ['single_groups','power5_groups','guitar_note_count','upper_voices_added_vs_A','upper_voices_removed_vs_A']} for k,v in r['candidates'].items()})
