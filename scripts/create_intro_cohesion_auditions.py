"""Fresh Intro foundation and position-aligned pitch cohesion auditions.

Only optional experiment code. No persistent tags or generator defaults change.
"""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path

import mido
from midi_generator.api import (generate, extract_rhythm_blueprint, generate_backing_steps,
                                render_backing_midi, BackingRules)


def _helper(name):
    path=Path(__file__).with_name(name+'.py')
    if not path.exists():path=Path.cwd()/'scripts'/(name+'.py')
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

pitch=_helper('create_pitch_variation_auditions')
density=_helper('create_pitch_density_auditions')
voicing=_helper('create_chord_voicing_auditions')
gate=_helper('create_duration_variation_auditions')


def position_map(groups, first_bar, bars, tpb):
    origin=first_bar*4*tpb;end=origin+bars*4*tpb
    return {g['start']-origin:i for i,g in enumerate(groups) if origin<=g['start']<end}


def reuse_by_position(roots,groups,donor_bar,target_bar,*,bars=2,tpb=480):
    """Transfer only matching local ticks, never event-number correspondence."""
    if len(roots)!=len(groups):raise ValueError('One root required per group')
    donor=position_map(groups,donor_bar,bars,tpb);target=position_map(groups,target_bar,bars,tpb)
    result=list(roots);pairs=[];missing=[]
    for tick,index in target.items():
        if tick in donor:
            result[index]=roots[donor[tick]];pairs.append((donor[tick],index,tick))
        else:missing.append({'target_index':index,'local_tick':tick,'policy':'Retain explicit role-based draft pitch, no index fallback'})
    return result,{'matched':pairs,'target_unmatched':missing,'donor_unmatched_ticks':sorted(set(donor)-set(target))}


