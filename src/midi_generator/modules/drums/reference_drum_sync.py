"""Opt-in original support drums for fixed Phase 12 guitars; no transcription."""
from copy import deepcopy
import math
from random import Random
import mido
from midi_generator.core.models import GenerationRequest
from midi_generator.core.midi_io import GM_DRUM_MAP
from midi_generator.modules.drums.generator import _make_output_midi, _select_drum_tracks
from midi_generator.modules.guitar.reference_composition import _groups

BAR=1920
TPB=480


def compare_reference_phases(groove):
    """Compare ALL raw observed attacks, not only those accepted near the C grid."""
    def score(events,spacing,phase):
        weights=[max(e['strength'],.05) for e in events];den=sum(weights)
        residual=[abs((e['section_beat']-phase+spacing/2)%spacing-spacing/2) for e in events]
        return dict(mean_absolute_residual_beats=sum(w*r for w,r in zip(weights,residual))/den if den else None,
                    within_04_beat_share=sum(w for w,r in zip(weights,residual) if r<=.04)/den if den else None)
    results=[]
    for half in [0,1]:
        for band in ['low','body','high']:
            events=[e for e in groove['observations'] if e['band']==band and half*16<=e['section_beat']<(half+1)*16]
            for spacing in [.5,.25]:
                phases=[(i-50)*spacing/100 for i in range(101)]
                candidates=[dict(phase_beats=p,**score(events,spacing,p)) for p in phases]
                valid=[c for c in candidates if c['mean_absolute_residual_beats'] is not None]
                best=min(valid,key=lambda c:c['mean_absolute_residual_beats']) if valid else None
                results.append(dict(bars=[half*4+1,half*4+4],band=band,event_count=len(events),spacing_beats=spacing,
                    at_C=score(events,spacing,0),best=best,scan=candidates))
    origins=[]
    for offset in range(4):
        roles={}
        for band in ['low','high']:
            events=[e for e in groove['observations'] if e['band']==band];den=sum(max(e['strength'],.05) for e in events)
            def share(targets):
                return sum(max(e['strength'],.05) for e in events if min(abs(((e['section_beat']-offset-t)+2)%4-2) for t in targets)<=.08)/den if den else None
            roles[band]=dict(downbeat_share=share([0]),backbeat_2_4_share=share([1,3]),half_time_beat3_share=share([2]))
        origins.append(dict(quarter_beat_offset=offset,roles=roles))
    return dict(phase_comparisons=results,coarse_bar_origin_candidates=origins,
        interpretation='Band attacks are observed; drum roles/downbeat labels are inferred. Subdivision phase cannot establish which beat is bar one. No MIDI/audio shift applied.',
        grid_adopted='C retained as user-adopted working hypothesis, not algorithmically certified',meter_status='4/4 hypothesis')


