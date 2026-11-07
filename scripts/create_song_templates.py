"""Explicit song-template generation using the accepted arrangement processing.

Python entry point: build(config, output). The existing song arrangement entry
point also accepts these configurations. Run from the repository root.
"""
from copy import deepcopy
import json
import importlib.util
import math
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import mido
from midi_generator.api import generate

sys.path.insert(0,str(Path(__file__).resolve().parent))
if not Path(__file__).with_name('create_intro_cohesion_auditions.py').exists():
    sys.path.insert(0,str(Path.cwd()/'scripts'))
def _helper(name):
    path=Path(__file__).with_name(name+'.py')
    if not path.exists():
        path=Path.cwd()/'scripts'/(name+'.py')
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

arrange=_helper('create_song_arrangement')
groove=_helper('create_accent_grouping')
ending=_helper('create_outro_endings')
band_io=arrange.band_io
pitch=arrange.pitch
transition=arrange.transition
BAR=1920

TEMPLATES={
    'standard':(
        ('intro',8,'tight_groove','intro'),
        ('verse',16,'stop_and_go','verse'),
        ('chorus',8,'chord_driven','chorus'),
        ('breakdown',8,'stutter_breakdown','breakdown'),
        ('outro',8,'groove_burst','outro')),
    'double_chorus':(
        ('intro',8,'tight_groove','intro'),
        ('verse',16,'stop_and_go','verse'),
        ('chorus_1',8,'chord_driven','chorus'),
        ('verse_2',8,'tight_groove','verse_2'),
        ('chorus_2',8,'chord_driven','chorus'),
        ('outro',8,'groove_burst','outro')),
    'breakdown_focus':(
        ('intro',8,'tight_groove','intro'),
        ('verse',16,'stop_and_go','verse'),
        ('chorus',8,'chord_driven','chorus'),
        ('breakdown_1',8,'stutter_breakdown','breakdown'),
        ('breakdown_2',8,'groove_burst','outro'),
        ('outro',8,'stop_and_go','outro_stop')),
}


def validate_config(config):
    allowed={'bpm','key','scale','seed','song_template','ending_style','foundation_config'}
    extra=set(config)-allowed
    if extra:
        raise ValueError(f'Unknown song configuration fields: {sorted(extra)}')
    if config.get('song_template') not in TEMPLATES:
        raise ValueError('song_template must be standard, double_chorus or breakdown_focus')
    if config.get('ending_style') not in ('original','stop_final_hit'):
        raise ValueError('ending_style must be original or stop_final_hit')
    if config.get('key')!='E' or config.get('scale')!='minor':
        raise ValueError('Song templates currently support key=E, scale=minor')
    if type(config.get('seed')) is not int:
        raise ValueError('seed must be an integer')
    bpm=config.get('bpm')
    if isinstance(bpm,bool) or not isinstance(bpm,(int,float)) or not math.isfinite(bpm) or bpm<=0:
        raise ValueError('bpm must be finite and positive')
    # The accepted accent and transition processing was verified at 125 BPM.
    if bpm!=125:
        raise ValueError('Song templates currently support 125 BPM')


def extract_section(band,start,bars,work,label):
    path=work/f'extract_{label}.mid'
    pitch.save_new(band,path)
    _,notes=band_io.read_notes(path)
    stop=start+bars*BAR
    rows=pitch.groups(transition.part(band,0))[0]
    guitar=[{k:deepcopy(g[k]) for k in ('start','end','root','intervals','velocity','channel')}
            for g in rows if start<=g['start']<stop]
    for g in guitar:
        g['start']-=start
        g['end']-=start
    drums=[(a-start,b-start,n,v,ch) for a,b,n,v,ch in notes
           if ch==10 and start<=a<stop]
    assert all(0<=g['start']<g['end']<=bars*BAR for g in guitar)
    return guitar,drums