def assignments(roots,groups,entries,*,tpb=480):
    index={(g['start']//(4*tpb),g['start']%(4*tpb)):i for i,g in enumerate(groups)}
    result=list(roots);seen=set()
    for row in entries:
        if type(row['bar']) is not int or row['bar']<1 or type(row['step']) is not int or not 0<=row['step']<16:
            raise ValueError('Assignment needs positive bar and integer sixteenth step')
        if type(row['root']) is not int or not 0<=row['root']<=127:
            raise ValueError('Assignment root must be a MIDI integer')
        key=(row['bar']-1,int(row['step']*tpb/4))
        if key in seen or key not in index:raise ValueError('Pitch assignment has duplicate or absent rhythmic position')
        seen.add(key);result[index[key]]=row['root']
    return result


def motif_comparison(groups,roots,first=0,second=4,*,bars=2,tpb=480):
    a=position_map(groups,first,bars,tpb);b=position_map(groups,second,bars,tpb)
    common=sorted(set(a)&set(b));union=set(a)|set(b)
    matches=sum(roots[a[t]]==roots[b[t]] for t in common)
    da=[roots[a[y]]-roots[a[x]] for x,y in zip(common,common[1:])]
    db=[roots[b[y]]-roots[b[x]] for x,y in zip(common,common[1:])]
    return {'common_positions':len(common),'positions_in_union':len(union),
       'rhythm_position_jaccard':len(common)/len(union) if union else 1,
       'root_matches_on_common_positions':matches,'root_match_rate_on_common_positions':matches/len(common) if common else None,
       'root_match_rate_over_position_union':matches/len(union) if union else None,
       'contour_matches':sum(x==y for x,y in zip(da,db)),'contour_comparisons':len(da),
       'contour_match_rate':sum(x==y for x,y in zip(da,db))/len(da) if da else None,
       'unmatched_first_positions':sorted(set(a)-set(b)),'unmatched_second_positions':sorted(set(b)-set(a))}


def directional_runs(roots):
    runs=[];current=0;length=0
    for a,b in zip(roots,roots[1:]):
        sign=(b>a)-(b<a)
        if sign and sign==current:length+=1
        else:
            if length:runs.append({'direction':'up' if current>0 else 'down','transitions':length})
            current=sign;length=1 if sign else 0
    if length:runs.append({'direction':'up' if current>0 else 'down','transitions':length})
    return runs


def assemble_guitar(bars,bpm,tpb=480):
    result=mido.MidiFile(type=0,ticks_per_beat=tpb)
    track=mido.MidiTrack([mido.MetaMessage('track_name',name='Intro Guitar'),
         mido.MetaMessage('set_tempo',tempo=mido.bpm2tempo(bpm)),mido.MetaMessage('time_signature',numerator=4,denominator=4),
         mido.Message('program_change',channel=0,program=30),mido.MetaMessage('marker',text='Fresh Intro - cohesion comparison')])
    events=[]
    for index,bar in enumerate(bars):
        rendered=render_backing_midi(bar,export_bpm=bpm,ticks_per_beat=tpb)
        tick=index*4*tpb
        for msg in rendered.tracks[0]:
            tick+=msg.time
            if msg.type in {'note_on','note_off'}:
                events.append((tick,0 if msg.type=='note_off' or msg.velocity==0 else 1,len(events),msg))
    previous=0
    for tick,priority,serial,msg in sorted(events,key=lambda e:e[:3]):track.append(msg.copy(time=tick-previous));previous=tick
    track.append(mido.MetaMessage('end_of_track',time=32*tpb-previous));result.tracks.append(track)
    return result


def build_foundation(req,root,work):
    catalog=json.loads((root/req['catalog_path']).read_text())
    record=deepcopy(next(r for r in catalog['files'] if r['path']==req['drum_source']['path']))
    if pitch.sha(root/record['path'])!=req['drum_source']['sha256']:raise ValueError('Drum source hash changed')
    record['tags']={**record.get('tags',{}),'section':['intro']}
    catalog['files']=[record]
    catalog['purpose']='Temporary task-only Intro assignment, not a verified source section or persistent catalog update.'
    cp=work/'intro_catalog.json'
    with cp.open('x') as stream:json.dump(catalog,stream,ensure_ascii=False,indent=2)
    result=generate({'instrument':'drums','section':'intro','tempo':req['bpm'],'bars':8,
                    'seed':req['drum_seed'],'time_signature':[4,4]},
                    output_path=work/'drums_generated.mid',catalog_path=cp,project_root=root)
    drum_path=result.output_files[0];drum=mido.MidiFile(drum_path)
    assert drum.type==0 and len(drum.tracks)==1 and drum.ticks_per_beat==480
    blueprints=[extract_rhythm_blueprint(drum_path,bar_index=i) for i in req['blueprint_source_bars']]
    bars=[]
    for index in range(8):
        bars.append(generate_backing_steps(blueprints[index%2],note=req['baseline_root'],
                       rules=BackingRules(duration_steps=1,rhythm_mode='phrase4'),phrase_bar_index=index))
    single=assemble_guitar(bars,req['bpm']);groups,end=pitch.groups(single)
    wanted=[]
    for g in groups:
        bar=g['start']//1920;step=g['start']%1920/120
        value=.125 if g['velocity']<96 else .25
        if g['velocity']==100:value=.5
        if bar in (3,7) and g is [x for x in groups if x['start']//1920==bar][-1]:value=.5
        wanted.append(value)
    contrast,safety=gate.change_gates(single,wanted)
    selected=set()
    for i,g in enumerate(groups):
        bar=g['start']//1920;step=g['start']%1920/120
        if step in req['power5_steps_by_bar'][bar]:selected.add(i)
    common=voicing.set_voicings(contrast,selected,scale_pitch_classes=req['scale_pitch_classes'],note_range=req['guitar_note_range'])
    assert len(selected)==req['expected_chord_groups']==26
    return common,drum_path,{'drum_generation':result.metadata,'blueprints':[asdict(bp) for bp in blueprints],
                            'gate_design':safety,'power5_group_indices':sorted(selected),
                            'note':'Only first two valid source drum bars define the recurring straight-grid guitar Blueprint; later drum events retain original performance/timing. No rejected source bar was forced to grid.'}


def run(request_path,output,*,project_root='.'):
    root=Path(project_root).resolve();req=json.loads(Path(request_path).read_text())
    for record in req['protected_audition_sources']:
        if pitch.sha(root/record['path'])!=record['sha256']:raise ValueError('Past preferred output hash changed')
    if pitch.sha(root/req['chorus_comparison']['path'])!=req['chorus_comparison']['sha256']:raise ValueError('Chorus comparison hash changed')
    out=Path(output);out.mkdir(parents=True,exist_ok=False);work=out/'generation_work';work.mkdir()
    common,drum_path,foundation=build_foundation(req,root,work)
    groups,end=pitch.groups(common);assert end==15360 and len(groups)==52
    old=mido.MidiFile(root/req['chorus_comparison']['path']);oldgroups,_=pitch.groups(old)
    assert [g['start'] for g in groups]!=[g['start'] for g in oldgroups]
    pitch.save_new(common,work/'common_rhythm_voicing.mid')
    for name in ['drums_shared.mid']+[f'drums_{label}.mid' for label in 'ABC']:
        with (out/name).open('xb') as f:f.write(drum_path.read_bytes())
    initial=[req['baseline_root']]*len(groups)
    A=assignments(initial,groups,req['A_pitch_assignments'])
    assert len(req['A_pitch_assignments'])==len(groups)
    B=assignments(A,groups,req['B_seed_assignments'])
    mappings=[]
    for target in (2,4,6):B,mapping=reuse_by_position(B,groups,0,target);mappings.append({'target_first_bar_1based':target+1,**mapping})
    C=assignments(B,groups,req['C_response_assignments'])
    roots_by_label={'A':A,'B':B,'C':C};names={'A':'current_motion','B':'unified_motif','C':'unified_development'}
    report={'request':req,'foundation':foundation,'common_groups':len(groups),'common_chord_groups':26,
      'common_guitar_notes':78,'common_duration':gate.duration_metrics(common),
      'position_mapping_for_B':mappings,'candidates':{},'files':{},'automatic_adoption':False,'defaults_modified':False,
      'chorus_comparison':{'intro_groups':len(groups),'chorus_groups':len(oldgroups),
          'onset_sets_different':True,'onset_jaccard':len({g['start'] for g in groups}&{g['start'] for g in oldgroups})/len({g['start'] for g in groups}|{g['start'] for g in oldgroups})}}
    for label,name in names.items():
        candidate=pitch.transpose_groups(common,roots_by_label[label],allowed_roots=req['allowed_roots'],
               guitar_range=req['guitar_note_range'],bass_range=req['bass_note_range'],max_jump=req['max_jump'])
        assert all(m.note%12 in req['scale_pitch_classes'] for t in candidate.tracks for m in t if m.type in {'note_on','note_off'})
        assert pitch.rhythm_signature(candidate)==pitch.rhythm_signature(common)
        gp=out/f'guitar_{label}_{name}.mid';pitch.save_new(candidate,gp)
        bp=out/f'bass_{label}_{name}.mid'
        bass=generate({'instrument':'bass','section':'intro','tempo':req['bpm'],'bars':8,
            'options':{'guitar_midi':str(gp.resolve()),'transpose_semitones':-24,'program':34,'midi_channel':2}},output_path=bp,project_root=root)
        cg,_=pitch.groups(candidate);bg,_=pitch.groups(mido.MidiFile(bp))
        assert [(g['start'],g['end'],g['root']-24,g['velocity']) for g in cg]==[(g['start'],g['end'],g['root'],g['velocity']) for g in bg]
        band=mido.MidiFile(type=1,ticks_per_beat=480)
        band.tracks.extend([deepcopy(candidate.tracks[0]),deepcopy(mido.MidiFile(bp).tracks[0]),deepcopy(mido.MidiFile(drum_path).tracks[0])])
        main=out/f'intro_{label}_{name}.mid';pitch.save_new(band,main)
        assert density.track_chunks(main)[2]==density.track_chunks(drum_path)[0]
        metric=density.motion_metrics(roots_by_label[label],[req['baseline_root']]*len(groups))
        motif=motif_comparison(cg,roots_by_label[label]);seedmap=position_map(cg,0,2,480)
        seedtokens=tuple((t,roots_by_label[label][seedmap[t]]) for t in sorted(seedmap))
        seed_deltas=tuple(b[1]-a[1] for a,b in zip(seedtokens,seedtokens[1:]))
        returns=[];contour_returns=[]
        for bar in (0,2,4,6):
            own=position_map(cg,bar,2,480)
            tokens=tuple((t,roots_by_label[label][own[t]]) for t in sorted(own))
            if tokens==seedtokens:returns.append([bar+1,bar+2])
            ds=tuple(b[1]-a[1] for a,b in zip(tokens,tokens[1:]))
            if [x[0] for x in tokens]==[x[0] for x in seedtokens] and ds==seed_deltas:contour_returns.append([bar+1,bar+2])
        metric.update({'motif_1_2_vs_5_6':motif,'motif_pitch_variety':len({p for t,p in seedtokens}),
          'first_two_bar_root_pattern_occurrences_including_original':returns,
          'first_two_bar_contour_occurrences_including_original':contour_returns,
          'bar4_vs_bar2':motif_comparison(cg,roots_by_label[label],1,3,bars=1),
          'bar8_vs_bar6':motif_comparison(cg,roots_by_label[label],5,7,bars=1),
          'direction_runs':directional_runs(roots_by_label[label]),
          'max_up_run':max((r['transitions'] for r in directional_runs(roots_by_label[label]) if r['direction']=='up'),default=0),
          'max_down_run':max((r['transitions'] for r in directional_runs(roots_by_label[label]) if r['direction']=='down'),default=0),
          'lowest_guitar_note':min(n['pitch'] for g in cg for n in g['members']),
          'highest_guitar_note':max(n['pitch'] for g in cg for n in g['members']),
          'range_violations':0,'root_plan':roots_by_label[label],
          'per_bar_roots':[[g['root'] for g in cg if g['start']//1920==bar] for bar in range(8)],
          'bass_exact_minus24_on_off_velocity':True,'drum_track_bytes_identical':True,
          'bass_metadata':bass.metadata})
        report['candidates'][label]=metric
        for p in [gp,bp,main]:report['files'][p.name]=pitch.sha(p)
    assert report['candidates']['A']['motif_1_2_vs_5_6']['root_match_rate_on_common_positions']<1
    assert all(report['candidates'][x]['motif_1_2_vs_5_6']['root_match_rate_on_common_positions']==1 for x in ['B','C'])
    for p in out.glob('drums*.mid'):report['files'][p.name]=pitch.sha(p)
    with (out/'verification.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    with (out/'request.json').open('x') as f:json.dump(req,f,ensure_ascii=False,indent=2);f.write('\n')
    return report


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('request');parser.add_argument('output');parser.add_argument('--project-root',default='.')
    a=parser.parse_args();r=run(a.request,a.output,project_root=a.project_root)
    print({k:{'motion':m['actual_motion_rate'],'motif_match':m['motif_1_2_vs_5_6']['root_match_rate_on_common_positions']} for k,m in r['candidates'].items()})
