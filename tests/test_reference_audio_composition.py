from copy import deepcopy
import hashlib
import json
from pathlib import Path

import mido
import pytest
from midi_generator.modules.guitar.reference_composition import compose_reference_guitar,midi_bytes


def map_fixture():
    bars=[]
    for b in range(4):
        cells=[dict(step=s,beat=s/4,accent_score=.8 if s in (0,5,10,14) else 0.,observation_ids=[f'event{b}_{s}'] if s in (0,5,10,14) else [],kick_candidate_score=.8 if s in (0,10) else 0.) for s in range(16)]
        bars.append(dict(bar_index=b,cells=cells,major_accent_steps=[0,5,10,14],kick_candidate_steps=[0,10]))
    return dict(section=dict(bars=4,bpm=95,meter=[4,4],start_seconds=1.287157894736842,end_seconds=11.392421052631578),bars=bars)


@pytest.mark.parametrize('pattern',list('abcd'))
def test_deterministic_harmony_and_note_safety(pattern):
    g=map_fixture();before=deepcopy(g);mid,meta=compose_reference_guitar(g,pattern=pattern,seed=42)
    assert g==before
    assert midi_bytes(mid)==midi_bytes(compose_reference_guitar(g,pattern=pattern,seed=42)[0])
    assert meta['key']=='B minor' and meta['audio_anchor_group_count']>0
    assert len(meta['groups'])>0 and any(len(row['notes'])==2 for row in meta['groups'])
    allowed={11,1,2,4,6,7,9}|({0} if pattern in 'bd' else set())
    assert all(n%12 in allowed for row in meta['groups'] for n in row['notes'])
    tick=0;active={};ons=0
    for msg in mid.tracks[0]:
        assert msg.time>=0;tick+=msg.time
        if msg.type=='note_on' and msg.velocity:
            assert msg.note not in active;active[msg.note]=tick;ons+=1
        elif msg.type=='note_off':assert active.pop(msg.note)<tick<=4*1920
    assert not active and tick==4*1920 and ons>=len(meta['groups'])
    assert min(meta['pitch_range'])>=40 and max(meta['pitch_range'])<=76
    assert all(b['start']>=a['end'] for a,b in zip(meta['groups'],meta['groups'][1:]))
    assert any(msg.type=='key_signature' and msg.key=='Bm' for msg in mid.tracks[0])
    assert next(msg.tempo for msg in mid.tracks[0] if msg.type=='set_tempo')==mido.bpm2tempo(95)


def test_four_different_rhythms_and_source_causality():
    g=map_fixture();rhythms=set()
    for p in 'abcd':
        _,meta=compose_reference_guitar(g,pattern=p);sig=tuple(row['start'] for row in meta['groups']);rhythms.add(sig)
        changed=deepcopy(g)
        for bar in changed['bars']:
            cells=deepcopy(bar['cells'])
            for s in range(16):bar['cells'][s]=dict(cells[(s-2)%16],step=s,beat=s/4)
            bar['major_accent_steps']=[2,7,12,0];bar['kick_candidate_steps']=[2,12]
        other=compose_reference_guitar(changed,pattern=p)[1]
        assert sig!=tuple(row['start'] for row in other['groups'])
    assert len(rhythms)==4


def test_motif_repetition_and_breakdown_stops():
    _,d=compose_reference_guitar(map_fixture(),pattern='d');rows=d['groups']
    bar0=[(r['start']%1920,r['root']) for r in rows if r['start']//1920==0]
    bar2=[(r['start']%1920,r['root']) for r in rows if r['start']//1920==2]
    assert bar0==bar2
    assert any(b['start']-a['start']==60 for a,b in zip(rows,rows[1:]))
    assert any(b['start']-a['end']>=360 for a,b in zip(rows,rows[1:]))
    assert any(r['root']==48 for r in rows)
    _,c=compose_reference_guitar(map_fixture(),pattern='c')
    assert [g['start']%1920 for g in c['groups'] if g['start']//1920==0]==[g['start']%1920 for g in c['groups'] if g['start']//1920==2]
    assert [next(g['root'] for g in c['groups'] if g['start']//1920==b) for b in range(4)]==[47,43,45,47]


def test_reject_generic_or_malformed_inputs():
    g=map_fixture()
    for row in g['bars']:row['major_accent_steps']=[]
    with pytest.raises(ValueError,match='Insufficient'):compose_reference_guitar(g,pattern='a')
    with pytest.raises(ValueError):compose_reference_guitar(map_fixture(),pattern='bad')
    with pytest.raises(ValueError):compose_reference_guitar(map_fixture(),pattern='a',seed=True)
    g=map_fixture();g['bars'][0]['cells'][0]['accent_score']=float('nan')
    with pytest.raises(ValueError):compose_reference_guitar(g,pattern='a')


def test_audio_features_are_not_instrument_ground_truth(tmp_path):
    np=pytest.importorskip('numpy');sf=pytest.importorskip('soundfile');pytest.importorskip('librosa')
    from midi_generator.reference_analysis.groove_map import analyze_audio_groove
    sr=22050;duration=16*60/95+.1;y=np.zeros(round(duration*sr));rng=np.random.default_rng(9)
    for beat in (0,1,1.75,2.5,4,5.25,6.5,8,9,10.75,12,13.5,15):
        start=round(beat*60/95*sr);t=np.arange(round(.06*sr))/sr
        pulse=(np.sin(2*np.pi*80*t)+rng.normal(0,.2,len(t)))*np.exp(-t*70)*.25
        y[start:start+len(t)]+=pulse
    path=tmp_path/'mix.wav';sf.write(path,y,sr);before=hashlib.sha256(path.read_bytes()).hexdigest()
    g=analyze_audio_groove(path,start_seconds=0,bpm=95,bar_origin_seconds=0)
    again=analyze_audio_groove(path,start_seconds=0,bpm=95,bar_origin_seconds=0)
    assert json.dumps(g,sort_keys=True)==json.dumps(again,sort_keys=True)
    assert g['summary']['observation_count']>0 and len(g['bars'])==4
    assert all(o['status']=='observed' and o['role_status']=='inferred' and not o['human_verified'] for o in g['observations'])
    assert all(abs(o['time_seconds']-(o['nearest_sixteenth']/4*60/95+o['grid_residual_seconds']))<1e-8 for o in g['observations'])
    assert before==hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):analyze_audio_groove(path,start_seconds=.1,bpm=95,bar_origin_seconds=0)
    silent=tmp_path/'silent.wav';sf.write(silent,np.zeros_like(y),sr)
    with pytest.raises(ValueError,match='Insufficient'):compose_reference_guitar(analyze_audio_groove(silent,start_seconds=0,bpm=95,bar_origin_seconds=0),pattern='a')
