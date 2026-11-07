from copy import deepcopy
from pathlib import Path
import hashlib
import json
import mido
import pytest

from midi_generator.modules.guitar.reference_composition import compose_reference_guitar,midi_bytes
from midi_generator.modules.guitar.reference_development import develop_reference_eight_bars,extend_reference_groove


def inputs():
    rows=[]
    for b in range(4):
        cells=[dict(step=s,beat=s/4,accent_score=.8 if s in (0,5,10,14) else 0,observation_ids=[f'{b}_{s}'] if s in (0,5,10,14) else [],kick_candidate_score=.8 if s in (0,10) else 0) for s in range(16)]
        rows.append(dict(bar_index=b,cells=cells,major_accent_steps=[0,5,10,14],kick_candidate_steps=[0,10]))
    first=dict(section=dict(bars=4,bpm=95,meter=[4,4],start_seconds=0,end_seconds=16*60/95,bar_origin_seconds=0),bars=rows)
    settings=dict(key='B minor',seed=42,patterns={})
    for p in 'cd':settings['patterns'][p]=compose_reference_guitar(first,pattern=p)[1]
    full=deepcopy(first);full['section'].update(bars=8,end_seconds=32*60/95)
    for b in range(4):r=deepcopy(rows[b]);r['bar_index']+=4;full['bars'].append(r)
    return first,settings,full


@pytest.mark.parametrize('pattern',['c','d'])
def test_eight_bars_deterministic_and_note_pairing(pattern):
    _,settings,g=inputs();before=deepcopy((settings,g));mid,meta=develop_reference_eight_bars(g,settings,pattern=pattern)
    assert (settings,g)==before
    assert midi_bytes(mid)==midi_bytes(develop_reference_eight_bars(g,settings,pattern=pattern)[0])
    tick=0;active={}
    for m in mid.tracks[0]:
        assert m.time>=0;tick+=m.time
        if m.type=='note_on' and m.velocity:assert m.note not in active;active[m.note]=tick
        elif m.type=='note_off':assert active.pop(m.note)<tick
    assert not active and tick==8*1920
    assert all(a['end']<=b['start'] for a,b in zip(meta['groups'],meta['groups'][1:]))
    assert all(40<=n<=76 for g in meta['groups'] for n in g['notes'])
    assert meta['groups'][-1]['end']<=8*1920-120


def test_c_two_bar_harmony_and_long_motif():
    first,s,g=inputs();_,m=develop_reference_eight_bars(g,s,pattern='c');groups=m['groups']
    assert [h['label'] for h in m['harmony_blocks']]==['Bm','G','A','Bm']
    assert [h['start_tick'] for h in m['harmony_blocks']]==[0,3840,7680,11520]
    for b in range(8):
        rows=[x for x in groups if x['start']//1920==b];assert rows[0]['start']==b*1920
        assert rows[0]['notes'][0]==[47,43,45,47][b//2]
    assert sum(g['end']-g['start']==960 for g in groups)==4
    old=s['patterns']['c']['groups'];assert sum(x['end']-x['start'] for x in groups)/len(groups)>sum(x['end']-x['start'] for x in old)/len(old)
    for b in [0,2,4,6]:assert [x['start']%1920 for x in groups if x['start']//1920==b]==[x['start']%1920 for x in groups if x['start']//1920==0]
    assert any(len(g['notes'])==1 for g in groups) and any(len(g['notes'])==2 for g in groups)
    assert all(n%12 in {11,1,2,4,6,7,9} for r in groups for n in r['notes'])


def test_d_chugs_stops_and_upper_harmony():
    _,s,g=inputs();_,m=develop_reference_eight_bars(g,s,pattern='d');rows=m['groups']
    assert m['harmony_blocks'][1]['accent_notes']==[47,55,62]
    assert m['harmony_blocks'][2]['accent_notes']==[47,57,64]
    for row in rows:
        source=next(x for x in s['patterns']['d']['groups'] if x['start']==row['source_motif_start_tick'])
        if row['start']%1920<300:assert row['end']-row['start']==source['end']-source['start'] and row['notes'][0]==47
    assert any(b['start']-a['start']==60 for a,b in zip(rows,rows[1:]))
    assert any(b['start']-a['end']>960 for a,b in zip(rows,rows[1:]))
    chromatic=[r for r in rows if 48 in r['notes']];assert len(chromatic)==2 and all(r['end']-r['start']<=120 for r in chromatic)
    assert rows[-1]['notes']==[47,54]
    assert len(m['omitted_source_events'])==1 and m['omitted_source_events'][0]['bar']==7
    assert any(r['notes']==[47,55,62] for r in rows) and any(r['notes']==[47,57,64] for r in rows)


def test_reject_unapproved_or_bad_context():
    _,s,g=inputs()
    for p in ['a','b','invalid']:
        with pytest.raises(ValueError):develop_reference_eight_bars(g,s,pattern=p)
    g['section']['bars']=4
    with pytest.raises(ValueError):develop_reference_eight_bars(g,s,pattern='c')


def test_append_only_audio_map(tmp_path):
    np=pytest.importorskip('numpy');sf=pytest.importorskip('soundfile');pytest.importorskip('librosa')
    from midi_generator.reference_analysis.groove_map import analyze_audio_groove
    sr=22050;seconds=32*60/95+.1;y=np.zeros(round(seconds*sr));rng=np.random.default_rng(9)
    for beat in range(32):
        t=np.arange(round(.07*sr))/sr;z=(np.sin(2*np.pi*80*t)+rng.normal(0,.3,len(t)))*np.exp(-t*70)*.2;i=round(beat*60/95*sr);y[i:i+len(t)]+=z
    path=tmp_path/'source.wav';sf.write(path,y,sr);first=analyze_audio_groove(path,start_seconds=0,bpm=95,bar_origin_seconds=0);original=deepcopy(first)
    g=extend_reference_groove(first,path)
    assert first==original and g['bars'][:4]==first['bars'] and g['observations'][:len(first['observations'])]==first['observations']
    assert len(g['bars'])==8 and g['section']['end_seconds']==pytest.approx(32*60/95)
    assert all(c['time_seconds']>=16*60/95 for c in g['observations'][len(first['observations']):])
    assert len(set(o['id'] for o in g['observations']))==len(g['observations'])
    ids={o['id'] for o in g['observations']}
    assert all(i in ids for b in g['bars'] for c in b['cells'] for i in c['observation_ids'])
    assert len(g['bar_similarity'])==28
    broken=deepcopy(first);broken['source']['sha256']='wrong'
    with pytest.raises(ValueError,match='hash'):extend_reference_groove(broken,path)