def compose_original_drums(guitar, groove, *, profile, mapping, seed=42):
    if profile not in ('c','d'):raise ValueError('Only C/D support profiles')
    if type(seed) is not int:raise ValueError('seed must be an integer')
    if guitar.ticks_per_beat!=480 or len(guitar.tracks)!=1 or groove['section']['bars']!=8:raise ValueError('Requires fixed 480 TPB eight-bar guitar and groove map')
    if sum(m.time for m in guitar.tracks[0])!=8*BAR:raise ValueError('Guitar must be exactly eight bars')
    meters={(m.numerator,m.denominator) for m in guitar.tracks[0] if m.type=='time_signature'}
    if meters!={(4,4)}:raise ValueError('Fixed 4/4 hypothesis required')
    bpm=groove['section']['bpm'];tempos={m.tempo for m in guitar.tracks[0] if m.type=='set_tempo'}
    if tempos!={mido.bpm2tempo(bpm)}:raise ValueError('Guitar tempo differs from adopted context')
    for key in ['kick','snare','closed_hat','open_hat','crash']:
        if type(mapping[key]) is not int or mapping[key] not in GM_DRUM_MAP:raise ValueError('Expected explicit GM percussion notes')
    rng=Random(seed);groups=_groups(guitar);notes=[];decisions=[]
    def add(tick,kind,velocity,reason,bar):
        note=mapping[kind]
        if not any(n['start']==tick and n['note']==note for n in notes):
            notes.append(dict(start=tick,end=min(8*BAR,tick+45),note=note,velocity=velocity,kind=kind,bar=bar+1,reason=reason))
    for bar in range(8):
        local=[g for g in groups if g['start']//BAR==bar];row=groove['bars'][bar]
        def accent(g):
            s=(g['start']%BAR)/120
            return row['cells'][int(s)]['accent_score'] if s.is_integer() else 0
        if profile=='d':
            # Original guitar remains untouched. Drums keep the quarter pulse
            # through normal rests, but bar 7's special break is a full stop.
            selected=[local[0]]+sorted([g for g in local if g['start']%BAR>=720],key=lambda g:(-accent(g),-g['velocity'],g['start']))[:2]
            for g in selected:add(g['start'],'kick',116 if g['velocity']>=108 else 105,'guitar accent; ranked using mix-audio proxy when available',bar)
            for tick in [0,480,960,1440]:
                if bar==6 and 225<tick<1440:continue
                add(bar*BAR+tick,'closed_hat',84 if tick%960==0 else 72,'quarter-note count anchor; deliberately remains during normal guitar rests',bar)
            if bar!=6:add(bar*BAR+960,'snare',110,'composed half-time beat3, not reference snare transcription',bar)
            if bar==0:add(0,'crash',100,'first phrase entry',bar)
            if bar==6:add(bar*BAR+1440,'crash',108,'restart after bar7 full break',bar)
            if bar==7:add(local[-1]['start'],'crash',104,'final B return',bar)
            decisions.append(dict(bar=bar+1,rest_policy='full gap after burst until final restart' if bar==6 else 'quarter hats and half-time snare remain across selected guitar rests',first_guitar_beat=(local[0]['start']%BAR)/480))
        else:
            add(bar*BAR,'kick',112,'harmonic bar anchor',bar)
            add(bar*BAR+960,'kick',98,'meter pulse supporting sustained guitar',bar)
            later=sorted([g for g in local if g['start']%BAR>=720],key=lambda g:(-accent(g),-g['velocity'],g['start']))
            if later:add(later[0]['start'],'kick',108,'selected later guitar/mix accent, not every guitar note',bar)
            for tick in range(0,BAR,240):
                if tick==0 and bar in (2,6):continue
                add(bar*BAR+tick,'closed_hat',80 if tick%480==0 else 64+rng.randrange(3)*3,'eighth-note pulse',bar)
            for tick in (480,1440):add(bar*BAR+tick,'snare',108,'original composed backbeat2/4',bar)
            if bar in (0,4):add(bar*BAR,'crash',102,'alternating two-bar harmonic block entry',bar)
            elif bar in (2,6):add(bar*BAR,'open_hat',94,'two-bar harmonic change without extra crash',bar)
            decisions.append(dict(bar=bar+1,rest_policy='support long guitar notes with eighth pulse; no fill',harmony_block=bar//2))
    notes.sort(key=lambda n:(n['start'],n['note']))
    events=[]
    for n in notes:
        events.extend([(n['start'],1,mido.Message('note_on',channel=9,note=n['note'],velocity=n['velocity'])),(n['end'],0,mido.Message('note_off',channel=9,note=n['note'],velocity=0))])
    tr=mido.MidiTrack([mido.MetaMessage('track_name',name='Original support drums')]);last=0
    for tick,_,msg in sorted(events,key=lambda x:(x[0],x[1],x[2].note)):tr.append(msg.copy(time=tick-last));last=tick
    tr.append(mido.MetaMessage('end_of_track',time=8*BAR-last));source=mido.MidiFile(type=0,ticks_per_beat=480);source.tracks.append(tr)
    tracks,_=_select_drum_tracks(source)
    req=GenerationRequest(instrument='drums',section='breakdown' if profile=='d' else 'chorus',tempo=bpm,bars=8,time_signature=(4,4),seed=seed)
    drums,operations,warnings=_make_output_midi(source,tracks,req,9,8)
    # Original guitar track (including every delta and CC/program message) copied intact.
    # It owns the one common tempo/meter; remove only duplicate drum meta events.
    band=mido.MidiFile(type=1,ticks_per_beat=480);band.tracks.append(mido.MidiTrack([m.copy() for m in guitar.tracks[0]]))
    stripped=mido.MidiTrack();absolute=0;previous=0
    for msg in drums.tracks[0]:
        absolute+=msg.time
        if msg.type in ('set_tempo','time_signature'):continue
        stripped.append(msg.copy(time=absolute-previous));previous=absolute
    band.tracks.append(stripped)
    return drums,band,dict(profile=profile,seed=seed,mapping=mapping,notes=notes,bar_decisions=decisions,
        reuse='Existing _select_drum_tracks and _make_output_midi; new opt-in composition, not catalog selection or audio transcription',
        operations=operations,warnings=warnings,reference_role_confidence='low; mix-band proxies inform accent ranking only')


def analyze_eighth_positions(guitar, drum_settings, *, bpm):
    groups=_groups(guitar);notes=drum_settings['notes'];rows=[]
    for bar in range(8):
        for pos in range(1,9):
            tick=bar*BAR+(pos-1)*240;attacks=[g for g in groups if g['start']==tick];active=[g for g in groups if g['start']<=tick<g['end']]
            kicks=[n for n in notes if n['kind']=='kick' and n['start']==tick]
            following=min((g['start']-tick for g in groups if g['start']>=tick and g['start']//BAR==bar),default=None)
            rows.append(dict(bar=bar+1,eighth_position=pos,odd_position_focus=pos in (1,3,5,7),tick=tick,seconds=tick/480*60/bpm,
                guitar_attack=bool(attacks),guitar_sustain=bool(active),guitar_groups=active,
                guitar_ending_here=[g for g in groups if g['end']==tick],
                next_onset_delay_beats=following/480 if following is not None else None,kick=bool(kicks),
                other_drum_notes=[n['kind'] for n in notes if n['start']==tick and n['kind']!='kick'],
                rest_interpretation='saved composed Stop&Go rest/displacement; listening intention still subject to user review' if not active else 'attack' if attacks else 'sustain, not missing note',
                accent_relation='shared kick attack' if attacks and kicks else 'guitar only' if attacks else 'drum pulse without new guitar attack'))
    return rows
