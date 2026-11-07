"""Phase 12 opt-in eight-bar C/D development; Step 1/defaults stay untouched."""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import mido
from midi_generator.reference_analysis.groove_map import analyze_audio_groove

BAR=1920
TPB=480


def extend_reference_groove(first, audio_path):
    """Preserve the four-bar analysis verbatim; analyze only the next four bars."""
    s=first['section']
    if s['bars']!=4 or len(first['bars'])!=4:raise ValueError('Expected saved Step 1 four-bar map')
    if hashlib.sha256(Path(audio_path).read_bytes()).hexdigest()!=first['source']['sha256']:
        raise ValueError('Reference audio differs from Step 1 source hash')
    tail=analyze_audio_groove(audio_path,start_seconds=s['end_seconds'],bars=4,bpm=s['bpm'],bar_origin_seconds=s['bar_origin_seconds'])
    merged=deepcopy(first);offsets={o['id']:'tail_'+o['id'] for o in tail['observations']}
    for row in tail['bars']:
        row=deepcopy(row);row['bar_index']+=4
        for cell in row['cells']:cell['observation_ids']=[offsets[i] for i in cell['observation_ids']]
        merged['bars'].append(row)
    for obs in tail['observations']:
        obs=deepcopy(obs);obs['id']=offsets[obs['id']];obs['section_beat']+=16;obs['nearest_sixteenth']+=64;merged['observations'].append(obs)
    merged['section']['bars']=8;merged['section']['end_seconds']=tail['section']['end_seconds']
    merged['analysis_blocks']=[dict(bars=[1,4],method=deepcopy(first['method']),source='saved_step1_unchanged'),dict(bars=[5,8],method=tail['method'],source='same_method_new_four_bar_window')]
    merged['normalization_note']='Peak/MAD statistics are evaluated separately per four-bar block, retaining all Step 1 observations/cells. Cross-block strengths are not calibrated instrument amplitudes.'
    merged['boundary_policy']='Existing bar-4 phrase boundary retained; bar 8 is final phrase/window end.'
    for i in range(8):
        for j in range(max(4,i+1),8):
            a=[c['accent_score'] for c in merged['bars'][i]['cells']];b=[c['accent_score'] for c in merged['bars'][j]['cells']]
            den=math.sqrt(sum(x*x for x in a)*sum(x*x for x in b))
            merged['bar_similarity'].append(dict(bars=[i,j],cosine=sum(x*y for x,y in zip(a,b))/den if den else None,status='inferred',meaning='accent-grid resemblance, not confirmed motif identity'))
    obs=merged['observations'];eligible=sum(o['composition_grid_eligible'] for o in obs)
    merged['summary'].update(observation_count=len(obs),grid_eligible_count=eligible,grid_eligible_fraction=eligible/len(obs) if obs else 0,
        mean_accent_score=sum(c['accent_score'] for b in merged['bars'] for c in b['cells'])/128)
    merged['extension_validation']={'first_four_bars_exact':merged['bars'][:4]==first['bars'],'first_observations_exact':merged['observations'][:len(first['observations'])]==first['observations']}
    return merged


def render_developed_groups(groups, *, bpm, bars=8, name='Developed Guitar'):
    if bars!=8:raise ValueError('Eight-bar prototype only')
    tr=mido.MidiTrack([mido.MetaMessage('track_name',name=name),mido.MetaMessage('set_tempo',tempo=mido.bpm2tempo(bpm)),mido.MetaMessage('time_signature',numerator=4,denominator=4),mido.MetaMessage('key_signature',key='Bm'),mido.Message('program_change',channel=1,program=30)])
    previous=0
    for g in groups:
        if not previous<=g['start']<g['end']<=bars*BAR:raise ValueError('Overlapping/invalid group timing')
        if len(set(g['notes']))!=len(g['notes']) or any(type(n) is not int or not 40<=n<=76 for n in g['notes']):raise ValueError('Invalid explicit prototype pitch range')
        for kind,tick in [('note_on',g['start']),('note_off',g['end'])]:
            for n in g['notes']:
                tr.append(mido.Message(kind,channel=1,note=n,velocity=g['velocity'] if kind=='note_on' else 0,time=tick-previous));previous=tick
    tr.append(mido.MetaMessage('end_of_track',time=bars*BAR-previous));mid=mido.MidiFile(type=0,ticks_per_beat=TPB);mid.tracks.append(tr);return mid


