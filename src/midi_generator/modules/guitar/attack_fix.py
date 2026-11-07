"""Explicit, bounded attack insertion preserving every existing absolute event."""
from copy import deepcopy
import math
import mido


def add_bar_head_attacks(source, *, bars, note=47, duration_beats=.25):
    if source.ticks_per_beat!=480 or len(source.tracks)!=1 or source.type!=0:raise ValueError('Requires original single-track 480 TPB guitar')
    if not bars or len(set(bars))!=len(bars) or any(type(b) is not int or not 1<=b<=8 for b in bars):raise ValueError('Specify unique one-based bars within the eight-bar source')
    if type(note) is not int or not 0<=note<=127:raise ValueError('Invalid note')
    if isinstance(duration_beats,bool) or not isinstance(duration_beats,(int,float)) or not math.isfinite(duration_beats) or duration_beats<=0:raise ValueError('Invalid duration')
    length=duration_beats*480
    if abs(length-round(length))>1e-9:raise ValueError('Duration not exactly representable at source resolution')
    length=round(length)
    if length<=0:raise ValueError('Duration is below MIDI resolution')
    events=[];tick=0;active={};intervals=[]
    for order,msg in enumerate(source.tracks[0]):
        tick+=msg.time;events.append((tick,1,order,msg.copy(time=0)))
        if msg.type=='note_on' and msg.velocity:
            if (msg.channel,msg.note) in active:raise ValueError('Overlapping source note')
            active[msg.channel,msg.note]=(tick,msg.velocity)
        elif msg.type=='note_off' or (msg.type=='note_on' and not msg.velocity):
            start,velocity=active.pop((msg.channel,msg.note));intervals.append(dict(start=start,end=tick,note=msg.note,channel=msg.channel,velocity=velocity))
    if active or tick!=15360:raise ValueError('Source must have paired notes and eight full bars')
    additions=[]
    for bar in sorted(bars):
        start=(bar-1)*1920
        if any(n['start']==start for n in intervals):raise ValueError('Bar head already has an attack; do not double it')
        candidates=[n for n in intervals if start<n['start']<start+1920]
        if not candidates:raise ValueError('No existing phrase in requested bar')
        first=min(n['start'] for n in candidates);chord=[n for n in candidates if n['start']==first]
        if note not in [n['note'] for n in chord]:raise ValueError('Requested pedal is not in the existing first harmony')
        channel=next(n['channel'] for n in chord if n['note']==note);velocity=next(n['velocity'] for n in chord if n['note']==note)
        end=min(start+length,first)
        if any(n['note']==note and n['channel']==channel and n['start']<end and n['end']>start for n in intervals):raise ValueError('Insertion would overlap an existing same-pitch note')
        additions.append(dict(bar=bar,start_tick=start,end_tick=end,note=note,channel=channel,velocity=velocity,requested_duration_beats=duration_beats,actual_duration_beats=(end-start)/480,capped_to_existing_attack=end!=start+length,existing_first_attack_tick=first,existing_first_chord=sorted(n['note'] for n in chord)))
        # Off precedes the untouched existing On at their shared tick.
        events.append((end,0,len(events),mido.Message('note_off',channel=channel,note=note,velocity=0)))
        events.append((start,2,len(events),mido.Message('note_on',channel=channel,note=note,velocity=velocity)))
    fixed=deepcopy(source);tr=mido.MidiTrack();previous=0
    for when,_,_,msg in sorted(events,key=lambda e:e[:3]):tr.append(msg.copy(time=when-previous));previous=when
    fixed.tracks[0]=tr
    return fixed,additions
