"""Opt-in original composition from a shared audio accent map.

Reuses Phase 10's style renderer as a motif proposal; never consumes guitar audio,
never changes its E-minor defaults, and never labels audio proxies as real drums.
"""
from collections import defaultdict
from io import BytesIO
import math
from random import Random
import mido
from .riff_styles import render_riff_style

PATTERNS = {'a':'tight_groove','b':'stop_and_go','c':'chord_driven','d':'stutter_breakdown'}
TPB=480


def _groups(midi):
    tick=0;active={};groups={}
    for msg in midi.tracks[0]:
        tick+=msg.time
        if msg.type=='note_on' and msg.velocity:
            active[msg.note]=(tick,msg.velocity)
        elif msg.type=='note_off':
            start,velocity=active.pop(msg.note)
            key=(start,tick,velocity);groups.setdefault(key,[]).append(msg.note)
    return [dict(start=k[0],end=k[1],velocity=k[2],notes=sorted(v)) for k,v in sorted(groups.items())]


def compose_reference_guitar(groove_map, *, pattern, seed=42):
    """Return (MIDI, provenance/settings). Supports explicit B-minor 4/8-bar prototype."""
    if pattern not in PATTERNS:raise ValueError('pattern must be a, b, c or d')
    if type(seed) is not int:raise ValueError('seed must be an integer')
    section=groove_map['section'];bars=section['bars'];bpm=section['bpm'];rows=groove_map['bars']
    if type(bars) is not int or bars not in (4,8) or len(rows)!=bars:raise ValueError('Expected 4 or 8 complete groove bars')
    if isinstance(bpm,bool) or not isinstance(bpm,(int,float)) or not math.isfinite(bpm) or bpm<=0:raise ValueError('Invalid BPM')
    if section['meter']!=[4,4]:raise ValueError('Only the explicit 4/4 hypothesis is supported')
    for i,row in enumerate(rows):
        if row['bar_index']!=i or len(row['cells'])!=16 or [c['step'] for c in row['cells']]!=list(range(16)):
            raise ValueError('Groove cells must be ordered complete sixteenth grids')
        for c in row['cells']:
            if not math.isfinite(c['accent_score']) or c['accent_score']<0:raise ValueError('Invalid accent score')
        for step in row['major_accent_steps']+row['kick_candidate_steps']:
            if type(step) is not int or not 0<=step<16:raise ValueError('Invalid candidate step')
    if sum(bool(r['major_accent_steps']) for r in rows)<bars//2:
        raise ValueError('Insufficient audio-derived accents; do not claim reference-guided generation')
    # Abstract candidate rows for a STYLE PROPOSAL only. No drum MIDI is invented.
    proposal=render_riff_style(PATTERNS[pattern],tempo=bpm,bars=bars,channel=2,seed=seed,
                              drum={'bar_patterns':[{'kick_steps':r['kick_candidate_steps']} for r in rows]})
    base=_groups(proposal);rng=Random(seed);result=[];provenance=[]
    motif_anchors={}
    for role in range(2):
        r=rows[role];rank=sorted(r['cells'],key=lambda c:(-c['accent_score'],c['step']))
        motif_anchors[role]=[c['step'] for c in rank if c['observation_ids'] and c['accent_score']>0][:3]
    for bar in range(bars):
        row=rows[bar];role=bar%2;anchors=motif_anchors[role]
        local=sorted(row['major_accent_steps'],key=lambda s:(-row['cells'][s]['accent_score'],s))
        if not anchors:anchors=local
        if not anchors:anchors=[0] # Explicit composition fallback only on a minority empty bar.
        strong=anchors[0];late=max(anchors);is_end=bar%4==3
        originals=[g for g in base if g['start']//1920==bar]
        edits=[];audio_slots=set()
        if pattern=='a':
            # Select a small subset of LOW proxies + generic accents; not a kick copy.
            low=sorted(row['kick_candidate_steps'],key=lambda s:-row['cells'][s]['kick_candidate_score'])[:2]
            chosen=set(low+anchors[:2]);audio_slots.update(chosen)
            supplements=[g['start']%1920//120 for g in originals if g['start']%240==0]
            chosen.update(s for s in supplements[:3] if all(abs(s-a)>=2 for a in chosen))
            if is_end and local:chosen.add(local[-1]);audio_slots.add(local[-1])
            starts=sorted(s*120 for s in chosen)
        elif pattern=='b':
            # One shared accent and displaced offbeat answers, with a middle stop.
            displaced=next((s for s in anchors if s%2),min(15,strong+1))
            chosen={strong,displaced,min(15,displaced+2)}
            restart=next((s for s in sorted(anchors,reverse=True) if s>=10),12+(role%2))
            chosen.add(restart);audio_slots.add(strong)
            if is_end:chosen.add(15)
            starts=sorted(s*120 for s in chosen)
        elif pattern=='c':
            # Existing sustained chord rhythm, with the opening change on an actual accent.
            motif_originals=[g for g in base if g['start']//1920==role]
            starts=[g['start']%1920 for g in motif_originals]
            if is_end:starts[-1]=min(1800,starts[-1]+120)
            target=min(anchors,key=lambda s:abs(s*120-starts[0]))*120
            starts[0]=target;audio_slots.add(target//120)
            if role==1:starts.append(min(1800,late*120));audio_slots.add(late)
            starts=sorted(set(starts))
        else:
            # Keep Phase 10's coherent burst/stop/restart; anchor the restart to the mix.
            starts=[g['start']%1920 for g in originals]
            restarts=[s for s in anchors if s>=6]
            target=min(restarts,key=lambda s:abs(s*120-960)) if restarts else strong
            # Preserve the early burst and its following stop, never insert unrelated bursts.
            starts=[x for x in starts if x<300]+[max(720,target*120),max(1440,late*120)]
            audio_slots.update({max(720,target*120)//120,max(1440,late*120)//120})
            starts=sorted(set(starts))
        for i,start in enumerate(starts):
            stop=starts[i+1] if i+1<len(starts) else 1920
            slot=start/120;observed=slot.is_integer() and int(slot) in audio_slots and bool(row['cells'][int(slot)]['observation_ids'])
            # Pitch choices are original composition, not inferred from drums or reference harmony.
            if pattern=='c':
                root=[47,43,45,47][bar%4]
                if i==len(starts)-1 and bar%4==1:root=50
                chord=i%3!=1;gate=480 if i==0 else (360 if i%2==0 else 120)
            elif pattern=='b':
                root=48 if i==1 else 47;chord=i!=1;gate=180 if i==0 else (300 if i==len(starts)-1 else 90)
            elif pattern=='d':
                root=48 if is_end and i==len(starts)-2 else 47
                burst=(i+1<len(starts) and starts[i+1]-start==60) or (i>0 and start-starts[i-1]==60)
                chord=not burst or i==0;gate=45 if burst else (360 if i==len(starts)-1 else 120)
            else:
                root=50 if is_end and i==len(starts)-1 else 47
                chord=i==0 or (i+bar)%3==0;gate=90 if i%2 else 120
            gate=min(gate,stop-start)
            velocity=112 if observed else (104 if i==0 else 82+rng.randrange(3)*4)
            result.append(dict(start=bar*1920+start,end=bar*1920+start+gate,root=root,
                               notes=[root,root+7] if chord else [root],velocity=velocity))
            provenance.append(dict(bar_index=bar,step=slot,source='audio_selected_anchor' if observed else 'composition_style_or_motif',
                observation_ids=row['cells'][int(slot)]['observation_ids'] if observed else [],
                reused_motif_role=role,metric_position=slot/4))
    track=mido.MidiTrack([mido.MetaMessage('track_name',name=f'Original B minor / {pattern}'),
        mido.MetaMessage('set_tempo',tempo=mido.bpm2tempo(bpm)),mido.MetaMessage('time_signature',numerator=4,denominator=4),
        mido.MetaMessage('key_signature',key='Bm'),mido.Message('program_change',channel=1,program=30)])
    previous=0
    for g in result:
        for typ,tick in [('note_on',g['start']),('note_off',g['end'])]:
            for note in g['notes']:
                track.append(mido.Message(typ,channel=1,note=note,velocity=g['velocity'] if typ=='note_on' else 0,time=tick-previous));previous=tick
    track.append(mido.MetaMessage('end_of_track',time=bars*1920-previous));midi=mido.MidiFile(type=0,ticks_per_beat=TPB);midi.tracks.append(track)
    harmonic={'a':dict(progression=['Bm pedal'],pedal='B',non_diatonic=[]),
              'b':dict(progression=['Bm','C','Bm'],pedal='B',non_diatonic=['C natural (bII tension)']),
              'c':dict(progression=['Bm','G','A','Bm'],pedal=None,non_diatonic=[]),
              'd':dict(progression=['Bm pedal with short C tension'],pedal='B',non_diatonic=['C natural (bII tension)'])}[pattern]
    metadata=dict(pattern=pattern,seed=seed,key='B minor',scale=['B','C#','D','E','F#','G','A'],harmony=harmonic,
        voicing='single or root+7 power5, no thirds; chord names describe intended harmony, not fully voiced triads',
        pitch_range=[min(min(g['notes']) for g in result),max(max(g['notes']) for g in result)],
        tuning='unknown; MIDI register choice, no Drop B assumption',declared_pitch_bounds=[40,76],
        source_section=section,style_proposal=PATTERNS[pattern],api_scope='independent opt-in composition function; Phase 10 defaults unchanged',
        groups=result,onset_provenance=provenance,audio_anchor_group_count=sum(p['source']=='audio_selected_anchor' for p in provenance),
        composition_hypothesis='Existing style motifs adapted with observed mix accent anchors, B-minor pedal/borrowed-bII/diatonic designs specified by user. Not reference guitar/harmony transcription.')
    return midi,metadata


def midi_bytes(midi):
    output=BytesIO();midi.save(file=output);return output.getvalue()