def fresh_section(config,foundation,style,ident,seed,work):
    """Generate another section through the same public guitar/drum APIs."""
    source=foundation['sections'][0]
    catalog=json.loads(Path(foundation['catalog']).read_text())
    record=next(r for r in catalog['files'] if r['path']==source['drum_source'])
    job=work/f'{ident}_catalog.json'
    role='verse' if ident=='verse_2' else 'outro'
    band_io.make_job_catalog(catalog,record,job,role)
    drums=generate(dict(instrument='drums',section=role,tempo=config['bpm'],bars=8,
                       seed=config['seed']+359),output_path=work/f'{ident}_drums.mid',catalog_path=job)
    guitar=generate(dict(instrument='guitar',section=role,tempo=config['bpm'],bars=8,
                         seed=seed,key='E minor',riff_style=style,sync_to_drums=str(drums.output_files[0])),
                    output_path=work/f'{ident}_guitar.mid')
    _,notes=band_io.read_notes(drums.output_files[0])
    return arrange.group_rows(guitar.output_files[0]),notes


def related_breakdown(guitar,breakdown):
    """Carry the first breakdown's stop/restart roles into the groove variation."""
    rows=deepcopy(guitar)
    changes=[]
    by_tick={g['start']:i for i,g in enumerate(rows)}
    for g in breakdown:
        # Strong post-burst restart roles, not the quick burst attacks.
        if g['velocity']<113 or g['end']-g['start']<240:
            continue
        index=by_tick.get(g['start'])
        if index is None:
            continue
        target=rows[index]
        target['velocity']=max(target['velocity'],g['velocity'])
        target['root']=g['root']
        target['intervals']=(0,7)
        if index and rows[index-1]['end']>g['start']-120:
            previous=rows[index-1]
            # Preserve burst integrity and avoid creating a nonpositive gate.
            if g['start']-previous['start']>180:
                previous['end']=min(previous['end'],g['start']-120)
        changes.append(g['start'])
    return rows,changes


def connect(parts,drums,specs):
    """Reuse accepted boundary roles at section-relative positions."""
    rows=deepcopy(parts)
    percussion=deepcopy(drums)
    changes=[]
    for i in range(len(specs)-1):
        ident,bars,style,_=specs[i]
        next_ident,_,next_style,_=specs[i+1]
        stop=bars*BAR
        last=rows[i][-1]
        if next_style=='chord_driven':
            rows[i+1][0]['velocity']=118
            for tick in (stop-240,stop-120):
                if not any(a<=tick<b and n==38 for a,b,n,_,_ in percussion[i]):
                    percussion[i].append((tick,tick+60,38,103 if tick%240==0 else 114,10))
            changes.append(f'{ident}→{next_ident}: snare fill and Chorus entry accent')
        elif next_style=='stutter_breakdown':
            last['end']=max(last['start']+45,min(last['end'],stop-180))
            rows[i+1][0]['velocity']=116
            changes.append(f'{ident}→{next_ident}: release before stutter and entry accent')
        elif style=='stutter_breakdown':
            # Original accepted pickup applies only when there is room for it.
            last['end']=min(last['end'],stop-240)
            if last['start']<last['end'] and last['end']<=stop-120:
                rows[i].append(dict(start=stop-120,end=stop-60,root=52,
                                     intervals=(0,),velocity=110,channel=0))
            changes.append(f'{ident}→{next_ident}: short unison pickup')
        else:
            last['end']=max(last['start']+45,min(last['end']-60,stop-120))
            changes.append(f'{ident}→{next_ident}: short breathing release')
    return rows,percussion,changes


