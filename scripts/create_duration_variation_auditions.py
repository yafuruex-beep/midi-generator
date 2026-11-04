"""Opt-in Note Off-only guitar comparison; bass/drums/defaults untouched."""
from collections import Counter
from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path
import statistics

import mido


def _helper(name):
    path=Path(__file__).with_name(name+'.py')
    if not path.exists():path=Path.cwd()/'scripts'/(name+'.py')
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

pitch=_helper('create_pitch_variation_auditions')
density=_helper('create_pitch_density_auditions')


def attack_signature(midi):
    gs,end=pitch.groups(midi)
    return (midi.ticks_per_beat,end,tuple((g['start'],g['root'],g['velocity'],g['channel'],g['intervals']) for g in gs))


def absolute_non_releases(midi):
    result=[]
    for ti,track in enumerate(midi.tracks):
        tick=0
        for msg in track:
            tick+=msg.time
            if msg.type=='note_off' or (msg.type=='note_on' and msg.velocity==0):continue
            result.append((ti,tick,msg.copy(time=0).dict()))
    return result


def change_gates(midi, requested_beats):
    gs,end=pitch.groups(midi);tpb=midi.ticks_per_beat
    if len(gs)!=len(requested_beats):raise ValueError('One duration per attack group required')
    replacement={};changes=[]
    for i,(g,value) in enumerate(zip(gs,requested_beats)):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
            raise ValueError('Duration must be a finite positive number of quarter beats')
        ticks=value*tpb
        if not float(ticks).is_integer():raise ValueError('Duration must be exactly representable in MIDI ticks')
        next_on=gs[i+1]['start'] if i+1<len(gs) else end
        bar_end=(g['start']//(4*tpb)+1)*4*tpb
        requested_end=g['start']+int(ticks)
        stop=min(requested_end,next_on,bar_end,end)
        if stop<=g['start']:raise ValueError('Duration cap resulted in nonpositive gate')
        for n in g['members']:replacement[n['off']]=stop
        changes.append({'group':i,'bar':g['start']//(4*tpb)+1,'step':g['start']%(4*tpb)/(tpb/4),
                        'old_beats':(g['end']-g['start'])/tpb,'requested_beats':value,
                        'actual_beats':(stop-g['start'])/tpb,'shortened_by_safety_cap':stop<requested_end})
    if all(g['end']==replacement[g['members'][0]['off']] for g in gs):return deepcopy(midi),changes
    result=deepcopy(midi)
    for ti,track in enumerate(midi.tracks):
        events=[];tick=0
        for index,msg in enumerate(track):
            tick+=msg.time
            target=replacement.get((ti,index),tick)
            priority=0 if msg.type=='note_off' or (msg.type=='note_on' and msg.velocity==0) else 1
            events.append((target,priority,index,msg))
        new=mido.MidiTrack();previous=0
        for target,priority,index,msg in sorted(events,key=lambda e:e[:3]):
            new.append(msg.copy(time=target-previous));previous=target
        result.tracks[ti]=new
    assert attack_signature(result)==attack_signature(midi)
    assert absolute_non_releases(result)==absolute_non_releases(midi)
    return result,changes


def duration_metrics(midi):
    gs,end=pitch.groups(midi);tpb=midi.ticks_per_beat
    gates=[(g['end']-g['start'])/tpb for g in gs]
    note_gates=[gate for gate,g in zip(gates,gs) for _ in g['members']]
    ioi=[(b['start']-a['start'])/tpb for a,b in zip(gs,gs[1:])]
    gaps=[(b['start']-a['end'])/tpb for a,b in zip(gs,gs[1:])]
    assert all(g>=0 for g in gaps)
    ratios=[gate/interval for gate,interval in zip(gates,ioi)]
    rests=[];cursor=0
    for g in gs:
        if g['start']>cursor:rests.append([cursor/tpb,g['start']/tpb])
        cursor=g['end']
    if cursor<end:rests.append([cursor/tpb,end/tpb])
    def histogram(values):return [{'beats':v,'count':n} for v,n in sorted(Counter(values).items())]
    return {'groups':len(gs),'notes':len(note_gates),
      'group_gate_histogram':histogram(gates),'note_gate_histogram':histogram(note_gates),
      'group_mean_gate_beats':statistics.mean(gates),'note_mean_gate_beats':statistics.mean(note_gates),
      'group_gate_population_stdev':statistics.pstdev(gates),
      'intervals_with_next_onset':len(ioi),'IOI_histogram':histogram(ioi),'next_onset_gap_histogram':histogram(gaps),
      'group_gate_to_IOI_ratios':ratios,'gate_to_IOI_mean':statistics.mean(ratios) if ratios else None,
      'gate_to_IOI_median':statistics.median(ratios) if ratios else None,'next_onset_gap_mean':statistics.mean(gaps) if gaps else None,
      'terminal_gap_beats':(end-gs[-1]['end'])/tpb,'rest_intervals':rests,
      'MIDI_gate_silence_ratio':sum(b-a for a,b in rests)/(end/tpb),
      'long_groups_ge_05':[{'group':i,'bar':g['start']//(4*tpb)+1,'step':g['start']%(4*tpb)/(tpb/4),
                         'beats':gate,'root':g['root'],'intervals':list(g['intervals']),'velocity':g['velocity']}
                          for i,(g,gate) in enumerate(zip(gs,gates)) if gate>=.5],
      'per_group':[{'group':i,'bar':g['start']//(4*tpb)+1,'step':g['start']%(4*tpb)/(tpb/4),
          'gate_beats':gate,'IOI':ioi[i] if i<len(ioi) else None,'next_gap':gaps[i] if i<len(gaps) else None,
          'root':g['root'],'velocity':g['velocity'],'notes':[n['pitch'] for n in g['members']]}
          for i,(g,gate) in enumerate(zip(gs,gates))]}


def requested_gates(gs,tpb,req):
    baseline=[(g['end']-g['start'])/tpb for g in gs]
    B=[req['short_beats'] if g['velocity']<req['weak_velocity_below'] and gate==req['baseline_short_beats'] else gate for g,gate in zip(gs,baseline)]
    C=list(B)
    for i,g in enumerate(gs):
        if g['start']%(4*tpb)==req['contrast_step']*tpb/4 and g['velocity']>=req['contrast_min_velocity']:
            available=((gs[i+1]['start'] if i+1<len(gs) else (g['start']//(4*tpb)+1)*4*tpb)-g['start'])/tpb
            if available>=req['long_beats']:C[i]=req['long_beats']
    return {'A':baseline,'B':B,'C':C}


def run(request_path,output,*,project_root='.'):
    root=Path(project_root).resolve();req=json.loads(Path(request_path).read_text())
    sources={k:root/v['path'] for k,v in req['sources'].items()}
    for k,path in sources.items():
        if pitch.sha(path)!=req['sources'][k]['sha256']:raise ValueError('Source hash mismatch: '+k)
    guitar=mido.MidiFile(sources['guitar']);band=mido.MidiFile(sources['band']);bass=mido.MidiFile(sources['bass']);drum=mido.MidiFile(sources['drums'])
    gs,end=pitch.groups(guitar);bs,_=pitch.groups(bass);tpb=guitar.ticks_per_beat
    assert end==32*tpb and len(gs)==60 and sum(len(g['members'])==2 for g in gs)==30
    assert band.tracks==[guitar.tracks[0],bass.tracks[0],drum.tracks[0]]
    assert [(g['start'],g['root']-24,g['velocity']) for g in gs]==[(g['start'],g['root'],g['velocity']) for g in bs]
    assert Counter((g['end']-g['start'])/tpb for g in gs)=={.25:56,.5:4}
    plans=requested_gates(gs,tpb,req)
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    for name in ['drums_shared.mid']+[f'drums_{k}.mid' for k in 'ABC']:
        with (out/name).open('xb') as f:f.write(sources['drums'].read_bytes())
    report={'request':req,'preference_record':'User rated Step 8-C best. This is the preserved A baseline, no automatic adoption of new gates.',
            'baseline_measurement':duration_metrics(guitar),'candidates':{},'files':{},'defaults_changed':False,'automatic_adoption':False}
    names={'A':'baseline','B':'short','C':'contrast'}
    for label,name in names.items():
        candidate,changes=change_gates(guitar,plans[label]);cg,_=pitch.groups(candidate)
        assert attack_signature(candidate)==attack_signature(guitar)
        assert pitch.local_pitch_signature(candidate,0)==pitch.local_pitch_signature(candidate,4)
        for i in range(30):assert cg[i]['end']-cg[i]['start']==cg[i+30]['end']-cg[i+30]['start']
        assert sum(len(g['members'])==2 for g in cg)==30
        gp=out/f'guitar_{label}_{name}.mid';bp=out/f'bass_{label}_{name}.mid';main=out/f'chorus_{label}_{name}.mid'
        with bp.open('xb') as f:f.write(sources['bass'].read_bytes())
        if label=='A':
            assert pitch.midi_bytes(candidate)==sources['guitar'].read_bytes()
            with gp.open('xb') as f:f.write(sources['guitar'].read_bytes())
            with main.open('xb') as f:f.write(sources['band'].read_bytes())
        else:
            pitch.save_new(candidate,gp)
            new=mido.MidiFile(type=1,ticks_per_beat=tpb)
            new.tracks.extend([deepcopy(candidate.tracks[0]),deepcopy(band.tracks[1]),deepcopy(band.tracks[2])]);pitch.save_new(new,main)
        assert density.track_chunks(main)[1:]==density.track_chunks(sources['band'])[1:]
        metric=duration_metrics(candidate)
        metric.update({'changes':[c for c in changes if c['old_beats']!=c['actual_beats']],
                       'safety_shortening':[c for c in changes if c['shortened_by_safety_cap']],
                       'motif_gate_match_1_2_vs_5_6':{'matched_groups':16,'denominator':16,'rate':1.0},
                       'bass_endpoints_intentionally_frozen':True,
                       'guitar_bass_gate_difference_groups':sum(a['end']!=b['end'] for a,b in zip(cg,bs)),
                       'all_onsets_pitches_velocity_voicings_fixed':True,'bass_drum_bytes_identical':True})
        report['candidates'][label]=metric
        for p in [gp,bp,main]:report['files'][p.name]=pitch.sha(p)
    for p in out.glob('drums*.mid'):report['files'][p.name]=pitch.sha(p)
    for k,path in sources.items():assert pitch.sha(path)==req['sources'][k]['sha256']
    with (out/'verification.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    with (out/'request.json').open('x') as f:json.dump(req,f,ensure_ascii=False,indent=2);f.write('\n')
    return report


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('request');parser.add_argument('output');parser.add_argument('--project-root',default='.')
    a=parser.parse_args();r=run(a.request,a.output,project_root=a.project_root)
    print({k:{n:v[n] for n in ['group_gate_histogram','note_gate_histogram','group_mean_gate_beats','MIDI_gate_silence_ratio']} for k,v in r['candidates'].items()})