def develop_reference_eight_bars(groove, step1, *, pattern):
    """Reuse approved Step 1 motif events, with two-bar harmony and explicit gates."""
    if pattern not in ('c','d'):raise ValueError('Only approved C/D are developed')
    if groove['section']['bars']!=8 or len(groove['bars'])!=8:raise ValueError('Requires an eight-bar groove map')
    if step1['key']!='B minor':raise ValueError('Requires explicit B minor setting')
    saved=step1['patterns'][pattern]
    if saved['source_section']['start_seconds']!=groove['section']['start_seconds']:raise ValueError('Source window start mismatch')
    base=saved['groups'];groups=[];edits=[];harmonies=[]
    if pattern=='c':
        for block,(label,root,third) in enumerate([('Bm',47,50),('G',43,47),('A',45,49),('Bm',47,50)]):
            harmonies.append(dict(bars=[block*2+1,block*2+2],start_tick=block*2*BAR,label=label,root=root,voicing='root+7 power5; thirds/fifths may appear in separate single-note replies'))
    else:
        for block,(label,notes) in enumerate([('B5 pedal',[47,54]),('G/B',[47,55,62]),('A5/B (no third)',[47,57,64]),('B5 return + brief C tension',[47,54])]):
            harmonies.append(dict(bars=[block*2+1,block*2+2],start_tick=block*2*BAR,label=label,pedal_note=47,accent_notes=notes))
    for bar in range(8):
        role=bar%2;block=bar//2;row=groove['bars'][bar]
        source_bar=role if pattern=='c' else bar%4
        proposals=[deepcopy(g) for g in base if g['start']//BAR==source_bar]
        for idx,g in enumerate(proposals):
            start=g['start']%BAR;duration=g['end']-g['start'];source_start=g['start'];source_notes=list(g['notes']);change=[]
            if pattern=='c':
                root=harmonies[block]['root'];third=[50,47,49,50][block]
                # Keep the recognized phrase; omit only the weak offbeat reply
                # in each opening bar to make room for a real half-note sustain.
                if role==0 and idx==1:
                    edits.append(dict(bar=bar+1,source_start_tick=source_start,action='omit_weak_reply_for_two_beat_opening'));continue
                if idx==0:
                    notes=[root,root+7];duration=960 if role==0 else 480;change.append('held_harmonic_anchor')
                elif len(source_notes)==1:
                    tone=third if idx==1 else root+7
                    if bar==7 and idx==len(proposals)-1:tone=root
                    notes=[tone];duration=240;change.append('diatonic_single_note_reply')
                else:
                    notes=[root,root+7];duration=240 if role==0 else 480
                # One final-bar rhythmic answer, not a new unrelated motif.
                if bar==7 and idx==len(proposals)-1:duration=360;change.append('final_tonic_reply')
            else:
                notes=[47,54] if len(source_notes)>1 else [47]
                # Bursts stay short and on B; change harmony only on restart/tail accents.
                if start>=720 and len(source_notes)>1:notes=list(harmonies[block]['accent_notes']);change.append('two_bar_upper_voice_colour')
                if bar==6 and idx==len(proposals)-2:
                    edits.append(dict(bar=7,source_start_tick=source_start,action='omit_restart_for_explicit_break_before_final_return'));continue
                if bar in (5,7) and idx==len(proposals)-2:
                    notes=[48];duration=min(duration,120);change.append('brief_C_tension_before_B_return')
                if bar==7 and idx==len(proposals)-1:notes=[47,54];change.append('final_B_return')
            # Preserve raw motif onset. Use added-region audio accents for dynamics,
            # never force an uncertain kick into a new event.
            step=start/120;observed=step.is_integer() and bool(row['cells'][int(step)]['observation_ids'])
            score=row['cells'][int(step)]['accent_score'] if observed else 0
            velocity=max(g['velocity'],108) if score>=.3 and start>=720 else g['velocity']
            groups.append(dict(start=bar*BAR+start,end=bar*BAR+start+duration,notes=notes,root=notes[0],velocity=velocity,
                source_motif_bar=source_bar+1,source_motif_start_tick=source_start,source_onset_preserved=True,
                audio_observation_ids=row['cells'][int(step)]['observation_ids'] if observed else [],changes=change))
    groups.sort(key=lambda g:g['start'])
    for i,g in enumerate(groups):
        boundary=(g['start']//BAR+1)*BAR;next_start=groups[i+1]['start'] if i+1<len(groups) else 8*BAR
        g['requested_duration_ticks']=g['end']-g['start'];g['end']=min(g['end'],boundary,next_start)
        # Leave native-synth release room at the final song edge.
        if i==len(groups)-1:g['end']=min(g['end'],8*BAR-120)
        if g['end']<=g['start']:raise ValueError('Invalid developed gate')
        g['gate_capped']=g['end']-g['start']!=g['requested_duration_ticks']
    metadata=dict(pattern=pattern,key='B minor',bpm=groove['section']['bpm'],bars=8,seed=step1['seed'],
        development='two_bar_harmony_and_gate_roles' if pattern=='c' else 'pedal_upper_colours_stop_go_and_final_break',
        source='Saved Step 1 generated motifs, NOT guitar transcription',source_groove=groove['section'],
        harmony_blocks=harmonies,groups=groups,omitted_source_events=edits,
        note_range=[min(min(g['notes']) for g in groups),max(max(g['notes']) for g in groups)],
        tuning='unknown; no Drop B inference; explicit MIDI register within 40..76',
        confidence='Composition hypothesis. Instrument roles of source accents unverified; listening quality not evaluated.',
        non_diatonic=['C natural at two brief D accents'] if pattern=='d' else [])
    mid=render_developed_groups(groups,bpm=metadata['bpm'],name=f'Phase12 Step2 {pattern.upper()} / B minor')
    return mid,metadata