def assemble(parts,drums,specs,config,work):
    guitar_notes=[]
    drum_notes=[]
    markers=[]
    offset=0
    section_metadata=[]
    for rows,percussion,(ident,bars,style,_) in zip(parts,drums,specs):
        markers.append((offset,ident))
        for g in rows:
            assert 0<=g['start']<g['end']<=bars*BAR
            guitar_notes.extend((g['start']+offset,g['end']+offset,g['root']+n,g['velocity'],1)
                                for n in g['intervals'])
        drum_notes.extend((a+offset,b+offset,n,v,ch) for a,b,n,v,ch in percussion)
        section_metadata.append(dict(id=ident,start_bar=offset//BAR+1,end_bar=offset//BAR+bars,
                                     style=style,groups=len(rows)))
        offset+=bars*BAR
    tempo=mido.bpm2tempo(config['bpm'])
    guitar_path=work/'arranged_guitar.mid'
    guitar=band_io.write_part(guitar_path,'Guitar',guitar_notes,offset,tempo,markers,program=31,channel=1)
    bass_path=generate(dict(instrument='bass',section='song',tempo=config['bpm'],bars=offset//BAR,
          options=dict(guitar_midi=str(guitar_path),transpose_semitones=-24,program=34,midi_channel=2)),
          output_path=work/'arranged_bass.mid').output_files[0]
    bass=mido.MidiFile(bass_path)
    # Bass API carries guitar markers; retain the accepted track labeling.
    drums=band_io.write_part(work/'arranged_drums.mid','Drums',drum_notes,offset,tempo,markers)
    band=mido.MidiFile(type=1,ticks_per_beat=480)
    band.tracks.extend([guitar.tracks[0],bass.tracks[0],drums.tracks[0]])
    return band,section_metadata


def build(config,output):
    validate_config(config)
    out=Path(output)
    if out.exists():
        raise FileExistsError(out)
    spec=TEMPLATES[config['song_template']]
    foundation_path=Path(config.get('foundation_config','examples/requests/song_arrangement.json'))
    foundation=json.loads(foundation_path.read_text())
    foundation['bpm']=config['bpm']
    for i,section in enumerate(foundation['sections']):
        # Seed 42 reproduces the accepted seeds 401..405. The offset is
        # explicit and also keeps shared sections identical across templates.
        section['guitar_seed']=config['seed']+359+i
        section['drum_seed']=config['seed']+359
    with TemporaryDirectory() as temp:
        work=Path(temp)
        base_bands=arrange.build(foundation,work/'foundation')
        accepted=base_bands[2]
        base_path=work/'base.mid'
        pitch.save_new(accepted,base_path)
        rows=[{k:deepcopy(g[k]) for k in ('start','end','root','intervals','velocity','channel')}
              for g in pitch.groups(transition.part(accepted,0))[0]]
        _,notes=band_io.read_notes(base_path)
        drums=[n for n in notes if n[4]==10]
        # Existing controlled logic; retain unmatched seed-specific masks
        # instead of inventing a new accent optimization.
        controlled,percussion,accent_changes=groove.revise(rows,drums,True,skip_unmatched=True)
        standard=groove.assemble(controlled,percussion,accepted,work,'controlled')
        clips={}
        offset=0
        for section in foundation['sections']:
            clips[section['id']]=extract_section(standard,offset,section['bars'],work,section['id'])
            offset+=section['bars']*BAR
        relationships=[]
        transitions=[]
        if config['song_template']=='standard':
            band=standard
            sections=[dict(id=s['id'],start_bar=sum(x['bars'] for x in foundation['sections'][:i])+1,
                           end_bar=sum(x['bars'] for x in foundation['sections'][:i+1]),
                           style=s['style'],groups=len(clips[s['id']][0]))
                      for i,s in enumerate(foundation['sections'])]
            transitions=['Accepted Step 4-C boundary processing, followed by Controlled Syncopation']
        else:
            if config['song_template']=='double_chorus':
                verse,verse_drums=fresh_section(config,foundation,'tight_groove','verse_2',
                                                 config['seed']+364,work)
                verse=arrange.unify(verse,clips['intro'][0],'verse',8)
                clips['verse_2']=(verse,verse_drums)
            else:
                outro,outro_drums=fresh_section(config,foundation,'stop_and_go','outro_stop',
                                                 config['seed']+365,work)
                outro=arrange.unify(outro,clips['intro'][0],'verse',8)
                outro=arrange.finish(outro,8)
                outro[-1]['intervals']=(0,)
                clips['outro_stop']=(outro,outro_drums)
            parts=[deepcopy(clips[source][0]) for _,_,_,source in spec]
            percussion=[deepcopy(clips[source][1]) for _,_,_,source in spec]
            if config['song_template']=='double_chorus':
                # Keep the two Choruses' principal riff identical.
                relationships.append('Chorus 1 and Chorus 2 share the entire generated motif.')
            else:
                parts[4],roles=related_breakdown(parts[4],parts[3])
                relationships.append(f'Breakdown 2 inherits strong restart roles at local ticks {roles}.')
                if not roles:
                    raise ValueError('No compatible restart roles for Breakdown 2')
            # Standard source clips contain the original outgoing pickup.
            # Remove it from a reused Breakdown to avoid applying it twice.
            for i,(_,bars,style,_) in enumerate(spec):
                if style=='stutter_breakdown' and parts[i][-1]['start']==bars*BAR-120:
                    parts[i]=parts[i][:-1]
            parts,percussion,transitions=connect(parts,percussion,spec)
            band,sections=assemble(parts,percussion,spec,config,work)
        total=sum(s[1] for s in spec)*BAR
        if config['ending_style']=='stop_final_hit':
            plain=work/'before_ending.mid'
            pitch.save_new(band,plain)
            _,notes=band_io.read_notes(plain)
            before=deepcopy(band)
            band=ending.make_variant(band,notes,True,end_tick=total)
            for a,b in zip(before.tracks,band.tracks):
                assert ending.prefix_signature(a,total-BAR)==ending.prefix_signature(b,total-BAR)
        markers=[((s['start_bar']-1)*BAR,s['id']) for s in sections]
        for track_index,track in enumerate(band.tracks):
            transition.validate_notes(track,total)
            events=transition.absolute(track)
            assert events[-1][0]==total
            observed=[(t,m.text) for t,m in events if m.type=='marker']
            assert observed==markers or (track_index==1 and not observed)
            assert [(t,m.tempo) for t,m in events if m.type=='set_tempo']==[(0,480000)]
            assert [(t,m.numerator,m.denominator) for t,m in events if m.type=='time_signature']==[(0,4,4)]
        guitar,bass=[pitch.groups(transition.part(band,i))[0] for i in (0,1)]
        transition.validate_pitch(guitar)
        assert len(guitar)==len(bass)
        assert all((g['start'],g['end'],g['root']-24,g['velocity'])==
                   (b['start'],b['end'],b['root'],b['velocity']) for g,b in zip(guitar,bass))
        for section in sections:
            start=(section['start_bar']-1)*BAR
            stop=section['end_bar']*BAR
            section['groups']=sum(start<=g['start']<stop for g in guitar)
        if config['song_template']=='double_chorus':
            first=[g for g in guitar if 24*BAR<=g['start']<32*BAR]
            second=[g for g in guitar if 40*BAR<=g['start']<48*BAR]
            assert [(g['start']-24*BAR,g['end']-g['start'],g['root'],g['intervals'],g['velocity']) for g in first]==[
                (g['start']-40*BAR,g['end']-g['start'],g['root'],g['intervals'],g['velocity']) for g in second]
        if config['song_template']=='breakdown_focus':
            first=[g for g in guitar if 32*BAR<=g['start']<40*BAR]
            second=[g for g in guitar if 40*BAR<=g['start']<48*BAR]
            assert any(g['velocity']>=113 and any(
                h['velocity']>=113 and h['start']-40*BAR==g['start']-32*BAR for h in second) for g in first)
        out.mkdir()
        pitch.save_new(band,out/'song.mid')
        metadata=dict(config=config,sections=sections,total_bars=total//BAR,
                      duration_seconds=mido.tick2second(total,480,480000),
                      guitar_seeds={s['id']:s['guitar_seed'] for s in foundation['sections']},
                      extra_seeds=dict(verse_2=config['seed']+364,outro_stop=config['seed']+365),
                      drum_seed=config['seed']+359,controlled_accent_changes=accent_changes,
                      motif_relationships=relationships,transitions=transitions)
        band_io.dump(out/'metadata.json',metadata)
    return band


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config')
    parser.add_argument('output')
    args=parser.parse_args()
    build(json.loads(Path(args.config).read_text()),args.output)
